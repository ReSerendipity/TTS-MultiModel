"""F6 隔离执行预案生成器（只读）：把「待判定」变成「可审阅、可回滚」的文本预案。

背景（``docs/TODO.md`` F6）：
- A/C 两批已把 2,167 + 206 行可证测试噪声移入 ``action_logs_quarantine_20261102``；
  主表余 333 行，B19 已逐行分类为 ``keep 252 / quarantine 0 / needs_manual 15``。
- 也就是说**严格口径下没有待隔离行**：可证签名（``test_*`` 引擎、``ep`` 端点、
  ``text_length=8192``、桩错误串等）已经清零。真正可能需要动的是 15 条
  ``needs_manual``（乱码 persona 6 + 文档化验证窗口 9），是否当噪声清理由所有者拍板。
- 本工具只**产预案**：不备份、不隔离、不改库。``--emit-sql`` 也只把 SQL 打到 stdout。

为什么要单独做预案而不是直接写执行脚本：
- 隔离是「同事务迁移 + 删除」，清单、口径、删除范围任何一处漂了都是不可逆误删。
- 预案把「动哪些行、为什么动、写进哪张表的哪些列、删哪一边、怎么回滚、备份叫什么」
  全部落成可审阅文本，并把机器可判的自检（行存在性、批次未重放、隔离表结构、
  WAL 旁文件、库 sha256 前后一致）内建进来：**自检不过就 exit 1 且不输出 SQL**。
  宁可不出预案，也不出半截预案。

幂等：输出不含「当前时间」，备份名由源库 mtime 派生，重复运行逐字节一致。

用法::

    python tools/f6_quarantine_plan.py --out-dir <dir>            # dry-run（默认）
    python tools/f6_quarantine_plan.py --scope needs-manual --out-dir <dir>
    python tools/f6_quarantine_plan.py --emit-sql                 # 只打印前向 SQL
    python tools/f6_quarantine_plan.py --emit-sql both            # 前向 + 回滚
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

SOURCE_TABLE = "action_logs"
QUARANTINE_TABLE = "action_logs_quarantine_20261102"

#: 迁移时原样搬过去的六列（顺序即 SELECT 顺序）。
COPY_COLUMNS: tuple[str, ...] = ("id", "ts_ms", "level", "action", "message", "extra_json")

#: 隔离表比主表多出的两列（回滚时不能写回主表）。
AUDIT_COLUMNS: tuple[str, ...] = ("quarantine_reason", "quarantine_batch")

TARGET_COLUMNS: tuple[str, ...] = COPY_COLUMNS + AUDIT_COLUMNS

#: 批次名沿用既有形态 ``F6-<YYYYMMDD>-<字母>``：A/B/C 已用完，本预案预置 D。
#: 刻意做成常量 + ``--batch-id`` 覆盖，而不是取「今天」——否则同一份库重复出预案
#: 会因为跑在别的一天而漂移，幂等就断了。
DEFAULT_BATCH_ID = "F6-20261109-D"

#: 预案日期（工单 WO-B28 的日期）。只用于文档标注，不参与任何库操作。
PLAN_DATE = "2026-11-09"

CST = timezone(timedelta(hours=8))

MD_ENCODING = "utf-8"
CSV_ENCODING = "utf-8-sig"

#: ``quarantine_reason`` 是 NOT NULL 文本列，单行理由按此上限截断（确定性截断）。
REASON_MAX_CHARS = 180

SCOPES = ("quarantine", "needs-manual")


@dataclass(frozen=True)
class PlanRow:
    """预案里的一行：分类结果 + 迁移所需的字段证据。"""

    id: int
    ts_ms: int
    time_cst: str
    level: str
    action: str
    message: str
    engine: str
    endpoint: str
    decision: str
    rule_id: str
    reason: str
    evidence: str

    @property
    def approval_required(self) -> bool:
        return self.decision != "quarantine"


def _load_classifier():
    """按文件路径加载 ``tools/f6_classify.py``，复用同一份分类口径。

    不复制判定规则：分类只有一处定义，预案与 B19 清单必须同源，否则「清单」
    和「预案」会各自漂。
    """
    path = (Path(__file__).resolve().parent / "f6_classify.py").resolve()
    cached = sys.modules.get("f6_classify")
    if cached is not None and Path(cached.__file__).resolve() == path:
        return cached
    spec = importlib.util.spec_from_file_location("f6_classify", path)
    if spec is None or spec.loader is None:  # pragma: no cover - 文件在位时不会发生
        raise RuntimeError(f"无法加载分类器：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["f6_classify"] = module
    spec.loader.exec_module(module)
    return module


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rel(path: Path) -> str:
    """仓内路径显示为相对路径，仓外路径原样；只影响可读性，不影响判定。"""
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _sql_literal(text: str) -> str:
    """SQL 单引号字符串字面量；内部单引号按 SQL 规则成对转义。"""
    return "'" + str(text).replace("'", "''") + "'"


def read_quarantine_state(db_path: Path) -> dict[str, Any]:
    """只读读取隔离表的结构与批次分布（不改库、不建旁文件）。"""
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if QUARANTINE_TABLE not in tables:
            raise RuntimeError(f"隔离表 {QUARANTINE_TABLE} 不在库中，预案无处落地")
        columns = [row[1] for row in con.execute(f"PRAGMA table_info({QUARANTINE_TABLE})")]
        missing = [name for name in TARGET_COLUMNS if name not in columns]
        if missing:
            raise RuntimeError(f"隔离表 {QUARANTINE_TABLE} 缺列：{missing}（现列={columns}）")
        batches = {
            str(batch): int(count)
            for batch, count in con.execute(
                f"SELECT quarantine_batch, COUNT(*) FROM {QUARANTINE_TABLE} "
                "GROUP BY quarantine_batch ORDER BY quarantine_batch"
            )
        }
        ids = {int(row[0]) for row in con.execute(f"SELECT id FROM {QUARANTINE_TABLE} WHERE id IS NOT NULL")}
        journal_mode = str(con.execute("PRAGMA journal_mode").fetchone()[0])
        total = int(con.execute(f"SELECT COUNT(*) FROM {QUARANTINE_TABLE}").fetchone()[0])
        return {
            "table": QUARANTINE_TABLE,
            "columns": columns,
            "batches": batches,
            "total_rows": total,
            "ids": ids,
            "journal_mode": journal_mode,
        }
    finally:
        con.close()


def _side_files(db_path: Path) -> list[dict[str, Any]]:
    """报出 -wal/-shm 旁文件的真实尺寸（空 WAL 不算隐患，非空 WAL 会拒绝 immutable）。"""
    result = []
    for suffix in ("-wal", "-shm"):
        path = db_path.with_name(db_path.name + suffix)
        result.append({"name": suffix, "exists": path.exists(), "size": path.stat().st_size if path.exists() else 0})
    return result


def _reason_for_quarantine(row: PlanRow) -> str:
    """隔离表 ``quarantine_reason`` 的单行文本：规则 + 判定理由（确定性截断）。"""
    text = f"{row.rule_id}: {row.reason}"
    if len(text) > REASON_MAX_CHARS:
        text = text[: REASON_MAX_CHARS - 1] + "…"
    return text


def _backup_plan(db_path: Path, db_meta: dict[str, Any], backup_dir: Path) -> dict[str, Any]:
    """备份方案：名字由源库 mtime 派生 → 与执行时刻无关，重复出预案不漂移。"""
    mtime_cst = datetime.fromtimestamp(db_path.stat().st_mtime, tz=CST).strftime("%Y%m%d_%H%M%S")
    name = f"history_backup_{mtime_cst}_pre_f6.db"
    target = backup_dir / name
    return {
        "dir": _rel(backup_dir),
        "name": name,
        "path": _rel(target),
        "exists": target.exists(),
        "name_derivation": (
            f"源库 mtime(CST) {mtime_cst} + 固定后缀 _pre_f6；不含执行时刻，同一份库重复出预案得到同一个名字"
        ),
        "collision_policy": "同名已存在时停下改后缀（如 _pre_f6-2），不得覆盖任何既有备份",
        "why_not_backup_script": (
            "`scripts/backup_history_db.py` 不接受自定义文件名（只有 --db-path/--backup-dir/"
            "--keep/--compress/--restore/--target/--drill/--full/--coverage/--verify），"
            "要落到固定可审阅的名字只能用整文件复制；本库 `journal_mode` 实测为 "
            f"`{db_meta['journal_mode']}`（非 WAL），服务停止时整文件复制即一致快照"
        ),
        "command": (f"Copy-Item -LiteralPath 'data/history.db' -Destination '{_rel(target)}' -ErrorAction Stop"),
        "verify": (
            "备份后立即比对 size 与 sha256 与源库一致（本预案 db.sha256 即基线）；"
            "再在仓外副本上 restore 回读 333/2,167/206 三数对平后才允许执行隔离"
        ),
    }


def _forward_sql(rows: list[PlanRow], batch_id: str, db_path: Path, db_sha: str, scope: str) -> str:
    batch = _sql_literal(batch_id)
    lines = [
        "-- F6 隔离预案 · 前向迁移（只读生成，未执行）",
        f"-- 作用域：{scope}；批次：{batch_id}；行数：{len(rows)}",
        f"-- 目标库：{_rel(db_path)}",
        f"-- 源库 sha256（执行前必须仍是此值）：{db_sha}",
        "-- 前置：服务已停；备份已完成并复算 sha256 一致；本批次在隔离表中不存在",
        "-- 口径：id 保持原值回填（便于回滚与原表对齐）；每行 INSERT 带 NOT EXISTS，重放不会产生重复行",
        "",
        "BEGIN IMMEDIATE;",
        "",
    ]
    if not rows:
        lines += [
            f"-- 0 行：作用域 {scope} 下没有待隔离行（可证签名已清零），本文件不含任何 DML",
            "",
            "PRAGMA integrity_check;",
            "",
            "COMMIT;",
        ]
        return "\n".join(lines) + "\n"
    for row in rows:
        reason = _sql_literal(_reason_for_quarantine(row))
        lines += [
            f"-- id={row.id} {row.time_cst} {row.action} {row.engine or '—'} {row.rule_id}",
            f"INSERT INTO {QUARANTINE_TABLE}",
            "    (id, ts_ms, level, action, message, extra_json, quarantine_reason, quarantine_batch)",
            "SELECT id, ts_ms, level, action, message, extra_json,",
            f"       {reason},",
            f"       {batch}",
            f"  FROM {SOURCE_TABLE}",
            f" WHERE id = {row.id}",
            f"   AND NOT EXISTS (SELECT 1 FROM {QUARANTINE_TABLE} q "
            f"WHERE q.quarantine_batch = {batch} AND q.id = {row.id});",
            "",
        ]
    lines += [
        "-- 只删「本批次确实进了隔离表」的 id：插入漏行时删不到，不会出现",
        "-- 「主表删了但隔离表没备份」这种不可回滚的状态",
        f"DELETE FROM {SOURCE_TABLE}",
        f" WHERE id IN (SELECT id FROM {QUARANTINE_TABLE} WHERE quarantine_batch = {batch});",
        "",
        "-- 收口自检：integrity_check 必须 ok；再核对两表行数",
        "PRAGMA integrity_check;",
        f"SELECT (SELECT COUNT(*) FROM {SOURCE_TABLE}) AS main_rows,",
        f"       (SELECT COUNT(*) FROM {QUARANTINE_TABLE} WHERE quarantine_batch = {batch}) AS batch_rows;",
        "",
        "COMMIT;",
    ]
    return "\n".join(lines) + "\n"


def _rollback_sql(rows: list[PlanRow], batch_id: str, db_path: Path, db_sha: str, scope: str) -> str:
    batch = _sql_literal(batch_id)
    lines = [
        "-- F6 隔离预案 · 回滚（反向迁移，只读生成、未执行）",
        f"-- 作用域：{scope}；批次：{batch_id}；行数：{len(rows)}",
        f"-- 目标库：{_rel(db_path)}",
        f"-- 隔离前源库 sha256：{db_sha}",
        "-- 判据：回滚后主表行数回到隔离前、隔离表本批次清零、六列内容指纹与隔离前一致",
        "-- 范围：只清本批次，不碰 F6-20261102-A / F6-20261103-C",
        "",
        "BEGIN IMMEDIATE;",
        "",
    ]
    if not rows:
        lines += [
            f"-- 0 行：作用域 {scope} 下没有待隔离行；回滚按「批次」作用域执行，"
            "该批次一旦执行过，本文件仍可把它整批退回",
            "",
        ]
    lines += [
        "-- 1) 反向迁移：按批次把六列原样写回主表（id 原值回填；已在主表的行跳过，重放安全）",
        f"INSERT INTO {SOURCE_TABLE}",
        "    (id, ts_ms, level, action, message, extra_json)",
        "SELECT q.id, q.ts_ms, q.level, q.action, q.message, q.extra_json",
        f"  FROM {QUARANTINE_TABLE} q",
        f" WHERE q.quarantine_batch = {batch}",
        f"   AND NOT EXISTS (SELECT 1 FROM {SOURCE_TABLE} a WHERE a.id = q.id);",
        "",
        "-- 2) 清零本批次（只删本批次，别的批次原样保留）",
        f"DELETE FROM {QUARANTINE_TABLE} WHERE quarantine_batch = {batch};",
        "",
        "-- 收口自检",
        "PRAGMA integrity_check;",
        f"SELECT (SELECT COUNT(*) FROM {SOURCE_TABLE}) AS main_rows,",
        f"       (SELECT COUNT(*) FROM {QUARANTINE_TABLE} WHERE quarantine_batch = {batch}) AS batch_rows;",
        "",
        "COMMIT;",
    ]
    return "\n".join(lines) + "\n"


def build_plan(
    db_path: Path,
    *,
    scope: str = "quarantine",
    batch_id: str = DEFAULT_BATCH_ID,
    backup_dir: Path | None = None,
    expect_doc_counts: bool = True,
    allow_wal: bool = False,
    classification_path: Path | None = None,
) -> tuple[dict[str, Any], int]:
    """产出预案 dict 与退出码。纯读：只打开 ``mode=ro&immutable=1``。"""
    classifier = _load_classifier()
    db_path = db_path.resolve()
    backup_dir = (backup_dir or (REPO_ROOT / "data" / "backups")).resolve()

    db_sha = sha256_file(db_path)
    db_stat = db_path.stat()
    rows = classifier.read_rows(db_path, allow_wal=allow_wal)
    summary = classifier.summarize(rows)

    quarantine_state = read_quarantine_state(db_path)
    db_meta = {
        "path": _rel(db_path),
        "size_bytes": db_stat.st_size,
        "sha256": db_sha,
        "mtime_cst": datetime.fromtimestamp(db_stat.st_mtime, tz=CST).strftime("%Y-%m-%d %H:%M:%S"),
        "journal_mode": quarantine_state["journal_mode"],
        "side_files": _side_files(db_path),
    }

    # ---- 分类结果（与 B19 同源：只统计 267 条未检范围） -------------------
    unchecked = [row for row in rows if classifier.doc_bucket(row) in ("generation_unchecked", "model_unchecked")]
    decisions: dict[int, Any] = {row.id: classifier.classify(row) for row in unchecked}
    plan_rows: dict[int, PlanRow] = {}
    for row in unchecked:
        decision = decisions[row.id]
        plan_rows[row.id] = PlanRow(
            id=row.id,
            ts_ms=row.ts_ms,
            time_cst=row.iso_time,
            level=row.level,
            action=row.action,
            message=row.message,
            engine=row.engine,
            endpoint=row.endpoint,
            decision=decision.decision,
            rule_id=decision.rule_id,
            reason=decision.reason,
            evidence=decision.evidence,
        )

    strict_ids = sorted(rid for rid, item in plan_rows.items() if item.decision == "quarantine")
    candidate_ids = sorted(rid for rid, item in plan_rows.items() if item.decision == "needs_manual")
    selected_ids = strict_ids if scope == "quarantine" else candidate_ids
    selected = [plan_rows[rid] for rid in selected_ids]

    guards: list[dict[str, Any]] = []

    def guard(name: str, ok: bool, detail: str) -> None:
        guards.append({"name": name, "ok": bool(ok), "detail": detail})

    main_ids = {row.id for row in rows}
    missing = [rid for rid in selected_ids if rid not in main_ids]
    guard(
        "selected_ids_in_main", not missing, f"作用域 {scope} 选中 {len(selected_ids)} 行，主表缺失 {missing or '无'}"
    )
    already = sorted(set(selected_ids) & quarantine_state["ids"])
    guard(
        "selected_ids_not_yet_quarantined",
        not already,
        f"与隔离表已有 id 交集：{already or '无'}（非空说明这批行已被隔离，预案过期）",
    )
    guard(
        "batch_not_replayed",
        batch_id not in quarantine_state["batches"],
        f"批次 {batch_id} 现有行数：{quarantine_state['batches'].get(batch_id, 0)}",
    )
    guard(
        "quarantine_table_shape",
        all(name in quarantine_state["columns"] for name in TARGET_COLUMNS),
        f"{QUARANTINE_TABLE} 列={quarantine_state['columns']}",
    )
    non_empty_wal = [item for item in db_meta["side_files"] if item["name"] == "-wal" and item["size"] > 0]
    guard("no_non_empty_wal", not non_empty_wal, f"非空 WAL：{non_empty_wal or '无'}")

    diff = {key: value for key, value in summary["bucket_diff"].items() if value}
    counts_ok = not diff and summary["total_rows"] == classifier.TOTAL_ROWS
    guard(
        "doc_counts_match",
        counts_ok or not expect_doc_counts,
        (
            f"rows={summary['total_rows']}/{classifier.TOTAL_ROWS}"
            + (f"，桶差异={diff}" if diff else "，五桶全对")
            + ("" if expect_doc_counts else "（--no-expect-doc-counts：只看不判）")
        ),
    )

    # ---- 分类清单交叉核对（可选）：清单与实时分类必须逐行同判定 --------------
    if classification_path is not None:
        mismatches, csv_ids = _cross_check_csv(classification_path, proposals=plan_rows)
        guard(
            "classification_csv_matches_live",
            not mismatches,
            f"{_rel(classification_path)}：{len(csv_ids)} 行参与核对，不一致 {mismatches or '无'}",
        )

    backup = _backup_plan(db_path, db_meta, backup_dir)
    forward = _forward_sql(selected, batch_id, db_path, db_sha, scope)
    rollback = _rollback_sql(selected, batch_id, db_path, db_sha, scope)

    plan: dict[str, Any] = {
        "plan_schema": "f6-quarantine-plan/1",
        "plan_date": PLAN_DATE,
        "generator": "tools/f6_quarantine_plan.py",
        "mode": "dry-run（只读；未备份、未隔离、未执行任何 SQL）",
        "scope": scope,
        "db": db_meta,
        "counts": {
            "main_rows": summary["total_rows"],
            "unchecked_rows": summary["unchecked_rows"],
            "quarantine_rows": len(strict_ids),
            "needs_manual_rows": len(candidate_ids),
            "keep_rows": summary["decisions"]["keep"],
            "selected_rows": len(selected_ids),
            "quarantine_table_rows": quarantine_state["total_rows"],
            "quarantine_table_batches": quarantine_state["batches"],
        },
        "doc_counts": {
            "measured": summary["buckets"],
            "documented": summary["doc_buckets"],
            "diff": summary["bucket_diff"],
        },
        "batch_id": batch_id,
        "target": {
            "table": QUARANTINE_TABLE,
            "columns": list(TARGET_COLUMNS),
            "column_mapping": {
                **{name: name for name in COPY_COLUMNS},
                "quarantine_reason": f"由分类 rule_id + 判定理由生成（单行上限 {REASON_MAX_CHARS} 字符）",
                "quarantine_batch": f"批次名 {batch_id}",
            },
            "delete_from": f"{SOURCE_TABLE} WHERE id IN (本批次已入隔离表的 id)",
        },
        "backup": backup,
        "rows": [row.__dict__ for row in selected],
        "candidates": [plan_rows[rid].__dict__ for rid in candidate_ids],
        "sql": {"forward": forward, "rollback": rollback},
        "guards": guards,
        "approval_required": scope == "needs-manual",
    }
    rc = 0 if all(item["ok"] for item in guards) else 1
    if rc:
        plan["mode"] = "dry-run（自检未通过：不输出 SQL）"
        plan["sql"] = {"forward": "", "rollback": ""}
    return plan, rc


def _cross_check_csv(path: Path, *, proposals: dict[int, PlanRow]) -> tuple[list[dict[str, Any]], list[int]]:
    """把外部分类清单（B19 的 267 CSV）与实时分类逐行对齐。

    只比 ``id`` 与 ``decision``/``rule_id``：清单是给人审的，格式细节不参与判定。
    """
    mismatches: list[dict[str, Any]] = []
    ids: list[int] = []
    with path.open("r", encoding=CSV_ENCODING, newline="", errors="strict") as handle:
        for record in csv.DictReader(handle):
            try:
                rid = int(record.get("id", ""))
            except (TypeError, ValueError):
                continue
            ids.append(rid)
            live = proposals.get(rid)
            if live is None:
                mismatches.append({"id": rid, "reason": "清单里的行不在实时未检范围"})
                continue
            if record.get("decision") != live.decision or record.get("rule_id") != live.rule_id:
                mismatches.append(
                    {
                        "id": rid,
                        "reason": (
                            f"清单 {record.get('decision')}/{record.get('rule_id')} "
                            f"≠ 实时 {live.decision}/{live.rule_id}"
                        ),
                    }
                )
    return mismatches, ids


def render_markdown(plan: dict[str, Any]) -> str:
    db = plan["db"]
    counts = plan["counts"]
    sql = plan["sql"]
    rows = plan["rows"]
    candidates = plan["candidates"]
    backup = plan["backup"]

    lines = [
        "# F6 隔离执行预案（只读生成，未执行）",
        "",
        f"- 生成器：`{plan['generator']}`（`--dry-run` 默认；本文件不含任何执行动作）",
        f"- 预案日期：{plan['plan_date']}；预案作用域：`{plan['scope']}`",
        f"- 数据源：`{db['path']}`",
        f"- 源库 size：{db['size_bytes']:,} B；sha256：`{db['sha256']}`；mtime(CST)：{db['mtime_cst']}；"
        f"journal_mode：`{db['journal_mode']}`",
        f"- 主表 `{SOURCE_TABLE}`：{counts['main_rows']} 行（未检范围 {counts['unchecked_rows']}）",
        f"- 隔离表 `{QUARANTINE_TABLE}`：{counts['quarantine_table_rows']:,} 行，"
        f"批次 {json.dumps(counts['quarantine_table_batches'], ensure_ascii=False)}",
        "",
        "## 1. 计数",
        "",
        "| 项 | 行数 | 是否进入本预案 |",
        "|----|------|----------------|",
        f"| keep（生产形态、无已钉死签名） | {counts['keep_rows']} | 否 |",
        f"| quarantine（已钉死测试签名） | {counts['quarantine_rows']} | "
        f"{'是' if plan['scope'] == 'quarantine' else '否'} |",
        f"| needs_manual（待所有者拍板） | {counts['needs_manual_rows']} | "
        f"{'是（需批准）' if plan['scope'] == 'needs-manual' else '否（见 §3）'} |",
        f"| **本预案作用域内待隔离** | **{counts['selected_rows']}** | — |",
        "",
    ]

    if plan["scope"] == "quarantine" and counts["quarantine_rows"] == 0:
        lines += [
            "> **严格口径 0 行**：可证测试签名（`test_*` 引擎 / `ep` 端点 / `text_length=8192` / 桩错误串）",
            "> 在 A/C 两批已隔离完毕，主表剩余 267 条未检行里没有一条能钉死在测试代码上。",
            "> 因此本预案在当前口径下**无可执行对象**；真正待决的是 §3 的 15 条 `needs_manual`。",
            "",
        ]

    lines += ["## 2. 作用域内待隔离行", ""]
    lines += _row_table(rows)

    lines += ["", "## 3. 待批准候选（needs_manual，不在本作用域内）", ""]
    candidate_rules: dict[str, int] = {}
    for row in candidates:
        candidate_rules[row["rule_id"]] = candidate_rules.get(row["rule_id"], 0) + 1
    note = f"（共 {len(candidates)} 行：{' + '.join(f'{k} {v}' for k, v in sorted(candidate_rules.items()))}）"
    lines += _row_table(candidates, note=note if candidates else "")

    lines += [
        "",
        "> 批不批由所有者定：`keep` = 当运维留痕原样保留；`quarantine` = 当验证噪声清理。",
        "> 批准后以 `--scope needs-manual` 重出预案与 SQL（本预案不预生成未批准的可执行 SQL）。",
        "",
        "## 4. 隔离目标表与字段",
        "",
        f"- 目标表：`{QUARANTINE_TABLE}`（既有隔离表，A/C 两批共用；本批按 `quarantine_batch` 隔离）",
        "- 字段映射：",
        "",
        "| 主表列 | 隔离表列 | 说明 |",
        "|--------|----------|------|",
    ]
    copy_notes = {
        "id": "原值迁移：不重排、不回填新 id（回滚按原 id 对齐；AUTOINCREMENT 序列不回退）",
        "ts_ms": "原值迁移（毫秒时间戳不变）",
        "level": "原值迁移",
        "action": "原值迁移",
        "message": "原值迁移（含原始错误串，逐字符不改写）",
        "extra_json": "原值迁移（JSON 文本原样，不重新序列化）",
    }
    for name in COPY_COLUMNS:
        lines.append(f"| `{name}` | `{name}` | {copy_notes[name]} |")
    lines.append(f"| — | `quarantine_reason` | {plan['target']['column_mapping']['quarantine_reason']} |")
    lines.append(f"| — | `quarantine_batch` | `{plan['batch_id']}` |")
    lines += [
        "",
        f"- 批次名：`{plan['batch_id']}`（沿用 `F6-<YYYYMMDD>-<字母>`，A/B/C 已用完）",
        f"- 删除范围：`{plan['target']['delete_from']}`",
        "- 单事务：`BEGIN IMMEDIATE` → 逐行 `INSERT…SELECT` → 按批次删主表 → `PRAGMA integrity_check` → `COMMIT`",
        "",
        "## 5. 备份",
        "",
        f"- 目录：`{backup['dir']}`",
        f"- 文件名：`{backup['name']}`（当前已存在：{'是' if backup['exists'] else '否'}）",
        f"- 命名依据：{backup['name_derivation']}",
        f"- 冲突处置：{backup['collision_policy']}",
        f"- 为什么不用备份脚本：{backup['why_not_backup_script']}",
        f"- 命令：`{backup['command']}`",
        f"- 校验：{backup['verify']}",
        "",
        "## 6. 前向 SQL（仅文本，本轮未执行）",
        "",
        "```sql",
        sql["forward"].rstrip("\n"),
        "```",
        "",
        "## 7. 回滚 SQL（反向迁移，仅文本）",
        "",
        "```sql",
        sql["rollback"].rstrip("\n"),
        "```",
        "",
        "## 8. 自检（机器判据）",
        "",
        "| 判据 | 结果 | 说明 |",
        "|------|------|------|",
    ]
    for item in plan["guards"]:
        lines.append(f"| `{item['name']}` | {'PASS' if item['ok'] else 'FAIL'} | {item['detail']} |")
    lines += [
        "",
        "## 9. 幂等与零写入",
        "",
        "- 输出不含「当前时间」；备份名由源库 mtime 派生 → 同一份库重复运行逐字节一致。",
        "- 读库走 `mode=ro&immutable=1`：不建 `-wal/-shm`，不改任何行；本预案未备份、未隔离。",
        "- 前向/回滚 SQL 均带 `NOT EXISTS` 守卫：重放不会产生重复行、也不会重复删除。",
        "",
        "## 10. 待批准",
        "",
        f"- 作用域 `{plan['scope']}` 下待隔离 **{counts['selected_rows']}** 行；"
        f"候选（needs_manual）**{counts['needs_manual_rows']}** 行需所有者批准后方可执行。",
        "- 执行前须完成：服务停止 → §5 备份 → 备份 sha256 复算 → 仓外 restore 回读对平 → 执行前向 SQL。",
        "- 回滚路径见 §7，实跑口径（在合成库上验证过）见 `tests/test_f6_quarantine_plan.py`。",
    ]
    return "\n".join(lines) + "\n"


def _row_table(rows: list[dict[str, Any]], *, note: str = "") -> list[str]:
    if not rows:
        return ["（无）"]
    header = ["| id | 时间(CST) | action | 端点/引擎 | 规则 | 判定依据 |"]
    header.append("|----|-----------|--------|-----------|------|----------|")
    body = []
    for row in rows:
        endpoint = row["endpoint"] or "—"
        engine = row["engine"] or "—"
        body.append(
            f"| {row['id']} | {row['time_cst']} | {row['action']} | {endpoint} / {engine} | "
            f"`{row['rule_id']}` | {row['reason']} |"
        )
    lines = header + body
    if note:
        lines.append("")
        lines.append(note)
    return lines


def write_artifacts(plan: dict[str, Any], out_dir: Path, *, emit_sql: bool = True) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "f6_quarantine_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding=MD_ENCODING
    )
    (out_dir / "f6_quarantine_plan.md").write_text(render_markdown(plan), encoding=MD_ENCODING)
    fields = [
        "id",
        "ts_ms",
        "time_cst",
        "level",
        "action",
        "message",
        "endpoint",
        "engine",
        "decision",
        "rule_id",
        "reason",
        "evidence",
    ]
    with (out_dir / "f6_quarantine_rows.csv").open("w", encoding=CSV_ENCODING, newline="", errors="strict") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in plan["rows"]:
            writer.writerow({key: row.get(key, "") for key in fields})
    if emit_sql:
        (out_dir / "f6_quarantine_forward.sql").write_text(plan["sql"]["forward"], encoding=MD_ENCODING)
        (out_dir / "f6_quarantine_rollback.sql").write_text(plan["sql"]["rollback"], encoding=MD_ENCODING)


def run(
    db_path: Path,
    *,
    scope: str = "quarantine",
    batch_id: str = DEFAULT_BATCH_ID,
    out_dir: Path | None = None,
    backup_dir: Path | None = None,
    expect_doc_counts: bool = True,
    allow_wal: bool = False,
    classification_path: Path | None = None,
    emit: str | None = None,
) -> int:
    """dry-run 主流程；``emit`` 非空时只打印 SQL，不落盘、不执行。"""
    plan, rc = build_plan(
        db_path,
        scope=scope,
        batch_id=batch_id,
        backup_dir=backup_dir,
        expect_doc_counts=expect_doc_counts,
        allow_wal=allow_wal,
        classification_path=classification_path,
    )

    # 本工具唯一可能的副作用是「读」，因此 sha 前后必须一致；不一致即判 FAIL。
    after_sha = sha256_file(Path(db_path).resolve())
    if after_sha != plan["db"]["sha256"]:
        plan["guards"].append(
            {"name": "db_sha_unchanged", "ok": False, "detail": f"读前 {plan['db']['sha256']} → 读后 {after_sha}"}
        )
        rc = 1
    else:
        plan["guards"].append({"name": "db_sha_unchanged", "ok": True, "detail": "读前读后 sha256 一致（零写入）"})

    if emit is not None:
        if rc != 0:
            print("[plan] 自检未通过，拒绝输出 SQL：", file=sys.stderr)
            for item in plan["guards"]:
                if not item["ok"]:
                    print(f"[plan]   FAIL {item['name']}: {item['detail']}", file=sys.stderr)
            return rc
        if emit == "forward":
            print(plan["sql"]["forward"], end="")
        elif emit == "rollback":
            print(plan["sql"]["rollback"], end="")
        else:
            print(plan["sql"]["forward"], end="")
            print("\n-- ================= 回滚（反向迁移）=================\n")
            print(plan["sql"]["rollback"], end="")
        return 0

    counts = plan["counts"]
    print(f"[plan] db={plan['db']['path']} rows={counts['main_rows']} unchecked={counts['unchecked_rows']}")
    print(
        f"[plan] scope={plan['scope']} selected={counts['selected_rows']} "
        f"quarantine={counts['quarantine_rows']} needs_manual={counts['needs_manual_rows']} "
        f"keep={counts['keep_rows']}"
    )
    print(f"[plan] batch={plan['batch_id']} target={plan['target']['table']}")
    print(f"[plan] backup={plan['backup']['path']} (exists={plan['backup']['exists']})")
    failed = [item for item in plan["guards"] if not item["ok"]]
    print(f"[plan] guards: {len(plan['guards']) - len(failed)} passed / {len(failed)} failed")
    for item in failed:
        print(f"[plan]   FAIL {item['name']}: {item['detail']}", file=sys.stderr)
    if out_dir is not None and rc == 0:
        write_artifacts(plan, out_dir)
        print(f"[plan] artifacts -> {out_dir}")
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F6 隔离执行预案（只读；不备份、不隔离、不执行 SQL）")
    parser.add_argument("--db", type=Path, default=REPO_ROOT / "data" / "history.db")
    parser.add_argument("--backup-dir", type=Path, default=REPO_ROOT / "data" / "backups")
    parser.add_argument("--out-dir", type=Path, default=None, help="预案产物目录（dry-run 才写；--emit-sql 不写）")
    parser.add_argument(
        "--scope",
        choices=SCOPES,
        default="quarantine",
        help="quarantine=只含已钉死签名（默认，当前 0 行）；needs-manual=待批准候选",
    )
    parser.add_argument("--batch-id", default=DEFAULT_BATCH_ID, help=f"批次名（默认 {DEFAULT_BATCH_ID}）")
    parser.add_argument(
        "--classification",
        type=Path,
        default=None,
        help="可选：B19 的分类清单 CSV，与实时分类逐行交叉核对（不一致即 FAIL）",
    )
    parser.add_argument(
        "--expect-doc-counts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="与 docs/TODO.md 的 333/41/3/22/179/88 对平（默认开）",
    )
    parser.add_argument(
        "--allow-wal",
        action="store_true",
        help="存在非空 WAL 时改用 mode=ro（可能产生旁文件，默认拒绝）",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="只产预案、不执行（默认）")
    mode.add_argument(
        "--emit-sql",
        nargs="?",
        const="forward",
        choices=("forward", "rollback", "both"),
        default=None,
        help="只把 SQL 打到 stdout（forward 默认 / rollback / both），绝不执行",
    )
    args = parser.parse_args(argv)

    if not args.batch_id or not all(ch.isalnum() or ch in "-._" for ch in args.batch_id):
        print(f"[plan] 非法批次名：{args.batch_id!r}", file=sys.stderr)
        return 2

    try:
        return run(
            args.db,
            scope=args.scope,
            batch_id=args.batch_id,
            out_dir=args.out_dir,
            backup_dir=args.backup_dir,
            expect_doc_counts=args.expect_doc_counts,
            allow_wal=args.allow_wal,
            classification_path=args.classification,
            emit=args.emit_sql,
        )
    except (FileNotFoundError, RuntimeError, sqlite3.Error) as exc:
        print(f"[plan] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
