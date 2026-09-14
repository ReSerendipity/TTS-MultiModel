#!/usr/bin/env python3
"""分支保护漂移检测 / 幂等应用（单一来源 docs/ci/branch-protection.json）。

用法：
    python scripts/apply_branch_protection.py            # 只读漂移检测
    python scripts/apply_branch_protection.py --apply     # 幂等写入远端
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(os.path.dirname(HERE), "ci", "branch-protection.json")


def gh(args: list[str]) -> tuple[int, str, str]:
    """调用 gh api，返回 (returncode, stdout, stderr)。"""
    r = subprocess.run(
        ["gh", "api", *args], capture_output=True, text=True, check=False
    )
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def desired_body(cfg: dict) -> dict:
    """由 cfg 组装 GitHub 分支保护更新体。"""
    p = cfg["policy"]
    return {
        "required_status_checks": {"strict": p["strict"], "contexts": cfg["contexts"]},
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": p["dismiss_stale_reviews"],
            "require_code_owner_reviews": p["require_code_owner_reviews"],
            "required_approving_review_count": p["required_approving_review_count"],
            "require_last_push_approval": p["require_last_push_approval"],
        },
        "required_signatures": p["required_signatures"],
        "enforce_admins": p["enforce_admins"],
        "required_linear_history": p["required_linear_history"],
        "allow_force_pushes": p["allow_force_pushes"],
        "allow_deletions": p["allow_deletions"],
        "block_creations": p["block_creations"],
        "required_conversation_resolution": cfg["required_conversation_resolution"],
        "lock_branch": p["lock_branch"],
        "allow_fork_syncing": p["allow_fork_syncing"],
        "restrictions": p["restrictions"],
    }


def compute_drift(cur: dict, want: dict, slug: str) -> list[str]:
    """比对当前保护与目标，返回漂移字段列表。"""
    d: list[str] = []
    pr = cur.get("required_pull_request_reviews") or {}
    want_pr = want["required_pull_request_reviews"]
    if (
        pr.get("required_approving_review_count")
        != want_pr["required_approving_review_count"]
    ):
        d.append("review")
    rsc = cur.get("required_status_checks") or {}
    if bool(rsc.get("strict")) != want["required_status_checks"]["strict"]:
        d.append("strict")
    if set(rsc.get("contexts") or []) != set(
        want["required_status_checks"]["contexts"]
    ):
        d.append("contexts")
    if bool((cur.get("enforce_admins") or {}).get("enabled")) != want["enforce_admins"]:
        d.append("enforce_admins")
    conv = (cur.get("required_conversation_resolution") or {}).get("enabled")
    if bool(conv) != want["required_conversation_resolution"]:
        d.append("conv_res")
    _, am, _ = gh(["repos/" + slug, "--jq", ".allow_auto_merge"])
    if (am == "true") != bool(want.get("_allow_auto_merge", True)):
        d.append("allow_auto_merge")
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description="分支保护漂移检测 / 幂等应用")
    ap.add_argument("--apply", action="store_true", help="写入远端（默认只读检测）")
    args = ap.parse_args()

    with open(CFG, encoding="utf-8") as f:
        cfg = json.load(f)
    slug = f"{cfg['owner']}/{cfg['repo']}"
    want = desired_body(cfg)
    want["_allow_auto_merge"] = bool(cfg["policy"]["allow_auto_merge"])

    code, out, err = gh(["repos/" + slug + "/branches/main/protection"])
    if code != 0:
        print("[FAIL] 读取保护失败:", err[:200])
        return 1
    drift = compute_drift(json.loads(out), want, slug)
    if not drift:
        print("[OK] 无漂移")
        return 0
    print("[DRIFT]", slug, "->", ", ".join(drift))
    if not args.apply:
        return 1

    path = os.path.join(HERE, "_body.json")
    body = {k: v for k, v in want.items() if not k.startswith("_")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False)
    c, _, e = gh(
        ["-X", "PUT", "repos/" + slug + "/branches/main/protection", "--input", path]
    )
    print("apply ->", "OK" if c == 0 else e[:200])
    if cfg["policy"]["allow_auto_merge"]:
        gh(["-X", "PATCH", "repos/" + slug, "-F", "allow_auto_merge=true"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
