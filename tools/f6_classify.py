"""F6 主表逐行分类器：把 333 行待判定记录变成可逐行拍板的清单。

背景（docs/TODO.md F6）：
- 主表 ``action_logs`` 现余 333 行，文档口径 = 41 streaming + 3 真实 streaming
  + 22 条「0.0s 且 persona 为空」+ 179 generation 未检 + 88 model 未检。
- 可证测试签名（``test_*`` 引擎、``ep`` 端点、``text_length=8192``、桩错误串等）
  在 A/C 两批已隔离；本工具只对**剩余 267 条未检行**（179 generation + 88 model）
  逐行给出 ``keep / quarantine / needs_manual``，并把 333 行必要字段一并导出。

判定原则（与 F6 文档一致）：
1. ``quarantine`` 只给**已钉死在测试代码上的签名**，不做「看起来像测试」的推断。
2. ``needs_manual`` 给字段不自洽、未知引擎/端点、乱码 persona、落在文档化验证窗口
   等「无法自动拍板」的行。
3. ``keep`` 给生产引擎、字段自洽、无任何已钉死签名、不在验证窗口的行。

只读约束：
- 默认用 ``mode=ro&immutable=1`` 打开 SQLite，不建 WAL/SHM、不写库；
  若检测到非空 ``-wal``，会拒绝使用 immutable（避免读不到未 checkpoint 的数据），
  并提示改用 ``--allow-wal``（此时可能生成旁文件，属已知副作用）。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 文档口径（docs/TODO.md F6 阶段 12 收口行）。分类前后都要对平这五个桶。
DOC_BUCKETS: dict[str, int] = {
    "streaming_41": 41,
    "streaming_real": 3,
    "zero_fail_22": 22,
    "generation_unchecked": 179,
    "model_unchecked": 88,
}
TOTAL_ROWS = 333
UNCHECKED_ROWS = 267

CST = timezone(timedelta(hours=8))

#: 生产引擎注册表（与 config.yaml ``engines`` 对齐：voxcpm2 / indextts2 /
#: indextts20 / edge-tts / breeze-tts2）。test_ 前缀一律视为测试桩。
PRODUCTION_ENGINES = {"voxcpm2", "indextts2", "indextts20", "edge-tts", "breeze-tts2"}

#: 端点 → 允许引擎。用于抓「端点与引擎不匹配」的数据自洽问题。
ENDPOINT_ENGINES: dict[str, set[str]] = {
    "IndexTTS2": {"indextts2"},
    "VoxCPM design": {"voxcpm2"},
    "VoxCPM clone": {"voxcpm2"},
    "VoxCPM ultimate clone": {"voxcpm2"},
    "VoxCPM script": {"voxcpm2"},
    "VoxCPM prompt continue": {"voxcpm2"},
    "Breeze design": {"breeze-tts2"},
    "Streaming": {"voxcpm2"},
}

#: 文档化的非用户验证窗口。窗口内记录不是测试套件签名可钉死的行，但也不是
#: 正常用户流量，交人工拍板（keep=当运维留痕 / quarantine=当噪声清理）。
#:
#: - 09:50–10:24：`_archive/执行记录/2026-11/2026-11-01-o3-failover-drill.md:120`
#:   原文「执行窗口 09:50–10:24（+0800），≤40 分钟预算内完成」。
#: - 17:25–17:35：`docs/TODO.md` G1「阶段 6 GPU 真机实测」的 load→合成→卸载→重载→
#:   再合成序列；`generation_history` id 851/852 的 `created_at` 为 17:32:52 / 17:33:41，
#:   与 action_logs 2462–2466 同秒配对，时长 8.1s / 4.1s 与 TODO 的 8.39s / 4.18s 同量级。
DRILL_WINDOWS: tuple[tuple[str, str, str], ...] = (
    (
        "2026-11-01 09:50:00",
        "2026-11-01 10:24:59",
        "failover drill 执行窗口（_archive/执行记录/2026-11/2026-11-01-o3-failover-drill.md:120）",
    ),
    (
        "2026-11-01 17:25:00",
        "2026-11-01 17:35:59",
        "阶段 6 GPU 真机验收序列（docs/TODO.md G1；generation_history 851/852 同秒配对）",
    ),
)

#: generation 行的必要字段。缺任何一个都不能自动 keep。
REQUIRED_GEN_FIELDS = ("endpoint", "engine", "voice_persona", "text_length", "duration")

#: 已钉死的测试签名（A/C 批口径）。
TEST_OPERATION_MESSAGE = "测试操作"
STUB_ERROR_FRAGMENT = "'object' object has no attribute 'generate'"
C_BATCH_TEXT_LENGTH = 8192

#: F6 文档中 generation 消息形如 "IndexTTS2 success (7.8s)"。
MSG_DURATION_RE = re.compile(r"\(([0-9]+(?:\.[0-9]+)?)s\)")
MODEL_LOAD_RE = re.compile(r"^(.+) 加载完成$")
MODEL_SWITCH_RE = re.compile(r"^已切换到 (.+)$")
MODEL_UNLOAD_MESSAGE = "已卸载当前引擎，显存已释放"

#: duration 与消息里一位小数的容差（四舍五入误差）。
DURATION_TOLERANCE = 0.15

#: Windows 控制台/CI 都可能不是 UTF-8；输出文件统一显式编码，不靠系统默认。
CSV_ENCODING = "utf-8-sig"
MD_ENCODING = "utf-8"


@dataclass(frozen=True)
class LogRow:
    """``action_logs`` 一行 + 解析结果。"""

    id: int
    ts_ms: int
    level: str
    action: str
    message: str
    extra_raw: str
    extra: dict[str, Any]
    extra_error: str | None

    @property
    def iso_time(self) -> str:
        return datetime.fromtimestamp(self.ts_ms / 1000, tz=CST).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def endpoint(self) -> str:
        value = self.extra.get("endpoint")
        return str(value) if value is not None else ""

    @property
    def engine(self) -> str:
        value = self.extra.get("engine")
        return str(value) if value is not None else ""

    @property
    def duration(self) -> float | None:
        value = self.extra.get("duration")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    @property
    def text_length(self) -> int | None:
        value = self.extra.get("text_length")
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    @property
    def voice_persona(self) -> str:
        value = self.extra.get("voice_persona")
        return str(value) if value is not None else ""

    @property
    def error(self) -> str:
        value = self.extra.get("error")
        return str(value) if value is not None else ""


@dataclass(frozen=True)
class Decision:
    decision: str
    rule_id: str
    reason: str
    evidence: str


def _load_extra(raw: str) -> tuple[dict[str, Any], str | None]:
    """解析 ``extra_json``；失败时返回空 dict + 错误串（不猜内容）。"""
    text = (raw or "").strip()
    if not text:
        return {}, None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, f"JSONDecodeError: {exc}"
    if not isinstance(data, dict):
        return {}, f"顶层类型为 {type(data).__name__}，不是 object"
    return data, None


def read_rows(db_path: Path, allow_wal: bool = False) -> list[LogRow]:
    """只读读取 ``action_logs`` 全表，按 id 升序返回。

    ``allow_wal=False``（默认）时用 ``immutable=1``，不建旁文件；检测到非空 WAL
    直接拒绝。``allow_wal=True`` 改用普通 ``mode=ro``：能读到未 checkpoint 的数据，
    但可能产生 ``-wal/-shm`` 旁文件，属显式选择的副作用。
    """
    db_path = db_path.resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"数据库不存在：{db_path}")

    wal = db_path.with_name(db_path.name + "-wal")
    if wal.exists() and wal.stat().st_size > 0 and not allow_wal:
        raise RuntimeError(
            f"检测到非空 WAL（{wal}，{wal.stat().st_size} B）。"
            "immutable=1 会读不到未 checkpoint 的数据；如确认可接受旁文件副作用，"
            "请加 --allow-wal 以 mode=ro 打开。"
        )

    uri = f"file:{db_path.as_posix()}?mode=ro" if allow_wal else f"file:{db_path.as_posix()}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.execute("SELECT id, ts_ms, level, action, message, extra_json FROM action_logs ORDER BY id")
        rows = []
        for rid, ts_ms, level, action, message, extra_json in cur:
            extra, error = _load_extra(extra_json or "")
            rows.append(
                LogRow(
                    id=int(rid),
                    ts_ms=int(ts_ms),
                    level=str(level),
                    action=str(action),
                    message=str(message or ""),
                    extra_raw=str(extra_json or ""),
                    extra=extra,
                    extra_error=error,
                )
            )
        return rows
    finally:
        con.close()


def doc_bucket(row: LogRow) -> str:
    """按文档口径把行归桶；不在五桶内的返回 ``other``。"""
    if row.action == "model":
        return "model_unchecked"
    if row.action != "generation":
        return "other"
    if row.endpoint == "Streaming":
        return "streaming_real" if (row.duration or 0.0) > 1.0 else "streaming_41"
    if "(0.0s)" in row.message and not row.voice_persona:
        return "zero_fail_22"
    return "generation_unchecked"


def _drill_window(iso_time: str) -> tuple[str, str] | None:
    """命中文档化验证窗口时返回 (窗口, 依据)，否则 None。"""
    for start, end, label in DRILL_WINDOWS:
        if start <= iso_time <= end:
            return f"{start[11:16]}–{end[11:16]}", label
    return None


def _repair_mojibake(value: str) -> str | None:
    """Latin-1 误读 GBK 的典型乱码还原；还原不出中文就返回 None。

    例：``Æ½¾²×ÔÈ»µÄÄêÇáÅ®Éù`` → ``平静自然的年轻女声``（GBK 字节被按 Latin-1 解码）。
    """
    if not value:
        return None
    try:
        fixed = value.encode("latin-1").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    if fixed == value:
        return None
    if not any("\u4e00" <= ch <= "\u9fff" for ch in fixed):
        return None
    return fixed


def _model_template_engine(message: str) -> str | None:
    """model 生命周期消息 → 引擎名；不是三种既定模板就返回 None。"""
    text = message.strip()
    if text == MODEL_UNLOAD_MESSAGE:
        return ""
    match = MODEL_LOAD_RE.match(text) or MODEL_SWITCH_RE.match(text)
    if match:
        return match.group(1).strip()
    return None


def _fmt_duration(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}s"


def classify(row: LogRow) -> Decision:
    """单行判定。规则按「硬签名 → 数据不自洽 → 人工 → keep」顺序短路。"""
    extra = row.extra
    # ---- 1) 已钉死的测试签名：只做 quarantine，不做推断 -------------------
    if row.action == "test_operation" or row.message.strip() == TEST_OPERATION_MESSAGE:
        return Decision(
            "quarantine",
            "Q1_TEST_OPERATION",
            "命中 A 批可证签名：action=test_operation / message=测试操作",
            f"message={row.message!r}",
        )
    if row.action == "unknown" and row.extra_raw.strip() in ("", "{}"):
        return Decision(
            "quarantine",
            "Q2_UNKNOWN_EMPTY",
            "命中 A 批可证签名：action=unknown 且 extra_json 为空（unknown 空签名双写）",
            "action=unknown，extra_json 空",
        )
    if row.action == "generation":
        engine = row.engine
        if engine.startswith("test_") or row.endpoint == "ep":
            return Decision(
                "quarantine",
                "Q3_TEST_ENGINE",
                "命中已钉死签名：test_ 引擎或 ep 端点（1476 行可证桩引擎同族）",
                f"engine={engine!r} endpoint={row.endpoint!r}",
            )
        if row.text_length == C_BATCH_TEXT_LENGTH and "failed" in row.message:
            return Decision(
                "quarantine",
                "Q4_TEXT_LENGTH_8192",
                "命中 C 批唯一钉死判据：text_length=8192 且 failed",
                f"text_length={row.text_length} message={row.message!r}",
            )
        if STUB_ERROR_FRAGMENT in row.error:
            return Decision(
                "quarantine",
                "Q5_STUB_ERROR",
                "命中 A 批桩错误指纹：'object' object has no attribute 'generate'",
                f"error={row.error[:120]!r}",
            )

    # ---- 2) 字段/数据不自洽：无法自动拍板 --------------------------------
    if row.extra_error:
        return Decision(
            "needs_manual",
            "N1_BAD_EXTRA_JSON",
            f"extra_json 无法解析为 object：{row.extra_error}",
            f"extra_json={row.extra_raw[:160]!r}",
        )

    if row.action == "generation":
        missing = [key for key in REQUIRED_GEN_FIELDS if key not in extra]
        if missing:
            return Decision(
                "needs_manual",
                "N2_MISSING_FIELD",
                "generation 行缺少必要字段：" + ", ".join(missing),
                f"extra keys={sorted(extra)}",
            )
        if not row.endpoint or not row.engine:
            return Decision(
                "needs_manual",
                "N2_MISSING_FIELD",
                "generation 行的 endpoint/engine 为空值",
                f"endpoint={row.endpoint!r} engine={row.engine!r}",
            )
        if row.text_length is None or row.text_length <= 0:
            return Decision(
                "needs_manual",
                "N3_BAD_TEXT_LENGTH",
                f"text_length 不是正整数：{extra.get('text_length')!r}",
                f"text_length={extra.get('text_length')!r}",
            )
        if row.duration is None or row.duration < 0:
            return Decision(
                "needs_manual",
                "N4_BAD_DURATION",
                f"duration 不是非负数：{extra.get('duration')!r}",
                f"duration={extra.get('duration')!r}",
            )
        if row.engine not in PRODUCTION_ENGINES:
            return Decision(
                "needs_manual",
                "N5_UNKNOWN_ENGINE",
                f"engine 不在 config.yaml 注册表：{row.engine!r}",
                f"engine={row.engine!r} 已知={sorted(PRODUCTION_ENGINES)}",
            )
        allowed = ENDPOINT_ENGINES.get(row.endpoint)
        if allowed is None or row.engine not in allowed:
            return Decision(
                "needs_manual",
                "N6_ENDPOINT_ENGINE_MISMATCH",
                f"端点 {row.endpoint!r} 与引擎 {row.engine!r} 不匹配",
                f"允许引擎={sorted(allowed) if allowed else '未登记端点'}",
            )
        if not row.message.startswith(row.endpoint + " "):
            return Decision(
                "needs_manual",
                "N7_MESSAGE_ENDPOINT_MISMATCH",
                "message 前缀与 endpoint 不一致",
                f"message={row.message[:80]!r} endpoint={row.endpoint!r}",
            )
        match = MSG_DURATION_RE.search(row.message)
        if match and row.duration is not None:
            stated = float(match.group(1))
            if abs(stated - row.duration) > DURATION_TOLERANCE:
                return Decision(
                    "needs_manual",
                    "N8_DURATION_MISMATCH",
                    f"消息自述 {stated}s 与 duration={row.duration:.2f}s 不一致",
                    f"message={row.message!r}",
                )
        is_failed = "failed" in row.message.lower()
        is_success = "success" in row.message.lower()
        if is_failed and not row.error:
            return Decision("needs_manual", "N9_STATUS_ERROR_MISMATCH", "failed 行没有 error 字段", "error 为空")
        if is_success and row.error:
            return Decision(
                "needs_manual", "N9_STATUS_ERROR_MISMATCH", "success 行却带 error 字段", f"error={row.error[:120]!r}"
            )
        if is_success and (row.duration or 0.0) <= 0.05:
            return Decision(
                "needs_manual",
                "N10_ZERO_DURATION_SUCCESS",
                "success 但 duration≈0，可能不是真实推理",
                f"duration={row.duration!r}",
            )
    elif row.action == "model":
        if extra:
            return Decision(
                "needs_manual",
                "N11_MODEL_EXTRA_NOT_EMPTY",
                "model 动作的 extra_json 不是既定空对象",
                f"extra={json.dumps(extra, ensure_ascii=False)[:160]}",
            )
        engine = _model_template_engine(row.message)
        if engine is None:
            return Decision(
                "needs_manual",
                "N12_MODEL_MESSAGE_UNKNOWN",
                "model 消息不匹配三种既定生命周期模板",
                f"message={row.message!r}",
            )
        if engine and engine not in PRODUCTION_ENGINES:
            return Decision(
                "needs_manual",
                "N5_UNKNOWN_ENGINE",
                f"model 消息中的引擎不在注册表：{engine!r}",
                f"message={row.message!r}",
            )
    else:
        return Decision(
            "needs_manual",
            "N13_ACTION_OUT_OF_SCOPE",
            f"action={row.action!r} 不在 F6 五桶口径内",
            f"action={row.action!r}",
        )

    # ---- 3) 乱码 persona（典型非 UTF-8 客户端/脚本痕迹） ------------------
    if row.action == "generation":
        repaired = _repair_mojibake(row.voice_persona)
        if repaired is not None:
            window = _drill_window(row.iso_time)
            extra_note = f"；且落在 {window[0]} 验证窗口（{window[1]}）" if window else ""
            return Decision(
                "needs_manual",
                "N14_MOJIBAKE_PERSONA",
                f"persona 是 Latin-1/GBK 乱码，可还原为「{repaired}」；非正常 UTF-8 客户端{extra_note}",
                f"persona={row.voice_persona!r} → {repaired!r}",
            )

    # ---- 4) 文档化验证窗口 ----------------------------------------------
    window = _drill_window(row.iso_time)
    if window:
        return Decision(
            "needs_manual",
            "N15_DRILL_WINDOW",
            f"落在文档化验证窗口 {window[0]}：{window[1]}",
            f"time={row.iso_time} CST",
        )

    # ---- 5) keep：生产形态、字段自洽、无签名、不在验证窗口 ----------------
    if row.action == "generation":
        return Decision(
            "keep",
            "K1_GENERATION_PROD",
            "生产引擎/端点、字段自洽、无已钉死测试签名、不在验证窗口",
            (
                f"engine={row.engine} endpoint={row.endpoint} "
                f"text_length={row.text_length} duration={_fmt_duration(row.duration)} "
                f"message={row.message!r}"
            ),
        )
    engine = _model_template_engine(row.message) or ""
    return Decision(
        "keep",
        "K2_MODEL_LIFECYCLE",
        "model 生命周期既定模板、引擎在注册表、extra_json 空、不在验证窗口",
        f"engine={engine or '（卸载，无引擎名）'} message={row.message!r}",
    )


def history_matches(rows: list[LogRow], db_path: Path) -> dict[int, str]:
    """可选证据：给 generation 行配 ``generation_history`` 同秒记录。

    旧记录的 ``created_timestamp`` 是 1970 起的相对秒，匹配不上时**不伪造**，
    只是不提供该条旁证。返回 ``{action_log_id: 证据串}``。
    """
    db_path = db_path.resolve()
    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error:
        return {}
    try:
        hist = list(
            con.execute(
                "SELECT id, created_timestamp, file_size_bytes, is_success, engine "
                "FROM generation_history ORDER BY created_timestamp"
            )
        )
    except sqlite3.Error:
        return {}
    finally:
        con.close()

    result: dict[int, str] = {}
    for row in rows:
        if row.action != "generation":
            continue
        seconds = row.ts_ms / 1000.0
        nearest = min(hist, key=lambda item: abs(float(item[1]) - seconds), default=None)
        if nearest is None:
            continue
        delta = abs(float(nearest[1]) - seconds)
        if delta > 6.0:
            continue
        result[row.id] = (
            f"generation_history#{nearest[0]} 同秒记录（Δ={delta:.2f}s）"
            f" is_success={nearest[3]} engine={nearest[4]} size={nearest[2]}"
        )
    return result


def summarize(rows: list[LogRow]) -> dict[str, Any]:
    """统计五桶、判定分布、规则分布，并给出与文档口径的差异。"""
    buckets = {key: 0 for key in DOC_BUCKETS}
    decisions = {"keep": 0, "quarantine": 0, "needs_manual": 0}
    rules: dict[str, int] = {}
    manual_reasons: dict[str, int] = {}

    for row in rows:
        bucket = doc_bucket(row)
        if bucket in buckets:
            buckets[bucket] += 1
        if bucket not in ("generation_unchecked", "model_unchecked"):
            continue
        decision = classify(row)
        decisions[decision.decision] += 1
        rules[decision.rule_id] = rules.get(decision.rule_id, 0) + 1
        if decision.decision == "needs_manual":
            manual_reasons[decision.reason] = manual_reasons.get(decision.reason, 0) + 1

    diff = {key: buckets[key] - expected for key, expected in DOC_BUCKETS.items()}
    return {
        "total_rows": len(rows),
        "buckets": buckets,
        "doc_buckets": dict(DOC_BUCKETS),
        "bucket_diff": diff,
        "unchecked_rows": sum(buckets[key] for key in ("generation_unchecked", "model_unchecked")),
        "decisions": decisions,
        "rules": dict(sorted(rules.items())),
        "manual_reasons": dict(sorted(manual_reasons.items())),
    }


def _write_csv(path: Path, fieldnames: list[str], records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=CSV_ENCODING, newline="", errors="strict") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow({key: record.get(key, "") for key in fieldnames})


def _export_record(row: LogRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "ts_ms": row.ts_ms,
        "time_cst": row.iso_time,
        "level": row.level,
        "action": row.action,
        "message": row.message,
        "extra_json": row.extra_raw,
        "endpoint": row.endpoint,
        "engine": row.engine,
        "text_length": row.text_length if row.text_length is not None else "",
        "duration": row.duration if row.duration is not None else "",
        "voice_persona": row.voice_persona,
        "error": row.error,
        "doc_bucket": doc_bucket(row),
        "in_unchecked_scope": "Y" if doc_bucket(row) in ("generation_unchecked", "model_unchecked") else "N",
        "extra_parse_error": row.extra_error or "",
    }


def _decision_record(row: LogRow, decision: Decision, history_note: str) -> dict[str, Any]:
    evidence = decision.evidence
    if history_note:
        evidence = f"{evidence}；{history_note}"
    return {
        "id": row.id,
        "ts_ms": row.ts_ms,
        "time_cst": row.iso_time,
        "action": row.action,
        "level": row.level,
        "message": row.message,
        "endpoint": row.endpoint,
        "engine": row.engine,
        "text_length": row.text_length if row.text_length is not None else "",
        "duration": row.duration if row.duration is not None else "",
        "voice_persona": row.voice_persona,
        "error": row.error,
        "decision": decision.decision,
        "rule_id": decision.rule_id,
        "reason": decision.reason,
        "evidence": evidence,
    }


def _write_markdown(path: Path, summary: dict[str, Any], records: list[dict[str, Any]], db_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manual = [record for record in records if record["decision"] == "needs_manual"]
    quarantined = [record for record in records if record["decision"] == "quarantine"]
    kept = [record for record in records if record["decision"] == "keep"]

    lines = [
        "# F6 主表 267 条未检行分类清单",
        "",
        f"- 数据源：`{db_path}`（只读 `mode=ro&immutable=1`，零写入）",
        f"- 全表行数：{summary['total_rows']}；未检范围：{summary['unchecked_rows']}（179 generation + 88 model）",
        "- 判定口径：`quarantine` 只给已钉死测试签名；`needs_manual` 给不自洽/未知/乱码/验证窗口；`keep` 给生产形态且无签名。",
        "",
        "## 汇总",
        "",
        "| 判定 | 行数 |",
        "|------|------|",
    ]
    for key in ("keep", "quarantine", "needs_manual"):
        lines.append(f"| {key} | {summary['decisions'][key]} |")
    lines += ["", "| 规则 | 行数 |", "|------|------|"]
    for key, value in summary["rules"].items():
        lines.append(f"| {key} | {value} |")
    lines += ["", "| 文档桶 | 实测 | 文档 | 差异 |", "|--------|------|------|------|"]
    for key, expected in DOC_BUCKETS.items():
        actual = summary["buckets"][key]
        lines.append(f"| {key} | {actual} | {expected} | {actual - expected:+d} |")

    def table(title: str, rows: list[dict[str, Any]], with_reason: bool) -> None:
        lines.extend(["", f"## {title}（{len(rows)}）", ""])
        if not rows:
            lines.append("（无）")
            return
        if with_reason:
            lines.append("| id | 时间(CST) | action | 端点/引擎 | 关键字段 | 规则 | 依据 |")
            lines.append("|----|-----------|--------|-----------|----------|------|------|")
            for record in rows:
                key_fields = (
                    f"tl={record['text_length'] or '—'} dur={record['duration'] or '—'} "
                    f"persona={str(record['voice_persona'])[:24]!r}"
                    if record["action"] == "generation"
                    else f"message={str(record['message'])[:32]!r}"
                )
                lines.append(
                    f"| {record['id']} | {record['time_cst']} | {record['action']} | "
                    f"{record['endpoint'] or '—'} / {record['engine'] or '—'} | {key_fields} | "
                    f"{record['rule_id']} | {record['reason']}；{record['evidence']} |"
                )
        else:
            lines.append("| id | 时间(CST) | action | 端点/引擎 | 关键字段 | 规则 | 依据 |")
            lines.append("|----|-----------|--------|-----------|----------|------|------|")
            for record in rows:
                key_fields = (
                    f"tl={record['text_length']} dur={record['duration']} "
                    if record["action"] == "generation"
                    else str(record["message"])[:32]
                )
                lines.append(
                    f"| {record['id']} | {record['time_cst']} | {record['action']} | "
                    f"{record['endpoint'] or '—'} / {record['engine'] or '—'} | {key_fields} | "
                    f"{record['rule_id']} | {record['evidence']} |"
                )

    table("需要人工判定", manual, True)
    table("quarantine（可证测试签名）", quarantined, True)
    table("keep（保留）", kept, False)
    path.write_text("\n".join(lines) + "\n", encoding=MD_ENCODING)


def run(db_path: Path, out_dir: Path, expect_doc_counts: bool = True, allow_wal: bool = False) -> int:
    rows = read_rows(db_path, allow_wal=allow_wal)
    summary = summarize(rows)
    matches = history_matches(
        [row for row in rows if doc_bucket(row) in ("generation_unchecked", "model_unchecked")], db_path
    )

    export_records = [_export_record(row) for row in rows]
    decision_records = []
    for row in rows:
        if doc_bucket(row) not in ("generation_unchecked", "model_unchecked"):
            continue
        decision_records.append(_decision_record(row, classify(row), matches.get(row.id, "")))

    export_fields = [
        "id",
        "ts_ms",
        "time_cst",
        "level",
        "action",
        "message",
        "extra_json",
        "endpoint",
        "engine",
        "text_length",
        "duration",
        "voice_persona",
        "error",
        "doc_bucket",
        "in_unchecked_scope",
        "extra_parse_error",
    ]
    decision_fields = [
        "id",
        "ts_ms",
        "time_cst",
        "action",
        "level",
        "message",
        "endpoint",
        "engine",
        "text_length",
        "duration",
        "voice_persona",
        "error",
        "decision",
        "rule_id",
        "reason",
        "evidence",
    ]

    _write_csv(out_dir / "action_logs_333.csv", export_fields, export_records)
    _write_csv(out_dir / "f6_classification_267.csv", decision_fields, decision_records)
    _write_markdown(out_dir / "f6_classification_267.md", summary, decision_records, db_path)
    (out_dir / "f6_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding=MD_ENCODING
    )

    print(f"[f6] rows={summary['total_rows']} unchecked={summary['unchecked_rows']}")
    print("[f6] decisions: " + " ".join(f"{key}={value}" for key, value in summary["decisions"].items()))
    print("[f6] buckets: " + " ".join(f"{key}={value}" for key, value in summary["buckets"].items()))
    diff = {key: value for key, value in summary["bucket_diff"].items() if value}
    if diff:
        print(f"[f6] DOC-COUNT DIFF: {diff}", file=sys.stderr)
        if expect_doc_counts:
            print("[f6] 与文档口径不一致（--no-expect-doc-counts 可只看不判）", file=sys.stderr)
            return 1
    if summary["total_rows"] != TOTAL_ROWS and expect_doc_counts:
        print(f"[f6] 全表 {summary['total_rows']} != 文档 {TOTAL_ROWS}", file=sys.stderr)
        return 1
    if summary["unchecked_rows"] != UNCHECKED_ROWS and expect_doc_counts:
        print(f"[f6] 未检范围 {summary['unchecked_rows']} != 文档 {UNCHECKED_ROWS}", file=sys.stderr)
        return 1
    print(f"[f6] artifacts -> {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F6 主表 333 行逐行分类（只读）")
    parser.add_argument("--db", type=Path, default=REPO_ROOT / "data" / "history.db")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--expect-doc-counts",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="与 docs/TODO.md F6 的 41/3/22/179/88 对平；不一致 exit 1（默认开）",
    )
    parser.add_argument(
        "--allow-wal",
        action="store_true",
        help="存在非空 WAL 时改用 mode=ro（可能产生 -wal/-shm 旁文件；默认拒绝）",
    )
    args = parser.parse_args(argv)
    try:
        return run(args.db, args.out_dir, expect_doc_counts=args.expect_doc_counts, allow_wal=args.allow_wal)
    except (FileNotFoundError, RuntimeError, sqlite3.Error) as exc:
        print(f"[f6] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
