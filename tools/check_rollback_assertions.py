"""SRE_RUNBOOK §2.4「回滚检查清单」硬停断言的可执行版本。

手册原文（docs/SRE_RUNBOOK.md §2.4，2026-11-01 C 缺口收口新增）要求回滚前校验：

1. 目标 tag ↔ ``version.json`` 的 ``$.version`` ↔ ``CHANGELOG.md`` 顶部条目，三者一致；
2. ``version.json`` 内嵌 ``changelog`` 字段自述版本 = ``$.version``（拦「版本号新、说明旧」）；
3. 任一不一致即**硬停**，不得继续回滚。

本脚本只做「读 + 判」，不改 runbook 文本、不改任何文件、不执行回滚。退出码：

- ``0``：全部检查通过；
- ``1``：存在不一致（硬停）；
- ``2``：用法/环境错误（仓库不存在、git 不可用、tag 不存在等）。

用法::

    python tools/check_rollback_assertions.py --worktree
    python tools/check_rollback_assertions.py --tag v2.9.0
    python tools/check_rollback_assertions.py --all-tags

``--tag`` 读的是**该 tag 指向的提交里的文件**（不是工作树）——回滚目标是 tag 对应的树，
工作树领先主分支是正常状态，不能拿它当 tag 的内容。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+)$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
CHANGELOG_TOP_RE = re.compile(r"^##\s+\[?(\d+\.\d+\.\d+)\]?")
#: ``version.json`` 的 changelog 形如 ``"2.9.0：本版主干是..."``。
CHANGELOG_SELF_RE = re.compile(r"^(\d+\.\d+\.\d+)")

REQUIRED_FILES = ("version.json", "CHANGELOG.md", "pyproject.toml")


@dataclass(frozen=True)
class Finding:
    level: str  # PASS / FAIL
    check: str
    detail: str


def _ver_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _parse_version_json(text: str) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"JSONDecodeError: {exc}"
    if not isinstance(data, dict):
        return None, f"顶层类型为 {type(data).__name__}，不是 object"
    return data, None


def changelog_top_version(text: str) -> str | None:
    """取 CHANGELOG 第一个 x.y.z 版本标题；兼容 ``## [2.9.0](链接)`` 与 ``## 2.9.0``。"""
    for line in text.splitlines():
        match = CHANGELOG_TOP_RE.match(line.strip())
        if match:
            return match.group(1)
    return None


def check_documents(
    expected_version: str | None,
    version_json_text: str | None,
    changelog_text: str | None,
    missing_files: list[str] | None = None,
) -> list[Finding]:
    """纯函数层：不碰 git，方便测试直接做正/负控。

    ``expected_version`` 为 tag 名推导的版本；``--worktree`` 模式传 ``None``，
    以 ``version.json`` 的 ``$.version`` 为基准（再核 CHANGELOG 与内嵌说明）。
    """
    findings: list[Finding] = []
    missing = list(missing_files or [])
    for name in REQUIRED_FILES:
        if name in missing:
            findings.append(Finding("FAIL", "必要文件存在性", f"{name} 不存在"))
        else:
            findings.append(Finding("PASS", "必要文件存在性", f"{name} 在位"))

    if version_json_text is None:
        findings.append(Finding("FAIL", "version.json 可读性", "读不到 version.json，后续版本位无法核对"))
        return findings
    data, error = _parse_version_json(version_json_text)
    if data is None:
        findings.append(Finding("FAIL", "version.json 可读性", str(error)))
        return findings

    version = str(data.get("version", ""))
    if not SEMVER_RE.fullmatch(version):
        findings.append(Finding("FAIL", "version.json $.version 形态", f"不是 x.y.z：{version!r}"))
        return findings
    findings.append(Finding("PASS", "version.json $.version 形态", version))

    baseline = expected_version or version
    if expected_version is not None:
        if version == expected_version:
            findings.append(Finding("PASS", "tag ↔ version.json $.version", f"{expected_version} == {version}"))
        else:
            findings.append(
                Finding("FAIL", "tag ↔ version.json $.version", f"tag 名说 {expected_version}，文件自称 {version}")
            )
    else:
        findings.append(Finding("PASS", "version.json $.version 基准", version))

    embedded = str(data.get("changelog", "")).lstrip()
    embedded_version = CHANGELOG_SELF_RE.match(embedded).group(1) if CHANGELOG_SELF_RE.match(embedded) else None
    if embedded_version is None:
        findings.append(
            Finding(
                "FAIL", "version.json $.changelog 自述", "changelog 字段为空或不以 x.y.z 开头（壳里那栏会是空/旧说明）"
            )
        )
    elif embedded_version == version:
        findings.append(Finding("PASS", "version.json $.changelog 自述", f"{embedded_version} == $.version {version}"))
    else:
        findings.append(
            Finding(
                "FAIL",
                "version.json $.changelog 自述",
                f"changelog 讲的是 {embedded_version}，$.version 却是 {version}（版本号新、说明旧）",
            )
        )

    if changelog_text is None:
        findings.append(Finding("FAIL", "CHANGELOG.md 可读性", "读不到 CHANGELOG.md，顶部条目无法核对"))
        return findings
    top = changelog_top_version(changelog_text)
    if top is None:
        findings.append(Finding("FAIL", "CHANGELOG.md 顶部条目", "找不到 x.y.z 形式的版本标题"))
    elif top == baseline:
        findings.append(Finding("PASS", "CHANGELOG.md 顶部条目", f"{top} == {baseline}"))
    else:
        findings.append(Finding("FAIL", "CHANGELOG.md 顶部条目", f"顶部条目是 {top}，目标/基准版本是 {baseline}"))
    return findings


def _git(root: Path, *args: str) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def _git_file(root: Path, tag: str, rel: str) -> str | None:
    rc, out, _ = _git(root, "show", f"{tag}:{rel}")
    return out if rc == 0 else None


def _file_present(root: Path, tag: str, rel: str) -> bool:
    rc, _, _ = _git(root, "cat-file", "-e", f"{tag}:{rel}")
    return rc == 0


def release_tags(root: Path) -> list[str]:
    rc, out, _ = _git(root, "tag", "--list", "v*")
    if rc != 0:
        return []
    tags = [line.strip() for line in out.splitlines() if TAG_RE.fullmatch(line.strip())]
    return sorted(tags, key=lambda tag: _ver_tuple(tag[1:]))


def verify_tag(root: Path, tag: str) -> list[Finding]:
    findings: list[Finding] = []
    match = TAG_RE.fullmatch(tag)
    if not match:
        return [Finding("FAIL", "tag 名形态", f"{tag!r} 不是 vX.Y.Z")]
    expected = match.group(1)
    findings.append(Finding("PASS", "tag 名形态", tag))

    rc, _, _ = _git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}")
    if rc != 0:
        findings.append(Finding("FAIL", "tag 存在性", f"{tag} 不存在（无法作为回滚目标）"))
        return findings
    findings.append(Finding("PASS", "tag 存在性", tag))

    missing: list[str] = []
    version_text = _git_file(root, tag, "version.json")
    changelog_text = _git_file(root, tag, "CHANGELOG.md")
    for rel in REQUIRED_FILES:
        if not _file_present(root, tag, rel):
            missing.append(rel)

    findings.extend(check_documents(expected, version_text, changelog_text, missing))
    return findings


def verify_worktree(root: Path) -> list[Finding]:
    missing = [rel for rel in REQUIRED_FILES if not (root / rel).is_file()]
    version_path = root / "version.json"
    changelog_path = root / "CHANGELOG.md"
    version_text = version_path.read_text(encoding="utf-8") if version_path.is_file() else None
    changelog_text = changelog_path.read_text(encoding="utf-8") if changelog_path.is_file() else None
    return check_documents(None, version_text, changelog_text, missing)


def _print_findings(scope: str, findings: list[Finding]) -> bool:
    ok = all(item.level == "PASS" for item in findings)
    print(f"\n== {scope} ==")
    for item in findings:
        print(f"  [{item.level}] {item.check}: {item.detail}")
    print(f"  结果：{'PASS' if ok else 'FAIL（硬停：不得继续回滚）'}")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SRE_RUNBOOK §2.4 回滚前版本位硬停断言（只读）")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="仓库根目录")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--worktree", action="store_true", help="核当前工作树（不比对 tag）")
    mode.add_argument("--tag", help="核指定 tag 指向的提交（回滚目标）")
    mode.add_argument("--all-tags", action="store_true", help="核全部 vX.Y.Z release tag")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if not root.is_dir():
        print(f"[FAIL] 仓库根目录不存在：{root}", file=sys.stderr)
        return 2
    if args.worktree:
        ok = _print_findings("工作树", verify_worktree(root))
        return 0 if ok else 1
    if args.all_tags:
        tags = release_tags(root)
        if not tags:
            print("[FAIL] 仓库里没有 vX.Y.Z release tag", file=sys.stderr)
            return 2
        results = [(tag, verify_tag(root, tag)) for tag in tags]
        all_ok = True
        for tag, findings in results:
            ok = _print_findings(tag, findings)
            all_ok = all_ok and ok
        passed = sum(1 for _, findings in results if all(item.level == "PASS" for item in findings))
        print(f"\n== 汇总 == {len(results)} 个 tag：{passed} PASS / {len(results) - passed} FAIL")
        return 0 if all_ok else 1

    target = args.tag
    if target is None:
        tags = release_tags(root)
        if not tags:
            print("[FAIL] 未指定 --tag，且仓库里没有 vX.Y.Z release tag", file=sys.stderr)
            return 2
        target = tags[-1]
        print(f"[info] 未指定 --tag，取最新 release tag：{target}")
    findings = verify_tag(root, target)
    return 0 if _print_findings(target, findings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
