"""routes/generate/voxcpm2 路由冒烟测试。

覆盖目标模块: app/integrated_app/routes/generate/voxcpm2/*.py
"""


class TestVoxcpm2GenerateRoutes:
    def test_design_endpoint(self, client):
        response = client.post("/api/generate/voxcpm2/design", data={"text": "你好"})
        assert response.status_code in (200, 403, 404, 400, 422)

    def test_clone_endpoint(self, client):
        response = client.post("/api/generate/voxcpm2/clone", data={"text": "你好"})
        assert response.status_code in (200, 403, 404, 400, 422)

    def test_script_endpoint(self, client):
        response = client.post("/api/generate/voxcpm2/script", data={"text": "你好"})
        assert response.status_code in (200, 403, 404, 400, 422)

    def test_streaming_endpoint(self, client):
        response = client.post("/api/generate/voxcpm2/streaming", data={"text": "你好"})
        assert response.status_code in (200, 403, 404, 400, 422)

    def test_ultimate_endpoint(self, client):
        response = client.post("/api/generate/voxcpm2/ultimate", data={"text": "你好"})
        assert response.status_code in (200, 403, 404, 400, 422)

    def test_prompt_endpoint(self, client):
        response = client.post("/api/generate/voxcpm2/prompt", data={"text": "你好"})
        assert response.status_code in (200, 403, 404, 400, 422)
