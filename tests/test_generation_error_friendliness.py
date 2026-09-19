"""发布前友好性修复的回归测试（2026-09-19）。

覆盖四件事，全部来自真机点击时发现的"用户看不到可读提示"问题：
① 参数校验失败（422）此前一律回 JSON，htmx 表单会把原始
   ``{"detail":[{"type":"float_parsing"…`` 直接换进结果区；
② 文本上限：UI 计数器显示 8192/3072，后端却只校验全局 10000，
   用户看到标红仍能提交，API 客户端更可塞进近万字符；
③ cfg/steps 无范围校验（实测 steps=-5 会"成功合成"）；
④ 用户主动取消被记成 ERROR 日志，污染错误面（项目有基于 ERROR 的
   告警与自动重载链路）；
⑤ 页面与引擎不匹配（切了引擎但屏幕上是旧引擎的表单）时，「X 模型未加载」
   这句提示是误导的——引擎其实加载着，只是不是这一页要的（GOTCHAS #131）。
"""

import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from integrated_app.config import ENGINE_TEXT_LIMITS, MAX_TEXT_LENGTH, get_engine_text_limit
from integrated_app.exceptions import GenerationCancelledError, GenerationError
from integrated_app.middleware.error_handler import (
    _validation_html_message,
    _validation_html_or_none,
    _wants_html_fragment,
)
from integrated_app.routes.generate.utils import _check_engine_ready


def _request(headers: dict[str, str]) -> Request:
    """构造一个最小 ASGI 请求。

    注意 ``scope["headers"]`` 必须是 ``(bytes, bytes)`` 列表 —— 传 dict 会让
    Starlette 在逐项解包时抛 ``ValueError: too many values to unpack``。
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "root_path": "",
        "path": "/api/generate/voxcpm_design",
        "raw_path": b"/api/generate/voxcpm_design",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


class TestWantsHtmlFragment:
    def test_htmx_header_means_html(self):
        assert _wants_html_fragment(_request({"hx-request": "true"})) is True

    def test_browser_navigation_accepts_html(self):
        assert _wants_html_fragment(_request({"accept": "text/html,application/xhtml+xml"})) is True

    @pytest.mark.parametrize(
        "accept",
        ["application/json", "*/*", "", "text/html, application/json"],
    )
    def test_api_clients_stay_on_json(self, accept):
        """OpenAI 兼容层等 API 客户端不能被拖进 HTML 分支。"""
        assert _wants_html_fragment(_request({"accept": accept})) is False

    def test_validation_message_is_human_readable(self):
        msg = _validation_html_message(
            [
                {"field": "body.cfg", "message": "Input should be a valid number", "type": "float_parsing"},
                {"field": "body.steps", "message": "...", "type": "less_than_equal"},
            ]
        )
        assert "cfg：需要是数字" in msg and "steps：数值超过允许上限" in msg
        assert "float_parsing" not in msg and "body." not in msg


class TestEngineTextLimit:
    def test_matches_ui_advertised_caps(self):
        """引擎档位必须与 UI 一直显示的数字同源，否则计数器仍在撒谎。"""
        assert ENGINE_TEXT_LIMITS["voxcpm2"] == 8192
        assert ENGINE_TEXT_LIMITS["indextts2"] == 3072
        assert get_engine_text_limit("indextts20") == 3072

    def test_unknown_engine_falls_back_to_stricest_common_cap(self):
        assert get_engine_text_limit(None) == 8192
        assert get_engine_text_limit("not-an-engine") == 8192

    def test_never_exceeds_global_hard_limit(self):
        probe = dict(ENGINE_TEXT_LIMITS)
        probe["huge"] = MAX_TEXT_LENGTH * 10
        with pytest.MonkeyPatch.context() as mp:
            mp.setitem(ENGINE_TEXT_LIMITS, "huge", MAX_TEXT_LENGTH * 10)
            assert get_engine_text_limit("huge") == MAX_TEXT_LENGTH


class TestNumericRangeValidation:
    """cfg/steps 越界要变成"参数有误"，不能被静默接受后照常合成。"""

    @pytest.fixture()
    def client(self) -> TestClient:
        from integrated_app.routes.generate import router as generate_router

        app = FastAPI()
        app.include_router(generate_router)
        return TestClient(app, raise_server_exceptions=False)

    @pytest.mark.parametrize("cfg", ["50", "0"])
    def test_cfg_out_of_range_is_rejected(self, client, cfg):
        resp = client.post(
            "/api/generate/voxcpm_design",
            data={"text": "测试", "instruction": "温柔", "cfg": cfg},
        )
        assert resp.status_code == 422

    def test_negative_steps_is_rejected(self, client):
        """修复前实测：steps=-5 会被接受并成功产出 4.5 秒音频。"""
        resp = client.post(
            "/api/generate/voxcpm_design",
            data={"text": "测试", "instruction": "温柔", "steps": "-5"},
        )
        assert resp.status_code == 422

    def test_in_range_values_pass_validation(self, client):
        resp = client.post(
            "/api/generate/voxcpm_design",
            data={"text": "测试", "instruction": "温柔", "cfg": "2.0", "steps": "32"},
        )
        assert resp.status_code != 422


class TestEngineCapIsActuallyEnforced:
    """UI 计数器显示的上限，后端必须真的按它拦（此前只校验全局 10000）。"""

    @pytest.fixture()
    def client(self, monkeypatch) -> TestClient:
        from integrated_app.model_registry import registry
        from integrated_app.routes.generate import router as generate_router

        monkeypatch.setattr(registry, "voxcpm_model", object(), raising=False)
        monkeypatch.setattr(registry, "_current_engine", "voxcpm2", raising=False)
        app = FastAPI()
        app.include_router(generate_router)
        return TestClient(app, raise_server_exceptions=False)

    def test_over_engine_cap_is_rejected_with_the_advertised_number(self, client):
        resp = client.post(
            "/api/generate/voxcpm_design",
            data={"text": "啊" * 8193, "instruction": "温柔"},
        )
        assert resp.status_code == 400
        assert "8192" in resp.text
        assert "剧本工坊" in resp.text  # 必须给出下一步怎么办，而不是只说不行

    def test_within_engine_cap_passes_length_check(self, client):
        resp = client.post(
            "/api/generate/voxcpm_design",
            data={"text": "啊" * 8192, "instruction": "温柔"},
        )
        assert "文本长度超过限制" not in resp.text


class TestValidationHtmlBranch:
    """`_validation_html_or_none` 的分流：htmx→HTML 片段，API→交给 JSON 分支。"""

    _ERRORS = [{"field": "body.cfg", "message": "Input should be a valid number", "type": "float_parsing"}]

    def test_htmx_request_renders_human_readable_message(self, monkeypatch):
        calls: dict[str, object] = {}
        sentinel = object()

        def spy(request, error_message, **kw):
            calls["msg"] = error_message
            calls["kw"] = kw
            return sentinel

        monkeypatch.setattr("integrated_app.routes.generate.utils._error_html", spy)
        resp = _validation_html_or_none(_request({"hx-request": "true"}), self._ERRORS)

        assert resp is sentinel
        assert calls["msg"].startswith("提交的内容有误") and "cfg：需要是数字" in calls["msg"]
        assert calls["kw"]["error_type"] == "validation"
        assert calls["kw"]["status_code"] == 422

    def test_api_client_falls_back_to_json(self):
        assert _validation_html_or_none(_request({"accept": "application/json"}), self._ERRORS) is None

    def test_render_failure_degrades_to_json_instead_of_500(self, monkeypatch):
        """错误响应本身绝不能因模板渲染失败而变成 500/空响应。"""

        def boom(*a, **kw):
            raise RuntimeError("模板不可用")

        monkeypatch.setattr("integrated_app.routes.generate.utils._error_html", boom)
        assert _validation_html_or_none(_request({"hx-request": "true"}), self._ERRORS) is None


class TestCancellationIsNotAnError:
    def test_still_a_generation_error_subclass(self):
        """保持既有 except GenerationError 语义与状态码不变。"""
        assert issubclass(GenerationCancelledError, GenerationError)
        assert GenerationCancelledError("生成已取消").status_code == 500

    def test_cancel_logs_info_not_error(self, caplog):
        from integrated_app.routes.generate.utils import _run_with_oom_retry

        def _boom():
            raise GenerationCancelledError("生成已取消")

        with (
            caplog.at_level(logging.DEBUG, logger="tts_multimodel"),
            pytest.raises(GenerationCancelledError),
        ):
            _run_with_oom_retry(_boom, "VoxCPM design")
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not errors, f"用户取消被记成 ERROR: {[r.getMessage() for r in errors]}"
        assert any("failed (non-OOM)" in r.getMessage() for r in caplog.records if r.levelno == logging.INFO)


class TestValidationHandlerIsWired:
    """422 分流必须挂在**真实 app** 上验证。

    只单测 `_validation_html_or_none` 会全绿却漏掉真正的缺陷：`RequestValidationError`
    从来没有被 `app_server` 注册过（只注册了 pydantic `ValidationError`，两者是兄弟类），
    所以表单类型错一直由 FastAPI 默认处理器兜底，把裸 JSON 吐进 htmx 结果区。
    """

    @staticmethod
    def _post(client, headers: dict[str, str]):
        return client.post(
            "/api/generate/voxcpm_design",
            data={"text": "友好性接线验证", "instruction": "温柔女声", "cfg": "abc"},
            headers=headers,
        )

    @staticmethod
    def _csrf(client) -> str:
        client.get("/")  # 首访下发 csrf_token cookie（双重提交模式）
        return client.cookies.get("csrf_token") or ""

    def test_htmx_form_gets_friendly_block(self, client):
        resp = self._post(client, {"HX-Request": "true", "X-CSRF-Token": self._csrf(client)})
        assert resp.status_code == 422, resp.text[:160]
        assert "tts-error-block" in resp.text
        assert "float_parsing" not in resp.text and "cfg" in resp.text

    def test_api_client_still_gets_machine_readable_json(self, client):
        resp = self._post(client, {"Accept": "application/json", "X-CSRF-Token": self._csrf(client)})
        assert resp.status_code == 422, resp.text[:160]
        assert "tts-error-block" not in resp.text
        assert resp.json()["detail"][0]["type"] == "float_parsing"


class TestEngineMismatchMessage:
    """⑤ 页面与引擎不匹配时，报错要说清「要哪个 / 现在加载的是哪个 / 下一步做什么」。

    真机场景：VoxCPM2 的语音克隆页开着，用户把引擎切到 IndexTTS 2.5，再点生成 →
    旧文案只说「VoxCPM2 模型未加载，请先加载模型」。这句话在这个场景里是**误导**：
    引擎确实加载着，只是不是这一页要的；用户照做反而会把当前引擎卸掉。
    """

    @staticmethod
    def _run(
        monkeypatch, *, need: str, voxcpm: object | None, indextts: object | None, current: str
    ) -> dict[str, object]:
        from integrated_app.model_registry import registry

        captured: dict[str, object] = {}

        def spy(request, error_message, **kw):
            captured["msg"] = error_message
            captured["kw"] = kw
            return "sentinel"

        monkeypatch.setattr("integrated_app.routes.generate.utils._error_html", spy)
        monkeypatch.setattr(registry, "voxcpm_model", voxcpm, raising=False)
        monkeypatch.setattr(registry, "indextts2_engine", indextts, raising=False)
        monkeypatch.setattr(registry, "current_engine", current, raising=False)
        result = _check_engine_ready(_request({"hx-request": "true"}), need)
        assert result == "sentinel", "未就绪时必须返回错误片段而不是 None"
        return captured

    def test_mismatch_names_both_engines_and_points_at_the_sidebar(self, monkeypatch):
        cap = self._run(monkeypatch, need="voxcpm2", voxcpm=None, indextts=object(), current="indextts2")
        msg = cap["msg"]
        assert "这一页需要 VoxCPM2" in msg and "当前加载的是 IndexTTS 2.5" in msg
        assert "侧栏" in msg, "要给出可操作的下一步，而不是只说未加载"
        assert "模型未加载，请先加载模型" not in msg, "引擎其实已加载，不能再叫用户去加载"
        # 渲染「立即加载」按钮依赖这两个 kw，文案改了不能破坏契约
        assert cap["kw"]["error_type"] == "engine_not_ready" and cap["kw"]["engine_id"] == "voxcpm2"

    def test_truly_nothing_loaded_keeps_the_load_hint(self, monkeypatch):
        cap = self._run(monkeypatch, need="voxcpm2", voxcpm=None, indextts=None, current="voxcpm2")
        assert cap["msg"] == "VoxCPM2 模型未加载，请先加载模型"

    def test_reverse_mismatch_indextts_page_with_voxcpm_loaded(self, monkeypatch):
        cap = self._run(monkeypatch, need="indextts2", voxcpm=object(), indextts=None, current="voxcpm2")
        assert "这一页需要 IndexTTS 2.5" in cap["msg"] and "当前加载的是 VoxCPM2" in cap["msg"]

    def test_ready_engines_return_none(self, monkeypatch):
        from integrated_app.model_registry import registry

        monkeypatch.setattr(registry, "voxcpm_model", object(), raising=False)
        monkeypatch.setattr(registry, "indextts2_engine", object(), raising=False)
        assert _check_engine_ready(_request({"hx-request": "true"}), "voxcpm2") is None
        assert _check_engine_ready(_request({"hx-request": "true"}), "indextts20") is None
