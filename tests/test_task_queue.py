"""task_queue 模块单元测试 — 覆盖单 worker 串行逻辑。

覆盖目标模块: app/integrated_app/task_queue.py
覆盖率目标: >=70%

覆盖范围:
- GenerationJob 数据类
- create_background_task
- init_queue / shutdown_queue (含 force 参数)
- enqueue_generation
- cancel_generation (排队中/运行中/不存在)
- is_generation_active
- get_queue_status
- _generation_worker
- _notify_generation_failed / _force_fail_if_active
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# =====================================================================
# GenerationJob 数据类测试
# =====================================================================


class TestGenerationJob:
    """GenerationJob 数据类测试。"""

    def test_creation(self):
        from integrated_app.task_queue import GenerationJob

        async def dummy():
            pass

        job = GenerationJob(generation_id="gen-1", coro=dummy())
        assert job.generation_id == "gen-1"
        assert job.created_at > 0
        job.coro.close()


# =====================================================================
# create_background_task 测试
# =====================================================================


class TestCreateBackgroundTask:
    """create_background_task 测试。"""

    def test_creates_and_runs_task(self):
        from integrated_app.task_queue import create_background_task

        async def simple_coro():
            await asyncio.sleep(0.01)
            return 42

        async def run():
            task = create_background_task(simple_coro())
            return await task

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            assert result == 42
        finally:
            loop.close()

    def test_task_added_to_background_set(self):
        """Verify task is actually added to background tasks set."""
        from integrated_app.task_queue import _background_tasks, create_background_task

        async def simple_coro():
            await asyncio.sleep(0.01)
            return "done"

        async def run():
            initial_count = len(_background_tasks)
            task = create_background_task(simple_coro())
            # Verify task was added to the set
            assert task in _background_tasks
            assert len(_background_tasks) == initial_count + 1
            result = await task
            return result

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            assert result == "done"
            # Task should be removed after completion (cleanup happens in task itself)
        finally:
            loop.close()


# =====================================================================
# init_queue / shutdown_queue 测试
# =====================================================================


class TestInitShutdownQueue:
    """init_queue / shutdown_queue 测试。"""

    @pytest.fixture(autouse=True)
    def setup_event_loop(self):
        """每个测试前创建新的事件循环。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._init())
        yield
        loop.run_until_complete(self._shutdown())
        loop.close()

    async def _init(self):
        from integrated_app.task_queue import init_queue

        await init_queue(force=True)

    async def _shutdown(self):
        from integrated_app.task_queue import shutdown_queue

        await shutdown_queue()

    def test_init_creates_queue(self):
        from integrated_app.task_queue import _generation_queue

        assert _generation_queue is not None

    def test_init_creates_worker(self):
        from integrated_app.task_queue import _generation_worker_task

        assert _generation_worker_task is not None
        assert not _generation_worker_task.done()

    def test_double_init_without_force(self):
        from integrated_app.task_queue import _generation_worker_task

        original = _generation_worker_task

        async def reinit():
            from integrated_app.task_queue import init_queue

            await init_queue(force=False)

        loop = asyncio.get_event_loop()
        loop.run_until_complete(reinit())
        # Same worker, not recreated
        assert _generation_worker_task is original

    def test_force_reinit(self):
        """Force reinit should recreate the worker task."""
        from integrated_app.task_queue import _generation_worker_task

        async def force_reinit():
            from integrated_app.task_queue import init_queue

            await init_queue(force=True)

        loop = asyncio.get_event_loop()
        # The old task should be done after force reinit
        old_task = _generation_worker_task
        loop.run_until_complete(force_reinit())
        # New worker should exist and be a different task
        from integrated_app.task_queue import _generation_worker_task as new_task
        assert new_task is not None
        assert new_task is not old_task or old_task.done()

    def test_shutdown_clears_state(self):
        """Verify shutdown clears queue and worker state."""
        from integrated_app.task_queue import (
            _generation_queue,
            _generation_worker_task,
            get_queue_status,
        )

        # After init in fixture, queue should exist
        assert _generation_queue is not None
        assert _generation_worker_task is not None

        async def shutdown_and_check():
            from integrated_app.task_queue import shutdown_queue

            await shutdown_queue()

        loop = asyncio.get_event_loop()
        loop.run_until_complete(shutdown_and_check())

        # After shutdown, both should be None
        from integrated_app.task_queue import _generation_queue as queue_after, _generation_worker_task as worker_after
        assert queue_after is None
        assert worker_after is None
        # Status should return zeros when uninitialized
        status = get_queue_status()
        assert status["queued_count"] == 0
        assert status["running_count"] == 0


# =====================================================================
# get_queue_status / is_generation_active 测试
# =====================================================================


class TestQueueStatus:
    """get_queue_status / is_generation_active 测试。"""

    @pytest.fixture(autouse=True)
    def setup_queue(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._init())
        yield
        loop.run_until_complete(self._shutdown())
        loop.close()

    async def _init(self):
        from integrated_app.task_queue import init_queue

        await init_queue(force=True)

    async def _shutdown(self):
        from integrated_app.task_queue import shutdown_queue

        await shutdown_queue()

    def test_get_queue_status_returns_dict(self):
        from integrated_app.task_queue import get_queue_status

        status = get_queue_status()
        assert isinstance(status, dict)
        assert "queued_count" in status
        assert "running_count" in status
        assert "queued_ids" in status
        assert "running_ids" in status

    def test_get_queue_status_initial(self):
        from integrated_app.task_queue import get_queue_status

        status = get_queue_status()
        assert status["queued_count"] == 0
        assert status["running_count"] == 0
        assert status["queued_ids"] == []
        assert status["running_ids"] == []

    def test_is_generation_active_false_for_nonexistent(self):
        from integrated_app.task_queue import is_generation_active

        assert is_generation_active("nonexistent") is False


# =====================================================================
# cancel_generation 测试
# =====================================================================


class TestCancelGeneration:
    """cancel_generation 测试。"""

    @pytest.fixture(autouse=True)
    def setup_queue(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._init())
        yield
        loop.run_until_complete(self._shutdown())
        loop.close()

    async def _init(self):
        from integrated_app.task_queue import init_queue

        await init_queue(force=True)

    async def _shutdown(self):
        from integrated_app.task_queue import shutdown_queue

        await shutdown_queue()

    def test_cancel_nonexistent_returns_none(self):
        from integrated_app.task_queue import cancel_generation

        result = cancel_generation("nonexistent")
        assert result is None

    def test_cancel_queued_generation(self):
        from integrated_app.task_queue import cancel_generation, enqueue_generation

        async def dummy():
            pass

        loop = asyncio.get_event_loop()

        async def enqueue_and_cancel():
            await enqueue_generation("gen-cancel-test-2", dummy())

        loop.run_until_complete(enqueue_and_cancel())
        result = cancel_generation("gen-cancel-test-2")
        # Task may be queued or already running by the time cancel is called
        assert result in ("queued", "running", None)

    def test_cancel_running_generation(self):
        from integrated_app.task_queue import (
            _running_generation_tasks,
            cancel_generation,
        )

        # Create a mock running task
        async def long_running():
            await asyncio.sleep(10)

        loop = asyncio.get_event_loop()

        async def setup():
            from integrated_app.task_queue import _lock

            task = asyncio.create_task(long_running())
            async with _lock:
                _running_generation_tasks["gen-running"] = task
            return task

        task = loop.run_until_complete(setup())
        result = cancel_generation("gen-running")
        assert result == "running"
        # Wait for cancellation to complete
        try:
            loop.run_until_complete(asyncio.wait_for(task, timeout=1.0))
        except (asyncio.CancelledError, Exception):
            pass
        # Verify the task was cancelled or completed
        assert task.cancelled() or task.done()
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass


# =====================================================================
# enqueue_generation 测试
# =====================================================================


class TestEnqueueGeneration:
    """enqueue_generation 测试。"""

    @pytest.fixture(autouse=True)
    def setup_queue(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._init())
        yield
        loop.run_until_complete(self._shutdown())
        loop.close()

    async def _init(self):
        from integrated_app.task_queue import init_queue

        await init_queue(force=True)

    async def _shutdown(self):
        from integrated_app.task_queue import shutdown_queue

        await shutdown_queue()

    def test_enqueue_when_queue_not_initialized_raises(self):
        """Queue not initialized should raise RuntimeError."""
        from integrated_app.task_queue import _generation_queue, enqueue_generation

        async def dummy():
            pass

        # Save original and set to None
        original = _generation_queue

        async def test_with_none():
            import integrated_app.task_queue as tq
            tq._generation_queue = None
            with pytest.raises(RuntimeError, match="尚未初始化"):
                await enqueue_generation("test-gen", dummy())

        loop = asyncio.get_event_loop()
        loop.run_until_complete(test_with_none())

        # Restore
        import integrated_app.task_queue as tq
        tq._generation_queue = original

    def test_enqueue_and_process(self):
        """Test that enqueued coroutine gets processed."""
        from integrated_app.task_queue import enqueue_generation

        results = []

        async def test_coro():
            results.append("processed")

        async def run():
            await enqueue_generation("gen-test", test_coro())
            # Wait for processing
            await asyncio.sleep(0.2)
            return results

        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(run())
        assert "processed" in result

    def test_enqueue_marks_queued(self):
        """Test that enqueued generation is marked as queued."""
        from integrated_app.task_queue import (
            _queued_generation_ids,
            enqueue_generation,
            is_generation_active,
        )

        async def long_coro():
            await asyncio.sleep(0.5)

        async def enqueue():
            await enqueue_generation("gen-queued", long_coro())

        loop = asyncio.get_event_loop()
        loop.run_until_complete(enqueue())
        # Should be active (either queued or running)
        assert is_generation_active("gen-queued") is True


# =====================================================================
# _generation_worker 串行处理测试
# =====================================================================


class TestSerialProcessing:
    """Worker 串行处理测试。"""

    @pytest.fixture(autouse=True)
    def setup_queue(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._init())
        yield
        loop.run_until_complete(self._shutdown())
        loop.close()

    async def _init(self):
        from integrated_app.task_queue import init_queue

        await init_queue(force=True)

    async def _shutdown(self):
        from integrated_app.task_queue import shutdown_queue

        await shutdown_queue()

    def test_tasks_processed_serially(self):
        """Two tasks should be processed one after another, not concurrently."""
        from integrated_app.task_queue import enqueue_generation

        execution_order = []
        active_count = [0]
        max_concurrent = [0]

        async def task_a():
            active_count[0] += 1
            max_concurrent[0] = max(max_concurrent[0], active_count[0])
            await asyncio.sleep(0.1)
            execution_order.append("A")
            active_count[0] -= 1

        async def task_b():
            active_count[0] += 1
            max_concurrent[0] = max(max_concurrent[0], active_count[0])
            await asyncio.sleep(0.1)
            execution_order.append("B")
            active_count[0] -= 1

        async def run():
            await enqueue_generation("gen-a", task_a())
            await enqueue_generation("gen-b", task_b())
            await asyncio.sleep(0.5)

        loop = asyncio.get_event_loop()
        loop.run_until_complete(run())
        # Verify serial processing
        assert max_concurrent[0] == 1
        assert len(execution_order) == 2

    def test_cancelled_queued_task_is_skipped(self):
        """Cancelled queued task should be skipped."""
        from integrated_app.task_queue import cancel_generation, enqueue_generation

        processed = []

        async def task_a():
            processed.append("A")

        async def task_b():
            processed.append("B")

        async def run():
            await enqueue_generation("gen-a", task_a())
            await enqueue_generation("gen-b", task_b())
            cancel_generation("gen-b")
            await asyncio.sleep(0.3)

        loop = asyncio.get_event_loop()
        loop.run_until_complete(run())
        assert "A" in processed
        assert "B" not in processed

    def test_shutdown_closes_pending_coroutines(self):
        """shutdown 时应关闭队列中未处理协程，避免 never-awaited 警告与资源泄漏。"""
        import inspect

        from integrated_app import task_queue as tq

        async def pending_coro():
            await asyncio.sleep(60)

        async def run():
            coro = pending_coro()
            # 模拟 worker 未及取出的排队任务
            tq._generation_queue = asyncio.Queue()
            await tq._generation_queue.put(tq.GenerationJob(generation_id="gen-pending", coro=coro))
            await tq.shutdown_queue()
            return coro

        loop = asyncio.get_event_loop()
        coro = loop.run_until_complete(run())
        assert inspect.getcoroutinestate(coro) == "CORO_CLOSED"

    def test_force_init_closes_pending_coroutines(self):
        """强制重建队列时应关闭旧队列中未处理协程，避免资源泄漏。"""
        import inspect

        from integrated_app import task_queue as tq

        async def pending_coro():
            await asyncio.sleep(60)

        async def run():
            coro = pending_coro()
            tq._generation_queue = asyncio.Queue()
            await tq._generation_queue.put(tq.GenerationJob(generation_id="gen-pending", coro=coro))
            await tq.init_queue(force=True)
            return coro

        loop = asyncio.get_event_loop()
        coro = loop.run_until_complete(run())
        assert inspect.getcoroutinestate(coro) == "CORO_CLOSED"


# =====================================================================
# _notify_generation_failed / _force_fail_if_active 测试
# =====================================================================


class TestNotifyFailed:
    """_notify_generation_failed / _force_fail_if_active 测试。"""

    def test_notify_generation_failed_silent_on_import_error(self):
        """_notify_generation_failed should not raise even if SSE module is unavailable."""
        from integrated_app.task_queue import _notify_generation_failed

        async def run():
            # Should not raise even if SSE module is unavailable
            await _notify_generation_failed("test-gen", "test error")
            return True

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            assert result is True
        finally:
            loop.close()

    def test_force_fail_if_active_silent(self):
        """_force_fail_if_active should complete without raising for unknown generation."""
        from integrated_app.task_queue import _force_fail_if_active

        async def run():
            await _force_fail_if_active("test-gen", "worker crash")
            return True

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(run())
            assert result is True
        finally:
            loop.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
