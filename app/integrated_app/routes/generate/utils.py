"""生成路由通用工具模块。

架构说明：
    本模块提供 generate 子路由的通用工具函数（不依赖具体引擎），
    被 VoxCPM2 / IndexTTS2 的生成路由共同调用，避免重复代码。

    主要功能分类：
    1. SSE 事件格式化与任务 ID 生成 (format_sse_event, new_task_id)
    2. 音频持久化 + 历史记录写入 (write_history_and_save_audio, save_uploaded_audio)
    3. 生成信号量与超时控制 (per-engine 并发限制 + 硬超时保护)
    4. OOM 降级重试机制 (_run_with_oom_retry)
    5. 音频后处理（响度归一化、语速调节、增强）
    6. 上传/文本输入校验
    7. 失败响应构建 (build_generation_error_response)
    8. S-R4: Legacy history.db 一次性迁移（两个 SQLite 路径合并）

注意：本模块同时暴露 `router = APIRouter(prefix="/api/generate")`，
    用于 app_server.py 顶层 pkgutil 扫描时挂载共享路由。
"""

import asyncio
import contextlib
import html
import json
import logging
import os
import random
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import quote

import aiofiles
import numpy as np
from fastapi import APIRouter, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from ...audio_processing import enhance_audio
from ...config import MAX_UPLOAD_SIZE_BYTES, SAVE_DIR, get_config
from ...exceptions import EngineSwitchError, InsufficientVRAMError, OOMRetryExhaustedError, TTSError
from ...gpu_utils import free_gpu_memory, is_oom_error
from ...history_db import get_history_db
from ...model_manager import _time_estimator
from ...monitor import get_health_monitor
from ...security.audit import log_audit
from ..system import increment_generation, log_operation

router = APIRouter(prefix="/api/generate", tags=["generate"])

logger = logging.getLogger("tts_multimodel")

# ---------------------------------------------------------------------------
# S-R4: 统一 history_db 单例 — 移除模块级独立实例，改用全局 get_history_db()
# 修复 P0 数据一致性 Bug：生成路由写入 outputs/history.db，但读取页面
# 查询 data/history.db，导致用户看不到刚生成的历史记录。
# ---------------------------------------------------------------------------
_generation_semaphores: dict[str, asyncio.Semaphore] = {}
_generation_semaphore_lock = asyncio.Lock()
_generation_retry_counter: dict[str, int] = {"total": 0, "oom_retries": 0}

# REFACTOR: 集中常量，消除魔法数字
_MAX_CONCURRENT_GENERATIONS: int = max(1, int(os.environ.get("TTS_MAX_CONCURRENT_GENERATIONS", "1")))
# E6-1 SECURITY/ROBUSTNESS: 信号量获取超时 (秒) — 用户排队等待上限
_SEMAPHORE_ACQUIRE_TIMEOUT_S: float = float(os.environ.get("TTS_SEMAPHORE_TIMEOUT_S", "120.0"))
# E6-1 SECURITY/ROBUSTNESS: 单次生成硬超时 (秒) — 防止超长文本耗尽信号量池
# 默认 600s (10 分钟)，可按硬件调优。生成超时后释放信号量，返回友好错误。
_GENERATION_HARD_TIMEOUT_S: float = float(os.environ.get("TTS_GENERATION_TIMEOUT_S", "600.0"))

# ---------------------------------------------------------------------------
# 生成结果缓存（BACKEND_DESIGN_ASSESSMENT §长期能力建设 T9）：相同请求短 TTL 命中，
# 经 config.yaml generation.cache_enabled 开启（默认关闭，零风险）。
# 仅缓存成功文件名（音频文件持久化于 SAVE_DIR），命中即复用既有文件、跳过 GPU 推理。
# ---------------------------------------------------------------------------
_GENERATION_CACHE: Any = None  # 惰性单例：`GenerationResultCache` 或 sentinel False
_GENERATION_CACHE_LOCK = threading.Lock()

# 幂等键缓存（同报告 §中期根治）：对携带 Idempotency-Key 的请求去重，防止客户端
# 网络重试导致重复生成。窗口 5 分钟，命中直接复用上次结果文件。
_IDEMPOTENCY_CACHE: dict[str, tuple[float, str]] = {}
_IDEMPOTENCY_LOCK = threading.Lock()
_IDEMPOTENCY_TTL_S: float = 300.0


def _get_generation_cache() -> Any:
    """惰性获取生成结果缓存单例（配置关闭时返回 None）。

    Returns:
        GenerationResultCache 实例（cache_enabled=true）或 None（关闭 / 配置异常）。
    """
    global _GENERATION_CACHE
    if _GENERATION_CACHE is not None:
        return _GENERATION_CACHE
    with _GENERATION_CACHE_LOCK:
        if _GENERATION_CACHE is not None:
            return _GENERATION_CACHE
        try:
            if get_config().pydantic_config.generation.cache_enabled:
                from ...generation_cache import GenerationResultCache

                ttl = float(get_config().pydantic_config.generation.cache_ttl_seconds)
                _GENERATION_CACHE = GenerationResultCache(ttl_seconds=ttl)
                logger.info("[gen-cache] 生成结果缓存已启用 (ttl=%.0fs)", ttl)
            else:
                _GENERATION_CACHE = False  # sentinel：已初始化且为关闭
        except Exception as exc:  # noqa: BLE001
            logger.debug("[gen-cache] 初始化失败，缓存关闭: %s", exc)
            _GENERATION_CACHE = False
    return None if _GENERATION_CACHE is False else _GENERATION_CACHE


def _build_generation_cache_key(
    engine: str,
    text: str,
    voice_or_persona: str,
    tempo_factor: float,
    voice_enhancement: str,
    target_lufs: float,
) -> str:
    """构造生成缓存键（覆盖所有影响最终输出的可见维度）。

    Args:
        engine: 引擎名。
        text: 生成文本。
        voice_or_persona: 音色/角色名（影响克隆结果）。
        tempo_factor: 语速因子。
        voice_enhancement: 增强开关。
        target_lufs: 目标响度。

    Returns:
        确定性缓存键。
    """
    from ...generation_cache import GenerationResultCache

    return GenerationResultCache.make_cache_key(
        engine,
        text,
        voice_or_persona=voice_or_persona,
        tempo_factor=tempo_factor,
        voice_enhancement=str(voice_enhancement),
        target_lufs=target_lufs,
    )


def _idempotency_lookup(key: str) -> str | None:
    """查询幂等缓存，命中且未过期返回缓存文件名（文件仍需存在）。"""
    with _IDEMPOTENCY_LOCK:
        entry = _IDEMPOTENCY_CACHE.get(key)
        if not entry:
            return None
        expire_at, filename = entry
        if time.monotonic() >= expire_at:
            _IDEMPOTENCY_CACHE.pop(key, None)
            return None
        path = filename if os.path.isabs(filename) else os.path.join(SAVE_DIR, filename)
        if os.path.isfile(path):
            return filename
        _IDEMPOTENCY_CACHE.pop(key, None)
        return None


def _idempotency_store(key: str, filename: str) -> None:
    """写入幂等缓存（带 TTL 与容量裁剪）。"""
    with _IDEMPOTENCY_LOCK:
        _IDEMPOTENCY_CACHE[key] = (time.monotonic() + _IDEMPOTENCY_TTL_S, filename)
        # 简易容量保护：超过 1024 条时清理过期项
        if len(_IDEMPOTENCY_CACHE) > 1024:
            now = time.monotonic()
            expired = [k for k, (exp, _) in _IDEMPOTENCY_CACHE.items() if now >= exp]
            for k in expired[: len(expired) - 512]:
                _IDEMPOTENCY_CACHE.pop(k, None)


# ---------------------------------------------------------------------------
# 配置外置（超时 / 并发 / 信号量排队）：原硬编码 env 常量迁移到 config.yaml
# generation.*，保留环境变量覆盖能力，回退到上面的默认值。
# ---------------------------------------------------------------------------
def _gen_max_concurrent() -> int:
    env = os.environ.get("TTS_MAX_CONCURRENT_GENERATIONS")
    if env:
        return max(1, int(env))
    try:
        return max(1, int(get_config().pydantic_config.generation.max_concurrent))
    except Exception:  # noqa: BLE001
        return _MAX_CONCURRENT_GENERATIONS


def _gen_semaphore_timeout() -> float:
    env = os.environ.get("TTS_SEMAPHORE_TIMEOUT_S")
    if env:
        return float(env)
    try:
        return float(get_config().pydantic_config.generation.semaphore_acquire_timeout_s)
    except Exception:  # noqa: BLE001
        return _gen_semaphore_timeout()


def _gen_hard_timeout() -> float:
    env = os.environ.get("TTS_GENERATION_TIMEOUT_S")
    if env:
        return float(env)
    try:
        return float(get_config().pydantic_config.generation.timeout_s)
    except Exception:  # noqa: BLE001
        return _gen_hard_timeout()


def _ensure_content_safe(request: Any, text: str) -> Any:
    """M3：内容安全网关统一入口。返回 HTMLResponse（拦截）或 None（放行）。

    所有 routes/generate/* 与 /v1/audio/speech 均经此单入口，避免散落调用。
    """
    try:
        if not get_config().pydantic_config.security.content_safety_enabled:
            return None
    except Exception:  # noqa: BLE001
        return None
    from ...security.content_safety import get_safety_detector

    result = get_safety_detector().detect(text)
    if not result.is_safe:
        from ...security.audit import log_audit

        actor = getattr(request.state, "user", "anonymous") if hasattr(request, "state") else "anonymous"
        log_audit("content_blocked", actor=str(actor), detail=result.category.value, outcome="blocked")
        # 运维稳定性评估 P1：审核拦截计入 safety 分类（此前 /metrics 完全不可见）。
        # 拦截发生在获取信号量之前，属于请求被拒而非生成失败，故只记分类计数、
        # 不动 success_rate 分母（由 record_generation_error_type 单独承载）。
        try:
            from ...monitor import get_health_monitor

            get_health_monitor().record_generation_error_type("safety")
        except Exception:  # noqa: BLE001
            pass
        return _error_html(request, f"⚠️ 内容安全检测未通过：{result.message}")
    return None


# S-R4: legacy history.db 迁移控制（幂等，只执行一次）
_legacy_history_migrated: bool = False
_legacy_migration_lock = threading.Lock()

# S-R4: legacy history.db 路径（原 create_history_db(SAVE_DIR) 使用的路径）
_LEGACY_HISTORY_DB_PATH: str = os.path.join(SAVE_DIR, "history.db")

ALLOWED_AUDIO_EXTENSIONS: set = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".wma", ".aac"}
_DIALECT_NAMES: set = {"四川话", "粤语", "吴语", "东北话", "河南话", "闽南语", "湖南话", "湖北话", "客家话"}


# ===========================================================================
# R9 新增公共工具函数（SSE / 任务 ID / 音频保存+历史 / 错误响应）
# ===========================================================================


def format_sse_event(
    event_type: str,
    data: dict[str, Any],
    event_id: str | None = None,
    retry: int | None = None,
) -> str:
    """构建 SSE (Server-Sent Events) 格式字符串。

    Why 注释 — retry 默认 3000ms：
        断线重连时 EventSource 默认 3 秒重试；前端若超过 15 秒没收到心跳
        会主动断开。此处显式声明 retry: 3000 让浏览器行为更可预期，
        便于前端统一心跳/重连策略调试。

    Args:
        event_type: 事件类型，如 "progress" / "complete" / "error" / "status" / "engine_switch"。
        data: 事件负载字典，需 JSON Serializable；非基本类型会通过 default=str 兜底。
        event_id: 可选事件 ID，断线重连时作为 Last-Event-ID 回传。
        retry: 可选断线重连间隔（毫秒），默认 3000。

    Returns:
        SSE 协议格式字符串：
            event: {event_type}\\n
            data: {...}\\n
            [id: {event_id}\\n]
            [retry: {retry}\\n]
            \\n
    """
    lines: list[str] = [f"event: {event_type}"]

    # data 序列化：非 JSON Serializable 对象先 default=str 兜底，再失败写 {} 保证流不中断
    try:
        data_str: str = json.dumps(data, ensure_ascii=False)
    except TypeError:
        try:
            data_str = json.dumps(data, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            logger.warning(f"format_sse_event: data 不可 JSON 序列化，已兜底 {{}} | event={event_type}")
            data_str = "{}"
    lines.append(f"data: {data_str}")

    if event_id is not None:
        lines.append(f"id: {event_id}")

    # Why: 显式 retry=3000，见函数级 Why 注释
    lines.append(f"retry: {retry if retry is not None else 3000}")

    return "\n".join(lines) + "\n\n"


def new_task_id() -> str:
    """生成新的任务 ID。

    使用 UUID4（无连字符的 32 位 hex 小写），避免 URL 转义问题，
    同时保证跨进程全局唯一性（不依赖自增 ID / 内存计数器）。

    Returns:
        32 字符小写 hex 字符串，如 "a1b2c3d4e5f6..."
    """
    return uuid.uuid4().hex


async def write_history_and_save_audio(
    audio_bytes: bytes,
    request: dict[str, Any],
    task_id: str,
    persona_id: str | None,
    engine: str,
) -> str:
    """保存生成音频到磁盘，并插入 history_db 历史记录。

    Why 顺序注释 — 先 save_audio 再 insert_history：
        history_db 记录中的 audio_url / filepath 依赖保存后生成的文件名。
        如果先 insert_history 后 save_audio 失败（如磁盘满、PermissionError），
        会产生 history 有记录但音频文件不存在的脏数据。必须先写文件成功，
        再写 DB；保存失败直接抛异常，DB 不会产生悬挂记录。

    Args:
        audio_bytes: 生成的原始音频二进制内容（.wav）。
        request: 请求上下文字典，至少含 "text" 字段用于历史预览。
        task_id: 任务 ID（new_task_id 生成），用作文件名前缀。
        persona_id: 关联音色 ID，无则 None。
        engine: 引擎标识，如 "voxcpm2" / "indextts2"。

    Returns:
        音频访问 URL 字符串，形如 "/api/audio/generated/{task_id}_{ts}.wav"

    Raises:
        PermissionError: 目标目录不可写时抛出，交由全局 error_handler 返回 500。
        OSError: 磁盘满等 IO 错误抛出。
    """
    timestamp: int = int(time.time() * 1000)
    filename: str = f"{task_id}_{timestamp}.wav"
    save_path: str = os.path.join(SAVE_DIR, filename)

    os.makedirs(SAVE_DIR, exist_ok=True)
    async with aiofiles.open(save_path, "wb") as f:
        await f.write(audio_bytes)

    text: str = request.get("text", "") if isinstance(request, dict) else ""
    duration_seconds: float = len(audio_bytes) / (44100 * 2 * 2)  # rough: 44.1kHz/16bit/stereo

    # S-R4: 首次调用时执行一次性 legacy 数据库迁移（幂等）
    _migrate_legacy_history_db_if_needed()

    db = get_history_db()
    # P2-7：db.insert 是同步 SQLite 操作，在 async 函数中直接调用会阻塞事件循环。
    # 移至线程池执行（与 _record_to_history_db 的调用方式一致）。
    await asyncio.to_thread(
        db.insert,
        {
            "filename": filename,
            "filepath": save_path,
            "created_at": datetime.now().isoformat(),
            "file_size_bytes": len(audio_bytes),
            "duration_seconds": round(duration_seconds, 2),
            "text_preview": text[:100] if text else "",
            "engine": engine,
            "model_type": request.get("model_type") if isinstance(request, dict) else None,
            "model_size": request.get("model_size") if isinstance(request, dict) else None,
            "persona_name": persona_id,
            "output_format": "wav",
            "is_success": True,
            "error_msg": None,
            "rtf": None,  # gen_start_ts 调用链未透传，RTF 暂置 None（保持既有行为，避免 F821）
        },
    )

    return f"/api/audio/generated/{filename}"


def build_generation_error_response(
    error: TTSError,
    task_id: str,
) -> JSONResponse:
    """构建统一的生成失败 JSON 响应结构。

    Args:
        error: 捕获到的 TTSError 派生异常（含 message / error_code）。
        task_id: 任务 ID，便于前端关联失败任务。

    Returns:
        JSONResponse: HTTP 500 / 4xx（根据异常类型动态取 status）
        {
            "status": "error",
            "task_id": str,
            "error": {
                "code": str,
                "message": str,
                "type": str,
            },
        }
    """
    error_type_name: str = type(error).__name__
    status_code: int = getattr(error, "status_code", 500)
    error_code: str = getattr(error, "error_code", error_type_name)
    message: str = str(error)

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "error",
            "task_id": task_id,
            "error": {
                "code": error_code,
                "message": message,
                "type": error_type_name,
            },
        },
    )


# ===========================================================================
# 信号量 / 并发控制
# ===========================================================================


async def _get_generation_semaphore(engine: str) -> asyncio.Semaphore:
    """Return the per-engine semaphore, creating it lazily if needed.

    Args:
        engine: 引擎名称（不区分大小写，默认 voxcpm2）。

    Returns:
        对应引擎的 asyncio.Semaphore 单例。
    """
    engine = (engine or "voxcpm2").lower()
    semaphore: asyncio.Semaphore | None = _generation_semaphores.get(engine)
    if semaphore is None:
        async with _generation_semaphore_lock:
            semaphore = _generation_semaphores.get(engine)
            if semaphore is None:
                semaphore = asyncio.Semaphore(_gen_max_concurrent())
                _generation_semaphores[engine] = semaphore
    return semaphore


# ===========================================================================
# S-R4: Legacy history.db 一次性迁移
# ===========================================================================


def _migrate_legacy_history_db_if_needed() -> None:
    """REFACTOR: [S-R4] 一次性迁移 legacy history.db 到统一位置。

    Root Cause:
        原 _get_history_db() 调用 create_history_db(SAVE_DIR) 创建独立实例，
        数据库路径为 {SAVE_DIR}/history.db (即 outputs/history.db)。
        而全局单例 get_history_db() 的路径为 {ROOT_DIR}/data/history.db。
        两个独立的 HistoryDatabase 实例操作不同的 SQLite 文件，导致：
        - 生成路由写入 outputs/history.db
        - 历史/音频页面读取 data/history.db
        - 用户看不到刚生成的历史记录（P0 数据一致性 Bug）

    Fix:
        统一使用 get_history_db() 全局单例。首次调用本函数时执行一次性迁移：
        1. 检查 legacy 路径是否存在 outputs/history.db
        2. 读取所有记录，用 insert_batch 合并到统一数据库（INSERT OR REPLACE 去重）
        3. 迁移成功后将 legacy 文件重命名为 .migrated_{timestamp}，防止再次迁移
        4. 幂等：已迁移则跳过；失败不阻塞应用启动（仅记录警告日志）

    Safety:
        - threading.Lock 保护，防止多线程并发触发
        - 双重检查 _legacy_history_migrated 标志位
        - 所有文件操作包裹 try/except，失败不影响主流程
    """
    global _legacy_history_migrated
    if _legacy_history_migrated:
        return

    with _legacy_migration_lock:
        if _legacy_history_migrated:
            return

        try:
            if not os.path.exists(_LEGACY_HISTORY_DB_PATH):
                _legacy_history_migrated = True
                return

            # 空文件直接重命名，避免后续无谓的读取
            try:
                if os.path.getsize(_LEGACY_HISTORY_DB_PATH) == 0:
                    try:
                        os.rename(_LEGACY_HISTORY_DB_PATH, f"{_LEGACY_HISTORY_DB_PATH}.empty")
                        logger.info("[S-R4] 检测到空的 legacy history.db，已重命名为 .empty")
                    except OSError as rename_err:
                        logger.debug(f"[S-R4] 重命名空 legacy 文件失败: {rename_err}")
                    _legacy_history_migrated = True
                    return
            except OSError as size_err:
                logger.debug(f"[S-R4] 获取 legacy 文件大小失败: {size_err}")
                _legacy_history_migrated = True
                return

            # 读取 legacy 数据库所有记录
            legacy_conn = sqlite3.connect(_LEGACY_HISTORY_DB_PATH)
            legacy_conn.row_factory = sqlite3.Row
            try:
                cursor = legacy_conn.execute("SELECT * FROM generation_history")
                rows = cursor.fetchall()
            except sqlite3.DatabaseError as read_err:
                logger.warning(f"[S-R4] 读取 legacy history.db 失败: {read_err}")
                _legacy_history_migrated = True
                return
            finally:
                with _suppress_os_errors():
                    legacy_conn.close()

            if not rows:
                # 空数据库（无记录），直接重命名
                try:
                    os.rename(_LEGACY_HISTORY_DB_PATH, f"{_LEGACY_HISTORY_DB_PATH}.migrated")
                    logger.info("[S-R4] legacy history.db 无记录，已重命名为 .migrated")
                except OSError as rename_err:
                    logger.debug(f"[S-R4] 重命名空 legacy 文件失败: {rename_err}")
                _legacy_history_migrated = True
                return

            # 转换为 dict 列表（sqlite3.Row 转 dict）
            records: list[dict[str, Any]] = [dict(row) for row in rows]

            # 写入目标数据库（get_history_db 全局单例）
            # insert_batch 内部使用 INSERT OR REPLACE，filepath UNIQUE 约束保证去重
            target_db = get_history_db()
            count: int = target_db.insert_batch(records)

            # 迁移成功，重命名旧文件（带时间戳防止冲突）
            migrated_path: str = f"{_LEGACY_HISTORY_DB_PATH}.migrated_{int(time.time())}"
            try:
                os.rename(_LEGACY_HISTORY_DB_PATH, migrated_path)
            except OSError as rename_err:
                logger.debug(f"[S-R4] 重命名 legacy 文件失败（迁移已完成）: {rename_err}")

            logger.info(
                f"[S-R4] 已迁移 {count} 条历史记录从 legacy history.db ({_LEGACY_HISTORY_DB_PATH}) 到统一数据库"
            )
        except Exception as migrate_err:  # noqa: BLE001
            # 任何异常都不阻塞应用启动
            logger.warning(f"[S-R4] 迁移 legacy history.db 失败: {migrate_err}（不影响应用启动）")
        finally:
            _legacy_history_migrated = True


class _suppress_os_errors:
    """E4: 上下文管理器，抑制 OSError（用于资源清理的 finally 块）。"""

    def __enter__(self) -> "_suppress_os_errors":
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


# ===========================================================================
# 引擎就绪校验 / 历史记录写入
# ===========================================================================


def _check_engine_ready(
    request: Any,
    engine_name: str | None = None,
) -> HTMLResponse | None:
    """检查当前引擎是否已加载，未就绪时返回 HTML 错误片段。

    Args:
        request: FastAPI 请求对象（用于访问 app.state.templates）。
        engine_name: 显式引擎名（None 则用 registry.current_engine）。

    Returns:
        未就绪时返回 HTMLResponse（400），就绪时返回 None。
    """
    from ...model_registry import registry

    if engine_name is None:
        engine_name = registry.current_engine
    if engine_name in ("indextts2", "indextts20"):
        if registry.indextts2_engine is None:
            return _error_html(
                request, "IndexTTS 模型未加载，请先加载模型", error_type="engine_not_ready", engine_id=engine_name
            )
    else:
        if registry.voxcpm_model is None:
            return _error_html(
                request, "VoxCPM2 模型未加载，请先加载模型", error_type="engine_not_ready", engine_id="voxcpm2"
            )
    return None


def _record_to_history_db(
    filepath: str,
    text: str,
    engine: str,
    duration: float,
    model_type: str | None = None,
    model_size: str | None = None,
    persona_name: str | None = None,
    output_format: str = "wav",
    is_success: bool = True,
    error_msg: str | None = None,
    audio_duration: float = 0.0,
) -> None:
    """将单次生成结果写入 history_db。

    Args:
        filepath: 音频绝对路径。
        text: 生成文本（前 100 字存入预览）。
        engine: 引擎标识。
        duration: 生成耗时（秒）。
        model_type: 模型类型（可选）。
        model_size: 模型大小标签（可选）。
        persona_name: 音色名称（可选）。
        output_format: 输出文件格式（默认 wav）。
        is_success: 本次是否生成成功。
        error_msg: 失败原因（仅 is_success=False 时使用）。
        audio_duration: 生成音频时长（秒），用于计算 RTF（实时率 = 生成耗时 / 音频时长）。
    """
    # S-R4: 首次调用时执行一次性 legacy 数据库迁移（幂等）
    _migrate_legacy_history_db_if_needed()

    try:
        # S-R4: 统一使用全局单例 get_history_db()，消除多实例路径不一致问题
        db = get_history_db()
        filename: str = os.path.basename(filepath) if filepath else ""
        file_size: int = os.path.getsize(filepath) if filepath and os.path.exists(filepath) else 0
        db.insert(
            {
                "filename": filename,
                "filepath": filepath or "",
                "created_at": datetime.now().isoformat(),
                "file_size_bytes": file_size,
                "duration_seconds": round(duration, 2),
                "text_preview": text[:100] if text else "",
                "engine": engine,
                "model_type": model_type,
                "model_size": model_size,
                "persona_name": persona_name,
                "output_format": output_format,
                "is_success": is_success,
                "error_msg": error_msg,
                "rtf": round(duration / audio_duration, 4) if audio_duration > 0 else None,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(f"历史记录数据库写入失败: {e}")


# ===========================================================================
# 错误/成功 HTML 片段 + 错误消息友好化
# ===========================================================================


def _safe_error_msg(exc: BaseException) -> str:
    """根据异常类型返回用户友好的错误消息。

    Args:
        exc: 已捕获的异常对象。

    Returns:
        用户可读的错误描述（中文，不超过 200 字符）。
    """
    if isinstance(exc, InsufficientVRAMError):
        return f"显存不足：{str(exc)}"
    if isinstance(exc, EngineSwitchError):
        return f"引擎切换失败：{str(exc)}"
    if isinstance(exc, TTSError):
        return str(exc)
    if isinstance(exc, RuntimeError):
        exc_str: str = str(exc)
        if "CUDA" in exc_str or "VRAM" in exc_str or "out of memory" in exc_str.lower():
            return "显存不足，请尝试缩短文本、关闭其他GPU程序，或在设置中切换到CPU模式"
        return f"运行时错误：{exc_str[:200]}"
    if isinstance(exc, ValueError):
        return f"参数错误：{str(exc)[:200]}"
    if isinstance(exc, FileNotFoundError):
        return "音频文件不存在或已被删除"
    if isinstance(exc, TimeoutError):
        return "请求超时，请稍后重试"
    if isinstance(exc, ConnectionError):
        return "网络连接异常，请检查网络"
    return "生成失败，请稍后重试"


# 结果卡内嵌播放器 HTML 模板（配合 static/js/embedded_player.js 自动初始化）：
# - data-embedded-player 标记容器，data-src 为音频 URL
# - .ep-play 播放/暂停按钮；canvas.ep-wave 波形；.ep-bar/.ep-bar-fill 可拖动进度条
# - .ep-time-cur / .ep-time-dur 当前时间 / 总时长
# 设计说明：完全自包含（内部 new Audio()），不依赖全局底部播放器
# (window.globalAudioPlayer)，即使全局播放器因缓存等原因未加载也始终可用。
_EMBEDDED_PLAYER_HTML = (
    '<div class="ep-player" data-embedded-player data-src="{audio_url}">'
    '<button type="button" class="ep-play" title="播放/暂停" aria-label="播放/暂停">'
    '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>'
    "</button>"
    '<div class="ep-body">'
    '<canvas class="ep-wave" height="44" aria-hidden="true"></canvas>'
    '<div class="ep-bar"><div class="ep-bar-fill"></div></div>'
    '<div class="ep-time"><span class="ep-time-cur">00:00</span><span class="ep-time-dur">00:00</span></div>'
    "</div></div>"
)


def _partial_success_html(filename: str, message: str, degraded_note: str) -> HTMLResponse:
    """渲染"部分成功"HTML 片段（降级重试成功，但质量下降）。

    Args:
        filename: 保存后的音频文件名。
        message: 主成功提示。
        degraded_note: 降级说明（橙字提示）。

    Returns:
        HTMLResponse（200），含内嵌播放器 + audio 标签 + 状态消息。
    """
    safe_filename: str = quote(filename, safe="")
    return HTMLResponse(
        f'<div data-audio-filename="{html.escape(filename)}">'
        f'<audio class="tts-audio-hidden" src="/api/audio/{safe_filename}"></audio>'
        f"{_EMBEDDED_PLAYER_HTML.format(audio_url='/api/audio/' + safe_filename)}"
        f'<div class="status-message success">{html.escape(message)}</div>'
        f'<div class="status-message warning" style="margin-top:8px;color:#f59e0b;">{html.escape(degraded_note)}</div>'
        f"</div>"
    )


def _log_generation(
    endpoint_name: str,
    text: str,
    engine: str,
    voice_or_persona: str,
    success: bool,
    duration: float,
    is_degraded: bool = False,
    error_msg: str | None = None,
) -> None:
    """记录生成操作日志（写入 health_monitor + operation_log）。

    Args:
        endpoint_name: 路由端点标识（如 clone/design/script）。
        text: 生成文本。
        engine: 引擎名称。
        voice_or_persona: 音色/角色名称。
        success: 是否成功。
        duration: 总耗时（秒）。
        is_degraded: 是否降级后成功（OOM 重试）。
        error_msg: 失败错误信息（仅 success=False）。
    """
    if success:
        increment_generation(success=True)
        details: dict[str, Any] = {
            "endpoint": endpoint_name,
            "engine": engine,
            "voice_persona": voice_or_persona,
            "text_length": len(text),
            "duration": round(duration, 2),
        }
        if is_degraded:
            details["degraded"] = True
        log_operation("generation", f"{endpoint_name} success ({duration:.1f}s)", details)
        log_audit("generation", detail=f"endpoint={endpoint_name} engine={engine}", outcome="success")
    else:
        increment_generation(success=False)
        details = {
            "endpoint": endpoint_name,
            "engine": engine,
            "voice_persona": voice_or_persona,
            "text_length": len(text),
            "duration": round(duration, 2),
        }
        if error_msg:
            details["error"] = str(error_msg)
        log_operation("generation", f"{endpoint_name} failed ({duration:.1f}s)", details)
        log_audit("generation", detail=f"endpoint={endpoint_name} engine={engine}", outcome="failure")


def _record_generation_failure(error_type: str, duration: float | None = None) -> None:
    """把一次生成失败写入 HealthMonitor 的分类计数与成功率分母（运维稳定性评估 P1）。

    Why 独立辅助 + 为什么这里必须调 record_generation(False)：
        盘点发现失败路径此前**从未**调用 ``monitor.record_generation(success=False)``
        （只有 5 处 success=True 调用），导致 HealthMonitor 的 errors/success_rate
        恒为满分、SLO 侧看不到任何失败。本函数是失败进入监控器的唯一入口。

    本函数绝不抛异常——监控埋点失败不能影响错误响应。

    Args:
        error_type: timeout / oom / param / safety / other。
        duration: 可选耗时（秒），提供时同步入延迟直方图。
    """
    try:
        from ...monitor import get_health_monitor

        mon = get_health_monitor()
        mon.record_generation(success=False)
        mon.record_generation_error_type(error_type)
        if duration is not None:
            mon.record_latency(duration)
    except Exception as fe:  # noqa: BLE001
        logger.debug("[metrics] 失败分类记录跳过: %s", fe)


# ===========================================================================
# 音频后处理（语速 / 增强 / 响度归一化）
# ===========================================================================


def _apply_post_processing_to_file(
    filename: str,
    tempo_factor: float,
    voice_enhancement: str | bool,
    target_lufs: float,
) -> str:
    """对已保存的音频文件应用后处理，输出为 *_pp.wav。

    Args:
        filename: 原始文件名（相对 SAVE_DIR，或绝对路径）。
        tempo_factor: 语速因子（1.0 不变，>1 更快，<1 更慢）。
        voice_enhancement: 是否启用语音增强（str "true"/"false" 或 bool）。
        target_lufs: 响度归一化目标 LUFS（默认 -16.0）。

    Returns:
        处理后的新文件名；无需处理则返回原文件名。
    """
    if tempo_factor == 1.0 and not voice_enhancement and target_lufs == -16.0:
        return filename

    from scipy.io import wavfile

    audio_path: str = filename if os.path.isabs(filename) else os.path.join(SAVE_DIR, filename)
    if not os.path.isfile(audio_path):
        logger.warning(f"后处理: 音频文件未找到: {audio_path}")
        return filename

    try:
        sr, data = wavfile.read(audio_path)
        if data.dtype == np.int16:
            audio: np.ndarray = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            audio = data.astype(np.float32) / 2147483648.0
        elif data.dtype == np.float32:
            audio = data.copy()
        else:
            audio = data.astype(np.float32)

        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        processed: np.ndarray = enhance_audio(
            audio,
            sr,
            normalize=True,
            tempo_factor=tempo_factor,
            voice_enhancement=voice_enhancement
            if isinstance(voice_enhancement, bool)
            else _parse_bool_form(voice_enhancement),
            target_lufs=target_lufs,
        )

        base, ext = os.path.splitext(filename)
        new_filename: str = f"{base}_pp{ext}"
        new_path: str = new_filename if os.path.isabs(new_filename) else os.path.join(SAVE_DIR, new_filename)

        # P2 安全修复（对齐 _save_wav_compatible）：后处理输出 *_pp.wav 同样嵌入
        # FFT 扩频水印（16-20kHz，source_id=WATERMARK_SOURCE_ID），确保经后处理
        # 导出的音频仍可溯源。水印失败仅记录 warning，不阻塞后处理（与主路径一致）。
        # 包装通用熔断器（BACKEND_DESIGN_ASSESSMENT §反模式 #6）：水印是「可选依赖」，
        # 其持续失败（如底层算子异常）会触发熔断，快速跳过水印而非反复重试拖慢主路径。
        try:
            from ...circuit_breaker import CircuitBreakerOpenError, get_circuit_breaker
            from ...watermark import WATERMARK_SOURCE_ID, watermark_audio

            _cb = get_circuit_breaker("watermark_svc")

            def _embed(audio: "np.ndarray") -> tuple:
                return watermark_audio(
                    audio.astype(np.float32),
                    sr,
                    enable=True,
                    source_id=WATERMARK_SOURCE_ID,
                )

            processed, wm_meta = _cb.call(_embed, processed)
            if wm_meta.get("watermarked"):
                logger.debug(
                    "后处理水印嵌入成功: source=%s, snr=%.1fdB, hash=%s",
                    wm_meta.get("source_id", ""),
                    wm_meta.get("snr_db", 0.0),
                    wm_meta.get("content_hash", ""),
                )
            else:
                logger.debug("后处理水印嵌入失败，*_pp.wav 已写入但无来源标识: %s", new_path)
        except CircuitBreakerOpenError as cboe:
            logger.warning("[后处理水印] 熔断器已打开，跳过水印嵌入（音频正常写出）: %s", cboe)
        except Exception as wm_exc:  # noqa: BLE001
            logger.debug("后处理水印嵌入异常（已忽略，音频正常写入）: %s", wm_exc)

        output: np.ndarray = (processed * 32768.0).clip(-32768, 32767).astype(np.int16)
        wavfile.write(new_path, sr, output)

        logger.info(f"后处理已应用: {filename} -> {new_filename}")
        return new_filename
    except Exception as e:  # noqa: BLE001
        logger.error(f"后处理失败 {filename}: {e}")
        return filename


def _error_html(
    request: Any,
    error_message: str,
    error_type: str = "general",
    engine_id: str = "",
    title_key: str = "gen_failed",
    status_code: int = 400,
    headers: dict[str, str] | None = None,
) -> HTMLResponse:
    """渲染 HTML 错误片段；优先使用 Jinja2 模板，模板不可用时降级返回安全字符串。

    Args:
        request: FastAPI 请求（用于访问 app.state.templates）。
        error_message: 错误提示文本。
        error_type: 错误分类（general / oom / validation / engine_not_ready）。
        engine_id: 引擎 ID（engine_not_ready 时渲染加载按钮）。
        title_key: 标题的 i18n 键。默认 ``gen_failed``（「生成失败」一类文案）；
            非生成类端点（如音色保存）必须传更贴切的键，否则校验错误会被冠以
            「生成时遇到了一点小状况」这种与操作无关的标题。
        status_code: HTTP 状态码，默认 400。排队超时传 429，硬超时传 503
            （运维稳定性评估 P1：此前 200/400+HTML 让监控统计不到这类失败）。
        headers: 额外响应头（如 ``Retry-After``），与内置 HX-Trigger 合并。

    Returns:
        HTMLResponse（按 status_code），携带 HX-Trigger toast 头及自定义头。
    """
    from ...i18n import get_lang, t

    lang: str = get_lang(request)
    # 合并自定义头与内置 toast 头（自定义头优先，避免覆盖 HX-Trigger）
    merged_headers: dict[str, str] = {
        "HX-Trigger": json.dumps({"tts-toast": {"type": "error", "message": html.escape(error_message)}}),
    }
    if headers:
        merged_headers.update(headers)
    try:
        templates = request.app.state.templates
        return templates.TemplateResponse(
            request=request,
            name="partials/error_message.html",
            context={
                "lang": lang,
                "error_message": error_message,
                "error_type": error_type,
                "engine_id": engine_id,
                "title_key": title_key,
            },
            status_code=status_code,
            headers=merged_headers,
        )
    except Exception:  # noqa: BLE001
        # 降级不再静默：模板路径失败会让 toast 消失，必须留下可查证据
        logger.exception("[_error_html] 错误模板渲染失败，降级为内联片段（toast 提示将丢失）")
        # 极端降级：仍保证 HTML 转义，防 XSS
        # 标题与按钮文案走 i18n，不再写死中文「生成失败」/「加载模型」——
        # 写死会让非生成类端点（音色保存等）的校验错误被冠以「生成失败」，
        # 且英文/日文界面上冒出中文。t() 自身有兜底，正常不会抛。
        try:
            title: str = html.escape(t(title_key, lang))
            load_label: str = html.escape(t("load_now", lang))
        except Exception:  # noqa: BLE001 - 降级路径的最后防线
            title = html.escape(title_key)
            load_label = "Load"
        load_btn: str = ""
        if error_type == "engine_not_ready" and engine_id:
            load_btn = (
                f'<button type="button" onclick="window.switchModel(\'{html.escape(engine_id)}\')" '
                f'style="margin-top:8px;padding:4px 12px;border-radius:4px;background:var(--p500);'
                f'color:#fff;border:none;cursor:pointer;font-size:12px">{load_label}</button>'
            )
        return HTMLResponse(
            f'<div class="tts-error-block" data-error-type="{html.escape(error_type)}">'
            f'<div class="error-title">{title}</div>'
            f'<div class="error-message">{html.escape(error_message)}</div>'
            f"{load_btn}"
            f"</div>",
            status_code=status_code,
            headers=merged_headers,
        )


# ===========================================================================
# 上传辅助 / 音色解析 / 输入校验
# ===========================================================================


def validate_reference_audio_quality(
    filepath: str,
    min_seconds: float = 3.0,
    silence_rms_threshold: float = 0.001,
) -> str | None:
    """P0 安全整改：校验参考音频时长与语音活动。

    使用 soundfile 解码后计算时长与 RMS 能量，拒绝过短或近静音文件。
    解码失败时 fail-open（不阻断上传），因为魔数校验已保证格式合法。

    Args:
        filepath: 已保存的音频文件绝对路径。
        min_seconds: 最小时长（秒），0 表示关闭时长校验。
        silence_rms_threshold: RMS 能量低于此值视为近静音。

    Returns:
        None 表示通过；字符串为失败原因（供错误响应使用）。
    """
    if min_seconds <= 0:
        return None
    try:
        import numpy as np
        import soundfile as sf

        data, sr = sf.read(filepath, always_2d=True)
        duration = len(data) / float(sr)
        if duration < min_seconds:
            return f"参考音频时长过短（{duration:.1f}s < {min_seconds}s），请上传至少 {min_seconds}s 的清晰语音"
        mono = data.mean(axis=1)
        rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))
        if rms < silence_rms_threshold:
            return "参考音频几乎为静音，请上传包含清晰人声的音频片段"
        return None
    except Exception:  # noqa: BLE001
        logger.debug("参考音频质量校验跳过（解码失败）: %s", filepath)
        return None


async def save_uploaded_audio(
    request: Any,
    upload_file: UploadFile | None,
    upload_dir: str | None = None,
    max_size_mb: int = 25,
    title_key: str = "gen_failed",
) -> tuple[str | None, HTMLResponse | None]:
    """保存上传的音频文件，返回 (path, None) 或 (None, error_html)。

    Args:
        request: FastAPI 请求（用于渲染错误 HTML）。
        upload_file: FastAPI UploadFile（可为 None）。
        upload_dir: 保存目录；默认 {SAVE_DIR}/uploads。
        max_size_mb: 最大文件大小 MB（仅用于错误消息显示；硬限制仍以 MAX_UPLOAD_SIZE_BYTES 为准）。
        title_key: 校验失败片段的标题 i18n 键。非生成类调用方（如音色保存）
            应传 ``op_failed``，否则上传校验错误会显示成「生成失败」。

    Returns:
        成功: (绝对保存路径, None)
        失败: (None, HTMLResponse 400)
    """
    if not upload_file or not upload_file.filename:
        return None, None

    if upload_dir is None:
        upload_dir = os.path.join(SAVE_DIR, "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    safe_name: str = os.path.basename(upload_file.filename)
    _, ext = os.path.splitext(safe_name)
    if ext.lower() not in ALLOWED_AUDIO_EXTENSIONS:
        return None, _error_html(request, f"不支持的音频格式: {ext}", title_key=title_key)

    upload_path: str = os.path.join(upload_dir, f"{int(time.time())}_{safe_name}")
    content: bytes = await upload_file.read()
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        return None, _error_html(
            request,
            f"上传文件大小超过 {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)}MB 限制",
            title_key=title_key,
        )

    # P0 安全修复：写盘前执行魔术字节校验（fail-closed 白名单模式）
    from ..audio import _validate_audio_content

    ext_lower = ext.lower()
    if not _validate_audio_content(content[:16], ext_lower):
        return None, _error_html(
            request,
            f"音频文件内容与声明格式不匹配（{ext_lower}），可能为伪装文件",
            title_key=title_key,
        )

    async with aiofiles.open(upload_path, "wb") as f:
        await f.write(content)

    # P0 安全整改：参考音频时长 + 语音活动门槛（拒绝过短/近静音文件）
    try:
        min_sec = float(get_config().pydantic_config.security.reference_audio_min_seconds)
    except Exception:  # noqa: BLE001
        min_sec = 3.0
    quality_err = validate_reference_audio_quality(upload_path, min_seconds=min_sec)
    if quality_err:
        with contextlib.suppress(OSError):
            os.remove(upload_path)
        return None, _error_html(request, quality_err, title_key=title_key)

    return upload_path, None


async def resolve_persona_ref(
    request: Any,
    persona_name: str | None,
) -> tuple[str | None, HTMLResponse | None]:
    """将 Persona 名称解析为参考音频路径。

    Args:
        request: FastAPI 请求（用于渲染错误 HTML）。
        persona_name: Persona 音色名称（basename）。

    Returns:
        成功: (wav_path, None)
        失败/不存在: (None, HTMLResponse 400)
    """
    if not persona_name:
        return None, None

    from ...persona_manager import PERSONA_DIR, load_persona_embedding

    safe_name: str = os.path.basename(persona_name)
    persona_data: Any | None = load_persona_embedding(safe_name)
    if persona_data is not None:
        # 处理不同返回格式（兼容 .pt 缓存嵌入和在线计算）
        # 情况 1: 在线计算分支 -> 返回二元组 (wav_path, ref_text)
        # 情况 2: .pt 缓存分支 -> 直接返回嵌入对象（此时音频文件必然存在）
        wav_path: str | None = None
        if isinstance(persona_data, tuple) and len(persona_data) == 2:
            wav_path, ref_text = persona_data
        elif isinstance(persona_data, (str, os.PathLike)) and os.path.isfile(str(persona_data)):
            wav_path = str(persona_data)
        else:
            # 缓存嵌入对象（张量或其他嵌入数据），wav 文件必然存在
            # （.pt 缓存只在 wav 存在后才会写入）
            candidate = os.path.join(PERSONA_DIR, f"{safe_name}.wav")
            wav_path = candidate

        if wav_path and os.path.isfile(wav_path):
            return wav_path, None
        else:
            return None, _error_html(request, f"音色文件不存在: {safe_name}")
    else:
        return None, _error_html(request, f"音色不存在: {safe_name}")


def pre_validate(
    request: Any,
    engine_name: str | None,
    text: str | None,
    max_length: int | None = None,
) -> HTMLResponse | None:
    """生成前预校验：引擎就绪 + 文本非空 + 长度限制。

    Args:
        request: FastAPI 请求。
        engine_name: 引擎名（None 用当前引擎）。
        text: 生成文本。
        max_length: 最大字符数（None 则不限制）。

    Returns:
        校验失败返回 HTMLResponse，成功返回 None。
    """
    model_not_ready: HTMLResponse | None = _check_engine_ready(request, engine_name)
    if model_not_ready:
        return model_not_ready
    if not text or not text.strip():
        return _error_html(request, "文本不能为空")
    if max_length and len(text) > max_length:
        return _error_html(request, f"文本长度超过限制（最大 {max_length} 字符）")
    return None


def _success_html(filename: str, status_message: str) -> HTMLResponse:
    """渲染成功 HTML 片段（audio + 成功状态）。

    Args:
        filename: 音频文件名。
        status_message: 成功提示文本。

    Returns:
        HTMLResponse（200）。
    """
    safe_filename: str = quote(filename, safe="")
    return HTMLResponse(
        f'<div data-audio-filename="{html.escape(filename)}">'
        f'<audio class="tts-audio-hidden" src="/api/audio/{safe_filename}"></audio>'
        f"{_EMBEDDED_PLAYER_HTML.format(audio_url='/api/audio/' + safe_filename)}"
        f'<div class="status-message success">{html.escape(status_message)}</div>'
        f"</div>"
    )


# ===========================================================================
# OOM 降级重试
# ===========================================================================


def _run_with_oom_retry(
    run_fn: Any,
    endpoint_name: str,
    degraded_fn: Any | None = None,
    max_retries: int = 2,
) -> tuple[Any, str, str | None]:
    """执行生成函数；OOM 时自动清理显存并使用降级参数重试。

    WHY 返回类型必须写成三元组：本函数**所有** return 点都是
    ``return result, msg, degraded_note``，但签名长期写成 ``tuple[Any, str]``，
    靠两处 ``# type: ignore[return-value]`` 压制。结果是 mypy 反过来把
    **正确的**三元解包调用点报成 ``Need more than 2 values to unpack``
    （clone.py 与 utils._execute_generation 各一处），把一个类型标注的谎
    变成了 34 条 misc 噪音里最像真 bug 的信号。标注必须与实现一致。

    Args:
        run_fn: 原始生成可调用，返回 (result, msg)。
        endpoint_name: 端点名称（用于日志）。
        degraded_fn: 降级生成可调用（相同签名）；None 则复用 run_fn。
        max_retries: 最大降级重试次数。

    Returns:
        (result, msg[, degraded_note]) 三元组（degraded_note 为 None 表示未降级）

    Raises:
        RuntimeError: 多次重试仍 OOM 时抛出友好中文错误。
        其他异常：非 OOM 异常原样抛出。
    """
    _generation_retry_counter["total"] += 1
    degraded_note: str | None = None
    retry_count: int = 0

    try:
        result, msg = run_fn()
        return result, msg, degraded_note
    except Exception as e:  # noqa: BLE001
        if not is_oom_error(e):
            logger.error(f"{endpoint_name} failed (non-OOM): {e}")
            raise

        logger.warning(f"{endpoint_name} hit OOM, attempting degraded retry...")
        _generation_retry_counter["oom_retries"] += 1
        free_gpu_memory()

        while retry_count < max_retries:
            retry_count += 1
            # 指数退避 + 抖动（BACKEND_DESIGN_ASSESSMENT §反模式 #6 中期项）：
            # 连续 OOM 往往是显存尚未完全释放，立即重试成功率低且加剧抖动；
            # 退避给 GPU 显存回收留出时间。base=1s，caps 在 8s，叠加 ±20% 抖动。
            backoff = min(1.0 * (2 ** (retry_count - 1)), 8.0)
            jitter = backoff * 0.2 * random.uniform(-1.0, 1.0)
            sleep_s = max(0.0, backoff + jitter)
            logger.info("%s OOM 重试前退避 %.1fs（第 %d/%d 次）", endpoint_name, sleep_s, retry_count, max_retries)
            time.sleep(sleep_s)
            try:
                degraded_note = "由于显存限制，已自动降低生成质量参数以完成生成。"
                if degraded_fn:
                    result, msg = degraded_fn()
                else:
                    result, msg = run_fn()
                return result, msg, degraded_note
            except Exception as retry_e:  # noqa: BLE001
                if not is_oom_error(retry_e):
                    raise
                logger.warning(f"{endpoint_name} OOM retry {retry_count}/{max_retries} failed")
                free_gpu_memory()

        # 运维稳定性评估 P1-4：抛专用异常子类而非裸 RuntimeError。
        # Why：耗尽文案是中文，不含 is_oom_error() 英文模式，此前监控分类会被
        # 误判为 other。专用类型让 oom 分类与自愈触发都可靠；继承 RuntimeError
        # 保持既有 except RuntimeError 调用方兼容。
        raise OOMRetryExhaustedError(
            "显存不足，已尝试降级重试但仍失败。请尝试缩短文本、关闭其他GPU程序，或在设置中切换到CPU模式"
        ) from None


# ---------------------------------------------------------------------------
# 运维稳定性评估 P1-4：OOM 后受控自动重载（无 on-call 场景的自愈路径）
# ---------------------------------------------------------------------------

# 上次自动重载的时间戳（epoch 秒）。0.0 = 从未重载过。
_last_oom_auto_reload_ts: float = 0.0


def _schedule_oom_auto_recovery(endpoint_name: str) -> None:
    """OOM（含重试耗尽）后调度一次后台自动重载：卸载当前引擎 → 重新加载。

    设计约束（对齐评估报告 P1-4「自动重载，失败则交由容器重启」）：
    - 默认开启（``generation.oom_auto_reload``），设 false 退回旧的「只靠容器重启」。
    - 冷却窗口（``oom_auto_reload_cooldown_s``）：窗口内不重复触发，防止权重损坏时
      反复重载风暴把 GPU 拖死。
    - 获取 per-engine 信号量（最多 120s）串行化：重载不与正在跑的推理抢显存；
      拿不到槽位就放弃本次（下次 OOM 再试）。
    - 重载后轮询 ``registry.is_engine_ready()``（上限 600s）判成败：成功发 INFO 告警、
      失败发 CRITICAL 告警并标 error——此时 ``/readyz`` 仍 503，容器编排据此重启兜底。
    - 本函数绝不抛异常：恢复调度失败不能影响正在返回给用户的 OOM 错误响应。

    Args:
        endpoint_name: 触发 OOM 的端点名（诊断用）。
    """
    global _last_oom_auto_reload_ts
    try:
        cfg = get_config().pydantic_config.generation
        if not getattr(cfg, "oom_auto_reload", True):
            return
        cooldown = float(getattr(cfg, "oom_auto_reload_cooldown_s", 300.0))
    except Exception:  # noqa: BLE001
        cooldown = 300.0
    now = time.time()
    if now - _last_oom_auto_reload_ts < cooldown:
        logger.warning(
            "[oom-recovery] %s 距上次自动重载仅 %.0fs（冷却 %.0fs），跳过本次",
            endpoint_name,
            now - _last_oom_auto_reload_ts,
            cooldown,
        )
        return
    _last_oom_auto_reload_ts = now

    async def _recover() -> None:
        from ...model_registry import registry
        from ...monitor import get_health_monitor
        from ...observability.alerting import Alert, AlertSeverity, get_alert_manager

        loop = asyncio.get_running_loop()
        sem: asyncio.Semaphore | None = None
        acquired = False
        engine: str = registry.current_engine or "voxcpm2"
        try:
            sem = await _get_generation_semaphore(engine)
            try:
                await asyncio.wait_for(sem.acquire(), timeout=120.0)
                acquired = True
            except asyncio.TimeoutError:
                logger.warning("[oom-recovery] 120s 内未获取到生成槽位，放弃本次自动重载（等待下次 OOM）")
                return

            logger.warning("[oom-recovery] 开始受控重载引擎 %s（卸载→重新加载）", engine)
            get_health_monitor().set_model_status("unloading")

            def _reload_worker() -> None:
                # 同步链路线程内执行：重新加载当前引擎。load_* 生成器内部已含
                # "卸载旧引擎 + GC + empty_cache" 阶段（见 model_manager_core.load
                # 的 phase=init），无需再单独 unload；且必须完整消费才会真正加载
                # （与 routes/model.py._run_load 同样的 4 元组消费方式）。
                from ...model_manager import (
                    load_indextts2,
                    load_indextts20,
                    load_voxcpm2,
                    switch_engine,
                )

                if engine == "indextts2":
                    _gen = load_indextts2()
                elif engine == "indextts20":
                    _gen = load_indextts20()
                elif engine == "voxcpm2":
                    _gen = load_voxcpm2()
                else:
                    _gen = switch_engine(engine)
                for _evt in _gen:
                    _status = _evt[0] if isinstance(_evt, tuple) and _evt else str(_evt)
                    logger.debug("[oom-recovery] 重载进度: %s", _status)

            try:
                await loop.run_in_executor(None, _reload_worker)
            except Exception as re_exc:  # noqa: BLE001
                get_health_monitor().set_model_status("error")
                get_alert_manager().emit(
                    Alert(
                        severity=AlertSeverity.CRITICAL,
                        title=f"OOM 后自动重载失败：{re_exc}",
                        detail=f"引擎={engine} 场景={endpoint_name}；服务保持未就绪（/readyz=503），交由容器重启兜底",
                        source="oom_auto_recovery",
                    )
                )
                return

            deadline = loop.time() + 600.0
            while loop.time() < deadline:
                if registry.is_engine_ready():
                    break
                await asyncio.sleep(2.0)
            if registry.is_engine_ready():
                get_health_monitor().set_model_status("ready")
                get_health_monitor().record_oom_auto_recovery()
                get_alert_manager().emit(
                    Alert(
                        severity=AlertSeverity.INFO,
                        title="OOM 后引擎已自动重载恢复",
                        detail=f"引擎={engine}，服务重新就绪（/readyz=200）",
                        source="oom_auto_recovery",
                    )
                )
                logger.info("[oom-recovery] 引擎 %s 自动重载完成，服务恢复就绪", engine)
            else:
                get_health_monitor().set_model_status("error")
                get_alert_manager().emit(
                    Alert(
                        severity=AlertSeverity.CRITICAL,
                        title="OOM 后自动重载超时未就绪",
                        detail=f"引擎={engine}；服务保持未就绪（/readyz=503），交由容器重启兜底",
                        source="oom_auto_recovery",
                    )
                )
                logger.error("[oom-recovery] 引擎 %s 重载后 600s 内未就绪", engine)
        except Exception as outer:  # noqa: BLE001
            logger.error("[oom-recovery] 自动重载流程异常（已忽略，交由后续请求/容器兜底）: %s", outer)
        finally:
            if acquired and sem is not None:
                with contextlib.suppress(Exception):
                    sem.release()

    try:
        asyncio.create_task(_recover())
    except Exception as ce:  # noqa: BLE001 - 无运行循环等极端场景
        logger.debug("[oom-recovery] 重载任务调度失败（忽略）: %s", ce)


def _store_generation_result(filename: str, idem_key: str | None, cache_key: str | None) -> None:
    """将一次成功生成的结果文件名写入缓存与幂等键（供后续命中短路）。

    Args:
        filename: 生成（含后处理）后的音频文件名（相对 SAVE_DIR）。
        idem_key: 请求携带的 Idempotency-Key（无则 None）。
        cache_key: 生成缓存键（缓存未启用则 None）。
    """
    try:
        if idem_key:
            _idempotency_store(idem_key, filename)
    except Exception as ie:  # noqa: BLE001
        logger.debug("[idempotency] 写入异常（忽略）: %s", ie)
    try:
        if cache_key:
            cache = _get_generation_cache()
            if cache is not None:
                cache.put(cache_key, filename)
    except Exception as ce:  # noqa: BLE001
        logger.debug("[gen-cache] 写入异常（忽略）: %s", ce)


def _parse_bool_form(value: Any) -> bool:
    """解析表单 bool 值（兼容 "true"/"1"/"yes" 字符串）。

    Args:
        value: 表单原始值（str / bool / int）。

    Returns:
        bool 解析结果。
    """
    return str(value).lower() in ("true", "1", "yes")


def _merge_dialect(instruction: str, dialect: str) -> str:
    """将方言标签合并到指令文本前。

    Args:
        instruction: 原指令文本。
        dialect: 方言名称（需在 _DIALECT_NAMES 白名单内）。

    Returns:
        合并后的指令；方言不在白名单内则原样返回 instruction。
    """
    if dialect and dialect in _DIALECT_NAMES:
        return (dialect + "，" + instruction) if instruction.strip() else dialect
    return instruction


# ===========================================================================
# 生成执行主流程（信号量 + 硬超时 + OOM 重试 + 历史记录）
# ===========================================================================


async def _execute_generation(
    request: Any,
    text: str,
    run_fn: Any,
    endpoint_name: str,
    voice_or_persona: str = "",
    model_type: str = "",
    engine: str = "voxcpm2",
    tempo_factor: float = 1.0,
    voice_enhancement: str = "false",
    target_lufs: float = -16.0,
    oom_retry: bool = True,
    degraded_fn: Any | None = None,
) -> HTMLResponse:
    """生成执行入口：获取 per-engine 信号量 → 加硬超时 → 调用实现函数。

    Args:
        request: FastAPI 请求对象。
        text: 生成文本。
        run_fn: 生成可调用（同步）。
        endpoint_name: 端点名称。
        voice_or_persona: 音色/角色名。
        model_type: 模型类型标签。
        engine: 引擎名。
        tempo_factor: 语速因子。
        voice_enhancement: 是否启用增强（"true"/"false" 字符串）。
        target_lufs: 目标响度 LUFS。
        oom_retry: 是否启用 OOM 降级重试。
        degraded_fn: 降级参数下的生成可调用。

    Returns:
        HTMLResponse（成功 200 / 失败 400）。
    """
    blocked = _ensure_content_safe(request, text)
    if blocked is not None:
        return blocked
    semaphore: asyncio.Semaphore = await _get_generation_semaphore(engine)
    try:
        await asyncio.wait_for(
            semaphore.acquire(),
            timeout=_gen_semaphore_timeout(),
        )
    except asyncio.TimeoutError:
        # 后端设计评估 P2-2：排队超时是客户端可重试的过载信号，返回 429 + Retry-After。
        # 此前返回 503，语义不准确（503 应保留给服务端硬超时/不可用）。
        _record_generation_failure("timeout")
        return _error_html(
            request,
            "系统繁忙，请稍后再试（等待超时）",
            error_type="queue_timeout",
            status_code=429,
            headers={"Retry-After": "30"},
        )
    try:
        # E6-1 ROBUSTNESS: 为生成任务本身加硬超时，防止超长文本/死循环耗尽信号量池。
        # 注意：底层 torch 推理不响应 asyncio 取消，但 run_in_executor 的 Future
        # 可被 wait_for 取消（线程仍会跑完，但 HTTP 客户端会立即收到超时响应，
        # 信号量也会被释放，避免请求堆积）。
        return await asyncio.wait_for(
            _execute_generation_impl(
                request,
                text,
                run_fn,
                endpoint_name,
                voice_or_persona,
                model_type,
                engine,
                tempo_factor,
                voice_enhancement,
                target_lufs,
                oom_retry,
                degraded_fn,
            ),
            timeout=_gen_hard_timeout(),
        )
    except asyncio.TimeoutError:
        logger.error(f"{endpoint_name} 生成超时 (>{_gen_hard_timeout()}s)，文本长度={len(text)}")
        _log_generation(
            endpoint_name,
            text,
            engine,
            voice_or_persona,
            False,
            _gen_hard_timeout(),
            error_msg=f"generation timeout (>{_gen_hard_timeout()}s)",
        )
        # 运维稳定性评估 P1：超时纳入失败分类计数（timeout），/metrics 可见。
        _record_generation_failure("timeout")
        # 运维稳定性评估 P1：超时后主动清理 GPU 缓存（best-effort，后台线程执行，
        # 不阻塞错误响应）。若底层推理线程尚未结束，empty_cache 回收有限，
        # 但其结束后残留的临时音频文件由 lifespan 的 30 分钟周期清理任务兜底。
        try:
            asyncio.get_running_loop().create_task(asyncio.to_thread(free_gpu_memory))
        except Exception as ce:  # noqa: BLE001
            logger.debug("超时后显存清理调度失败（忽略）: %s", ce)
        return _error_html(
            request,
            f"生成超时（超过 {_gen_hard_timeout():.0f} 秒），请尝试缩短文本或减少并发",
            error_type="timeout",
            status_code=503,
            headers={"Retry-After": "60"},
        )
    finally:
        semaphore.release()


async def _execute_generation_impl(
    request: Any,
    text: str,
    run_fn: Any,
    endpoint_name: str,
    voice_or_persona: str = "",
    model_type: str = "",
    engine: str = "voxcpm2",
    tempo_factor: float = 1.0,
    voice_enhancement: str = "false",
    target_lufs: float = -16.0,
    oom_retry: bool = True,
    degraded_fn: Any | None = None,
) -> HTMLResponse:
    """生成核心实现：线程池执行同步 run_fn → 记录历史 → 后处理 → 返回 HTML。

    短路逻辑（在真正进入 GPU 推理前）：
        1. 幂等键：若请求携带 ``Idempotency-Key`` 且近期命中，直接复用上次结果文件；
        2. 生成缓存：若 ``generation.cache_enabled`` 且该 (引擎+文本+参数) 已缓存，
           复用既有音频文件，跳过 GPU 推理。

    Args:
        同 _execute_generation。

    Returns:
        HTMLResponse（成功 / OOM 降级成功 / 失败 / 幂等或缓存命中）。
    """
    # --- 短路 1：幂等键去重（防止客户端重试重复生成）---
    idem_key: str | None = None
    try:
        idem_key = getattr(request, "headers", {}).get("Idempotency-Key") if hasattr(request, "headers") else None
        if idem_key:
            hit_file = _idempotency_lookup(idem_key)
            if hit_file:
                logger.info("[idempotency] 命中幂等键，复用结果: %s", hit_file)
                _log_generation(endpoint_name, text, engine, voice_or_persona, True, 0.0)
                monitor = get_health_monitor()
                monitor.record_generation(success=True)
                return _success_html(hit_file, "生成成功（幂等命中）")
    except Exception as ide:  # noqa: BLE001
        logger.debug("[idempotency] 查询异常（忽略）: %s", ide)

    # --- 短路 2：生成结果缓存 ---
    cache_key: str | None = None
    try:
        cache = _get_generation_cache()
        if cache is not None:
            cache_key = _build_generation_cache_key(
                engine, text, voice_or_persona, tempo_factor, voice_enhancement, target_lufs
            )
            hit_file = cache.get(cache_key)
            if hit_file and os.path.isfile(hit_file if os.path.isabs(hit_file) else os.path.join(SAVE_DIR, hit_file)):
                logger.info("[gen-cache] 命中，复用结果: %s", hit_file)
                _log_generation(endpoint_name, text, engine, voice_or_persona, True, 0.0)
                monitor = get_health_monitor()
                monitor.record_generation(success=True)
                return _success_html(hit_file, "生成成功（缓存命中）")
    except Exception as ce:  # noqa: BLE001
        logger.debug("[gen-cache] 查询异常（忽略）: %s", ce)

    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    start_time: float = time.monotonic()
    try:
        if oom_retry:
            result, msg, degraded_note = await loop.run_in_executor(
                None, lambda: _run_with_oom_retry(run_fn, endpoint_name, degraded_fn=degraded_fn)
            )
        else:
            result, msg = await loop.run_in_executor(None, run_fn)
            degraded_note = None
        duration: float = time.monotonic() - start_time
        if result is None:
            _log_generation(endpoint_name, text, engine, voice_or_persona, False, duration, error_msg=msg)
            _record_generation_failure("other", duration)
            return _error_html(request, msg)
        # --- P2-3: bad_case_retry 质量检测接入生成管线 ---
        # 此前 bad_case_retry 模块有完整测试但零生产调用（死代码）。
        # 此处接入 detect_failure_type：生成成功后对音频做静音/过短/爆音/重复检测，
        # 命中则记录 bad_case 指标 + warning 日志（不自动重试：run_fn 为参数闭包，
        # 调参重试需重构生成函数签名，风险较高，留待后续迭代）。
        bad_case_note: str | None = None
        try:
            if isinstance(result, tuple) and len(result) >= 2 and hasattr(result[0], "shape"):
                from ...bad_case_retry import detect_failure_type

                _wav = result[0]
                _sr = int(result[1]) if len(result) >= 2 else 48000
                _has_fail, _ftype, _reason = detect_failure_type(_wav, _sr)
                if _has_fail:
                    bad_case_note = f"质量检测: {_reason}"
                    logger.warning("[bad-case] %s 引擎=%s 类型=%s", endpoint_name, engine, _ftype.value)
                    _record_generation_failure("bad_case", duration)
        except Exception as bce:  # noqa: BLE001
            logger.debug("[bad-case] 质量检测异常（忽略）: %s", bce)
        is_degraded: bool = degraded_note is not None
        _log_generation(endpoint_name, text, engine, voice_or_persona, True, duration, is_degraded=is_degraded)
        _time_estimator.record(len(text), duration, engine, segment_count=1)
        if isinstance(result, tuple) and len(result) >= 3:
            audio_path: str = result[2] if os.path.isabs(result[2]) else os.path.join(SAVE_DIR, result[2])
            await asyncio.to_thread(
                _record_to_history_db,
                filepath=audio_path,
                text=text,
                engine=engine,
                duration=duration,
                model_type=model_type,
                output_format="wav",
                is_success=True,
            )
        monitor = get_health_monitor()
        monitor.record_generation(success=True)
        # 运维稳定性评估 P1：成功耗时入直方图（支撑 p95 / 30s SLO 尾部判定）。
        monitor.record_latency(duration)
        filename: str = result[2]
        pp_voice_enhancement: bool = _parse_bool_form(voice_enhancement)
        filename = await asyncio.to_thread(
            _apply_post_processing_to_file, filename, tempo_factor, pp_voice_enhancement, target_lufs
        )
        # 写回生成缓存 / 幂等缓存（命中即可复用本次结果文件，跳过下次 GPU 推理）
        _store_generation_result(filename, idem_key, cache_key)
        if degraded_note:
            return _partial_success_html(filename, msg, degraded_note)
        if bad_case_note:
            return _partial_success_html(filename, msg, bad_case_note)
        return _success_html(filename, msg)
    except Exception as e:  # noqa: BLE001
        duration = time.monotonic() - start_time
        logger.error(f"{endpoint_name} generation failed: {e}")
        _log_generation(endpoint_name, text, engine, voice_or_persona, False, duration, error_msg=str(e))
        error_type: str = "general"
        metric_type: str = "other"
        if isinstance(e, OOMRetryExhaustedError) or is_oom_error(e):
            error_type = "oom"
            metric_type = "oom"
            # 运维稳定性评估 P1-4：OOM（含重试耗尽）后调度受控自动重载。
            # 冷却窗口内的重复 OOM 不会反复触发；调度失败不影响错误响应。
            _schedule_oom_auto_recovery(endpoint_name)
        elif isinstance(e, ValueError):
            error_type = "validation"
            metric_type = "param"
        # 运维稳定性评估 P1：失败进入监控器（此前失败从不调用 record_generation，
        # success_rate 恒为 100%，SLO 侧存在系统性盲区）。
        _record_generation_failure(metric_type, duration)
        return _error_html(request, _safe_error_msg(e), error_type=error_type)


# ===========================================================================
# 共享工具：音频上传验证 / 文本验证 / 参考音频加载
# ===========================================================================

# 支持的音频格式（与 ALLOWED_AUDIO_EXTENSIONS 保持一致）
SUPPORTED_AUDIO_FORMATS: set = ALLOWED_AUDIO_EXTENSIONS
MAX_AUDIO_SIZE_MB: int = 50  # 最大音频文件大小（MB）
MAX_TEXT_LENGTH_DEFAULT: int = 5000  # 默认最大文本长度


async def validate_audio_upload(
    file: UploadFile,
    max_size_mb: int = MAX_AUDIO_SIZE_MB,
    supported_formats: set = SUPPORTED_AUDIO_FORMATS,
) -> tuple[bool, str]:
    """验证上传的音频文件扩展名和大小。

    Args:
        file: FastAPI UploadFile。
        max_size_mb: 最大文件大小（MB）。
        supported_formats: 允许的扩展名集合。

    Returns:
        (is_valid, error_message)：error_message 为空串表示验证通过。
    """
    if not file or not file.filename:
        return False, "未选择音频文件"

    ext: str = os.path.splitext(file.filename)[1].lower()
    if ext not in supported_formats:
        return False, f"不支持的音频格式: {ext}，支持: {', '.join(sorted(supported_formats))}"

    try:
        content: bytes = await file.read()
        await file.seek(0)
        size_mb: float = len(content) / (1024 * 1024)
        if size_mb > max_size_mb:
            return False, f"音频文件过大: {size_mb:.1f}MB，最大支持: {max_size_mb}MB"
    except (OSError, ValueError) as read_err:
        logger.warning(f"读取音频文件失败: {read_err}")
        return False, f"读取音频文件失败: {read_err}"

    # P0 安全修复：写盘前执行魔术字节校验（fail-closed 白名单模式）
    from ..audio import _validate_audio_content

    if not _validate_audio_content(content[:16], ext):
        return False, f"音频文件内容与声明格式不匹配（{ext}），可能为伪装文件"

    return True, ""


def validate_text_input(
    text: str,
    max_length: int = MAX_TEXT_LENGTH_DEFAULT,
    field_name: str = "文本",
) -> tuple[bool, str]:
    """验证文本输入非空且长度合法。

    Args:
        text: 原始文本。
        max_length: 最大字符数。
        field_name: 错误消息中显示的字段名（默认"文本"）。

    Returns:
        (is_valid, error_message)。
    """
    if not text or not text.strip():
        return False, f"请输入{field_name}"

    if len(text) > max_length:
        return False, f"{field_name}过长: {len(text)}字，最大支持: {max_length}字"

    return True, ""


async def load_reference_audio(
    request: Any,
    file: UploadFile,
    output_dir: str,
    prefix: str = "ref",
) -> tuple[str | None, str]:
    """校验并保存参考音频文件到指定目录。

    Args:
        request: FastAPI 请求（当前未直接使用，保留以对齐签名）。
        file: FastAPI UploadFile。
        output_dir: 目标输出目录。
        prefix: 文件名前缀（默认 "ref"）。

    Returns:
        成功: (绝对路径, "")
        失败: (None, 错误信息)
    """
    is_valid, error = await validate_audio_upload(file)
    if not is_valid:
        return None, error

    try:
        content: bytes = await file.read()
        filename: str = f"{prefix}_{file.filename}"
        filepath: str = os.path.join(output_dir, filename)

        os.makedirs(output_dir, exist_ok=True)
        async with aiofiles.open(filepath, "wb") as f:
            await f.write(content)

        return filepath, ""
    except (OSError, PermissionError) as save_err:
        logger.error(f"保存参考音频失败: {save_err}")
        return None, f"保存参考音频失败: {save_err}"
