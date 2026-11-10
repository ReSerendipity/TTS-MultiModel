"""F6 隔离预案测试：分类边界 / dry-run 零写入 / SQL 模板可回滚 / 幂等。

测试全部跑 ``tmp_path`` 下的合成库，**不碰生产** ``data/history.db``：
- 分类边界：作用域只取对应判定，``keep`` 永不进入待隔离集；
- dry-run 零写入：跑前跑后库 sha256 逐字节一致、无 ``-wal/-shm`` 旁文件，
  并用「故意改一行」的对照证明这条 sha 断言有分辨力；
- SQL 可回滚：把生成的前向 SQL 打到**副本**上，再打回滚 SQL，核对主表行数、
  隔离表批次、六列内容指纹三方复原；并验证两个方向都可重放（不产生重复行）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE action_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms INTEGER NOT NULL,
    level TEXT NOT NULL,
    action TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    extra_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE action_logs_quarantine_20261102 (
    id INTEGER,
    ts_ms INTEGER NOT NULL,
    level TEXT NOT NULL,
    action TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    extra_json TEXT NOT NULL DEFAULT '{}',
    quarantine_reason TEXT NOT NULL,
    quarantine_batch TEXT NOT NULL
)
"""

MOJIBAKE = "Æ½¾²×ÔÈ»µÄÄêÇáÅ®Éù"


def _load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


f6 = _load_module("f6_classify_for_plan", "tools/f6_classify.py")
plan_mod = _load_module("f6_quarantine_plan_under_test", "tools/f6_quarantine_plan.py")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_db(path: Path, rows: list[tuple[str, str, dict, int]], *, with_quarantine: bool = True) -> Path:
    """按 ``(action, message, extra, ts_ms)`` 造库；行 id 从 1 递增。"""
    schema = SCHEMA
    if not with_quarantine:
        schema = SCHEMA.split("CREATE TABLE action_logs_quarantine_20261102")[0]
    con = sqlite3.connect(path)
    try:
        con.executescript(schema)
        for action, message, extra, ts_ms in rows:
            con.execute(
                "INSERT INTO action_logs (ts_ms, level, action, message, extra_json) VALUES (?,?,?,?,?)",
                (ts_ms, "INFO", action, message, json.dumps(extra, ensure_ascii=False)),
            )
        con.commit()
    finally:
        con.close()
    return path


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


def _ts(month: int, day: int, hour: int, minute: int, second: int = 0) -> int:
    return int(datetime(2026, month, day, hour, minute, second, tzinfo=f6.CST).timestamp() * 1000)


def _mixed_db(path: Path) -> Path:
    """三行：1 条已钉死签名（quarantine）+ 1 条乱码 persona（needs_manual）+ 1 条 keep。"""
    return _make_db(
        path,
        [
            (
                "generation",
                "ep failed (0.3s)",
                _gen_extra(endpoint="ep", engine="test_stub", duration=0.3, error="x"),
                _ts(9, 1, 12, 0),
            ),
            ("generation", "VoxCPM design success (5.0s)", _gen_extra(voice_persona=MOJIBAKE), _ts(10, 20, 12, 30)),
            ("model", "voxcpm2 加载完成", {}, _ts(10, 20, 13, 0)),
        ],
    )


def _rows(path: Path) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return list(con.execute("SELECT id, ts_ms, level, action, message, extra_json FROM action_logs ORDER BY id"))
    finally:
        con.close()


def _batches(path: Path) -> dict[str, int]:
    con = sqlite3.connect(path)
    try:
        return {
            str(batch): int(count)
            for batch, count in con.execute(
                "SELECT quarantine_batch, COUNT(*) FROM action_logs_quarantine_20261102 GROUP BY quarantine_batch"
            )
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 分类边界
# ---------------------------------------------------------------------------


def test_scope_boundaries_split_strict_and_candidate(tmp_path: Path) -> None:
    db = _mixed_db(tmp_path / "mixed.db")

    strict, rc = plan_mod.build_plan(db, scope="quarantine", expect_doc_counts=False)
    assert rc == 0
    assert strict["counts"]["quarantine_rows"] == 1
    assert strict["counts"]["needs_manual_rows"] == 1
    assert strict["counts"]["keep_rows"] == 1
    assert [row["rule_id"] for row in strict["rows"]] == ["Q3_TEST_ENGINE"]
    # keep 与 needs_manual 都不进严格作用域
    assert strict["rows"][0]["decision"] == "quarantine"
    assert [row["id"] for row in strict["candidates"]] == [2]

    candidate, rc2 = plan_mod.build_plan(db, scope="needs-manual", expect_doc_counts=False)
    assert rc2 == 0
    assert [row["rule_id"] for row in candidate["rows"]] == ["N14_MOJIBAKE_PERSONA"]
    assert candidate["approval_required"] is True
    # 候选集在严格作用域下只是「列出来」，不产生可执行 SQL
    assert "INSERT INTO action_logs_quarantine_20261102" in strict["sql"]["forward"]
    assert strict["counts"]["selected_rows"] == 1


def test_keep_rows_never_enter_plan(tmp_path: Path) -> None:
    db = _make_db(
        tmp_path / "keep_only.db",
        [
            ("model", "voxcpm2 加载完成", {}, _ts(10, 20, 13, 0)),
            ("generation", "VoxCPM design success (5.0s)", _gen_extra(), _ts(10, 20, 14, 0)),
        ],
    )
    plan, rc = plan_mod.build_plan(db, scope="quarantine", expect_doc_counts=False)
    assert rc == 0
    assert plan["counts"]["keep_rows"] == 2
    assert plan["rows"] == []
    assert plan["candidates"] == []
    assert "本文件不含任何 DML" in plan["sql"]["forward"]


# ---------------------------------------------------------------------------
# dry-run 零写入
# ---------------------------------------------------------------------------


def test_dry_run_leaves_db_byte_identical(tmp_path: Path, capsys) -> None:
    db = _mixed_db(tmp_path / "mixed.db")
    before = _sha256(db)
    out = tmp_path / "out"

    assert plan_mod.run(db, scope="quarantine", out_dir=out, expect_doc_counts=False) == 0

    assert _sha256(db) == before
    assert not list(tmp_path.glob("mixed.db-*"))
    assert "guards:" in capsys.readouterr().out
    for name in (
        "f6_quarantine_plan.md",
        "f6_quarantine_plan.json",
        "f6_quarantine_rows.csv",
        "f6_quarantine_forward.sql",
        "f6_quarantine_rollback.sql",
    ):
        assert (out / name).stat().st_size > 0, name
    assert json.loads((out / "f6_quarantine_plan.json").read_text(encoding="utf-8"))["mode"].startswith("dry-run")


def test_sha_assertion_has_resolution(tmp_path: Path) -> None:
    """负控：同样的 sha 断言遇到真写入必须能分辨出来，否则「零写入」是空话。"""
    db = _mixed_db(tmp_path / "mixed.db")
    before = _sha256(db)
    con = sqlite3.connect(db)
    try:
        con.execute("UPDATE action_logs SET message = message || '（改动）' WHERE id = 3")
        con.commit()
    finally:
        con.close()
    assert _sha256(db) != before


# ---------------------------------------------------------------------------
# SQL 模板：可回滚 + 可重放（只在副本上执行）
# ---------------------------------------------------------------------------


def test_forward_then_rollback_restores_copy(tmp_path: Path) -> None:
    source = _mixed_db(tmp_path / "seed.db")
    work = tmp_path / "work.db"
    shutil.copy2(source, work)
    before_rows = _rows(work)
    before_sha = _sha256(work)

    plan, rc = plan_mod.build_plan(work, scope="quarantine", expect_doc_counts=False)
    assert rc == 0
    assert "INSERT INTO action_logs_quarantine_20261102" in plan["sql"]["forward"]
    assert "DELETE FROM action_logs" in plan["sql"]["forward"]
    # 回滚模板必须含反向迁移语句（写回主表 + 清本批次）
    rollback = plan["sql"]["rollback"]
    assert "INSERT INTO action_logs" in rollback
    assert "DELETE FROM action_logs_quarantine_20261102" in rollback
    assert plan["batch_id"] in rollback

    con = sqlite3.connect(work)
    try:
        con.executescript(plan["sql"]["forward"])
    finally:
        con.close()
    assert _rows(work) == before_rows[1:]  # 只剩 keep + needs_manual 两行
    assert _batches(work) == {plan["batch_id"]: 1}
    assert _sha256(work) != before_sha  # 迁移确实改了库（不是空跑）

    con = sqlite3.connect(work)
    try:
        con.executescript(plan["sql"]["rollback"])
    finally:
        con.close()
    assert _rows(work) == before_rows  # 六列内容指纹与隔离前逐行一致
    assert _batches(work) == {}


def test_both_directions_are_replay_safe(tmp_path: Path) -> None:
    source = _mixed_db(tmp_path / "seed.db")
    work = tmp_path / "work.db"
    shutil.copy2(source, work)
    plan, rc = plan_mod.build_plan(work, scope="quarantine", expect_doc_counts=False)
    assert rc == 0

    con = sqlite3.connect(work)
    try:
        con.executescript(plan["sql"]["forward"])
        con.executescript(plan["sql"]["forward"])  # 重放：NOT EXISTS 挡住重复入隔离表
    finally:
        con.close()
    assert _batches(work) == {plan["batch_id"]: 1}
    assert _rows(work)[0][0] == 2

    con = sqlite3.connect(work)
    try:
        con.executescript(plan["sql"]["rollback"])
        con.executescript(plan["sql"]["rollback"])  # 重放：已在主表的行跳过
    finally:
        con.close()
    ids = [row[0] for row in _rows(work)]
    assert ids == [1, 2, 3]
    assert _batches(work) == {}


def test_candidate_scope_sql_carries_row_reason(tmp_path: Path) -> None:
    db = _mixed_db(tmp_path / "mixed.db")
    plan, rc = plan_mod.build_plan(db, scope="needs-manual", expect_doc_counts=False)
    assert rc == 0
    forward = plan["sql"]["forward"]
    assert "N14_MOJIBAKE_PERSONA" in forward
    assert "WHERE id = 2" in forward
    assert "WHERE id = 1" not in forward  # 严格集的行不混进候选批


# ---------------------------------------------------------------------------
# 自检：不合格就不出 SQL
# ---------------------------------------------------------------------------


def test_replayed_batch_is_hard_stop(tmp_path: Path) -> None:
    db = _mixed_db(tmp_path / "mixed.db")
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO action_logs_quarantine_20261102 "
            "(id, ts_ms, level, action, message, extra_json, quarantine_reason, quarantine_batch) "
            "VALUES (1, 0, 'INFO', 'generation', 'x', '{}', 'seed', ?)",
            (plan_mod.DEFAULT_BATCH_ID,),
        )
        con.commit()
    finally:
        con.close()

    plan, rc = plan_mod.build_plan(db, scope="quarantine", expect_doc_counts=False)
    assert rc == 1
    assert plan["sql"] == {"forward": "", "rollback": ""}
    failed = [item["name"] for item in plan["guards"] if not item["ok"]]
    assert "batch_not_replayed" in failed


def test_emit_sql_prints_nothing_when_guard_fails(tmp_path: Path, capsys) -> None:
    db = _mixed_db(tmp_path / "mixed.db")
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO action_logs_quarantine_20261102 "
            "(id, ts_ms, level, action, message, extra_json, quarantine_reason, quarantine_batch) "
            "VALUES (1, 0, 'INFO', 'generation', 'x', '{}', 'seed', ?)",
            (plan_mod.DEFAULT_BATCH_ID,),
        )
        con.commit()
    finally:
        con.close()

    assert plan_mod.run(db, scope="quarantine", expect_doc_counts=False, emit="forward") == 1
    result = capsys.readouterr()
    assert result.out == ""
    assert "拒绝输出 SQL" in result.err


def test_emit_sql_does_not_write_artifacts(tmp_path: Path, capsys) -> None:
    db = _mixed_db(tmp_path / "mixed.db")
    before = _sha256(db)
    assert plan_mod.run(db, scope="quarantine", expect_doc_counts=False, emit="both") == 0
    result = capsys.readouterr()
    assert result.out.count("BEGIN IMMEDIATE") == 2
    assert "回滚（反向迁移）" in result.out
    assert _sha256(db) == before
    assert list(tmp_path.iterdir()) == [db]


def test_missing_quarantine_table_is_env_error(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "noq.db", [("model", "voxcpm2 加载完成", {}, _ts(10, 20, 13, 0))], with_quarantine=False)
    assert plan_mod.main(["--db", str(db), "--no-expect-doc-counts"]) == 2


# ---------------------------------------------------------------------------
# 幂等 & 清单交叉核对
# ---------------------------------------------------------------------------


def test_output_is_byte_identical_across_runs(tmp_path: Path) -> None:
    db = _mixed_db(tmp_path / "mixed.db")
    out1, out2 = tmp_path / "o1", tmp_path / "o2"
    assert plan_mod.run(db, scope="quarantine", out_dir=out1, expect_doc_counts=False) == 0
    assert plan_mod.run(db, scope="quarantine", out_dir=out2, expect_doc_counts=False) == 0
    for name in (
        "f6_quarantine_plan.md",
        "f6_quarantine_plan.json",
        "f6_quarantine_rows.csv",
        "f6_quarantine_forward.sql",
        "f6_quarantine_rollback.sql",
    ):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes(), name


def test_classification_csv_cross_check(tmp_path: Path, capsys) -> None:
    db = _mixed_db(tmp_path / "mixed.db")
    csv_path = tmp_path / "classification.csv"
    live = {1: ("quarantine", "Q3_TEST_ENGINE"), 2: ("needs_manual", "N14_MOJIBAKE_PERSONA")}
    lines = ["id,decision,rule_id"]
    lines += [f"{rid},{decision},{rule}" for rid, (decision, rule) in live.items()]
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert plan_mod.run(db, scope="quarantine", expect_doc_counts=False, classification_path=csv_path) == 0

    capsys.readouterr()
    csv_path.write_text("\n".join(lines[:1] + ["2,keep,UNKNOWN"]) + "\n", encoding="utf-8")
    assert plan_mod.run(db, scope="quarantine", expect_doc_counts=False, classification_path=csv_path) == 1
    assert "classification_csv_matches_live" in capsys.readouterr().err
