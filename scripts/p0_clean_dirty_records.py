import os
import shutil
import sqlite3

DB = "data/history.db"
BAK = "data/history.db.bak_20260905"

print("=== 1. 备份 ===")
if not os.path.exists(BAK):
    shutil.copy2(DB, BAK)
print(f"备份: {BAK} ({os.path.getsize(BAK)} bytes)")

c = sqlite3.connect(DB)

print("=== 2. 清理前 ===")
dirty = c.execute("SELECT COUNT(*) FROM generation_history WHERE LENGTH(output_format)>1000").fetchone()[0]
dirty_bytes = c.execute(
    "SELECT SUM(LENGTH(output_format)) FROM generation_history WHERE LENGTH(output_format)>1000"
).fetchone()[0]
print(f"脏记录数: {dirty}, 总字节: {dirty_bytes}")

print("=== 3. UPDATE 清理 output_format ===")
c.execute("UPDATE generation_history SET output_format='wav' WHERE LENGTH(output_format)>1000")
c.commit()
print(f"已更新行数: {c.total_changes}")

print("=== 4. VACUUM ===")
c.execute("VACUUM")
c.commit()
print("VACUUM 完成")

print("=== 5. 清理后验证 ===")
total = c.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0]
dirty_after = c.execute("SELECT COUNT(*) FROM generation_history WHERE LENGTH(output_format)>1000").fetchone()[0]
fmt_dist = c.execute("SELECT output_format,COUNT(*) FROM generation_history GROUP BY output_format").fetchall()
size = os.path.getsize(DB)
print(f"总记录: {total}")
print(f"脏记录残留: {dirty_after}")
print(f"output_format 分布: {fmt_dist}")
print(f"库体积: {size} bytes = {size / 1024 / 1024:.2f} MB")
c.close()
print("P0-1 完成")
