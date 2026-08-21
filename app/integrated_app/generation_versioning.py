"""生成版本管理模块。

提供 TTS 生成结果的版本谱系追踪功能，支持：
- 为每次生成分配唯一版本 ID
- 记录生成参数、文本、引擎、音频路径
- 支持父子版本关系（用于"换个语气再来一次"等场景的版本链追踪）
- 与 history_db.py 集成或独立使用 SQLite 存储

设计要点：
- 单例模式全局复用 VersionManager
- 线程安全（使用 threading.Lock）
- best-effort 写入：失败时返回 None 但不中断生成流程
- 支持版本查询和谱系遍历
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger("tts_multimodel")


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class GenerationVersion:
    """单次生成的版本记录。

    Attributes:
        version_id: 唯一版本 ID（UUID4 字符串）。
        parent_id: 父版本 ID（None 表示根版本）。
        audio_path: 生成音频文件的绝对路径。
        text: 输入文本。
        params: 生成参数字典。
        engine: 使用的引擎名称。
        created_at: 创建时间戳（Unix 时间，秒）。
        created_at_str: 可读的创建时间字符串。
    """

    version_id: str
    parent_id: str | None
    audio_path: str
    text: str
    params: dict[str, Any]
    engine: str
    created_at: float
    created_at_str: str = ""

    def __post_init__(self) -> None:
        if not self.created_at_str:
            self.created_at_str = datetime.fromtimestamp(self.created_at).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# VersionManager
# ---------------------------------------------------------------------------


class VersionManager:
    """生成版本管理器。

    管理 TTS 生成结果的版本谱系，提供版本保存、查询、遍历功能。
    使用独立的 SQLite 数据库（或内存存储作为回退），与 history_db 分离。

    线程安全：所有公共方法通过 _lock 互斥保护。

    Usage::

        vm = get_version_manager()
        version_id = vm.save_generation(
            audio_path="/path/to/audio.wav",
            text="你好世界",
            params={"cfg": 2.0, "steps": 10},
            engine="voxcpm2",
        )
    """

    def __init__(self, db_path: str | None = None) -> None:
        """初始化版本管理器。

        Args:
            db_path: SQLite 数据库文件路径。None 时使用默认路径
                     （在 config.SAVE_DIR 下的 generation_versions.db）。
        """
        self._lock = threading.Lock()
        self._db_path: str | None = None
        self._conn: sqlite3.Connection | None = None
        self._use_memory = False

        if db_path is None:
            try:
                from .config import SAVE_DIR

                db_path = os.path.join(SAVE_DIR, "generation_versions.db")
            except Exception:
                self._use_memory = True
                logger.debug("[VersionManager] 无法获取 SAVE_DIR，使用内存存储")

        if not self._use_memory and db_path:
            try:
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                self._db_path = db_path
                self._init_db()
            except Exception as e:
                logger.warning(f"[VersionManager] 初始化数据库失败，使用内存存储: {e}")
                self._use_memory = True

        if self._use_memory:
            self._memory_store: dict[str, GenerationVersion] = {}

    def _init_db(self) -> None:
        """初始化数据库表结构。"""
        if self._db_path is None:
            return

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS generation_versions (
                version_id TEXT PRIMARY KEY,
                parent_id TEXT,
                audio_path TEXT NOT NULL,
                text TEXT,
                params TEXT,
                engine TEXT,
                created_at REAL NOT NULL,
                created_at_str TEXT
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_parent_id ON generation_versions(parent_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON generation_versions(created_at)")
        self._conn.commit()

    def save_generation(
        self,
        audio_path: str,
        text: str,
        params: dict[str, Any],
        engine: str,
        parent_id: str | None = None,
    ) -> str | None:
        """保存一次生成的版本记录。

        Args:
            audio_path: 生成音频文件的绝对路径。
            text: 输入文本。
            params: 生成参数字典（会被 JSON 序列化）。
            engine: 使用的引擎名称（如 "voxcpm2", "indextts2"）。
            parent_id: 父版本 ID，用于追踪版本谱系。None 表示根版本。

        Returns:
            新创建的版本 ID（UUID4 字符串），失败时返回 None。
        """
        version_id = str(uuid.uuid4())
        created_at = time.time()

        version = GenerationVersion(
            version_id=version_id,
            parent_id=parent_id,
            audio_path=audio_path,
            text=text,
            params=params,
            engine=engine,
            created_at=created_at,
        )

        with self._lock:
            try:
                if self._use_memory:
                    self._memory_store[version_id] = version
                else:
                    self._save_to_db(version)
                logger.debug(f"[VersionManager] 保存版本记录: {version_id}, engine={engine}")
                return version_id
            except Exception as e:
                logger.warning(f"[VersionManager] 保存版本记录失败: {e}")
                return None

    def _save_to_db(self, version: GenerationVersion) -> None:
        """将版本记录写入 SQLite 数据库。

        Args:
            version: 要保存的 GenerationVersion 实例。
        """
        if self._conn is None:
            raise RuntimeError("数据库连接未初始化")

        params_json = json.dumps(version.params, ensure_ascii=False)
        self._conn.execute(
            """
            INSERT OR REPLACE INTO generation_versions
            (version_id, parent_id, audio_path, text, params, engine, created_at, created_at_str)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version.version_id,
                version.parent_id,
                version.audio_path,
                version.text,
                params_json,
                version.engine,
                version.created_at,
                version.created_at_str,
            ),
        )
        self._conn.commit()

    def get_version(self, version_id: str) -> GenerationVersion | None:
        """查询指定版本 ID 的记录。

        Args:
            version_id: 版本 ID。

        Returns:
            GenerationVersion 实例，不存在时返回 None。
        """
        with self._lock:
            try:
                if self._use_memory:
                    return self._memory_store.get(version_id)
                else:
                    return self._get_from_db(version_id)
            except Exception as e:
                logger.warning(f"[VersionManager] 查询版本记录失败: {e}")
                return None

    def _get_from_db(self, version_id: str) -> GenerationVersion | None:
        """从数据库查询版本记录。

        Args:
            version_id: 版本 ID。

        Returns:
            GenerationVersion 实例，不存在时返回 None。
        """
        if self._conn is None:
            return None

        row = self._conn.execute(
            "SELECT * FROM generation_versions WHERE version_id = ?",
            (version_id,),
        ).fetchone()

        if row is None:
            return None

        return self._row_to_version(row)

    def _row_to_version(self, row: sqlite3.Row | tuple) -> GenerationVersion:
        """将数据库行转换为 GenerationVersion 实例。

        Args:
            row: 数据库行（tuple 格式）。

        Returns:
            GenerationVersion 实例。
        """
        params = {}
        if row[4]:
            with contextlib.suppress(json.JSONDecodeError):
                params = json.loads(row[4])

        return GenerationVersion(
            version_id=row[0],
            parent_id=row[1],
            audio_path=row[2],
            text=row[3] or "",
            params=params,
            engine=row[5] or "",
            created_at=row[6],
            created_at_str=row[7] or "",
        )

    def get_version_chain(self, version_id: str) -> list[GenerationVersion]:
        """获取版本谱系链（从根版本到指定版本）。

        从给定版本 ID 向上追溯 parent_id，直到根版本（parent_id=None），
        返回按时间从旧到新排列的版本列表。

        Args:
            version_id: 起始版本 ID。

        Returns:
            版本链列表（根版本在前，目标版本在后）。
        """
        chain: list[GenerationVersion] = []
        current_id: str | None = version_id

        while current_id:
            version = self.get_version(current_id)
            if version is None:
                break
            chain.append(version)
            current_id = version.parent_id

        chain.reverse()
        return chain

    def list_recent(self, limit: int = 50, engine: str | None = None) -> list[GenerationVersion]:
        """列出最近的版本记录。

        Args:
            limit: 最大返回数量。
            engine: 按引擎过滤，None 表示不过滤。

        Returns:
            按创建时间倒序排列的版本列表。
        """
        with self._lock:
            try:
                if self._use_memory:
                    versions = list(self._memory_store.values())
                    if engine:
                        versions = [v for v in versions if v.engine == engine]
                    versions.sort(key=lambda v: v.created_at, reverse=True)
                    return versions[:limit]
                else:
                    return self._list_recent_from_db(limit, engine)
            except Exception as e:
                logger.warning(f"[VersionManager] 列出最近版本失败: {e}")
                return []

    def _list_recent_from_db(self, limit: int, engine: str | None) -> list[GenerationVersion]:
        """从数据库查询最近版本。

        Args:
            limit: 最大返回数量。
            engine: 按引擎过滤。

        Returns:
            版本列表。
        """
        if self._conn is None:
            return []

        if engine:
            rows = self._conn.execute(
                "SELECT * FROM generation_versions WHERE engine = ? ORDER BY created_at DESC LIMIT ?",
                (engine, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM generation_versions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        return [self._row_to_version(row) for row in rows]

    def close(self) -> None:
        """关闭数据库连接，释放资源。"""
        with self._lock:
            if self._conn is not None:
                with contextlib.suppress(Exception):
                    self._conn.close()
                self._conn = None


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

_version_manager: VersionManager | None = None
_manager_lock = threading.Lock()


def get_version_manager() -> VersionManager:
    """获取全局 VersionManager 单例。

    Returns:
        VersionManager 实例。
    """
    global _version_manager
    if _version_manager is None:
        with _manager_lock:
            if _version_manager is None:
                _version_manager = VersionManager()
    return _version_manager


def reset_version_manager() -> None:
    """重置全局 VersionManager 单例（用于测试或配置变更）。"""
    global _version_manager
    with _manager_lock:
        if _version_manager is not None:
            _version_manager.close()
        _version_manager = None
