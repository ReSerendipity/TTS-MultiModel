# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Breeze 依赖隔离桥接的单测（纯 mock，不加载模型、不真起子进程）。

覆盖：
    - transformers 版本检测 → 是否委托
    - 隔离 venv 路径优先级（环境变量 > D 盘 > 项目内）
    - 子进程命令构造（各模式参数透传）
    - 输出 JSON 解析（横幅行跳过、失败透传、超时/异常兜底）
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

_APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from integrated_app import breeze_runtime as br  # noqa: E402


def _args(**overrides):
    base = {
        "text": "夜色慢慢沉下来",
        "control": "一位低沉成熟的男声",
        "reference_audio": None,
        "ref_text": None,
        "cfg_scale": 4.0,
        "seed": 42,
        "output": "out.wav",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestVersionDetection:
    def test_major_5_delegates(self, monkeypatch):
        monkeypatch.setattr(br, "transformers_major", lambda: 5)
        assert br.should_delegate() is True

    def test_major_4_runs_in_process(self, monkeypatch):
        monkeypatch.setattr(br, "transformers_major", lambda: 4)
        assert br.should_delegate() is False

    def test_transformers_major_parses_version(self, monkeypatch):
        fake = MagicMock()
        fake.__version__ = "5.20.2"
        monkeypatch.setitem(sys.modules, "transformers", fake)
        assert br.transformers_major() == 5


class TestVenvPath:
    def test_env_var_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TTS_BREEZE_VENV", str(tmp_path))
        monkeypatch.setattr(br.sys, "platform", "win32")
        # 造一个假解释器
        exe = tmp_path / "Scripts" / "python.exe"
        exe.parent.mkdir(parents=True)
        exe.touch()
        assert br.breeze_venv_python() == exe

    def test_returns_none_when_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TTS_BREEZE_VENV", str(tmp_path / "nonexistent"))
        # 用非 Windows 平台隔离掉 D 盘候选，避免测试依赖本机环境
        monkeypatch.setattr(br.sys, "platform", "linux")
        monkeypatch.setattr(br, "_PROJECT_ROOT", tmp_path)
        assert br.breeze_venv_python() is None

    def test_setup_hint_actionable(self):
        hint = br.setup_hint()
        assert "setup_breeze_venv.py" in hint
        assert "TTS_BREEZE_VENV" in hint


class TestRunIsolated:
    def _fake_python(self, monkeypatch, tmp_path):
        exe = tmp_path / "Scripts" / "python.exe"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch()
        monkeypatch.setattr(br, "breeze_venv_python", lambda: exe)
        return exe

    def test_success_parses_last_json_line(self, monkeypatch, tmp_path):
        self._fake_python(monkeypatch, tmp_path)
        data = {"audio_path": "out.wav", "duration": 10.24, "engine": "breeze-tts2"}
        completed = MagicMock(returncode=0)
        completed.stdout = "CLI banner\n" + json.dumps({"ok": True, "data": data})
        completed.stderr = ""
        run = MagicMock(return_value=completed)
        monkeypatch.setattr(subprocess, "run", run)

        assert br.run_isolated_breeze(_args(), "design") == data
        cmd = run.call_args.args[0]
        assert "generate" in cmd and "breeze-design" in cmd
        assert "--text" in cmd and "夜色慢慢沉下来" in cmd
        assert "--control" in cmd
        assert cmd[-1] == "--json"

    def test_clone_passes_reference_args(self, monkeypatch, tmp_path):
        self._fake_python(monkeypatch, tmp_path)
        completed = MagicMock(returncode=0)
        completed.stdout = json.dumps({"ok": True, "data": {"audio_path": "x.wav"}})
        completed.stderr = ""
        run = MagicMock(return_value=completed)
        monkeypatch.setattr(subprocess, "run", run)

        br.run_isolated_breeze(_args(reference_audio="ref.wav", ref_text="转写文本"), "clone")
        cmd = run.call_args.args[0]
        assert "breeze-clone" in cmd
        assert "--reference-audio" in cmd and "ref.wav" in cmd
        assert "--ref-text" in cmd and "转写文本" in cmd

    def test_failure_payload_passed_through(self, monkeypatch, tmp_path):
        self._fake_python(monkeypatch, tmp_path)
        completed = MagicMock(returncode=1)
        completed.stdout = json.dumps({"ok": False, "error": "生成失败", "error_code": "BUSINESS_ERROR"})
        completed.stderr = ""
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=completed))

        result = br.run_isolated_breeze(_args(), "design")
        assert result["error"] == "生成失败"
        assert result["error_code"] == "BUSINESS_ERROR"

    def test_no_json_returns_error(self, monkeypatch, tmp_path):
        self._fake_python(monkeypatch, tmp_path)
        completed = MagicMock(returncode=1)
        completed.stdout = "some banner only"
        completed.stderr = "traceback ..."
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=completed))

        result = br.run_isolated_breeze(_args(), "design")
        assert result["error_code"] == "INTERNAL_ERROR"
        assert "未返回 JSON" in result["error"]

    def test_timeout(self, monkeypatch, tmp_path):
        self._fake_python(monkeypatch, tmp_path)

        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="x", timeout=1)

        monkeypatch.setattr(subprocess, "run", boom)
        result = br.run_isolated_breeze(_args(), "design")
        assert result["error_code"] == "TIMEOUT"

    def test_missing_venv_returns_not_ready(self, monkeypatch):
        monkeypatch.setattr(br, "breeze_venv_python", lambda: None)
        result = br.run_isolated_breeze(_args(), "design")
        assert result["error_code"] == "NOT_READY"
        assert "setup_breeze_venv.py" in result["error"]
