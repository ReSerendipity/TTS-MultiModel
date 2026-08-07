"""Security tests for CSRF, XSS prevention, parameter validation, and path traversal."""

import os
import sys

import pytest

# Ensure integrated_app is importable
_BIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")


class TestCSRFMiddleware:
    """Test CSRF protection middleware."""

    def test_csrf_middleware_import(self):
        """CSRF middleware module can be imported."""
        from integrated_app.middleware.csrf import CSRFMiddleware

        assert CSRFMiddleware is not None

    def test_csrf_cookie_name(self):
        """CSRF cookie name is configured."""
        from integrated_app.middleware.csrf import _CSRF_COOKIE_NAME

        assert _CSRF_COOKIE_NAME == "csrf_token"

    def test_csrf_header_name(self):
        """CSRF header name is configured."""
        from integrated_app.middleware.csrf import _CSRF_HEADER_NAME

        assert _CSRF_HEADER_NAME == "x-csrf-token"

    def test_safe_methods_skip_validation(self):
        """GET/HEAD/OPTIONS are in safe methods set."""
        from integrated_app.middleware.csrf import _SAFE_METHODS

        assert "GET" in _SAFE_METHODS
        assert "HEAD" in _SAFE_METHODS
        assert "OPTIONS" in _SAFE_METHODS
        assert "POST" not in _SAFE_METHODS


class TestTrainingParameterValidation:
    """Test training endpoint parameter validation."""

    def test_validate_valid_params(self):
        """Valid parameters pass validation."""
        from integrated_app.routes.training import _validate_training_params

        body = {
            "learning_rate": 1e-4,
            "num_iters": 2000,
            "batch_size": 1,
            "grad_accum_steps": 1,
            "save_interval": 1000,
            "log_interval": 10,
            "weight_decay": 0.01,
            "warmup_steps": 100,
            "max_grad_norm": 1.0,
            "num_workers": 2,
            "valid_interval": 1000,
        }
        errors = _validate_training_params(body)
        assert errors == []

    def test_validate_invalid_learning_rate(self):
        """Invalid learning_rate is rejected."""
        from integrated_app.routes.training import _validate_training_params

        body = {"learning_rate": 999}
        errors = _validate_training_params(body)
        assert len(errors) > 0
        assert any("learning_rate" in e for e in errors)

    def test_validate_invalid_batch_size(self):
        """Invalid batch_size is rejected."""
        from integrated_app.routes.training import _validate_training_params

        body = {"batch_size": 9999}
        errors = _validate_training_params(body)
        assert len(errors) > 0
        assert any("batch_size" in e for e in errors)

    def test_validate_invalid_num_iters(self):
        """Invalid num_iters is rejected."""
        from integrated_app.routes.training import _validate_training_params

        body = {"num_iters": 999999999}
        errors = _validate_training_params(body)
        assert len(errors) > 0
        assert any("num_iters" in e for e in errors)

    def test_validate_negative_weight_decay(self):
        """Negative weight_decay is rejected."""
        from integrated_app.routes.training import _validate_training_params

        body = {"weight_decay": -0.1}
        errors = _validate_training_params(body)
        assert len(errors) > 0
        assert any("weight_decay" in e for e in errors)

    def test_validate_max_log_length(self):
        """Max log length constant is set."""
        from integrated_app.routes.training import _MAX_LOG_LENGTH

        assert _MAX_LOG_LENGTH > 0
        assert _MAX_LOG_LENGTH == 1_000_000


class TestAudioUploadValidation:
    """Test audio upload content validation."""

    def test_magic_bytes_detection_wav(self):
        """WAV magic bytes are correctly detected."""
        from integrated_app.routes.audio import _validate_audio_content

        # RIFF header for WAV
        wav_header = b"RIFF" + b"\x00" * 12
        assert _validate_audio_content(wav_header, ".wav") is True

    def test_magic_bytes_detection_mp3_id3(self):
        """MP3 with ID3 tag is correctly detected."""
        from integrated_app.routes.audio import _validate_audio_content

        mp3_header = b"ID3" + b"\x00" * 13
        assert _validate_audio_content(mp3_header, ".mp3") is True

    def test_magic_bytes_detection_flac(self):
        """FLAC magic bytes are correctly detected."""
        from integrated_app.routes.audio import _validate_audio_content

        flac_header = b"fLaC" + b"\x00" * 12
        assert _validate_audio_content(flac_header, ".flac") is True

    def test_magic_bytes_mismatch_rejected(self):
        """File with wrong magic bytes for claimed format is rejected."""
        from integrated_app.routes.audio import _validate_audio_content

        # WAV header but claiming to be MP3
        wav_header = b"RIFF" + b"\x00" * 12
        assert _validate_audio_content(wav_header, ".mp3") is False

    def test_unknown_format_rejected(self):
        """Unknown format is rejected (fail-closed validation).

        P0 安全修复：无法通过魔数签名确定音频格式时拒绝上传（fail-closed 白名单模式），
        防止伪装文件上传攻击。
        """
        from integrated_app.routes.audio import _validate_audio_content

        # Random bytes that don't match any known format
        unknown_header = b"\x01\x02\x03\x04" + b"\x00" * 12
        # Should be rejected since we can't identify the format (fail-closed)
        assert _validate_audio_content(unknown_header, ".wav") is False

    def test_m4a_detection(self):
        """M4A/MP4 container is correctly detected."""
        from integrated_app.routes.audio import _validate_audio_content

        # M4A/MP4 container: 00 00 00 XX 66 74 79 70
        m4a_header = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 6
        assert _validate_audio_content(m4a_header, ".m4a") is True


class TestPathTraversal:
    """Test path traversal prevention."""

    def test_validate_path_normal(self):
        """Normal path passes validation."""
        from integrated_app.routes.training import _validate_path

        result = _validate_path("/app", "pretrained_models/model")
        normalized = os.path.normpath(result)
        assert normalized.endswith(os.path.normpath("/app/pretrained_models/model"))

    def test_validate_path_traversal_rejected(self):
        """Path traversal attack is rejected."""
        from integrated_app.routes.training import _validate_path

        with pytest.raises(ValueError, match="Path traversal"):
            _validate_path("/app", "../../etc/passwd")

    def test_validate_path_absolute_rejected(self):
        """Absolute path outside base is rejected."""
        from integrated_app.routes.training import _validate_path

        with pytest.raises(ValueError, match="Path traversal"):
            _validate_path("/app", "/etc/passwd")


class TestPromptCacheSecurity:
    """Test prompt cache uses safe serialization."""

    def test_no_pickle_import(self):
        """prompt_cache module does not import pickle."""
        import integrated_app.prompt_cache as pc

        # Check that pickle is not in the module's namespace
        assert not hasattr(pc, "pickle") or "pickle" not in dir(pc)

    def test_cache_file_extension_is_json(self):
        """Cache files use .json extension, not .pkl."""
        from integrated_app.prompt_cache import _get_cache_file_path

        path = _get_cache_file_path("test_key")
        assert str(path).endswith(".json")

    def test_metadata_extension_is_json(self):
        """Metadata file uses .json extension."""
        from integrated_app.prompt_cache import _get_metadata_path

        path = _get_metadata_path()
        assert str(path).endswith(".json")
