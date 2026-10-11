#!/usr/bin/env python3
"""Thin wrapper -> shared family auditor. Blocks only on NEW hard findings.

The auditor lives OUTSIDE this repo (a sibling .spec_audit directory); where it is
found the check is authoritative, in a fresh CI checkout it is absent.

执行状态一律写在 stdout 的 SPEC_REFS 标记行上，两种状态互不冒充（CI 日志 grep
`SPEC_REFS STATE=` 就能读出本轮到底是「查过了」还是「根本没查」）：
  STATE=SKIPPED verdict=not-audited   审计器缺失，本轮未做任何审计
  STATE=CHECKED verdict=pass|fail     审计器真实跑过，同行计数是实测结果

退出码口径：0 = CHECKED 且无新增 HARD 项，或 SKIPPED；1 = 出现新增 HARD 项；
2 = 包装器自身跑不动（取不到稳定项目键 / 审计器没有可解析的 JSON 输出）。

为什么 SKIPPED 保持退出码 0、只在输出上分层：审计器是家族级工具、按设计落在仓库之外，
干净检出与 CI runner 上结构性地拿不到它；那里没人改过任何文档，据此返回非零码等于给无关
提交判假阳性，且与 docs/DOD.md / docs/release-governance.md 里「本脚本退出码 0」的门禁
口径互相矛盾。代价是「跳过」必须靠自身可读来兜底，所以它不再沿用旧文案里 "skipping
(CI green)" 这种读起来像通过的措辞，而是显式声明未审计并列出探测过的路径。

Hard categories = ASSERTIVE phantom refs, dead links, missing workflows/hooks/npm
scripts. A committed baseline (configs/spec_refs_baseline.json) grandfathers known
issues (e.g. a CHANGELOG entry citing an external path that legitimately lives in a
distributable, not this repo); only items ABSENT from the baseline fail. Baseline
keys are prefixed with a stable project id taken from pyproject.toml (see
`_project_key`), never the checkout directory name. Regenerate after a legitimate
resolve/accept: `python scripts/check_spec_refs.py --update-baseline`.
"""

from __future__ import annotations

import json
import re
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
    # 口径与理由见模块 docstring：默认不阻断 CI，但必须与「本机真跑过审计」在输出上分开。
    print("SPEC_REFS STATE=SKIPPED verdict=not-audited 家族审计器缺失，本轮未审计任何规范引用（不是「检查通过」）")
    for p in AUDITORS:
        print(f"  probed, not found: {p}", file=sys.stderr)
    sys.exit(0)

BASELINE = HERE / "configs" / "spec_refs_baseline.json"


def _name_via_tomllib(text):
    """3.11+ 走 tomllib；3.10 缺该模块或 TOML 解析异常时返回 None，交给手工扫描兜底。"""
    try:
        import tomllib
    except ModuleNotFoundError:
        return None
    try:
        return tomllib.loads(text)["project"]["name"]
    except Exception:  # TOMLDecodeError / KeyError / TypeError 等：一律退回手工扫描
        return None


def _scan_project_name(text):
    """兜底解析：只认 [project] 段内的第一个 name，避免误取其他段的同名字段。"""
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        header = re.fullmatch(r"\[([^\]]+)\]", stripped)
        if header:
            section = header.group(1)
        elif section == "project":
            match = re.match(r"""name\s*=\s*["']([^"']+)["']""", stripped)
            if match:
                return match.group(1)
    return None


def _project_key():
    """基线指纹的项目键：与检出目录名解耦，取 pyproject.toml 的 [project].name。

    原先直接用 HERE.name，于是工作副本改名（TTS_MultiModel -> TTS-MultiModel）就让
    整份基线一次性失配：同一条已接受项同时以 NEW 和 resolved 两副面孔出现，没人动文档
    门禁也变红。pyproject.toml 的项目名受 git 跟踪、已是本包的规范身份，重命名目录、
    zipball 检出、CI 换个 workspace 名都不受影响。
    """
    try:
        text = (HERE / "pyproject.toml").read_text(encoding="utf-8")
    except OSError as exc:
        print(f"SPEC_REFS STATE=ERROR 读不到 pyproject.toml：{exc}", file=sys.stderr)
        sys.exit(2)
    name = _name_via_tomllib(text) or _scan_project_name(text)
    if not name:
        print("SPEC_REFS STATE=ERROR pyproject.toml 缺少 [project].name，无法确定稳定项目键", file=sys.stderr)
        sys.exit(2)
    return name


PROJECT_KEY = _project_key()


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
    # --project 必须传目录名（审计器在自己的家族根目录下按名字定位仓库）；它回写的
    # project 字段在下方统一改写成 PROJECT_KEY，基线因此与检出目录名彻底解耦。
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
    try:
        results = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # 工具坏了（没落盘 / 落盘不是 JSON）不能与「查出新增 HARD 项」共用退出码 1。
        print(f"SPEC_REFS STATE=ERROR 审计器未产出可解析结果：{exc}", file=sys.stderr)
        sys.exit(2)
    for r in results:
        r["project"] = PROJECT_KEY
    return results


if "--update-baseline" in sys.argv:
    # 重新生成基线快照：把当前所有 HARD 阻塞项固化为“已接受”（合法解钉/消除误报后调用）。
    with tempfile.TemporaryDirectory(prefix="spec_audit_") as td:
        results = _run_auditor(Path(td) / "cur.json")
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=list) + "\n", encoding="utf-8")
    hard = _hard_fingerprint(results)
    print(f"SPEC_REFS STATE=CHECKED mode=update-baseline project={PROJECT_KEY} hard_total={len(hard)}")
    print(f"[baseline] 已写入 {BASELINE.relative_to(HERE)}（{len(hard)} 条 HARD 阻塞项转为已接受）")
    sys.exit(0)

with tempfile.TemporaryDirectory(prefix="spec_audit_") as td:
    results = _run_auditor(Path(td) / "cur.json")

cur_hard = _hard_fingerprint(results)
if BASELINE.is_file():
    try:
        base_results = json.loads(BASELINE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # 基线损坏也是工具故障，不能与「查出新增 HARD 项」共用退出码 1。
        print(f"SPEC_REFS STATE=ERROR 基线不可解析（{BASELINE.relative_to(HERE)}）：{exc}", file=sys.stderr)
        sys.exit(2)
    base_hard = _hard_fingerprint(base_results)
else:
    base_hard = set()
    print(
        "WARN: 基线缺失（configs/spec_refs_baseline.json）；fail-closed，全部 HARD 项视为新增。"
        "如需固化现状跑 `python scripts/check_spec_refs.py --update-baseline`。",
        file=sys.stderr,
    )

new = sorted(cur_hard - base_hard)
resolved = sorted(base_hard - cur_hard)
print(
    f"SPEC_REFS STATE=CHECKED verdict={'fail' if new else 'pass'} project={PROJECT_KEY} "
    f"hard_total={len(cur_hard)} baseline={len(base_hard)} new={len(new)} resolved={len(resolved)}"
)
for x in new:
    print(f"  NEW {x}")
for x in resolved:
    print(f"  ~ resolved since baseline (可 --update-baseline 固化): {x}")
sys.exit(1 if new else 0)
