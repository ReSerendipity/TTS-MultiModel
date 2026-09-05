"""routes/system/logs.py 单元测试 — 操作日志查询与清理。

覆盖目标模块: app/integrated_app/routes/system/logs.py
"""


class TestLogHelpers:
    def test_log_operation_and_query(self):
        from integrated_app.routes.system import logs

        logs.log_operation("test_operation", "测试操作", details={"key": "value"})
        result = logs.get_logs(level=None, action=None, page=1, page_size=10, start_ts=None, end_ts=None)
        assert hasattr(result, "items")
        assert hasattr(result, "total_count")

    def test_log_operation_none(self):
        from integrated_app.routes.system import logs

        # 异常参数不应崩溃
        logs.log_operation(None, None, details=None)

    def test_build_filter_sql(self):
        from integrated_app.routes.system.logs import _build_filter_sql

        where, params = _build_filter_sql(level="info", action="generate", start_ts=1, end_ts=2)
        assert "level = ?" in where
        assert "action = ?" in where
        assert "ts_ms >=" in where
        assert "ts_ms <=" in where
        assert len(params) == 4

    def test_build_filter_sql_empty(self):
        from integrated_app.routes.system.logs import _build_filter_sql

        where, params = _build_filter_sql(level=None, action=None, start_ts=None, end_ts=None)
        assert where == ""
        assert params == []

    def test_get_operation_log_singleton(self):
        from integrated_app.routes.system.logs import get_operation_log

        assert get_operation_log() is not None


class TestLogsAccessControl:
    """运维稳定性评估 P1-1：日志端点访问控制（require_logs_access）行为测试。"""

    def _app_with_client_host(self, host: str | None):
        """构造一个把 request.client.host 固定为 host 的最小 app（挂载真实依赖）。"""
        from fastapi import Depends, FastAPI, Request
        from fastapi.testclient import TestClient

        from integrated_app.routes.system import logs

        app = FastAPI()

        @app.get("/probe")
        def probe(request: Request, _access: None = Depends(logs.require_logs_access)):
            return {"ok": True}

        # 通过 ASGI scope 注入 client.host，模拟不同来源
        @app.middleware("http")
        async def _force_client(request: Request, call_next):
            request.scope["client"] = (host, 0) if host is not None else None
            return await call_next(request)

        return TestClient(app)

    def test_loopback_allowed(self):
        """回环地址在无鉴权时应放行（本机浏览器场景）。"""
        client = self._app_with_client_host("127.0.0.1")
        resp = client.get("/probe")
        assert resp.status_code == 200

    def test_no_client_allowed(self):
        """无 client 信息（测试态/域套接字）应放行。"""
        client = self._app_with_client_host(None)
        resp = client.get("/probe")
        assert resp.status_code == 200

    def test_remote_denied_when_auth_off(self, monkeypatch):
        """对外暴露且未启用鉴权时，非回环来源应 403。"""
        from integrated_app.routes.system import logs

        monkeypatch.setattr(logs, "_api_auth_enabled", lambda: False)
        client = self._app_with_client_host("203.0.113.9")
        resp = client.get("/probe")
        assert resp.status_code == 403

    def test_remote_allowed_when_auth_on(self, monkeypatch):
        """启用鉴权后（中间件已强制 Bearer），非回环来源应放行。"""
        from integrated_app.routes.system import logs

        monkeypatch.setattr(logs, "_api_auth_enabled", lambda: True)
        client = self._app_with_client_host("203.0.113.9")
        resp = client.get("/probe")
        assert resp.status_code == 200

    def test_real_endpoints_guarded(self):
        """真实 GET /api/system/logs 与 DELETE /logs/clean 必须挂有依赖。"""
        import inspect

        from integrated_app.routes.system import logs

        for fn in (logs.get_logs, logs.clean_logs):
            params = inspect.signature(fn).parameters
            assert "_access" in params, f"{fn.__name__} 缺少 require_logs_access 依赖"
