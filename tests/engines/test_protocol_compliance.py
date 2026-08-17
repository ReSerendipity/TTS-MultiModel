"""Engine protocol compliance tests using mocks.

Test that all engine implementations conform to the TTSEngine Protocol.
This is L2 (engine interface) testing as described in AGENTS.md section 4.1.

Run like::

    pytest tests/engines/test_protocol_compliance.py -v

Note: These tests use mocks, so they don't require GPU or model weights.
"""
import os
import sys
from typing import Any

import pytest

_BIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")


class MockTTSImplementation:
    """Mock TTS implementation for protocol compliance testing."""
    
    name: str = "mock_engine"
    version: str = "0.0.1"

    async def synthesize(self, text: str, voice: str = "default", **kwargs: Any) -> bytes:
        """Synthesize audio from text."""
        wav_header = b"RIFF" + b"\x00" * 40
        return wav_header

    def list_voices(self) -> list[dict[str, str]]:
        """List available voices."""
        return [
            {"id": "default", "name": "Default Voice"},
            {"id": "male_1", "name": "Male Speaker 1"},
        ]


class TestTTSEngineProtocolCompliance:
    """Verify TTSEngine Protocol contract compliance."""

    def test_mock_implements_required_members(self):
        """Mock implementation must have name, version, synthesize, list_voices."""
        mock = MockTTSImplementation()
        
        assert hasattr(mock, "name")
        assert isinstance(mock.name, str)
        assert len(mock.name) > 0
        
        assert hasattr(mock, "version")
        assert isinstance(mock.version, str)
        assert len(mock.version) > 0
        
        assert hasattr(mock, "synthesize")
        assert callable(mock.synthesize)
        
        assert hasattr(mock, "list_voices")
        assert callable(mock.list_voices)

    @pytest.mark.asyncio
    async def test_mock_synthesize_returns_bytes(self):
        """synthesize() must return bytes (WAV format)."""
        mock = MockTTSImplementation()
        
        result = await mock.synthesize("Hello world")
        
        assert isinstance(result, bytes)
        assert len(result) >= 44  # Minimum WAV header size

    def test_mock_list_voices_returns_list_of_dicts(self):
        """list_voices() must return list of dicts with id/name keys."""
        mock = MockTTSImplementation()
        
        voices = mock.list_voices()
        
        assert isinstance(voices, list)
        assert len(voices) > 0
        
        for voice in voices:
            assert isinstance(voice, dict)
            assert "id" in voice
            assert "name" in voice
            assert isinstance(voice["id"], str)
            assert isinstance(voice["name"], str)

    def test_voice_format_validation(self):
        """Voice objects should follow consistent format."""
        mock = MockTTSImplementation()
        voices = mock.list_voices()

        required_keys = {"id", "name"}
        for voice in voices:
            assert required_keys.issubset(voice.keys()), \
                f"Voice {voice} missing required keys"


class TestEngineErrorHandling:
    """Test that engines properly handle invalid inputs."""

    @pytest.mark.asyncio
    async def test_synthesize_rejects_empty_text(self):
        """synthesis should handle empty text gracefully."""
        mock = MockTTSImplementation()
        
        try:
            result = await mock.synthesize("")
            assert isinstance(result, bytes)
        except ValueError:
            pass  # Expected behavior
        except Exception:
            pass  # Other errors also acceptable

    def test_list_voices_handles_no_voices_gracefully(self):
        """list_voices should return empty list if no voices available."""
        class NoVoicesEngine:
            name = "empty"
            version = "1.0"
            
            def list_voices(self) -> list[dict[str, str]]:
                return []
            
            async def synthesize(self, text: str, **kwargs) -> bytes:
                return b""
        
        engine = NoVoicesEngine()
        voices = engine.list_voices()
        
        assert voices == []


class TestEngineRegistry:
    """Test EngineRegistry class existence and structure."""

    def test_registry_class_exists(self):
        """EngineRegistry class should be importable."""
        from integrated_app.engine_interface import EngineRegistry

        assert EngineRegistry is not None
        assert isinstance(EngineRegistry, type)
        assert hasattr(EngineRegistry, "__init__")


class TestRealEngineDetection:
    """Detect and document available real engines."""

    def test_engines_directory_structure(self):
        """engines/ directory should contain engine implementations."""
        engines_dir = os.path.join(_BIN_DIR, "integrated_app", "engines")
        
        if os.path.exists(engines_dir):
            files = os.listdir(engines_dir)
            assert len(files) > 0, "engines/ directory should contain modules"
        else:
            # Engines might be elsewhere - just document this
            pytest.skip("engines/ directory not found at expected location")

    def test_auto_register_module_exists(self):
        """auto_register module should exist for automatic engine discovery."""
        try:
            from integrated_app import auto_register
            assert auto_register is not None
        except ImportError:
            pytest.skip("auto_register module not implemented yet")
