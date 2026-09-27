#!/usr/bin/env python3
"""分支保护**声明镜像**的漂移检测 / 幂等应用（单一来源 docs/ci/branch-protection.json）。

口径：live 为准，JSON 是 live 的镜像 —— 不是愿望清单。
发现漂移时先确认哪个是你想要的，改 JSON 再回来跑本脚本；反过来用 `--apply`
把旧 JSON 压到 live 上，会静默改掉门禁（2026-09-27 实测过一次：JSON 少一条
`DCO Check`，`--apply` 会把这条必需检查摘掉）。

用法：
    python docs/ci/apply_branch_protection.py                     # 只读漂移检测
    python docs/ci/apply_branch_protection.py --apply             # 幂等写入远端
    python docs/ci/apply_branch_protection.py --apply \\
        --allow-drop-contexts "A|B"                               # 非交互环境里显式批准摘掉 A、B

退出码：0 = 无漂移；1 = 有漂移（或已执行 --apply）；2 = 判不了（保护或某项读不到）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(os.path.dirname(HERE), "ci", "branch-protection.json")

# 专用端点 404 的两种可能（审批要求未配置 / 读不到），都不能当“无漂移”。
REVIEWS_OK = "ok"
REVIEWS_MISSING = "not-configured-or-unreadable"
REVIEWS_ERROR = "read-error"


def gh(args: list[str]) -> tuple[int, str, str]:
    """调用 gh api，返回 (returncode, stdout, stderr)。"""
    r = subprocess.run(
        ["gh", "api", *args], capture_output=True, text=True, check=False, encoding="utf-8"
    )
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def fetch_reviews(slug: str) -> tuple[str, dict[str, Any] | None, str]:
    """单独取 required_pull_request_reviews。

    组合端点 `GET .../branches/{b}/protection` 的响应里**没有**这个键（2026-09-27 实测），
    从组合响应读它会恒得 `{}`，于是“审批数漂移”既永远被误报、也可能反过来漏报。
    """
    code, out, err = gh([f"repos/{slug}/branches/main/protection/required_pull_request_reviews"])
    if code == 0:
        return REVIEWS_OK, json.loads(out), ""
    if "404" in err:
        return REVIEWS_MISSING, None, err
    return REVIEWS_ERROR, None, err


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


def context_changes(live_ctx: list[str], want_ctx: list[str]) -> tuple[list[str], list[str]]:
    """返回 (将新增的 context, 将摘掉的 context)，都按名字排序，便于人工核对。"""
    live, want = set(live_ctx), set(want_ctx)
    return sorted(want - live), sorted(live - want)


def drops_confirmed(
    drop: list[str], allow_arg: str | None, isatty: bool, typed: str | None = None
) -> tuple[bool, str]:
    """摘掉必需检查必须逐字批准；新增/不变不需要额外批准。

    typed 传给定时任务/测试用；交互终端下从 stdin 读。非交互且没带 --allow-drop-contexts
    一律拒绝 —— 宁可让人重跑一次，也不要“顺手摘掉一条门禁”这种事静默发生。
    """
    if not drop:
        return True, "没有要摘掉的 context"
    expected = "|".join(drop)
    if allow_arg is not None:
        if allow_arg.strip() == expected:
            return True, f"已按 --allow-drop-contexts 显式批准摘掉 {expected}"
        return False, f"--allow-drop-contexts 与实际不符：应为 {expected!r}，收到 {allow_arg!r}"
    if not isatty:
        return False, f"非交互环境拒绝摘掉必需检查 {expected}；确认无误请加 --allow-drop-contexts {expected!r}"
    print("本次 --apply 将**摘掉**这些必需检查：")
    for c in drop:
        print(f"  - {c}")
    hint = f"逐字输入要摘掉的名字（{expected}）以确认，其他任何输入都取消："
    got = (input(hint) if typed is None else typed).strip()
    if got == expected:
        return True, "交互确认通过"
    return False, "输入与待摘掉清单不符，已取消"


def read_allow_auto_merge(slug: str) -> tuple[int, str, str]:
    """仓库级 allow_auto_merge 开关（不在 protection 响应里，单独一次 GET）。"""
    return gh(["repos/" + slug, "--jq", ".allow_auto_merge"])


def compute_drift(
    cur: dict,
    reviews: tuple[str, dict[str, Any] | None, str],
    want: dict,
    slug: str,
    am_reader=read_allow_auto_merge,
) -> tuple[list[str], list[str]]:
    """返回 (漂移字段列表, 无法判定的字段列表)。am_reader 可注入，供测试离线跑。"""
    d: list[str] = []
    unknown: list[str] = []

    state, payload, detail = reviews
    if state == REVIEWS_OK and payload is not None:
        want_pr = want["required_pull_request_reviews"]
        for key in (
            "required_approving_review_count",
            "dismiss_stale_reviews",
            "require_code_owner_reviews",
            "require_last_push_approval",
        ):
            if payload.get(key) != want_pr[key]:
                d.append(f"review:{key}")
    elif state == REVIEWS_MISSING:
        unknown.append(f"review(404，未配置或读不到：{detail[:60]})")
    else:
        unknown.append(f"review(读取失败：{detail[:60]})")

    rsc = cur.get("required_status_checks") or {}
    if bool(rsc.get("strict")) != want["required_status_checks"]["strict"]:
        d.append("strict")
    add, drop = context_changes(list(rsc.get("contexts") or []), want["required_status_checks"]["contexts"])
    if add or drop:
        d.append("contexts")
    if bool((cur.get("enforce_admins") or {}).get("enabled")) != want["enforce_admins"]:
        d.append("enforce_admins")
    conv = (cur.get("required_conversation_resolution") or {}).get("enabled")
    if bool(conv) != want["required_conversation_resolution"]:
        d.append("conv_res")
    code, am, am_err = am_reader(slug)
    if code != 0:
        unknown.append(f"allow_auto_merge(读取失败：{am_err[:60]})")
    elif (am == "true") != bool(want.get("_allow_auto_merge", True)):
        d.append("allow_auto_merge")
    return d, unknown


def main() -> int:
    ap = argparse.ArgumentParser(description="分支保护声明镜像的漂移检测 / 幂等应用")
    ap.add_argument("--apply", action="store_true", help="写入远端（默认只读检测）")
    ap.add_argument(
        "--allow-drop-contexts",
        default=None,
        metavar='"A|B"',
        help="批准摘掉的必需检查清单，必须与将要摘掉的内容逐字相同",
    )
    args = ap.parse_args()

    with open(CFG, encoding="utf-8") as f:
        cfg = json.load(f)
    slug = f"{cfg['owner']}/{cfg['repo']}"
    want = desired_body(cfg)
    want["_allow_auto_merge"] = bool(cfg["policy"]["allow_auto_merge"])

    code, out, err = gh(["repos/" + slug + "/branches/main/protection"])
    if code != 0:
        print("[FAIL] 读取保护失败:", err[:200])
        return 2
    cur = json.loads(out)
    reviews = fetch_reviews(slug)
    drift, unknown = compute_drift(cur, reviews, want, slug)

    live_ctx = list((cur.get("required_status_checks") or {}).get("contexts") or [])
    add, drop = context_changes(live_ctx, want["required_status_checks"]["contexts"])

    for item in unknown:
        print("[UNKNOWN]", item)
    if not drift and not unknown:
        print("[OK] 无漂移")
        return 0

    print("[DRIFT]", slug, "->", ", ".join(drift) or "(仅无法判定项)")
    print(f"  必需检查：live {len(live_ctx)} 条 -> 声明 {len(want['required_status_checks']['contexts'])} 条"
          f"| 将新增 {add or '无'} | 将摘掉 {drop or '无'}")
    if reviews[0] == REVIEWS_OK and reviews[1] is not None:
        live_pr, want_pr = reviews[1], want["required_pull_request_reviews"]
        print(f"  审批：live count={live_pr.get('required_approving_review_count')} "
              f"dismiss_stale={live_pr.get('dismiss_stale_reviews')} -> 声明 count="
              f"{want_pr['required_approving_review_count']} dismiss_stale={want_pr['dismiss_stale_reviews']}")
    if not args.apply:
        return 1

    ok, why = drops_confirmed(drop, args.allow_drop_contexts, sys.stdin.isatty())
    print(f"  摘检查确认：{why}")
    if not ok:
        return 1

    body = {k: v for k, v in want.items() if not k.startswith("_")}
    fd, path = tempfile.mkstemp(suffix=".json", prefix="branch-protection-body-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False)
        c, _, e = gh(["-X", "PUT", "repos/" + slug + "/branches/main/protection", "--input", path])
        print("apply ->", "OK" if c == 0 else e[:200])
    finally:
        os.unlink(path)
    if cfg["policy"]["allow_auto_merge"]:
        gh(["-X", "PATCH", "repos/" + slug, "-F", "allow_auto_merge=true"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
