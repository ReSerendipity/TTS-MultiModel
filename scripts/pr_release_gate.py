#!/usr/bin/env python3
"""PR 事件的 release-gate 状态判定（只判定，不回写）。

背景（issue #166 §闸门 A，2026-09-27 探针定性）：
- release-please 机器人用 `GITHUB_TOKEN` 身份开的 release PR **不级联触发任何 workflow**，
  它的 `release-gate` 状态只能由 push 作业回写（见 release-please.yml 的同名步骤）。
- 普通 PR（人类 / dependabot 等）会正常触发 `pull_request`，但 head 上没有 `release-gate`
  这个 context。一旦把 `release-gate` 提成分支保护的必需检查，普通 PR 会因「缺 context」
  永远无法合并。

本脚本给 release-please.yml 的 `pr-release-gate` job 用：对普通 PR 判 success（免发版判据，
发版判据本来就与普通改动无关）；对 release PR 判同 `scripts/check_release_readiness.py`
完全一致的结果（同一个脚本，禁止两套判据）。退出码恒为 0——**判定失败不等于 workflow
失败**（release PR 尚未补齐手工同步位是常态，不该染红每次 PR CI）；结论由调用方写成
commit status。真正的 readiness 失败文本走 stdout 的 JSON 第二行。

stdout 协议（两行，调用方按行读）：
    line1: state            —— success | failure
    line2: description      —— 写进 GitHub status 的一句话（JSON 字符串，含原始失败摘要）

用法：
    HEAD_REF=release-please--branches--main ROOT=/path python scripts/pr_release_gate.py
    HEAD_REF=feat/xxx python scripts/pr_release_gate.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# release-please-action 开 PR 时用的固定分支名
RELEASE_BRANCH = "release-please--branches--main"

OK_DESC = "非 release PR：免发版判据（发版门禁只在 release-please PR 上强制）"
RELEASE_FAIL_DESC = "发版条件未满足：见 check_release_readiness.py 输出（合下去会让 main 变红）"


def decide(head_ref: str, readiness_ok: bool) -> tuple[str, str]:
    """纯判定函数，单测只测它。

    readiness_ok 仅在 release 分支上有意义；非 release 分支不消费这个入参。
    """
    if head_ref == RELEASE_BRANCH:
        if readiness_ok:
            return "success", "发版条件满足：版本位一致、check_release_readiness 全过"
        return "failure", RELEASE_FAIL_DESC
    return "success", OK_DESC


def run_readiness(root: Path) -> tuple[bool, str]:
    """跑同源判据，返回 (是否通过, 输出原文)。判据脚本缺失按失败处理（fail closed）。"""
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "check_release_readiness.py"), "--root", str(root)],
        capture_output=True,
        text=True,
    )
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, output


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), type=Path)
    ap.add_argument("--head-ref", default=None, help="缺省读环境变量 HEAD_REF")
    args = ap.parse_args()

    import os

    head_ref = args.head_ref if args.head_ref is not None else os.environ.get("HEAD_REF", "")
    root = args.root.resolve()

    readiness_ok = True
    detail = ""
    if head_ref == RELEASE_BRANCH:
        readiness_ok, detail = run_readiness(root)

    state, description = decide(head_ref, readiness_ok)
    if state == "failure" and detail:
        # 把判据原文压进 description（单行、限长，GitHub description 上限 140 字符左右）
        first = next((ln.strip() for ln in detail.splitlines() if ln.strip()), "")
        description = f"{RELEASE_FAIL_DESC}｜{first[:100]}"

    print(state)
    print(json.dumps(description, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
