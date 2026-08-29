"""Tests for exceptions.py hierarchy and decorator."""

import asyncio

import pytest

from integrated_app.exceptions import (
    EngineSwitchError,
    GenerationError,
    InsufficientVRAMError,
    ModelLoadError,
    TTSError,
    ValidationError,
    tts_error_handler,
)


class TestExceptionHierarchy:
    def test_all_inherit_from_tts_error(self):
        assert issubclass(ModelLoadError, TTSError)
        assert issubclass(InsufficientVRAMError, TTSError)
        assert issubclass(GenerationError, TTSError)
        assert issubclass(EngineSwitchError, TTSError)
        assert issubclass(ValidationError, TTSError)

    def test_error_code(self):
        e = InsufficientVRAMError("test")
        assert e.code == "INSUFFICIENT_VRAM"
        assert e.error_code == "INSUFFICIENT_VRAM"  # backward compat alias

    def test_validation_error_has_field(self):
        e = ValidationError("invalid", field="text")
        assert e.field == "text"
        assert e.code == "VALIDATION_ERROR"

    def test_generation_error_has_engine(self):
        e = GenerationError("failed", engine="voxcpm2")
        assert e.engine == "voxcpm2"


class TestTTSErrorHandler:
    def test_sync_reraise_tts_error(self):
        @tts_error_handler
        def fn():
            raise InsufficientVRAMError("oom")

        with pytest.raises(InsufficientVRAMError):
            fn()

    def test_sync_wraps_unknown_error(self):
        @tts_error_handler
        def fn():
            raise ValueError("bad")

        with pytest.raises(GenerationError):
            fn()

    def test_async_wraps_unknown_error(self):
        @tts_error_handler
        async def fn():
            raise ValueError("bad")

        with pytest.raises(GenerationError):
            asyncio.run(fn())

    def test_sync_success(self):
        @tts_error_handler
        def fn():
            return "ok"

        assert fn() == "ok"
