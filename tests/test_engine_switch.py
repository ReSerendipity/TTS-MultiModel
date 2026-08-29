from unittest.mock import MagicMock, patch

import pytest

from integrated_app.exceptions import EngineSwitchError, InsufficientVRAMError
from integrated_app.model_registry import EngineName


class TestEngineSwitch:
    def test_rejects_unknown_engine(self):
        with pytest.raises(EngineSwitchError):
            from integrated_app.model_manager import switch_engine

            gen = switch_engine("unknown_engine")
            list(gen)

    def test_vram_check_raises_insufficient(self):
        with patch("integrated_app.gpu_backend.GPUBackendManager") as mock_gpu:
            mock_gpu.detect_backend.return_value = MagicMock(value="cuda")
            mock_gpu.get_device_properties.return_value = {"total_memory": 8 * 1024**3}
            mock_gpu.memory_allocated.return_value = 7 * 1024**3
            with (
                patch("integrated_app.model_manager_core.state.get_gpu_device", return_value=0),
                pytest.raises(InsufficientVRAMError),
            ):
                from integrated_app.model_manager import switch_engine

                gen = switch_engine("voxcpm2")
                list(gen)

    def test_engine_name_enum_values(self):
        assert EngineName.VOXCPM2.value == "voxcpm2"
        assert EngineName.INDEXTTS2.value == "indextts2"

    def test_registry_initial_state(self):
        from integrated_app.model_registry import ModelRegistry

        ModelRegistry._reset()
        r = ModelRegistry()
        assert r.voxcpm_model is None
        assert r.indextts2_engine is None
        assert r.current_engine is None
        assert r.model_loaded is False
        ModelRegistry._reset()
