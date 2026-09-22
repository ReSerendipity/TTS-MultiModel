"""DCO 检查的豁免范围：只放过"自动化自己产的提交"，人的提交缺签名照样红。

为什么要改：release-please 用 GitHub API 建提交（作者 = github-actions[bot]，
工作流里设的 GIT_AUTHOR_* 对它无效），所以它的 release PR **永远不可能**有 Signed-off-by；
分支保护又要求 DCO 通过 —— 于是自动发版路径结构上不可用（#115 / #120 / #132 都撞在这）。

这道测试的作用是把"豁免"钉在它能豁免的地方：混合提交里只要有一条人写的没签名就必须红。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_WF = _ROOT / ".github" / "workflows" / "dco.yml"

HUMAN = ("Re Serendipity", "human@example.com")
BOT = ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com")


def _dco_script() -> str:
    doc = yaml.safe_load(_WF.read_text(encoding="utf-8"))
    steps = doc["jobs"]["dco"]["steps"]
    step = next((s for s in steps if "Signed-off-by" in str(s.get("name") or "")), None)
    assert step, "dco.yml 里找不到 Signed-off-by 校验步骤"
    body = step["run"]
    assert "BASE_SHA" in body and "HEAD_SHA" in body, (
        "脚本又直接从 ${{ }} 模板取 SHA 了 —— 那样本测试无法注入，判据也就没法验"
    )
    return body


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _repo_with(tmp: Path, commits: list[tuple[tuple[str, str], bool]]) -> tuple[Path, str, str]:
    """建一个一次性仓库：commits = [(作者身份, 是否带 Signed-off-by)]，返回 (base, head)。"""
    repo = tmp / "dco_repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test Base")
    _git(repo, "config", "user.email", "base@example.com")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "seed")
    base = _git(repo, "rev-parse", "HEAD")
    for (name, email), signed in commits:
        (repo / f"f-{email}-{len(str(repo))}.txt").write_text("x\n", encoding="utf-8")
        _git(repo, "add", ".")
        msg = "some change" + ("\n\nSigned-off-by: " + f"{name} <{email}>" if signed else "")
        _git(repo, "commit", "-q", "--author", f"{name} <{email}>", "-m", msg)
    return repo, base, _git(repo, "rev-parse", "HEAD")


def _run_dco(tmp: Path, commits: list[tuple[tuple[str, str], bool]]) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    if not bash:  # pragma: no cover - 无 bash 的机器上直接跳过，不给假绿
        pytest.skip("本机没有 bash，无法执行从工作流里抽出的脚本")
    repo, base, head = _repo_with(tmp, commits)
    script = tmp / "dco.sh"
    script.write_text(_dco_script(), encoding="utf-8", newline="\n")
    env = {**os.environ, "BASE_SHA": base, "HEAD_SHA": head, "PATH": os.environ["PATH"]}
    return subprocess.run(
        [bash, str(script)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )


def test_human_commit_without_signoff_still_fails(tmp_path: Path) -> None:
    res = _run_dco(tmp_path, [(HUMAN, False)])
    assert res.returncode == 1, f"人写的提交没签名却被放行：{res.stdout}{res.stderr}"


def test_human_commit_with_signoff_passes(tmp_path: Path) -> None:
    res = _run_dco(tmp_path, [(HUMAN, True)])
    assert res.returncode == 0, res.stdout + res.stderr


def test_bot_release_commit_without_signoff_is_exempt(tmp_path: Path) -> None:
    """这正是 release-please 的形状：作者是 bot、没有也签不出 Signed-off-by。"""
    res = _run_dco(tmp_path, [(BOT, False)])
    assert res.returncode == 0, f"自动化提交仍被判红，自动发版路径依旧不可用：{res.stdout[-300:]}"
    assert "豁免 1" in res.stdout, f"没有把豁免显式记出来（审计要看得到放过了谁）：{res.stdout[-300:]}"


def test_exemption_does_not_bypass_a_whole_pr(tmp_path: Path) -> None:
    """混合：bot 提交 + 人提交（无签名）→ 必须红。这条是本豁免不是后门的关键证据。"""
    res = _run_dco(tmp_path, [(BOT, False), (HUMAN, False)])
    assert res.returncode == 1, f"有 bot 提交在场时人的缺签名被一起放过了：{res.stdout[-400:]}"
    assert "豁免 1" in res.stdout
