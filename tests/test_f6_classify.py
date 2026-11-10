"""F6 分类器测试：规则边界 + 333 行端到端 + 只读/可复算自证。

测试不碰生产 ``data/history.db``：全部用 ``tmp_path`` 下合成库。合成库按文档口径
（41 + 3 + 22 + 179 + 88 = 333）造行，端到端跑 ``run()`` 并核对：
- 未检范围 267、keep 267；
- 两次输出逐字节一致（可复算）；
- 跑前跑后库 sha256 不变、不产生 ``-wal/-shm``（零写入）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE action_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    level TEXT NOT NULL,
    action TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    extra_json TEXT NOT NULL DEFAULT '{}'
)
"""


def _load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


f6 = _load_module("f6_classify_under_test", "tools/f6_classify.py")


def _make_db(path: Path, rows: list[tuple[str, str, dict]]) -> Path:
    con = sqlite3.connect(path)
    try:
        con.execute(SCHEMA)
        for action, message, extra in rows:
            con.execute(
                "INSERT INTO action_logs (ts_ms, level, action, message, extra_json) VALUES (?,?,?,?,?)",
                (1788000000000, "INFO", action, message, json.dumps(extra, ensure_ascii=False)),
            )
        con.commit()
    finally:
        con.close()
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row(
    *,
    rid: int = 1,
    ts_ms: int = 1788000000000,
    action: str = "generation",
    message: str = "",
    extra: dict | None = None,
    extra_raw: str | None = None,
) -> f6.LogRow:
    raw = extra_raw if extra_raw is not None else json.dumps(extra or {}, ensure_ascii=False)
    parsed, error = f6._load_extra(raw)
    return f6.LogRow(
        id=rid,
        ts_ms=ts_ms,
        level="INFO",
        action=action,
        message=message,
        extra_raw=raw,
        extra=parsed,
        extra_error=error,
    )


def _gen_extra(**overrides) -> dict:
    base = {
        "endpoint": "VoxCPM design",
        "engine": "voxcpm2",
        "voice_persona": "温柔女声",
        "text_length": 10,
        "duration": 5.0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 规则边界
# ---------------------------------------------------------------------------


def test_hard_test_signatures_are_quarantine() -> None:
    cases = [
        (
            _row(action="test_operation", message="测试操作", extra={}),
            "Q1_TEST_OPERATION",
        ),
        (
            _row(action="unknown", message="", extra_raw=""),
            "Q2_UNKNOWN_EMPTY",
        ),
        (
            _row(
                message="ep success (1.0s)",
                extra=_gen_extra(endpoint="ep", engine="test_stub", duration=1.0),
            ),
            "Q3_TEST_ENGINE",
        ),
        (
            _row(
                message="VoxCPM design failed (0.3s)",
                extra=_gen_extra(text_length=8192, duration=0.3, error="x"),
            ),
            "Q4_TEXT_LENGTH_8192",
        ),
        (
            _row(
                message="VoxCPM design failed (1.0s)",
                extra=_gen_extra(duration=1.0, error=f6.STUB_ERROR_FRAGMENT),
            ),
            "Q5_STUB_ERROR",
        ),
    ]
    for row, expected_rule in cases:
        decision = f6.classify(row)
        assert decision.decision == "quarantine", (row.message, decision)
        assert decision.rule_id == expected_rule


def test_generation_field_boundaries_are_needs_manual() -> None:
    cases = [
        (_row(message="x", extra_raw="{bad"), "N1_BAD_EXTRA_JSON"),
        (
            _row(message="VoxCPM design success (5.0s)", extra={"endpoint": "VoxCPM design", "engine": "voxcpm2"}),
            "N2_MISSING_FIELD",
        ),
        (
            _row(
                message="VoxCPM design success (5.0s)",
                extra=_gen_extra(engine="unknown-engine"),
            ),
            "N5_UNKNOWN_ENGINE",
        ),
        (
            _row(
                message="IndexTTS2 success (5.0s)",
                extra=_gen_extra(endpoint="IndexTTS2", engine="voxcpm2"),
            ),
            "N6_ENDPOINT_ENGINE_MISMATCH",
        ),
        (
            _row(
                message="VoxCPM design success (5.0s)",
                extra=_gen_extra(text_length=0),
            ),
            "N3_BAD_TEXT_LENGTH",
        ),
        (
            _row(
                message="VoxCPM design success (9.9s)",
                extra=_gen_extra(duration=5.0),
            ),
            "N8_DURATION_MISMATCH",
        ),
        (
            _row(
                message="VoxCPM design failed (1.0s)",
                extra=_gen_extra(duration=1.0, error=""),
            ),
            "N9_STATUS_ERROR_MISMATCH",
        ),
        (
            _row(
                message="VoxCPM design success (0.0s)",
                extra=_gen_extra(duration=0.0),
            ),
            "N10_ZERO_DURATION_SUCCESS",
        ),
        (_row(action="weird", message="?", extra={}), "N13_ACTION_OUT_OF_SCOPE"),
    ]
    for row, expected_rule in cases:
        decision = f6.classify(row)
        assert decision.decision == "needs_manual", (row.message, decision)
        assert decision.rule_id == expected_rule


def test_model_boundaries() -> None:
    assert f6.classify(_row(action="model", message="voxcpm2 加载完成", extra={})).decision == "keep"
    assert f6.classify(_row(action="model", message="已切换到 indextts20", extra={})).decision == "keep"
    assert f6.classify(_row(action="model", message=f6.MODEL_UNLOAD_MESSAGE, extra={})).decision == "keep"

    unknown_message = f6.classify(_row(action="model", message="引擎状态未知", extra={}))
    assert unknown_message.decision == "needs_manual"
    assert unknown_message.rule_id == "N12_MODEL_MESSAGE_UNKNOWN"

    non_empty_extra = f6.classify(_row(action="model", message="voxcpm2 加载完成", extra={"x": 1}))
    assert non_empty_extra.rule_id == "N11_MODEL_EXTRA_NOT_EMPTY"

    unknown_engine = f6.classify(_row(action="model", message="已切换到 test_engine", extra={}))
    assert unknown_engine.rule_id == "N5_UNKNOWN_ENGINE"


def test_mojibake_and_drill_window() -> None:
    mojibake = "Æ½¾²×ÔÈ»µÄÄêÇáÅ®Éù"
    assert f6._repair_mojibake(mojibake) == "平静自然的年轻女声"
    decision = f6.classify(_row(message="VoxCPM design success (5.0s)", extra=_gen_extra(voice_persona=mojibake)))
    assert decision.decision == "needs_manual"
    assert decision.rule_id == "N14_MOJIBAKE_PERSONA"

    drill_ts = int(datetime(2026, 11, 1, 10, 0, tzinfo=f6.CST).timestamp() * 1000)
    drill = f6.classify(_row(ts_ms=drill_ts, message="VoxCPM design success (5.0s)", extra=_gen_extra()))
    assert drill.decision == "needs_manual"
    assert drill.rule_id == "N15_DRILL_WINDOW"

    model_drill = f6.classify(
        _row(
            ts_ms=int(datetime(2026, 11, 1, 17, 30, tzinfo=f6.CST).timestamp() * 1000),
            action="model",
            message="voxcpm2 加载完成",
            extra={},
        )
    )
    assert model_drill.rule_id == "N15_DRILL_WINDOW"


def test_production_engine_registry_matches_config() -> None:
    text = (PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")
    for engine in f6.PRODUCTION_ENGINES:
        assert f'name: "{engine}"' in text, f"config.yaml 已不再声明引擎 {engine}，F6 分类表需同步"


# ---------------------------------------------------------------------------
# 端到端：文档口径 333 行
# ---------------------------------------------------------------------------


def _build_333(path: Path) -> Path:
    rows: list[tuple[str, str, dict]] = []
    base_ts = int(datetime(2026, 9, 1, 12, 0, tzinfo=f6.CST).timestamp() * 1000)

    def add(action: str, message: str, extra: dict, minutes: int) -> None:
        rows.append((action, message, extra))

    # 41 streaming（其中 39 success 0.0s + 2 failed 0.3s，均为 Streaming 端点）
    for index in range(41):
        message = "Streaming success (0.0s)" if index < 39 else "Streaming failed (0.3s)"
        add(
            "generation",
            message,
            {
                "endpoint": "Streaming",
                "engine": "voxcpm2",
                "voice_persona": "streaming",
                "text_length": 7,
                "duration": 0.0 if index < 39 else 0.3,
            },
            index,
        )
    # 3 真实 streaming
    for index in range(3):
        add(
            "generation",
            "Streaming success (3.3s)",
            {
                "endpoint": "Streaming",
                "engine": "voxcpm2",
                "voice_persona": "streaming",
                "text_length": 15,
                "duration": 3.3,
            },
            100 + index,
        )
    # 22 条 0.0s 且 persona 为空
    for index in range(22):
        add(
            "generation",
            "VoxCPM design failed (0.0s)",
            {
                "endpoint": "VoxCPM design",
                "engine": "voxcpm2",
                "voice_persona": "",
                "text_length": 4,
                "duration": 0.0,
                "error": "音色描述不能为空，请输入有效的音色特征描述",
            },
            200 + index,
        )
    # 179 generation 未检（生产形态，keep）
    for index in range(179):
        add(
            "generation",
            "VoxCPM design success (5.0s)",
            _gen_extra(text_length=10 + index % 5),
            300 + index,
        )
    # 88 model 未检（keep）
    model_messages = ["voxcpm2 加载完成", "已切换到 indextts2", f6.MODEL_UNLOAD_MESSAGE]
    for index in range(88):
        add("model", model_messages[index % 3], {}, 600 + index)

    assert len(rows) == 333
    con = sqlite3.connect(path)
    try:
        con.execute(SCHEMA)
        for offset, (action, message, extra) in enumerate(rows):
            con.execute(
                "INSERT INTO action_logs (ts_ms, level, action, message, extra_json) VALUES (?,?,?,?,?)",
                (base_ts + offset * 60_000, "INFO", action, message, json.dumps(extra, ensure_ascii=False)),
            )
        con.commit()
    finally:
        con.close()
    return path


def test_end_to_end_counts_readonly_and_reproducible(tmp_path: Path) -> None:
    db = _build_333(tmp_path / "history.db")
    before_hash = _sha256(db)
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"

    assert f6.run(db, out1, expect_doc_counts=True) == 0
    assert f6.run(db, out2, expect_doc_counts=True) == 0

    summary = json.loads((out1 / "f6_summary.json").read_text(encoding="utf-8"))
    assert summary["total_rows"] == 333
    assert summary["buckets"] == f6.DOC_BUCKETS
    assert summary["bucket_diff"] == {key: 0 for key in f6.DOC_BUCKETS}
    assert summary["unchecked_rows"] == 267
    assert summary["decisions"] == {"keep": 267, "quarantine": 0, "needs_manual": 0}

    assert len((out1 / "action_logs_333.csv").read_text(encoding="utf-8-sig").splitlines()) == 334
    assert len((out1 / "f6_classification_267.csv").read_text(encoding="utf-8-sig").splitlines()) == 268

    # 可复算：两次输出逐字节一致
    for name in ("action_logs_333.csv", "f6_classification_267.csv", "f6_classification_267.md", "f6_summary.json"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes(), name

    # 零写入：库 sha256 不变、不产生 -wal/-shm
    assert _sha256(db) == before_hash
    assert not list(tmp_path.glob("history.db-*"))


def test_doc_count_mismatch_exit_codes(tmp_path: Path) -> None:
    db = _make_db(
        tmp_path / "one.db",
        [("generation", "VoxCPM design success (5.0s)", _gen_extra())],
    )
    assert f6.run(db, tmp_path / "o1", expect_doc_counts=True) == 1
    assert f6.run(db, tmp_path / "o2", expect_doc_counts=False) == 0


def test_non_empty_wal_is_refused(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "wal.db", [("generation", "VoxCPM design success (5.0s)", _gen_extra())])
    wal = db.with_name(db.name + "-wal")
    wal.write_bytes(b"not-empty")
    with pytest.raises(RuntimeError, match="非空 WAL"):
        f6.read_rows(db)
