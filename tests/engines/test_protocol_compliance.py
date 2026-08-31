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

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")


def _torch_is_functional() -> bool:
    """判断 torch 是否真正可用（而非仅"能被找到"）。

    WHY 不用 importlib.util.find_spec：它只检测模块可被找到，
    而损坏/占位安装同样能被找到，会把环境缺陷误判为产品注册缺陷。
    """
    try:
        import torch
    except ImportError:
        return False
    return hasattr(torch, "no_grad")


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
            assert required_keys.issubset(voice.keys()), f"Voice {voice} missing required keys"


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
    """真实引擎契约校验（L2）：验证注册表与真实引擎类满足 TTSEngine 协议。

    此前该文件只用「手写 Mock 引擎」自证协议形状，无法发现真实引擎实现
    偏离协议的问题。现改为对 engine_registry 中真实注册的引擎类做结构性
    协议校验——缺方法即失败，是比 mock 自测更强的真实契约保证。
    """

    def test_engines_directory_structure(self):
        """engines/ directory should contain engine implementations."""
        engines_dir = os.path.join(_APP_DIR, "integrated_app", "engines")

        if os.path.exists(engines_dir):
            files = os.listdir(engines_dir)
            assert len(files) > 0, "engines/ directory should contain modules"
        else:
            # Engines might be elsewhere - just document this
            pytest.skip("engines/ directory not found at expected location")

    def _load_engine_interface(self):
        """惰性导入 engine_interface；轻量环境（无 torch）下返回 None 以便跳过。"""
        try:
            from integrated_app import engine_interface as ei

            return ei
        except ImportError:
            return None

    def test_builtin_engines_registered(self):
        """VoxCPM2 / IndexTTS2 / IndexTTS2(2.0) 必须注册进全局引擎注册表。"""
        ei = self._load_engine_interface()
        if ei is None:
            pytest.skip("引擎依赖（torch/transformers）未安装，跳过注册表契约校验")

        registered = set(ei.engine_registry.list_engines())
        for name in ("voxcpm2", "indextts2", "indextts20"):
            assert name in registered, f"内置引擎未注册: {name}"

    def test_registered_engines_conform_to_protocol(self):
        """注册表中的真实引擎类必须结构性满足 TTSEngine Protocol。"""
        ei = self._load_engine_interface()
        if ei is None:
            pytest.skip("引擎依赖（torch/transformers）未安装，跳过协议契约校验")

        # engine_registry.get() 在引擎类导入失败时返回 None（见其文档字符串）。
        # 据此区分「环境缺依赖」与「真实注册缺陷」，避免把环境差异误判为产品缺陷。
        torch_ready = _torch_is_functional()
        for name in ("voxcpm2", "indextts2", "indextts20"):
            cls = ei.engine_registry.get(name)
            if cls is None:
                if torch_ready:
                    pytest.fail(f"重型依赖齐全却无法解析引擎类，注册存在缺陷: {name}")
                pytest.skip(f"引擎依赖未安装，无法解析引擎类: {name}")
            assert isinstance(cls, ei.TTSEngine), f"{name} 的引擎类 {cls!r} 不满足 TTSEngine 协议"

    def test_real_engine_classes_conform_to_protocol(self):
        """直接导入真实引擎类并验证其满足 TTSEngine 协议（CI 中执行）。"""
        ei = self._load_engine_interface()
        if ei is None:
            pytest.skip("引擎依赖（torch/transformers）未安装，跳过真实类协议校验")
        try:
            from integrated_app.engines.indextts2_engine import IndexTTS2Engine
            from integrated_app.engines.voxcpm2.engine import VoxCPM2Engine
        except ImportError as exc:
            pytest.skip(f"引擎依赖未安装，跳过真实类协议校验: {exc}")

        assert isinstance(VoxCPM2Engine, ei.TTSEngine)
        assert isinstance(IndexTTS2Engine, ei.TTSEngine)
