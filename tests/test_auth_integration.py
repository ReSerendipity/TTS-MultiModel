"""Integration tests for API authentication middleware."""
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.integrated_app.auth import APIAuthMiddleware


@pytest.fixture
def auth_app_enabled():
    """Create a test app with auth enabled and a valid token."""
    app = FastAPI()
    app.add_middleware(APIAuthMiddleware, enabled=True, token="test-secret-token")

    @app.get("/api/test")
    async def test_endpoint():
        return {"status": "ok"}

    @app.get("/api/health/check")
    async def health_endpoint():
        return {"status": "healthy"}

    @app.get("/public/page")
    async def public_endpoint():
        return {"status": "ok"}

    return app


@pytest.fixture
def auth_app_empty_token():
    """Create a test app with auth enabled but empty token."""
    app = FastAPI()
    app.add_middleware(APIAuthMiddleware, enabled=True, token="")

    @app.get("/api/test")
    async def test_endpoint():
        return {"status": "ok"}

    return app


@pytest.fixture
def auth_app_disabled():
    """Create a test app with auth disabled."""
    app = FastAPI()
    app.add_middleware(APIAuthMiddleware, enabled=False, token="")

    @app.get("/api/test")
    async def test_endpoint():
        return {"status": "ok"}

    return app


class TestAPIAuthMiddleware:
    def test_valid_token_accepted(self, auth_app_enabled):
        client = TestClient(auth_app_enabled, raise_server_exceptions=False)
        response = client.get("/api/test", headers={"Authorization": "Bearer test-secret-token"})
        assert response.status_code == 200

    def test_invalid_token_rejected(self, auth_app_enabled):
        client = TestClient(auth_app_enabled, raise_server_exceptions=False)
        response = client.get("/api/test", headers={"Authorization": "Bearer wrong-token"})
        assert response.status_code == 401

    def test_missing_token_rejected(self, auth_app_enabled):
        client = TestClient(auth_app_enabled, raise_server_exceptions=False)
        response = client.get("/api/test")
        assert response.status_code == 401

    def test_empty_token_rejects_all(self, auth_app_empty_token):
        """When token is empty, all requests should be rejected."""
        client = TestClient(auth_app_empty_token, raise_server_exceptions=False)
        response = client.get("/api/test", headers={"Authorization": "Bearer "})
        assert response.status_code == 401

    def test_empty_token_rejects_no_header(self, auth_app_empty_token):
        client = TestClient(auth_app_empty_token, raise_server_exceptions=False)
        response = client.get("/api/test")
        assert response.status_code == 401

    def test_health_endpoint_bypasses_auth(self, auth_app_enabled):
        client = TestClient(auth_app_enabled, raise_server_exceptions=False)
        response = client.get("/api/health/check")
        assert response.status_code == 200

    def test_public_pages_bypass_auth(self, auth_app_enabled):
        client = TestClient(auth_app_enabled, raise_server_exceptions=False)
        response = client.get("/public/page")
        assert response.status_code == 200

    def test_auth_disabled_allows_all(self, auth_app_disabled):
        client = TestClient(auth_app_disabled, raise_server_exceptions=False)
        response = client.get("/api/test")
        assert response.status_code == 200
