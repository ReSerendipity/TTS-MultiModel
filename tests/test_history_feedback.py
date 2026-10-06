"""P2-#8：history.db 反馈字段 roundtrip 测试。"""

import os
import sys

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")


@pytest.fixture(autouse=True)
def _disable_pii(monkeypatch):
    monkeypatch.setattr("integrated_app.history_db._get_pii_cipher", lambda: None)


@pytest.fixture
def db(tmp_path):
    from integrated_app.history_db import HistoryDatabase

    return HistoryDatabase(db_path=str(tmp_path / "feedback.db"))


def _add_one(db) -> int:
    rid = db.add_record(
        filename="fb.wav",
        filepath="/out/fb.wav",
        created_at="2026-10-06T00:00:00",
        file_size=1024,
        duration_seconds=2.0,
        engine="voxcpm2",
        text_preview="feedback roundtrip",
    )
    return rid


def _get(db, rid: int) -> dict:
    for row in db.get_paginated_records(limit=50, offset=0)["items"]:
        if row["id"] == rid:
            return row
    raise AssertionError(f"record {rid} not found")


def test_feedback_columns_exist(db):
    """v008 迁移后四个反馈列存在，默认 NULL/空。"""
    rid = _add_one(db)
    row = _get(db, rid)
    assert row["feedback_rating"] is None
    assert row["feedback_liked"] is None
    assert row["feedback_note"] == ""
    assert row["feedback_at"] is None


def test_set_feedback_roundtrip(db):
    """写入 rating/liked/note 后能读回，feedback_at 自动填充。"""
    rid = _add_one(db)
    ok = db.set_feedback(rid, rating=5, liked=1, note="很好听")
    assert ok is True
    row = _get(db, rid)
    assert row["feedback_rating"] == 5
    assert row["feedback_liked"] == 1
    assert row["feedback_note"] == "很好听"
    assert row["feedback_at"] is not None


def test_set_feedback_partial_update_keeps_existing(db):
    """只更新 liked 时，已写入的 rating/note 不被清空。"""
    rid = _add_one(db)
    db.set_feedback(rid, rating=3, note="一般")
    db.set_feedback(rid, liked=0)
    row = _get(db, rid)
    assert row["feedback_rating"] == 3
    assert row["feedback_note"] == "一般"
    assert row["feedback_liked"] == 0


def test_set_feedback_missing_record_returns_false(db):
    """不存在的 id 返回 False。"""
    assert db.set_feedback(999999, rating=1) is False


def test_migration_is_idempotent(tmp_path):
    """重复实例化 HistoryDatabase（重复跑迁移）不报错、行数不变。"""
    from integrated_app.history_db import HistoryDatabase

    p = str(tmp_path / "idem.db")
    d1 = HistoryDatabase(db_path=p)
    rid = _add_one(d1)
    d1.set_feedback(rid, rating=5)
    # 再次打开（构造函数重跑 _run_versioned_migrations，v008 已记录应跳过）
    d2 = HistoryDatabase(db_path=p)
    row = _get(d2, rid)
    assert row["feedback_rating"] == 5
