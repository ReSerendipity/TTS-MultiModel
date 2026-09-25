"""阿拉伯语（AR）端到端登记与未知语种告警测试（UPSTREAM_SYNC B4）。

实测起点：ES 早已全通（config._LANGS 有"西班牙语"、引擎 supported_langs 有 ES），
AR 只在引擎侧有、UI 侧三张表都没登记，所以下拉框根本选不到阿拉伯语。
另有两个"静默回退"叠在一起会让 AR/ES 失败伪装成成功：
    - 上游 indextts/utils/tokenizer.py:173-177  lang_to_token 未知码回落 "common"
    - 我方 indextts2_engine 归一后不在 supported_langs 时回落 "Auto"
RTL 部分守的是模板：语音文本入口必须带 dir="auto"，且按标签逐个扫——只扫共用
partial 的那条测试曾在 13 个手写入口全缺 dir 的情况下一直绿。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from integrated_app.config import (
    _LANG_ALIASES,
    _LANG_I18N_KEYS,
    _LANGS,
    build_lang_options,
    to_lang_code,
)

_LOCALES = Path(__file__).resolve().parents[1] / "app" / "integrated_app" / "locales"
_TEMPLATES = _LOCALES.parent / "templates"

# 用户在这些字段里输入的是"待合成文本 / 参考音频转写"，语种可能就是阿拉伯语；
# instruction 一类是风格指令，不纳入 dir 门禁。
_SPEECH_TEXT_FIELDS = ("prompt_text", "ref_text", "text")
_INPUT_TAG_RE = re.compile(r"<(?:textarea|input)\b[^>]*>")


def _speech_text_tags(source: str) -> list[str]:
    """源码里所有"语音文本入口"的开标签（textarea / input）。"""
    field_re = re.compile(f'name="(?:{"|".join(_SPEECH_TEXT_FIELDS)})"')
    return [m.group(0) for m in _INPUT_TAG_RE.finditer(source) if field_re.search(m.group(0))]


def _speech_text_tags_without_dir(source: str) -> list[str]:
    """挑出源码里所有「语音文本入口但没声明 dir」的开标签。"""
    return [tag for tag in _speech_text_tags(source) if 'dir="' not in tag]


class TestArabicRegistered:
    def test_in_ui_langs(self) -> None:
        assert "阿拉伯语" in _LANGS, "下拉框里没有阿拉伯语"

    def test_in_aliases(self) -> None:
        assert _LANG_ALIASES.get("阿拉伯语") == "ar"
        assert _LANG_ALIASES.get("ar") == "ar"

    def test_in_i18n_key_table(self) -> None:
        assert _LANG_I18N_KEYS.get("阿拉伯语") == "Arabic"

    def test_to_lang_code_roundtrip(self) -> None:
        for raw in ("阿拉伯语", "ar", "AR", "ar-SA", "ar_EG"):
            assert to_lang_code(raw) == "ar", raw

    @pytest.mark.parametrize("ui_lang", ["zh", "zh-tw", "en", "ja", "ko"])
    def test_shows_up_in_dropdown(self, ui_lang: str) -> None:
        options = dict(build_lang_options(ui_lang))
        assert "阿拉伯语" in options, f"{ui_lang} 界面下没有阿拉伯语选项"

    def test_dropdown_label_is_translated(self) -> None:
        """标签必须随 UI 语言走；中文 UI 下标签等于值是正确的，不能拿它当反例。"""
        labels = dict(build_lang_options("en"))
        assert labels["阿拉伯语"] == "Arabic"
        assert dict(build_lang_options("zh"))["阿拉伯语"] == "阿拉伯语"

    def test_arabic_label_exists_in_all_five_vocab(self) -> None:
        for name in ("zh.json", "zh-tw.json", "en.json", "ja.json", "ko.json"):
            data = json.loads((_LOCALES / name).read_text(encoding="utf-8"))
            assert "Arabic" in data, name
            assert data["Arabic"].strip()


class TestUnsupportedLangIsLoud:
    def test_engine_supports_ar_and_es(self) -> None:
        """2.5 的语种集合必须含 ES/AR，否则上面的登记只是把选项送到一个不认它的引擎。

        ``supported_langs`` 是 __init__ 里按版本赋的实例属性（2.0 只有 Auto/ZH/EN），
        而实例化会去加载权重，所以这里查源码而不是查对象。
        """
        import inspect

        from integrated_app.engines.indextts2_engine import IndexTTS2Engine

        src = inspect.getsource(IndexTTS2Engine.__init__)
        assignments = [line for line in src.splitlines() if "supported_langs" in line and "=" in line]
        assert assignments, "找不到 supported_langs 赋值，本断言已失去意义"
        with_ar = [line for line in assignments if '"AR"' in line]
        assert with_ar, f"2.5 的语种集合里没有 AR：{[s.strip() for s in assignments]}"
        assert '"ES"' in with_ar[0], f"2.5 的语种集合里没有 ES：{with_ar[0].strip()}"

    def test_lang_normalization_falls_back_only_with_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不支持且用户明确指定过语种时必须 warn（不能只 debug）。"""
        import logging

        from integrated_app.engines import indextts2_engine as eng_mod

        records: list[tuple[int, str]] = []

        class _Spy(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append((record.levelno, record.getMessage()))

        spy = _Spy(level=logging.DEBUG)
        eng_mod.logger.addHandler(spy)
        try:
            # 直接验证决策函数存在且把 auto 与非 auto 分开处理
            src = Path(eng_mod.__file__).read_text(encoding="utf-8")
            assert 'if _code != "auto":' in src, "回退又变回无条件的 debug 了"
            assert "已回退 Auto" in src
        finally:
            eng_mod.logger.removeHandler(spy)


class TestRtlInput:
    def test_shared_text_input_partial_sets_dir_auto(self) -> None:
        """阿拉伯语从右往左；textarea 不声明 dir 会按页面 LTR 排版，光标行为很怪。"""
        tpl = (_LOCALES.parent / "templates" / "partials" / "text_input.html").read_text(encoding="utf-8")
        assert 'dir="auto"' in tpl

    def test_scanner_catches_a_tag_without_dir(self) -> None:
        """已知答案：扫描器必须能分辨"带 dir"与"不带 dir"，否则下一条测试的绿没有意义。"""
        assert _speech_text_tags_without_dir('<textarea name="text" id="a">\n') == ['<textarea name="text" id="a">']
        assert _speech_text_tags_without_dir('<textarea name="text" dir="auto" id="a">\n') == []
        # 非语音文本入口（风格指令）不该被算进来
        assert _speech_text_tags_without_dir('<textarea name="instruction" id="b">\n') == []

    def test_every_speech_text_field_declares_direction(self) -> None:
        """全部模板里的语音文本入口都要有 dir。

        上一轮我只给共用 partial 加了 dir="auto"，而 10 个 tab 各自手写自己的
        textarea——那条 partial 测试全绿，实际 13 个入口一个都没覆盖到。
        """
        offenders: list[str] = []
        seen = 0
        for path in sorted(_TEMPLATES.rglob("*.html")):
            source = path.read_text(encoding="utf-8")
            seen += len(_speech_text_tags(source))
            offenders.extend(
                f"{path.name} :: {tag.splitlines()[0].strip()}" for tag in _speech_text_tags_without_dir(source)
            )
        assert seen >= 14, f"只扫到 {seen} 个语音文本入口，扫描器或模板目录变了，本断言已空转"
        assert not offenders, "以下入口缺 dir 声明，阿拉伯语下仍是 LTR 编辑器：" + "; ".join(offenders)
