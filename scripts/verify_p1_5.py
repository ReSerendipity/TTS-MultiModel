import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

print("=== 1. 语法检查 ===")
import py_compile

for f in ["app/integrated_app/app_server.py", "app/integrated_app/history_db.py"]:
    try:
        py_compile.compile(f, doraise=True)
        print(f"  OK: {f}")
    except py_compile.PyCompileError as e:
        print(f"  FAIL: {f}: {e}")
        sys.exit(1)

print("\n=== 2. cleanup_orphan_records 功能验证 ===")
from integrated_app.history_db import HistoryDatabase

tmpdir = tempfile.mkdtemp()
try:
    db_path = os.path.join(tmpdir, "test.db")
    db = HistoryDatabase(db_path)
    # 插入 3 条记录，其中 2 条文件不存在
    records = [
        {
            "filename": "exist.wav",
            "filepath": os.path.join(tmpdir, "exist.wav"),
            "created_at": "2026-01-01 00:00:00",
            "file_size_bytes": 100,
            "text_preview": "t1",
            "engine": "voxcpm2",
            "created_timestamp": 1767225600,
        },
        {
            "filename": "missing1.wav",
            "filepath": os.path.join(tmpdir, "missing1.wav"),
            "created_at": "2026-01-02 00:00:00",
            "file_size_bytes": 200,
            "text_preview": "t2",
            "engine": "voxcpm2",
            "created_timestamp": 1767312000,
        },
        {
            "filename": "missing2.wav",
            "filepath": os.path.join(tmpdir, "missing2.wav"),
            "created_at": "2026-01-03 00:00:00",
            "file_size_bytes": 300,
            "text_preview": "t3",
            "engine": "voxcpm2",
            "created_timestamp": 1767398400,
        },
    ]
    open(os.path.join(tmpdir, "exist.wav"), "w").close()
    db.insert_batch(records)
    # 清理前 file_missing 全 0
    conn = sqlite3.connect(db_path)
    before = conn.execute("SELECT COUNT(*) FROM generation_history WHERE file_missing=1").fetchone()[0]
    print(f"  清理前 file_missing=1: {before}")
    assert before == 0
    # 执行清理
    orphan = db.cleanup_orphan_records()
    print(f"  cleanup_orphan_records 返回: {orphan} (期望 2)")
    assert orphan == 2
    after = conn.execute("SELECT COUNT(*) FROM generation_history WHERE file_missing=1").fetchone()[0]
    print(f"  清理后 file_missing=1: {after} (期望 2)")
    assert after == 2
    # 存在的文件不应被标记
    exist_missing = conn.execute("SELECT file_missing FROM generation_history WHERE filename='exist.wav'").fetchone()[0]
    print(f"  exist.wav file_missing: {exist_missing} (期望 0)")
    assert exist_missing == 0
    conn.close()
    print("  ✓ orphan 标记功能正确")
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

print("\n=== 3. app_server.py 调用点确认 ===")
with open("app/integrated_app/app_server.py", encoding="utf-8") as f:
    src = f.read()
print(f"  cleanup_orphan_records 调用存在: {'cleanup_orphan_records' in src}")
assert "cleanup_orphan_records" in src
print("  ✓ 调用点已接入")

print("\n=== 4. 现有库 orphan 扫描验证 ===")
import integrated_app.history_db as hdb_mod

hdb_mod._history_db = None
from integrated_app.history_db import get_history_db

db = get_history_db()
# 不实际执行 cleanup（会修改生产库），只验证方法可用
print(f"  方法存在: {hasattr(db, 'cleanup_orphan_records')}")
print(
    f"  当前 file_missing=1: {db._execute('SELECT COUNT(*) FROM generation_history WHERE file_missing=1').fetchone()[0]}"
)

print("\nP1-5 验收全部通过 ✓")
