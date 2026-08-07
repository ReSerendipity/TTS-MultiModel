"""resampling 模块单元测试 — 音频重采样。

覆盖目标模块: bin/integrated_app/resampling.py
"""

import numpy as np
import pytest

from integrated_app.exceptions import AudioProcessingError
from integrated_app.resampling import (
    ResampleBackend,
    ResamplingConfig,
    _to_mono,
    detect_sample_rate,
    get_default_pipeline,
    normalize_sample_rate,
    reset_default_pipeline,
)


def _sine(sr=24000, duration=1.0, freq=440.0):
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class TestToMono:
    def test_mono_unchanged(self):
        audio = _sine()
        mono = _to_mono(audio)
        assert mono.shape == audio.shape

    def test_stereo_mixed_down(self):
        stereo = np.stack([_sine(), _sine()], axis=-1)
        mono = _to_mono(stereo)
        assert mono.ndim == 1
        assert mono.shape[0] == stereo.shape[0]


class TestNormalizeSampleRate:
    def test_same_rate_unchanged(self):
        audio = _sine(sr=24000)
        out = normalize_sample_rate(audio, 24000, 24000)
        assert out.shape == audio.shape

    def test_upsample(self):
        audio = _sine(sr=16000, duration=1.0)
        out = normalize_sample_rate(audio, 16000, 24000)
        assert len(out) > len(audio)

    def test_downsample(self):
        audio = _sine(sr=48000, duration=1.0)
        out = normalize_sample_rate(audio, 48000, 16000)
        assert len(out) < len(audio)

    def test_empty_audio(self):
        with pytest.raises(AudioProcessingError):
            normalize_sample_rate(np.array([], dtype=np.float32), 24000, 16000)

    def test_nan_sanitized(self):
        audio = _sine()
        audio[0] = np.nan
        audio[1] = np.inf
        out = normalize_sample_rate(audio, 24000, 24000)
        assert np.all(np.isfinite(out))

    def test_invalid_source_sr(self):
        with pytest.raises(AudioProcessingError):
            normalize_sample_rate(_sine(), 0, 16000)

    def test_stereo_forced_mono(self):
        stereo = np.stack([_sine(), _sine()], axis=-1)
        out = normalize_sample_rate(stereo, 24000, 24000)
        assert out.ndim == 1

    def test_clip_output(self):
        audio = np.full(24000, 2.0, dtype=np.float32)
        out = normalize_sample_rate(audio, 24000, 24000)
        assert np.max(out) <= 1.0


class TestDetectSampleRate:
    def test_returns_reasonable_rate(self):
        rate = detect_sample_rate(24000, 1.0)
        assert 16000 <= rate <= 48000


class TestConfigAndPipeline:
    def test_resampling_config_defaults(self):
        cfg = ResamplingConfig()
        assert cfg.target_sr > 0

    def test_backend_enum(self):
        assert ResampleBackend.AUTO.value == "auto"

    def test_default_pipeline_singleton(self):
        p1 = get_default_pipeline()
        p2 = get_default_pipeline()
        assert p1 is p2
        reset_default_pipeline()
        assert get_default_pipeline() is not None
