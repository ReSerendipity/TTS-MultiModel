"""scripts/backup_history_db.py 的备份 / 恢复 / 演练能力测试（使用小型临时库）。

不接触生产 data/history.db，全部在 tmp_path 内完成。
"""

import os
import sqlite3
import sys

import pytest

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import backup_history_db as bk  # noqa: E402


def _seed_db(path: str, rows: int = 5) -> str:
    """创建一个含 generation_history 表的最小历史库。"""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE generation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL UNIQUE,
                created_timestamp REAL NOT NULL DEFAULT 0
            )
            """
        )
        for i in range(rows):
            conn.execute(
                "INSERT INTO generation_history (filename, filepath, created_timestamp) VALUES (?, ?, ?)",
                (f"f{i}.wav", os.path.join(os.path.dirname(path), f"f{i}.wav"), float(i)),
            )
        conn.commit()
    finally:
        conn.close()
    return path


def test_backup_creates_snapshot(tmp_path):
    db = _seed_db(str(tmp_path / "history.db"))
    backup_dir = str(tmp_path / "backups")

    dest = bk.create_backup(db, backup_dir)

    assert os.path.exists(dest)
    assert os.path.getsize(dest) > 0


def test_restore_recovers_all_rows(tmp_path):
    db = _seed_db(str(tmp_path / "history.db"), rows=7)
    backup_dir = str(tmp_path / "backups")
    backup_path = bk.create_backup(db, backup_dir)

    # 破坏源库，验证可从备份恢复
    os.remove(db)
    target = str(tmp_path / "restored" / "history.db")
    restored = bk.restore_backup(backup_path, target)

    conn = sqlite3.connect(restored)
    try:
        count = conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0]
    finally:
        conn.close()
    assert count == 7


def test_backup_compress_roundtrip(tmp_path):
    db = _seed_db(str(tmp_path / "history.db"), rows=3)
    backup_dir = str(tmp_path / "backups")
    backup_path = bk.create_backup(db, backup_dir, compress=True)

    assert backup_path.endswith(".gz")
    target = str(tmp_path / "restored" / "history.db")
    bk.restore_backup(backup_path, target)
    assert bk._count_rows(target) == 3


def test_keep_prunes_old_backups(tmp_path):
    db = _seed_db(str(tmp_path / "history.db"))
    backup_dir = str(tmp_path / "backups")

    for _ in range(4):
        bk.create_backup(db, backup_dir, keep=0)
    # 生成 4 份后指定只保留 2 份
    bk.create_backup(db, backup_dir, keep=2)

    remaining = [f for f in os.listdir(backup_dir) if f.startswith("history_backup_")]
    assert len(remaining) == 2


def test_backup_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        bk.create_backup(str(tmp_path / "nope.db"), str(tmp_path / "backups"))


def test_restore_missing_backup_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        bk.restore_backup(str(tmp_path / "missing.db"), str(tmp_path / "target.db"))


def test_drill_passes_on_small_db(tmp_path):
    db = _seed_db(str(tmp_path / "history.db"), rows=4)
    assert bk.run_drill(db, str(tmp_path / "backups")) is True
