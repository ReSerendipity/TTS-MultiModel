#!/usr/bin/env python3
"""Thin wrapper -> shared family auditor. Blocks only on NEW hard findings.

The auditor lives OUTSIDE this repo (a sibling .spec_audit directory); where it
is found the check is authoritative, and in a fresh CI checkout it is absent so
the check degrades to "skip" (keeps CI green).

Hard categories = ASSERTIVE phantom refs, dead links, missing workflows/hooks/npm
scripts. A committed baseline (configs/spec_refs_baseline.json) grandfathers known
issues (e.g. a CHANGELOG entry citing an external path that legitimately lives in a
distributable, not this repo); only items ABSENT from the baseline fail. Regenerate
after a legitimate resolve/accept: `python scripts/check_spec_refs.py --update-baseline`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
AUDITORS = [
    HERE / ".spec_audit" / "audit_spec_refs.py",
    HERE.parent / ".spec_audit" / "audit_spec_refs.py",
]
auditor = next((p for p in AUDITORS if p.is_file()), None)
if auditor is None:
    print("family auditor not found; skipping (CI green)", file=sys.stderr)
    sys.exit(0)

BASELINE = HERE / "configs" / "spec_refs_baseline.json"


def _hard_fingerprint(results):
    """Hard-category keys only (same format as the auditor's fingerprint), so the
    gate blocks on NEW blocking items and not on grandfathered / non-blocking ones."""
    out = set()
    for r in results:
        proj = r["project"]
        for x in r["findings"]:
            if x["status"] == "PHANTOM" and x["tier"] == "ASSERTIVE":
                out.add(f"{proj}|PHANTOM|{x['ref']}")
        for d in r["dead_links"]:
            out.add(f"{proj}|DEAD_LINK|{d['spec']}:{d['line']}|{d['link']}")
        pc = r.get("precommit", {})
        if pc.get("configured"):
            for h in pc["declared_not_configured"]:
                out.add(f"{proj}|BAD_HOOK|{h}")
        for w in r.get("workflows", {}).get("missing", []):
            out.add(f"{proj}|BAD_WORKFLOW|{w}")
        for s in r.get("npm_scripts", {}).get("missing", []):
            out.add(f"{proj}|BAD_SCRIPT|{s}")
    return out


def _run_auditor(json_path):
    # check=False：审计器对高置信幻影会 exit 3，但它会先写出 JSON（main 里
    # 先落盘再算退出码）；成败判定完全交给下面的 baseline 差集，不能让 CalledProcessError 抢先崩掉。
    subprocess.run(
        [
            sys.executable,
            str(auditor),
            "--project",
            HERE.name,
            "--json",
            str(json_path),
            "--md",
            str(Path(str(json_path) + ".md")),
        ],
        check=False,
        stdout=subprocess.DEVNULL,
    )
    return json.loads(Path(json_path).read_text(encoding="utf-8"))


if "--update-baseline" in sys.argv:
    # 重新生成基线快照：把当前所有 HARD 阻塞项固化为“已接受”（合法解钉/消除误报后调用）。
    with tempfile.TemporaryDirectory(prefix="spec_audit_") as td:
        results = _run_auditor(Path(td) / "cur.json")
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=list) + "\n", encoding="utf-8")
    print(
        f"[baseline] 已写入 {BASELINE.relative_to(HERE)}（{len(_hard_fingerprint(results))} 条 HARD 阻塞项转为已接受）"
    )
    sys.exit(0)

with tempfile.TemporaryDirectory(prefix="spec_audit_") as td:
    results = _run_auditor(Path(td) / "cur.json")

cur_hard = _hard_fingerprint(results)
if BASELINE.is_file():
    base_hard = _hard_fingerprint(json.loads(BASELINE.read_text(encoding="utf-8")))
else:
    base_hard = set()
    print(
        "WARN: 基线缺失（configs/spec_refs_baseline.json）；fail-closed，全部 HARD 项视为新增。"
        "如需固化现状跑 `python scripts/check_spec_refs.py --update-baseline`。",
        file=sys.stderr,
    )

new = sorted(cur_hard - base_hard)
resolved = sorted(base_hard - cur_hard)
print(f"hard_total={len(cur_hard)} baseline={len(base_hard)} new={len(new)} resolved={len(resolved)}")
for x in new:
    print(f"  NEW {x}")
for x in resolved:
    print(f"  ~ resolved since baseline (可 --update-baseline 固化): {x}")
sys.exit(1 if new else 0)
