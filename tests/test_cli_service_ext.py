#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""P1 引擎适配层的单元测试（ADR-0005 P1）。

不加载任何模型：只覆盖纯逻辑——音频元组解析（两种顺序）、路径解析、
默认输出路径、后处理真实链路（纯函数）、以及各动作的参数校验失败分支。
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from integrated_app.cli_output import EXIT_NOT_READY, EXIT_USAGE_ERROR
from integrated_app.cli_service_ext import (
    _audio_tuple_to_result,
    _default_output,
    _resolve_audio_path,
    run_convert,
    run_edit,
    run_postprocess,
)


class _Args:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _Parser:
    def error(self, message):  # pragma: no cover - 仅用于断言不被调用
        raise AssertionError(f"parser.error 不应被调用: {message}")


class TestAudioTupleParsing:
    def test_sr_first_order(self, tmp_path):
        """VoxCPM2 / IndexTTS 约定：(sample_rate, audio, ref)。"""
        audio = np.zeros(1600, dtype=np.float32)
        target = tmp_path / "out.wav"
        raw = ((16000, audio, str(target)), "ok")
        result = _audio_tuple_to_result(raw, command="ultimate", engine="voxcpm2")
        assert result.ok is True
        # 文件不存在时用数组落盘
        assert os.path.isfile(result.data["audio_path"])
        assert result.data["duration"] == pytest.approx(0.1)
        assert result.data["engine"] == "voxcpm2"

    def test_audio_first_order(self, tmp_path):
        """Breeze 约定：(audio, sample_rate, ref)。"""
        audio = np.zeros(800, dtype=np.float32)
        target = tmp_path / "bz.wav"
        raw = ((audio, 16000, str(target)), "ok")
        result = _audio_tuple_to_result(raw, command="breeze-design", engine="breeze-tts2", audio_first=True)
        assert result.ok is True
        assert result.data["duration"] == pytest.approx(0.05)

    def test_invalid_shape_returns_failure(self):
        result = _audio_tuple_to_result((("bad",), "msg"), command="x")
        assert result.ok is False
        assert result.error_code == "BUSINESS_ERROR"

    def test_empty_audio_without_file_returns_failure(self):
        result = _audio_tuple_to_result(((16000, None, ""), "msg"), command="x")
        assert result.ok is False


class TestPathHelpers:
    def test_resolve_absolute_path(self):
        p = str(os.path.abspath("a.wav"))
        assert _resolve_audio_path(p) == p

    def test_resolve_bare_filename_joins_save_dir(self):
        from integrated_app.config import SAVE_DIR

        assert _resolve_audio_path("foo.wav") == os.path.join(SAVE_DIR, "foo.wav")

    def test_resolve_empty(self):
        assert _resolve_audio_path("") == ""

    def test_default_output_under_outputs(self):
        from integrated_app.config import SAVE_DIR

        p = _default_output("unit_test")
        assert os.path.dirname(p) == SAVE_DIR
        assert os.path.basename(p).startswith("unit_test_")
        assert p.endswith(".wav")


class TestPostprocess:
    def test_real_postprocess_pipeline(self, tmp_path):
        """纯函数链路：读音频 → 变速 → 写回。"""
        import soundfile as sf

        src = tmp_path / "in.wav"
        sr = 16000
        sf.write(str(src), np.zeros(sr, dtype=np.float32), sr)

        args = _Args(
            audio=str(src),
            tempo=1.25,
            lufs=-16.0,
            enhance=False,
            trim_silence=False,
            denoise=False,
            normalize=False,
            output=None,
        )
        result = run_postprocess(args, _Parser())
        assert result.ok is True
        assert os.path.isfile(result.data["audio_path"])
        # 1 秒音频按 1.25 倍速 → 约 0.8 秒
        assert result.data["duration"] == pytest.approx(0.8, abs=0.05)

    def test_missing_audio_returns_usage_error(self, tmp_path):
        args = _Args(
            audio=str(tmp_path / "nope.wav"),
            tempo=1.0,
            lufs=-16.0,
            enhance=False,
            trim_silence=False,
            denoise=False,
            normalize=True,
            output=None,
        )
        result = run_postprocess(args, _Parser())
        assert result.ok is False
        assert result.error_code == "USAGE_ERROR"
        assert result.exit_code() == EXIT_USAGE_ERROR


class TestParamValidation:
    def test_convert_missing_files(self, tmp_path):
        args = _Args(source_audio=str(tmp_path / "a.wav"), target_audio=str(tmp_path / "b.wav"), tau=0.3, output=None)
        result = run_convert(args, _Parser())
        assert result.ok is False
        assert result.error_code == "USAGE_ERROR"

    def test_edit_requires_source_text(self, tmp_path):
        src = tmp_path / "a.wav"
        src.write_bytes(b"RIFF")
        args = _Args(source_audio=str(src), source_text="", edit_type="emotion", edit_info="", output=None)
        result = run_edit(args, _Parser())
        assert result.ok is False
        assert result.error_code == "USAGE_ERROR"

    def test_not_ready_exit_code_semantics(self):
        """NOT_READY 应映射到退出码 3（环境未就绪），供 agent 区分。"""
        result = _audio_tuple_to_result((("bad",), "msg"), command="x")
        assert result.exit_code() != EXIT_NOT_READY  # 结构错误属业务失败
        from integrated_app.cli_output import Result

        assert Result.failure("x", "NOT_READY").exit_code() == EXIT_NOT_READY
