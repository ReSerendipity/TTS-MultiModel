"""发音控制标记与风格前缀的存活测试（UPSTREAM_SYNC B2 + A4）。

为什么需要这一整组：本项目把 IndexTTS 2.5 的发音控制当成"已支持"，
但实测 ``normalize_text`` 会把标注**整段删掉**——

    normalize_text('<重庆|chong2 qing4>欢迎你', 'zh')  ->  '欢迎你'

凶手是 clean_markdown_emoji 的 ``_re_md_html_tag = <[^>]+>``（当成 HTML 标签），
第二凶手是 _normalize_zh 的数字口语化（``chong2`` -> ``chong二``），
第三凶手是 normalize_punctuation 在 zh 下的半角转全角（吃掉 VoxCPM 的
``(风格描述)`` 前缀）。三处全部静默，不报错、不降级，只是音色/读音悄悄不对。
"""

from __future__ import annotations

import re

import pytest

from integrated_app import text_frontend
from integrated_app.text_frontend import (
    PINYIN_TONE_PATTERN,
    PRONUNCIATION_ANNOTATION_PATTERN,
    TextNormalizer,
    check_text_safety,
    normalize_text,
)


class TestMarkerPatternsTrackUpstream:
    """我方保护的范围必须与上游消费的范围一致，否则保护了个寂寞。"""

    def test_pronunciation_annotation_pattern_matches_upstream(self) -> None:
        mod = pytest.importorskip("indextts.infer_v2_5")
        assert mod.PRONUNCIATION_ANNOTATION_PATTERN.pattern == PRONUNCIATION_ANNOTATION_PATTERN

    def test_pinyin_tone_pattern_matches_upstream(self) -> None:
        front = pytest.importorskip("indextts.utils.front")
        assert PINYIN_TONE_PATTERN == front.TextNormalizer.PINYIN_TONE_PATTERN

    def test_annotation_regex_shape(self) -> None:
        compiled = re.compile(PRONUNCIATION_ANNOTATION_PATTERN)
        assert compiled.search("前<重庆|chong2 qing4>后").group(1) == "重庆"
        assert compiled.search("前<重庆|chong2 qing4>后").group(2) == "chong2 qing4"


class TestPronunciationMarkersSurvive:
    """三类标记经过完整规范化后必须原样存在。"""

    @pytest.mark.parametrize(
        ("lang", "text", "marker"),
        [
            ("zh", "<重庆|chong2 qing4>欢迎你", "<重庆|chong2 qing4>"),
            ("en", "Please <read|R EH1 D> this", "<read|R EH1 D>"),
            ("en", "<hello|HH AH0 L OW1> world", "<hello|HH AH0 L OW1>"),
            ("ja", "<漢|かん>テスト", "<漢|かん>"),
            ("zh", "请你 chong2 qing4 一下", "chong2 qing4"),
        ],
    )
    def test_marker_untouched(self, lang: str, text: str, marker: str) -> None:
        out = normalize_text(text, lang)
        assert marker in out, f"[{lang}] 标记被改写：{text!r} -> {out!r}"

    def test_style_prefix_keeps_ascii_parentheses(self) -> None:
        """VoxCPM 的 Voice Design 语法依赖半角圆角，全角化会让模型读不到风格提示。"""
        text = "(A young woman, gentle and sweet voice)欢迎来到北京"
        out = normalize_text(text, "中文")  # 用 UI 的中文显示名，顺带覆盖 to_lang_code 归一
        assert out.startswith("(A young woman, gentle and sweet voice)"), out

    def test_normalization_still_runs_around_markers(self) -> None:
        """保护不能顺手把正常规范化也关掉，否则数字/日期读法全线回归。"""
        out = normalize_text("<重庆|chong2 qing4>在 2024年3月5日 有 30% 折扣", "zh")
        assert "<重庆|chong2 qing4>" in out
        assert "二零二四年三月五日" in out, f"日期规范化被误伤：{out!r}"
        assert "百分之三十" in out, f"百分比规范化被误伤：{out!r}"

    def test_plain_text_path_unchanged(self) -> None:
        out = normalize_text("2024年3月5日 会议", "zh")
        assert out == "二零二四年三月五日 会议"

    def test_mixed_zh_en_ja_does_not_crash(self) -> None:
        text = "他说<hello|HH AH0 L OW1>，然后<漢|かん>地说了 chong2 qing4，共 2 次"
        out = normalize_text(text, "zh")
        for marker in ("<hello|HH AH0 L OW1>", "<漢|かん>", "chong2 qing4"):
            assert marker in out, out


class TestShieldInternals:
    def test_roundtrip_is_lossless(self) -> None:
        text = "a<重庆|chong2 qing4>b<read|R EH1 D>c chong1 d"
        shielded, guards = text_frontend._shield_control_markers(text)
        assert "<" not in shielded and "|" not in shielded
        assert text_frontend._unshield_control_markers(shielded, guards) == text

    def test_no_markers_is_noop(self) -> None:
        assert text_frontend._shield_control_markers("纯文本。") == ("纯文本。", {})

    def test_pua_collision_falls_back_to_unprotected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """文本里已占满私用区时，宁可退回旧行为也不能抛异常打断合成。"""
        filler = "".join(chr(cp) for cp in range(text_frontend._PUA_FIRST, text_frontend._PUA_FIRST + 3))
        text = f"{filler}<重庆|chong2 qing4>"
        monkeypatch.setattr(text_frontend, "_PUA_FIRST", 0xE000)
        monkeypatch.setattr(text_frontend, "_PUA_LAST", 0xE002)  # 只有 3 个槽位，已被 filler 占满
        shielded, guards = text_frontend._shield_control_markers(text)
        assert guards == {}
        assert shielded == text

    def test_normalize_entrypoint_delegates(self) -> None:
        assert TextNormalizer().normalize("", "zh") == ""


class TestSafetyGateSurvivesBypass:
    """text_normalization=False 跳过改写，但绝不能跳过内容安全。"""

    @staticmethod
    def _block_everything(_text: str):
        from integrated_app.security.content_safety import SafetyCategory, SafetyDetectionResult

        return SafetyDetectionResult(
            is_safe=False,
            category=SafetyCategory.VIOLENCE,
            confidence=0.9,
            matched_patterns=["test-hook"],
        )

    def _assert_blocked(self, fn, monkeypatch: pytest.MonkeyPatch) -> None:
        from integrated_app.exceptions import ContentSafetyError

        monkeypatch.setattr(text_frontend, "check_safety", self._block_everything)
        with pytest.raises(ContentSafetyError):
            fn("一些会被拦的文本")

    def test_normalize_text_still_blocks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._assert_blocked(lambda t: normalize_text(t, "zh"), monkeypatch)

    def test_check_text_safety_blocks_without_rewriting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._assert_blocked(check_text_safety, monkeypatch)

    def test_check_text_safety_passes_clean_text(self) -> None:
        assert check_text_safety("正常文本") is None


class TestSwitchIsWiredThrough:
    """HTTP -> 引擎的开关链路存在性（不加载模型也能验证的参数面）。"""

    def test_engine_impl_accepts_the_switch(self) -> None:
        import inspect

        from integrated_app.engines.indextts2_engine import IndexTTS2Engine

        params = inspect.signature(IndexTTS2Engine._infer_impl).parameters
        assert "text_normalization" in params
        assert params["text_normalization"].default is True

    def test_route_exposes_the_form_field(self) -> None:
        import inspect

        from integrated_app.routes.generate.indextts2.synthesize import generate_indextts2

        params = inspect.signature(generate_indextts2).parameters
        assert "text_normalization" in params, "HTTP 面没有这个字段，UI 勾了也没用"

    def test_voxcpm2_engine_shares_the_same_frontend_guard(self) -> None:
        """A4：VoxCPM2 走同一个 normalize_text，所以括号风格前缀的保护对它同样生效。"""
        from integrated_app.engines.voxcpm2 import _base

        assert _base.normalize_text is text_frontend.normalize_text, (
            "voxcpm2 不再经过文本前端，本文件的括号保护对它已失效，需重新评估"
        )
