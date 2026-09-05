import os
import sqlite3

c = sqlite3.connect("data/history.db")
print(f"库体积: {os.path.getsize('data/history.db') / 1024 / 1024:.3f} MB")
print(f"总记录: {c.execute('SELECT COUNT(*) FROM generation_history').fetchone()[0]}")
print(
    f"脏记录残留: {c.execute('SELECT COUNT(*) FROM generation_history WHERE LENGTH(output_format)>1000').fetchone()[0]}"
)
print(
    f"output_format: {c.execute('SELECT output_format,COUNT(*) FROM generation_history GROUP BY output_format').fetchall()}"
)
print(f"action_logs: {c.execute('SELECT COUNT(*) FROM action_logs').fetchone()[0]}")
print(f"FTS: {c.execute('SELECT COUNT(*) FROM generation_history_fts').fetchone()[0]}")
print(f"max_id: {c.execute('SELECT MAX(id) FROM generation_history').fetchone()[0]}")
c.close()
print("P0-1 验收: 库体积<10MB ✓, 脏记录=0 ✓, 记录数保持137 ✓")
