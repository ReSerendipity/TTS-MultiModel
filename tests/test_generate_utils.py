"""routes/generate/utils.py 单元测试 — 生成辅助工具。

覆盖目标模块: app/integrated_app/routes/generate/utils.py
"""

from integrated_app.routes.generate.utils import (
    _parse_bool_form,
    _safe_error_msg,
    build_generation_error_response,
    format_sse_event,
    new_task_id,
    validate_reference_audio_quality,
)


class TestFormatSSEEvent:
    def test_basic_event(self):
        raw = format_sse_event("progress", {"percent": 50})
        assert "event: progress" in raw
        assert '"percent": 50' in raw
        assert raw.endswith("\n\n")
        assert "retry: 3000" in raw

    def test_data_serialized(self):
        raw = format_sse_event("status", {"engine": "voxcpm2"})
        assert "engine" in raw


class TestNewTaskId:
    def test_unique_ids(self):
        ids = {new_task_id() for _ in range(100)}
        assert len(ids) == 100

    def test_returns_str(self):
        assert isinstance(new_task_id(), str)


class TestParseBoolForm:
    def test_truthy(self):
        for truthy in ("true", "1", "yes", "True", "TRUE"):
            assert _parse_bool_form(truthy) is True

    def test_falsy(self):
        for falsy in ("false", "0", "no", "off", "", None, "on"):
            assert _parse_bool_form(falsy) is False

    def test_bool_input(self):
        assert _parse_bool_form(True) is True
        assert _parse_bool_form(False) is False


class TestSafeErrorMsg:
    def test_str_exception(self):
        assert "boom" in _safe_error_msg(ValueError("boom"))

    def test_runtime_error_cuda_hint(self):
        msg = _safe_error_msg(RuntimeError("CUDA out of memory"))
        assert "显存不足" in msg

    def test_file_not_found(self):
        assert "不存在" in _safe_error_msg(FileNotFoundError())

    def test_unknown_exception_fallback(self):
        assert _safe_error_msg(ValueError("x"))  # 不崩溃即可


class TestBuildErrorResponse:
    def test_returns_json(self):
        import json

        from integrated_app.exceptions import GenerationError

        resp = build_generation_error_response(GenerationError("出错了"), "gen-1")
        assert resp.status_code == 500
        data = json.loads(resp.body)
        assert data["status"] == "error"
        assert data["task_id"] == "gen-1"
        assert data["error"]["message"] == "出错了"


class TestValidateReferenceAudioQuality:
    """P0 安全整改：参考音频时长 + 语音活动门槛单元测试。"""

    def _make_wav(self, tmp_path, duration_s: float, freq: float = 440.0, amplitude: float = 0.1):
        """生成指定时长/音量的正弦波 WAV 文件。"""
        import numpy as np
        import soundfile as sf

        sr = 16000
        t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
        data = (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        path = tmp_path / "ref.wav"
        sf.write(str(path), data, sr)
        return str(path)

    def test_valid_audio_passes(self, tmp_path):
        """3s 以上有声音频通过校验。"""
        path = self._make_wav(tmp_path, duration_s=5.0)
        assert validate_reference_audio_quality(path, min_seconds=3.0) is None

    def test_short_audio_rejected(self, tmp_path):
        """时长短于阈值被拒绝。"""
        path = self._make_wav(tmp_path, duration_s=1.0)
        err = validate_reference_audio_quality(path, min_seconds=3.0)
        assert err is not None
        assert "时长过短" in err

    def test_silent_audio_rejected(self, tmp_path):
        """近静音音频被拒绝。"""
        path = self._make_wav(tmp_path, duration_s=5.0, amplitude=0.0)
        err = validate_reference_audio_quality(path, min_seconds=3.0)
        assert err is not None
        assert "静音" in err

    def test_min_seconds_zero_disables_check(self, tmp_path):
        """min_seconds=0 关闭校验，即使短音频也通过。"""
        path = self._make_wav(tmp_path, duration_s=0.5)
        assert validate_reference_audio_quality(path, min_seconds=0.0) is None

    def test_nonexistent_file_fail_open(self):
        """文件不存在/解码失败时 fail-open（不阻断）。"""
        assert validate_reference_audio_quality("/nonexistent/path.wav", min_seconds=3.0) is None

    def test_boundary_exact_min_duration_passes(self, tmp_path):
        """恰好等于阈值时长通过。"""
        path = self._make_wav(tmp_path, duration_s=3.0)
        assert validate_reference_audio_quality(path, min_seconds=3.0) is None
