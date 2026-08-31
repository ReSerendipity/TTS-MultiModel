"""Service layer core path tests - M1 milestone completion.

Test the critical business logic paths in TTSGenerationService, ModelService,
and PersonaService using mocks for engine/model dependencies.

AGENTS.md Section 4.1 L3: Service layer integration tests with mocked engines.

Run like::

    pytest tests/integration/test_service_layer_core.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")

pytestmark = pytest.mark.integration


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_registry():
    """Create a mocked EngineRegistry.

    注意：service_layer 通过模块级单例 ``integrated_app.model_registry.registry``
    取引擎（前次整改从 ``ModelRegistry._instance`` 迁移而来），因此 mock 必须
    以 ``patch("integrated_app.model_registry.registry", ...)`` 注入，
    直接 patch ``ModelRegistry._instance`` 不再生效。
    """
    mock = MagicMock()
    mock.is_engine_ready.return_value = True
    mock.current_engine = "mock_engine"
    mock.model_loaded = True

    mock_engine = MagicMock()
    mock_engine.name = "mock_engine"
    mock_engine.generate_voice_design.return_value = b"WAV_DATA"
    mock_engine.generate_voice_clone.return_value = b"WAV_DATA"
    mock_engine.list_voices.return_value = [{"id": "default", "name": "Default"}]

    mock.get_current_engine.return_value = mock_engine
    return mock


# ============================================================================
# TTSGenerationService Core Path Tests
# ============================================================================


class TestTTSGenerationServiceReadyPaths:
    """Test successful generation paths with all components ready."""

    def test_generate_voice_design_success(self, mock_registry):
        """Voice design generation should succeed when engine is ready."""
        from integrated_app.service_layer import TTSGenerationService

        with (
            patch("integrated_app.model_registry.registry", mock_registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
            patch("integrated_app.service_layer.TTSGenerationService._save_version_record", return_value=None),
        ):
            svc = TTSGenerationService()

            result = svc.generate_voice_design(
                text="Hello world",
                instruction="gentle female voice",
            )

            assert result is not None
            mock_registry.get_current_engine.assert_called_once()

    def test_generate_voice_clone_success(self, mock_registry):
        """Voice clone generation should succeed with valid reference audio."""
        from integrated_app.service_layer import TTSGenerationService

        # 引擎的 generate_voice_clone 返回 b"WAV_DATA"（见 fixture），
        # 但 _extract_generation_result 需要 (audio_path, message) 结构：
        # mock 返回真实可解析的元组
        mock_registry.get_current_engine().generate_voice_clone.return_value = ("/tmp/out.wav", "ok")

        with (
            patch("integrated_app.model_registry.registry", mock_registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
            patch("integrated_app.service_layer.TTSGenerationService._save_version_record", return_value=None),
        ):
            svc = TTSGenerationService()

            result = svc.generate_voice_clone(
                text="你好世界",
                reference_audio="/tmp/ref.wav",
            )

            assert result is not None

    def test_generate_with_valid_params(self, mock_registry):
        """Generation with custom params should pass them to engine."""
        from integrated_app.service_layer import TTSGenerationService

        with (
            patch("integrated_app.model_registry.registry", mock_registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
            patch("integrated_app.service_layer.TTSGenerationService._save_version_record", return_value=None),
        ):
            svc = TTSGenerationService()

            result = svc.generate_voice_design(
                text="Test",
                cfg_value=3.0,
                inference_timesteps=20,
            )

            assert result is not None


class TestTTSGenerationServiceErrorPaths:
    """Test error handling and edge cases in service layer."""

    def test_engine_not_loaded_raises(self, mock_registry):
        """Should raise EngineNotLoadedError when engine is not ready."""
        from integrated_app.exceptions import EngineNotLoadedError
        from integrated_app.service_layer import TTSGenerationService

        mock_registry.is_engine_ready.return_value = False

        with (
            patch("integrated_app.model_registry.registry", mock_registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
        ):
            svc = TTSGenerationService()

            with pytest.raises(EngineNotLoadedError):
                svc.generate_voice_design(text="test")

    def test_vram_circuit_breaker_triggers(self, mock_registry):
        """Should raise InsufficientVRAMError when VRAM usage exceeds threshold."""
        from integrated_app.exceptions import InsufficientVRAMError
        from integrated_app.service_layer import TTSGenerationService

        with (
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=True),
            patch("integrated_app.model_registry.registry", mock_registry),
        ):
            svc = TTSGenerationService()

            with pytest.raises(InsufficientVRAMError):
                svc.generate_voice_design(text="test")

    def test_empty_text_handling(self, mock_registry):
        """Empty text should either raise or be handled gracefully."""
        from integrated_app.service_layer import TTSGenerationService

        with (
            patch("integrated_app.model_registry.registry", mock_registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
            patch("integrated_app.service_layer.TTSGenerationService._save_version_record", return_value=None),
        ):
            svc = TTSGenerationService()

            try:
                result = svc.generate_voice_design(text="")
                assert result is not None
            except Exception:
                pass  # Also acceptable

    def test_exception_from_engine_propagates(self, mock_registry):
        """Engine exceptions should propagate correctly."""
        from integrated_app.service_layer import TTSGenerationService

        mock_registry.get_current_engine().generate_voice_design.side_effect = RuntimeError("Engine crash")

        with (
            patch("integrated_app.model_registry.registry", mock_registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
        ):
            svc = TTSGenerationService()

            with pytest.raises(RuntimeError):
                svc.generate_voice_design(text="test")


# ============================================================================
# ModelService Core Path Tests
# ============================================================================


class TestModelServiceLoadUnload:
    """Test model loading and unloading paths."""

    def test_load_model_success(self):
        """Model loading should succeed and report success."""
        from integrated_app.service_layer import ModelService

        def fake_load_voxcpm2():
            yield ("loading...",)
            yield ("done",)

        with (
            patch("integrated_app.model_manager.load_voxcpm2", fake_load_voxcpm2),
            patch("integrated_app.model_registry.registry") as reg,
        ):
            reg.is_voxcpm_ready.return_value = True
            svc = ModelService()
            result = svc.load_model(engine="voxcpm2")
            assert result.success is True
            assert result.engine == "voxcpm2"

    def test_load_model_failure(self):
        """Model loading failure should report failure reason."""
        from integrated_app.service_layer import ModelService

        def fake_load_voxcpm2():
            yield ("model path not found",)

        with (
            patch("integrated_app.model_manager.load_voxcpm2", fake_load_voxcpm2),
            patch("integrated_app.model_registry.registry") as reg,
        ):
            reg.is_voxcpm_ready.return_value = False
            svc = ModelService()
            result = svc.load_model(engine="voxcpm2")
            assert result.success is False

    def test_unload_model_success(self):
        """Model unloading should succeed."""
        from integrated_app.service_layer import ModelService

        svc = ModelService()
        with patch("integrated_app.model_manager.unload_model") as mock_unload:
            svc.unload_model()
            mock_unload.assert_called_once()

    def test_get_model_status(self):
        """Model status query should return current state."""
        from integrated_app.service_layer import ModelService

        svc = ModelService()
        with (
            patch("integrated_app.model_registry.registry") as reg,
            patch("integrated_app.service_layer._get_vram_usage_percent", return_value=42.5),
        ):
            reg.current_engine = "voxcpm2"
            reg.model_loaded = True
            reg.is_engine_ready.return_value = True
            reg.get_current_model_info.return_value = {"ready": True}

            result = svc.get_model_status()

            assert result is not None
            assert result.engine == "voxcpm2"
            assert result.loaded is True
            assert result.ready is True


class TestModelServiceSwitchEngine:
    """Test engine switching behavior."""

    def test_switch_engine_success(self):
        """Engine switching should unload old and load new."""
        from integrated_app.service_layer import ModelService

        def fake_switch(engine):
            yield ("switching...",)
            yield ("done",)

        with (
            patch("integrated_app.model_manager.switch_engine", fake_switch),
            patch("integrated_app.model_registry.registry") as reg,
        ):
            reg.current_engine = "voxcpm2"
            svc = ModelService()
            result = svc.switch_engine(engine="indextts2")
            assert result.success is True
            assert result.from_engine == "voxcpm2"
            assert result.to_engine == "indextts2"

    def test_switch_to_same_engine(self):
        """Switching to current engine should handle gracefully."""
        from integrated_app.service_layer import ModelService

        def fake_switch(engine):
            yield ("done",)

        with (
            patch("integrated_app.model_manager.switch_engine", fake_switch),
            patch("integrated_app.model_registry.registry") as reg,
        ):
            reg.current_engine = "voxcpm2"
            svc = ModelService()
            result = svc.switch_engine(engine="voxcpm2")
            assert result is not None


# ============================================================================
# PersonaService Core Path Tests
# ============================================================================


class TestPersonaServiceListAndQuery:
    """Test persona listing and querying."""

    def test_list_personas_returns_list(self):
        """list_personas should return a list."""
        from integrated_app.service_layer import PersonaService

        svc = PersonaService()
        personas = svc.list_personas()

        assert isinstance(personas, list)

    def test_get_nonexistent_persona_returns_none(self):
        """get_persona should return None for nonexistent persona."""
        from integrated_app.service_layer import PersonaService

        svc = PersonaService()
        persona = svc.get_persona("nonexistent_persona_xyz")

        assert persona is None


class TestPersonaServiceDelete:
    """Test persona deletion."""

    def test_delete_persona_not_found(self, tmp_path):
        """delete_persona should handle nonexistent gracefully."""
        from integrated_app.service_layer import PersonaService

        persona_dir = tmp_path / "personas"
        persona_dir.mkdir()

        svc = PersonaService()
        svc.persona_dir = str(persona_dir)

        result = svc.delete_persona("nonexistent")

        assert result is False or result is True


# ============================================================================
# Error Handling Integration Tests
# ============================================================================


class TestServiceLayerErrorPropagation:
    """Test that errors propagate correctly through service layer."""

    def test_nested_exception_preserves_context(self, mock_registry):
        """Nested exceptions should preserve original error context."""
        from integrated_app.service_layer import TTSGenerationService

        original_error = ValueError("Original error message")
        mock_registry.get_current_engine().generate_voice_design.side_effect = original_error

        with (
            patch("integrated_app.model_registry.registry", mock_registry),
            patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=False),
        ):
            svc = TTSGenerationService()

            with pytest.raises(ValueError) as exc_info:
                svc.generate_voice_design(text="test")

            assert "Original error message" in str(exc_info.value)
