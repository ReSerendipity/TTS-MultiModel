"""Tests for generation.py text splitting logic."""

from integrated_app.generation import (
    _find_best_split_point,
    _is_abbreviation,
    _is_decimal_point,
    _is_inside_quotes,
    split_text_for_tts,
)


class TestSplitTextForTTS:
    def test_short_text_no_split(self):
        assert split_text_for_tts("你好世界") == ["你好世界"]

    def test_split_at_chinese_period(self):
        text = "第一句话。第二句话。第三句话。"
        result = split_text_for_tts(text, max_chars=10)
        assert len(result) > 1
        for segment in result:
            assert len(segment) <= 10

    def test_split_at_comma(self):
        text = "很长的一段话，需要在这里分割，然后继续。"
        result = split_text_for_tts(text, max_chars=12)
        assert len(result) > 1

    def test_empty_text(self):
        assert split_text_for_tts("") == [""]


class TestDecimalPoint:
    def test_decimal_point(self):
        assert _is_decimal_point("3.14", 1) is True

    def test_not_decimal(self):
        assert _is_decimal_point("a.b", 1) is False

    def test_sentence_period(self):
        assert _is_decimal_point("Hello. World", 6) is False


class TestAbbreviation:
    def test_us_abbreviation(self):
        assert _is_abbreviation("U.S.A.", 1) is True

    def test_dr_abbreviation(self):
        assert _is_abbreviation("Dr. Smith", 2) is True

    def test_not_abbreviation(self):
        assert _is_abbreviation("Hello. World", 6) is False


class TestInsideQuotes:
    def test_inside_chinese_quotes(self):
        text = "\u201c你好世界\u201d然后走了"
        # 位置 2 在引号内
        assert _is_inside_quotes(text, 2) is True

    def test_outside_quotes(self):
        text = "\u201c你好\u201d然后走了"
        # 位置 0 在引号外
        assert _is_inside_quotes(text, 0) is False


class TestFindBestSplitPoint:
    def test_find_chinese_period(self):
        text = "这是一段很长的文本。需要找到分割点。"
        idx = _find_best_split_point(text)
        assert idx > 0
        assert text[idx] in "。！？"

    def test_no_split_point(self):
        text = "没有标点符号的纯文本"
        idx = _find_best_split_point(text)
        assert idx == 0
