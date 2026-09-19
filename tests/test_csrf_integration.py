"""Integration tests for CSRF protection middleware."""

import pytest
from app.integrated_app.middleware.csrf import CSRFMiddleware
from fastapi import FastAPI
from starlette.testclient import TestClient


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


class TestDownstreamErrorIsNotDisguisedAsCsrf:
    """GOTCHAS #132：dispatch 的 fail-closed 兜底只能吞 CSRF 自己的错。

    以前整个 dispatch（含结尾的 ``await call_next``）套在同一个 try 里，任何路由内部
    未处理异常都变成 ``CSRF_FATAL`` 403 —— 既绕过 error_handler 的中文错误块，
    又把 5xx 从错误率统计里抹成 403，排查时还会把人往安全模块上引。
    """

    @staticmethod
    def _app_with_boom():
        app = FastAPI()
        app.add_middleware(CSRFMiddleware)

        @app.post("/boom")
        async def boom():
            raise ZeroDivisionError("下游自己的 bug")

        return app

    def test_downstream_exception_propagates_instead_of_403(self):
        client = TestClient(self._app_with_boom())  # raise_server_exceptions=True（默认）
        with pytest.raises(ZeroDivisionError):
            client.post("/boom", headers={"Cookie": "csrf_token=x", "X-CSRF-Token": "x"})

    def test_response_is_500_and_never_mentions_csrf(self):
        client = TestClient(self._app_with_boom(), raise_server_exceptions=False)
        resp = client.post("/boom", headers={"Cookie": "csrf_token=x", "X-CSRF-Token": "x"})
        assert resp.status_code == 500, f"下游异常应归 5xx，实际 {resp.status_code}: {resp.text[:120]}"
        assert "CSRF" not in resp.text

    def test_csrf_internal_failure_still_fails_closed(self):
        """反向自证：CSRF 自身出错仍然必须 403，不能被这次改动放宽成放行或 500。"""
        app = self._app_with_boom()
        client = TestClient(app, raise_server_exceptions=False)

        def explode(*_a, **_kw):
            raise RuntimeError("secrets 炸了")

        original = CSRFMiddleware._generate_token
        CSRFMiddleware._generate_token = explode
        try:
            resp = client.post("/boom")  # 无 cookie → 走签发路径 → 内部异常
        finally:
            CSRFMiddleware._generate_token = original
        assert resp.status_code == 403
        assert "CSRF" in resp.text
