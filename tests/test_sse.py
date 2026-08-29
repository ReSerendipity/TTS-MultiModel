class TestSSE:
    def test_format_time_estimate(self):
        from integrated_app.routes.sse import _format_time_estimate

        assert "秒" in _format_time_estimate(5)
        assert "约" in _format_time_estimate(30)
        assert "分" in _format_time_estimate(120)

    def test_sse_router_exists(self):
        from integrated_app.routes.sse import router

        assert router is not None

    def test_sse_endpoint_defined(self):
        from integrated_app.routes.sse import router

        routes = [r.path for r in router.routes]
        assert "/api/sse/events" in routes
