"""routes/audio.py 单元测试 — 音频服务路由辅助函数。

覆盖目标模块: app/integrated_app/routes/audio.py
"""

import pytest

from integrated_app.routes.audio import (
    _build_content_disposition,
    _safe_file_path,
    _validate_audio_content,
    _validate_ids,
)


class TestContentDisposition:
    def test_ascii_filename(self):
        header = _build_content_disposition("audio.wav")
        assert "attachment" in header
        assert "audio.wav" in header

    def test_chinese_filename(self):
        header = _build_content_disposition("测试.wav")
        assert header  # RFC5987 编码

    def test_inline_disposition(self):
        header = _build_content_disposition("audio.wav", disposition="inline")
        assert "inline" in header


class TestSafeFilePath:
    def test_normal_path(self, tmp_path):
        root = tmp_path
        path = _safe_file_path(root, "audio.wav")
        assert path.is_absolute()
        assert path.name == "audio.wav"

    def test_illegal_characters_rejected(self, tmp_path):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _safe_file_path(tmp_path, "../secret.txt")

    def test_absolute_path_rejected(self, tmp_path):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _safe_file_path(tmp_path, "/etc/passwd")

    def test_empty_rejected(self, tmp_path):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            _safe_file_path(tmp_path, "")


class TestValidateAudioContent:
    def test_wav_header(self):
        wav_bytes = b"RIFF\x00\x00\x00\x00WAVEfmt "
        assert _validate_audio_content(wav_bytes, ".wav") is True

    def test_mp3_header(self):
        mp3_bytes = b"ID3\x04\x00\x00\x00\x00\x00\x00"
        assert _validate_audio_content(mp3_bytes, ".mp3") is True

    def test_mismatch_rejected(self):
        assert _validate_audio_content(b"RIFF\x00\x00\x00\x00WAVE", ".mp3") is False

    def test_empty_rejected(self):
        assert _validate_audio_content(b"", ".wav") is False

    def test_unknown_magic_rejected(self):
        assert _validate_audio_content(b"GIF89a\x00\x00\x00\x00", ".wav") is False


class TestValidateIds:
    def test_valid_int_list(self):
        ids, error = _validate_ids([1, 2, 3])
        assert ids == [1, 2, 3]
        assert error is None

    def test_string_ids_rejected(self):
        ids, error = _validate_ids(["a", "b"])
        assert error is not None

    def test_non_list_rejected(self):
        ids, error = _validate_ids("single")
        assert error is not None

    def test_empty(self):
        ids, error = _validate_ids([])
        assert error is not None

    def test_too_many(self):
        ids, error = _validate_ids([i for i in range(501)])
        assert error is not None
