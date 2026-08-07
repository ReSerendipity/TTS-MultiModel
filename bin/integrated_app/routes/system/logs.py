"""操作日志查询与清理 API 路由。

架构说明：
- 本模块管理应用操作日志（非 Python logging 全量），供 Settings → Logs 面板使用
- 路径前缀：``/api/system``
- 持久化：双通道存储
  1. 内存 ``OperationLog`` 环形缓冲区（maxlen=2000）— 保证导入后立即可用，0 依赖
  2. ``history_db`` 中的 ``action_logs`` 表（若可用）— 跨重启持久化，支持分页与按时间筛选
- 历史 action 类型枚举（常量）：
  * generation_start / generation_success / generation_failed
  * model_load / model_unload / model_switch
  * persona_create / persona_update / persona_delete
  * lora_load / lora_unload
  * system_startup / system_shutdown / config_update
- 接口清单：
  * ``GET /api/system/logs`` — 查询操作日志（分页/按 level/action/时间范围）
  * ``DELETE /api/system/logs/clean`` — 清理 30 天前或超过 10 万条的旧日志

Why ``action_logs`` 单独建表而不是混在 ``generation_history``：
    操作日志的产生频率（系统启动/关闭/模型切换/每一次 Persona 增删改）约
    为 generation 记录的 10× 以上；若混合建表，generations 的 UNIQUE/索引
    会被非 generation 记录稀释，查询 generation 列表时扫描行数膨胀 10×，
    导致性能下降一个数量级。独立表 + 独立索引使两条查询路径互不干扰。
"""

import json
import logging
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

logger = logging.getLogger("tts_multimodel")

router = APIRouter(prefix="/api/system", tags=["system"])


# ---------------------------------------------------------------------------
# 常量：合法枚举值
# ---------------------------------------------------------------------------

VALID_LEVELS: frozenset = frozenset({"INFO", "WARN", "ERROR"})
VALID_ACTIONS: frozenset = frozenset(
    {
        "generation_start",
        "generation_success",
        "generation_failed",
        "model_load",
        "model_unload",
        "model_switch",
        "persona_create",
        "persona_update",
        "persona_delete",
        "lora_load",
        "lora_unload",
        "system_startup",
        "system_shutdown",
        "config_update",
    }
)

ACTION_LEVEL_MAP: dict[str, str] = {
    "generation_failed": "ERROR",
    "model_unload": "WARN",
    "system_shutdown": "WARN",
}
"""按 action 推断的默认 level（INFO 为默认，显式覆盖写在这个表里）。"""

CLEAN_DAYS_THRESHOLD: int = 30
CLEAN_COUNT_THRESHOLD: int = 100_000


# ---------------------------------------------------------------------------
# Pydantic 响应模型
# ---------------------------------------------------------------------------


class LogEntryResponse(BaseModel):
    """单条操作日志响应。

    Attributes:
        id: 自增 ID（内存中为递增计数器，DB 中为 rowid）。
        ts: Unix 时间戳（毫秒）。
        level: 严重等级 ``INFO`` / ``WARN`` / ``ERROR``。
        action: 动作类型枚举（见 VALID_ACTIONS）。
        message: 人类可读的中文/英文摘要。
        extra: 扩展元数据（request_id / task_id / duration_ms / persona_id / engine 等）。
    """

    id: int = Field(description="日志 ID")
    ts: int = Field(description="时间戳（毫秒）")
    level: str = Field(description="等级 INFO/WARN/ERROR")
    action: str = Field(description="动作类型")
    message: str = Field(description="摘要文本")
    extra: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")


class LogListResponse(BaseModel):
    """分页日志列表响应。

    Attributes:
        total_count: 满足过滤条件的总条数（用于分页控件计算页数）。
        page: 当前页码（1 起始）。
        page_size: 每页大小。
        items: 本页日志条目，按时间倒序（最新第一条）。
    """

    total_count: int = Field(description="满足条件的总条数")
    page: int = Field(description="当前页码，1 起始")
    page_size: int = Field(description="每页大小")
    items: list[LogEntryResponse] = Field(default_factory=list, description="日志条目列表")


# ---------------------------------------------------------------------------
# 内存 OperationLog（向后兼容，供 settings.py 等模块调用 log_operation / get_operation_log）
# ---------------------------------------------------------------------------


class OperationLog:
    """内存环形缓冲区操作日志。

    Why 用内存 deque 而不是只依赖 DB：
        1. 模块加载顺序：history_db 的初始化依赖 config，但日志模块可能在
           config 就绪前就被 import 并记录 system_startup；此时 DB 连接未开。
        2. 零依赖：某些最小化部署（CLI 模式）不创建 history_db，
           内存环形缓冲区保证 Settings → Logs 面板仍有最近 2000 条可查。
    """

    def __init__(self, maxlen: int = 2000) -> None:
        self._logs: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.RLock()
        self._counter: int = 0

    def add(
        self,
        operation_type: str,
        message: str,
        details: dict[str, Any] | None = None,
        level: str | None = None,
    ) -> int:
        """追加一条日志，返回分配的自增 ID。"""
        resolved_level = level or ACTION_LEVEL_MAP.get(operation_type, "INFO")
        if resolved_level not in VALID_LEVELS:
            resolved_level = "INFO"
        with self._lock:
            self._counter += 1
            entry: dict[str, Any] = {
                "id": self._counter,
                "timestamp": time.time() * 1000,
                "type": operation_type,
                "message": message,
                "details": details or {},
                "level": resolved_level,
            }
            self._logs.appendleft(entry)
            return self._counter

    def get_latest(
        self,
        limit: int = 50,
        filter_type: str | None = None,
        level: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """读取最新 N 条，可按 action type / level / 时间窗过滤。"""
        with self._lock:
            logs = list(self._logs)
        if filter_type and filter_type != "all":
            logs = [log for log in logs if log["type"] == filter_type]
        if level:
            logs = [log for log in logs if log["level"] == level]
        if start_ts is not None:
            logs = [log for log in logs if log["timestamp"] >= start_ts]
        if end_ts is not None:
            logs = [log for log in logs if log["timestamp"] <= end_ts]
        return logs[:limit]

    def count(self) -> int:
        with self._lock:
            return len(self._logs)


_operation_log = OperationLog(maxlen=2000)


def get_operation_log() -> OperationLog:
    """获取内存 OperationLog 单例（向后兼容的公开 API）。"""
    return _operation_log


def log_operation(
    operation_type: str,
    message: str,
    details: dict[str, Any] | None = None,
    level: str | None = None,
) -> None:
    """对外暴露的写日志入口（向后兼容，settings.py 等模块使用）。

    双通道写入：内存环形缓冲区 + 若 history_db.action_logs 表可用则同步落库。
    """
    # 1. 内存写入（必然成功）
    _operation_log.add(operation_type, message, details, level)

    # 2. 尝试 DB 持久化（静默失败，不影响内存路径）
    try:
        from ...history_db import get_history_db

        db = get_history_db()
        if db is None:
            return
        _ensure_action_logs_table(db)
        resolved_level = level or ACTION_LEVEL_MAP.get(operation_type, "INFO")
        if resolved_level not in VALID_LEVELS:
            resolved_level = "INFO"
        extra_json = json.dumps(details or {}, ensure_ascii=False)
        # 兜底：operation_type/message 为 None 时避免 NOT NULL IntegrityError
        safe_operation = str(operation_type or "unknown")
        safe_message = str(message or "")
        with db._transaction() as conn:
            conn.execute(
                "INSERT INTO action_logs (ts_ms, level, action, message, extra_json) VALUES (?, ?, ?, ?, ?)",
                (
                    int(time.time() * 1000),
                    resolved_level,
                    safe_operation,
                    safe_message,
                    extra_json,
                ),
            )
    except sqlite3.Error:
        # DB 不可用/约束冲突等都当没发生过，内存路径已经记录
        return
    except (AttributeError, ImportError, OSError, RuntimeError):
        return


# ---------------------------------------------------------------------------
# DB Schema 辅助：确保 action_logs 表存在
# ---------------------------------------------------------------------------


def _ensure_action_logs_table(db: Any) -> None:
    """若 ``action_logs`` 表不存在则创建。幂等。

    Why 建表放在日志模块而不是 history_db：
        history_db.py 的核心职责是 generation 历史，action_logs 是辅助扩展。
        在日志模块内懒建表可以避免修改 history_db 的 Schema 迁移流程，
        降低与既有 generation 表耦合。
    """
    try:
        create_sql = """
        CREATE TABLE IF NOT EXISTS action_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_ms INTEGER NOT NULL,
            level TEXT NOT NULL,
            action TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            extra_json TEXT NOT NULL DEFAULT '{}'
        )
        """
        with db._transaction() as conn:
            conn.execute(create_sql)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_action_logs_ts_ms ON action_logs(ts_ms DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_action_logs_level_action ON action_logs(level, action)")
    except sqlite3.OperationalError:
        return


def _db_available() -> Any | None:
    """返回可用的 history_db 实例；不可用返回 None。"""
    try:
        from ...history_db import get_history_db

        db = get_history_db()
        if db is None:
            return None
        _ensure_action_logs_table(db)
        return db
    except (ImportError, AttributeError, TypeError, OSError, RuntimeError):
        return None


# ---------------------------------------------------------------------------
# 1. GET /logs — 查询操作日志（分页）
# ---------------------------------------------------------------------------


@router.get(
    "/logs",
    summary="查询操作日志",
    description="分页查询操作日志，支持 level/action/时间窗过滤",
    response_model=LogListResponse,
)
def get_logs(
    level: str | None = Query(default=None, description="日志等级 INFO/WARN/ERROR"),
    action: str | None = Query(default=None, description="动作类型过滤，如 generation_success"),
    page: int = Query(default=1, ge=1, description="页码，1 起始"),
    page_size: int = Query(default=50, ge=1, le=500, description="每页 1-500 条"),
    start_ts: int | None = Query(default=None, description="起始时间戳（毫秒，含）"),
    end_ts: int | None = Query(default=None, description="结束时间戳（毫秒，含）"),
) -> LogListResponse:
    """分页查询操作日志。

    优先级：DB 有数据则查 DB，否则回退到内存环形缓冲区。
    """
    # 参数显式校验：level 非 INFO/WARN/ERROR → 400（显式优于隐式）
    if level is not None and level not in VALID_LEVELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"level 必须是 INFO/WARN/ERROR（收到 '{level}'）",
        )

    db = _db_available()
    if db is not None:
        return _query_from_db(db, level, action, page, page_size, start_ts, end_ts)
    return _query_from_memory(level, action, page, page_size, start_ts, end_ts)


def _build_filter_sql(
    level: str | None,
    action: str | None,
    start_ts: int | None,
    end_ts: int | None,
) -> tuple[str, list[Any]]:
    """构建 WHERE 子句与参数列表。"""
    clauses: list[str] = []
    params: list[Any] = []
    if level:
        clauses.append("level = ?")
        params.append(level)
    if action:
        clauses.append("action = ?")
        params.append(action)
    if start_ts is not None:
        clauses.append("ts_ms >= ?")
        params.append(start_ts)
    if end_ts is not None:
        clauses.append("ts_ms <= ?")
        params.append(end_ts)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _query_from_db(
    db: Any,
    level: str | None,
    action: str | None,
    page: int,
    page_size: int,
    start_ts: int | None,
    end_ts: int | None,
) -> LogListResponse:
    where, params = _build_filter_sql(level, action, start_ts, end_ts)

    # total_count
    try:
        cursor = db._execute(f"SELECT COUNT(*) FROM action_logs {where}", params)
        total_row = cursor.fetchone()
        total_count = int(total_row[0]) if total_row else 0
    except sqlite3.OperationalError as exc:
        logger.error(f"[logs] DB 计数查询失败: {exc}")
        total_count = 0

    # items（按 ts_ms DESC + id DESC 保证时间倒序）
    items: list[LogEntryResponse] = []
    if total_count > 0:
        offset = (page - 1) * page_size
        try:
            cursor = db._execute(
                f"SELECT id, ts_ms, level, action, message, extra_json "
                f"FROM action_logs {where} "
                f"ORDER BY ts_ms DESC, id DESC "
                f"LIMIT ? OFFSET ?",
                params + [page_size, offset],
            )
            for row in cursor.fetchall():
                row_id, ts_ms, row_level, row_action, row_msg, extra_json = row
                try:
                    extra_parsed: dict[str, Any] = json.loads(extra_json) if extra_json else {}
                    if not isinstance(extra_parsed, dict):
                        extra_parsed = {"raw": extra_parsed}
                except (json.JSONDecodeError, TypeError, ValueError):
                    extra_parsed = {"_parse_error": True, "raw": extra_json}
                items.append(
                    LogEntryResponse(
                        id=int(row_id),
                        ts=int(ts_ms),
                        level=str(row_level),
                        action=str(row_action),
                        message=str(row_msg or ""),
                        extra=extra_parsed,
                    )
                )
        except sqlite3.OperationalError as exc:
            logger.error(f"[logs] DB 分页查询失败: {exc}")
            items = []

    return LogListResponse(total_count=total_count, page=page, page_size=page_size, items=items)


def _query_from_memory(
    level: str | None,
    action: str | None,
    page: int,
    page_size: int,
    start_ts: int | None,
    end_ts: int | None,
) -> LogListResponse:
    # 先拿过滤后的全量，再在 Python 层分页
    filter_type = action or "all"
    filtered = _operation_log.get_latest(
        limit=_operation_log.count(),
        filter_type=filter_type,
        level=level,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    total_count = len(filtered)
    offset = (page - 1) * page_size
    page_slice = filtered[offset : offset + page_size]

    items: list[LogEntryResponse] = []
    for raw in page_slice:
        details = raw.get("details") or {}
        if not isinstance(details, dict):
            details = {"raw": details}
        items.append(
            LogEntryResponse(
                id=int(raw.get("id", 0)),
                ts=int(raw.get("timestamp", 0)),
                level=str(raw.get("level", "INFO")),
                action=str(raw.get("type", "unknown")),
                message=str(raw.get("message", "")),
                extra=details,
            )
        )
    return LogListResponse(total_count=total_count, page=page, page_size=page_size, items=items)


# ---------------------------------------------------------------------------
# 2. DELETE /logs/clean — 清理旧日志（30 天 OR 超过 10 万条双重阈值）
# ---------------------------------------------------------------------------


@router.delete("/logs/clean", summary="清理旧日志", description="清理 30 天前或超过 10 万条之前的操作日志")
def clean_logs() -> dict[str, Any]:
    """按双重阈值清理操作日志。

    双重阈值 Why：
        1. 时间 30 天：普通用户一天 ~10 条，一年 3600 条，30 天仅 300 条，属于安全保留。
        2. 条数 10 万：重度批处理用户一天 5000 条 × 30 天 = 15 万，此时不受 30 天保护，
           按条数阈值截断，防止 SQLite 单表无限增长拖垮 VACUUM / 查询性能。
    """
    db = _db_available()
    deleted_db: int = 0

    if db is not None:
        # OperationalError（DB 锁）→ 重试 3 次 × 0.5s
        last_err: sqlite3.OperationalError | None = None
        for attempt in range(3):
            try:
                _ensure_action_logs_table(db)
                cut_ts_ms = int((datetime.now() - timedelta(days=CLEAN_DAYS_THRESHOLD)).timestamp() * 1000)

                with db._transaction() as conn:
                    # A. 按时间删除
                    cur = conn.execute("DELETE FROM action_logs WHERE ts_ms < ?", (cut_ts_ms,))
                    deleted_time = cur.rowcount or 0

                    # B. 若仍 >10 万条，按条数阈值删除（保留最新 10 万）
                    cur = conn.execute("SELECT COUNT(*) FROM action_logs")
                    remaining = int(cur.fetchone()[0] or 0)
                    deleted_count = 0
                    if remaining > CLEAN_COUNT_THRESHOLD:
                        drop_n = remaining - CLEAN_COUNT_THRESHOLD
                        cur = conn.execute(
                            "DELETE FROM action_logs WHERE id IN "
                            "(SELECT id FROM action_logs ORDER BY ts_ms ASC, id ASC LIMIT ?)",
                            (drop_n,),
                        )
                        deleted_count = cur.rowcount or 0

                    deleted_db = deleted_time + deleted_count
                    logger.info(
                        f"[logs/clean] DB 清理完成：时间删除 {deleted_time} 条，"
                        f"条数阈值删除 {deleted_count} 条，共 {deleted_db} 条"
                    )
                break
            except sqlite3.OperationalError as exc:
                last_err = exc
                logger.warning(f"[logs/clean] DB 锁/错误（attempt {attempt + 1}/3）: {exc}")
                time.sleep(0.5)
        else:
            # 3 次都失败 → 500
            logger.error(f"[logs/clean] DB 清理 3 次重试后仍失败: {last_err}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"清理操作日志失败（DB 锁超时）：{last_err}",
            )

    # 内存环形缓冲区：deque maxlen 自动裁剪，这里也手动将最旧的一半丢掉（配合 DB 清理语义）
    current = _operation_log.count()
    if current > 1000:
        drop_from_memory = current - 1000
        with _operation_log._lock:
            # 从右侧（最旧）截断：deque.appendleft → 最新在左，最旧在右
            for _ in range(min(drop_from_memory, len(_operation_log._logs))):
                _operation_log._logs.pop()

    return {
        "status": "ok",
        "deleted_db_rows": deleted_db,
        "clean_policy": {
            "days_threshold": CLEAN_DAYS_THRESHOLD,
            "count_threshold": CLEAN_COUNT_THRESHOLD,
        },
    }


# ---------------------------------------------------------------------------
# 向后兼容：保留原 /logs 简单接口（无分页参数时仍返回 {"logs", "total"}）
# ---------------------------------------------------------------------------


@router.get("/logs-compat", include_in_schema=False)
def get_logs_simple(limit: int = 50, filter_type: str = "all") -> dict[str, Any]:
    """（内部兼容）原 Settings 页面使用的简单无分页格式。"""
    valid_types = {"all", "generation", "model", "config"}
    if filter_type not in valid_types:
        filter_type = "all"
    logs_raw = _operation_log.get_latest(limit=limit, filter_type=filter_type)
    logs_public: list[dict[str, Any]] = []
    for raw in logs_raw:
        logs_public.append(
            {
                "id": raw.get("id"),
                "timestamp": raw.get("timestamp"),
                "type": raw.get("type"),
                "message": raw.get("message"),
                "details": raw.get("details", {}),
            }
        )
    return {"logs": logs_public, "total": len(logs_public)}
