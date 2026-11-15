# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""长文本智能分块与音频交叉淡入淡出模块。

提供两大核心能力：
    1. TextSegmenter — 长文本智能分块
       按标点符号、段落、字数限制将长文本切分为适合 TTS 引擎逐段合成的片段。
       优先在标点处断句，避免在词中间截断。

    2. AudioCrossfader — 音频片段交叉淡入淡出拼接
       将多段音频通过交叉淡入淡出（cross-fade）技术无缝拼接，
       消除段间拼接处的 click 噪声，实现自然流畅的长文本语音输出。

架构设计：
    TextSegmenter → [文本块1, 文本块2, ...] → TTS引擎逐段合成 → [音频1, 音频2, ...]
                                                                     ↓
                                           AudioCrossfader.crossfade_concat → 完整音频

典型使用::

    from .text_segmenter import TextSegmenter, AudioCrossfader

    # 文本分块
    segmenter = TextSegmenter(max_chars=500)
    segments = segmenter.segment(long_text, lang="zh")

    # 逐段合成后拼接
    audio_segments = [engine.synthesize(seg) for seg in segments]
    crossfader = AudioCrossfader(fade_duration_ms=50)
    final_audio = crossfader.crossfade_concat(audio_segments, sample_rate=24000)
"""

from __future__ import annotations

import bisect
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger("tts_multimodel")

# ---------------------------------------------------------------------------
# 常量定义
# ---------------------------------------------------------------------------

#: 默认最大分块字符数
DEFAULT_MAX_CHARS = 500

#: 默认最小分块字符数（避免过短的分块）
DEFAULT_MIN_CHARS = 50

#: 默认可保护片段的正则：特殊 token、SSML/XML 标签、发音标注
DEFAULT_PROTECTED_PATTERNS: tuple[str, ...] = (
    r"<\|[^\n|]{1,64}\|>",
    r"<[^>\n]{1,64}>",
    r"\[[^\]\n]{1,32}\]",
)

#: 默认交叉淡入淡出时长（毫秒）
DEFAULT_FADE_DURATION_MS = 50

#: 默认采样率
DEFAULT_SAMPLE_RATE = 24000

#: 中文断句标点
_ZH_SENTENCE_ENDINGS = set("。！？；\n")

#: 中文分句标点（可在这些标点后断句但不强制）
_ZH_PAUSE_MARKS = set("，、：")

#: 英文断句标点
_EN_SENTENCE_ENDINGS = set(".!?;\n")

#: 英文分句标点
_EN_PAUSE_MARKS = set(",:")

#: 日文断句标点
_JA_SENTENCE_ENDINGS = set("。！？\n")

#: 韩文断句标点
_KO_SENTENCE_ENDINGS = set(".!?。\n")


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class TextSegment:
    """文本分块结果。

    Attributes:
        text: 分块文本内容。
        index: 分块序号（从 0 开始）。
        start_char: 在原文中的起始字符位置。
        end_char: 在原文中的结束字符位置。
        char_count: 分块字符数。
    """

    text: str
    index: int
    start_char: int
    end_char: int
    char_count: int

    def __len__(self) -> int:
        return self.char_count

    def __repr__(self) -> str:
        return f"TextSegment(index={self.index}, chars={self.char_count}, text='{self.text[:30]}...')"


@dataclass
class SegmentResult:
    """文本分块整体结果。

    Attributes:
        segments: 分块列表。
        total_chars: 原文总字符数。
        segment_count: 分块数量。
        avg_chars: 平均每块字符数。
    """

    segments: list[TextSegment] = field(default_factory=list)
    total_chars: int = 0
    segment_count: int = 0
    avg_chars: float = 0.0

    def __post_init__(self) -> None:
        self.segment_count = len(self.segments)
        if self.segment_count > 0:
            self.avg_chars = self.total_chars / self.segment_count

    @property
    def texts(self) -> list[str]:
        """仅返回分块文本列表。"""
        return [s.text for s in self.segments]


# ---------------------------------------------------------------------------
# TextSegmenter
# ---------------------------------------------------------------------------


class TextSegmenter:
    """长文本智能分块器。

    将长文本按语言特定的标点符号和字数限制切分为适合 TTS 合成的片段。
    优先在句末标点处断句，其次在逗号等停顿标点处断句，
    最后在空格处断句，避免在词中间截断。

    分块策略优先级：
        1. 句末标点（。！？.!?等）— 最佳断句点
        2. 停顿标点（，、,:等）— 次优断句点
        3. 空格/换行 — 英文等空格分隔语言的兜底
        4. 强制截断 — 超过 max_chars 时的最后手段

    预算与保护：
        - max_chars 始终是硬上限；提供 token_counter 时 max_tokens 同时生效（先到者为准）。
        - protected_patterns 命中的片段（特殊 token / SSML 标签 / 发音标注）不会被切断；
          若片段本身超预算，则整段独立成块并保留原文。
        - start_char/end_char 始终是分块文本在原文中的真实区间。
    """

    def __init__(
        self,
        max_chars: int = DEFAULT_MAX_CHARS,
        min_chars: int = DEFAULT_MIN_CHARS,
        max_tokens: int | None = None,
        token_counter: Callable[[str], int] | None = None,
        protected_patterns: list[str] | None = None,
    ) -> None:
        """初始化文本分块器。

        Args:
            max_chars: 每个分块的最大字符数（硬上限）。
            min_chars: 每个分块的最小字符数（最后一块除外）。
            max_tokens: 每个分块的最大 token 数，需配合 token_counter 生效。
            token_counter: 文本 → token 数的可注入计数器；None 时仅按字符预算。
            protected_patterns: 不可切断片段的正则列表；None 使用默认三类，
                传空列表表示不保护。
        """
        if max_chars <= 0:
            raise ValueError(f"max_chars 必须为正数，得到: {max_chars}")
        if min_chars < 0:
            raise ValueError(f"min_chars 不能为负数，得到: {min_chars}")
        if min_chars > max_chars:
            raise ValueError(f"min_chars({min_chars}) 不能大于 max_chars({max_chars})")
        if max_tokens is not None and max_tokens <= 0:
            raise ValueError(f"max_tokens 必须为正数，得到: {max_tokens}")

        self._max_chars = max_chars
        self._min_chars = min_chars
        self._max_tokens = max_tokens
        self._token_counter = token_counter
        patterns = DEFAULT_PROTECTED_PATTERNS if protected_patterns is None else protected_patterns
        self._protected_patterns: list[str] = list(patterns)
        self._re_protected = [re.compile(p) for p in self._protected_patterns]

        # 编译语言特定的标点正则
        self._re_zh_sentence = re.compile(r"[。！？；\n]+")
        self._re_en_sentence = re.compile(r"[.!?;\n]+")
        self._re_ja_sentence = re.compile(r"[。！？\n]+")
        self._re_ko_sentence = re.compile(r"[.!?。\n]+")

    @property
    def max_chars(self) -> int:
        """最大字符数。"""
        return self._max_chars

    @property
    def min_chars(self) -> int:
        """最小字符数。"""
        return self._min_chars

    @property
    def max_tokens(self) -> int | None:
        """最大 token 数（未配置时为 None）。"""
        return self._max_tokens

    @property
    def protected_patterns(self) -> list[str]:
        """当前生效的受保护片段正则列表。"""
        return list(self._protected_patterns)

    def segment(
        self,
        text: str,
        lang: str = "zh",
    ) -> SegmentResult:
        """将长文本分块。

        Args:
            text: 待分块的长文本。
            lang: 语言代码 (zh/en/ja/ko)，影响标点优先级。

        Returns:
            SegmentResult 包含所有分块信息。
        """
        if not text or not text.strip():
            return SegmentResult(segments=[], total_chars=0)

        total_chars = len(text)
        trimmed_start = len(text) - len(text.lstrip())
        trimmed_end = len(text.rstrip())

        # 如果文本不超过预算，直接返回单个分块
        if self._fits(text[trimmed_start:trimmed_end]):
            seg = TextSegment(
                text=text[trimmed_start:trimmed_end],
                index=0,
                start_char=trimmed_start,
                end_char=trimmed_end,
                char_count=trimmed_end - trimmed_start,
            )
            return SegmentResult(
                segments=[seg],
                total_chars=total_chars,
            )

        # 按语言选择停顿标点集
        _sentence_endings, pause_marks = self._get_punctuation_sets(lang)

        # 受保护片段作为不可分割的原子单元参与装配
        protected_spans = self._find_protected_spans(text)
        pieces = self._build_pieces(text, lang, protected_spans)
        chunks = self._merge_pieces(text, pieces, pause_marks)

        # 按真实区间生成分块（首尾空白在原文中定位后裁掉）
        result_segments: list[TextSegment] = []
        for chunk_start, chunk_end in chunks:
            while chunk_start < chunk_end and text[chunk_start].isspace():
                chunk_start += 1
            while chunk_end > chunk_start and text[chunk_end - 1].isspace():
                chunk_end -= 1
            if chunk_start >= chunk_end:
                continue
            seg_text = text[chunk_start:chunk_end]
            result_segments.append(
                TextSegment(
                    text=seg_text,
                    index=len(result_segments),
                    start_char=chunk_start,
                    end_char=chunk_end,
                    char_count=len(seg_text),
                )
            )

        result = SegmentResult(
            segments=result_segments,
            total_chars=total_chars,
        )

        logger.info(
            "文本分块完成: total=%d chars, segments=%d, avg=%.1f chars/seg",
            total_chars,
            result.segment_count,
            result.avg_chars,
        )
        return result

    def segment_texts(self, text: str, lang: str = "zh") -> list[str]:
        """便捷方法：仅返回分块文本列表。

        Args:
            text: 待分块的长文本。
            lang: 语言代码。

        Returns:
            分块文本字符串列表。
        """
        return self.segment(text, lang).texts

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    @staticmethod
    def _get_punctuation_sets(
        lang: str,
    ) -> tuple[set[str], set[str]]:
        """获取语言特定的标点集合。

        Args:
            lang: 语言代码。

        Returns:
            二元组：(句末标点集合, 停顿标点集合)。
        """
        if lang == "zh":
            return _ZH_SENTENCE_ENDINGS, _ZH_PAUSE_MARKS
        elif lang == "en":
            return _EN_SENTENCE_ENDINGS, _EN_PAUSE_MARKS
        elif lang == "ja":
            return _JA_SENTENCE_ENDINGS, set()
        elif lang == "ko":
            return _KO_SENTENCE_ENDINGS, set()
        else:
            return _ZH_SENTENCE_ENDINGS | _EN_SENTENCE_ENDINGS, _ZH_PAUSE_MARKS | _EN_PAUSE_MARKS

    def _get_sentence_pattern(self, lang: str) -> re.Pattern[str]:
        """获取语言特定的句末标点正则。"""
        patterns = {
            "zh": self._re_zh_sentence,
            "en": self._re_en_sentence,
            "ja": self._re_ja_sentence,
            "ko": self._re_ko_sentence,
        }
        return patterns.get(lang, self._re_zh_sentence)

    def _sentence_spans(
        self,
        text: str,
        start: int,
        end: int,
        lang: str,
    ) -> list[tuple[int, int]]:
        """按句末标点切分区间，返回 [start, end) 内的句子偏移区间。

        标点保留在句子末尾；纯空白句子被丢弃。

        Args:
            text: 原文。
            start: 区间起点（含）。
            end: 区间终点（不含）。
            lang: 语言代码。

        Returns:
            句子区间列表。
        """
        pattern = self._get_sentence_pattern(lang)
        spans: list[tuple[int, int]] = []
        last = start
        for match in pattern.finditer(text, start, end):
            if text[last : match.end()].strip():
                spans.append((last, match.end()))
            last = match.end()
        if text[last:end].strip():
            spans.append((last, end))
        return spans

    def _find_protected_spans(self, text: str) -> list[tuple[int, int]]:
        """定位受保护片段在原文中的区间（互不重叠，按位置排序）。"""
        found: list[tuple[int, int]] = []
        for pattern in self._re_protected:
            for match in pattern.finditer(text):
                span_start, span_end = match.span()
                if span_end <= span_start:
                    continue
                # 已按 start 排序且互不重叠，只需检查左右邻居
                idx = bisect.bisect_left(found, (span_start,))
                if idx > 0 and found[idx - 1][1] > span_start:
                    continue
                if idx < len(found) and found[idx][0] < span_end:
                    continue
                found.insert(idx, (span_start, span_end))
        return found

    def _build_pieces(
        self,
        text: str,
        lang: str,
        protected_spans: list[tuple[int, int]],
    ) -> list[tuple[int, int, bool]]:
        """将原文拆为按序拼接的原子片段。

        返回三元组 (start, end, protected)；受保护片段整体作为一个原子片段，
        其余空白区间按句末标点切成句子片段。
        """
        pieces: list[tuple[int, int, bool]] = []
        pos = 0
        for span_start, span_end in protected_spans:
            if span_start > pos:
                pieces.extend((s, e, False) for s, e in self._sentence_spans(text, pos, span_start, lang))
            pieces.append((span_start, span_end, True))
            pos = span_end
        if pos < len(text):
            pieces.extend((s, e, False) for s, e in self._sentence_spans(text, pos, len(text), lang))
        return pieces

    def _merge_pieces(
        self,
        text: str,
        pieces: list[tuple[int, int, bool]],
        pause_marks: set[str],
    ) -> list[tuple[int, int]]:
        """将原子片段合并为满足预算的分块区间。

        合并策略：
            1. 顺序累积片段，直到再加入一个片段会超出预算
            2. 单片段超预算时：受保护片段整段成块；普通片段在停顿标点/
               空格/预算边界处二次切分
            3. 过短分块（< min_chars）在预算允许时并入前一块
        """
        chunks: list[tuple[int, int]] = []
        cur_start: int | None = None
        cur_end = 0

        for piece_start, piece_end, protected in pieces:
            if not self._fits(text[piece_start:piece_end]):
                if cur_start is not None:
                    chunks.append((cur_start, cur_end))
                    cur_start, cur_end = None, 0
                if protected:
                    # 受保护片段本身超预算：整段独立成块，保留原文
                    chunks.append((piece_start, piece_end))
                else:
                    chunks.extend(self._split_free_span(text, piece_start, piece_end, pause_marks))
                continue

            if cur_start is None:
                cur_start, cur_end = piece_start, piece_end
            elif self._fits(text[cur_start:piece_end]):
                cur_end = piece_end
            else:
                chunks.append((cur_start, cur_end))
                cur_start, cur_end = piece_start, piece_end

        if cur_start is not None:
            chunks.append((cur_start, cur_end))

        if self._min_chars > 0 and len(chunks) > 1:
            merged: list[tuple[int, int]] = []
            for chunk_start, chunk_end in chunks:
                if merged and len(text[chunk_start:chunk_end].strip()) < self._min_chars:
                    prev_start = merged[-1][0]
                    if self._fits(text[prev_start:chunk_end]):
                        merged[-1] = (prev_start, chunk_end)
                        continue
                merged.append((chunk_start, chunk_end))
            chunks = merged

        return chunks

    def _split_free_span(
        self,
        text: str,
        start: int,
        end: int,
        pause_marks: set[str],
    ) -> list[tuple[int, int]]:
        """在预算约束下切分不受保护的超长片段。

        优先在停顿标点处断开，其次空格，最后强制按预算截断。

        Args:
            text: 原文。
            start: 片段起点（含）。
            end: 片段终点（不含）。
            pause_marks: 停顿标点集合。

        Returns:
            子片段区间列表。
        """
        spans: list[tuple[int, int]] = []
        cur = start
        while cur < end:
            if self._fits(text[cur:end]):
                spans.append((cur, end))
                break

            cut = self._max_fit_end(text, cur, end)
            if cut <= cur:
                # token 预算过小导致无一字符“可计费”时，退回字符硬上限
                cut = min(end, cur + self._max_chars)
            if cut >= end:
                spans.append((cur, end))
                break
            cut = self._prefer_break(text, cur, cut, pause_marks)
            spans.append((cur, cut))
            cur = cut

        return spans

    def _max_fit_end(self, text: str, start: int, end: int) -> int:
        """返回从 start 起在预算内可容纳的最大终点（假定 token 数随长度单调不减）。"""
        limit = min(end, start + self._max_chars)
        if self._token_counter is None or self._max_tokens is None:
            return limit

        best = start
        low, high = start + 1, limit
        while low <= high:
            mid = (low + high) // 2
            if self._token_counter(text[start:mid]) <= self._max_tokens:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        return best

    @staticmethod
    def _prefer_break(
        text: str,
        start: int,
        cut: int,
        pause_marks: set[str],
    ) -> int:
        """在 (start, cut] 内后向寻找更自然的断点（停顿标点 > 空格）。"""
        for i in range(cut, start, -1):
            if text[i - 1] in pause_marks:
                return i
        space_pos = text.rfind(" ", start, cut)
        if space_pos > start:
            return space_pos
        return cut

    def _fits(self, candidate: str) -> bool:
        """判断分块是否同时满足字符硬上限与 token 预算。"""
        if len(candidate) > self._max_chars:
            return False
        if self._token_counter is not None and self._max_tokens is not None:
            return self._token_counter(candidate) <= self._max_tokens
        return True


# ---------------------------------------------------------------------------
# AudioCrossfader
# ---------------------------------------------------------------------------


class AudioCrossfader:
    """音频交叉淡入淡出拼接器。

    将多段音频通过交叉淡入淡出技术无缝拼接，
    消除段间的 click 噪声。

    交叉淡入淡出原理：
        在两段音频的重叠区域，前一段的音量从 1 线性衰减到 0，
        后一段的音量从 0 线性增加到 1，两者相加实现平滑过渡。

    fade_duration_ms=50 表示重叠区域为 50ms，
    对于 24kHz 采样率，重叠区域为 1200 个采样点。
    """

    def __init__(
        self,
        fade_duration_ms: int = DEFAULT_FADE_DURATION_MS,
        default_sample_rate: int = DEFAULT_SAMPLE_RATE,
    ) -> None:
        """初始化音频交叉淡入淡出拼接器。

        Args:
            fade_duration_ms: 交叉淡入淡出时长（毫秒），0 表示直接拼接。
            default_sample_rate: 默认采样率（Hz）。
        """
        if fade_duration_ms < 0:
            raise ValueError(f"fade_duration_ms 不能为负数，得到: {fade_duration_ms}")
        if default_sample_rate <= 0:
            raise ValueError(f"default_sample_rate 必须为正数，得到: {default_sample_rate}")

        self._fade_duration_ms = fade_duration_ms
        self._default_sample_rate = default_sample_rate

    @property
    def fade_duration_ms(self) -> int:
        """交叉淡入淡出时长（毫秒）。"""
        return self._fade_duration_ms

    def crossfade_concat(
        self,
        audio_segments: list[np.ndarray],
        sample_rate: int | None = None,
    ) -> np.ndarray:
        """将多段音频通过交叉淡入淡出拼接为一段。

        Args:
            audio_segments: 音频段列表（每段为 1-D numpy 数列）。
            sample_rate: 采样率（Hz），None 时使用默认值。

        Returns:
            拼接后的音频 numpy 数列。

        Raises:
            ValueError: audio_segments 为空。
        """
        if not audio_segments:
            raise ValueError("audio_segments 不能为空")

        if len(audio_segments) == 1:
            return audio_segments[0].copy()

        sr = sample_rate or self._default_sample_rate
        fade_samples = int(sr * self._fade_duration_ms / 1000)

        # 如果 fade_samples 为 0，直接拼接
        if fade_samples == 0:
            return np.concatenate(audio_segments)

        result = audio_segments[0].copy().astype(np.float64)

        for i in range(1, len(audio_segments)):
            current = audio_segments[i].astype(np.float64)

            # 确保 fade_samples 不超过任一段长度
            actual_fade = min(fade_samples, len(result), len(current))

            if actual_fade <= 0:
                # 无法交叉淡入淡出，直接拼接
                result = np.concatenate([result, current])
            else:
                # 生成线性淡入淡出权重
                fade_out = np.linspace(1.0, 0.0, actual_fade, endpoint=False)
                fade_in = np.linspace(0.0, 1.0, actual_fade, endpoint=False)

                # 交叉淡入淡出：重叠区域 = 前段尾部 * fade_out + 后段头部 * fade_in
                overlap_region = result[-actual_fade:] * fade_out + current[:actual_fade] * fade_in

                # 拼接：前段（去尾部）+ 重叠区域 + 后段（去头部）
                result = np.concatenate(
                    [
                        result[:-actual_fade],
                        overlap_region,
                        current[actual_fade:],
                    ]
                )

        return result.astype(np.float32)

    def simple_concat(
        self,
        audio_segments: list[np.ndarray],
    ) -> np.ndarray:
        """简单拼接（无交叉淡入淡出）。

        直接将音频段首尾相连，不应用淡入淡出。
        适用于需要原始拼接的场景（如调试对比）。

        Args:
            audio_segments: 音频段列表。

        Returns:
            拼接后的音频 numpy 数列。

        Raises:
            ValueError: audio_segments 为空。
        """
        if not audio_segments:
            raise ValueError("audio_segments 不能为空")

        return np.concatenate(audio_segments)

    def apply_fade(
        self,
        audio: np.ndarray,
        fade_in_ms: int = 0,
        fade_out_ms: int = 0,
        sample_rate: int | None = None,
    ) -> np.ndarray:
        """对单段音频应用淡入和/或淡出效果。

        Args:
            audio: 输入音频 numpy 数列。
            fade_in_ms: 淡入时长（毫秒），0 表示不淡入。
            fade_out_ms: 淡出时长（毫秒），0 表示不淡出。
            sample_rate: 采样率，None 时使用默认值。

        Returns:
            应用了淡入淡出的音频 numpy 数列。
        """
        sr = sample_rate or self._default_sample_rate
        result = audio.copy().astype(np.float64)

        if fade_in_ms > 0:
            fade_in_samples = min(int(sr * fade_in_ms / 1000), len(result))
            if fade_in_samples > 0:
                fade_in = np.linspace(0.0, 1.0, fade_in_samples)
                result[:fade_in_samples] *= fade_in

        if fade_out_ms > 0:
            fade_out_samples = min(int(sr * fade_out_ms / 1000), len(result))
            if fade_out_samples > 0:
                fade_out = np.linspace(1.0, 0.0, fade_out_samples)
                result[-fade_out_samples:] *= fade_out

        return result.astype(np.float32)


# ---------------------------------------------------------------------------
# 模块级便捷函数
# ---------------------------------------------------------------------------

_segmenter_instance: TextSegmenter | None = None


def get_segmenter(
    max_chars: int = DEFAULT_MAX_CHARS,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> TextSegmenter:
    """获取模块级 TextSegmenter 单例。

    Args:
        max_chars: 最大字符数。
        min_chars: 最小字符数。

    Returns:
        TextSegmenter 实例。
    """
    global _segmenter_instance
    if _segmenter_instance is None:
        _segmenter_instance = TextSegmenter(max_chars=max_chars, min_chars=min_chars)
    return _segmenter_instance


def build_segmenter(
    max_chars: int = DEFAULT_MAX_CHARS,
    min_chars: int = DEFAULT_MIN_CHARS,
    max_tokens: int | None = None,
    token_counter: Callable[[str], int] | None = None,
    protected_patterns: list[str] | None = None,
) -> TextSegmenter:
    """按引擎能力装配 TextSegmenter。

    Args:
        max_chars: 最大字符数（硬上限）。
        min_chars: 最小字符数。
        max_tokens: 最大 token 数，需配合 token_counter 生效。
        token_counter: 文本 → token 数的计数器。
        protected_patterns: 受保护片段正则；None 使用默认值，[] 表示不保护。

    Returns:
        配置好的 TextSegmenter 实例。
    """
    return TextSegmenter(
        max_chars=max_chars,
        min_chars=min_chars,
        max_tokens=max_tokens,
        token_counter=token_counter,
        protected_patterns=protected_patterns,
    )


def segment_text(
    text: str,
    lang: str = "zh",
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[str]:
    """便捷函数：将长文本分块。

    Args:
        text: 待分块的长文本。
        lang: 语言代码。
        max_chars: 每块最大字符数。

    Returns:
        分块文本列表。
    """
    segmenter = TextSegmenter(max_chars=max_chars)
    return segmenter.segment_texts(text, lang)


def crossfade_concat_audio(
    audio_segments: list[np.ndarray],
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    fade_duration_ms: int = DEFAULT_FADE_DURATION_MS,
) -> np.ndarray:
    """便捷函数：交叉淡入淡出拼接音频。

    Args:
        audio_segments: 音频段列表。
        sample_rate: 采样率。
        fade_duration_ms: 交叉淡入淡出时长（毫秒）。

    Returns:
        拼接后的音频 numpy 数列。
    """
    crossfader = AudioCrossfader(
        fade_duration_ms=fade_duration_ms,
        default_sample_rate=sample_rate,
    )
    return crossfader.crossfade_concat(audio_segments, sample_rate)
