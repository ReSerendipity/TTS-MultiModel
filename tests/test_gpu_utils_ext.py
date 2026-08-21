"""gpu_utils 模块单元测试 — OOM 检测与显存工具。

覆盖目标模块: app/integrated_app/gpu_utils.py
"""

import pytest

from integrated_app.gpu_utils import (
    GPUMemoryMonitor,
    free_gpu_memory,
    get_gpu_device,
    get_gpu_memory_info,
    is_oom_error,
)


class TestIsOomError:
    def test_cuda_oom(self):
        assert is_oom_error(RuntimeError("CUDA out of memory. Tried to allocate 2 GiB")) is True

    def test_torch_cuda_oom(self):
        try:
            import torch

            exc = torch.cuda.OutOfMemoryError("OOM")
            assert is_oom_error(exc) is True
        except (ImportError, AttributeError):
            pytest.skip("torch 不可用")

    def test_other_error(self):
        assert is_oom_error(ValueError("普通错误")) is False


class TestFreeGpuMemory:
    def test_free_gpu_memory_no_crash(self):
        # CPU/无 GPU 环境下应静默返回
        free_gpu_memory()


class TestGpuInfo:
    def test_get_gpu_device(self):
        device = get_gpu_device()
        assert device is None or isinstance(device, int)

    def test_get_gpu_memory_info(self):
        info = get_gpu_memory_info()
        assert isinstance(info, tuple)
        assert len(info) == 4
        assert all(isinstance(v, (int, float)) for v in info)


class TestGPUMemoryMonitor:
    def test_get_vram_info(self):
        info = GPUMemoryMonitor.get_vram_info()
        assert "total" in info
        assert "used" in info
        assert "free" in info

    def test_can_load_model(self):
        ok, code = GPUMemoryMonitor.can_load_model("voxcpm2")
        assert isinstance(ok, bool)
        assert isinstance(code, int)

    def test_check_vram_safety(self):
        report = GPUMemoryMonitor.check_vram_safety("voxcpm2")
        assert isinstance(report, (dict, tuple)) or report is not None
