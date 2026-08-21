"""audio_processing 模块单元测试 — 响度归一化、变速、增强与 VAD 裁切。

覆盖目标模块: app/integrated_app/audio_processing.py
"""

import numpy as np

from integrated_app.audio_processing import (
    AudioEffectsProcessor,
    adjust_tempo,
    apply_voice_enhancement,
    change_tempo,
    denoise_audio,
    enhance_audio,
    normalize_loudness,
    reduce_noise,
    trim_silence_vad,
    trim_tts_output,
)


def _sine(sr=24000, duration=1.0, freq=440.0, amplitude=0.5):
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class TestNormalizeLoudness:
    def test_rms_method(self):
        audio = _sine(amplitude=0.1)
        out = normalize_loudness(audio, 24000, method="rms")
        assert out.shape == audio.shape
        assert out.dtype == np.float32
        assert np.all(np.isfinite(out))

    def test_auto_method(self):
        audio = _sine(amplitude=0.1)
        out = normalize_loudness(audio, 24000, method="auto")
        assert out.shape == audio.shape

    def test_lufs_fallback_when_missing(self, monkeypatch):
        import integrated_app.audio_processing as ap

        monkeypatch.setattr(ap, "_HAS_PYLOUDNORM", False)
        audio = _sine(amplitude=0.1)
        out = normalize_loudness(audio, 24000, method="lufs")
        assert out.shape == audio.shape


class TestTempo:
    def test_adjust_tempo_faster(self):
        audio = _sine()
        out, sr = adjust_tempo(audio, 24000, factor=2.0)
        assert sr == 24000
        assert len(out) < len(audio)

    def test_adjust_tempo_slower(self):
        audio = _sine()
        out, sr = adjust_tempo(audio, 24000, factor=0.5)
        assert len(out) > len(audio)

    def test_adjust_tempo_factor_one_unchanged(self):
        audio = _sine()
        out, sr = adjust_tempo(audio, 24000, factor=1.0)
        assert out is audio

    def test_adjust_tempo_invalid_factor(self):
        audio = _sine()
        out, sr = adjust_tempo(audio, 24000, factor=0)
        assert out is audio

    def test_change_tempo_wrapper(self):
        audio = _sine()
        out = change_tempo(audio, 24000, factor=1.5)
        assert out.dtype == np.float32


class TestVoiceEnhancement:
    def test_enhancement_preserves_shape(self):
        audio = _sine()
        out = apply_voice_enhancement(audio, 24000)
        assert out.shape == audio.shape
        assert np.all(np.isfinite(out))


class TestTrimTTsOutput:
    def test_trim_non_silence(self):
        audio = np.zeros(24000, dtype=np.float32)
        audio[6000:18000] = 0.5
        trimmed = trim_tts_output(audio, 24000)
        assert len(trimmed) > 0
        assert np.max(np.abs(trimmed)) > 0.1

    def test_trim_empty_audio(self):
        trimmed = trim_tts_output(np.array([], dtype=np.float32), 24000)
        assert len(trimmed) == 0


class TestTrimSilenceVad:
    def test_trims_silence_edges(self):
        audio = np.zeros(48000, dtype=np.float32)
        audio[12000:36000] = 0.5  # 中间 0.5s 有效语音
        trimmed = trim_silence_vad(audio, 24000)
        assert len(trimmed) < len(audio)
        assert np.max(np.abs(trimmed)) > 0.4

    def test_pure_silence_returns(self):
        audio = np.zeros(24000, dtype=np.float32)
        trimmed = trim_silence_vad(audio, 24000)
        assert trimmed is not None

    def test_empty_audio(self):
        trimmed = trim_silence_vad(np.array([], dtype=np.float32), 24000)
        assert len(trimmed) == 0


class TestAudioEffectsProcessor:
    def test_no_pedalboard_noop(self, monkeypatch):
        import integrated_app.audio_processing as ap

        monkeypatch.setattr(ap, "_HAS_PEDALBOARD", False)
        proc = AudioEffectsProcessor(sample_rate=24000)
        audio = _sine()
        out = proc.apply(audio, effects=["reverb"])
        assert out.shape == audio.shape

    def test_no_pedalboard_preset_noop(self, monkeypatch):
        import integrated_app.audio_processing as ap

        monkeypatch.setattr(ap, "_HAS_PEDALBOARD", False)
        proc = AudioEffectsProcessor(sample_rate=24000)
        audio = _sine()
        out = proc.apply_preset(audio, "warm")
        assert out.shape == audio.shape

    def test_unknown_effect_skipped(self, monkeypatch):
        import integrated_app.audio_processing as ap

        class _FakePB:
            PitchShift = object
            Reverb = object
            Delay = object
            Chorus = object
            Compressor = object
            Gain = object
            HighpassFilter = object
            LowpassFilter = object
            LowShelfFilter = object
            HighShelfFilter = object

        monkeypatch.setattr(ap, "_HAS_PEDALBOARD", True)
        monkeypatch.setattr(ap, "_pedalboard", _FakePB)
        proc = AudioEffectsProcessor(sample_rate=24000)
        assert proc._build_effect("unknown_effect") is None
        # 已知效果名能创建实例（fake 类对象）
        assert proc._build_effect("gain") is not None


class TestDenoise:
    def test_denoise_without_enhancer(self, monkeypatch):
        import integrated_app.audio_processing as ap

        monkeypatch.setattr(ap, "_HAS_NOISEREDUCE", False)
        audio = _sine()
        out = denoise_audio(audio, 24000)
        assert out.shape == audio.shape

    def test_denoise_empty(self, monkeypatch):
        import integrated_app.audio_processing as ap

        monkeypatch.setattr(ap, "_HAS_NOISEREDUCE", False)
        assert len(denoise_audio(np.array([], dtype=np.float32), 24000)) == 0

    def test_reduce_noise_noop(self):
        audio = _sine()
        out = reduce_noise(audio, 24000)
        assert out.shape == audio.shape

    def test_enhance_audio_preserves_shape(self):
        audio = _sine()
        out = enhance_audio(audio, 24000)
        assert out.shape == audio.shape
