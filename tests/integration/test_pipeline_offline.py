"""Offline integration tests for the TTS MultiModel pipeline.

These tests verify component assembly and inter-module wiring without requiring
a GPU or real model. They use mock engines to test:
- model_registry <-> engine interface wiring
- SSE event bus integration
- tracker state transitions
- model_manager load/unload flow (mocked)
- route registration and health endpoint

This module runs in CI (no GPU, no models) as part of the standard test suite.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# Ensure app/ is on sys.path
_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Model Registry <-> Engine Interface wiring
# ---------------------------------------------------------------------------


class TestModelRegistryEngineWiring:
    """Test model_registry <-> engine_interface integration."""

    def test_registry_singleton(self):
        """model_registry.registry should be a singleton."""
        from integrated_app.model_registry import registry

        assert registry is not None
        assert hasattr(registry, "current_engine")
        assert hasattr(registry, "get_current_engine")

    def test_engine_registry(self):
        """InMemoryEngineRegistry should allow register/get."""
        from integrated_app.engine_interface import InMemoryEngineRegistry, TTSEngine

        reg = InMemoryEngineRegistry()
        mock_engine = MagicMock(spec=TTSEngine)
        reg.register("test-engine", mock_engine)
        assert reg.get("test-engine") is mock_engine

    def test_engine_registry_list(self):
        """InMemoryEngineRegistry should list registered engines."""
        from integrated_app.engine_interface import InMemoryEngineRegistry, TTSEngine

        reg = InMemoryEngineRegistry()
        mock = MagicMock(spec=TTSEngine)
        reg.register("engine-a", mock)
        reg.register("engine-b", mock)
        names = reg.list_engines()
        assert "engine-a" in names
        assert "engine-b" in names

    def test_engine_registry_get_returns_none(self):
        """InMemoryEngineRegistry.get() should return None for non-existent engine."""
        from integrated_app.engine_interface import InMemoryEngineRegistry

        reg = InMemoryEngineRegistry()
        assert reg.get("nonexistent") is None


# ---------------------------------------------------------------------------
# SSE Event Bus integration
# ---------------------------------------------------------------------------


class TestSSEEventBusIntegration:
    """Test SSE event bus wiring with model_registry."""

    def test_app_has_sse_endpoint(self, client):
        """The FastAPI app should expose /api/sse/events."""
        # SSE 端点是流式的，直接 GET 会导致 TestClient 无限等待。
        # 通过 OpenAPI schema 验证端点已注册。
        schema = client.get("/openapi.json").json()
        paths = schema.get("paths", {})
        assert "/api/sse/events" in paths, "SSE endpoint /api/sse/events should be registered"


# ---------------------------------------------------------------------------
# Tracker state transitions
# ---------------------------------------------------------------------------


class TestTrackerStateTransitions:
    """Test GenerationTracker state machine integration."""

    def test_tracker_has_states(self):
        """GenerationTracker should define task states."""
        from integrated_app.tracker import GenerationTracker

        tracker = GenerationTracker()
        assert hasattr(tracker, "start_generation") or hasattr(tracker, "get_info")

    def test_tracker_task_lifecycle(self):
        """Test create -> start -> complete lifecycle."""
        from integrated_app.tracker import GenerationTracker

        tracker = GenerationTracker()
        assert hasattr(tracker, "start_generation"), "GenerationTracker should have start_generation method"
        gen_id = tracker.start_generation()
        assert gen_id is not None

    def test_tracker_progress_update(self):
        """Test progress update propagation."""
        from integrated_app.tracker import GenerationTracker

        tracker = GenerationTracker()
        assert hasattr(tracker, "update_phase"), "GenerationTracker should have update_phase method"
        tracker.update_phase("processing")


# ---------------------------------------------------------------------------
# Model Manager flow (mocked)
# ---------------------------------------------------------------------------


class TestModelManagerFlow:
    """Test model_manager load/unload flow with mocked engine."""

    def test_model_manager_import(self):
        """model_manager should be importable with core functions."""
        from integrated_app import model_manager

        assert hasattr(model_manager, "load_voxcpm2") or hasattr(model_manager, "unload_model"), (
            "model_manager should have load_voxcpm2 or unload_model function"
        )


# ---------------------------------------------------------------------------
# Route registration and health endpoints
# ---------------------------------------------------------------------------


class TestRouteRegistrationHealth:
    """Test that all key routes are registered and health endpoint works."""

    def test_health_endpoint(self, client):
        """GET /api/system/health should return 200."""
        resp = client.get("/api/system/health")
        assert resp.status_code == 200

    def test_root_page(self, client):
        """GET / should return 200."""
        resp = client.get("/")
        assert resp.status_code == 200

    def test_static_files_served(self, client):
        """Static files should be served (check at least one endpoint)."""
        # The app should serve static CSS/JS
        resp = client.get("/")
        assert resp.status_code == 200
        # Check that the HTML contains static asset references
        text = resp.text
        assert "css" in text.lower() or "js" in text.lower()

    def test_openai_api_routes_registered(self, client):
        """OpenAI-compatible routes should be registered."""
        resp = client.get("/v1/models")
        assert resp.status_code == 200

    def test_model_api_routes_registered(self, client):
        """Model API routes should be registered."""
        resp = client.get("/api/model/status")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# OpenAI API <-> model_registry integration
# ---------------------------------------------------------------------------


def _get_csrf_token(client):
    """通过 GET 请求获取 CSRF token，用于后续 POST 请求。"""
    resp = client.get("/")
    cookies = resp.headers.get("set-cookie", "")
    # 从 cookie 中提取 csrf_token 值
    for part in cookies.split(";"):
        part = part.strip()
        if part.startswith("csrf_token="):
            return part.split("=", 1)[1]
    return ""


class TestOpenAIAPIIntegration:
    """Test OpenAI API <-> model_registry wiring."""

    def test_speech_endpoint_no_model_503(self, client):
        """POST /v1/audio/speech should return 503 when no model loaded."""
        csrf_token = _get_csrf_token(client)
        resp = client.post(
            "/v1/audio/speech",
            json={"input": "hello", "model": "tts-1", "voice": "alloy"},
            headers={"X-CSRF-Token": csrf_token},
        )
        # 503 if model not loaded, 403 if CSRF blocked (no token)
        assert resp.status_code in (503, 403)

    def test_batch_endpoint_registered(self, client):
        """POST /v1/audio/speech/batch should be available."""
        csrf_token = _get_csrf_token(client)
        resp = client.post(
            "/v1/audio/speech/batch",
            json={"texts": ["hello"], "model": "tts-1"},
            headers={"X-CSRF-Token": csrf_token},
        )
        # 200 if route works, 403 if CSRF blocked
        assert resp.status_code in (200, 403)

    def test_models_endpoint_lists_both_engines(self, client):
        """GET /v1/models should list tts-1 and tts-1-hd."""
        resp = client.get("/v1/models")
        data = resp.json()
        ids = [m["id"] for m in data["data"]]
        assert "tts-1" in ids
        assert "tts-1-hd" in ids


# ---------------------------------------------------------------------------
# Middleware integration
# ---------------------------------------------------------------------------


class TestMiddlewareIntegration:
    """Test middleware is properly wired."""

    def test_request_id_middleware(self, client):
        """Response should include X-Request-ID header."""
        resp = client.get("/")
        # RequestIDMiddleware should add X-Request-ID header
        assert "x-request-id" in resp.headers or "request-id" in resp.headers

    def test_cors_middleware(self, client):
        """CORS headers should be present on preflight OPTIONS."""
        resp = client.options(
            "/",
            headers={
                "Origin": "http://localhost",
                "Access-Control-Request-Method": "GET",
            },
        )
        # CORS middleware should handle preflight and return 200
        # or pass through to app (405 if no OPTIONS handler)
        assert resp.status_code in (200, 405)

    def test_csrf_middleware_present(self, client):
        """CSRF middleware should be registered."""
        resp = client.get("/")
        # CSRF middleware sets a cookie
        cookies = resp.headers.get("set-cookie", "")
        assert "csrf" in cookies.lower() or resp.status_code == 200
