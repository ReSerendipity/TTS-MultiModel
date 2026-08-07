"""Tests for audio processing utilities."""

import os
import sys

import numpy as np

_BIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")


class TestAudioProcessing:
    """Test audio processing functions."""

    def test_module_import(self):
        """Audio processing module can be imported."""
        from integrated_app.audio_processing import normalize_loudness

        assert normalize_loudness is not None

    def test_normalize_loudness_silence(self):
        """Normalizing silence doesn't crash."""
        from integrated_app.audio_processing import normalize_loudness

        silence = np.zeros(24000, dtype=np.float32)
        result = normalize_loudness(silence, target_lufs=-16.0)
        assert result is not None
        assert len(result) == len(silence)

    def test_normalize_loudness_sine(self):
        """Normalizing a sine wave produces non-zero output."""
        from integrated_app.audio_processing import normalize_loudness

        t = np.linspace(0, 1, 24000, dtype=np.float32)
        sine = np.sin(2 * np.pi * 440 * t) * 0.5
        result = normalize_loudness(sine, target_lufs=-16.0)
        assert result is not None
        assert np.max(np.abs(result)) > 0

    def test_normalize_loudness_preserves_shape(self):
        """Normalization preserves the general shape of the waveform."""
        from integrated_app.audio_processing import normalize_loudness

        t = np.linspace(0, 1, 24000, dtype=np.float32)
        sine = np.sin(2 * np.pi * 440 * t) * 0.5
        result = normalize_loudness(sine, target_lufs=-16.0)
        # Cross-correlation should be high (same shape)
        correlation = np.corrcoef(sine, result)[0, 1]
        assert correlation > 0.9


class TestAudioFormatDetection:
    """Test audio format detection via magic bytes."""

    def test_wav_magic_bytes(self):
        """WAV files are detected by RIFF header."""
        from integrated_app.routes.audio import _validate_audio_content

        assert _validate_audio_content(b"RIFF\x00\x00\x00\x00WAVEfmt ", ".wav") is True

    def test_mp3_id3_magic_bytes(self):
        """MP3 files with ID3 tag are detected."""
        from integrated_app.routes.audio import _validate_audio_content

        assert _validate_audio_content(b"ID3\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00", ".mp3") is True

    def test_flac_magic_bytes(self):
        """FLAC files are detected by fLaC header."""
        from integrated_app.routes.audio import _validate_audio_content

        assert _validate_audio_content(b"fLaC\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00", ".flac") is True

    def test_ogg_magic_bytes(self):
        """OGG files are detected by OggS header."""
        from integrated_app.routes.audio import _validate_audio_content

        assert _validate_audio_content(b"OggS\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00", ".ogg") is True

    def test_mismatch_rejected(self):
        """File with wrong magic bytes for claimed format is rejected."""
        from integrated_app.routes.audio import _validate_audio_content

        assert _validate_audio_content(b"RIFF\x00\x00\x00\x00WAVEfmt ", ".mp3") is False

    def test_unknown_format_rejected(self):
        """Unknown format is rejected (fail-closed validation).

        P0 安全修复：无法通过魔数签名确定音频格式时拒绝上传（fail-closed 白名单模式）。
        """
        from integrated_app.routes.audio import _validate_audio_content

        assert (
            _validate_audio_content(b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10", ".wav")
            is False
        )


class TestEnhanceAudioPipeline:
    """[H-R6] Test enhance_audio memory optimization preserves caller input."""

    @staticmethod
    def _sample():
        rng = np.random.default_rng(0)
        a = (rng.standard_normal(24000 * 2).astype(np.float32)) * 0.3
        a[:2000] = 0.0
        a[-2000:] = 0.0
        return a

    def test_noop_returns_input_unchanged(self):
        """With every stage disabled, the input is returned untouched (zero-copy)."""
        from integrated_app.audio_processing import enhance_audio

        a = self._sample()
        before = a.copy()
        out = enhance_audio(a, 24000, normalize=False)
        assert np.array_equal(a, before)  # input never mutated
        assert out is a  # no-op fast path avoids an allocation

    def test_normalize_only_does_not_mutate_input(self):
        """Normalization returns a new array and leaves the input intact."""
        from integrated_app.audio_processing import enhance_audio

        a = self._sample()
        before = a.copy()
        out = enhance_audio(a, 24000, normalize=True)
        assert np.array_equal(a, before)
        assert out is not a

    def test_trim_silence_does_not_mutate_input(self):
        """trim_silence (in-place fade) must not corrupt the caller's buffer."""
        from integrated_app.audio_processing import enhance_audio

        # normalize=False forces the defensive copy guard before trim
        a = self._sample()
        before = a.copy()
        out = enhance_audio(a, 24000, normalize=False, trim_silence=True)
        assert np.array_equal(a, before)
        assert len(out) <= len(a)

    def test_full_pipeline_does_not_mutate_input(self):
        """Full pipeline (normalize + enhance + trim + tempo) preserves the input."""
        from integrated_app.audio_processing import enhance_audio

        a = self._sample()
        before = a.copy()
        out = enhance_audio(
            a,
            24000,
            normalize=True,
            voice_enhancement=True,
            trim_silence=True,
            tempo_factor=1.1,
        )
        assert np.array_equal(a, before)
        assert out is not a

    def test_empty_input(self):
        """Empty input does not raise across stages."""
        from integrated_app.audio_processing import enhance_audio

        empty = np.zeros(0, dtype=np.float32)
        out = enhance_audio(empty, 24000, normalize=True, trim_silence=True)
        assert out is not None
