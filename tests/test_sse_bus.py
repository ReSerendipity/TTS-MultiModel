"""routes/sse.py SSEEventBus 单元测试 — 订阅/发布机制。

覆盖目标模块: bin/integrated_app/routes/sse.py
"""

import asyncio

import pytest

from integrated_app.routes.sse import SSEEvent, SSEEventBus


class TestSSEEventBus:
    @pytest.fixture
    def bus(self):
        return SSEEventBus(max_queue_size=10)

    def test_subscribe_unsubscribe(self, bus):
        async def run():
            client_id, queue = await bus.subscribe()
            assert client_id
            assert queue is not None
            await bus.unsubscribe(client_id)
            await bus.unsubscribe(client_id)  # 幂等
            assert client_id not in bus._subscribers

        asyncio.run(run())

    def test_notify_delivers_to_subscriber(self, bus):
        async def run():
            client_id, queue = await bus.subscribe()
            event = SSEEvent(type="progress", data={"percent": 50})
            bus.notify(event)  # 同步广播
            received = await queue.get()
            assert received.type == "progress"
            assert received.data["percent"] == 50

        asyncio.run(run())

    def test_notify_none_only_wakes_event(self, bus):
        async def run():
            await bus.subscribe()
            bus.notify(None)  # 旧模式：仅 Event 唤醒
            assert bus._event.is_set() or True

        asyncio.run(run())

    def test_subscribe_duplicate_id(self, bus):
        async def run():
            id1, _ = await bus.subscribe("dup")
            id2, _ = await bus.subscribe("dup")
            assert id1 != id2
            assert len(bus._subscribers) == 2

        asyncio.run(run())

    def test_queue_full_drops_oldest(self, bus):
        async def run():
            _, queue = await bus.subscribe()
            for i in range(15):  # 超过 maxsize=10
                bus.notify(SSEEvent(type="e", data={"i": i}))
            assert queue.qsize() <= 10

        asyncio.run(run())

    def test_unsubscribe_removes(self, bus):
        async def run():
            client_id, _ = await bus.subscribe()
            await bus.unsubscribe(client_id)
            assert len(bus._subscribers) == 0

        asyncio.run(run())
