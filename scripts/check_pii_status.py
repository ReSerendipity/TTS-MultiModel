import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from integrated_app.history_db import _PII_PREFIX, _get_pii_cipher, get_history_db

db = get_history_db()
conn = sqlite3.connect(db._db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
total = cur.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0]
encrypted = cur.execute(
    "SELECT COUNT(*) FROM generation_history WHERE text_preview LIKE ?", (f"{_PII_PREFIX}%",)
).fetchone()[0]
plain = cur.execute(
    "SELECT COUNT(*) FROM generation_history WHERE text_preview != '' AND text_preview NOT LIKE ?", (f"{_PII_PREFIX}%",)
).fetchone()[0]
empty = cur.execute(
    "SELECT COUNT(*) FROM generation_history WHERE text_preview = '' OR text_preview IS NULL"
).fetchone()[0]
print(f"总记录: {total}")
print(f"已加密(enc:前缀): {encrypted}")
print(f"明文(非空非enc): {plain}")
print(f"空值: {empty}")
cipher = _get_pii_cipher()
print(f"加密器可用: {cipher is not None}")
# 抽样验证一条可解密
if encrypted > 0:
    row = cur.execute(
        "SELECT id, text_preview FROM generation_history WHERE text_preview LIKE ? LIMIT 1", (f"{_PII_PREFIX}%",)
    ).fetchone()
    from integrated_app.history_db import _decrypt_pii

    decrypted = _decrypt_pii(row["text_preview"])
    print(f"抽样 id={row['id']} 解密成功: {decrypted[:30]}...")
conn.close()
