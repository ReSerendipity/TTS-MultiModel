import os
import sqlite3

f = "outputs/history.db.migrated_1785477740"
print(f"文件大小: {os.path.getsize(f) / 1024 / 1024:.2f} MB")
c = sqlite3.connect(f)
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(f"表: {tables}")
if "generation_history" in tables:
    print(f"generation_history 行数: {c.execute('SELECT COUNT(*) FROM generation_history').fetchone()[0]}")
c.close()
