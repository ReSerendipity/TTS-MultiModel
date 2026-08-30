"""i18n 模块单元测试 — 翻译加载、键解析与语言检测。

覆盖目标模块: app/integrated_app/i18n.py
"""

import json
import re
from pathlib import Path

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


class TestLocaleKeyCoverage:
    """词表完整性守卫。

    WHY 需要这一类静态对账：``t()`` 的三层兜底会在键缺失时**静默**返回
    ``default=`` 传入的中文、甚至把键名原样吐到界面上，因此「翻译没生效」
    从来不报错，只表现为外语界面冒出中文或裸露英文键名。2026-08-30 实测
    抓到 14 个模板在用的键在**全部 5 个词表里都不存在**，其中
    ``indextts2.html`` 的 ``voice_clone`` 没写 default，IndexTTS 页的内层
    标签按钮直接显示成字面量 ``voice_clone``。这类问题靠人工核对必然漏，
    只能把「模板/代码里出现的每个键都在每个词表里有值」变成断言。
    """

    _APP = Path(__file__).resolve().parents[1] / "app" / "integrated_app"
    _LOCALES = ("zh", "zh-tw", "en", "ja", "ko")
    _KEY_RE = re.compile(r"""["']([a-z][a-z0-9_]{2,})["']\s*\|\s*t\(""")
    _TITLE_KEY_RE = re.compile(r"""title_key\s*=\s*["']([a-z][a-z0-9_]+)["']""")

    def _load(self, name: str) -> dict[str, str]:
        path = self._APP / "locales" / f"{name}.json"
        data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def test_all_locales_have_identical_key_sets(self):
        """5 个词表的键集合必须完全一致，不允许任何语种缺键或多键。"""
        tables = {name: self._load(name) for name in self._LOCALES}
        reference = set(tables["zh"])
        assert reference, "zh.json 键集合为空，词表可能已损坏"
        problems: list[str] = []
        for name, table in tables.items():
            keys = set(table)
            for missing in sorted(reference - keys):
                problems.append(f"{name}.json 缺键: {missing}")
            for extra in sorted(keys - reference):
                problems.append(f"{name}.json 多余键: {extra}")
        assert not problems, "词表键不对齐：\n" + "\n".join(problems)

    def test_no_empty_translation_values(self):
        """空串会被 t() 当成有效翻译直接返回，等于把文案抹掉，必须拦住。"""
        problems = [
            f"{name}.json -> {key!r} 值为空"
            for name in self._LOCALES
            for key, value in self._load(name).items()
            if not str(value).strip()
        ]
        assert not problems, "存在空翻译：\n" + "\n".join(problems)

    def test_every_template_key_exists_in_all_locales(self):
        """模板里 ``"key"|t(lang)`` 用到的每个键，必须在每个词表都有值。"""
        used: set[str] = set()
        templates = list((self._APP / "templates").rglob("*.html"))
        assert len(templates) > 10, f"只扫到 {len(templates)} 个模板，扫描逻辑可能已失效"
        for path in templates:
            used |= set(self._KEY_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))
        assert len(used) > 100, f"只提取到 {len(used)} 个模板键，正则可能已失配"
        missing = [
            f"{key}（缺失于 {name}.json）"
            for name in self._LOCALES
            for key in sorted(used)
            if key not in self._load(name)
        ]
        assert not missing, "模板使用了词表中不存在的键：\n" + "\n".join(sorted(set(missing)))

    def test_error_html_title_keys_are_translated(self):
        """``_error_html`` 的 title_key 取值必须在所有词表里有翻译。

        标题不走 ``default=`` 兜底，漏键会直接把键名渲染给用户。
        """
        declared: set[str] = {"gen_failed", "op_failed"}
        for path in (self._APP / "routes").rglob("*.py"):
            declared |= set(self._TITLE_KEY_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))
        missing = [
            f"{key}（缺失于 {name}.json）"
            for name in self._LOCALES
            for key in sorted(declared)
            if key not in self._load(name)
        ]
        assert not missing, "错误标题键缺少翻译：\n" + "\n".join(sorted(set(missing)))
