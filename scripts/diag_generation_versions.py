import os
import sqlite3

db_path = "outputs/generation_versions.db"
if not os.path.exists(db_path):
    print(f"文件不存在: {db_path}")
    exit()
c = sqlite3.connect(db_path)
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(f"表: {tables}")
for t in tables:
    print(f"\n--- {t} ---")
    print(f"schema: {c.execute(f'PRAGMA table_info({t})').fetchall()}")
    print(f"行数: {c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})").fetchall()]
    if "parent_id" in cols:
        non_null = c.execute(f"SELECT COUNT(*) FROM {t} WHERE parent_id IS NOT NULL AND parent_id != ''").fetchone()[0]
        print(f"parent_id非空: {non_null}")
    print(f"样例: {c.execute(f'SELECT * FROM {t} LIMIT 2').fetchall()}")
c.close()
