import sqlite3
from datetime import datetime

c = sqlite3.connect("data/history.db")
c.row_factory = sqlite3.Row
rows = c.execute("SELECT id, created_at, created_timestamp FROM generation_history ORDER BY id").fetchall()
print(f"总记录: {len(rows)}")
inconsistent = 0
zero_ts = 0
zero_at = 0
for r in rows:
    ca = r["created_at"]
    ct = r["created_timestamp"]
    if not ct or ct == 0:
        zero_ts += 1
        continue
    if not ca:
        zero_at += 1
        continue
    try:
        ca_ts = datetime.strptime(ca, "%Y-%m-%d %H:%M:%S").timestamp()
        diff = abs(ct - ca_ts)
        if diff > 86400:
            inconsistent += 1
            if inconsistent <= 5:
                print(f"  id={r['id']} created_at={ca} ({ca_ts:.0f}) created_timestamp={ct} diff={diff / 3600:.1f}h")
    except Exception as e:
        print(f"  id={r['id']} 解析失败: {e}")
print(f"\ncreated_timestamp=0: {zero_ts}")
print(f"created_at 为空: {zero_at}")
print(f"不一致(>1天): {inconsistent}")
c.close()
