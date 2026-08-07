"""Integration tests for CSRF protection middleware."""
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from bin.integrated_app.middleware.csrf import CSRFMiddleware


@pytest.fixture
def csrf_app():
    """Create a test app with CSRF middleware."""
    app = FastAPI()
    app.add_middleware(CSRFMiddleware)

    @app.get("/api/data")
    async def get_data():
        return {"status": "ok"}

    @app.post("/api/submit")
    async def submit_data():
        return {"status": "ok"}

    @app.put("/api/update")
    async def update_data():
        return {"status": "ok"}

    @app.delete("/api/delete")
    async def delete_data():
        return {"status": "ok"}

    return app


class TestCSRFMiddleware:
    def test_get_requests_allowed(self, csrf_app):
        client = TestClient(csrf_app, raise_server_exceptions=False)
        response = client.get("/api/data")
        assert response.status_code == 200

    def test_post_without_csrf_token_rejected(self, csrf_app):
        client = TestClient(csrf_app, raise_server_exceptions=False)
        response = client.post("/api/submit")
        assert response.status_code == 403

    def test_htmx_request_not_bypassed(self, csrf_app):
        """HTMX requests should NOT bypass CSRF validation."""
        client = TestClient(csrf_app, raise_server_exceptions=False)
        response = client.post("/api/submit", headers={"HX-Request": "true"})
        assert response.status_code == 403

    def test_post_with_valid_csrf_token(self, csrf_app):
        """POST with matching CSRF cookie and header should succeed."""
        client = TestClient(csrf_app, raise_server_exceptions=False)
        # First GET to get the CSRF cookie
        get_response = client.get("/api/data")
        csrf_token = get_response.cookies.get("csrf_token")
        assert csrf_token is not None

        # Then POST with the token (cookie set on client instance)
        client.cookies.set("csrf_token", csrf_token)
        response = client.post(
            "/api/submit",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert response.status_code == 200

    def test_post_with_mismatched_csrf_token(self, csrf_app):
        """POST with mismatched CSRF token should be rejected."""
        client = TestClient(csrf_app, raise_server_exceptions=False)
        # First GET to get the CSRF cookie
        get_response = client.get("/api/data")
        csrf_token = get_response.cookies.get("csrf_token")
        client.cookies.set("csrf_token", csrf_token)

        # POST with wrong token
        response = client.post(
            "/api/submit",
            headers={"X-CSRF-Token": "wrong-token"},
        )
        assert response.status_code == 403

    def test_put_requires_csrf_token(self, csrf_app):
        client = TestClient(csrf_app, raise_server_exceptions=False)
        response = client.put("/api/update")
        assert response.status_code == 403

    def test_delete_requires_csrf_token(self, csrf_app):
        client = TestClient(csrf_app, raise_server_exceptions=False)
        response = client.delete("/api/delete")
        assert response.status_code == 403
