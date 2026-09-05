import os
import sqlite3

DB = "data/history.db"
print(f"文件大小: {os.path.getsize(DB)} bytes = {os.path.getsize(DB) / 1024 / 1024:.2f} MB")
print("page_count: ", end="")
c = sqlite3.connect(DB)
print(c.execute("PRAGMA page_count").fetchone()[0], "x", c.execute("PRAGMA page_size").fetchone()[0], "bytes")
print(f"freelist_count: {c.execute('PRAGMA freelist_count').fetchone()[0]}")
print(f"auto_vacuum: {c.execute('PRAGMA auto_vacuum').fetchone()[0]}")
print(f"journal_mode: {c.execute('PRAGMA journal_mode').fetchone()[0]}")

print("\n=== 各表 dbstat 页数 ===")
for row in c.execute(
    "SELECT name, SUM(pgsize) as total_size, COUNT(*) as pages FROM dbstat GROUP BY name ORDER BY total_size DESC"
):
    print(f"  {row[0]:40s} {row[1] / 1024 / 1024:10.2f} MB  ({row[2]} pages)")

print("\n=== generation_history 各列最大长度 ===")
cols = [r[1] for r in c.execute("PRAGMA table_info(generation_history)").fetchall()]
for col in cols:
    try:
        mx = c.execute(f"SELECT MAX(LENGTH(CAST({col} AS TEXT))) FROM generation_history").fetchone()[0]
        if mx and mx > 1000:
            print(f"  {col:25s} max_len={mx}")
    except Exception:
        pass

print("\n=== 仍有大字段的记录 ===")
for col in cols:
    try:
        cnt = c.execute(f"SELECT COUNT(*) FROM generation_history WHERE LENGTH(CAST({col} AS TEXT))>10000").fetchone()[
            0
        ]
        if cnt > 0:
            print(f"  {col}: {cnt} 条 >10000 字符")
    except Exception:
        pass

print("\n=== FTS 表大小 ===")
for t in [
    "generation_history_fts",
    "generation_history_fts_data",
    "generation_history_fts_idx",
    "generation_history_fts_docsize",
    "generation_history_fts_config",
]:
    try:
        cnt = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {cnt} 行")
    except Exception as e:
        print(f"  {t}: {e}")

c.close()
