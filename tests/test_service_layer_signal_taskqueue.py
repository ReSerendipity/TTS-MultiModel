"""service_layer, signal_handlers, task_queue 模块的单元测试。

覆盖 P1-3: 关键模块 0% 覆盖率技术债，补充以下模块的测试：
- service_layer.py: 数据类、VRAM 检查、服务单例
- signal_handlers.py: 信号注册/注销/检查/重置
- task_queue.py: 队列初始化/入队/取消/状态/关闭
"""

import asyncio
import os
import signal
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# =====================================================================
# service_layer 测试
# =====================================================================


class TestServiceLayerDataclasses:
    """service_layer 数据类测试。"""

    def test_generation_result_to_dict(self):
        """GenerationResult.to_dict 正确序列化。"""
        from integrated_app.service_layer import GenerationResult

        result = GenerationResult(
            audio_path="/tmp/test.wav",
            message="test message",
            duration=3.5,
            engine="voxcpm2",
            params={"cfg": 2.0},
        )
        d = result.to_dict()
        assert d["audio_path"] == "/tmp/test.wav"
        assert d["message"] == "test message"
        assert d["duration"] == 3.5
        assert d["engine"] == "voxcpm2"
        assert d["params"]["cfg"] == 2.0

    def test_load_result_defaults(self):
        """LoadResult 默认值正确。"""
        from integrated_app.service_layer import LoadResult

        r = LoadResult()
        assert r.success is False
        assert r.message == ""
        assert r.engine == ""
        assert r.load_time == 0.0

    def test_switch_result_defaults(self):
        """SwitchResult 默认值正确。"""
        from integrated_app.service_layer import SwitchResult

        r = SwitchResult()
        assert r.success is False
        assert r.from_engine == ""
        assert r.to_engine == ""

    def test_model_status_defaults(self):
        """ModelStatus 默认值正确。"""
        from integrated_app.service_layer import ModelStatus

        s = ModelStatus()
        assert s.engine is None
        assert s.loaded is False
        assert s.ready is False
        assert s.vram_usage_percent == -1.0

    def test_persona_info_defaults(self):
        """PersonaInfo 默认值正确。"""
        from integrated_app.service_layer import PersonaInfo

        info = PersonaInfo()
        assert info.name == ""
        assert info.exists is False
        assert info.wav_size_kb == 0.0


class TestServiceLayerVRAMCheck:
    """service_layer VRAM 熔断检查测试。"""

    def test_vram_check_returns_false_on_cpu(self):
        """CPU 后端时 VRAM 检查返回 False（不触发熔断）。"""
        from integrated_app.service_layer import _check_vram_circuit_breaker

        with patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend") as mock:
            from integrated_app.gpu_backend import GPUBackend

            mock.return_value = GPUBackend.CPU
            assert _check_vram_circuit_breaker() is False

    def test_vram_usage_percent_on_cpu(self):
        """CPU 后端时显存占用为 0.0。"""
        from integrated_app.service_layer import _get_vram_usage_percent

        with patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend") as mock:
            from integrated_app.gpu_backend import GPUBackend

            mock.return_value = GPUBackend.CPU
            assert _get_vram_usage_percent() == 0.0


class TestServiceLayerSingletons:
    """service_layer 单例获取测试。"""

    def test_get_generation_service_singleton(self):
        """get_generation_service 返回同一实例。"""
        from integrated_app.service_layer import get_generation_service

        svc1 = get_generation_service()
        svc2 = get_generation_service()
        assert svc1 is svc2

    def test_get_model_service_singleton(self):
        """get_model_service 返回同一实例。"""
        from integrated_app.service_layer import get_model_service

        svc1 = get_model_service()
        svc2 = get_model_service()
        assert svc1 is svc2

    def test_get_persona_service_singleton(self):
        """get_persona_service 返回同一实例。"""
        from integrated_app.service_layer import get_persona_service

        svc1 = get_persona_service()
        svc2 = get_persona_service()
        assert svc1 is svc2

    def test_persona_service_cache_invalidity(self):
        """PersonaService 缓存失效逻辑。"""
        from integrated_app.service_layer import PersonaService

        svc = PersonaService()
        # 初始缓存无效
        assert svc._is_cache_valid() is False
        # 设置时间戳后缓存有效
        svc._cache_timestamp = time.time()
        assert svc._is_cache_valid() is True
        # 清除缓存
        svc._invalidate_cache()
        assert svc._is_cache_valid() is False
        assert len(svc._cache) == 0


# =====================================================================
# signal_handlers 测试
# =====================================================================


class TestSignalHandlers:
    """signal_handlers 模块测试。"""

    def test_shutdown_flag_initially_not_set(self):
        """graceful_shutdown_requested 初始未设置。"""
        from integrated_app.signal_handlers import graceful_shutdown_requested

        graceful_shutdown_requested.clear()
        assert graceful_shutdown_requested.is_set() is False

    def test_check_graceful_shutdown_false(self):
        """check_graceful_shutdown 返回 False。"""
        from integrated_app.signal_handlers import (
            check_graceful_shutdown,
            graceful_shutdown_requested,
        )

        graceful_shutdown_requested.clear()
        assert check_graceful_shutdown() is False

    def test_check_graceful_shutdown_true(self):
        """check_graceful_shutdown 返回 True。"""
        from integrated_app.signal_handlers import (
            check_graceful_shutdown,
            graceful_shutdown_requested,
        )

        graceful_shutdown_requested.set()
        assert check_graceful_shutdown() is True
        graceful_shutdown_requested.clear()

    def test_is_shutdown_requested(self):
        """is_shutdown_requested 函数。"""
        from integrated_app.signal_handlers import (
            graceful_shutdown_requested,
            is_shutdown_requested,
            reset_shutdown_flag,
        )

        graceful_shutdown_requested.set()
        assert is_shutdown_requested() is True
        reset_shutdown_flag()
        assert is_shutdown_requested() is False

    def test_register_unregister_signal_handlers(self):
        """Signal handlers register and unregister idempotently."""
        import integrated_app.signal_handlers as sig

        # 注册
        sig.register_signal_handlers()
        assert sig._handlers_registered is True
        # 重复注册（幂等）
        sig.register_signal_handlers()
        assert sig._handlers_registered is True
        # 注销
        sig.unregister_signal_handlers()
        assert sig._handlers_registered is False
        # 重复注销（幂等）
        sig.unregister_signal_handlers()
        assert sig._handlers_registered is False

    def test_register_with_callbacks(self):
        """带回调的信号处理器注册。"""
        import integrated_app.signal_handlers as sig

        # 先注销以确保干净状态
        sig.unregister_signal_handlers()

        callback_called = []

        def checkpoint_cb():
            callback_called.append("checkpoint")
            return True

        def cleanup_cb():
            callback_called.append("cleanup")

        sig.register_signal_handlers(
            checkpoint_callback=checkpoint_cb,
            cleanup_callbacks=[cleanup_cb],
        )
        # 手动触发信号处理器
        sig._signal_handler(signal.SIGINT, None)
        assert "checkpoint" in callback_called
        assert "cleanup" in callback_called
        assert sig.graceful_shutdown_requested.is_set() is True
        sig.graceful_shutdown_requested.clear()
        sig.unregister_signal_handlers()

    def test_wait_for_shutdown_timeout(self):
        """wait_for_shutdown 超时返回 False。"""
        from integrated_app.signal_handlers import (
            graceful_shutdown_requested,
            wait_for_shutdown,
        )

        graceful_shutdown_requested.clear()
        result = wait_for_shutdown(timeout=0.05)
        assert result is False

    def test_signal_handler_context(self):
        """SignalHandlerContext enters/exits cleanly and restores state."""
        from integrated_app.signal_handlers import (
            SignalHandlerContext,
            graceful_shutdown_requested,
        )

        graceful_shutdown_requested.clear()
        with SignalHandlerContext():
            pass  # 进出上下文不抛异常
        # After context exit, flag should still be cleared (no signal was sent)
        assert graceful_shutdown_requested.is_set() is False


# =====================================================================
# task_queue 测试
# =====================================================================


class TestTaskQueue:
    """task_queue 模块测试。"""

    @pytest.fixture(autouse=True)
    def setup_queue(self):
        """每个测试前初始化队列，测试后关闭。"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._init_and_cleanup())
        yield
        loop.run_until_complete(self._shutdown_and_cleanup())
        loop.close()

    async def _init_and_cleanup(self):
        from integrated_app.task_queue import init_queue, shutdown_queue

        await init_queue(force=True)

    async def _shutdown_and_cleanup(self):
        from integrated_app.task_queue import shutdown_queue

        await shutdown_queue()

    def test_get_queue_status_initial(self):
        """初始队列状态。"""
        from integrated_app.task_queue import get_queue_status

        status = get_queue_status()
        assert isinstance(status, dict)
        assert "queued_count" in status
        assert "running_count" in status

    def test_is_generation_active_false(self):
        """不存在的生成 ID 不活跃。"""
        from integrated_app.task_queue import is_generation_active

        assert is_generation_active("nonexistent") is False

    def test_cancel_nonexistent_generation(self):
        """取消不存在的生成返回 None。"""
        from integrated_app.task_queue import cancel_generation

        result = cancel_generation("nonexistent_id")
        assert result is None

    def test_create_background_task(self):
        """create_background_task 创建并运行后台任务。"""
        from integrated_app.task_queue import create_background_task

        async def simple_coro():
            await asyncio.sleep(0.01)
            return 42

        # create_background_task needs a running event loop
        loop = asyncio.get_event_loop()

        async def run_and_wait():
            task = create_background_task(simple_coro())
            return await task

        result = loop.run_until_complete(run_and_wait())
        assert result == 42


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
