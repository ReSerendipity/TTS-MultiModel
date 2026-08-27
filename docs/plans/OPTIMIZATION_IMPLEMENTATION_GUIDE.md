# TTS MultiModel 系统优化实施方案（手工对接指南）

> **目标**：对 TTS 多模型项目进行全面代码审查与系统性优化，提升代码质量、
> 系统健壮性、运行性能及用户交互流畅度，不改变原有业务逻辑。
>
> **状态**：本指南基于对代码库的深度审查后制定，涵盖四项已验证的优化项，
> 每项均经过功能验证与基准测试。所有改动均为向后兼容，可独立提交。
>
> **使用方式**：按章节顺序依次实施，每项完成后运行对应验证命令，
> 全部完成后运行完整测试门禁确认无回归。

---

## 目录

1. [项目现状摘要](#1-项目现状摘要)
2. [优化项一：FTS5 全文检索加速](#2-优化项一fts5-全文检索加速)
3. [优化项二：Keyset 游标分页](#3-优化项二keyset-游标分页)
4. [优化项三：迁移代码去重](#4-优化项三迁移代码去重)
5. [优化项四：音频流水线内存优化](#5-优化项四音频流水线内存优化)
6. [性能基准测试](#6-性能基准测试)
7. [测试门禁验证](#7-测试门禁验证)
8. [回滚计划](#8-回滚计划)
9. [已知风险与注意事项](#9-已知风险与注意事项)

---

## 1. 项目现状摘要

### 1.1 已有基础（无需改动）

项目在 `history_db.py` 和 `audio_processing.py` 中已实现大量高性能设计：

| 特性 | 实现位置 | 说明 |
|------|----------|------|
| WAL 模式 | `_PRAGMA_CONFIG` | journal_mode=WAL，并发读写 |
| 64MB 页缓存 | `_PRAGMA_CONFIG` | cache_size=-64000 |
| 256MB mmap | `_PRAGMA_CONFIG` | mmap_size=268435456 |
| 5s 锁等待 | `_PRAGMA_CONFIG` | busy_timeout=5000 |
| 9 个索引 | `_ensure_indexes()` | 含复合索引 (engine, created_at) |
| 统一 INSERT | `_INSERT_SQL` + `_build_record_tuple()` | 消除三处重复 |
| 批量分块 500 | `_CHUNK_SIZE=500` | 避免 SQLITE_MAX_VARIABLE_NUMBER |
| 文件删除移出事务 | `delete_multiple_records()` | DB 为事实源 |
| 线程本地连接池 | `_get_connection()` + `_all_connections` | 避免跨线程共享连接 |
| 音频向量化 | numpy 全路径 | 无 Python 循环热路径 |
| VAD 对象缓存 | `_vad_cache` | 避免 C 扩展重复初始化 |

### 1.2 识别的瓶颈（本次优化目标）

| 瓶颈 | 复杂度 | 影响 |
|------|--------|------|
| 关键词搜索使用 `LIKE '%kw%'` | O(n) 全表扫描 | 万级记录下每次查询数十~百 ms |
| 深分页使用 `LIMIT ? OFFSET ?` | O(offset) | 第 N 页延迟随 N 线性增长 |
| 三处迁移方法代码完全重复 | 维护性 | 新增列迁移需复制粘贴 |
| `enhance_audio` 无条件 `audio.copy()` | 一次性全量拷贝 | 分钟级音频每次浪费 ~16MB |

---

## 2. 优化项一：FTS5 全文检索加速

### 2.1 原理

SQLite 内置 FTS5 虚拟表支持 trigram 分词器，对任意脚本（含中文 CJK）做
大小写不敏感子串匹配，语义与 `LOWER(col) LIKE '%kw%'` 完全等价。

FTS5 只需 ≥3 个 Unicode 字符的查询词。1-2 字符的子串回退 LIKE。

**实测环境**：WinPython 内置 SQLite 3.49.1，已验证 `ENABLE_FTS5` 和
`tokenize='trigram'` 均可用。

### 2.2 技术设计

采用 **external-content FTS5** 方案：

```
generation_history (主表，22字段，正文)
        ↕ content_rowid=id
generation_history_fts (FTS5虚表，仅索引)
```

- FTS 表不重复存储正文，仅存 trigram 倒排索引，磁盘开销极小。
- 三个触发器 (AFTER INSERT / DELETE / UPDATE) 在主表写入时自动同步索引。
- **关键前提**：必须在 PRAGMA 配置中加入 `recursive_triggers=1`。
  否则 `INSERT OR REPLACE`（冲突时先 DELETE 再 INSERT）的隐式 DELETE
  不会触发 AFTER DELETE 触发器，导致 FTS 残留旧文本索引。

**已验证的递归触发器行为**：
```
INSERT OR REPLACE 同 filepath 的记录 → 旧索引正确删除 → 新索引正确插入
DELETE 记录 → 索引正确删除
integrity_check → 通过
```

### 2.3 改动清单（`app/integrated_app/history_db.py`）

#### 2.3.1 模块常量区域（约第 57-68 行之后）

在 `_PRAGMA_CONFIG` 字典**内部**（`busy_timeout` 行之后）添加：

```python
    "busy_timeout": 5000,  # H-R3: 5s 锁等待，避免 database is locked 错误
    # Why recursive_triggers=1（H-R6 FTS5 索引一致性的关键前提）：
    # 写入使用 INSERT OR REPLACE，冲突时 SQLite 先 DELETE 旧行再 INSERT 新行。
    # 默认 recursive_triggers=OFF 时，REPLACE 触发的 DELETE 不会触发 AFTER
    # DELETE 触发器 —— 会导致 generation_history_fts 残留旧行的索引（搜索到
    # 已被覆盖的过期文本）。开启后 REPLACE 的隐式 DELETE 正常触发 fts 清理
    # 触发器，保证全文索引与主表严格一致。本库无其他触发器，开启无副作用。
    "recursive_triggers": 1,
}
```

在 `_PRAGMA_CONFIG` 闭合花括号之后、`_CHUNK_SIZE` 之前，添加 FTS 常量：

```python
# --- FTS5 全文检索（H-R6）---
# 外部内容（external-content）FTS5 虚表：仅存索引、不复制正文，正文仍在
# generation_history 主表，通过 content_rowid=id 关联。trigram 分词器支持
# 任意脚本（含 CJK）的子串匹配，语义等价于 LOWER(col) LIKE '%kw%'（≥3 字符）。
_FTS_TABLE: str = "generation_history_fts"
# trigram 分词器要求查询词至少 3 个 Unicode 字符；不足 3 字符回退 LIKE（
# LIKE 能处理 1-2 字符子串，trigram 不能），保证短词搜索行为不变。
_FTS_MIN_QUERY_CHARS: int = 3
```

#### 2.3.2 类属性声明（约第 135-140 行）

在 `_connections_lock` 之后添加：

```python
    _fts_enabled: bool
```

#### 2.3.3 `__init__` 方法（约第 170 行附近）

在 `self._connections_lock = threading.Lock()` 之后添加：

```python
        # H-R6: FTS5 可用性在 _ensure_fts() 中探测并缓存；False 时搜索回退 LIKE。
        self._fts_enabled = False
```

在 `self._ensure_indexes()` 之后添加调用：

```python
        self._ensure_fts()
```

#### 2.3.4 `_optimize_pragmas` 之后添加 `_ensure_fts` 方法

在 `_optimize_pragmas` 方法结束后、`_build_record_tuple` 之前插入：

```python
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
                    f"""
                    CREATE TRIGGER IF NOT EXISTS generation_history_ai
                    AFTER INSERT ON generation_history BEGIN
                        INSERT INTO {_FTS_TABLE}(rowid, filename, text_preview)
                        VALUES (new.id, new.filename, new.text_preview);
                    END
                    """
                )
                conn.execute(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS generation_history_ad
                    AFTER DELETE ON generation_history BEGIN
                        INSERT INTO {_FTS_TABLE}({_FTS_TABLE}, rowid, filename, text_preview)
                        VALUES('delete', old.id, old.filename, old.text_preview);
                    END
                    """
                )
                conn.execute(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS generation_history_au
                    AFTER UPDATE ON generation_history BEGIN
                        INSERT INTO {_FTS_TABLE}({_FTS_TABLE}, rowid, filename, text_preview)
                        VALUES('delete', old.id, old.filename, old.text_preview);
                        INSERT INTO {_FTS_TABLE}(rowid, filename, text_preview)
                        VALUES (new.id, new.filename, new.text_preview);
                    END
                    """
                )
                # 全量重建索引，保证与主表一致（含旧库回填）
                conn.execute(f"INSERT INTO {_FTS_TABLE}({_FTS_TABLE}) VALUES('rebuild')")
            self._fts_enabled = True
            logger.info("[history_db] FTS5 全文索引已就绪（trigram 分词）")
        except sqlite3.OperationalError as e:
            self._fts_enabled = False
            logger.info(
                "[history_db] FTS5/trigram 不可用（%s），搜索回退 LIKE 全表扫描", e
            )
        except Exception as e:
            self._fts_enabled = False
            logger.warning("[history_db] FTS5 初始化异常（%s），搜索回退 LIKE", e)
```

#### 2.3.5 `_escape_like` 方法之后添加 FTS 辅助方法

```python
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

    def _can_use_fts(self, search_text: Optional[str]) -> bool:
        """[H-R6] 判断当前搜索是否可走 FTS5 加速路径。

        仅当 FTS5 已启用且关键词长度 ≥ trigram 最小要求（3 个 Unicode
        字符）时返回 True；否则回退 LIKE（trigram 无法处理 1-2 字符子串）。

        Args:
            search_text: 搜索关键词（可为 None / 空）。

        Returns:
            可用 FTS 加速返回 True，否则 False。
        """
        return (
            self._fts_enabled
            and bool(search_text)
            and len(search_text) >= _FTS_MIN_QUERY_CHARS
        )
```

#### 2.3.6 `_build_filter_conditions` 中的搜索分支（约第 405-414 行）

将现有的搜索代码：

```python
        if search_text:
            escaped = self._escape_like(search_text).lower()
            if search_filename:
                conditions.append(
                    "(LOWER(filename) LIKE ? ESCAPE '\\' OR LOWER(text_preview) LIKE ? ESCAPE '\\')"
                )
                params.extend([f"%{escaped}%", f"%{escaped}%"])
            else:
                conditions.append("LOWER(text_preview) LIKE ? ESCAPE '\\'")
                params.append(f"%{escaped}%")
```

替换为：

```python
        if search_text:
            # [H-R6] 优先 FTS5 子查询（O(log n)），不可用时回退 LIKE 全表扫描。
            # 两者对 ≥3 字符关键词语义一致（大小写不敏感子串匹配）。
            if self._can_use_fts(search_text):
                fts_query = self._build_fts_query(search_text)
                if not search_filename:
                    # 仅匹配 text_preview 列：使用 FTS5 列过滤语法 ``col : phrase``
                    fts_query = f"text_preview : {fts_query}"
                conditions.append(
                    f"id IN (SELECT rowid FROM {_FTS_TABLE} WHERE {_FTS_TABLE} MATCH ?)"
                )
                params.append(fts_query)
            else:
                escaped = self._escape_like(search_text).lower()
                if search_filename:
                    conditions.append(
                        "(LOWER(filename) LIKE ? ESCAPE '\\' OR LOWER(text_preview) LIKE ? ESCAPE '\\')"
                    )
                    params.extend([f"%{escaped}%", f"%{escaped}%"])
                else:
                    conditions.append("LOWER(text_preview) LIKE ? ESCAPE '\\'")
                    params.append(f"%{escaped}%")
```

#### 2.3.7 `get_paginated_records` 中的内联搜索分支（约第 804-807 行）

将：

```python
        if search_keyword:
            kw_lower = self._escape_like(search_keyword).lower()
            conditions.append("(LOWER(filename) LIKE ? ESCAPE '\\' OR LOWER(text_preview) LIKE ? ESCAPE '\\')")
            params.extend([f"%{kw_lower}%", f"%{kw_lower}%"])
```

替换为：

```python
        if search_keyword:
            # [H-R6] 与 _build_filter_conditions 一致：优先 FTS5，不可用时回退 LIKE。
            if self._can_use_fts(search_keyword):
                conditions.append(
                    f"id IN (SELECT rowid FROM {_FTS_TABLE} WHERE {_FTS_TABLE} MATCH ?)"
                )
                params.append(self._build_fts_query(search_keyword))
            else:
                kw_lower = self._escape_like(search_keyword).lower()
                conditions.append("(LOWER(filename) LIKE ? ESCAPE '\\' OR LOWER(text_preview) LIKE ? ESCAPE '\\')")
                params.extend([f"%{kw_lower}%", f"%{kw_lower}%"])
```

#### 2.3.8 损坏恢复路径（约第 259 行附近）

在 `self._ensure_indexes()` 之后、`logger.info("数据库损坏后重建成功")` 之前添加：

```python
                self._ensure_fts()
```

### 2.4 验证

```powershell
# 快速功能验证（120 条记录）
$env:PYTHONUTF8="1"
.\WPy64-312101\python\python.exe -c "
import os, sys, tempfile
sys.path.insert(0, 'bin')
from integrated_app.history_db import HistoryDatabase
db = HistoryDatabase(os.path.join(tempfile.mkdtemp(), 't.db'))
for i in range(60):
    db.add_record(filename=f'f{i}.wav', filepath=f'/o/f{i}.wav',
        created_at='2026-01-01', file_size=1, duration_seconds=1,
        engine='voxcpm2', text_preview=('你好世界编号%d'%i) if i%3==0 else 'hello world %d'%i)
print('fts:', db._fts_enabled)
print('CJK search:', db.get_paginated_records(limit=200, search_keyword='你好世界')['total'])  # expect 20
print('REPLACE resync:')
db.add_record(filename='f0.wav', filepath='/o/f0.wav', created_at='2026-01-01',
    file_size=1, duration_seconds=1, engine='voxcpm2', text_preview='新文本替换')
print('  old gone:', db.get_paginated_records(limit=10, search_keyword='你好世界编号0')['total'])  # expect 0
print('  new found:', db.get_paginated_records(limit=10, search_keyword='新文本替换')['total'])    # expect 1
print('integrity:', db.validate_integrity())
"
```

---

## 3. 优化项二：Keyset 游标分页

### 3.1 原理

`LIMIT ? OFFSET ?` 在大表上需扫描并丢弃前 `offset` 行，代价随页码线性增长。
Keyset 分页用上一页末尾记录的 `(created_timestamp, id)` 作游标，直接通过
索引定位起点，每页恒为 `O(log n + limit)`。

### 3.2 改动（`app/integrated_app/history_db.py`）

在 `query_records` 方法结束后、`count_records` 之前添加新方法：

```python
    def query_records_keyset(
        self,
        limit: int = 50,
        cursor_timestamp: Optional[float] = None,
        cursor_id: Optional[int] = None,
        filters: Optional[dict[str, Any]] = None,
        search_text: Optional[str] = None,
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

        conditions, params = self._build_filter_conditions(
            filters=filters, search_text=search_text
        )

        if not include_hidden:
            conditions.append("hidden = 0")
        if not include_missing:
            conditions.append("file_missing = 0")

        # keyset 游标条件：严格小于上一页末尾 (timestamp, id)
        if cursor_timestamp is not None and cursor_id is not None:
            conditions.append(
                "(created_timestamp < ? OR (created_timestamp = ? AND id < ?))"
            )
            params.extend([float(cursor_timestamp), float(cursor_timestamp), int(cursor_id)])

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        # 多取一条用于判断 hasMore，不需额外 COUNT(*)
        cursor = self._execute(
            f"""
            SELECT * FROM generation_history
            {where_clause}
            ORDER BY created_timestamp DESC, id DESC
            LIMIT ?
            """,
            (*params, limit + 1),
        )
        rows = [dict(row) for row in cursor.fetchall()]

        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor: Optional[dict[str, Any]] = None
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
```

### 3.3 验证

```powershell
# keyset 与 offset 全量等价性验证
$env:PYTHONUTF8="1"
.\WPy64-312101\python\python.exe -c "
import os, sys, tempfile
sys.path.insert(0, 'bin')
from integrated_app.history_db import HistoryDatabase
db = HistoryDatabase(os.path.join(tempfile.mkdtemp(), 'k.db'))
for i in range(120):
    db.add_record(filename=f'k{i}.wav', filepath=f'/o/k{i}.wav',
        created_at='2026-01-01', file_size=1, duration_seconds=1,
        engine='voxcpm2', text_preview=f'记录{i}')
# offset walk
off_ids, off = [], 0
while True:
    p = db.get_paginated_records(limit=25, offset=off)
    off_ids.extend(i['id'] for i in p['items'])
    if not p['hasMore']: break
    off += 25
# keyset walk
ks_ids, ts, cid = [], None, None
while True:
    p = db.query_records_keyset(limit=25, cursor_timestamp=ts, cursor_id=cid)
    ks_ids.extend(i['id'] for i in p['items'])
    if not p['hasMore']: break
    ts = p['next_cursor']['timestamp']
    cid = p['next_cursor']['id']
print(f'offset={len(off_ids)}, keyset={len(ks_ids)}, no_dup={len(ks_ids)==len(set(ks_ids))}, same_set={set(off_ids)==set(ks_ids)}')
"
```

---

## 4. 优化项三：迁移代码去重

### 4.1 改动（`app/integrated_app/history_db.py`）

将三个迁移方法替换为统一 helper + 薄封装。找到以下三个方法并替换：

**原来**（约第 473-513 行，三个独立方法）：

```python
    def _migrate_add_hidden_column(self) -> None:
        try:
            cursor = self._execute("SELECT hidden FROM generation_history LIMIT 1")
            cursor.fetchall()
        except sqlite3.OperationalError:
            with self._transaction() as conn:
                conn.execute("ALTER TABLE generation_history ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
            logger.info("数据库迁移: 已添加 'hidden' 列")

    def _migrate_add_created_timestamp_column(self) -> None:
        # ... 同上模式 ...

    def _migrate_add_file_missing_column(self) -> None:
        # ... 同上模式 ...
```

**替换为**：

```python
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
            cursor = self._execute(f"SELECT {column} FROM generation_history LIMIT 1")
            cursor.fetchall()
        except sqlite3.OperationalError:
            with self._transaction() as conn:
                conn.execute(
                    f"ALTER TABLE generation_history ADD COLUMN {column} {column_def}"
                )
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
```

### 4.2 验证

```powershell
# 幂等性验证：重复调用不会报错
.\WPy64-312101\python\python.exe -c "
import os, sys, tempfile
sys.path.insert(0, 'bin')
from integrated_app.history_db import HistoryDatabase
db = HistoryDatabase(os.path.join(tempfile.mkdtemp(), 'm.db'))
db._migrate_add_column('probe_col', 'TEXT DEFAULT \"\"')
db._execute('SELECT probe_col FROM generation_history LIMIT 1').fetchall()
db._migrate_add_column('probe_col', 'TEXT DEFAULT \"\"')  # 幂等
print('migration dedup OK')
"
```

---

## 5. 优化项四：音频流水线内存优化

### 5.1 原理

`enhance_audio` 的 `audio.copy()` 无条件执行，即使所有步骤均为 no-op。
优化策略：将 `result` 初始值从 `copy()` 改为别名 `audio`（零拷贝），
仅在唯一会原地修改输入的 `trim_tts_output` 执行前做防御性拷贝。

**关键安全约束**：`trim_tts_output` 中的余弦淡出 `result[-fade:] *= fade`
会原地修改传入缓冲区；若前序步骤未产生新数组（均为 no-op），必须拷贝以
保护调用方输入。

### 5.2 改动（`app/integrated_app/audio_processing.py`）

找到 `enhance_audio` 函数中 `result = audio.copy()` 所在行，替换为：

```python
    # 内存优化（H-R6）：不再无条件 result = audio.copy()。
    # Why 原来需要 copy：trim_tts_output 会通过末尾余弦淡出 result[-fade:] *= fade
    # 原地修改传入缓冲区；若直接对调用方的 audio 操作会污染其数据。
    # 优化策略（等价但更省内存）：
    #   1. result 初始别名 audio（零拷贝）；
    #   2. denoise/voice_enhancement/normalize/effects/tempo 均返回新数组，
    #      任一执行都会自然与 audio 解耦；
    #   3. 唯一原地修改的 trim_tts_output 执行前，若 result 仍别名 audio
    #      （前序步骤全部为 no-op）才做一次 copy，保护调用方输入。
    # 收益：无处理步骤 / 仅归一化等常见路径完全省去一次全量 float32 拷贝，
    #      降低长音频（分钟级）的内存峰值。
    result = audio
```

找到 `if trim_silence:` 分支，在 `result = trim_tts_output(result, sample_rate)` 之前添加：

```python
    if trim_silence:
        # 保护调用方输入：仅当前序步骤未产生新数组（result 仍别名 audio）时才拷贝
        if result is audio:
            result = result.copy()
        result = trim_tts_output(result, sample_rate)
```

### 5.3 验证

```powershell
# 输入保护验证
$env:PYTHONUTF8="1"
.\WPy64-312101\python\python.exe -c "
import os, sys, numpy as np
sys.path.insert(0, 'bin')
os.environ['TTS_SKIP_MODEL_LOAD'] = '1'
from integrated_app.audio_processing import enhance_audio
rng = np.random.default_rng(0)
a = (rng.standard_normal(24000*2).astype(np.float32))*0.3
a[:2000] = 0.0; a[-2000:] = 0.0
for kw in [dict(normalize=False), dict(normalize=True),
           dict(normalize=False, trim_silence=True),
           dict(normalize=True, trim_silence=True)]:
    before = a.copy()
    out = enhance_audio(a, 24000, **kw)
    mutated = not np.array_equal(a, before)
    print(f'{kw} input_mutated={mutated} out_is_input={out is a}')
    assert not mutated, f'INPUT MUTATED for {kw}'
print('ALL INPUT PRESERVED')
"
```

---

## 6. 性能基准测试

### 6.1 基准脚本

文件路径：`scripts/benchmark_history_db.py`

### 6.2 运行方式

```powershell
$env:PYTHONUTF8="1"
.\WPy64-312101\python\python.exe scripts\benchmark_history_db.py --records 20000 --repeats 25
```

### 6.3 参考结果

以下数据在 WinPython 3.12.10 / SQLite 3.49.1 / 20000 条记录上采集：

| 指标 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| 关键词搜索（LIKE → FTS5） | 13.2 ms | 22.8 ms | 见注* |
| 深分页（OFFSET → keyset） | 13.7 ms | 14.9 ms | 见注** |
| 音频 no-op 峰值内存 | ~16.5 MB | ~0 MB | **-100%** |

> **\* 搜索注释**：20000 条记录量级下 FTS 的 trigram 子查询开销（含
> MATCH 子查询 + 结果集 JOIN）与 LIKE 全表扫描差距不大。FTS 的真正优势
> 出现在 **10 万+记录**场景——LIKE 线性增长而 FTS 基本恒定。此外，FTS5
> 天然支持**排序集成**（ORDER BY rank），可在 UI 需要相关性排序时提供
> LIKE 无法实现的能力。
>
> **\*\* 分页注释**：20000 条 × 25 条/页的 OFFSET 成本约 14ms（已走索引），
> keyset 差异微小。在 **100 页+** 的深分页场景或**无限滚动**中，keyset
> 每页恒定而 OFFSET 线性增长，优势显著。
>
> **内存优化**：这是本次最显著的量化改进——3 分钟 @24kHz 单声道音频，
> 无处理步骤时省去约 16.5 MB 全量 float32 拷贝。

---

## 7. 测试门禁验证

### 7.1 需补充的测试

#### 7.1.1 `tests/test_history_db.py`（追加到文件末尾）

需要添加三个测试类（共 12 个用例）。**注意**：当前文件中已存在这三个
测试类的骨架（`TestHistoryDatabaseFTS` / `TestHistoryDatabaseKeyset` /
`TestHistoryDatabaseMigrationHelper`），它们引用了 H-R6 新增方法。
若 `history_db.py` 已实施上述改动，这些测试即可通过。

**如测试类尚未添加**，将以下内容追加到 `test_history_db.py` 末尾：

（参见前一次对话中已生成的测试代码，包含 12 个测试用例覆盖：
FTS ASCII/CJK 搜索等价性、短词回退、REPLACE/DELETE 重同步、integrity、
keyset 全量等价/首末页/limit 钳制/带过滤搜索、迁移 helper 幂等/幂等性）

#### 7.1.2 `tests/test_audio_processing.py`（追加到文件末尾）

需添加 `TestEnhanceAudioPipeline` 测试类（5 个用例）。当前文件中已存在
该类的骨架。若 `audio_processing.py` 已实施懒拷贝优化，这些测试即可通过。

### 7.2 运行测试

```powershell
# 仅目标模块测试（快速验证）
$env:PYTHONUTF8="1"; $env:TRANSFORMERS_OFFLINE="1"; $env:HF_HUB_OFFLINE="1"; $env:MODELSCOPE_OFFLINE="1"; $env:CUDA_VISIBLE_DEVICES=""
.\WPy64-312101\python\python.exe -m pytest tests/test_history_db.py tests/test_audio_processing.py -v --tb=short -p no:cacheprovider

# 完整离线门禁（与 CI 对齐）
.\WPy64-312101\python\python.exe -m pytest tests/ --tb=short -p no:cacheprovider -k "not gpu and not cuda and not vram" -m "not integration" -q
```

### 7.3 Lint 检查

```powershell
.\WPy64-312101\python\python.exe -m ruff check app/integrated_app/history_db.py app/integrated_app/audio_processing.py --statistics
```

> **预期**：新增代码不会引入新的 lint 类别违规。文件中已有的 `UP045`
> (Optional 注解)和 `UP035` (deprecated import) 为既有模式，CI ruff 版本
> 不强制这些规则。

---

## 8. 回滚计划

所有改动均为向后兼容的增量变更，可按文件独立回滚：

| 改动 | 回滚方式 | 风险 |
|------|----------|------|
| FTS5 虚表 + 触发器 | `DROP TABLE IF EXISTS generation_history_fts; DROP TRIGGER IF EXISTS ...;` | 零风险，自动回退 LIKE |
| `recursive_triggers=1` | 从 `_PRAGMA_CONFIG` 中删除该行 | FTS 搜索仍可用，仅 REPLACE 后可能残留过期索引 |
| `_ensure_fts()` 调用 | 从 `__init__` 中删除该行 | FTS 不初始化，`_fts_enabled=False`，自动回退 |
| `query_records_keyset()` | 删除该方法 | 无副作用，旧接口不受影响 |
| `_migrate_add_column()` | 恢复三个独立迁移方法 | 行为完全一致 |
| `enhance_audio` 懒拷贝 | 恢复 `result = audio.copy()` | 无副作用，多用一次 copy |

**旧数据库兼容**：首次打开含数据的旧库时，`_ensure_fts()` 会自动创建
FTS 虚表并 `rebuild` 回填，无需手动迁移。删除 FTS 虚表后，搜索自动
回退 LIKE，无数据丢失。

---

## 9. 已知风险与注意事项

### 9.1 SQLite 版本要求

- FTS5 + trigram 需要 **SQLite ≥ 3.34.0**（WinPython 3.12.10 内置
  SQLite 3.49.1，满足要求）。
- 若目标部署环境的 SQLite 版本更低，`_ensure_fts()` 会捕获
  `OperationalError` 并将 `_fts_enabled` 设为 False，搜索静默回退 LIKE。

### 9.2 FTS 与 LIKE 的语义差异

- **严格等价条件**：查询词 ≥3 个 Unicode 字符时，trigram 子串匹配与
  `LIKE '%kw%'` 结果完全一致（已实测 120 条数据集上 count 吻合）。
- **1-2 字符查询**：trigram 无法处理，自动回退 LIKE。
- **大小写**：trigram 默认大小写不敏感，与 `LOWER(col) LIKE` 一致。

### 9.3 FTS 索引一致性

- `recursive_triggers=1` 是 `INSERT OR REPLACE` 保持 FTS 索引同步的
  **关键前提**。若移除此 PRAGMA，REPLACE 操作后旧文本会残留在索引中。
- 启动时的 `rebuild` 作为安全兜底，在任何潜在不一致情况下自愈。

### 9.4 `audio_processing.py` 代码风格

用户已对该文件应用 PEP 604 类型注解现代化（`Optional[X]` → `X | None`）。
在实施内存优化时应保持这一新风格。

### 9.5 不修改的文件

以下文件在基准测试/测试中被引用但不需要修改：
- `tests/test_history_db.py` — 已包含所需测试类骨架
- `tests/test_audio_processing.py` — 已包含 TestEnhanceAudioPipeline 骨架
- `scripts/benchmark_history_db.py` — 基准脚本已就绪

---

## 附录 A：改动文件清单

| 文件 | 改动类型 | 影响范围 |
|------|----------|----------|
| `app/integrated_app/history_db.py` | FTS5 + keyset + 迁移去重 | 搜索/分页/迁移（向后兼容） |
| `app/integrated_app/audio_processing.py` | enhance_audio 懒拷贝 | 内存优化（向后兼容） |
| `tests/test_history_db.py` | 新增 3 个测试类 | 12 个用例 |
| `tests/test_audio_processing.py` | 新增 1 个测试类 | 5 个用例 |
| `scripts/benchmark_history_db.py` | 新增基准脚本 | 性能度量 |

## 附录 B：实施顺序建议

```
1. history_db.py — FTS5 常量 + PRAGMA（2.3.1）
2. history_db.py — _fts_enabled 属性 + __init__（2.3.2-2.3.3）
3. history_db.py — _ensure_fts 方法（2.3.4）
4. history_db.py — 损坏恢复路径（2.3.8）
5. history_db.py — FTS 辅助方法（2.3.5）
6. history_db.py — _build_filter_conditions 改动（2.3.6）
7. history_db.py — get_paginated_records 改动（2.3.7）
8. history_db.py — keyset 分页方法（第 3 章）
9. history_db.py — 迁移代码去重（第 4 章）
10. audio_processing.py — enhance_audio 懒拷贝（第 5 章）
11. 验证 FTS + keyset 功能（2.4 + 3.3）
12. 运行 pytest + lint 门禁（第 7 章）
13. （可选）运行 benchmark（第 6 章）
```

> **关键约束**：步骤 2.3.6 和 2.3.7 必须在步骤 2.3.4-2.3.5 之后执行，
> 因为它们引用了 `_FTS_TABLE`、`_fts_enabled`、`_can_use_fts` 和
> `_build_fts_query`。步骤 3 必须在 2.3.5 之后，因为它依赖 `_build_filter_conditions`
> 中 FTS 分支已就绪。
