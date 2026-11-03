"""``scripts/run_python.py``（仓库唯一解释器解析入口，F7）的判定测试。

判据（与 docs/runbooks/2026-11-03-f7-venv-entrypoint-and-f8-hooks-drift.md 对齐）：

- 解析顺序 = 显式覆盖 → 项目 venv → PATH python → PATH python3 → py -3 → 当前进程；
- 候选必须**实际可执行**才算通过（探测失败即跳过，不把「文件存在」当「能用」）；
- ``TTS_PYTHON`` 显式覆盖失败时**不静默回退**（否则「钉了解释器」是一句空话）；
- 项目 venv 失效（符号链接目标不存在）时返回 None，而不是抛异常或写死路径。

本测试全部离线、不触碰系统状态；只有最后一条用例会以子进程跑一次真实入口。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import run_python as rp  # noqa: E402


def _which(mapping):
    """构造可控的 which：返回 mapping.get(name)。"""
    return lambda name: mapping.get(name)


# ---------------------------------------------------------------- venv 解析
def test_venv_python_none_when_venv_absent(tmp_path):
    assert rp.venv_python(tmp_path) is None


def test_venv_python_none_when_layout_present_but_executable_missing(tmp_path):
    """目录在、解释器不在（本次事故的形态：.venv 还在，解释器不可达）。"""
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    assert rp.venv_python(tmp_path) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows 布局 .venv/Scripts/python.exe 仅在 Windows 上判定")
def test_venv_python_windows_layout(tmp_path):
    exe = tmp_path / ".venv" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert rp.venv_python(tmp_path) == exe


@pytest.mark.skipif(os.name == "nt", reason="POSIX 布局 .venv/bin/python 仅在 POSIX 上判定")
def test_venv_python_posix_layout(tmp_path):
    exe = tmp_path / ".venv" / "bin" / "python"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    assert rp.venv_python(tmp_path) == exe


# ---------------------------------------------------------------- 候选顺序
def test_override_is_the_only_candidate(tmp_path):
    candidates = list(rp.iter_candidates(tmp_path, {rp.ENV_OVERRIDE: "/opt/py/bin/python"}, _which({})))
    assert [c.source for c in candidates] == [f"env:{rp.ENV_OVERRIDE}"]
    assert candidates[0].executable == "/opt/py/bin/python"


def test_path_python_precedes_python3(tmp_path):
    candidates = list(rp.iter_candidates(tmp_path, {}, _which({"python": "A", "python3": "B"})))
    sources = [c.source for c in candidates]
    assert sources[0] == "PATH:python"
    assert sources[1] == "PATH:python3"


def test_current_process_is_last_resort(tmp_path):
    candidates = list(rp.iter_candidates(tmp_path, {}, _which({})))
    assert candidates[-1].source == "current-process"
    assert candidates[-1].executable == sys.executable


def test_first_existing_returns_none_without_any_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(rp.sys, "executable", "")
    assert rp.first_existing(tmp_path, {}, _which({})) is None


# ---------------------------------------------------------------- 探测
def test_probe_rejects_missing_executable(tmp_path):
    assert rp.probe_interpreter(rp.Interpreter(str(tmp_path / "nope"))) is False


def test_probe_accepts_current_interpreter():
    assert rp.probe_interpreter(rp.Interpreter(sys.executable)) is True


def test_resolve_skips_candidate_that_fails_probe(tmp_path):
    def probe(interp):
        return interp.source != "PATH:python"

    chosen = rp.resolve(tmp_path, {}, _which({"python": "A", "python3": "B"}), probe)
    assert chosen.executable == "B"
    assert chosen.source == "PATH:python3"


def test_resolve_override_failure_is_not_silently_downgraded(tmp_path):
    with pytest.raises(rp.InterpreterNotFoundError) as excinfo:
        rp.resolve(tmp_path, {rp.ENV_OVERRIDE: "bad"}, _which({"python": "A"}), lambda _i: False)
    assert rp.ENV_OVERRIDE in str(excinfo.value)


def test_resolve_all_candidates_fail_reports_guidance(tmp_path, monkeypatch):
    monkeypatch.setattr(rp.sys, "executable", "")
    with pytest.raises(rp.InterpreterNotFoundError) as excinfo:
        rp.resolve(tmp_path, {}, _which({}), lambda _i: False)
    assert "无法解析可用的 Python 解释器" in str(excinfo.value)
    assert "修复" in str(excinfo.value)


# ---------------------------------------------------------------- CLI 契约
def test_main_without_arguments_returns_usage_error(capsys):
    assert rp.main([]) == 2
    assert "用法" in capsys.readouterr().err


def test_main_print_outputs_only_resolved_path(monkeypatch, capsys):
    monkeypatch.setattr(rp, "resolve", lambda *a, **k: rp.Interpreter("/fake/python", (), "project-venv"))
    assert rp.main(["--print"]) == 0
    assert capsys.readouterr().out.strip() == "/fake/python"


def test_main_explain_reports_source(monkeypatch, capsys):
    monkeypatch.setattr(rp, "resolve", lambda *a, **k: rp.Interpreter("/fake/python", (), "PATH:python"))
    assert rp.main(["--explain"]) == 0
    assert "PATH:python" in capsys.readouterr().out


def test_main_reports_unresolvable_interpreter(monkeypatch, capsys):
    def boom(*_a, **_k):
        raise rp.InterpreterNotFoundError("no interpreter")

    monkeypatch.setattr(rp, "resolve", boom)
    assert rp.main(["--print"]) == 127
    assert "no interpreter" in capsys.readouterr().err


def test_main_unknown_target_returns_two(monkeypatch, capsys):
    monkeypatch.setattr(rp, "resolve", lambda *a, **k: rp.Interpreter(sys.executable, (), "current-process"))
    assert rp.main(["scripts/definitely-not-here.py"]) == 2
    assert "目标脚本不存在" in capsys.readouterr().err


def test_live_entry_print_runs_in_this_repo():
    """真入口冒烟：子进程跑一次 --print，必须 exit 0 且给出非空解释器。"""
    completed = subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / "run_python.py"), "--print"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip(), "解析入口必须输出解释器，不能静默成功"


def test_exit_code_is_propagated_verbatim(tmp_path):
    """回归守卫：入口必须把目标程序退出码原样带回（Windows 的 os.execv 曾吞掉它）。"""
    script = tmp_path / "exit_seven.py"
    script.write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / "run_python.py"), str(script)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 7, completed.stderr
