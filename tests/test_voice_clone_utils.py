"""voice_clone_utils 模块单元测试 — 参考音频验证与预处理工具。

覆盖目标模块: app/integrated_app/voice_clone_utils.py
"""

import numpy as np
import pytest

from integrated_app.voice_clone_utils import (
    AudioQualityResult,
    PreprocessResult,
    estimate_audio_duration,
    get_audio_format,
    is_supported_audio_format,
    preprocess_reference_audio,
    save_audio_array,
    validate_reference_audio,
)


def _sine(sr=24000, duration=1.0):
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    return (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


class TestValidateReferenceAudio:
    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            validate_reference_audio(str(tmp_path / "nope.wav"))

    def test_unsupported_format(self, tmp_path):
        f = tmp_path / "audio.xyz"
        f.write_bytes(b"data")
        with pytest.raises(ValueError):
            validate_reference_audio(str(f))

    def test_unreadable_file(self, tmp_path):
        f = tmp_path / "audio.wav"
        f.write_bytes(b"not a real wav")
        result = validate_reference_audio(str(f))
        assert result.is_valid is False


class TestPreprocessReferenceAudio:
    def test_array_input(self):
        audio = _sine()
        result = preprocess_reference_audio(audio, sample_rate=24000, target_sr=24000)
        assert isinstance(result, PreprocessResult)
        assert result.audio is not None

    def test_target_sr_resample(self):
        audio = _sine(sr=16000, duration=1.0)
        result = preprocess_reference_audio(audio, sample_rate=16000, target_sr=24000)
        assert result.sample_rate == 24000

    def test_stereo_to_mono(self):
        stereo = np.stack([_sine(), _sine()], axis=-1)
        result = preprocess_reference_audio(stereo, sample_rate=24000, target_sr=24000)
        assert result.audio.ndim == 1


class TestHelpers:
    def test_estimate_audio_duration(self):
        audio = _sine(sr=24000, duration=2.0)
        assert estimate_audio_duration(audio, 24000) == pytest.approx(2.0)

    def test_get_audio_format(self):
        assert get_audio_format("a.wav") == ".wav"
        assert get_audio_format("b.mp3") == ".mp3"
        assert get_audio_format("noext") == ""

    def test_is_supported_audio_format(self):
        assert is_supported_audio_format("a.wav") is True
        assert is_supported_audio_format("a.mp3") is True
        assert is_supported_audio_format("a.xyz") is False


class TestSaveAudioArray:
    def test_save_wav(self, tmp_path):
        audio = _sine(sr=24000, duration=0.5)
        out = tmp_path / "out.wav"
        save_audio_array(audio, str(out), 24000)
        assert out.exists()
        assert out.stat().st_size > 100  # WAV 头 + 数据


class TestAudioQualityResult:
    def test_fields(self):
        result = AudioQualityResult(
            is_valid=True,
            duration=1.0,
            sample_rate=24000,
            peak_db=-20.0,
            rms_db=-30.0,
            has_silence_issues=False,
        )
        assert result.is_valid is True
        assert result.issues == []
        assert result.warnings == []
