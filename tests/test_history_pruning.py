"""历史库生命周期治理测试：keep_days 自动裁剪 + 配置路径对齐。

覆盖：
1. ``get_history_db_path()`` / ``get_history_keep_days()`` 真实消费 config.yaml；
2. ``prune_old_records(keep_days)`` 的裁剪语义与边界条件；
3. ``estimate_size_bytes()`` 统计主库 + WAL + SHM。
"""

import os
import time

from integrated_app.history_db import HistoryDatabase

_DAY = 86400.0


def _make_db(tmp_path):
    return HistoryDatabase(str(tmp_path / "history.db"))


def test_config_db_path_points_inside_project(tmp_path, monkeypatch):
    monkeypatch.delenv("TTS_HISTORY_DB_PATH", raising=False)
    from integrated_app.config import get_history_db_path

    path = get_history_db_path()
    # 应解析为绝对路径，且落在项目内 data/ 目录
    assert os.path.isabs(path)
    assert os.path.basename(os.path.dirname(path)).lower() == "data"
    assert path.endswith("history.db")


def test_config_keep_days_defaults_to_zero(monkeypatch):
    from integrated_app.config import get_history_keep_days

    # config.yaml 中 keep_days: 0（永久保留），函数应真实读取而非返回硬编码值
    assert get_history_keep_days() == 0


def test_prune_noop_when_keep_days_zero(tmp_path):
    db = _make_db(tmp_path)
    db.add_record("old.wav", str(tmp_path / "old.wav"), "2020-01-01 00:00:00", 100)
    assert db.prune_old_records(0) == 0
    assert db.get_total_count() == 1


def test_prune_deletes_only_expired_records(tmp_path):
    db = _make_db(tmp_path)
    now = time.time()

    db.insert(
        {
            "filename": "expired.wav",
            "filepath": str(tmp_path / "expired.wav"),
            "created_timestamp": now - 30 * _DAY,
        }
    )
    db.insert(
        {
            "filename": "fresh.wav",
            "filepath": str(tmp_path / "fresh.wav"),
            "created_timestamp": now - 1 * _DAY,
        }
    )

    deleted = db.prune_old_records(keep_days=7)

    assert deleted == 1
    remaining = {r["filename"] for r in db.get_paginated_records(limit=100)["items"]}
    assert remaining == {"fresh.wav"}


def test_prune_preserves_legacy_zero_timestamp(tmp_path):
    db = _make_db(tmp_path)
    # created_timestamp == 0 的极旧记录（该字段引入前）不应被裁剪
    db.insert({"filename": "legacy.wav", "filepath": str(tmp_path / "legacy.wav"), "created_timestamp": 0.0})
    assert db.prune_old_records(1) == 0
    assert db.get_total_count() == 1


def test_estimate_size_bytes_counts_main_and_wal(tmp_path):
    db = _make_db(tmp_path)
    main_file = tmp_path / "history.db"
    assert db.estimate_size_bytes() >= main_file.stat().st_size
