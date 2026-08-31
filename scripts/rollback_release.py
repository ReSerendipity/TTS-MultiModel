#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""版本化回滚辅助脚本（SRE 评估 P1-3：消除人肉发布风险）。

行为：
    - ``--list``：列出最近的发布 tag（按版本号倒序）；
    - ``--target <tag>``：将当前分支回退到该 tag 对应的发布提交（反向 revert 其后所有提交）；
    - ``--dry-run``：只打印将要执行的命令，不实际改动工作区。

设计原则：
    - 安全优先：默认**只生成提交、不自动 push**，需人工 review 后 push 再部署；
    - 可审计：使用 ``git revert``（非 ``git reset``），历史完整保留，可再 forward；
    - 离线：仅操作本地 git，不请求任何外部服务（AGENTS.md 硬约束 #5）。

示例：
    python scripts/rollback_release.py --list
    python scripts/rollback_release.py --target v2.2.0 --dry-run
    python scripts/rollback_release.py --target v2.2.0
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def _run(args: list[str], dry_run: bool = False, check: bool = True) -> str:
    """执行 git 命令（dry-run 时仅打印）。

    Args:
        args: git 命令参数列表（不含 ``git``）。
        dry_run: 为 True 时只打印、不执行。
        check: 是否检查返回码。

    Returns:
        str: 命令标准输出。
    """
    cmd = ["git"] + args
    if dry_run:
        print("  [dry-run] $ " + " ".join(cmd))
        return ""
    try:
        return subprocess.run(cmd, check=check, capture_output=True, text=True).stdout.strip()
    except subprocess.CalledProcessError as exc:  # pragma: no cover - 依赖真实 git 状态
        print(f"[error] 命令失败: {' '.join(cmd)}\n{exc.stderr}", file=sys.stderr)
        sys.exit(1)


def list_releases() -> list[str]:
    """列出所有发布 tag（按创建时间倒序，最新的在前）。"""
    out = subprocess.run(
        ["git", "tag", "--sort=-creatordate"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return [t for t in out.splitlines() if t]


def rollback(target: str, dry_run: bool) -> None:
    """回退到指定 tag 的发布提交（反向 revert 其后的提交）。

    Args:
        target: 目标发布 tag（如 ``v2.2.0``）。
        dry_run: 是否仅预览。
    """
    tags = list_releases()
    if target not in tags:
        print(f"[error] 未找到 tag: {target}", file=sys.stderr)
        print("可用 tag：\n  " + "\n  ".join(tags) if tags else "  (无)")
        sys.exit(1)

    # 目标 tag 对应的发布提交
    target_commit = _run(["rev-list", "-n1", target])
    print(f"[info] 目标 tag {target} -> commit {target_commit[:8]}")

    # 其后所有提交（不含该提交），由新到旧
    commits_out = _run(["rev-list", f"{target_commit}..HEAD"])
    commits = commits_out.splitlines() if commits_out else []
    if not commits:
        print("[info] 当前 HEAD 已在目标 tag 之上无新增提交，无需回滚。")
        return

    print(f"[info] 将反向 revert 其后 {len(commits)} 个提交：")
    for c in commits:
        print("   - " + c[:8])

    for c in commits:
        print(f"[revert] {c[:8]}")
        _run(["revert", "--no-edit", c], dry_run=dry_run)

    if dry_run:
        print("[dry-run] 以上为预览，未实际改动。去掉 --dry-run 以执行。")
    else:
        print("[done] 已生成回滚提交。请 review 后执行 `git push`，再重新部署。")


def main() -> None:
    """解析参数并分发。"""
    parser = argparse.ArgumentParser(description="TTS_MultiModel 版本化回滚辅助脚本")
    parser.add_argument("--list", action="store_true", help="列出最近发布 tag")
    parser.add_argument("--target", help="回退目标 tag（如 v2.2.0）")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际执行")
    args = parser.parse_args()

    if args.list:
        tags = list_releases()
        print("发布 tag（最新在前）：")
        for t in tags:
            print("  " + t)
        return

    if not args.target:
        parser.error("请提供 --target <tag> 或 --list")

    rollback(args.target, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
