"""Tests for GPU utility functions."""

import os
import sys

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")


class TestGPUBackendManager:
    """Test GPUBackendManager with strategy pattern."""

    def test_module_import(self):
        """GPU backend module can be imported."""
        from integrated_app.gpu_backend import GPUBackendManager

        assert GPUBackendManager is not None

    def test_gpu_backend_enum(self):
        """GPUBackend enum has expected values."""
        from integrated_app.gpu_backend import GPUBackend

        assert GPUBackend.CUDA.value == "cuda"
        assert GPUBackend.ROCM.value == "rocm"
        assert GPUBackend.XPU.value == "xpu"
        assert GPUBackend.MPS.value == "mps"
        assert GPUBackend.CPU.value == "cpu"

    def test_detect_backend_returns_enum(self):
        """detect_backend returns a GPUBackend enum value."""
        from integrated_app.gpu_backend import GPUBackend, GPUBackendManager

        backend = GPUBackendManager.detect_backend()
        assert isinstance(backend, GPUBackend)

    def test_get_device_returns_torch_device(self):
        """get_device returns a torch.device object."""
        import torch

        from integrated_app.gpu_backend import GPUBackendManager

        device = GPUBackendManager.get_device()
        assert isinstance(device, torch.device)

    def test_strategy_dispatch(self):
        """Strategy dispatch mechanism works."""
        from integrated_app.gpu_backend import GPUBackend, GPUBackendManager

        # Test that get_strategy returns a strategy class
        strategy = GPUBackendManager.get_strategy(GPUBackend.CPU)
        assert strategy is not None
        assert hasattr(strategy, "get_device")
        assert hasattr(strategy, "get_memory_info")

    def test_cpu_strategy_defaults(self):
        """CPU strategy returns safe defaults."""
        from integrated_app.gpu_backend import _CPUStrategy

        assert _CPUStrategy.get_device_count() == 0
        assert _CPUStrategy.memory_allocated() == 0
        assert _CPUStrategy.memory_reserved() == 0
        assert _CPUStrategy.get_memory_info() == (0, 0, 0, 0)
        assert _CPUStrategy.get_autocast_device_type() == "cpu"
        assert _CPUStrategy.get_process_group_backend() == "gloo"

    def test_format_device_string(self):
        """format_device_string returns valid device strings."""
        from integrated_app.gpu_backend import GPUBackendManager

        device_str = GPUBackendManager.format_device_string()
        assert isinstance(device_str, str)
        assert len(device_str) > 0

    def test_is_available_returns_bool(self):
        """is_available returns a boolean."""
        from integrated_app.gpu_backend import GPUBackendManager

        result = GPUBackendManager.is_available()
        assert isinstance(result, bool)

    def test_get_memory_info_returns_tuple(self):
        """get_memory_info returns a 4-element tuple."""
        from integrated_app.gpu_backend import GPUBackendManager

        info = GPUBackendManager.get_memory_info()
        assert isinstance(info, tuple)
        assert len(info) == 4

    def test_clear_cache(self):
        """clear_cache doesn't raise errors."""
        from integrated_app.gpu_backend import GPUBackendManager

        GPUBackendManager.clear_cache()  # Should not raise
