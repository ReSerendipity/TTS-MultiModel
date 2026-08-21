"""i18n 模块单元测试 — 翻译加载、键解析与语言检测。

覆盖目标模块: app/integrated_app/i18n.py
"""

from integrated_app.i18n import (
    _load_translations,
    _resolve_key,
    get_i18n_json,
    get_lang,
    register_i18n_filters,
    t,
)


class TestLoadTranslations:
    def test_load_zh(self):
        translations = _load_translations("zh")
        assert translations is not None
        assert "app_title" in translations

    def test_load_unknown_lang_returns_none(self):
        assert _load_translations("xx") is None


class TestResolveKey:
    def test_direct_key(self):
        translations = _load_translations("zh")
        assert _resolve_key(translations, "app_title") is not None

    def test_missing_key(self):
        translations = _load_translations("zh")
        assert _resolve_key(translations, "no.such.key.xyz") is None

    def test_nested_key(self):
        # 支持点分路径（若 JSON 为嵌套结构）
        nested = {"a": {"b": "value"}}
        assert _resolve_key(nested, "a.b") == "value"


class TestTFunction:
    def test_known_key(self):
        assert t("close", lang="zh")

    def test_missing_key_returns_key(self):
        assert t("no.such.key", lang="zh") == "no.such.key"

    def test_missing_key_with_default(self):
        assert t("no.such.key", lang="zh", default="fallback") == "fallback"

    def test_en_lang(self):
        assert t("close", lang="en")


class TestGetLang:
    def test_valid_lang(self):
        request = type("R", (), {"query_params": {"lang": "en"}, "cookies": {}, "headers": {}})()
        assert get_lang(request) == "en"

    def test_zh_mapping(self):
        request = type("R", (), {"query_params": {"lang": "zh"}, "cookies": {}, "headers": {}})()
        assert get_lang(request) in ("zh", "zh-CN")

    def test_no_lang_defaults(self):
        request = type("R", (), {"query_params": {}, "cookies": {}, "headers": {}})()
        assert get_lang(request) is not None

    def test_invalid_lang_falls_back(self):
        request = type("R", (), {"query_params": {"lang": "xx"}, "cookies": {}, "headers": {}})()
        assert get_lang(request) is not None


class TestI18nJson:
    def test_get_i18n_json_zh(self):
        data = get_i18n_json("zh")
        assert isinstance(data, dict)
        assert "app_title" in data

    def test_get_i18n_json_unknown(self):
        data = get_i18n_json("xx")
        assert isinstance(data, dict)  # 回退到默认语言或空

    def test_get_i18n_json_zh_tw(self):
        data = get_i18n_json("zh-TW")
        assert isinstance(data, dict)
        assert "app_title" in data


class TestRegisterFilters:
    def test_register_i18n_filters(self):
        class FakeEnv:
            def __init__(self):
                self.filters = {}

            def add_template_filter(self, func, name):
                self.filters[name] = func

        env = FakeEnv()
        register_i18n_filters(env)
        assert "t" in env.filters
