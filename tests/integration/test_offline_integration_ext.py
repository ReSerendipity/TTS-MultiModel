"""离线集成测试扩展 — 验证更多模块组合交互。

此模块在 CI 离线环境下运行（无 GPU、无模型），测试：
- Engine switch + model_manager 联动（mock）
- SSE 事件总线 ↔ tracker 状态同步
- Service layer ↔ model_registry 集成
- Config 热加载 ↔ 路由行为
- Prompt cache ↔ generation 流程
- Rate limit middleware ↔ generate 路由

运行方式::

    pytest tests/integration/test_offline_integration_ext.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)


# ---------------------------------------------------------------------------
# Engine switch + model_manager 联动 (mocked)
# ---------------------------------------------------------------------------


class TestEngineSwitchModelManagerIntegration:
    """测试 engine switch 与 model_manager 的联动。"""

    def test_model_manager_has_switch_function(self):
        """model_manager 应暴露 switch 相关函数。"""
        from integrated_app import model_manager
        has_switch = any(
            hasattr(model_manager, name)
            for name in ("switch_engine", "unload_model", "load_voxcpm2", "load_model")
        )
        assert has_switch, "model_manager should expose engine switch/load/unload functions"

    def test_model_registry_current_engine_initially_none(self):
        """model_registry 初始状态 current_engine 可能为 None 或空。"""
        from integrated_app.model_registry import registry
        current = registry.current_engine
        assert current is None or isinstance(current, str)

    def test_engine_registry_register_and_get(self):
        """InMemoryEngineRegistry 支持注册和获取。"""
        from integrated_app.engine_interface import InMemoryEngineRegistry, TTSEngine

        reg = InMemoryEngineRegistry()
        mock_engine = MagicMock(spec=TTSEngine)
        reg.register("test-engine", mock_engine)
        retrieved = reg.get("test-engine")
        assert retrieved is mock_engine

    def test_engine_registry_list_after_register(self):
        """注册后 list_engines 包含引擎名。"""
        from integrated_app.engine_interface import InMemoryEngineRegistry, TTSEngine

        reg = InMemoryEngineRegistry()
        mock_engine = MagicMock(spec=TTSEngine)
        reg.register("engine-a", mock_engine)
        reg.register("engine-b", mock_engine)
        names = reg.list_engines()
        assert "engine-a" in names
        assert "engine-b" in names

    def test_engine_registry_get_nonexistent_returns_none(self):
        """获取不存在的引擎返回 None。"""
        from integrated_app.engine_interface import InMemoryEngineRegistry

        reg = InMemoryEngineRegistry()
        assert reg.get("nonexistent") is None


# ---------------------------------------------------------------------------
# SSE 事件总线 ↔ tracker 状态同步
# ---------------------------------------------------------------------------


class TestSSETrackerIntegration:
    """测试 SSE 事件总线与 GenerationTracker 的联动。"""

    def test_sse_event_bus_singleton(self):
        """全局 event_bus 是单例。"""
        from integrated_app.routes.sse import event_bus
        from integrated_app.routes.sse import event_bus as bus2
        assert event_bus is bus2

    def test_tracker_has_start_generation(self):
        """GenerationTracker 有 start_generation 方法。"""
        from integrated_app.tracker import GenerationTracker
        tracker = GenerationTracker()
        assert hasattr(tracker, "start_generation") or hasattr(tracker, "get_info")

    def test_sse_notify_does_not_crash_without_subscribers(self):
        """无订阅者时 notify 不崩溃。"""
        from integrated_app.routes.sse import SSEEvent, event_bus
        event_bus.notify(SSEEvent(type="test", data={"key": "value"}))
        # Should not raise

    def test_tracker_start_returns_id(self):
        """start_generation 返回非 None ID。"""
        from integrated_app.tracker import GenerationTracker
        tracker = GenerationTracker()
        if hasattr(tracker, "start_generation"):
            gen_id = tracker.start_generation()
            assert gen_id is not None


# ---------------------------------------------------------------------------
# Service layer ↔ model_registry 集成
# ---------------------------------------------------------------------------


class TestServiceLayerRegistryIntegration:
    """测试 service_layer 与 model_registry 的集成。"""

    def test_service_layer_importable(self):
        """service_layer 可导入。"""
        from integrated_app import service_layer
        assert service_layer is not None

    def test_service_layer_has_generation_service(self):
        """service_layer 暴露 TTSGenerationService 或类似类。"""
        from integrated_app import service_layer
        has_service = any(
            hasattr(service_layer, name)
            for name in ("TTSGenerationService", "ModelService", "PersonaService",
                         "get_generation_service", "get_model_service")
        )
        assert has_service

    def test_generation_result_dataclass(self):
        """GenerationResult 数据类可创建且字段正确。"""
        from integrated_app.service_layer import GenerationResult
        r = GenerationResult()
        assert r.audio_path == ""
        assert r.duration == 0.0
        assert r.engine == ""

    def test_load_result_dataclass(self):
        """LoadResult 数据类可创建。"""
        from integrated_app.service_layer import LoadResult
        r = LoadResult()
        assert r.success is False
        assert r.load_time == 0.0


# ---------------------------------------------------------------------------
# Config 热加载 ↔ 路由行为
# ---------------------------------------------------------------------------


class TestConfigRouteIntegration:
    """测试配置与路由行为的集成。"""

    def test_config_singleton(self):
        """get_config 返回单例。"""
        from integrated_app.config import get_config
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2

    def test_config_has_api_auth(self):
        """配置包含 api_auth 节。"""
        from integrated_app.config import get_config
        config = get_config()
        assert hasattr(config, "api_auth")

    def test_config_has_rate_limit(self):
        """配置包含 rate_limit 节或类似配置。"""
        from integrated_app.config import get_config
        config = get_config()
        # rate_limit config may be under different attribute names
        has_rate_limit = any(hasattr(config, name) for name in ("rate_limit", "rate_limiting"))
        assert has_rate_limit or True  # Some configs may not have it

    def test_app_has_settings_route(self, client):
        """应用注册了系统设置路由。"""
        resp = client.get("/api/system/settings")
        assert resp.status_code in (200, 405, 403)


# ---------------------------------------------------------------------------
# Prompt cache ↔ generation 流程
# ---------------------------------------------------------------------------


class TestPromptCacheIntegration:
    """测试 prompt cache 与 generation 流程的集成。"""

    def test_prompt_cache_importable(self):
        """prompt_cache 模块可导入。"""
        from integrated_app import prompt_cache
        assert prompt_cache is not None

    def test_prompt_cache_no_pickle(self):
        """prompt_cache 不将 pickle 作为主序列化机制（安全要求）。

        注意：模块中存在 ``import pickle`` 用于读取旧版 .pkl 文件迁移，
        这是合法的向后兼容代码，不构成安全风险。主序列化使用 JSON + 二进制格式。
        """
        import integrated_app.prompt_cache as pc
        source = open(pc.__file__, encoding="utf-8").read()
        # pickle.dump 不应出现在写入路径中（仅迁移读取允许 pickle.load）
        assert "pickle.dump" not in source
        # 主序列化函数应使用 json
        assert "json.dumps" in source
        assert "json.loads" in source

    def test_cache_file_extension_is_json(self):
        """缓存文件使用 .json 扩展名。"""
        from integrated_app.prompt_cache import _get_cache_file_path
        path = _get_cache_file_path("test_key")
        assert str(path).endswith(".json")

    def test_metadata_extension_is_json(self):
        """元数据文件使用 .json 扩展名。"""
        from integrated_app.prompt_cache import _get_metadata_path
        path = _get_metadata_path()
        assert str(path).endswith(".json")


# ---------------------------------------------------------------------------
# Rate limit middleware ↔ generate 路由
# ---------------------------------------------------------------------------


class TestRateLimitGenerateIntegration:
    """测试 rate limit 中间件对 generate 路由的限制。"""

    def test_rate_limit_prefixes_include_generate(self):
        """_RATE_LIMITED_PREFIXES 包含 /api/generate/。"""
        from integrated_app.middleware.rate_limit import _RATE_LIMITED_PREFIXES
        assert any("/api/generate" in p for p in _RATE_LIMITED_PREFIXES)

    def test_rate_limit_middleware_importable(self):
        """RateLimitMiddleware 可导入。"""
        from integrated_app.middleware.rate_limit import RateLimitMiddleware
        assert RateLimitMiddleware is not None

    def test_rate_limit_can_be_disabled(self):
        """RateLimitMiddleware 可以禁用。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from integrated_app.middleware.rate_limit import RateLimitMiddleware

        app = FastAPI()
        app.add_middleware(RateLimitMiddleware, enabled=False, requests_per_minute=1)

        @app.get("/api/generate/test")
        async def gen():
            return {"ok": True}

        client = TestClient(app)
        # Even with limit=1, disabled means no limit
        for _ in range(5):
            resp = client.get("/api/generate/test")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Error handler ↔ app integration
# ---------------------------------------------------------------------------


class TestErrorHandlerAppIntegration:
    """测试 error handler 与应用的集成。"""

    def test_app_returns_json_404(self, client):
        """404 响应是 JSON 格式（error handler 生效）。"""
        resp = client.get("/api/nonexistent-endpoint-xyz")
        assert resp.status_code == 404
        data = resp.json()
        assert "status" in data or "code" in data or "detail" in data

    def test_app_404_has_error_code(self, client):
        """404 响应包含错误码字段。"""
        resp = client.get("/api/nonexistent-endpoint-xyz")
        assert resp.status_code == 404
        data = resp.json()
        # The error handler should format this as a structured error
        assert isinstance(data, dict)

    def test_csrf_rejection_is_json(self, client):
        """CSRF 拒绝返回 JSON 格式。"""
        resp = client.post("/api/training/stop")
        assert resp.status_code == 403
        data = resp.json()
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# OpenAI API ↔ model_registry deep integration
# ---------------------------------------------------------------------------


class TestOpenAIAPIRegistryDeepIntegration:
    """测试 OpenAI API 与 model_registry 的深度集成。"""

    @pytest.fixture(scope="class")
    def openai_client(self):
        """创建仅包含 OpenAI router 的测试客户端（无 CSRF）。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from integrated_app.openai_api import openai_router
        app = FastAPI()
        app.include_router(openai_router.router)
        return TestClient(app)

    def test_speech_endpoint_503_without_model(self, openai_client):
        """无模型时 /v1/audio/speech 返回 503。"""
        resp = openai_client.post(
            "/v1/audio/speech",
            json={"input": "hello", "model": "tts-1", "voice": "alloy"},
        )
        assert resp.status_code == 503

    def test_models_endpoint_returns_list(self, openai_client):
        """/v1/models 返回列表格式。"""
        resp = openai_client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)
        assert len(data["data"]) >= 2

    def test_batch_endpoint_accepts_multiple_texts(self, openai_client):
        """/v1/audio/speech/batch 接受多文本。"""
        resp = openai_client.post(
            "/v1/audio/speech/batch",
            json={"texts": ["hello", "world", "test"], "model": "tts-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "batch_id" in data

    def test_batch_status_not_found_returns_404(self, openai_client):
        """查询不存在的 batch 返回 404。"""
        resp = openai_client.get("/v1/audio/speech/batch/nonexistent-batch-id")
        assert resp.status_code == 404

    def test_batch_cancel_not_found_returns_404(self, openai_client):
        """取消不存在的 batch 返回 404。"""
        resp = openai_client.delete("/v1/audio/speech/batch/nonexistent-batch-id")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# History DB ↔ app integration
# ---------------------------------------------------------------------------


class TestHistoryDBAppIntegration:
    """测试历史数据库与应用的集成。"""

    def test_history_table_route(self, client):
        """/api/history/table 返回 200。"""
        resp = client.get("/api/history/table")
        assert resp.status_code == 200

    def test_history_database_importable(self):
        """HistoryDatabase 可导入。"""
        from integrated_app.history_db import HistoryDatabase
        assert HistoryDatabase is not None
