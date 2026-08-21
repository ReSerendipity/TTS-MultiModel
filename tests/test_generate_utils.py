"""routes/generate/utils.py 单元测试 — 生成辅助工具。

覆盖目标模块: app/integrated_app/routes/generate/utils.py
"""

from integrated_app.routes.generate.utils import (
    _parse_bool_form,
    _safe_error_msg,
    build_generation_error_response,
    format_sse_event,
    new_task_id,
)


class TestFormatSSEEvent:
    def test_basic_event(self):
        raw = format_sse_event("progress", {"percent": 50})
        assert "event: progress" in raw
        assert '"percent": 50' in raw
        assert raw.endswith("\n\n")
        assert "retry: 3000" in raw

    def test_data_serialized(self):
        raw = format_sse_event("status", {"engine": "voxcpm2"})
        assert "engine" in raw


class TestNewTaskId:
    def test_unique_ids(self):
        ids = {new_task_id() for _ in range(100)}
        assert len(ids) == 100

    def test_returns_str(self):
        assert isinstance(new_task_id(), str)


class TestParseBoolForm:
    def test_truthy(self):
        for truthy in ("true", "1", "yes", "True", "TRUE"):
            assert _parse_bool_form(truthy) is True

    def test_falsy(self):
        for falsy in ("false", "0", "no", "off", "", None, "on"):
            assert _parse_bool_form(falsy) is False

    def test_bool_input(self):
        assert _parse_bool_form(True) is True
        assert _parse_bool_form(False) is False


class TestSafeErrorMsg:
    def test_str_exception(self):
        assert "boom" in _safe_error_msg(ValueError("boom"))

    def test_runtime_error_cuda_hint(self):
        msg = _safe_error_msg(RuntimeError("CUDA out of memory"))
        assert "显存不足" in msg

    def test_file_not_found(self):
        assert "不存在" in _safe_error_msg(FileNotFoundError())

    def test_unknown_exception_fallback(self):
        assert _safe_error_msg(ValueError("x"))  # 不崩溃即可


class TestBuildErrorResponse:
    def test_returns_json(self):
        import json

        from integrated_app.exceptions import GenerationError

        resp = build_generation_error_response(GenerationError("出错了"), "gen-1")
        assert resp.status_code == 500
        data = json.loads(resp.body)
        assert data["status"] == "error"
        assert data["task_id"] == "gen-1"
        assert data["error"]["message"] == "出错了"
