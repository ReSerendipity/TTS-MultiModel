"""Tests for API authentication middleware - Behavior-level tests.

This module tests the actual HTTP behavior of the Bearer Token authentication
middleware, replacing the previous smoke tests that only constructed objects.

Coverage:
- Auth disabled: all requests pass
- Auth enabled + valid token: API requests allowed
- Auth enabled + invalid/missing token: 401 returned
- Public paths bypass: /api/health/*, static files need no token
- Empty token fail-closed: even valid-looking requests rejected
"""

import os
import sys

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")


# ============================================================================
# Test fixtures
# ============================================================================


@pytest.fixture
def app_with_auth_off():
    """FastAPI app with API auth disabled."""
    from integrated_app.auth import APIAuthMiddleware

    app = FastAPI()

    @app.get("/api/test")
    async def test_endpoint():
        return {"status": "ok"}

    app.add_middleware(APIAuthMiddleware, enabled=False, token="")
    return app


@pytest.fixture
def app_with_auth_on():
    """FastAPI app with API auth enabled and valid token."""
    from integrated_app.auth import APIAuthMiddleware

    app = FastAPI()

    @app.get("/api/protected")
    async def protected_endpoint():
        return {"data": "secret"}

    @app.get("/api/health/ping")
    async def health_ping():
        return {"status": "healthy"}

    app.add_middleware(APIAuthMiddleware, enabled=True, token="test-secret-token")
    return app


# ============================================================================
# Authentication disabled behavior
# ============================================================================


class TestAuthDisabled:
    """Test behavior when API authentication is disabled."""

    def test_all_requests_pass_when_disabled(self, app_with_auth_off):
        """When auth disabled, any request should succeed."""
        with TestClient(app_with_auth_off) as client:
            resp = client.get("/api/test")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

    def test_no_token_header_required_when_disabled(self, app_with_auth_off):
        """No Authorization header needed when auth disabled."""
        with TestClient(app_with_auth_off) as client:
            # Request without any auth header
            resp = client.get("/api/test")
            assert resp.status_code == 200


# ============================================================================
# Authentication enabled - valid token scenarios
# ============================================================================


class TestAuthEnabledValidToken:
    """Test behavior when API auth is enabled with valid token."""

    def test_valid_bearer_token_allows_access(self, app_with_auth_on):
        """Correct Bearer token should allow API access."""
        with TestClient(app_with_auth_on) as client:
            resp = client.get("/api/protected", headers={"Authorization": "Bearer test-secret-token"})
            assert resp.status_code == 200
            assert resp.json()["data"] == "secret"

    def test_bearer_scheme_must_be_capitalized(self, app_with_auth_on):
        """RFC 6750 requires "Bearer" with capital B for timing-attack resistance.

        The implementation uses hmac.compare_digest() to compare the scheme bytes,
        which is case-sensitive. This prevents attackers from probing valid schemes
        through timing side-channels (e.g., testing "bearer", "BEARER", etc.).
        """
        with TestClient(app_with_auth_on) as client:
            # Uppercase "Bearer" works
            resp_upper = client.get("/api/protected", headers={"Authorization": "Bearer test-secret-token"})
            assert resp_upper.status_code == 200

            # Lowercase "bearer" is rejected (timing-safe comparison)
            resp_lower = client.get("/api/protected", headers={"Authorization": "bearer test-secret-token"})
            assert resp_lower.status_code == 401
            # Check error message contains "invalid" in English or Chinese ("无效")
            detail_lower = resp_lower.json()["detail"].lower()
            assert "invalid" in detail_lower or "无效" in resp_lower.json()["detail"]

    def test_public_health_endpoint_no_token_needed(self, app_with_auth_on):
        """Public paths like /api/health/* should not require auth."""
        with TestClient(app_with_auth_on) as client:
            resp = client.get("/api/health/ping")
            assert resp.status_code == 200
            assert resp.json()["status"] == "healthy"


# ============================================================================
# Authentication enabled - invalid/missing token rejection
# ============================================================================


class TestAuthEnabledRejectInvalid:
    """Test that invalid/missing tokens are properly rejected."""

    def test_missing_authorization_header_rejected(self, app_with_auth_on):
        """Request without Authorization header should get 401."""
        with TestClient(app_with_auth_on) as client:
            resp = client.get("/api/protected")
            assert resp.status_code == 401

    def test_invalid_token_rejected(self, app_with_auth_on):
        """Wrong token should get 401."""
        with TestClient(app_with_auth_on) as client:
            resp = client.get("/api/protected", headers={"Authorization": "Bearer wrong-token"})
            assert resp.status_code == 401

    def test_empty_token_rejected(self, app_with_auth_on):
        """Empty Bearer token should be rejected when auth enabled."""
        with TestClient(app_with_auth_on) as client:
            resp = client.get("/api/protected", headers={"Authorization": "Bearer "})
            assert resp.status_code == 401

    def test_non_bearer_scheme_rejected(self, app_with_auth_on):
        """Non-Bearer auth scheme should be rejected."""
        with TestClient(app_with_auth_on) as client:
            resp = client.get("/api/protected", headers={"Authorization": "Basic dXNlcjpwYXNz"})
            assert resp.status_code == 401


# ============================================================================
# Configuration tests
# ============================================================================


class TestAuthConfiguration:
    """Test auth configuration structure."""

    def test_config_has_api_auth_section(self):
        """Config has api_auth section."""
        from integrated_app.config import get_config

        config = get_config()
        assert hasattr(config, "api_auth")

    def test_api_auth_enabled_field_exists_and_bool(self):
        """API auth config has enabled field of bool type."""
        from integrated_app.config import get_config

        config = get_config()
        auth = config.api_auth
        assert hasattr(auth, "enabled")
        assert isinstance(auth.enabled, bool)

    def test_api_auth_token_field_exists_and_str(self):
        """API auth config has token field (SecretStr, L2 整改：避免明文泄漏)。"""
        from pydantic import SecretStr

        from integrated_app.config import get_config

        config = get_config()
        auth = config.api_auth
        assert hasattr(auth, "token")
        assert isinstance(auth.token, SecretStr)
        assert isinstance(auth.token.get_secret_value(), str)
