#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI 结构化输出层与 service 适配层的单元测试（ADR-0005 P0）。

覆盖：
    - Result 信封的序列化与语义化退出码；
    - emit() 在 --json 下输出单行合法 JSON，人类模式输出文本；
    - begin_json_mode() 隔离第三方 print（stdout 不被污染）；
    - _resolve_engine / _uses_custom_model_path 的参数映射与 Mock 防御。
"""

from __future__ import annotations

import contextlib
import io
import json
import sys

import pytest

from integrated_app.cli_output import (
    EXIT_BUSINESS_ERROR,
    EXIT_NOT_READY,
    EXIT_OK,
    EXIT_USAGE_ERROR,
    Result,
    begin_json_mode,
    emit,
)
from integrated_app.cli_service import _resolve_engine, _uses_custom_model_path


class _FakeArgs:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


@pytest.fixture(autouse=True)
def _reset_json_mode():
    """每个用例后恢复 sys.stdout，避免 begin_json_mode 影响其他测试。"""
    original = sys.stdout
    yield
    sys.stdout = original
    # 重置模块级状态
    import integrated_app.cli_output as mod

    mod._ORIGINAL_STDOUT = None


class TestResultEnvelope:
    def test_success_serialization(self):
        r = Result.success({"audio_path": "a.wav", "duration": 1.5}, command="design")
        payload = r.to_dict()
        assert payload["ok"] is True
        assert payload["command"] == "design"
        assert payload["data"]["audio_path"] == "a.wav"
        assert "error" not in payload

    def test_failure_serialization(self):
        r = Result.failure("模型未加载", error_code="NOT_READY", command="design")
        payload = r.to_dict()
        assert payload["ok"] is False
        assert payload["error_code"] == "NOT_READY"
        assert payload["error"] == "模型未加载"
        assert "data" not in payload

    def test_exit_code_mapping(self):
        assert Result.success().exit_code() == EXIT_OK
        assert Result.failure("x", "BUSINESS_ERROR").exit_code() == EXIT_BUSINESS_ERROR
        assert Result.failure("x", "USAGE_ERROR").exit_code() == EXIT_USAGE_ERROR
        assert Result.failure("x", "NOT_READY").exit_code() == EXIT_NOT_READY
        assert Result.failure("x", "ENVIRONMENT").exit_code() == EXIT_NOT_READY

    def test_warnings_included(self):
        r = Result.success({"a": 1}, warnings=["注意 A"])
        assert r.to_dict()["warnings"] == ["注意 A"]


class TestEmit:
    def test_json_output_is_strictly_parseable(self):
        r = Result.success({"audio_path": "a.wav", "engine": "voxcpm2"}, command="design")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = emit(r, as_json=True)
        raw = buf.getvalue()
        assert code == EXIT_OK
        assert raw.count("\n") == 1
        obj = json.loads(raw)
        assert obj["ok"] is True
        assert obj["data"]["engine"] == "voxcpm2"

    def test_human_output(self):
        r = Result.success({"audio_path": "a.wav"})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            emit(r, as_json=False)
        assert "audio_path: a.wav" in buf.getvalue()

    def test_failure_goes_to_stderr(self):
        r = Result.failure("boom", "BUSINESS_ERROR")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = emit(r, as_json=False)
        assert code == EXIT_BUSINESS_ERROR
        assert out.getvalue() == ""
        assert "boom" in err.getvalue()

    def test_warnings_go_to_stderr_and_json_field(self):
        """warnings 既写 stderr 日志，也作为结构化字段进 JSON（便于 agent 判断）。"""
        r = Result.success({"a": 1}, warnings=["警告"])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            emit(r, as_json=True)
        # stderr 有可读告警
        assert "警告" in err.getvalue()
        # stdout 是单行 JSON，warnings 在字段中而非裸日志
        assert out.getvalue().count("\n") == 1
        obj = json.loads(out.getvalue())
        assert obj["warnings"] == ["警告"]


class TestJsonModeIsolation:
    def test_third_party_print_redirected_to_stderr(self):
        """模拟 funasr 在 import 时 print，验证不会污染 stdout。

        begin_json_mode 把 sys.stdout 指向 stderr，第三方 print 因此落到 stderr；
        emit 写回"进入 JSON 模式前的原始 stdout"，故结果流里只有一行 JSON。
        """
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            begin_json_mode()
            print("funasr version: 1.4.16.")  # 走 sys.stdout → 已被重定向到 stderr
            emit(Result.success({"ok_field": 1}), as_json=True)
        assert "funasr version" in err.getvalue()
        assert "funasr version" not in out.getvalue()
        # 结果流是干净的单行 JSON
        json.loads(out.getvalue().strip())
        assert out.getvalue().strip().count("\n") == 0


class TestEngineResolution:
    def test_default_engine(self):
        assert _resolve_engine(_FakeArgs(engine=None)) == "voxcpm2"
        assert _resolve_engine(_FakeArgs()) == "voxcpm2"

    def test_explicit_engine(self):
        assert _resolve_engine(_FakeArgs(engine="indextts2")) == "indextts2"
        assert _resolve_engine(_FakeArgs(engine="voxcpm2")) == "voxcpm2"

    def test_non_string_engine_falls_back(self):
        """测试里 args 可能是 MagicMock，不能把对象当引擎名。"""
        from unittest.mock import MagicMock

        assert _resolve_engine(_FakeArgs(engine=MagicMock())) == "voxcpm2"
        assert _resolve_engine(_FakeArgs(engine="")) == "voxcpm2"
        assert _resolve_engine(_FakeArgs(engine="   ")) == "voxcpm2"

    def test_custom_model_path_detection(self):
        """True 表示用户指定了非默认路径（service 会忽略并告警）。"""
        assert _uses_custom_model_path(_FakeArgs(model_path="D:/other/VoxCPM2")) is True
        assert _uses_custom_model_path(_FakeArgs(model_path="/data/models/foo")) is True
        assert _uses_custom_model_path(_FakeArgs(model_path="model/VoxCPM2")) is False
        assert _uses_custom_model_path(_FakeArgs(model_path=None)) is False
        assert _uses_custom_model_path(_FakeArgs()) is False
