"""engine_ui_data 模块单元测试 — 引擎 UI 元数据注册与查询。

覆盖目标模块: app/integrated_app/engine_ui_data.py
"""

from integrated_app.engine_ui_data import (
    EngineFeature,
    EngineUIData,
    ParamDefinition,
    ParamGroup,
    ParamOption,
    ParamType,
    get_all_engine_uis,
    get_engine_ids,
    get_engine_ui,
    register_engine_ui,
)


def _make_engine(engine_id="test-engine") -> EngineUIData:
    params = [
        ParamDefinition(
            key="text",
            label_i18n="test.text",
            param_type=ParamType.TEXT,
            group=ParamGroup.BASIC,
            default="hello",
            required=True,
        ),
        ParamDefinition(
            key="speed",
            label_i18n="test.speed",
            param_type=ParamType.SLIDER,
            group=ParamGroup.ADVANCED,
            default=1.0,
            min=0.5,
            max=2.0,
            step=0.1,
        ),
        ParamDefinition(
            key="voice",
            label_i18n="test.voice",
            param_type=ParamType.SELECT,
            options=[ParamOption(value="a", label_i18n="a"), ParamOption(value="b", label_i18n="b")],
        ),
    ]
    return EngineUIData(
        engine_id=engine_id,
        name_i18n="test.name",
        description_i18n="test.desc",
        features=[EngineFeature.VOICE_CLONE, EngineFeature.SCRIPT_WORKSHOP],
        params=params,
    )


class TestParamDefinition:
    def test_defaults(self):
        p = ParamDefinition(key="k", label_i18n="l", param_type=ParamType.NUMBER)
        assert p.group == ParamGroup.BASIC
        assert p.visible is True
        assert p.required is False


class TestEngineUIData:
    def setup_method(self):
        self.engine = _make_engine()

    def test_get_param(self):
        assert self.engine.get_param("text").key == "text"
        assert self.engine.get_param("missing") is None

    def test_get_params_by_group(self):
        basic = self.engine.get_params_by_group(ParamGroup.BASIC)
        advanced = self.engine.get_params_by_group(ParamGroup.ADVANCED)
        assert any(p.key == "text" for p in basic)
        assert any(p.key == "speed" for p in advanced)
        assert all(p.visible for p in basic)

    def test_get_default_params(self):
        defaults = self.engine.get_default_params()
        assert defaults["text"] == "hello"
        assert defaults["speed"] == 1.0
        # select 参数 default=None，不应出现在默认值字典中
        assert "voice" not in defaults

    def test_has_feature(self):
        assert self.engine.has_feature(EngineFeature.VOICE_CLONE)
        assert not self.engine.has_feature(EngineFeature.LORA)


class TestRegistry:
    def test_register_and_query(self):
        register_engine_ui(_make_engine("reg-test"))
        ui = get_engine_ui("reg-test")
        assert ui is not None
        assert ui.engine_id == "reg-test"

    def test_get_engine_ui_missing(self):
        assert get_engine_ui("no-such-engine") is None

    def test_get_all_and_ids(self):
        register_engine_ui(_make_engine("reg-test-2"))
        all_uis = get_all_engine_uis()
        assert any(u.engine_id == "reg-test-2" for u in all_uis)
        ids = get_engine_ids()
        assert "reg-test-2" in ids
