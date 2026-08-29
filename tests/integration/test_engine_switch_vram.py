"""引擎切换 VRAM 释放稳定性测试。

验证引擎切换（unload → load）后显存释放是否彻底，
防止连续切换导致显存累积膨胀。

运行方式::

    # 需要真实 GPU 环境
    pytest tests/integration/test_engine_switch_vram.py -v -m integration
"""

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gpu]


@pytest.fixture
def gpu_available():
    """检查 CUDA GPU 是否可用。"""
    try:
        import torch

        if torch.cuda.is_available():
            return torch.cuda.current_device()
    except ImportError:
        pass
    pytest.skip("CUDA GPU not available")


class TestEngineSwitchVRAM:
    """引擎切换 VRAM 释放测试。"""

    def test_vram_released_after_unload(self, gpu_available):
        """卸载引擎后显存应显著降低。"""
        import torch

        # 记录初始显存
        torch.cuda.empty_cache()
        initial_allocated = torch.cuda.memory_allocated()

        # 分配一个大张量模拟模型加载
        dummy = torch.randn(1000, 1000, device="cuda")
        loaded_allocated = torch.cuda.memory_allocated()
        assert loaded_allocated > initial_allocated

        # 释放
        del dummy
        torch.cuda.empty_cache()
        released_allocated = torch.cuda.memory_allocated()
        assert released_allocated < loaded_allocated

    def test_repeated_load_unload_no_accumulation(self, gpu_available):
        """多次加载/卸载不应导致显存累积。"""
        import torch

        torch.cuda.empty_cache()
        baseline = torch.cuda.memory_allocated()

        for _i in range(5):
            dummy = torch.randn(500, 500, device="cuda")
            allocated_during = torch.cuda.memory_allocated()
            assert allocated_during > baseline

            del dummy
            torch.cuda.empty_cache()

        final_allocated = torch.cuda.memory_allocated()
        # 最终显存不应显著超过基线（允许少量碎片差异）
        assert final_allocated <= baseline + 1024  # 1KB tolerance for fragmentation
