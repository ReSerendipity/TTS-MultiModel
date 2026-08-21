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
