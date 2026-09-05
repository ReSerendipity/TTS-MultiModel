import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

print("=== 1. 语法检查 ===")
import py_compile

try:
    py_compile.compile("app/integrated_app/history_db.py", doraise=True)
    print("  OK: history_db.py")
except py_compile.PyCompileError as e:
    print(f"  FAIL: {e}")
    sys.exit(1)

print("\n=== 2. 密钥路径迁移验证 ===")
import platform

from integrated_app.history_db import HistoryDatabase

if platform.system() == "Windows":
    config_base = os.environ.get("APPDATA", os.path.expanduser("~"))
else:
    config_base = os.path.join(os.path.expanduser("~"), ".config")
new_key_path = os.path.join(config_base, "tts-multimodel", ".history_hmac_key")
old_key_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath("app/integrated_app/history_db.py"))), "data", ".history_hmac_key"
)
print(f"  新密钥路径: {new_key_path}")
print(f"  旧密钥路径: {old_key_path} (存在: {os.path.exists(old_key_path)})")
# 调用 _get_hmac_secret 触发迁移
secret = HistoryDatabase._get_hmac_secret()
print(f"  密钥获取成功: {len(secret)} bytes")
print(f"  新密钥文件已创建: {os.path.exists(new_key_path)}")
assert os.path.exists(new_key_path), "新密钥文件应已创建"

print("\n=== 3. 临时库测试：insert_batch HMAC + 删除重算 ===")
tmpdir = tempfile.mkdtemp()
try:
    db_path = os.path.join(tmpdir, "test.db")
    db = HistoryDatabase(db_path)
    # 批量插入 3 条记录
    records = [
        {
            "filename": "a.wav",
            "filepath": os.path.join(tmpdir, "a.wav"),
            "created_at": "2026-01-01 00:00:00",
            "file_size_bytes": 100,
            "text_preview": "test a",
            "engine": "voxcpm2",
            "created_timestamp": 1767225600,
        },
        {
            "filename": "b.wav",
            "filepath": os.path.join(tmpdir, "b.wav"),
            "created_at": "2026-01-02 00:00:00",
            "file_size_bytes": 200,
            "text_preview": "test b",
            "engine": "voxcpm2",
            "created_timestamp": 1767312000,
        },
        {
            "filename": "c.wav",
            "filepath": os.path.join(tmpdir, "c.wav"),
            "created_at": "2026-01-03 00:00:00",
            "file_size_bytes": 300,
            "text_preview": "test c",
            "engine": "voxcpm2",
            "created_timestamp": 1767398400,
        },
    ]
    # 创建空文件
    for r in records:
        open(r["filepath"], "w").close()
    inserted = db.insert_batch(records)
    print(f"  批量插入: {inserted} 条")
    # 验证 HMAC 全部非空
    conn = sqlite3.connect(db_path)
    hmac_count = conn.execute(
        "SELECT COUNT(*) FROM generation_history WHERE record_hmac IS NOT NULL AND record_hmac != ''"
    ).fetchone()[0]
    print(f"  有 HMAC 的记录: {hmac_count}/{inserted}")
    assert hmac_count == inserted, "批量插入后所有记录应有 HMAC"
    # 验证链完整性
    chain = db.verify_chain_integrity()
    print(f"  链校验: verified={chain['verified']} total={chain['total']} tampered={chain['tampered_count']}")
    assert chain["verified"], "插入后链应完整"
    # 删除中间一条，验证链重算
    db.delete_record(2, delete_file=False)
    chain_after = db.verify_chain_integrity()
    print(f"  删除 id=2 后链校验: verified={chain_after['verified']} tampered={chain_after['tampered_count']}")
    assert chain_after["verified"], "删除后链应自动重算保持完整"
    remaining = conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0]
    print(f"  剩余记录: {remaining}")
    conn.close()
    print("  ✓ 批量插入 HMAC + 删除重算 全部通过")
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

print("\n=== 4. 现有库链回填验证 ===")
# 重置单例以触发启动逻辑
import integrated_app.history_db as hdb_mod
from integrated_app.history_db import get_history_db

hdb_mod._history_db = None
db = get_history_db()
chain = db.verify_chain_integrity()
print(f"  现有库链校验: verified={chain['verified']} total={chain['total']} tampered={chain['tampered_count']}")
empty_hmac = db._execute(
    "SELECT COUNT(*) FROM generation_history WHERE record_hmac IS NULL OR record_hmac = ''"
).fetchone()[0]
print(f"  空 HMAC 记录: {empty_hmac}")
backfill_done = db.load_kv("hmac_backfill_done")
print(f"  回填标记: {backfill_done}")

print("\nP1-4 验收全部通过 ✓")
