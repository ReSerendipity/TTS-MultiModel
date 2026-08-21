"""AGENTS.md §6 硬约束的机械验证测试（CPU-only，全部基于 mock）。

覆盖三大硬约束：
1. 显存预检（1.5 倍规则）—— GPUMemoryMonitor.can_load_model / _check_vram_prereq
2. 90% 显存熔断 —— HealthMonitor.check_vram_circuit_breaker
3. 单 worker 串行 —— model_manager._model_lock（threading.RLock）

注意：测试节点 ID 刻意不含 gpu/cuda/vram 关键词，
确保不被 CI 的 `-k "not gpu and not cuda and not vram"` 过滤器排除。
"""

import os
import sys
import threading

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")

_GB = 1024**3


class TestPreloadSafetyMargin:
    """硬约束 1：模型加载前可用显存需为模型大小的 1.5 倍以上。"""

    def test_preload_safety_factor_constant_is_one_point_five(self):
        from integrated_app.monitor import HealthMonitor

        assert HealthMonitor.VRAM_PRELOAD_SAFETY_FACTOR == 1.5

    def test_precheck_rejects_below_150_percent_margin(self, monkeypatch):
        from integrated_app import gpu_utils
        from integrated_app.model_registry import ENGINE_VRAM_REQUIREMENTS

        needed_gb = ENGINE_VRAM_REQUIREMENTS.get("voxcpm2", 6.5)
        needed = int(needed_gb * _GB * 1.5)

        # 空闲显存恰好差 1 字节：必须拒绝
        monkeypatch.setattr(gpu_utils, "get_gpu_memory_info", lambda: (needed * 2, 0, 0, needed - 1))
        ok, free = gpu_utils.GPUMemoryMonitor.can_load_model("voxcpm2")
        assert ok is False
        assert free == needed - 1

    def test_precheck_accepts_at_150_percent_margin(self, monkeypatch):
        from integrated_app import gpu_utils
        from integrated_app.model_registry import ENGINE_VRAM_REQUIREMENTS

        needed_gb = ENGINE_VRAM_REQUIREMENTS.get("voxcpm2", 6.5)
        needed = int(needed_gb * _GB * 1.5)

        # 空闲显存恰好达标：必须放行
        monkeypatch.setattr(gpu_utils, "get_gpu_memory_info", lambda: (needed * 2, 0, 0, needed))
        ok, free = gpu_utils.GPUMemoryMonitor.can_load_model("voxcpm2")
        assert ok is True
        assert free == needed

    def test_engine_switch_precheck_raises_when_memory_insufficient(self, monkeypatch):
        from integrated_app import model_manager
        from integrated_app.exceptions import InsufficientVRAMError
        from integrated_app.gpu_backend import GPUBackend, GPUBackendManager

        total = 8 * _GB
        # 仅剩 1GB 空闲，远低于任何引擎基线（>= 6GB）
        monkeypatch.setattr(GPUBackendManager, "get_device_properties", lambda *a, **k: {"total_memory": total})
        monkeypatch.setattr(GPUBackendManager, "memory_allocated", lambda *a, **k: total - 1 * _GB)

        with pytest.raises(InsufficientVRAMError):
            model_manager._check_vram_prereq("voxcpm2", GPUBackend.CUDA, 0)

    def test_engine_switch_precheck_skips_on_cpu_backend(self):
        from integrated_app import model_manager
        from integrated_app.gpu_backend import GPUBackend

        # CPU 模式跳过预检，返回 0.0 且不抛异常
        assert model_manager._check_vram_prereq("voxcpm2", GPUBackend.CPU, None) == 0.0


class TestCircuitBreaker:
    """硬约束 2：显存占用超过 90% 时必须触发熔断。"""

    def test_circuit_breaker_threshold_constant_is_90(self):
        from integrated_app.monitor import HealthMonitor

        assert HealthMonitor.VRAM_CIRCUIT_BREAKER_PCT == 90.0

    def test_circuit_breaker_trips_above_threshold(self, monkeypatch):
        from integrated_app.monitor import HealthMonitor

        monitor = HealthMonitor()
        monkeypatch.setattr(monitor, "get_vram_usage_percent", lambda: 95.0)
        tripped, reason = monitor.check_vram_circuit_breaker()
        assert tripped is True
        assert "95.0%" in reason

    def test_circuit_breaker_stays_closed_below_threshold(self, monkeypatch):
        from integrated_app.monitor import HealthMonitor

        monitor = HealthMonitor()
        monkeypatch.setattr(monitor, "get_vram_usage_percent", lambda: 89.9)
        tripped, _ = monitor.check_vram_circuit_breaker()
        assert tripped is False

    def test_circuit_breaker_boundary_exactly_at_threshold(self, monkeypatch):
        from integrated_app.monitor import HealthMonitor

        monitor = HealthMonitor()
        # 恰好 90%（不超过阈值）不触发；实现为严格大于比较
        monkeypatch.setattr(monitor, "get_vram_usage_percent", lambda: 90.0)
        tripped, _ = monitor.check_vram_circuit_breaker()
        assert tripped is False

    def test_circuit_breaker_increments_trip_counter(self, monkeypatch):
        from integrated_app.monitor import HealthMonitor

        monitor = HealthMonitor()
        monkeypatch.setattr(monitor, "get_vram_usage_percent", lambda: 99.0)
        before = monitor._circuit_breaker_trips
        monitor.check_vram_circuit_breaker()
        assert monitor._circuit_breaker_trips == before + 1

    def test_circuit_breaker_conservative_on_read_failure(self, monkeypatch):
        from integrated_app.monitor import HealthMonitor

        monitor = HealthMonitor()

        def _boom():
            raise RuntimeError("device query failed")

        # 读取失败时保守放行（不测不误杀），不应抛异常
        monkeypatch.setattr(monitor, "get_vram_usage_percent", _boom)
        tripped, _ = monitor.check_vram_circuit_breaker()
        assert tripped is False


class TestSerialModelLock:
    """硬约束 3：模型加载/卸载/切换通过 model_manager 的全局锁串行处理。"""

    def test_model_lock_is_reentrant_lock(self):
        from integrated_app import model_manager

        assert isinstance(model_manager._model_lock, type(threading.RLock()))

    def test_model_lock_is_reentrant_in_same_thread(self):
        from integrated_app import model_manager

        # switch_engine 内部重入 unload/load，RLock 必须允许同线程嵌套获取
        with model_manager._model_lock, model_manager._model_lock:
            pass

    def test_model_lock_serializes_across_threads(self):
        from integrated_app import model_manager

        lock = model_manager._model_lock
        acquired_in_other_thread: list[bool] = []

        def _try_acquire():
            got = lock.acquire(blocking=True, timeout=0.2)
            acquired_in_other_thread.append(got)
            if got:
                lock.release()

        # 主线程持锁期间，其他线程必须获取失败（串行保证）
        assert lock.acquire(blocking=False)
        try:
            worker = threading.Thread(target=_try_acquire)
            worker.start()
            worker.join(timeout=5)
            assert acquired_in_other_thread == [False]
        finally:
            lock.release()

        # 释放后其他线程可正常获取
        acquired_in_other_thread.clear()
        worker = threading.Thread(target=_try_acquire)
        worker.start()
        worker.join(timeout=5)
        assert acquired_in_other_thread == [True]
