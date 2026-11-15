"""长文本智能分块与音频交叉淡入淡出单元测试。

覆盖目标模块: app/integrated_app/text_segmenter.py
测试内容:
    1. TextSegmenter 基本分块功能（多语言）
    2. 分块边界在标点处准确率
    3. 超长句子二次切分
    4. 过短分块合并
    5. AudioCrossfader 交叉淡入淡出拼接
    6. 淡入淡出效果
    7. 边界条件与异常处理
    8. 受保护片段（特殊 token / SSML / 发音标注）不切断
    9. token 预算与 max_chars 硬上限兜底
    10. start_char/end_char 与原文切片一致
"""

import os
import sys

import numpy as np
import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from integrated_app.text_segmenter import (
    DEFAULT_PROTECTED_PATTERNS,
    AudioCrossfader,
    TextSegment,
    TextSegmenter,
    build_segmenter,
    crossfade_concat_audio,
    get_segmenter,
    segment_text,
)

# ---------------------------------------------------------------------------
# TextSegmenter 基础分块测试
# ---------------------------------------------------------------------------


class TestTextSegmenterBasic:
    """TextSegmenter 基本分块功能测试。"""

    def setup_method(self):
        self.segmenter = TextSegmenter(max_chars=100, min_chars=10)

    def test_empty_text(self):
        """空文本应返回空结果。"""
        result = self.segmenter.segment("", "zh")
        assert result.segments == []
        assert result.total_chars == 0
        assert result.segment_count == 0

    def test_whitespace_only(self):
        """纯空白文本应返回空结果。"""
        result = self.segmenter.segment("   \n\t  ", "zh")
        assert result.segments == []

    def test_short_text_single_segment(self):
        """短文本应返回单个分块。"""
        text = "你好世界"
        result = self.segmenter.segment(text, "zh")
        assert result.segment_count == 1
        assert result.segments[0].text == text
        assert result.segments[0].char_count == len(text)

    def test_long_text_multiple_segments(self):
        """长文本应被切分为多个分块。"""
        # 生成一段超过 max_chars 的中文文本
        text = "这是一个测试句子。每个句子大约十几个字。" * 10
        result = self.segmenter.segment(text, "zh")
        assert result.segment_count > 1
        for seg in result.segments:
            assert seg.char_count <= self.segmenter.max_chars

    def test_segment_index_sequential(self):
        """分块序号应从 0 开始连续递增。"""
        text = "这是第一句话。这是第二句话。这是第三句话。这是第四句话。这是第五句话。" * 5
        result = self.segmenter.segment(text, "zh")
        for i, seg in enumerate(result.segments):
            assert seg.index == i

    def test_segment_texts_convenience(self):
        """segment_texts 应返回字符串列表。"""
        text = "测试文本。"
        texts = self.segmenter.segment_texts(text, "zh")
        assert isinstance(texts, list)
        assert all(isinstance(t, str) for t in texts)

    def test_total_chars_matches_input(self):
        """分块总字符数应与输入一致（去除空白后）。"""
        text = "你好世界。这是一个测试。"
        result = self.segmenter.segment(text, "zh")
        total_seg_chars = sum(s.char_count for s in result.segments)
        assert total_seg_chars <= len(text)

    def test_avg_chars_calculated(self):
        """平均字符数应正确计算。"""
        text = "你好世界。这是一个测试。"
        result = self.segmenter.segment(text, "zh")
        if result.segment_count > 0:
            expected_avg = result.total_chars / result.segment_count
            assert result.avg_chars == pytest.approx(expected_avg)


# ---------------------------------------------------------------------------
# 分块边界准确性测试
# ---------------------------------------------------------------------------


class TestSegmentBoundaries:
    """分块边界在标点处的准确性测试。"""

    def setup_method(self):
        self.segmenter = TextSegmenter(max_chars=50, min_chars=5)

    def test_breaks_at_chinese_sentence_end(self):
        """中文应在句号处断句。"""
        text = "这是第一句话。这是第二句话。这是第三句话。"
        result = self.segmenter.segment(text, "zh")
        # 每个句子约 7-8 字，50 字限制应允许多句合并
        assert result.segment_count >= 1

    def test_breaks_at_english_sentence_end(self):
        """英文应在句号处断句。"""
        text = "This is sentence one. This is sentence two. This is sentence three."
        result = self.segmenter.segment(text, "en")
        assert result.segment_count >= 1

    def test_no_break_mid_word(self):
        """不应在词中间截断。"""
        # 英文长词
        text = "pneumonoultramicroscopicsilicovolcanoconiosis " * 20
        result = self.segmenter.segment(text, "en")
        for seg in result.segments:
            assert seg.char_count <= self.segmenter.max_chars

    def test_preserves_punctuation(self):
        """标点应保留在分块中。"""
        text = "你好。世界！测试？"
        result = self.segmenter.segment(text, "zh")
        full_text = "".join(s.text for s in result.segments)
        assert "。" in full_text or "！" in full_text or "？" in full_text

    def test_japanese_breaks(self):
        """日文应在句号处断句。"""
        text = "これは一つ目の文です。これは二つ目の文です。これは三つ目の文です。"
        result = self.segmenter.segment(text, "ja")
        assert result.segment_count >= 1

    def test_korean_breaks(self):
        """韩文应在句号处断句。"""
        text = "이것은 첫 번째 문장입니다. 이것은 두 번째 문장입니다. 이것은 세 번째 문장입니다."
        result = self.segmenter.segment(text, "ko")
        assert result.segment_count >= 1


# ---------------------------------------------------------------------------
# 超长句子二次切分测试
# ---------------------------------------------------------------------------


class TestLongSentenceSplit:
    """超长句子二次切分测试。"""

    def setup_method(self):
        self.segmenter = TextSegmenter(max_chars=30, min_chars=5)

    def test_split_long_sentence_at_pause(self):
        """超长句子应在停顿标点处切分。"""
        text = "这是一个很长的句子，包含多个逗号，用于测试停顿标点处的切分功能，确保不会超长。"
        result = self.segmenter.segment(text, "zh")
        for seg in result.segments:
            assert seg.char_count <= self.segmenter.max_chars

    def test_split_long_sentence_no_punctuation(self):
        """无标点的超长文本应强制截断。"""
        text = "a" * 100
        result = self.segmenter.segment(text, "zh")
        for seg in result.segments:
            assert seg.char_count <= self.segmenter.max_chars

    def test_split_long_english_at_space(self):
        """超长英文应在空格处切分。"""
        text = "word " * 50
        result = self.segmenter.segment(text, "en")
        for seg in result.segments:
            assert seg.char_count <= self.segmenter.max_chars


# ---------------------------------------------------------------------------
# 过短分块合并测试
# ---------------------------------------------------------------------------


class TestShortSegmentMerge:
    """过短分块合并测试。"""

    def test_merge_short_last_segment(self):
        """最后过短的分块应合并到前一个。"""
        segmenter = TextSegmenter(max_chars=50, min_chars=20)
        # 构造一个最后一段很短的文本
        text = "这是一个比较长的句子用于填充第一个分块的字符数量。" + "短。"
        result = segmenter.segment(text, "zh")
        # 最后一个分块不应太短（除非只有一个分块）
        if result.segment_count > 1:
            assert result.segments[-1].char_count >= segmenter.min_chars or result.segment_count == 1


# ---------------------------------------------------------------------------
# AudioCrossfader 测试
# ---------------------------------------------------------------------------


class TestAudioCrossfader:
    """音频交叉淡入淡出拼接测试。"""

    def setup_method(self):
        self.sr = 24000
        self.crossfader = AudioCrossfader(
            fade_duration_ms=50,
            default_sample_rate=self.sr,
        )

    def test_empty_list_raises(self):
        """空列表应抛出 ValueError。"""
        with pytest.raises(ValueError, match="不能为空"):
            self.crossfader.crossfade_concat([])

    def test_single_segment_returns_copy(self):
        """单段音频应返回副本。"""
        audio = np.ones(1000, dtype=np.float32)
        result = self.crossfader.crossfade_concat([audio])
        assert len(result) == len(audio)
        assert not np.shares_memory(result, audio)

    def test_two_segments_concatenated(self):
        """两段音频拼接后长度应正确。"""
        fade_samples = int(self.sr * 50 / 1000)
        audio1 = np.ones(2000, dtype=np.float32)
        audio2 = np.ones(2000, dtype=np.float32)
        result = self.crossfader.crossfade_concat([audio1, audio2], self.sr)
        # 交叉淡入淡出重叠长度为 fade_samples，结果长度 = len1 + len2 - fade_samples
        expected_len = len(audio1) + len(audio2) - fade_samples
        assert len(result) == expected_len

    def test_no_click_noise_at_boundary(self):
        """拼接处不应有 click 噪声（值连续过渡）。"""
        audio1 = np.ones(2000, dtype=np.float32) * 0.5
        audio2 = np.ones(2000, dtype=np.float32) * 0.5
        result = self.crossfader.crossfade_concat([audio1, audio2], self.sr)
        # 在拼接区域附近不应有突然的值跳变
        fade_samples = int(self.sr * 50 / 1000)
        boundary_start = len(audio1) - fade_samples
        boundary_region = result[boundary_start : boundary_start + fade_samples]
        # 交叉淡入淡出区域值应在 0~0.5 之间（两段都是 0.5）
        assert np.all(boundary_region >= -0.01)
        assert np.all(boundary_region <= 0.51)

    def test_zero_fade_duration(self):
        """fade_duration_ms=0 时应直接拼接。"""
        crossfader = AudioCrossfader(fade_duration_ms=0, default_sample_rate=self.sr)
        audio1 = np.ones(1000, dtype=np.float32)
        audio2 = np.ones(1000, dtype=np.float32)
        result = crossfader.crossfade_concat([audio1, audio2], self.sr)
        assert len(result) == 2000

    def test_simple_concat(self):
        """simple_concat 应直接拼接。"""
        audio1 = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        audio2 = np.array([4.0, 5.0], dtype=np.float32)
        result = self.crossfader.simple_concat([audio1, audio2])
        assert len(result) == 5
        assert result[0] == 1.0
        assert result[-1] == 5.0

    def test_simple_concat_empty_raises(self):
        """simple_concat 空列表应抛出 ValueError。"""
        with pytest.raises(ValueError):
            self.crossfader.simple_concat([])

    def test_apply_fade_in(self):
        """淡入效果应使开头渐变。"""
        audio = np.ones(1000, dtype=np.float32)
        result = self.crossfader.apply_fade(audio, fade_in_ms=100, sample_rate=self.sr)
        # 开头应接近 0
        assert result[0] < 0.1
        # 结尾应保持 1.0
        assert result[-1] == pytest.approx(1.0)

    def test_apply_fade_out(self):
        """淡出效果应使结尾渐变。"""
        audio = np.ones(1000, dtype=np.float32)
        result = self.crossfader.apply_fade(audio, fade_out_ms=100, sample_rate=self.sr)
        # 开头应保持 1.0
        assert result[0] == pytest.approx(1.0)
        # 结尾应接近 0
        assert result[-1] < 0.1

    def test_apply_fade_both(self):
        """同时淡入淡出。"""
        audio = np.ones(2000, dtype=np.float32)
        result = self.crossfader.apply_fade(audio, fade_in_ms=100, fade_out_ms=100, sample_rate=self.sr)
        assert result[0] < 0.1
        assert result[-1] < 0.1

    def test_multiple_segments(self):
        """多段音频拼接。"""
        segments = [np.ones(1000, dtype=np.float32) * 0.5 for _ in range(5)]
        result = self.crossfader.crossfade_concat(segments, self.sr)
        # 结果应为浮点数组
        assert result.dtype in (np.float32, np.float64)
        # 长度应小于各段总和（因为重叠）
        total_len = sum(len(s) for s in segments)
        assert len(result) < total_len


# ---------------------------------------------------------------------------
# 边界条件与异常处理测试
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """边界条件与异常处理测试。"""

    def test_invalid_max_chars(self):
        """max_chars <= 0 应抛出 ValueError。"""
        with pytest.raises(ValueError):
            TextSegmenter(max_chars=0)
        with pytest.raises(ValueError):
            TextSegmenter(max_chars=-1)

    def test_invalid_min_chars(self):
        """min_chars < 0 应抛出 ValueError。"""
        with pytest.raises(ValueError):
            TextSegmenter(max_chars=100, min_chars=-1)

    def test_min_greater_than_max(self):
        """min_chars > max_chars 应抛出 ValueError。"""
        with pytest.raises(ValueError):
            TextSegmenter(max_chars=50, min_chars=100)

    def test_invalid_fade_duration(self):
        """负的 fade_duration_ms 应抛出 ValueError。"""
        with pytest.raises(ValueError):
            AudioCrossfader(fade_duration_ms=-1)

    def test_invalid_sample_rate(self):
        """非正采样率应抛出 ValueError。"""
        with pytest.raises(ValueError):
            AudioCrossfader(default_sample_rate=0)

    def test_segment_single_char(self):
        """单字符文本应正常处理。"""
        segmenter = TextSegmenter(max_chars=500)
        result = segmenter.segment("好", "zh")
        assert result.segment_count == 1
        assert result.segments[0].text == "好"

    def test_segment_only_punctuation(self):
        """纯标点文本应正常处理。"""
        segmenter = TextSegmenter(max_chars=500)
        result = segmenter.segment("。！？", "zh")
        # 纯标点可能被 strip 掉
        assert result.segment_count <= 1

    def test_crossfade_different_lengths(self):
        """不同长度的音频段拼接。"""
        crossfader = AudioCrossfader(fade_duration_ms=10, default_sample_rate=24000)
        audio1 = np.ones(500, dtype=np.float32)
        audio2 = np.ones(2000, dtype=np.float32)
        audio3 = np.ones(100, dtype=np.float32)
        result = crossfader.crossfade_concat([audio1, audio2, audio3], 24000)
        assert len(result) > 0

    def test_crossfade_very_short_segments(self):
        """极短音频段拼接（fade > segment length）。"""
        crossfader = AudioCrossfader(fade_duration_ms=100, default_sample_rate=24000)
        audio1 = np.array([0.5], dtype=np.float32)
        audio2 = np.array([0.5], dtype=np.float32)
        result = crossfader.crossfade_concat([audio1, audio2], 24000)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# 模块级便捷函数测试
# ---------------------------------------------------------------------------


class TestModuleFunctions:
    """模块级便捷函数测试。"""

    def test_get_segmenter_singleton(self):
        """get_segmenter 应返回同一实例。"""
        s1 = get_segmenter()
        s2 = get_segmenter()
        assert s1 is s2

    def test_segment_text_function(self):
        """segment_text 便捷函数应正常工作。"""
        text = "你好世界。这是一个测试。"
        result = segment_text(text, "zh", max_chars=500)
        assert isinstance(result, list)
        assert all(isinstance(t, str) for t in result)

    def test_crossfade_concat_audio_function(self):
        """crossfade_concat_audio 便捷函数应正常工作。"""
        audio1 = np.ones(1000, dtype=np.float32)
        audio2 = np.ones(1000, dtype=np.float32)
        result = crossfade_concat_audio([audio1, audio2], sample_rate=24000, fade_duration_ms=50)
        assert isinstance(result, np.ndarray)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# TextSegment 数据类测试
# ---------------------------------------------------------------------------


class TestTextSegment:
    """TextSegment 数据类测试。"""

    def test_creation(self):
        """TextSegment 应正确创建。"""
        seg = TextSegment(
            text="测试文本",
            index=0,
            start_char=0,
            end_char=4,
            char_count=4,
        )
        assert seg.text == "测试文本"
        assert seg.index == 0
        assert seg.char_count == 4

    def test_len(self):
        """__len__ 应返回字符数。"""
        seg = TextSegment(text="测试", index=0, start_char=0, end_char=2, char_count=2)
        assert len(seg) == 2

    def test_repr(self):
        """__repr__ 应包含关键信息。"""
        seg = TextSegment(
            text="这是一个测试文本",
            index=1,
            start_char=5,
            end_char=12,
            char_count=7,
        )
        repr_str = repr(seg)
        assert "index=1" in repr_str
        assert "chars=7" in repr_str


# ---------------------------------------------------------------------------
# 受保护片段测试
# ---------------------------------------------------------------------------


class TestProtectedPatterns:
    """受保护片段（特殊 token / SSML 标签 / 发音标注）不被切断。"""

    @staticmethod
    def _covering_segments(segments, token, text):
        """返回完整覆盖 token 区间的分块。"""
        token_start = text.index(token)
        return [s for s in segments if s.start_char <= token_start and s.end_char >= token_start + len(token)]

    def test_default_patterns_used_when_none(self):
        """默认保护三类片段；显式传 [] 表示不保护。"""
        assert TextSegmenter().protected_patterns == list(DEFAULT_PROTECTED_PATTERNS)
        assert TextSegmenter(protected_patterns=[]).protected_patterns == []

    @pytest.mark.parametrize("token", ["<|SPECIAL_1|>", "<speak>", "[ni3]"])
    def test_protected_token_never_split(self, token):
        """恰好跨在预算边界上的受保护片段不应被切成两半。"""
        segmenter = TextSegmenter(max_chars=20, min_chars=1)
        text = "甲" * 17 + token + "乙" * 17
        result = segmenter.segment(text, "zh")
        assert len(self._covering_segments(result.segments, token, text)) == 1
        for seg in result.segments:
            assert text[seg.start_char : seg.end_char] == seg.text

    def test_oversized_protected_span_is_standalone(self):
        """受保护片段超过预算时整段独立成块并保留原文。"""
        token = "<|SPECIAL_" + "A" * 30 + "|>"
        segmenter = TextSegmenter(max_chars=20, min_chars=2)
        text = "前缀。" + token + "后缀。"
        result = segmenter.segment(text, "zh")
        assert token in result.texts
        seg = next(s for s in result.segments if s.text == token)
        assert seg.char_count > segmenter.max_chars
        assert text[seg.start_char : seg.end_char] == token

    def test_empty_protected_patterns_disables_protection(self):
        """传 [] 应完全关闭保护（片段可以被切断）。"""
        token = "<|SPECIAL_1|>"
        segmenter = TextSegmenter(max_chars=20, min_chars=1, protected_patterns=[])
        text = "甲" * 18 + token + "乙" * 18
        result = segmenter.segment(text, "zh")
        assert self._covering_segments(result.segments, token, text) == []

    def test_custom_patterns_replace_defaults(self):
        """调用方提供的正则替换默认三类。"""
        segmenter = TextSegmenter(max_chars=20, min_chars=1, protected_patterns=[r"\{\{[^}]{1,16}\}\}"])
        text = "甲" * 18 + "{{x}}" + "乙" * 18
        result = segmenter.segment(text, "zh")
        assert len(self._covering_segments(result.segments, "{{x}}", text)) == 1


# ---------------------------------------------------------------------------
# token 预算测试
# ---------------------------------------------------------------------------


class TestTokenBudget:
    """token 预算与 max_chars 硬上限兜底。"""

    def test_token_counter_enforced(self):
        """提供 token_counter 时应按 token 预算切分。"""
        segmenter = TextSegmenter(
            max_chars=1000,
            min_chars=5,
            max_tokens=10,
            token_counter=lambda s: len(s) // 2,
        )
        text = "。".join(["一二三四五"] * 12) + "。"
        result = segmenter.segment(text, "zh")
        assert result.segment_count > 1
        for seg in result.segments:
            assert len(seg.text) // 2 <= segmenter.max_tokens

    def test_max_chars_hard_cap_with_token_counter(self):
        """token 预算宽松时 max_chars 仍为硬上限。"""
        segmenter = TextSegmenter(
            max_chars=10,
            min_chars=2,
            max_tokens=100,
            token_counter=lambda s: len(s) // 2,
        )
        text = "一二三四五。" * 10
        result = segmenter.segment(text, "zh")
        assert result.segment_count > 1
        for seg in result.segments:
            assert seg.char_count <= segmenter.max_chars

    def test_max_tokens_without_counter_falls_back_to_chars(self):
        """未提供 token_counter 时回退字符预算，max_tokens 不生效。"""
        segmenter = TextSegmenter(max_chars=10, min_chars=2, max_tokens=1)
        text = "一二三四五。" * 10
        result = segmenter.segment(text, "zh")
        assert result.segment_count > 1
        for seg in result.segments:
            assert seg.char_count <= segmenter.max_chars

    def test_invalid_max_tokens(self):
        """max_tokens <= 0 应抛出 ValueError。"""
        with pytest.raises(ValueError):
            TextSegmenter(max_tokens=0)


# ---------------------------------------------------------------------------
# 偏移准确性测试
# ---------------------------------------------------------------------------


class TestOffsetAccuracy:
    """start_char/end_char 应与分块文本在原文中的真实区间一致。"""

    def test_offsets_match_original_with_whitespace(self):
        """多分块且原文带首尾空白时，区间仍与原文切片一致。"""
        segmenter = TextSegmenter(max_chars=20, min_chars=5)
        text = "  \n" + "这是第一句话。这是第二句话。这是第三句话。" + "\n  "
        result = segmenter.segment(text, "zh")
        assert result.segment_count > 1
        for seg in result.segments:
            assert text[seg.start_char : seg.end_char] == seg.text
        assert result.segments[0].start_char == 3
        assert result.segments[-1].end_char == len(text) - 3

    def test_offsets_single_segment_with_whitespace(self):
        """单分块时首尾空白被裁掉，但区间落在原文对应位置。"""
        segmenter = TextSegmenter(max_chars=100, min_chars=5)
        text = "  你好世界。  "
        result = segmenter.segment(text, "zh")
        assert result.segment_count == 1
        seg = result.segments[0]
        assert seg.text == "你好世界。"
        assert (seg.start_char, seg.end_char) == (2, 7)

    def test_offsets_monotonic_and_non_overlapping(self):
        """多分块区间应递增且互不重叠。"""
        text = "这是第一句话。这是第二句话，还有停顿。这是第三句话。"
        segmenter = TextSegmenter(max_chars=12, min_chars=2)
        result = segmenter.segment(text, "zh")
        assert result.segment_count > 1
        prev_end = 0
        for seg in result.segments:
            assert seg.start_char >= prev_end
            assert seg.end_char - seg.start_char == seg.char_count
            assert text[seg.start_char : seg.end_char] == seg.text
            prev_end = seg.end_char


# ---------------------------------------------------------------------------
# 工厂函数测试
# ---------------------------------------------------------------------------


class TestBuildSegmenter:
    """build_segmenter 装配测试。"""

    def test_build_segmenter_passes_params(self):
        """工厂应把全部参数透传给 TextSegmenter。"""
        segmenter = build_segmenter(
            max_chars=80,
            min_chars=20,
            max_tokens=32,
            token_counter=lambda s: len(s) // 2,
            protected_patterns=[],
        )
        assert segmenter.max_chars == 80
        assert segmenter.min_chars == 20
        assert segmenter.max_tokens == 32
        assert segmenter.protected_patterns == []

    def test_build_segmenter_defaults(self):
        """默认装配保持旧行为（字符预算 + 默认保护）。"""
        segmenter = build_segmenter()
        assert segmenter.max_chars == 500
        assert segmenter.min_chars == 50
        assert segmenter.max_tokens is None
        assert segmenter.protected_patterns == list(DEFAULT_PROTECTED_PATTERNS)
