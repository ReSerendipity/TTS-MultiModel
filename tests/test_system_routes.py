"""system/model/sse 路由冒烟测试 — 精确状态码断言。

覆盖目标模块: app/integrated_app/routes/system/health.py / gpu.py / model.py
"""


class TestSystemRoutes:
    def test_health_ping(self, client):
        response = client.get("/api/system/health/ping")
        assert response.status_code == 200
        assert response.json().get("status") == "ok"

    def test_health_overview(self, client):
        response = client.get("/api/system/health")
        assert response.status_code == 200

    def test_queue_status(self, client):
        response = client.get("/api/system/queue")
        assert response.status_code == 200

    def test_gpu_status(self, client):
        response = client.get("/api/system/gpu/status")
        assert response.status_code == 200

    def test_gpu_history(self, client):
        response = client.get("/api/system/gpu/history")
        assert response.status_code == 200


class TestModelRoutes:
    def test_model_status(self, client):
        response = client.get("/api/model/status")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    def test_model_preload_status(self, client):
        response = client.get("/api/model/preload/status")
        assert response.status_code == 200

    def test_model_lora_state(self, client):
        response = client.get("/api/model/lora/state")
        assert response.status_code == 200

    def test_model_lora_list(self, client):
        response = client.get("/api/model/lora/list")
        assert response.status_code == 200

    def test_model_download_hints(self, client):
        response = client.get("/api/model/download_hints")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    def test_model_switch_csrf_protected(self, client):
        # POST 无 CSRF token → 403
        response = client.post("/api/model/switch", data={"engine": "voxcpm2"})
        assert response.status_code == 403


class TestSSERoutes:
    def test_event_bus_notify(self):
        # SSE 事件总线 notify 不崩溃（无事件循环时降级）
        from integrated_app.routes.sse import SSEEvent, event_bus

        event_bus.notify(SSEEvent(type="test", data={"k": 1}))
        assert event_bus is not None
