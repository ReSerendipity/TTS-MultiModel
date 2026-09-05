"""API 契约测试 — 基于 OpenAPI schema 自动校验 API 响应格式。

使用 FastAPI 的 OpenAPI schema 对 API 端点进行响应格式验证，
确保 API 响应始终符合 schema 定义，防止接口格式回归。

运行方式::

    pytest tests/test_api_contract.py -v
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def openai_client():
    """创建仅包含 OpenAI router 的测试客户端（无 CSRF）。"""
    from integrated_app.openai_api import openai_router

    app = FastAPI()
    app.include_router(openai_router.router)
    return TestClient(app)


class TestOpenAPISchema:
    """OpenAPI schema 完整性与契约测试。"""

    def test_openapi_schema_available(self, client):
        """OpenAPI schema 可获取。"""
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "openapi" in schema
        assert "paths" in schema
        assert "info" in schema

    def test_openapi_has_paths(self, client):
        """OpenAPI schema 包含路径定义。"""
        schema = client.get("/openapi.json").json()
        assert len(schema["paths"]) > 0

    def test_openapi_has_health_endpoints(self, client):
        """OpenAPI schema 包含健康检查端点。"""
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]
        assert "/api/health/ping" in paths or "/api/system/health/ping" in paths

    def test_openapi_has_model_endpoints(self, client):
        """OpenAPI schema 包含模型管理端点。"""
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]
        assert "/api/model/status" in paths

    def test_openapi_has_all_model_routes(self, client):
        """遍历 /api/model/* 全量路由存在性（运维稳定性评估 P2 事故防线）。

        Why 逐端点断言：KNOWN_GOTCHAS #21 事故——pre-commit ruff --fix 删除
        model_manager.py re-export 门面 → 路由模块 import 时静默消失 →
        /api/model/* 全 404，而 success_rate 类指标只统生成侧、端点级失效
        对监控不可见。本测试把「每个模型端点必须出现在 schema」固化为门禁，
        同类事故在 CI 即被拦截，不再依赖 per-file-ignores 白名单逐文件豁免。
        """
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]
        expected_model_paths = [
            "/api/model/status",
            "/api/model/load",
            "/api/model/unload",
            "/api/model/preload",
            "/api/model/preload/status",
            "/api/model/switch",
            "/api/model/lora/load",
            "/api/model/lora/unload",
            "/api/model/lora/toggle",
            "/api/model/lora/state",
            "/api/model/lora/list",
            "/api/model/download_hints",
        ]
        missing = [p for p in expected_model_paths if p not in paths]
        assert not missing, f"模型端点从 OpenAPI schema 消失（疑似 re-export 门面被误删）: {missing}"


class TestAPIResponseContract:
    """API 响应格式契约测试。"""

    def test_health_ping_response_format(self, client):
        """健康检查响应格式正确。"""
        resp = client.get("/api/health/ping")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] == "ok"
        assert "timestamp" in data

    def test_model_status_response_format(self, client):
        """模型状态响应格式正确。"""
        resp = client.get("/api/model/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_gpu_status_response_format(self, client):
        """GPU 状态响应格式正确。"""
        resp = client.get("/api/system/gpu/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_history_table_response_format(self, client):
        """历史记录表响应格式正确。"""
        resp = client.get("/api/history/table")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    def test_csrf_rejection_format(self, client):
        """CSRF 拒绝返回结构化错误。"""
        resp = client.post("/api/training/stop")
        assert resp.status_code == 403
        data = resp.json()
        assert isinstance(data, dict)

    def test_404_response_format(self, client):
        """404 响应返回结构化错误。"""
        resp = client.get("/api/nonexistent-xyz-123")
        assert resp.status_code == 404
        data = resp.json()
        assert isinstance(data, dict)


class TestOpenAIAPIContract:
    """OpenAI API 响应格式契约测试。"""

    def test_models_response_format(self, openai_client):
        """/v1/models 返回列表格式。"""
        resp = openai_client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)
        for model in data["data"]:
            assert "id" in model
            assert "object" in model

    def test_speech_503_response_format(self, openai_client):
        """OpenAI speech 无模型时返回 503。"""
        resp = openai_client.post(
            "/v1/audio/speech",
            json={"input": "hello", "model": "tts-1", "voice": "alloy"},
        )
        assert resp.status_code == 503
        data = resp.json()
        assert isinstance(data, dict)

    def test_speech_invalid_model_422(self, openai_client):
        """OpenAI speech 无效模型返回 422。"""
        resp = openai_client.post(
            "/v1/audio/speech",
            json={"input": "hello", "model": "gpt-4", "voice": "alloy"},
        )
        assert resp.status_code == 422

    def test_batch_response_format(self, openai_client):
        """OpenAI batch 端点响应格式正确。"""
        resp = openai_client.post(
            "/v1/audio/speech/batch",
            json={"texts": ["hello"], "model": "tts-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "batch_id" in data
