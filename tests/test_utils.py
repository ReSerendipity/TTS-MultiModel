"""共享工具函数测试"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")


class TestValidateAudioUpload:
    """测试音频上传验证"""

    @pytest.mark.asyncio
    async def test_valid_wav_file(self):
        from integrated_app.routes.generate.utils import validate_audio_upload

        mock_file = MagicMock()
        mock_file.filename = "test.wav"
        # P0 安全修复：使用有效的 WAV 魔数签名（RIFF...WAVE）以通过 fail-closed 校验
        wav_header = b"RIFF" + b"\x00" * 4 + b"WAVEfmt " + b"x" * 92
        mock_file.read = AsyncMock(return_value=wav_header)
        mock_file.seek = AsyncMock()
        is_valid, error = await validate_audio_upload(mock_file)
        assert is_valid is True
        assert error == ""

    @pytest.mark.asyncio
    async def test_invalid_extension(self):
        from integrated_app.routes.generate.utils import validate_audio_upload

        mock_file = MagicMock()
        mock_file.filename = "test.exe"
        mock_file.read = AsyncMock(return_value=b"x" * 100)
        mock_file.seek = AsyncMock()
        is_valid, error = await validate_audio_upload(mock_file)
        assert is_valid is False
        assert "不支持" in error

    @pytest.mark.asyncio
    async def test_no_file(self):
        from integrated_app.routes.generate.utils import validate_audio_upload

        is_valid, error = await validate_audio_upload(None)
        assert is_valid is False

    @pytest.mark.asyncio
    async def test_empty_filename(self):
        from integrated_app.routes.generate.utils import validate_audio_upload

        mock_file = MagicMock()
        mock_file.filename = ""
        is_valid, error = await validate_audio_upload(mock_file)
        assert is_valid is False


class TestValidateTextInput:
    """测试文本输入验证"""

    def test_valid_text(self):
        from integrated_app.routes.generate.utils import validate_text_input

        is_valid, error = validate_text_input("你好世界")
        assert is_valid is True
        assert error == ""

    def test_empty_text(self):
        from integrated_app.routes.generate.utils import validate_text_input

        is_valid, error = validate_text_input("")
        assert is_valid is False
        assert "请输入" in error

    def test_whitespace_only(self):
        from integrated_app.routes.generate.utils import validate_text_input

        is_valid, error = validate_text_input("   ")
        assert is_valid is False

    def test_too_long_text(self):
        from integrated_app.routes.generate.utils import validate_text_input

        long_text = "a" * 5001
        is_valid, error = validate_text_input(long_text, max_length=5000)
        assert is_valid is False
        assert "过长" in error
