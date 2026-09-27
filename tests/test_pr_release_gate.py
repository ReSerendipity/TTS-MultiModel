"""scripts/pr_release_gate.py 的判定单测（纯函数 + 进程边界各一组）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pr_release_gate as gate  # noqa: E402

RELEASE_REF = "release-please--branches--main"


def test_non_release_pr_is_success_regardless_of_readiness():
    # 普通 PR：readiness 入参不被消费，恒 success
    assert gate.decide("feat/something", readiness_ok=False) == ("success", gate.OK_DESC)
    assert gate.decide("chore/ci", readiness_ok=True) == ("success", gate.OK_DESC)
    assert gate.decide("dependabot/pip/requests-x.y", readiness_ok=False)[0] == "success"


def test_release_pr_passes_through_readiness_result():
    state_ok, _ = gate.decide(RELEASE_REF, readiness_ok=True)
    state_bad, desc_bad = gate.decide(RELEASE_REF, readiness_ok=False)
    assert state_ok == "success"
    assert state_bad == "failure"
    assert "check_release_readiness" in desc_bad


def test_release_branch_constant_matches_release_please_default():
    # 防止有人改了 release-please 分支约定而漏改这里
    assert gate.RELEASE_BRANCH == "release-please--branches--main"


def test_main_stdout_protocol_on_non_release_ref(monkeypatch):
    # 非 release ref：不跑 readiness，stdout 两行 = state + JSON(description)
    monkeypatch.setenv("HEAD_REF", "feat/probe")
    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "pr_release_gate.py"), "--root", str(repo_root)],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = proc.stdout.strip().splitlines()
    assert lines[0] == "success"
    assert json.loads(lines[1]) == gate.OK_DESC
