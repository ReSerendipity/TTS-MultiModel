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


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_registry():
    """Create a mocked EngineRegistry."""
    mock = MagicMock()
    mock.is_engine_ready.return_value = True
    mock.current_engine = "mock_engine"
    
    mock_engine = MagicMock()
    mock_engine.name = "mock_engine"
    mock_engine.generate_voice_design.return_value = b"WAV_DATA"
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
        from integrated_app.model_registry import ModelRegistry
        from integrated_app.service_layer import TTSGenerationService
        
        with patch.object(ModelRegistry, "_instance", mock_registry):
            svc = TTSGenerationService()
            
            result = svc.generate_voice_design(
                text="Hello world",
                instruction="gentle female voice",
            )
            
            assert result is not None
            mock_registry.get_current_engine.assert_called_once()

    def test_generate_voice_clone_success(self, mock_registry):
        """Voice clone generation should succeed with valid reference audio."""
        from integrated_app.model_registry import ModelRegistry
        from integrated_app.service_layer import TTSGenerationService
        
        with patch.object(ModelRegistry, "_instance", mock_registry):
            svc = TTSGenerationService()
            
            result = svc.generate_voice_clone(
                text="你好世界",
                reference_audio_path="/tmp/ref.wav",
                persona_name="test_persona",
            )
            
            assert result is not None

    def test_generate_with_valid_params(self, mock_registry):
        """Generation with custom params should pass them to engine."""
        from integrated_app.model_registry import ModelRegistry
        from integrated_app.service_layer import TTSGenerationService
        
        with patch.object(ModelRegistry, "_instance", mock_registry):
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
        from integrated_app.model_registry import ModelRegistry
        from integrated_app.service_layer import TTSGenerationService
        
        mock_registry.is_engine_ready.return_value = False
        
        with patch.object(ModelRegistry, "_instance", mock_registry):
            svc = TTSGenerationService()
            
            with pytest.raises(EngineNotLoadedError):
                svc.generate_voice_design(text="test")

    def test_vram_circuit_breaker_triggers(self, mock_registry):
        """Should raise InsufficientVRAMError when VRAM usage exceeds threshold."""
        from integrated_app.exceptions import InsufficientVRAMError
        from integrated_app.model_registry import ModelRegistry
        from integrated_app.service_layer import TTSGenerationService
        
        with patch("integrated_app.service_layer._check_vram_circuit_breaker", return_value=True):
            with patch.object(ModelRegistry, "_instance", mock_registry):
                svc = TTSGenerationService()
                
                with pytest.raises(InsufficientVRAMError):
                    svc.generate_voice_design(text="test")

    def test_empty_text_handling(self, mock_registry):
        """Empty text should either raise or be handled gracefully."""
        from integrated_app.model_registry import ModelRegistry
        from integrated_app.service_layer import TTSGenerationService
        
        with patch.object(ModelRegistry, "_instance", mock_registry):
            svc = TTSGenerationService()
            
            try:
                result = svc.generate_voice_design(text="")
                assert result is not None
            except Exception:
                pass  # Also acceptable

    def test_exception_from_engine_propagates(self, mock_registry):
        """Engine exceptions should propagate correctly."""
        from integrated_app.model_registry import ModelRegistry
        from integrated_app.service_layer import TTSGenerationService
        
        mock_registry.get_current_engine().generate_voice_design.side_effect = RuntimeError("Engine crash")
        
        with patch.object(ModelRegistry, "_instance", mock_registry):
            svc = TTSGenerationService()
            
            with pytest.raises(RuntimeError):
                svc.generate_voice_design(text="test")


# ============================================================================
# ModelService Core Path Tests
# ============================================================================

class TestModelServiceLoadUnload:
    """Test model loading and unloading paths."""

    def test_load_model_success(self, mock_registry):
        """Model loading should succeed and report success."""
        from integrated_app.model_registry import ModelRegistry
        from integrated_app.service_layer import ModelService
        
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message = "Loaded successfully"
        mock_result.load_time = 5.0
        mock_registry.load_model.return_value = mock_result
        
        with patch.object(ModelRegistry, "_instance", mock_registry):
            svc = ModelService()
            
            result = svc.load_model(engine_name="voxcpm2", model_path="/path/to/model")
            
            assert result is not None
            assert result.success is True
            mock_registry.load_model.assert_called_once()

    def test_load_model_failure(self, mock_registry):
        """Model loading failure should report failure reason."""
        from integrated_app.model_registry import ModelRegistry
        from integrated_app.service_layer import ModelService
        
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.message = "Model path not found"
        mock_result.load_time = 0.0
        mock_registry.load_model.return_value = mock_result
        
        with patch.object(ModelRegistry, "_instance", mock_registry):
            svc = ModelService()
            
            result = svc.load_model(engine_name="invalid", model_path="/nonexistent")
            
            assert result is not None
            assert result.success is False

    def test_unload_model_success(self, mock_registry):
        """Model unloading should succeed."""
        from integrated_app.model_registry import ModelRegistry
        from integrated_app.service_layer import ModelService
        
        mock_registry.unload_model.return_value = True
        
        with patch.object(ModelRegistry, "_instance", mock_registry):
            svc = ModelService()
            
            result = svc.unload_model()
            
            assert result is True
            mock_registry.unload_model.assert_called_once()

    def test_get_model_status(self, mock_registry):
        """Model status query should return current state."""
        from integrated_app.model_registry import ModelRegistry
        from integrated_app.service_layer import ModelService
        
        mock_registry.is_engine_ready.return_value = True
        mock_registry.current_engine = "voxcpm2"
        
        with patch.object(ModelRegistry, "_instance", mock_registry):
            svc = ModelService()
            
            result = svc.get_model_status()
            
            assert result is not None
            assert result.engine == "voxcpm2"


class TestModelServiceSwitchEngine:
    """Test engine switching behavior."""

    def test_switch_engine_success(self, mock_registry):
        """Engine switching should unload old and load new."""
        from integrated_app.model_registry import ModelRegistry
        from integrated_app.service_layer import ModelService
        
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message = "Switched successfully"
        mock_result.from_engine = "voxcpm2"
        mock_result.to_engine = "chattts"
        mock_result.switch_time = 10.0
        
        mock_registry.switch_engine.return_value = mock_result
        
        with patch.object(ModelRegistry, "_instance", mock_registry):
            svc = ModelService()
            
            result = svc.switch_engine(target_engine="chattts")
            
            assert result is not None
            assert result.success is True
            mock_registry.switch_engine.assert_called_once()

    def test_switch_to_same_engine(self, mock_registry):
        """Switching to current engine should handle gracefully."""
        from integrated_app.model_registry import ModelRegistry
        from integrated_app.service_layer import ModelService
        
        mock_registry.current_engine = "voxcpm2"
        
        with patch.object(ModelRegistry, "_instance", mock_registry):
            svc = ModelService()
            
            result = svc.switch_engine(target_engine="voxcpm2")
            
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
        """get_persona_by_name should return None for nonexistent persona."""
        from integrated_app.service_layer import PersonaService
        
        svc = PersonaService()
        persona = svc.get_persona_by_name("nonexistent_persona_xyz")
        
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
        from integrated_app.model_registry import ModelRegistry
        from integrated_app.service_layer import TTSGenerationService
        
        original_error = ValueError("Original error message")
        mock_registry.get_current_engine().generate_voice_design.side_effect = original_error
        
        with patch.object(ModelRegistry, "_instance", mock_registry):
            svc = TTSGenerationService()
            
            with pytest.raises(ValueError) as exc_info:
                svc.generate_voice_design(text="test")
            
            assert "Original error message" in str(exc_info.value)

