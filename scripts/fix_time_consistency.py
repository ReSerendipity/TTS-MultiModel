"""P1-7：统一 created_at 与 created_timestamp 时间口径。

以 created_timestamp 为权威时间源，重新生成统一格式的 created_at。
修复后重算 HMAC 链（因为 created_at 是 HMAC 输入字段之一）。
"""

import contextlib
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from integrated_app.history_db import get_history_db

db = get_history_db()
conn = sqlite3.connect(db._db_path)
conn.row_factory = sqlite3.Row

rows = conn.execute("SELECT id, created_at, created_timestamp FROM generation_history ORDER BY id").fetchall()
print(f"总记录: {len(rows)}")

updates = []
fixed_format = 0
fixed_value = 0
for row in rows:
    _id, ca, ct = row["id"], row["created_at"], row["created_timestamp"]
    new_ca = ca

    # 以 created_timestamp 为权威生成 created_at
    if ct and ct > 0:
        with contextlib.suppress(ValueError, OSError):
            new_ca = datetime.fromtimestamp(float(ct)).strftime("%Y-%m-%d %H:%M:%S")
    elif ca:
        # created_timestamp 为 0，从 created_at 解析
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                ct = datetime.strptime(ca, fmt).timestamp()
                new_ca = datetime.fromtimestamp(ct).strftime("%Y-%m-%d %H:%M:%S")
                break
            except ValueError:
                continue

    if new_ca != ca:
        if "T" in (ca or "") or "." in (ca or ""):
            fixed_format += 1
        else:
            fixed_value += 1
        updates.append((new_ca, ct, _id))

print(f"需修复: {len(updates)} 条 (格式统一 {fixed_format}, 值修正 {fixed_value})")
if updates:
    conn.executemany("UPDATE generation_history SET created_at = ?, created_timestamp = ? WHERE id = ?", updates)
    conn.commit()
    print(f"已更新 {len(updates)} 条记录")

# 验证修复结果
rows2 = conn.execute("SELECT id, created_at, created_timestamp FROM generation_history ORDER BY id").fetchall()
inconsistent = 0
iso_format = 0
for r in rows2:
    ca, ct = r["created_at"], r["created_timestamp"]
    if "T" in (ca or ""):
        iso_format += 1
    if ct and ct > 0 and ca:
        try:
            ca_ts = datetime.strptime(ca, "%Y-%m-%d %H:%M:%S").timestamp()
            if abs(ct - ca_ts) > 1:
                inconsistent += 1
        except ValueError:
            inconsistent += 1
print("\n修复后验证:")
print(f"  ISO 格式残留: {iso_format} (期望 0)")
print(f"  不一致(>1秒): {inconsistent} (期望 0)")
conn.close()

# 重算 HMAC 链（created_at 改变会影响 HMAC）
print("\n重算 HMAC 链...")
recomputed = db._recompute_chain_from(0)
print(f"已重算 {recomputed} 条记录的 HMAC 链")

# 最终验证链完整性
chain = db.verify_chain_integrity()
print(f"链校验: verified={chain['verified']} tampered={chain['tampered_count']}")
print("P1-7 时间口径修复完成")
