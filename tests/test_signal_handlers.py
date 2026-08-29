"""signal_handlers 模块单元测试 — 覆盖信号注册/清理。

覆盖目标模块: app/integrated_app/signal_handlers.py
覆盖率目标: >=70%

覆盖范围:
- graceful_shutdown_requested Event 标志
- register_signal_handlers / unregister_signal_handlers (幂等性)
- _signal_handler 信号处理函数
- check_graceful_shutdown / is_shutdown_requested / reset_shutdown_flag
- wait_for_shutdown (超时和成功)
- create_training_checkpoint_callback
- SignalHandlerContext 上下文管理器
"""

import signal
import threading
import time
from unittest.mock import MagicMock

import pytest

# =====================================================================
# 基础状态测试
# =====================================================================


class TestShutdownFlag:
    """graceful_shutdown_requested Event 标志测试。"""

    def test_initially_not_set(self):
        from integrated_app.signal_handlers import graceful_shutdown_requested

        graceful_shutdown_requested.clear()
        assert graceful_shutdown_requested.is_set() is False

    def test_set_and_clear(self):
        from integrated_app.signal_handlers import graceful_shutdown_requested

        graceful_shutdown_requested.set()
        assert graceful_shutdown_requested.is_set() is True
        graceful_shutdown_requested.clear()
        assert graceful_shutdown_requested.is_set() is False


class TestCheckGracefulShutdown:
    """check_graceful_shutdown 函数测试。"""

    def test_returns_false_when_not_set(self):
        from integrated_app.signal_handlers import (
            check_graceful_shutdown,
            graceful_shutdown_requested,
        )

        graceful_shutdown_requested.clear()
        assert check_graceful_shutdown() is False

    def test_returns_true_when_set(self):
        from integrated_app.signal_handlers import (
            check_graceful_shutdown,
            graceful_shutdown_requested,
        )

        graceful_shutdown_requested.set()
        assert check_graceful_shutdown() is True
        graceful_shutdown_requested.clear()


class TestIsShutdownRequested:
    """is_shutdown_requested 函数测试。"""

    def test_returns_false_when_not_set(self):
        from integrated_app.signal_handlers import (
            graceful_shutdown_requested,
            is_shutdown_requested,
        )

        graceful_shutdown_requested.clear()
        assert is_shutdown_requested() is False

    def test_returns_true_when_set(self):
        from integrated_app.signal_handlers import (
            graceful_shutdown_requested,
            is_shutdown_requested,
        )

        graceful_shutdown_requested.set()
        assert is_shutdown_requested() is True
        graceful_shutdown_requested.clear()


class TestResetShutdownFlag:
    """reset_shutdown_flag 函数测试。"""

    def test_resets_flag(self):
        from integrated_app.signal_handlers import (
            graceful_shutdown_requested,
            reset_shutdown_flag,
        )

        graceful_shutdown_requested.set()
        reset_shutdown_flag()
        assert graceful_shutdown_requested.is_set() is False


# =====================================================================
# 信号处理器注册/注销测试
# =====================================================================


class TestRegisterUnregister:
    """信号处理器注册与注销测试。"""

    def test_register_and_unregister(self):
        from integrated_app.signal_handlers import (
            register_signal_handlers,
            unregister_signal_handlers,
        )

        register_signal_handlers()
        register_signal_handlers()  # Idempotent
        unregister_signal_handlers()
        unregister_signal_handlers()  # Idempotent

    def test_register_with_checkpoint_callback(self):
        from integrated_app.signal_handlers import (
            _signal_handler,
            graceful_shutdown_requested,
            register_signal_handlers,
            unregister_signal_handlers,
        )

        callback_called = []

        def checkpoint_cb():
            callback_called.append("checkpoint")
            return True

        register_signal_handlers(checkpoint_callback=checkpoint_cb)
        _signal_handler(signal.SIGINT, None)
        assert "checkpoint" in callback_called
        assert graceful_shutdown_requested.is_set() is True
        graceful_shutdown_requested.clear()
        unregister_signal_handlers()

    def test_register_with_cleanup_callbacks(self):
        from integrated_app.signal_handlers import (
            _signal_handler,
            graceful_shutdown_requested,
            register_signal_handlers,
            unregister_signal_handlers,
        )

        cleanup_called = []

        def cleanup_cb():
            cleanup_called.append("cleanup")

        register_signal_handlers(cleanup_callbacks=[cleanup_cb])
        _signal_handler(signal.SIGINT, None)
        assert "cleanup" in cleanup_called
        graceful_shutdown_requested.clear()
        unregister_signal_handlers()

    def test_checkpoint_callback_failure_is_safe(self):
        from integrated_app.signal_handlers import (
            _signal_handler,
            graceful_shutdown_requested,
            register_signal_handlers,
            unregister_signal_handlers,
        )

        def bad_checkpoint():
            raise Exception("checkpoint save failed")

        register_signal_handlers(checkpoint_callback=bad_checkpoint)
        # Should not raise even if callback fails
        _signal_handler(signal.SIGINT, None)
        assert graceful_shutdown_requested.is_set() is True
        graceful_shutdown_requested.clear()
        unregister_signal_handlers()

    def test_cleanup_callback_failure_is_safe(self):
        from integrated_app.signal_handlers import (
            _signal_handler,
            graceful_shutdown_requested,
            register_signal_handlers,
            unregister_signal_handlers,
        )

        def bad_cleanup():
            raise Exception("cleanup failed")

        register_signal_handlers(cleanup_callbacks=[bad_cleanup])
        _signal_handler(signal.SIGINT, None)
        assert graceful_shutdown_requested.is_set() is True
        graceful_shutdown_requested.clear()
        unregister_signal_handlers()


# =====================================================================
# wait_for_shutdown 测试
# =====================================================================


class TestWaitForShutdown:
    """wait_for_shutdown 函数测试。"""

    def test_timeout_returns_false(self):
        from integrated_app.signal_handlers import (
            graceful_shutdown_requested,
            wait_for_shutdown,
        )

        graceful_shutdown_requested.clear()
        result = wait_for_shutdown(timeout=0.05)
        assert result is False

    def test_signaled_returns_true(self):
        from integrated_app.signal_handlers import (
            graceful_shutdown_requested,
            wait_for_shutdown,
        )

        graceful_shutdown_requested.clear()

        # Set flag after a short delay
        def setter():
            time.sleep(0.02)
            graceful_shutdown_requested.set()

        t = threading.Thread(target=setter)
        t.start()
        result = wait_for_shutdown(timeout=1.0)
        t.join()
        assert result is True
        graceful_shutdown_requested.clear()


# =====================================================================
# SignalHandlerContext 测试
# =====================================================================


class TestSignalHandlerContext:
    """SignalHandlerContext 上下文管理器测试。"""

    def test_context_manager_enters_and_exits(self):
        """SignalHandlerContext enters and exits without exception."""
        from integrated_app.signal_handlers import SignalHandlerContext, graceful_shutdown_requested

        graceful_shutdown_requested.clear()
        with SignalHandlerContext():
            pass  # No exception
        # After exit, handlers should be unregistered and flag cleared
        assert graceful_shutdown_requested.is_set() is False

    def test_context_manager_with_callbacks(self):
        """SignalHandlerContext registers and invokes callbacks correctly."""
        from integrated_app.signal_handlers import SignalHandlerContext

        checkpoint_called = []

        def cb():
            checkpoint_called.append("called")
            return True

        with SignalHandlerContext(checkpoint_callback=cb):
            pass
        # Callback was registered (not invoked during normal exit, only on signal)
        # Verify the context completed without error
        assert checkpoint_called == []  # Not called on normal exit

    def test_context_manager_restores_on_exception(self):
        from integrated_app.signal_handlers import SignalHandlerContext

        with pytest.raises(ValueError), SignalHandlerContext():
            raise ValueError("test")


# =====================================================================
# create_training_checkpoint_callback 测试
# =====================================================================


class TestCreateTrainingCheckpointCallback:
    """create_training_checkpoint_callback 函数测试。"""

    def test_creates_callable(self):
        from integrated_app.signal_handlers import create_training_checkpoint_callback

        mock_state = MagicMock()
        mock_state.generator = MagicMock()
        mock_state.generator.state_dict.return_value = {}
        mock_state.optimizer.state_dict.return_value = {}
        mock_state.scheduler.state_dict.return_value = {}
        mock_state.tracker.state_dict.return_value = {}

        cb = create_training_checkpoint_callback(mock_state, "/tmp/test_ckpt")
        assert callable(cb)

    def test_callback_handles_ddp_model(self):
        from integrated_app.signal_handlers import create_training_checkpoint_callback

        mock_model = MagicMock()
        mock_model.module = MagicMock()
        mock_model.module.state_dict.return_value = {}
        mock_model.state_dict.return_value = {}

        mock_state = MagicMock()
        mock_state.generator = mock_model
        mock_state.optimizer.state_dict.return_value = {}
        mock_state.scheduler.state_dict.return_value = {}
        mock_state.tracker.state_dict.return_value = {}

        cb = create_training_checkpoint_callback(mock_state, "/tmp/test_ckpt")
        # We can't actually save without torch, but we can verify it doesn't crash at creation
        assert callable(cb)


# =====================================================================
# 模块级变量重置测试
# =====================================================================


class TestModuleStateReset:
    """模块级状态重置测试。"""

    def test_state_cleared_after_unregister(self):
        """Module-level state is reset after unregister."""
        from integrated_app.signal_handlers import (
            _checkpoint_callback,
            _cleanup_callbacks,
            _handlers_registered,
            graceful_shutdown_requested,
            register_signal_handlers,
            unregister_signal_handlers,
        )

        def cb():
            return True

        def cleanup():
            pass

        register_signal_handlers(
            checkpoint_callback=cb,
            cleanup_callbacks=[cleanup],
        )
        unregister_signal_handlers()

        # After unregister, state should be reset
        assert graceful_shutdown_requested.is_set() is False
        assert _handlers_registered is False
        assert _checkpoint_callback is None
        assert _cleanup_callbacks == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
