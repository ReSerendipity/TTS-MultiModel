"""routes/generate/generic 路由冒烟测试。

覆盖目标模块: app/integrated_app/routes/generate/generic/clone.py
"""


class TestGenericCloneEndpoint:
    def test_missing_ref_audio(self, client):
        # 无参考音频时返回错误提示（不进入 GPU 推理）；无 CSRF token 时 403
        response = client.post(
            "/api/generate/generic/clone",
            data={"text": "你好世界"},
        )
        assert response.status_code in (200, 400, 403, 422)

    def test_empty_text(self, client):
        response = client.post(
            "/api/generate/generic/clone",
            data={"text": ""},
        )
        assert response.status_code in (200, 400, 403, 422)

    def test_get_method_not_allowed(self, client):
        response = client.get("/api/generate/generic/clone")
        assert response.status_code in (405, 404)
