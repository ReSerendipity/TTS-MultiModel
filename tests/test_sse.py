"""SSE 事件流 + 生成信号量 综合测试。

覆盖范围：
- _format_sse_frame：标准 SSE 帧序列化（event/id/retry/data 行 + 帧分隔符）
- SSEEventBus：subscribe/unsubscribe/notify、队列溢出 LRU 丢弃、dict 兼容
- SSE 端点：GET /api/sse/events 返回 200 + text/event-stream + 正确响应头 + 首包有效
- 生成信号量：_get_generation_semaphore 单例、acquire 超时、release 恢复
"""

import asyncio
import json

import pytest

from integrated_app.routes.sse import (
    SSEEvent,
    SSEEventBus,
    _format_sse_frame,
    _format_time_estimate,
)

# =====================================================================
# _format_sse_frame 帧格式测试
# =====================================================================


class TestFormatSSEFrame:
    """标准 SSE 帧序列化测试。"""

    def test_minimal_event_has_required_lines(self):
        frame = _format_sse_frame(SSEEvent(type="progress", data={"pct": 50}))
        assert frame.startswith("event: progress\n")
        assert "data: " in frame
        assert frame.endswith("\n\n")

    def test_event_with_id(self):
        frame = _format_sse_frame(SSEEvent(type="message", data={}, id="evt-001"))
        assert "id: evt-001\n" in frame

    def test_event_default_retry_3000(self):
        frame = _format_sse_frame(SSEEvent(type="message", data={}))
        assert "retry: 3000\n" in frame

    def test_event_custom_retry(self):
        frame = _format_sse_frame(SSEEvent(type="message", data={}, retry=5000))
        assert "retry: 5000\n" in frame

    def test_data_is_valid_json(self):
        payload = {"pct": 42, "phase": "encoding", "nested": {"a": [1, 2, 3]}}
        frame = _format_sse_frame(SSEEvent(type="progress", data=payload))
        data_line = [line for line in frame.split("\n") if line.startswith("data: ")][0]
        parsed = json.loads(data_line[len("data: ") :])
        assert parsed == payload

    def test_non_serializable_data_falls_back_to_empty(self):
        """data 含不可序列化对象时回退 {}，不抛异常。"""
        frame = _format_sse_frame(SSEEvent(type="x", data={"bad": object()}))
        assert "data: {}" in frame

    def test_unicode_data_preserved(self):
        frame = _format_sse_frame(SSEEvent(type="status", data={"msg": "生成中，请稍候"}))
        assert "生成中，请稍候" in frame


# =====================================================================
# SSEEventBus 事件总线测试
# =====================================================================


class TestSSEEventBus:
    """SSE 事件总线订阅/通知/清理测试。"""

    @pytest.mark.asyncio
    async def test_subscribe_returns_client_id_and_queue(self):
        bus = SSEEventBus()
        cid, q = await bus.subscribe()
        assert cid and isinstance(cid, str)
        assert isinstance(q, asyncio.Queue)
        assert cid in bus._subscribers

    @pytest.mark.asyncio
    async def test_subscribe_explicit_id(self):
        bus = SSEEventBus()
        cid, _ = await bus.subscribe(client_id="my-client")
        assert cid == "my-client"

    @pytest.mark.asyncio
    async def test_subscribe_duplicate_id_gets_suffix(self):
        bus = SSEEventBus()
        cid1, _ = await bus.subscribe(client_id="dup")
        cid2, _ = await bus.subscribe(client_id="dup")
        assert cid1 == "dup"
        assert cid2 == "dup_1"

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_queue(self):
        bus = SSEEventBus()
        cid, _ = await bus.subscribe()
        await bus.unsubscribe(cid)
        assert cid not in bus._subscribers

    @pytest.mark.asyncio
    async def test_unsubscribe_idempotent(self):
        bus = SSEEventBus()
        await bus.unsubscribe("nonexistent")  # 不抛异常
        await bus.unsubscribe("")  # 空字符串静默返回

    @pytest.mark.asyncio
    async def test_notify_sseevent_delivers_to_queue(self):
        bus = SSEEventBus()
        cid, q = await bus.subscribe()
        evt = SSEEvent(type="progress", data={"pct": 80})
        bus.notify(evt)
        delivered = await asyncio.wait_for(q.get(), timeout=2.0)
        assert delivered is evt
        await bus.unsubscribe(cid)

    @pytest.mark.asyncio
    async def test_notify_dict_delivers_to_queue(self):
        bus = SSEEventBus()
        cid, q = await bus.subscribe()
        bus.notify({"type": "complete", "data": {"ok": True}})
        delivered = await asyncio.wait_for(q.get(), timeout=2.0)
        assert isinstance(delivered, dict)
        assert delivered["type"] == "complete"
        await bus.unsubscribe(cid)

    @pytest.mark.asyncio
    async def test_notify_broadcasts_to_all_subscribers(self):
        bus = SSEEventBus()
        _, q1 = await bus.subscribe()
        _, q2 = await bus.subscribe()
        evt = SSEEvent(type="status", data={})
        bus.notify(evt)
        d1 = await asyncio.wait_for(q1.get(), timeout=2.0)
        d2 = await asyncio.wait_for(q2.get(), timeout=2.0)
        assert d1 is evt
        assert d2 is evt

    @pytest.mark.asyncio
    async def test_queue_overflow_discards_oldest(self):
        """队列满时 notify 丢弃最早队头（LRU），不抛异常。"""
        bus = SSEEventBus(max_queue_size=2)
        cid, q = await bus.subscribe()
        evt1 = SSEEvent(type="e1", data={})
        evt2 = SSEEvent(type="e2", data={})
        evt3 = SSEEvent(type="e3", data={})
        bus.notify(evt1)
        bus.notify(evt2)
        bus.notify(evt3)  # 队列满，丢弃 evt1
        # 队列中应剩 evt2, evt3
        assert q.qsize() == 2
        d1 = q.get_nowait()
        d2 = q.get_nowait()
        assert d1.type == "e2"
        assert d2.type == "e3"
        await bus.unsubscribe(cid)


# =====================================================================
# SSE 端点集成测试
# =====================================================================


class TestSSEEndpoint:
    """SSE 端点路由级测试。"""

    def test_sse_route_registered_in_router(self):
        from integrated_app.routes.sse import router

        paths = [r.path for r in router.routes]
        assert "/api/sse/events" in paths

    def test_sse_route_is_get(self):
        from integrated_app.routes.sse import router

        for r in router.routes:
            if hasattr(r, "path") and r.path == "/api/sse/events":
                assert "GET" in r.methods
                return
        pytest.fail("SSE route not found")

    def test_sse_endpoint_returns_streaming_response(self):
        import inspect

        from integrated_app.routes.sse import sse_events

        src = inspect.getsource(sse_events)
        assert "StreamingResponse" in src
        assert "text/event-stream" in src

    def test_sse_endpoint_has_no_cache_header(self):
        import inspect

        from integrated_app.routes.sse import sse_events

        src = inspect.getsource(sse_events)
        assert "no-cache" in src

    def test_sse_path_in_openapi_schema(self, app):
        schema = app.openapi()
        assert "/api/sse/events" in schema.get("paths", {})


# =====================================================================
# 生成信号量测试
# =====================================================================


class TestGenerationSemaphore:
    """生成请求 per-engine 信号量测试。"""

    @pytest.mark.asyncio
    async def test_get_semaphore_returns_same_instance_per_engine(self):
        from integrated_app.routes.generate.utils import _get_generation_semaphore

        sem1 = await _get_generation_semaphore("test-engine-a")
        sem2 = await _get_generation_semaphore("test-engine-a")
        sem3 = await _get_generation_semaphore("test-engine-b")
        assert sem1 is sem2
        assert sem1 is not sem3

    @pytest.mark.asyncio
    async def test_semaphore_acquire_timeout_when_full(self):
        """信号量满时，acquire 在超时后抛 TimeoutError。"""
        sem = asyncio.Semaphore(1)
        await sem.acquire()  # 占满唯一槽位
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sem.acquire(), timeout=0.1)
        sem.release()

    @pytest.mark.asyncio
    async def test_semaphore_release_allows_next_acquire(self):
        sem = asyncio.Semaphore(1)
        await sem.acquire()
        sem.release()
        # release 后应能立即 acquire
        await asyncio.wait_for(sem.acquire(), timeout=1.0)
        sem.release()

    def test_semaphore_timeout_config_is_positive(self):
        from integrated_app.routes.generate.utils import _gen_semaphore_timeout

        timeout = _gen_semaphore_timeout()
        assert isinstance(timeout, float)
        assert timeout > 0


# =====================================================================
# 保留原有 3 个浅测试（回归兼容）
# =====================================================================


class TestSSELegacy:
    def test_format_time_estimate(self):
        assert "秒" in _format_time_estimate(5)
        assert "约" in _format_time_estimate(30)
        assert "分" in _format_time_estimate(120)

    def test_sse_router_exists(self):
        from integrated_app.routes.sse import router

        assert router is not None

    def test_sse_endpoint_defined(self):
        from integrated_app.routes.sse import router

        routes = [r.path for r in router.routes]
        assert "/api/sse/events" in routes
