"""middleware/error_handler 单元测试 — 统一异常响应。

覆盖目标模块: bin/integrated_app/middleware/error_handler.py
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from integrated_app.exceptions import TTSError


def _build_app() -> FastAPI:
    from integrated_app.middleware.error_handler import register_error_handlers

    app = FastAPI()
    register_error_handlers(app)

    @app.get("/ok")
    async def ok():
        return {"status": "ok"}

    @app.get("/tts-error")
    async def tts_error():
        raise TTSError(code="TEST_ERR", message="测试错误", status_code=400)

    @app.get("/generic-error")
    async def generic_error():
        raise RuntimeError("内部错误")

    return app


class TestErrorHandler:
    def setup_method(self):
        self.client = TestClient(_build_app(), raise_server_exceptions=False)

    def test_ok_endpoint(self):
        response = self.client.get("/ok")
        assert response.status_code == 200

    def test_tts_error_response(self):
        response = self.client.get("/tts-error")
        assert response.status_code == 400
        data = response.json()
        assert data.get("code") == "TEST_ERR"
        assert data.get("message") == "测试错误"

    def test_generic_error_response(self):
        response = self.client.get("/generic-error")
        assert response.status_code == 500
        data = response.json()
        assert "error" in data or "detail" in data or "message" in data

    def test_404_response(self):
        response = self.client.get("/no-such-path")
        assert response.status_code == 404
        assert "application/json" in response.headers.get("content-type", "")
