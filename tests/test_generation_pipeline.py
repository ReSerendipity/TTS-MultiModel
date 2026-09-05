"""假引擎完整生成链路测试 — 服务层 _execute_generation 全流程。

覆盖：
- 信号量排队超时 → 429 + Retry-After:30
- 生成硬超时 → 503 + Retry-After:60
- 信号量在成功/失败/超时后均正确释放（不泄漏槽位）
- run_fn 返回 None+错误消息 → 错误响应
- 并发请求排队（semaphore=1 时第二个请求等待第一个完成）
- per-engine 信号量隔离
"""

import asyncio
import time

import pytest

from integrated_app.routes.generate.utils import (
    _execute_generation,
    _get_generation_semaphore,
)


class _MockRequest:
    """最小化 mock 请求对象。"""

    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}
        self.state = type("State", (), {"user": "test"})()


def _fake_run_error():
    """假生成函数：返回 (None, msg) 触发错误路径。"""
    return None, "fake engine error"


def _fake_run_slow():
    """假生成函数：sleep 超过硬超时，触发 503。"""
    time.sleep(3)
    return None, "should not reach"


class TestGenerationPipelineSemaphore:
    """生成链路信号量与超时测试。"""

    @pytest.mark.asyncio
    async def test_queue_timeout_returns_429_with_retry_after(self):
        """信号量满时，acquire 超时返回 429 + Retry-After:30。"""
        engine = "test_queue_timeout_engine"
        sem = await _get_generation_semaphore(engine)
        await sem.acquire()
        try:
            import integrated_app.routes.generate.utils as gen_utils

            original = gen_utils._gen_semaphore_timeout
            gen_utils._gen_semaphore_timeout = lambda: 0.2
            try:
                req = _MockRequest()
                resp = await _execute_generation(req, "test text", _fake_run_error, "test_endpoint", engine=engine)
                assert resp.status_code == 429
                assert resp.headers.get("Retry-After") == "30"
            finally:
                gen_utils._gen_semaphore_timeout = original
        finally:
            sem.release()

    @pytest.mark.asyncio
    async def test_hard_timeout_returns_503_with_retry_after(self):
        """生成函数执行超过硬超时返回 503 + Retry-After:60。"""
        engine = "test_hard_timeout_engine"
        import integrated_app.routes.generate.utils as gen_utils

        original = gen_utils._gen_hard_timeout
        gen_utils._gen_hard_timeout = lambda: 0.5
        try:
            req = _MockRequest()
            resp = await _execute_generation(req, "test text", _fake_run_slow, "test_endpoint", engine=engine)
            assert resp.status_code == 503
            assert resp.headers.get("Retry-After") == "60"
        finally:
            gen_utils._gen_hard_timeout = original

    @pytest.mark.asyncio
    async def test_semaphore_released_after_error(self):
        """run_fn 返回错误后信号量必须释放。"""
        engine = "test_release_error_engine"
        sem = await _get_generation_semaphore(engine)
        initial_value = sem._value  # noqa: SLF001

        req = _MockRequest()
        await _execute_generation(req, "test", _fake_run_error, "ep", engine=engine)

        assert sem._value == initial_value  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_semaphore_released_after_timeout(self):
        """硬超时后信号量必须释放。"""
        engine = "test_release_timeout_engine"
        sem = await _get_generation_semaphore(engine)
        initial_value = sem._value  # noqa: SLF001

        import integrated_app.routes.generate.utils as gen_utils

        original = gen_utils._gen_hard_timeout
        gen_utils._gen_hard_timeout = lambda: 0.3
        try:
            req = _MockRequest()
            await _execute_generation(req, "test", _fake_run_slow, "ep", engine=engine)
        finally:
            gen_utils._gen_hard_timeout = original

        await asyncio.sleep(0.2)
        assert sem._value == initial_value  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_error_path_returns_error_response(self):
        """run_fn 返回 (None, msg) 时返回错误响应（非 200 成功）。"""
        engine = "test_error_path_engine"
        req = _MockRequest()
        resp = await _execute_generation(req, "test", _fake_run_error, "ep", engine=engine)
        # 错误路径返回 400（或模板降级后的 200 错误片段），绝不是成功响应
        assert resp.status_code in (200, 400)
        body = resp.body.decode("utf-8", errors="replace") if isinstance(resp.body, bytes) else str(resp.body)
        # 错误响应体应包含错误标记
        assert "error" in body.lower() or "失败" in body or "tts-error" in body

    @pytest.mark.asyncio
    async def test_concurrent_requests_queue_behind_semaphore(self):
        """semaphore=1 时，第二个请求必须等第一个完成才能获取信号量。"""
        engine = "test_concurrent_queue_engine"
        await _get_generation_semaphore(engine)  # 确保创建

        order: list[str] = []

        def slow_run():
            time.sleep(0.3)
            order.append("first_complete")
            return None, "first done"

        async def first_request():
            req = _MockRequest()
            order.append("first_start")
            await _execute_generation(req, "t1", slow_run, "ep", engine=engine)

        async def second_request():
            await asyncio.sleep(0.05)
            req = _MockRequest()
            order.append("second_start")
            await _execute_generation(req, "t2", _fake_run_error, "ep", engine=engine)
            order.append("second_complete")

        await asyncio.gather(first_request(), second_request())

        assert "first_complete" in order, f"first_complete not in order: {order}"
        assert "second_complete" in order
        assert order.index("first_complete") < order.index("second_complete")

    @pytest.mark.asyncio
    async def test_per_engine_semaphore_isolation(self):
        """不同引擎使用独立信号量，A 引擎满不影响 B 引擎。"""
        sem_a = await _get_generation_semaphore("test_isolation_engine_a")
        sem_b = await _get_generation_semaphore("test_isolation_engine_b")
        assert sem_a is not sem_b

        await sem_a.acquire()
        try:
            acquired = False
            try:
                await asyncio.wait_for(sem_b.acquire(), timeout=0.5)
                acquired = True
            except asyncio.TimeoutError:
                pass
            assert acquired, "B 引擎信号量被 A 引擎阻塞（隔离失败）"
            sem_b.release()
        finally:
            sem_a.release()
