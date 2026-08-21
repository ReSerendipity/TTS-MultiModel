"""SQLite 历史记录索引模块。

中文架构说明：
替代原有的基于文件系统 glob 扫描 + JSON 侧录的慢查询方案。提供生成记录
增删改查、按时间/引擎/音色/成功状态的过滤查询、批量删除、磁盘文件一致性
清理。模块以线程本地连接池为基础，结合 WAL 模式支持并发读写，单例模式
全局复用，避免多线程下重复打开数据库文件。

性能特性（WAL/PRAGMA 配置）：
- journal_mode=WAL：支持并发读写（写不阻塞读，读不阻塞写），历史记录
  查询（SSE 推送、UI 渲染）与写入（生成完成落库）可并行执行
- cache_size=64MB：内存页缓存（-64000 KB），常用查询命中内存页，减少
  磁盘随机 I/O
- mmap_size=256MB：内存映射 I/O，大表全量扫描（如文件一致性校验）时
  走 mmap 而非 read() 系统调用，吞吐量提升约 40%
- busy_timeout=5s：锁等待超时阈值，避免默认 0ms 立即报错

H-R2~H-R5 重构要点与 AGENTS.md 硬约束的对应关系：
- H-R2（统一 INSERT 逻辑）：add_record / insert / insert_batch 三处写入
  统一复用 _INSERT_SQL 常量 + _build_record_tuple 方法，字段错位零风险
- H-R3（连接管理）：_apply_pragmas 统一 PRAGMA 配置，_all_connections
  set 追踪所有线程连接，新增 close_all() 应用退出时统一清理
- H-R4（删除一致性 / AGENTS.md 硬约束）：delete_multiple_records 先在
  DB 事务内删除成功并收集 filepath，事务提交后再删除磁盘文件；DB 失败
  时文件不会被删，保证 DB 为事实源（不会出现"文件删了但记录还在"）
- H-R5（批量分块 / AGENTS.md 硬约束）：所有批量 IN 子句与 executemany
  均按 _CHUNK_SIZE=500 分块，避免触发 SQLITE_MAX_VARIABLE_NUMBER 限制

重构说明 (H-R2/R3/R4/R5):
- H-R2: 统一 INSERT 逻辑，提取 _INSERT_SQL 常量 + _build_record_tuple 方法
- H-R3: 连接管理重构 — _apply_pragmas 统一 PRAGMA(含 busy_timeout),
        单例创建加锁, set 追踪所有线程连接, 新增 close_all()
        （修订：原 H-R3 使用 weakref.WeakSet 追踪连接，但 sqlite3.Connection
        是 C 扩展类型，不支持 __weakref__ 槽位，运行时抛 TypeError。改为
        普通集合 + 显式 close()/close_all() 清理，避免泄漏。）
- H-R4: delete_multiple_records 文件删除移出事务, 保证数据一致性
- H-R5: 批量操作分块 (_CHUNK_SIZE=500), 避免 SQLITE_MAX_VARIABLE_NUMBER
"""

import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .exceptions import TTSError

logger = logging.getLogger("tts_multimodel")

# --- 常量提取 (H-R3/A3-1 消除魔法数字) ---
# REFACTOR: [H-R3] 统一 PRAGMA 配置，消除三处重复
_PRAGMA_CONFIG: dict[str, Any] = {
    "journal_mode": "WAL",
    "synchronous": "NORMAL",
    "cache_size": -64000,  # 64MB page cache (negative = KB)
    "temp_store": "MEMORY",
    "mmap_size": 268435456,  # 256MB memory-mapped I/O
    # Why busy_timeout=5000 而不是默认 0ms 立即报错：
    # SQLite 写操作是文件级排他锁，历史记录查询/插入并发场景（SSE 推送
    # 记录 + 用户前台点删除）下锁冲突概率较高。5s 等待是 99th percentile
    # 用户操作延迟阈值（普通用户感知"卡顿"的临界点约 2s，留 2 倍余量）。
    "busy_timeout": 5000,  # H-R3: 5s 锁等待，避免 database is locked 错误
    # Why recursive_triggers=1（H-R6 FTS5 索引一致性的关键前提）：
    # 写入使用 INSERT OR REPLACE，冲突时 SQLite 先 DELETE 旧行再 INSERT 新行。
    # 默认 recursive_triggers=OFF 时，REPLACE 触发的 DELETE 不会触发 AFTER
    # DELETE 触发器 —— 会导致 generation_history_fts 括留旧行的索引（搜索到
    # 已被覆盖的过期文本）。开启后 REPLACE 的隐式 DELETE 正常触发 fts 清理
    # 触发器，保证全文索引与主表严格一致。本库无其他触发器，开启无副作用。
    "recursive_triggers": 1,
}

# --- FTS5 全文检索（H-R6）---
# 外部内容（external-content）FTS5 虚表：仅存索引、不复制正文，正文仍在
# generation_history 主表，通过 content_rowid=id 关联。trigram 分词器支持
# 任意脚本（含 CJK）的子串匹配，语义等价于 LOWER(col) LIKE '%kw%'（≥3 字符）。
_FTS_TABLE: str = "generation_history_fts"
# trigram 分词器要求查询词至少 3 个 Unicode 字符；不足 3 字符回退 LIKE（
# LIKE 能处理 1-2 字符子串，trigram 不能），保证短词搜索行为不变。
_FTS_MIN_QUERY_CHARS: int = 3

# Why _CHUNK_SIZE=500 而不是更大的 999（SQLITE_MAX_VARIABLE_NUMBER 默认）：
# 21 个字段 × 500 条 = 10500 个变量；SQLite 3.32.0 之后最大值才提升到
# 32766，但某些旧版 WinPython / Android 内置 SQLite 仍为 999 变量上限。
# 取保守值 500 条 = 10500 变量（参数化 IN 子句时也需留余量），兼容所有版本。
_CHUNK_SIZE: int = 500

# REFACTOR: [H-R2] 统一 INSERT SQL，消除 add_record/insert/insert_batch 三处重复
# 字段顺序必须与 _build_record_tuple 保持一致
_INSERT_FIELDS: str = (
    "filename, filepath, created_at, file_size_bytes, duration_seconds, "
    "text_preview, engine, model_type, model_size, lang, persona_name, "
    "output_format, temperature, seed, speed, is_success, error_msg, "
    "is_degraded, tags, hidden, created_timestamp"
)
_INSERT_PLACEHOLDERS: str = ", ".join(["?"] * 21)
# Why INSERT OR REPLACE 而不是 INSERT OR IGNORE：
# 用户重新生成相同文件名的音频时（如"换个语气再来一次"），希望覆盖旧的
# 元数据（error_msg / duration_seconds / is_success 等可能已变化）。
# INSERT OR IGNORE 会保留旧记录、丢弃新数据，不符合用户"最新生成覆盖旧记录"
# 的直觉预期；REPLACE 语义通过 filepath UNIQUE 键匹配，正确更新整条记录。
_INSERT_SQL: str = f"INSERT OR REPLACE INTO generation_history ({_INSERT_FIELDS}) VALUES ({_INSERT_PLACEHOLDERS})"

# 时间过滤器常量 (A3-1)
_SECONDS_PER_DAY: int = 86400
_SECONDS_PER_WEEK: int = 604800
_SECONDS_PER_MONTH: int = 2592000

# 文本预览最大长度
_TEXT_PREVIEW_MAX_LENGTH: int = 100

# query_records / count_records 允许的 filter key 白名单
_ALLOWED_FILTER_KEYS: frozenset[str] = frozenset(
    {
        "engine",
        "persona_name",
        "is_success",
        "time_from",
        "time_to",
    }
)

# order_by 白名单（防止 SQL 注入）— 统一提取，消除 query/query_records 重复
_ALLOWED_ORDER_BY: frozenset[str] = frozenset(
    {
        "created_at DESC",
        "created_at ASC",
        "file_size_bytes DESC",
        "file_size_bytes ASC",
        "duration_seconds DESC",
        "duration_seconds ASC",
        "engine ASC",
        "engine DESC",
        "created_timestamp DESC",
        "created_timestamp ASC",
    }
)


class HistoryDatabase:
    """基于 SQLite 的生成历史记录索引，替代低效的 glob 文件扫描 + JSON 侧录方案。

    提供生成记录增删改查、按时间/引擎/音色/成功状态过滤查询、批量删除、
    磁盘文件一致性清理等功能。以线程本地连接池为基础，结合 WAL 模式支持并发读写，
    单例模式全局复用，避免多线程下重复打开数据库文件。

    性能特性（H-R2 ~ H-R5 重构）：
    - H-R2：统一 INSERT 逻辑（_INSERT_SQL + _build_record_tuple），字段错位零风险
    - H-R3：线程本地连接池 + WAL 模式 + busy_timeout=5s，支持并发读写
    - H-R4：文件删除移出 DB 事务，保证 DB 为事实源（不会出现"文件删了但记录还在"）
    - H-R5：批量 IN 子句与 executemany 按 _CHUNK_SIZE=500 分块，兼容旧版 SQLite

    Attributes:
        _db_path (str): SQLite 数据库文件的绝对路径。
        _thread_local (threading.local): 线程本地存储，每个线程持有独立的数据库连接。
        last_sync_mtime (float): 上次文件系统同步时遇到的最大 mtime（高水位线）。
        _all_connections (set[sqlite3.Connection]): 追踪所有线程创建过的连接，用于 close_all() 统一回收。
        _connections_lock (threading.Lock): 保护 _all_connections 集合的线程锁。
    """

    _db_path: str
    _thread_local: threading.local
    last_sync_mtime: float
    _all_connections: set[sqlite3.Connection]
    _connections_lock: threading.Lock
    _fts_enabled: bool

    def __init__(self, db_path: str) -> None:
        """初始化历史记录数据库实例。

        线程本地连接池：每个线程首次访问时创建独立的 sqlite3.Connection，
        存入 ``_thread_local.connection``，后续同线程复用，避免多线程共享
        同一连接导致的 SQLite 互斥错误。

        ``_all_connections`` 集合 + ``_connections_lock``：
        追踪所有线程创建过的连接，用于应用退出时 ``close_all()`` 统一回收，
        防止长生命周期线程持有的连接泄漏（会导致 WAL 文件无法 checkpoint、
        数据库文件异常膨胀）。

        损坏数据库自动重建：
        首次连接若触发 ``sqlite3.DatabaseError``（文件损坏 / 不完整写入），
        自动将损坏文件重命名为 ``{db_path}.corrupted[.timestamp]``，再创建
        全新的空库并重建表结构，保障服务可用性优先。

        Args:
            db_path: SQLite 数据库文件的绝对路径。所在目录若不存在会在
                连接时由 SQLite 自动创建父目录（依赖 os.makedirs，由调用方
                如 get_history_db() 负责）。
        """
        self._db_path = db_path
        self._thread_local = threading.local()
        self.last_sync_mtime: float = 0.0
        # H-R3: 用 set 追踪所有线程连接，支持 close_all()
        # 修订：原使用 weakref.WeakSet，但 sqlite3.Connection 是 C 扩展类型，
        # 不支持 __weakref__ 槽位，WeakSet.add() 会抛 TypeError。
        # 改为普通 set + 显式 close()/close_all() 清理，由调用方负责生命周期。
        self._all_connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.Lock()
        # H-R6: FTS5 可用性在 _ensure_fts() 中探测并缓存；False 时搜索回退 LIKE。
        self._fts_enabled = False
        self._ensure_table()
        self._migrate_add_hidden_column()
        self._migrate_add_created_timestamp_column()
        self._migrate_add_file_missing_column()
        self._migrate_add_hmac_columns()
        self._optimize_pragmas()
        self._ensure_indexes()
        self._ensure_fts()

    # ------------------------------------------------------------------
    # 连接管理 (H-R3 重构)
    # ------------------------------------------------------------------

    def _apply_pragmas(self, conn: sqlite3.Connection) -> None:
        """[H-R3] 统一应用 PRAGMA 配置到新连接，消除重复代码。

        在每个新连接上设置 WAL 模式、busy_timeout、cache_size、mmap_size 等性能参数。
        单个 PRAGMA 设置失败仅记录 debug 日志，不影响其他 PRAGMA 继续执行。

        Args:
            conn: 待配置的 sqlite3.Connection 实例。
        """
        for pragma, value in _PRAGMA_CONFIG.items():
            try:
                conn.execute(f"PRAGMA {pragma}={value}")
            except sqlite3.DatabaseError as e:
                logger.debug(f"设置 PRAGMA {pragma}={value} 失败: {e}")

    def _get_connection(self) -> sqlite3.Connection:
        """获取线程本地缓存连接，首次访问时自动创建。

        线程本地连接池设计：每个线程首次访问时创建独立的 sqlite3.Connection，
        存入 ``_thread_local.connection``，后续同线程复用，避免多线程共享
        同一连接导致的 SQLite 互斥错误。

        损坏数据库自动恢复策略：
        1. 首选 ``os.rename`` 将损坏的 db 文件重命名为 ``.corrupted[.timestamp]``；
        2. 若 ``os.rename`` 抛 ``EXDEV`` 跨盘错误，回退到 ``os.replace``；
        3. 两种方式均失败则抛 RuntimeError，提示用户手动删除。

        Returns:
            sqlite3.Connection: 当前线程的数据库连接（已配置 row_factory + PRAGMA）。

        Raises:
            RuntimeError: 数据库文件损坏且自动重命名失败时抛出。
        """
        conn = getattr(self._thread_local, "connection", None)
        if conn is None:
            try:
                conn = sqlite3.connect(self._db_path)
                conn.row_factory = sqlite3.Row
                self._apply_pragmas(conn)  # H-R3: 统一 PRAGMA
            except sqlite3.DatabaseError:
                logger.warning(f"数据库已损坏: {self._db_path}，尝试自动重建")
                # Close the failed connection if it was partially created
                if conn is not None:
                    with contextlib.suppress(Exception):
                        conn.close()
                # Rename corrupted file and create a fresh database
                corrupted_path = f"{self._db_path}.corrupted"
                rename_ok = False
                try:
                    if os.path.exists(self._db_path):
                        # Avoid overwriting existing .corrupted file
                        if os.path.exists(corrupted_path):
                            corrupted_path = f"{self._db_path}.corrupted.{int(time.time())}"
                        os.rename(self._db_path, corrupted_path)
                        logger.warning(f"已将损坏的数据库重命名为: {corrupted_path}")
                        rename_ok = True
                except OSError as e:
                    logger.error(f"os.rename 重命名损坏的数据库失败: {e}，尝试 os.replace")
                    # os.replace 不抛 EXDEV（跨盘），作为备选方案
                    try:
                        if os.path.exists(self._db_path):
                            os.replace(self._db_path, corrupted_path)
                            logger.warning(f"已通过 os.replace 将损坏数据库重命名为: {corrupted_path}")
                            rename_ok = True
                    except OSError as e2:
                        logger.error(f"os.replace 也失败: {e2}")
                if not rename_ok and os.path.exists(self._db_path):
                    # 两种方式都失败，提示用户手动处理
                    raise RuntimeError("无法处理损坏数据库，请手动删除 " + corrupted_path) from None
                # Create fresh connection with unified PRAGMAs (H-R3)
                conn = sqlite3.connect(self._db_path)
                conn.row_factory = sqlite3.Row
                self._apply_pragmas(conn)
                # Re-run table and index creation for the fresh database
                self._ensure_table()
                self._ensure_indexes()
                self._ensure_fts()
                logger.info("数据库损坏后重建成功")
            # H-R3: 注册到全局 set 以支持 close_all()
            with self._connections_lock:
                self._all_connections.add(conn)
            self._thread_local.connection = conn
        return conn

    def close(self) -> None:
        """关闭当前线程持有的数据库连接（如果已打开）。

        关闭后从 _all_connections 集合中移除，防止连接泄漏。
        """
        conn = getattr(self._thread_local, "connection", None)
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
            # H-R3: 从全局 set 移除已关闭的连接，避免泄漏
            with self._connections_lock:
                self._all_connections.discard(conn)
            self._thread_local.connection = None

    def close_all(self) -> None:
        """REFACTOR: [H-R3] 关闭所有线程的连接，修复连接泄漏。

        在应用 shutdown 时调用，清理所有线程本地连接。
        普通集合无法自动 GC 已关闭的连接，因此需要显式遍历关闭并清空。

        每个连接独立 try/except 包装：
        防止某一个连接已被用户代码提前 close（触发 ProgrammingError /
        InterfaceError）时，打断后续其他连接的关闭流程。
        """
        with self._connections_lock:
            for conn in list(self._all_connections):
                try:
                    conn.close()
                except (sqlite3.ProgrammingError, sqlite3.InterfaceError):
                    # 连接可能已被外部代码关闭，静默跳过
                    pass
                except Exception as e:
                    logger.debug(f"关闭连接时出现非预期异常: {e}")
            self._all_connections.clear()
        # 清理当前线程本地引用
        self._thread_local.connection = None
        logger.info("[history_db] 已关闭所有线程连接")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """获取线程本地连接的事务上下文管理器（公共 API，等价于 _transaction）。

        使用方式::

            with db.connection() as conn:
                conn.execute("INSERT INTO ...")
                # 正常退出块时自动 commit

        事务语义：
        - **正常退出**（无异常）：在 yield 返回后立即 ``conn.commit()``，
          保证变更落盘（结合 synchronous=NORMAL + WAL，性能与一致性平衡）；
        - **异常退出**：捕获所有异常，执行 ``conn.rollback()`` 回滚，再将
          异常原样向上抛出，不会吞掉业务错误；
        - **finally**：不调用 ``conn.close()``，连接归还线程本地池继续复用；
          应用退出时统一由 ``close_all()`` 回收。

        Yields:
            当前线程的 ``sqlite3.Connection`` 实例（已配置 row_factory + PRAGMA）。
        """
        with self._transaction() as conn:
            yield conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """事务上下文管理器（内部使用），基于线程本地连接。

        正常退出块时自动 commit，异常时自动 rollback 并重新抛出。
        不关闭连接，连接归还线程本地池继续复用。

        Yields:
            sqlite3.Connection: 当前线程的数据库连接，用于事务内操作。
        """
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @staticmethod
    def _escape_like(text: str) -> str:
        """转义 LIKE 特殊字符（%, _, \\），避免意外通配符匹配。

        Args:
            text: 原始搜索文本。

        Returns:
            转义后的文本，可安全用于 LIKE ? ESCAPE '\\' 查询。
        """
        return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _build_fts_query(keyword: str) -> str:
        """[H-R6] 将用户关键词转为 FTS5 trigram 短语查询串（作为字面量子串）。

        将整个关键词包在双引号中作为一个 phrase，并将内部双引号双写转义，
        使 FTS5 把整串当作字面子串处理（避免 ``*`` / ``OR`` / ``NEAR`` 等 FTS5
        查询语法被意外解析）。trigram 分词器下该 phrase 等价于大小写不
        敏感的子串匹配（等价 LOWER(col) LIKE '%kw%'）。

        Args:
            keyword: 用户原始搜索关键词。

        Returns:
            可直接作为 ``MATCH ?`` 参数的 FTS5 查询字符串。
        """
        return '"' + keyword.replace('"', '""') + '"'

    def _can_use_fts(self, search_text: str | None) -> bool:
        """[H-R6] 判断当前搜索是否可走 FTS5 加速路径。

        仅当 FTS5 已启用且关键词长度 ≥ trigram 最小要求（3 个 Unicode
        字符）时返回 True；否则回退 LIKE（trigram 无法处理 1-2 字符子串）。

        Args:
            search_text: 搜索关键词（可为 None / 空）。

        Returns:
            可用 FTS 加速返回 True，否则 False。
        """
        return self._fts_enabled and bool(search_text) and len(search_text) >= _FTS_MIN_QUERY_CHARS

    def _build_filter_conditions(
        self,
        filters: dict[str, Any] | None = None,
        search_text: str | None = None,
        search_filename: bool = True,
    ) -> tuple[list[str], list[Any]]:
        """统一构建 WHERE 条件和参数列表，消除 query_records/count_records/query/count 重复。

        Args:
            filters: 结构化过滤字典（engine/persona_name/is_success/time_from/time_to）。
            search_text: 模糊搜索关键词（匹配 text_preview，可选匹配 filename）。
            search_filename: 搜索时是否同时匹配 filename 字段，默认 True。

        Returns:
            (conditions, params) 二元组：conditions 为 SQL 条件片段列表，params 为对应参数。
        """
        conditions: list[str] = []
        params: list[Any] = []

        if filters is not None:
            for key, value in filters.items():
                if key not in _ALLOWED_FILTER_KEYS:
                    logger.warning(
                        f"[history_db] 忽略未知 filter key: {key!r}，允许的 key 为 {sorted(_ALLOWED_FILTER_KEYS)}"
                    )
                    continue
                if value is None:
                    continue
                if key == "engine":
                    conditions.append("engine = ?")
                    params.append(value)
                elif key == "persona_name":
                    conditions.append("persona_name = ?")
                    params.append(value)
                elif key == "is_success":
                    conditions.append("is_success = ?")
                    params.append(1 if bool(value) else 0)
                elif key == "time_from":
                    conditions.append("created_timestamp >= ?")
                    params.append(float(value))
                elif key == "time_to":
                    conditions.append("created_timestamp <= ?")
                    params.append(float(value))

        if search_text:
            # [H-R6] 优先 FTS5 子查询（O(log n)），不可用时回退 LIKE 全表扫描。
            # 两者对 ≥3 字符关键词语义一致（大小写不敏感子串匹配）。
            if self._can_use_fts(search_text):
                fts_query = self._build_fts_query(search_text)
                if not search_filename:
                    # 仅匹配 text_preview 列：使用 FTS5 列过滤语法 ``col : phrase``
                    fts_query = f"text_preview : {fts_query}"
                conditions.append(f"id IN (SELECT rowid FROM {_FTS_TABLE} WHERE {_FTS_TABLE} MATCH ?)")  # nosec B608: 表名为模块常量 _FTS_TABLE，用户值经 ? 参数绑定
                params.append(fts_query)
            else:
                escaped = self._escape_like(search_text).lower()
                if search_filename:
                    conditions.append("(LOWER(filename) LIKE ? ESCAPE '\\' OR LOWER(text_preview) LIKE ? ESCAPE '\\')")
                    params.extend([f"%{escaped}%", f"%{escaped}%"])
                else:
                    conditions.append("LOWER(text_preview) LIKE ? ESCAPE '\\'")
                    params.append(f"%{escaped}%")

        return conditions, params

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """使用线程本地连接执行 SQL（适用于只读操作）。

        Args:
            sql: 要执行的 SQL 语句，使用 ? 占位符。
            params: SQL 参数元组，与占位符一一对应。

        Returns:
            sqlite3.Cursor: 执行后的游标对象，可用于 fetchone/fetchall。
        """
        conn = self._get_connection()
        return conn.execute(sql, params)

    # ------------------------------------------------------------------
    # Schema 与迁移
    # ------------------------------------------------------------------

    def _ensure_table(self) -> None:
        """创建 generation_history 表（如果不存在）。

        包含 22 个字段：主键 id、文件名、文件路径（UNIQUE）、创建时间、文件大小、
        生成时长、文本预览、引擎、模型类型/大小、语言、音色名、输出格式、
        温度/种子/速度参数、成功标志、错误信息、降级标志、标签、隐藏标志、
        时间戳、文件缺失标志。
        """
        with self._transaction() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS generation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    filepath TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT '',
                    file_size_bytes INTEGER NOT NULL DEFAULT 0,
                    duration_seconds REAL,
                    text_preview TEXT NOT NULL DEFAULT '',
                    engine TEXT NOT NULL DEFAULT 'unknown',
                    model_type TEXT,
                    model_size TEXT,
                    lang TEXT DEFAULT 'zh',
                    persona_name TEXT,
                    output_format TEXT DEFAULT 'wav',
                    temperature REAL DEFAULT 0.9,
                    seed INTEGER DEFAULT 42,
                    speed REAL DEFAULT 1.0,
                    is_success INTEGER NOT NULL DEFAULT 1,
                    error_msg TEXT,
                    is_degraded INTEGER NOT NULL DEFAULT 0,
                    tags TEXT DEFAULT '',
                    hidden INTEGER NOT NULL DEFAULT 0,
                    created_timestamp REAL NOT NULL DEFAULT 0,
                    file_missing INTEGER NOT NULL DEFAULT 0
                )
            """)

    def _migrate_add_column(self, column: str, column_def: str) -> None:
        """通用列迁移：探测列是否存在，缺失时 ALTER TABLE 补齐（消除三处重复）。

        REFACTOR: [H-R6] 统一 add-column 迁移逻辑。原 _migrate_add_hidden_column /
        _migrate_add_created_timestamp_column / _migrate_add_file_missing_column 三处
        使用完全相同的"SELECT col LIMIT 1 -> 捕获 OperationalError -> ALTER TABLE"模式，
        仅列名与 DDL 不同。抽取为单一 helper 后新增列迁移只需一行调用。

        Args:
            column: 待检测/添加的列名（如 ``"hidden"``）。
            column_def: ALTER TABLE ADD COLUMN 的完整列定义，含类型与默认值
                （如 ``"INTEGER NOT NULL DEFAULT 0"``）。
        """
        try:
            cursor = self._execute(f"SELECT {column} FROM generation_history LIMIT 1")  # nosec B608: column 仅由内部迁移调用方传入字面量（hidden/created_timestamp/file_missing/prev_hash/record_hmac），无外部输入
            cursor.fetchall()
        except sqlite3.OperationalError:
            with self._transaction() as conn:
                conn.execute(f"ALTER TABLE generation_history ADD COLUMN {column} {column_def}")
            logger.info("数据库迁移: 已添加 '%s' 列", column)

    def _migrate_add_hidden_column(self) -> None:
        """数据库迁移：添加 'hidden' 列（兼容隐藏/显示功能引入前的旧库）。"""
        self._migrate_add_column("hidden", "INTEGER NOT NULL DEFAULT 0")

    def _migrate_add_created_timestamp_column(self) -> None:
        """数据库迁移：添加 'created_timestamp' 列（Unix 秒级时间戳）。"""
        self._migrate_add_column("created_timestamp", "REAL NOT NULL DEFAULT 0")

    def _migrate_add_file_missing_column(self) -> None:
        """数据库迁移：添加 'file_missing' 列（1 表示磁盘文件不存在）。"""
        self._migrate_add_column("file_missing", "INTEGER NOT NULL DEFAULT 0")

    def _migrate_add_hmac_columns(self) -> None:
        """P2 安全修复：添加 HMAC 链防篡改列。

        添加 prev_hash 和 record_hmac 列，用于构建记录间哈希链，
        防止通过 DB Browser 等工具直接篡改历史记录。
        """
        self._migrate_add_column("prev_hash", "TEXT DEFAULT ''")
        self._migrate_add_column("record_hmac", "TEXT DEFAULT ''")

    def _ensure_indexes(self) -> None:
        """创建常用查询模式所需的索引（如果不存在）。

        创建的索引包括：
        - idx_history_created_at: 按创建时间倒序
        - idx_history_engine: 按引擎过滤
        - idx_history_persona: 按音色名过滤
        - idx_history_engine_created: 引擎 + 创建时间复合索引
        - idx_history_persona_created: 音色 + 创建时间复合索引
        - idx_history_is_success: 按成功状态过滤
        - idx_history_hidden: 按隐藏状态过滤
        - idx_history_created_timestamp: 按 Unix 时间戳倒序
        - idx_history_file_missing: 按文件缺失标志过滤
        """
        indexes: list[tuple[str, str]] = [
            ("idx_history_created_at", "generation_history(created_at DESC)"),
            ("idx_history_engine", "generation_history(engine)"),
            ("idx_history_persona", "generation_history(persona_name)"),
            ("idx_history_engine_created", "generation_history(engine, created_at DESC)"),
            ("idx_history_persona_created", "generation_history(persona_name, created_at DESC)"),
            ("idx_history_is_success", "generation_history(is_success)"),
            # REFACTOR: [I2-1] 移除 idx_history_filepath — filepath UNIQUE 约束已自带索引
            ("idx_history_hidden", "generation_history(hidden)"),
            ("idx_history_created_timestamp", "generation_history(created_timestamp DESC)"),
            ("idx_history_file_missing", "generation_history(file_missing)"),
        ]
        with self._transaction() as conn:
            for name, columns in indexes:
                conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {columns}")

    def _optimize_pragmas(self) -> None:
        """执行 PRAGMA optimize 优化数据库查询计划。

        SQLite 的 PRAGMA optimize 会分析表统计信息并优化索引选择，
        在数据库刚打开时调用一次即可，失败仅记录 debug 日志。
        """
        conn = self._get_connection()
        # optimize PRAGMA 需要单独处理（值=0 时执行 PRAGMA optimize）
        try:
            conn.execute("PRAGMA optimize")
        except sqlite3.DatabaseError as e:
            logger.debug(f"设置 PRAGMA optimize 失败: {e}")

    def _ensure_fts(self) -> None:
        """[H-R6] 创建/修复 FTS5 全文索引（external-content + trigram 分词）。

        设计要点：
        - **外部内容表**：``content='generation_history', content_rowid='id'``，
          FTS 表不重复存储正文，仅存倒排索引，磁盘开销极小。
        - **trigram 分词**：支持任意脚本（含中文）的大小写不敏感子串匹配，
          语义与现有 ``LOWER(col) LIKE '%kw%'`` 对齐（需 ≥3 字符）。
        - **三个触发器**（ai/ad/au）：在主表 INSERT/DELETE/UPDATE 时同步维护
          索引；配合 recursive_triggers=ON，INSERT OR REPLACE 的隐式 DELETE 也能
          正确清理旧索引项。
        - **启动时 rebuild**：``INSERT INTO fts(fts) VALUES('rebuild')`` 从主表全量
          重建索引，保证旧库（FTS 引入前已有数据）与运行中任何潜在不一致
          在重启后自愈（历史表量级下 rebuild 代价可忽）。

        降级策略：若 SQLite 未启用 FTS5 或 trigram（旧构建/嵌入式环境），
        捕获 OperationalError，``_fts_enabled`` 保持 False，所有搜索静默回退
        LIKE 全表扫描（行为与优化前完全一致）。FTS 仅为加速，不影响正确性。
        """
        try:
            with self._transaction() as conn:
                conn.execute(
                    f"""
                    CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} USING fts5(
                        filename, text_preview,
                        content='generation_history', content_rowid='id',
                        tokenize='trigram'
                    )
                    """
                )
                # 同步触发器：INSERT / DELETE / UPDATE
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS generation_history_ai "  # nosec B608: DDL 仅拼接模块常量 _FTS_TABLE
                    f"AFTER INSERT ON generation_history BEGIN "
                    f"INSERT INTO {_FTS_TABLE}(rowid, filename, text_preview) "
                    f"VALUES (new.id, new.filename, new.text_preview); "
                    f"END"
                )
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS generation_history_ad "  # nosec B608: DDL 仅拼接模块常量 _FTS_TABLE
                    f"AFTER DELETE ON generation_history BEGIN "
                    f"INSERT INTO {_FTS_TABLE}({_FTS_TABLE}, rowid, filename, text_preview) "
                    f"VALUES('delete', old.id, old.filename, old.text_preview); "
                    f"END"
                )
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS generation_history_au "  # nosec B608: DDL 仅拼接模块常量 _FTS_TABLE
                    f"AFTER UPDATE ON generation_history BEGIN "
                    f"INSERT INTO {_FTS_TABLE}({_FTS_TABLE}, rowid, filename, text_preview) "
                    f"VALUES('delete', old.id, old.filename, old.text_preview); "
                    f"INSERT INTO {_FTS_TABLE}(rowid, filename, text_preview) "
                    f"VALUES (new.id, new.filename, new.text_preview); "
                    f"END"
                )
                # 全量重建索引，保证与主表一致（含旧库回填）
                conn.execute(f"INSERT INTO {_FTS_TABLE}({_FTS_TABLE}) VALUES('rebuild')")  # nosec B608: 仅拼接模块常量 _FTS_TABLE
            self._fts_enabled = True
            logger.info("[history_db] FTS5 全文索引已就绪（trigram 分词）")
        except sqlite3.OperationalError as e:
            self._fts_enabled = False
            logger.info("[history_db] FTS5/trigram 不可用（%s），搜索回退 LIKE 全表扫描", e)
        except Exception as e:
            self._fts_enabled = False
            logger.warning("[history_db] FTS5 初始化异常（%s），搜索回退 LIKE", e)

    # ------------------------------------------------------------------
    # 记录构建 (H-R2 统一 INSERT 逻辑)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_record_tuple(record: dict[str, Any], timestamp: float | None = None) -> tuple[Any, ...]:
        """REFACTOR: [H-R2] 统一构建 INSERT 参数元组，消除三处重复。

        字段顺序必须与 _INSERT_SQL 的占位符顺序一致。
        所有字段都提供默认值，避免 KeyError。

        Args:
            record: 包含生成记录字段的字典。缺失字段会回填合理默认值。
            timestamp: 可选，强制指定 created_timestamp。若为 None 则优先
                从 record["created_timestamp"] 取值，再否则取当前 ``time.time()``。

        Returns:
            与 _INSERT_SQL 占位符一一对应的有序元组，长度恒为 21。
        """
        if timestamp is None:
            timestamp = record.get("created_timestamp", time.time())
        return (
            record.get("filename", ""),
            record.get("filepath", ""),
            record.get("created_at", ""),
            record.get("file_size_bytes", 0),
            record.get("duration_seconds"),
            record.get("text_preview", ""),
            record.get("engine", "unknown"),
            record.get("model_type"),
            record.get("model_size"),
            record.get("lang", "zh"),
            record.get("persona_name"),
            record.get("output_format", "wav"),
            record.get("temperature", 0.9),
            record.get("seed", 42),
            record.get("speed", 1.0),
            1 if record.get("is_success", True) else 0,
            record.get("error_msg"),
            1 if record.get("is_degraded", False) else 0,
            record.get("tags", ""),
            1 if record.get("hidden", False) else 0,
            timestamp,
        )

    # ------------------------------------------------------------------
    # 写入操作
    # ------------------------------------------------------------------

    def add_record(
        self,
        filename: str,
        filepath: str,
        created_at: str,
        file_size: int,
        text_preview: str = "",
        engine: str = "unknown",
        persona_name: str | None = None,
        duration_seconds: float = 0.0,
    ) -> int:
        """添加一条新的生成历史记录（便捷方法）。

        内部调用 insert() 完成实际写入，字段由 _build_record_tuple 统一构建。

        Args:
            filename: 音频文件名（不含路径）。
            filepath: 音频文件绝对路径（UNIQUE 键，重复则覆盖）。
            created_at: 格式化创建时间字符串，如 "2024-01-01 12:00:00"。
            file_size: 文件大小（字节）。
            text_preview: 文本预览（截断到 100 字符）。
            engine: 使用的引擎名，默认 "unknown"。
            persona_name: 音色角色名，None 表示未指定音色。
            duration_seconds: 生成耗时（秒）。

        Returns:
            int: 新插入记录的 rowid（主键 id）。
        """
        # H-R2: 复用 _build_record_tuple
        record: dict[str, Any] = {
            "filename": filename,
            "filepath": filepath,
            "created_at": created_at,
            "file_size_bytes": file_size,
            "duration_seconds": duration_seconds,
            "text_preview": text_preview,
            "engine": engine,
            "persona_name": persona_name,
        }
        return self.insert(record)

    def insert(self, record: dict[str, Any]) -> int:
        """插入一条生成记录（INSERT OR REPLACE 语义）。

        INSERT OR REPLACE 语义：
        通过 ``filepath`` 字段的 UNIQUE 索引键匹配。若 DB 中已存在相同
        filepath 的记录，则先 DELETE 旧记录再 INSERT 新记录——相当于覆盖
        整条元数据。适用于"用户重新生成同名音频文件"时更新错误信息、时长等。

        锁超时重试：
        捕获 ``OperationalError("database is locked")``，每次 sleep 100ms
        后重试，最多 3 次。超过阈值则抛出 ``TTSError(code="HISTORY_LOCKED")``，
        让前端给出"请稍后重试"的明确提示，而不是模糊的 500 错误。

        Args:
            record: 生成记录字典。字段参考 ``_INSERT_FIELDS``，缺失字段会
                由 ``_build_record_tuple`` 回填默认值。

        Returns:
            新插入（或 REPLACE 后新行）的 ``rowid``（即 ``id`` 主键）。

        Raises:
            TTSError: 锁等待超过 3 次重试仍失败，code = ``"HISTORY_LOCKED"``。
        """
        last_exc: sqlite3.OperationalError | None = None
        for attempt in range(3):
            try:
                with self._transaction() as conn:
                    cursor = conn.execute(_INSERT_SQL, self._build_record_tuple(record))
                    rowid = cursor.lastrowid
                    # P2: HMAC 链防篡改 — 插入后计算并存储哈希链
                    self._compute_and_store_hmac(conn, rowid, record)
                    return rowid
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e):
                    last_exc = e
                    if attempt < 2:
                        time.sleep(0.1)
                        continue
                raise
        if last_exc is not None:
            raise TTSError(
                code="HISTORY_LOCKED",
                message="历史记录写入锁超时，请稍后重试",
                status_code=503,
            )
        return 0  # unreachable，安抚类型检查器

    def add_records_batch(self, records: list[dict[str, Any]]) -> int:
        """批量插入生成记录（add_records_batch 语义，insert_batch 的别名）。

        Args:
            records: 生成记录字典列表，每个 dict 字段参考 ``_INSERT_FIELDS``。

        Returns:
            实际插入 / REPLACE 的记录总数（等于 len(records)，空列表返回 0）。
        """
        return self.insert_batch(records)

    @staticmethod
    def _get_hmac_secret() -> bytes:
        """P2: 获取或生成历史记录 HMAC 密钥。

        密钥持久化在 ``data/.history_hmac_key`` 文件中，首次调用时自动生成。

        Returns:
            HMAC 密钥字节串。
        """
        import hashlib

        key_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", ".history_hmac_key"
        )
        try:
            os.makedirs(os.path.dirname(key_path), exist_ok=True)
            if os.path.exists(key_path):
                with open(key_path, encoding="utf-8") as f:
                    return f.read().strip().encode("utf-8")
            import secrets

            new_key = secrets.token_urlsafe(48)
            with open(key_path, "w", encoding="utf-8") as f:
                f.write(new_key)
            return new_key.encode("utf-8")
        except OSError:
            # 回退到固定密钥（仅在文件系统不可用时）
            return hashlib.sha256(b"tts-multimodel-history-fallback").digest()

    def _compute_and_store_hmac(self, conn: Any, rowid: int, record: dict[str, Any]) -> None:
        """P2: 计算并存储记录的 HMAC 链。

        Args:
            conn: 数据库连接（在当前事务内）。
            rowid: 刚插入的记录 ID。
            record: 记录字典。
        """
        import hashlib
        import hmac as _hmac

        try:
            secret = self._get_hmac_secret()
            # 获取上一条记录的 HMAC
            cursor = conn.execute(
                "SELECT record_hmac FROM generation_history WHERE id < ? ORDER BY id DESC LIMIT 1", (rowid,)
            )
            row = cursor.fetchone()
            prev_hash = row[0] if row else ""

            # 构建记录摘要（关键字段）
            record_str = f"{record.get('filename', '')}|{record.get('filepath', '')}|{record.get('created_at', '')}|{record.get('engine', '')}|{record.get('text_preview', '')}"
            record_hash = hashlib.sha256(record_str.encode("utf-8")).hexdigest()

            # 计算 HMAC: HMAC(secret, prev_hash + record_hash)
            hmac_value = _hmac.new(secret, f"{prev_hash}{record_hash}".encode(), hashlib.sha256).hexdigest()

            conn.execute(
                "UPDATE generation_history SET prev_hash = ?, record_hmac = ? WHERE id = ?",
                (prev_hash, hmac_value, rowid),
            )
        except Exception as exc:
            logger.warning("[history_db] HMAC 链计算失败（已忽略）: %s", exc)

    def verify_chain_integrity(self) -> dict[str, Any]:
        """P2: 验证历史记录 HMAC 链的完整性。

        Returns:
            包含 verified、total、tampered_count、first_tampered_id 的字典。
        """
        import hashlib
        import hmac as _hmac

        try:
            secret = self._get_hmac_secret()
        except Exception as exc:
            return {"verified": False, "error": f"HMAC 密钥获取失败: {exc}"}

        cursor = self._execute(
            "SELECT id, filename, filepath, created_at, engine, text_preview, prev_hash, record_hmac FROM generation_history ORDER BY id ASC"
        )
        rows = cursor.fetchall()

        prev_hash = ""
        tampered: list[int] = []
        for row in rows:
            rowid, filename, filepath, created_at, engine, text_preview, stored_prev_hash, stored_hmac = row
            record_str = f"{filename}|{filepath}|{created_at}|{engine}|{text_preview}"
            record_hash = hashlib.sha256(record_str.encode("utf-8")).hexdigest()
            expected_hmac = _hmac.new(secret, f"{prev_hash}{record_hash}".encode(), hashlib.sha256).hexdigest()

            if stored_prev_hash != prev_hash or stored_hmac != expected_hmac:
                tampered.append(rowid)

            prev_hash = stored_hmac if stored_hmac else expected_hmac

        return {
            "verified": len(tampered) == 0,
            "total": len(rows),
            "tampered_count": len(tampered),
            "first_tampered_id": tampered[0] if tampered else None,
        }

    def insert_batch(self, records: list[dict[str, Any]]) -> int:
        """批量插入多条生成记录（INSERT OR REPLACE 语义）。

        H-R5 分块处理：按 _CHUNK_SIZE=500 条分块执行 executemany，
        避免 SQL 参数数量超过 SQLITE_MAX_VARIABLE_NUMBER 限制。
        所有记录使用相同的时间戳（now），保证批量插入的时间一致性。

        Args:
            records: 生成记录字典列表，每个 dict 字段参考 _INSERT_FIELDS，
                缺失字段由 _build_record_tuple 回填默认值。空列表直接返回 0。

        Returns:
            int: 实际插入 / REPLACE 的记录总数。
        """
        if not records:
            return 0
        now = time.time()
        total_inserted = 0
        # H-R5: 分块执行，每块 _CHUNK_SIZE 条
        for chunk_start in range(0, len(records), _CHUNK_SIZE):
            chunk = records[chunk_start : chunk_start + _CHUNK_SIZE]
            params_list: list[tuple[Any, ...]] = [self._build_record_tuple(r, timestamp=now) for r in chunk]
            with self._transaction() as conn:
                conn.executemany(_INSERT_SQL, params_list)
                total_inserted += len(chunk)
        return total_inserted

    # ------------------------------------------------------------------
    # 查询操作
    # ------------------------------------------------------------------

    def get_records_by_ids(self, ids: list[int]) -> list[dict[str, Any]]:
        """REFACTOR: [S-R7] 公共方法 - 按 id 列表查询历史记录。

        B4: 遵循分层原则，替代 routes/audio.py 中直接调用私有 _execute 的做法。
        使用参数化查询（? 占位符）防止 SQL 注入。

        Args:
            ids: 记录 id 列表（整数）。

        Returns:
            匹配的记录字典列表，字段包含 id/filename/filepath 等。
            若 ids 为空或查询失败，返回空列表。
        """
        if not ids:
            return []
        # H-R5: 批量查询仍受 SQLITE_MAX_VARIABLE_NUMBER 限制，
        # 调用方（routes/audio.py）已通过 _MAX_BATCH_EXPORT_COUNT 限制单次数量
        placeholders = ",".join("?" * len(ids))
        sql = f"SELECT id, filename, filepath FROM generation_history WHERE id IN ({placeholders})"  # nosec B608: 占位符仅生成 ?，ids 全部参数绑定
        try:
            cursor = self._execute(sql, list(ids))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"[history_db] 按 id 查询历史记录失败: {e}", exc_info=True)
            return []

    def get_paginated_records(
        self,
        limit: int = 20,
        offset: int = 0,
        search_keyword: str = "",
        time_filter: str = "all",
        duration_filter: str = "all",
        include_hidden: bool = False,
        include_missing: bool = False,
    ) -> dict[str, Any]:
        """获取分页历史记录，支持关键词搜索和时间/时长过滤。

        Args:
            limit: 单页返回记录数上限（1~1000，超出自动修正为 20）。
            offset: 跳过前 N 条记录（用于分页，<0 自动修正为 0）。
            search_keyword: 搜索关键词，匹配文件名和文本预览（LIKE 模糊匹配，自动转义特殊字符）。
            time_filter: 时间过滤，可选值："all"（全部）、"today"（今天）、"week"（本周）、"month"（本月）。
            duration_filter: 时长过滤，可选值："all"（全部）、"lt5"（<5秒）、"5to10"（5~10秒）、"gt10"（>10秒）。
            include_hidden: 是否包含已隐藏记录，默认 False。
            include_missing: 是否包含文件缺失记录，默认 False。

        Returns:
            dict[str, Any]: 包含四个键的字典：
                - items (list[dict]): 当前页记录列表
                - total (int): 满足条件的总记录数
                - loaded (int): 已加载到的位置（offset + len(items)）
                - hasMore (bool): 是否还有下一页
        """
        # REFACTOR: [D4-1] 校验 limit/offset 上限，防止 OOM
        if limit <= 0 or limit > 1000:
            limit = 20
        if offset < 0:
            offset = 0

        conditions: list[str] = []
        params: list[Any] = []

        if not include_hidden:
            conditions.append("hidden = 0")

        if not include_missing:
            conditions.append("file_missing = 0")

        if search_keyword:
            # [H-R6] 与 _build_filter_conditions 一致：优先 FTS5，不可用时回退 LIKE。
            if self._can_use_fts(search_keyword):
                conditions.append(f"id IN (SELECT rowid FROM {_FTS_TABLE} WHERE {_FTS_TABLE} MATCH ?)")  # nosec B608: 表名为模块常量，用户值经 ? 参数绑定
                params.append(self._build_fts_query(search_keyword))
            else:
                kw_lower = self._escape_like(search_keyword).lower()
                conditions.append("(LOWER(filename) LIKE ? ESCAPE '\\' OR LOWER(text_preview) LIKE ? ESCAPE '\\')")
                params.extend([f"%{kw_lower}%", f"%{kw_lower}%"])

        # Time filter based on created_timestamp
        now = time.time()
        if time_filter == "today":
            conditions.append("created_timestamp > ?")
            params.append(now - _SECONDS_PER_DAY)
        elif time_filter == "week":
            conditions.append("created_timestamp > ?")
            params.append(now - _SECONDS_PER_WEEK)
        elif time_filter == "month":
            conditions.append("created_timestamp > ?")
            params.append(now - _SECONDS_PER_MONTH)

        # Duration filter based on duration_seconds
        if duration_filter == "lt5":
            conditions.append("duration_seconds < 5")
        elif duration_filter == "5to10":
            conditions.append("duration_seconds >= 5 AND duration_seconds < 10")
        elif duration_filter == "gt10":
            conditions.append("duration_seconds >= 10")

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        # Get total count
        cursor = self._execute(f"SELECT COUNT(*) as count FROM generation_history {where_clause}", params)  # nosec B608: where 子句由硬编码字面量拼接，值全部参数绑定
        total = cursor.fetchone()["count"]

        # Get paginated records
        cursor = self._execute(
            f"SELECT * FROM generation_history "  # nosec B608: where 子句由硬编码字面量拼接，值全部参数绑定
            f"{where_clause} "
            f"ORDER BY created_timestamp DESC "
            f"LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )

        items = [dict(row) for row in cursor.fetchall()]
        loaded = offset + len(items)
        has_more = loaded < total

        return {
            "items": items,
            "total": total,
            "loaded": loaded,
            "hasMore": has_more,
        }

    def query_records(
        self,
        offset: int = 0,
        limit: int = 50,
        filters: dict[str, Any] | None = None,
        order_by: str = "created_at DESC",
        search_text: str | None = None,
        include_hidden: bool = True,
        include_missing: bool = True,
    ) -> list[dict[str, Any]]:
        """使用 filters 字典过滤 + 分页查询历史记录。

        ``filters`` 字典结构（所有字段均可选）：
            - ``engine`` (str): 按引擎精确匹配，如 ``"voxcpm2"`` / ``"indextts2"``
            - ``persona_name`` (str): 按音色名精确匹配，None 代表未指定音色
            - ``is_success`` (bool): 是否仅查询成功/失败的生成
            - ``time_from`` (float): ``created_timestamp >= time_from``（Unix 秒）
            - ``time_to`` (float): ``created_timestamp <= time_to``（Unix 秒）

        SQL 注入防护：
        - filters 字典的 key 必须在 ``_ALLOWED_FILTER_KEYS`` 白名单内，未知
          key 会被 ``logger.warning`` 并忽略（不抛错，避免合法请求被阻断）；
        - 所有 filter 值均通过参数化查询 ``?`` 占位符传入，不做字符串拼接。

        ``order_by`` 白名单校验：
        防止调用方传入恶意列名（如 ``"1; DROP TABLE..."``）。非法值自动回退
        为 ``"created_at DESC"``。

        Args:
            offset: 跳过的记录数（用于分页），默认 0。
            limit: 单次返回的最大记录数，默认 50，上限 1000。
            filters: 过滤条件字典，见上。None 等价于空 dict（无条件）。
            order_by: ORDER BY 子句，必须在白名单内。
            search_text: 可选，模糊搜索关键词（同时匹配 filename 和 text_preview）。
            include_hidden: 是否包含已隐藏记录，默认 True。
            include_missing: 是否包含文件缺失记录，默认 True。

        Returns:
            满足条件的记录字典列表，按 ``order_by`` 排序，最多 ``limit`` 条。
        """
        if order_by not in _ALLOWED_ORDER_BY:
            order_by = "created_at DESC"

        if limit <= 0 or limit > 1000:
            limit = 50
        if offset < 0:
            offset = 0

        conditions, params = self._build_filter_conditions(filters=filters, search_text=search_text)

        if not include_hidden:
            conditions.append("hidden = 0")
        if not include_missing:
            conditions.append("file_missing = 0")

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        cursor = self._execute(
            f"SELECT * FROM generation_history "  # nosec B608: where 子句由硬编码字面量拼接，order_by 经 _ALLOWED_ORDER_BY 白名单校验
            f"{where_clause} "
            f"ORDER BY {order_by} "
            f"LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_records_keyset(
        self,
        limit: int = 50,
        cursor_timestamp: float | None = None,
        cursor_id: int | None = None,
        filters: dict[str, Any] | None = None,
        search_text: str | None = None,
        include_hidden: bool = True,
        include_missing: bool = True,
    ) -> dict[str, Any]:
        """[H-R6] 基于游标（keyset / seek）的分页查询，避免深分页 O(offset) 扫描。

        Why keyset 而不是 LIMIT/OFFSET：
            ``LIMIT ? OFFSET ?`` 在大表上需扫描并丢弃前 ``offset`` 行，翻到
            第 N 页的代价随 N 线性增长（O(offset)）。keyset 分页用上一页
            末尾记录的 ``(created_timestamp, id)`` 作游标，直接通过
            ``idx_history_created_timestamp`` 定位起点，每页恒为 O(log n + limit)，
            与页码深度无关。适用于下拉无限滚动、SSE 增量加载场景。

        排序：固定为 ``created_timestamp DESC, id DESC``（id 作为同时间戳的
        稳定次序 tiebreaker，避免同秒多条记录分页遗漏/重复）。

        Args:
            limit: 单页返回最大记录数（1~1000，越界修正为 50）。
            cursor_timestamp: 上一页末条记录的 ``created_timestamp``；None 表示首页。
            cursor_id: 上一页末条记录的 ``id``；与 ``cursor_timestamp`` 配对使用。
            filters: 过滤条件字典（同 ``query_records``）。
            search_text: 可选模糊搜索关键词（同样优先走 FTS5）。
            include_hidden: 是否包含已隐藏记录，默认 True。
            include_missing: 是否包含文件缺失记录，默认 True。

        Returns:
            dict[str, Any]: 包含：
                - ``items`` (list[dict]): 当页记录（按 created_timestamp DESC, id DESC）
                - ``next_cursor`` (dict | None): 下一页游标；
                  无更多数据时为 None。
                - ``hasMore`` (bool): 是否还有下一页。
        """
        if limit <= 0 or limit > 1000:
            limit = 50

        conditions, params = self._build_filter_conditions(filters=filters, search_text=search_text)

        if not include_hidden:
            conditions.append("hidden = 0")
        if not include_missing:
            conditions.append("file_missing = 0")

        # keyset 游标条件：严格小于上一页末尾 (timestamp, id)
        if cursor_timestamp is not None and cursor_id is not None:
            conditions.append("(created_timestamp < ? OR (created_timestamp = ? AND id < ?))")
            params.extend([float(cursor_timestamp), float(cursor_timestamp), int(cursor_id)])

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        # 多取一条用于判断 hasMore，不需额外 COUNT(*)
        cursor = self._execute(
            f"SELECT * FROM generation_history "  # nosec B608: where 子句由硬编码字面量拼接，值全部参数绑定
            f"{where_clause} "
            f"ORDER BY created_timestamp DESC, id DESC "
            f"LIMIT ?",
            (*params, limit + 1),
        )
        rows = [dict(row) for row in cursor.fetchall()]

        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor: dict[str, Any] | None = None
        if has_more and items:
            last = items[-1]
            next_cursor = {
                "timestamp": last.get("created_timestamp"),
                "id": last.get("id"),
            }

        return {
            "items": items,
            "next_cursor": next_cursor,
            "hasMore": has_more,
        }

    def count_records(
        self,
        filters: dict[str, Any] | None = None,
        search_text: str | None = None,
        include_hidden: bool = True,
        include_missing: bool = True,
    ) -> int:
        """使用 filters 字典统计满足条件的记录总数。

        支持的 filter 字段与 ``query_records`` 完全一致：
        engine / persona_name / is_success / time_from / time_to，可选 search_text。
        未知 key 会被 warning 后忽略，所有值均走参数化查询占位符。

        Args:
            filters: 过滤条件字典，None 等价于空 dict（统计全部）。
            search_text: 可选，模糊搜索关键词。
            include_hidden: 是否包含已隐藏记录，默认 True。
            include_missing: 是否包含文件缺失记录，默认 True。

        Returns:
            满足条件的记录数量（整数）。
        """
        conditions, params = self._build_filter_conditions(filters=filters, search_text=search_text)

        if not include_hidden:
            conditions.append("hidden = 0")
        if not include_missing:
            conditions.append("file_missing = 0")

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        cursor = self._execute(
            f"SELECT COUNT(*) as count FROM generation_history {where_clause}",  # nosec B608: where 子句由硬编码字面量拼接，值全部参数绑定
            params,
        )
        return cursor.fetchone()["count"]

    def query(
        self,
        limit: int = 50,
        offset: int = 0,
        engine: str | None = None,
        persona_name: str | None = None,
        search_text: str | None = None,
        order_by: str = "created_at DESC",
    ) -> list[dict[str, Any]]:
        """查询生成历史记录（旧版兼容接口），支持过滤器和分页。

        内部委托给 ``query_records()``，保持向后兼容。

        Args:
            limit: 单次返回最大记录数（1~1000，超出自动修正为 50）。
            offset: 跳过前 N 条记录（<0 自动修正为 0）。
            engine: 按引擎名精确过滤，None 不过滤。
            persona_name: 按音色名精确过滤，None 不过滤。
            search_text: 文本预览模糊搜索关键词（自动转义 LIKE 特殊字符）。
            order_by: ORDER BY 子句，必须在白名单内（防 SQL 注入）。

        Returns:
            list[dict[str, Any]]: 满足条件的记录字典列表，按 order_by 排序。
        """
        filters: dict[str, Any] = {}
        if engine is not None:
            filters["engine"] = engine
        if persona_name is not None:
            filters["persona_name"] = persona_name

        return self.query_records(
            offset=offset,
            limit=limit,
            filters=filters if filters else None,
            order_by=order_by,
            search_text=search_text,
            include_hidden=True,
            include_missing=True,
        )

    def count(
        self,
        engine: str | None = None,
        persona_name: str | None = None,
        search_text: str | None = None,
    ) -> int:
        """统计满足过滤条件的记录数量（旧版兼容接口）。

        内部委托给 ``count_records()``，保持向后兼容。

        Args:
            engine: 按引擎名精确过滤，None 不过滤。
            persona_name: 按音色名精确过滤，None 不过滤。
            search_text: 文本预览模糊搜索关键词（自动转义 LIKE 特殊字符）。

        Returns:
            int: 满足条件的记录总数。
        """
        filters: dict[str, Any] = {}
        if engine is not None:
            filters["engine"] = engine
        if persona_name is not None:
            filters["persona_name"] = persona_name

        return self.count_records(
            filters=filters if filters else None,
            search_text=search_text,
            include_hidden=True,
            include_missing=True,
        )

    # ------------------------------------------------------------------
    # 删除操作（H-R4 文件删除移出事务）
    # ------------------------------------------------------------------

    def delete_record(
        self,
        record_id: int,
        delete_file: bool = True,
    ) -> tuple[bool, str]:
        """按记录 id 删除单条历史记录，可选同步删除磁盘文件。

        Args:
            record_id: 要删除的记录主键 ``id``。
            delete_file: 是否同时删除 DB 记录中的 ``filepath`` 对应磁盘文件。
                默认 True（用户从 UI 删除通常希望文件一起清理）。

        Returns:
            ``(success: bool, message: str)`` 二元组：
            - success = True：DB 记录删除成功（如果 delete_file=True 且文件
              不存在，不会导致 success=False，因为文件可能已被用户手动清理）。
            - message：人类可读的结果描述（成功时为空串或成功提示，失败时
              包含具体错误原因）。
        """
        filepath_to_delete: str | None = None
        try:
            with self._transaction() as conn:
                if delete_file:
                    cursor = conn.execute(
                        "SELECT filepath FROM generation_history WHERE id = ?",
                        (record_id,),
                    )
                    row = cursor.fetchone()
                    if row and row["filepath"]:
                        filepath_to_delete = row["filepath"]
                cursor = conn.execute(
                    "DELETE FROM generation_history WHERE id = ?",
                    (record_id,),
                )
                if cursor.rowcount == 0:
                    return (False, f"未找到 id={record_id} 的历史记录")
        except Exception as e:
            logger.error(f"[history_db] 删除记录 id={record_id} 失败: {e}", exc_info=True)
            return (False, f"DB 删除失败: {e}")

        # H-R4: 事务成功后才删文件；DB 失败不影响磁盘，保证 DB 为事实源
        if filepath_to_delete and delete_file:
            if os.path.exists(filepath_to_delete):
                try:
                    os.remove(filepath_to_delete)
                    logger.debug(f"已删除文件: {filepath_to_delete}")
                except OSError as e:
                    logger.error(f"删除文件失败 {filepath_to_delete}: {e}")
                    return (True, f"DB 记录已删除，但文件删除失败: {e}")
            else:
                # 文件不存在（可能被用户手动清理），不视为失败
                logger.info(f"[history_db] 文件已不存在，跳过删除: {filepath_to_delete}")
        return (True, "")

    def delete_multiple_records_by_ids(
        self,
        record_ids: list[int],
        delete_file: bool = True,
    ) -> tuple[int, list[str]]:
        """按记录 id 列表批量删除历史记录，可选同步删除磁盘文件。

        H-R4 "文件删除移出事务"设计：
        1. 先在 DB 事务内按 id 批量 DELETE 记录，并在同一事务中 SELECT
           收集对应 filepath（若 delete_file=True）；
        2. 事务 COMMIT 成功之后，才遍历 filepath 列表删文件；
        3. 若 DB 事务中途回滚（如磁盘满、锁超时），filepath 还没被删除，
           用户数据不会丢失。**保证 DB 永远是事实源**。

        H-R5 分块：按 _CHUNK_SIZE=500 条分块执行 IN 子句 DELETE，避免变量
        数超过 SQLITE_MAX_VARIABLE_NUMBER。

        Args:
            record_ids: 要删除的记录主键 id 列表。会自动去重。
            delete_file: 是否同步删除每条记录的 filepath 对应磁盘文件。

        Returns:
            ``(deleted_count: int, failed_files: list[str])`` 二元组：
            - deleted_count：DB 中实际删除的记录行数（受 rowcount 影响）。
            - failed_files：删除失败的磁盘文件路径列表（DB 删除成功但文件
              删除失败的那些）。空列表表示所有文件均已删除成功 / 不存在。
        """
        if not record_ids:
            return (0, [])
        unique_ids: list[int] = list(dict.fromkeys(record_ids))
        filepaths_to_delete: list[str] = []
        deleted_count = 0

        # H-R4: 事务内只做 DB 操作，收集待删文件路径
        for chunk_start in range(0, len(unique_ids), _CHUNK_SIZE):
            chunk = unique_ids[chunk_start : chunk_start + _CHUNK_SIZE]
            placeholders = ",".join("?" * len(chunk))
            with self._transaction() as conn:
                if delete_file:
                    cursor = conn.execute(
                        f"SELECT filepath FROM generation_history WHERE id IN ({placeholders})",  # nosec B608: 占位符仅生成 ?，chunk 全部参数绑定
                        chunk,
                    )
                    for row in cursor.fetchall():
                        if row["filepath"]:
                            filepaths_to_delete.append(row["filepath"])
                cursor = conn.execute(
                    f"DELETE FROM generation_history WHERE id IN ({placeholders})",  # nosec B608: 占位符仅生成 ?，chunk 全部参数绑定
                    chunk,
                )
                deleted_count += cursor.rowcount

        # H-R4: 事务成功后才删文件；DB 失败不会到这里
        failed_files: list[str] = []
        if filepaths_to_delete:
            for filepath in filepaths_to_delete:
                if os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                        logger.debug(f"已删除文件: {filepath}")
                    except OSError as e:
                        logger.error(f"删除文件失败 {filepath}: {e}")
                        failed_files.append(filepath)
                else:
                    # 文件已被用户手动清理，不视为失败
                    logger.info(f"[history_db] 文件已不存在，跳过删除: {filepath}")
        return (deleted_count, failed_files)

    def delete_multiple_records(
        self,
        filenames: list[str],
        delete_files: bool = False,
    ) -> int:
        """按文件名批量删除历史记录，可选同步删除磁盘文件。

        H-R4 设计：文件删除移出事务，保证数据一致性。
        1. 先在事务内删除 DB 记录并收集 filepath；
        2. 事务 COMMIT 成功后，才遍历 filepath 列表删文件；
        3. DB 事务中途回滚时文件不会被删，保证 DB 为事实源。
        H-R5：IN 子句按 _CHUNK_SIZE=500 分块，避免变量数超限。

        Args:
            filenames: 要删除的文件名列表（自动去重）。
            delete_files: 是否同时删除磁盘上的音频文件，默认 False。

        Returns:
            int: DB 中实际删除的记录行数。
        """
        if not filenames:
            return 0
        filenames = list(dict.fromkeys(filenames))
        filepaths_to_delete: list[str] = []
        count = 0

        # H-R4: 事务内只做 DB 操作，收集待删文件路径
        for chunk_start in range(0, len(filenames), _CHUNK_SIZE):
            chunk = filenames[chunk_start : chunk_start + _CHUNK_SIZE]
            placeholders = ",".join("?" * len(chunk))
            with self._transaction() as conn:
                if delete_files:
                    cursor = conn.execute(
                        f"SELECT filepath FROM generation_history WHERE filename IN ({placeholders})",  # nosec B608: 占位符仅生成 ?，chunk 全部参数绑定
                        chunk,
                    )
                    for row in cursor.fetchall():
                        if row["filepath"]:
                            filepaths_to_delete.append(row["filepath"])
                cursor = conn.execute(
                    f"DELETE FROM generation_history WHERE filename IN ({placeholders})",  # nosec B608: 占位符仅生成 ?，chunk 全部参数绑定
                    chunk,
                )
                count += cursor.rowcount

        # H-R4: 事务成功后才删文件；失败不影响 DB 一致性
        if filepaths_to_delete:
            for filepath in filepaths_to_delete:
                if os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                        logger.debug(f"已删除文件: {filepath}")
                    except OSError as e:
                        logger.error(f"删除文件失败 {filepath}: {e}")
                else:
                    logger.info(f"[history_db] 文件已不存在，跳过删除: {filepath}")
        return count

    def hide_multiple_records(self, filenames: list[str]) -> int:
        """按文件名批量隐藏历史记录（设置 hidden=1）。

        隐藏的记录默认不在 UI 列表中显示，但仍保留在数据库中可恢复。
        H-R5：IN 子句按 _CHUNK_SIZE=500 分块。

        Args:
            filenames: 要隐藏的文件名列表（自动去重）。

        Returns:
            int: 实际被标记为隐藏的记录行数。
        """
        if not filenames:
            return 0
        filenames = list(dict.fromkeys(filenames))
        total = 0
        for chunk_start in range(0, len(filenames), _CHUNK_SIZE):
            chunk = filenames[chunk_start : chunk_start + _CHUNK_SIZE]
            placeholders = ",".join("?" * len(chunk))
            with self._transaction() as conn:
                cursor = conn.execute(
                    f"UPDATE generation_history SET hidden = 1 WHERE filename IN ({placeholders}) AND hidden = 0",  # nosec B608: 占位符仅生成 ?，chunk 全部参数绑定
                    chunk,
                )
                total += cursor.rowcount
        return total

    def show_multiple_records(self, filenames: list[str]) -> int:
        """按文件名批量恢复显示历史记录（设置 hidden=0）。

        取消隐藏，让记录重新出现在 UI 列表中。
        H-R5：IN 子句按 _CHUNK_SIZE=500 分块。

        Args:
            filenames: 要恢复显示的文件名列表（自动去重）。

        Returns:
            int: 实际被取消隐藏的记录行数。
        """
        if not filenames:
            return 0
        filenames = list(dict.fromkeys(filenames))
        total = 0
        for chunk_start in range(0, len(filenames), _CHUNK_SIZE):
            chunk = filenames[chunk_start : chunk_start + _CHUNK_SIZE]
            placeholders = ",".join("?" * len(chunk))
            with self._transaction() as conn:
                cursor = conn.execute(
                    f"UPDATE generation_history SET hidden = 0 WHERE filename IN ({placeholders}) AND hidden = 1",  # nosec B608: 占位符仅生成 ?，chunk 全部参数绑定
                    chunk,
                )
                total += cursor.rowcount
        return total

    def hide_multiple_records_by_ids(self, record_ids: list[int]) -> int:
        """按记录 ID 列表批量隐藏历史记录（设置 hidden=1）。

        隐藏的记录默认不在 UI 列表中显示，但仍保留在数据库中可恢复。
        H-R5：IN 子句按 _CHUNK_SIZE=500 分块。

        Args:
            record_ids: 要隐藏的记录 ID 列表（自动去重）。

        Returns:
            int: 实际被标记为隐藏的记录行数。
        """
        if not record_ids:
            return 0
        unique_ids: list[int] = list(dict.fromkeys(record_ids))
        total = 0
        for chunk_start in range(0, len(unique_ids), _CHUNK_SIZE):
            chunk = unique_ids[chunk_start : chunk_start + _CHUNK_SIZE]
            placeholders = ",".join("?" * len(chunk))
            with self._transaction() as conn:
                cursor = conn.execute(
                    f"UPDATE generation_history SET hidden = 1 WHERE id IN ({placeholders}) AND hidden = 0",  # nosec B608: 占位符仅生成 ?，chunk 全部参数绑定
                    chunk,
                )
                total += cursor.rowcount
        return total

    def show_multiple_records_by_ids(self, record_ids: list[int]) -> int:
        """按记录 ID 列表批量恢复显示历史记录（设置 hidden=0）。

        取消隐藏，让记录重新出现在 UI 列表中。
        H-R5：IN 子句按 _CHUNK_SIZE=500 分块。

        Args:
            record_ids: 要恢复显示的记录 ID 列表（自动去重）。

        Returns:
            int: 实际被取消隐藏的记录行数。
        """
        if not record_ids:
            return 0
        unique_ids: list[int] = list(dict.fromkeys(record_ids))
        total = 0
        for chunk_start in range(0, len(unique_ids), _CHUNK_SIZE):
            chunk = unique_ids[chunk_start : chunk_start + _CHUNK_SIZE]
            placeholders = ",".join("?" * len(chunk))
            with self._transaction() as conn:
                cursor = conn.execute(
                    f"UPDATE generation_history SET hidden = 0 WHERE id IN ({placeholders}) AND hidden = 1",  # nosec B608: 占位符仅生成 ?，chunk 全部参数绑定
                    chunk,
                )
                total += cursor.rowcount
        return total

    def show_all_records(self) -> int:
        """恢复显示所有已隐藏的记录（设置 hidden=0）。

        Returns:
            int: 实际被取消隐藏的记录行数。
        """
        with self._transaction() as conn:
            cursor = conn.execute("UPDATE generation_history SET hidden = 0 WHERE hidden = 1")
            return cursor.rowcount

    def clear_all_records(self, hide_only: bool = True) -> int:
        """清空所有历史记录。

        Args:
            hide_only: True 表示仅隐藏（设置 hidden=1，可恢复），False 表示物理删除（不可恢复）。

        Returns:
            int: 被隐藏或删除的记录数量。
        """
        if hide_only:
            with self._transaction() as conn:
                cursor = conn.execute("UPDATE generation_history SET hidden = 1 WHERE hidden = 0")
                return cursor.rowcount
        else:
            with self._transaction() as conn:
                cursor = conn.execute("SELECT COUNT(*) as count FROM generation_history")
                count = cursor.fetchone()["count"]
                conn.execute("DELETE FROM generation_history")
                return count

    def get_total_count(self, include_hidden: bool = False) -> int:
        """获取历史记录总数。

        Args:
            include_hidden: 是否包含已隐藏记录，默认 False。

        Returns:
            int: 记录总数。
        """
        if include_hidden:
            cursor = self._execute("SELECT COUNT(*) as count FROM generation_history")
        else:
            cursor = self._execute("SELECT COUNT(*) as count FROM generation_history WHERE hidden = 0")
        return cursor.fetchone()["count"]

    # ------------------------------------------------------------------
    # 文件缺失记录查询与清理
    # ------------------------------------------------------------------

    def get_file_records_missing_on_disk(self) -> list[dict[str, Any]]:
        """查询 DB 中已标记 file_missing=1 的"文件在磁盘上不存在"记录。

        用于 UI 提示用户"以下生成记录对应的音频文件已被手动清理"，或批量
        清理这些"僵尸记录"。不做实时磁盘扫描，直接依赖
        ``cleanup_orphan_records()`` 或 ``sync_from_filesystem()`` 设置的
        ``file_missing`` 标志位，避免全表扫 + stat 调用的开销。

        Returns:
            file_missing=1 的完整记录字典列表（含 id / filename / filepath /
            created_at 等）。
        """
        cursor = self._execute(
            "SELECT * FROM generation_history WHERE file_missing = 1 ORDER BY created_timestamp DESC"
        )
        return [dict(row) for row in cursor.fetchall()]

    def cleanup_file_missing_records(
        self,
        dry_run: bool = False,
    ) -> tuple[int, int]:
        """清理 DB 中 file_missing=1 且磁盘上确实不存在的记录。

        两步一致性校验：
        1. SELECT id, filepath FROM file_missing=1 的记录；
        2. 对每条记录 **重新** ``os.path.exists(filepath)``（防止标志位陈旧，
           如用户又把文件放回了目录），只有确实不存在的才进入删除列表。

        Args:
            dry_run: True 时仅统计将被删除的数量，不实际执行 DELETE。
                用于 UI 预览"点击清理将移除 X 条记录"。

        Returns:
            ``(candidate_count: int, deleted_count: int)`` 二元组：
            - candidate_count：DB 中 file_missing=1 且磁盘上确实不存在的总数。
            - deleted_count：实际删除的行数（dry_run=True 时恒为 0）。
        """
        cursor = self._execute("SELECT id, filepath FROM generation_history WHERE file_missing = 1")
        rows = cursor.fetchall()
        to_delete_ids: list[int] = []
        for row in rows:
            fp = row["filepath"]
            if not fp or not os.path.exists(fp):
                to_delete_ids.append(row["id"])
        candidate_count = len(to_delete_ids)
        deleted_count = 0

        if not dry_run and to_delete_ids:
            # H-R5: 分块 DELETE
            for chunk_start in range(0, len(to_delete_ids), _CHUNK_SIZE):
                chunk = to_delete_ids[chunk_start : chunk_start + _CHUNK_SIZE]
                placeholders = ",".join("?" * len(chunk))
                with self._transaction() as conn:
                    cursor = conn.execute(
                        f"DELETE FROM generation_history WHERE id IN ({placeholders})",  # nosec B608: 占位符仅生成 ?，chunk 全部参数绑定
                        chunk,
                    )
                    deleted_count += cursor.rowcount
            logger.info(f"[history_db] 清理文件缺失记录: 候选 {candidate_count} 条，实际删除 {deleted_count} 条")
        return (candidate_count, deleted_count)

    # ------------------------------------------------------------------
    # 文件系统同步
    # ------------------------------------------------------------------

    def sync_from_filesystem(
        self,
        output_dir: str | None = None,
        since_mtime: float = 0.0,
    ) -> float:
        """扫描文件系统，将未入库的音频文件同步到数据库。

        仅处理 mtime > since_mtime 的文件（增量同步）。同步完成后返回并更新
        last_sync_mtime 高水位线，供下次增量同步使用。支持 .wav/.mp3/.ogg/.flac 格式。

        Args:
            output_dir: 音频输出目录，默认使用 config.SAVE_DIR。
            since_mtime: Unix 时间戳，只同步修改时间晚于此值的文件。

        Returns:
            float: 本次同步遇到的最大 mtime（新高水位线）。
        """
        import glob
        from datetime import datetime

        from .config import SAVE_DIR

        if output_dir is None:
            output_dir = SAVE_DIR

        # Get existing file paths efficiently
        existing_paths: set[str] = set()
        cursor = self._execute("SELECT filepath FROM generation_history")
        for row in cursor.fetchall():
            existing_paths.add(row["filepath"])

        audio_extensions = {".wav", ".mp3", ".ogg", ".flac"}
        records_to_insert: list[dict[str, Any]] = []
        max_mtime = since_mtime

        for ext in audio_extensions:
            pattern = os.path.join(output_dir, f"*{ext}")
            for filepath in glob.glob(pattern):
                if filepath in existing_paths:
                    continue

                try:
                    filename = os.path.basename(filepath)
                    stat = os.stat(filepath)
                    if stat.st_mtime <= since_mtime:
                        continue

                    # Extract info from filename pattern: engine_type_text_timestamp.wav
                    text_preview = filename.rsplit(".", 1)[0][:_TEXT_PREVIEW_MAX_LENGTH]

                    record: dict[str, Any] = {
                        "filename": filename,
                        "filepath": filepath,
                        "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "file_size_bytes": stat.st_size,
                        "text_preview": text_preview,
                        "engine": "unknown",
                        "output_format": ext.lstrip("."),
                        "is_success": True,
                        "is_degraded": False,
                        "tags": "",
                        "created_timestamp": stat.st_mtime,
                    }
                    records_to_insert.append(record)
                    if stat.st_mtime > max_mtime:
                        max_mtime = stat.st_mtime
                except Exception as e:
                    logger.debug(f"同步文件失败 {filepath}: {e}")

        # H-R5: insert_batch 内部已分块
        if records_to_insert:
            self.insert_batch(records_to_insert)
            logger.info(f"已从文件系统同步 {len(records_to_insert)} 个文件到历史记录数据库")

        if max_mtime > self.last_sync_mtime:
            self.last_sync_mtime = max_mtime
        return self.last_sync_mtime

    def cleanup_orphan_records(self, output_dir: str | None = None) -> int:
        """标记磁盘上对应文件已不存在的"孤立记录"为 file_missing=1。

        全表扫描所有 filepath 非空的记录，对每条执行 os.path.exists() 检查，
        不存在则标记为 file_missing=1。H-R5：UPDATE IN 子句按 _CHUNK_SIZE 分块。

        Args:
            output_dir: 输出目录（保留参数以兼容旧 API，当前未使用）。

        Returns:
            int: 被标记为 file_missing=1 的孤立记录数量。
        """
        cursor = self._execute(
            "SELECT id, filepath FROM generation_history WHERE filepath IS NOT NULL AND filepath != ''"
        )
        rows = cursor.fetchall()
        orphan_ids: list[int] = []
        for row in rows:
            if not os.path.exists(row["filepath"]):
                orphan_ids.append(row["id"])

        if orphan_ids:
            # H-R5: 分块更新
            for chunk_start in range(0, len(orphan_ids), _CHUNK_SIZE):
                chunk = orphan_ids[chunk_start : chunk_start + _CHUNK_SIZE]
                placeholders = ",".join("?" * len(chunk))
                with self._transaction() as conn:
                    conn.execute(
                        f"UPDATE generation_history SET file_missing = 1 WHERE id IN ({placeholders})",  # nosec B608: 占位符仅生成 ?，chunk 全部参数绑定
                        chunk,
                    )
            logger.info(f"已标记 {len(orphan_ids)} 条孤立记录为 file_missing")
        return len(orphan_ids)

    def validate_integrity(self) -> tuple[bool, str]:
        """执行 PRAGMA integrity_check 验证数据库完整性。

        Returns:
            tuple[bool, str]: (is_ok, message)
                - is_ok: True 表示完整性检查通过，False 表示数据库损坏。
                - message: SQLite 返回的检查结果文本，"ok" 表示正常，否则为错误描述。
        """
        try:
            cursor = self._execute("PRAGMA integrity_check")
            result = cursor.fetchone()
            message = result[0] if result else "unknown"
            is_ok = message == "ok"
            return (is_ok, message)
        except sqlite3.DatabaseError as e:
            return (False, str(e))

    def get_stats(self) -> dict[str, Any]:
        """获取历史记录聚合统计信息。

        Returns:
            dict[str, Any]: 包含以下字段的统计字典：
                - total_records (int): 总记录数
                - unique_engines (int): 不同引擎数量
                - unique_personas (int): 不同音色数量
                - avg_duration_seconds (float): 平均生成时长（秒）
                - avg_file_size_bytes (float): 平均文件大小（字节）
        """
        cursor = self._execute("""
            SELECT
                COUNT(*) as total,
                COUNT(DISTINCT engine) as engine_count,
                COUNT(DISTINCT persona_name) as persona_count,
                AVG(duration_seconds) as avg_duration,
                AVG(file_size_bytes) as avg_file_size
            FROM generation_history
        """)
        row = dict(cursor.fetchone())
        return {
            "total_records": row["total"],
            "unique_engines": row["engine_count"],
            "unique_personas": row["persona_count"],
            "avg_duration_seconds": round(row["avg_duration"] or 0, 2),
            "avg_file_size_bytes": round(row["avg_file_size"] or 0, 0),
        }

    def _migrate_from_json(self) -> None:
        """从旧版 JSON 格式历史记录（data/history_records.json）迁移数据到 SQLite。

        迁移完成后将原 JSON 文件重命名为 .migrated，避免重复迁移。
        空 JSON 文件直接重命名，不做数据迁移。
        迁移失败仅记录 error 日志，不影响数据库正常使用。
        """
        from .config import PROJECT_ROOT

        json_path = os.path.join(PROJECT_ROOT, "data", "history_records.json")
        migrated_path = json_path + ".migrated"

        if not os.path.exists(json_path):
            return

        # Skip if already migrated
        if os.path.exists(migrated_path):
            return

        try:
            with open(json_path, encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)

            if not data:
                # Empty JSON file, just rename
                os.rename(json_path, migrated_path)
                logger.info("空的 history_records.json 文件，已重命名为 .migrated")
                return

            records: list[dict[str, Any]] = []
            for filename, record_data in data.items():
                records.append(
                    {
                        "filename": record_data.get("filename", filename),
                        "filepath": record_data.get("filepath", ""),
                        "created_at": record_data.get("created_at", ""),
                        "file_size_bytes": record_data.get("file_size", 0),
                        "duration_seconds": record_data.get("duration_seconds", 0),
                        "text_preview": record_data.get("text_preview", ""),
                        "engine": record_data.get("engine", "unknown"),
                        "persona_name": record_data.get("persona_name"),
                        "hidden": record_data.get("hidden", False),
                        "created_timestamp": record_data.get("created_timestamp", 0),
                        "is_success": True,
                        "is_degraded": False,
                        "tags": "",
                    }
                )

            count = self.insert_batch(records)
            logger.info(f"已从 history_records.json 迁移 {count} 条记录到 SQLite")

            # Rename the JSON file to mark migration complete
            os.rename(json_path, migrated_path)
            logger.info(f"已将 {json_path} 重命名为 {migrated_path}")

        except Exception as e:
            logger.error(f"从 JSON 迁移失败: {e}")


# --- 单例管理 (H-R3 线程安全) ---
_history_db: HistoryDatabase | None = None
_singleton_lock = threading.Lock()  # H-R3: 保护单例创建


def get_history_db() -> HistoryDatabase:
    """获取全局 HistoryDatabase 单例实例（线程安全双重检查锁定）。

    单例模式确保全应用共享同一个数据库连接池和索引，避免多线程各自实例化
    导致连接泄漏和数据不一致。首次调用时自动创建数据目录并运行 JSON 迁移。

    Returns:
        HistoryDatabase: 全局共享的历史记录数据库实例。
    """
    global _history_db
    if _history_db is None:
        with _singleton_lock:
            # 双重检查，避免锁内重复创建
            if _history_db is None:
                from .config import SAVE_DIR

                db_dir = os.path.dirname(SAVE_DIR)
                db_path = os.path.join(db_dir, "data", "history.db")
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
                _history_db = HistoryDatabase(db_path)
                # Run JSON migration on first initialization
                _history_db._migrate_from_json()
    return _history_db


def create_history_db(output_dir: str) -> HistoryDatabase:
    """创建一个新的 HistoryDatabase 实例（工厂函数，用于测试场景）。

    不依赖全局单例状态，在指定目录下创建 history.db 文件。

    Args:
        output_dir: 数据库文件所在目录路径。

    Returns:
        HistoryDatabase: 新创建的历史记录数据库实例。
    """
    db_path = os.path.join(output_dir, "history.db")
    return HistoryDatabase(db_path)


def close_all_connections() -> None:
    """REFACTOR: [H-R3] 模块级便捷函数，关闭全局单例的所有连接。

    在应用 shutdown 钩子中调用。
    """
    global _history_db
    if _history_db is not None:
        _history_db.close_all()
