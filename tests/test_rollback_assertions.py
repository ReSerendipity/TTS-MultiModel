"""SRE_RUNBOOK §2.4 断言脚本测试：正控 + 多形态负控。

「负控有效」的判据不是「脚本能返回非零」，而是：
- 同一份合成仓在一致时 **PASS（rc=0）**；
- 人为制造一处不一致后 **FAIL（rc=1）** 且失败项点名到具体检查；
- ``--tag`` 读的是 tag 指向的提交，不是工作树。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


r4 = _load_module("check_rollback_assertions_under_test", "tools/check_rollback_assertions.py")


def _version_json(version: str, embedded: str | None = None) -> str:
    return json.dumps(
        {
            "version": version,
            "release_date": "2026-11-09",
            "minimum_shell_version": "2.2.2",
            "changelog": f"{embedded or version}：测试用说明",
        },
        ensure_ascii=False,
    )


def _changelog(top: str) -> str:
    return f"# Changelog\n\n## [{top}](https://example.com/compare)\n\n### Features\n\n* x\n"


def _write_project(root: Path, version: str, *, embedded: str | None = None, changelog_top: str | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "version.json").write_text(_version_json(version, embedded), encoding="utf-8")
    (root / "CHANGELOG.md").write_text(_changelog(changelog_top or version), encoding="utf-8")
    (root / "pyproject.toml").write_text(f'[project]\nname = "demo"\nversion = "{version}"\n', encoding="utf-8")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")


def _all_levels(findings: list[r4.Finding]) -> set[str]:
    return {item.level for item in findings}


# ---------------------------------------------------------------------------
# 纯函数层：正控 + 负控
# ---------------------------------------------------------------------------


def test_documents_consistent_is_pass() -> None:
    findings = r4.check_documents("1.2.3", _version_json("1.2.3"), _changelog("1.2.3"))
    assert _all_levels(findings) == {"PASS"}, findings


def test_version_json_version_mismatch_is_fail() -> None:
    findings = r4.check_documents("1.2.3", _version_json("1.2.2"), _changelog("1.2.3"))
    assert any(item.level == "FAIL" and "tag ↔ version.json" in item.check for item in findings)


def test_embedded_changelog_drift_is_fail() -> None:
    findings = r4.check_documents("1.2.3", _version_json("1.2.3", embedded="1.2.2"), _changelog("1.2.3"))
    failed = [item for item in findings if item.level == "FAIL"]
    assert failed and "$.changelog 自述" in failed[0].check
    assert "版本号新、说明旧" in failed[0].detail


def test_changelog_top_mismatch_is_fail() -> None:
    findings = r4.check_documents("1.2.3", _version_json("1.2.3"), _changelog("1.2.2"))
    assert any(item.level == "FAIL" and "CHANGELOG.md 顶部条目" in item.check for item in findings)


def test_missing_version_json_is_fail() -> None:
    findings = r4.check_documents("1.2.3", None, _changelog("1.2.3"), ["version.json"])
    assert any(item.level == "FAIL" and "必要文件存在性" in item.check for item in findings)


def test_worktree_mode_positive_and_negative(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "ok"
    _write_project(root, "1.2.3")
    assert r4.main(["--root", str(root), "--worktree"]) == 0
    assert "结果：PASS" in capsys.readouterr().out

    _write_project(root, "1.2.3", changelog_top="1.2.2")
    assert r4.main(["--root", str(root), "--worktree"]) == 1
    assert "[FAIL] CHANGELOG.md 顶部条目" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# git tag 层：正控 + 负控 + 「读 tag 树而非工作树」
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")
def test_tag_mode_positive_then_negative(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "repo"
    _write_project(root, "1.2.3")
    _init_repo(root)
    _git(root, "tag", "v1.2.3")

    assert r4.main(["--root", str(root), "--tag", "v1.2.3"]) == 0
    assert "结果：PASS" in capsys.readouterr().out

    # 负控：把 tag 重打到「tag 名 1.2.3、文件自称 1.2.4」的提交上
    _write_project(root, "1.2.4")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "bump-only")
    _git(root, "tag", "-d", "v1.2.3")
    _git(root, "tag", "v1.2.3")

    assert r4.main(["--root", str(root), "--tag", "v1.2.3"]) == 1
    out = capsys.readouterr().out
    assert "tag ↔ version.json $.version" in out
    assert "硬停" in out


@pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")
def test_tag_mode_reads_tag_tree_not_worktree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "repo"
    _write_project(root, "1.2.3")
    _init_repo(root)
    _git(root, "tag", "v1.2.3")

    # 工作树改成自相矛盾的 9.9.9/9.9.8；tag v1.2.3 仍应 PASS
    _write_project(root, "9.9.9", embedded="9.9.8", changelog_top="9.9.7")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "worktree-drift")

    assert r4.main(["--root", str(root), "--tag", "v1.2.3"]) == 0
    assert "1.2.3" in capsys.readouterr().out

    assert r4.main(["--root", str(root), "--worktree"]) == 1
    out = capsys.readouterr().out
    assert "$.changelog 自述" in out


@pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")
def test_all_tags_summary_reports_mixed_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "repo"
    _write_project(root, "1.2.3")
    _init_repo(root)
    _git(root, "tag", "v1.2.3")

    _write_project(root, "1.2.3", embedded="1.2.2")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "bad-tag")
    _git(root, "tag", "v1.2.4")

    assert r4.main(["--root", str(root), "--all-tags"]) == 1
    out = capsys.readouterr().out
    assert "2 个 tag：1 PASS / 1 FAIL" in out
    assert "v1.2.4" in out and "v1.2.3" in out
