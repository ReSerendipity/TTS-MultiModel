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

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger("tts_multimodel")

# ---------------------------------------------------------------------------
# 常量定义
# ---------------------------------------------------------------------------

#: 默认最大分块字符数
DEFAULT_MAX_CHARS = 500

#: 默认最小分块字符数（避免过短的分块）
DEFAULT_MIN_CHARS = 50

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
        return (
            f"TextSegment(index={self.index}, chars={self.char_count}, "
            f"text='{self.text[:30]}...')"
        )


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
    """

    def __init__(
        self,
        max_chars: int = DEFAULT_MAX_CHARS,
        min_chars: int = DEFAULT_MIN_CHARS,
    ) -> None:
        """初始化文本分块器。

        Args:
            max_chars: 每个分块的最大字符数。
            min_chars: 每个分块的最小字符数（最后一块除外）。
        """
        if max_chars <= 0:
            raise ValueError(f"max_chars 必须为正数，得到: {max_chars}")
        if min_chars < 0:
            raise ValueError(f"min_chars 不能为负数，得到: {min_chars}")
        if min_chars > max_chars:
            raise ValueError(f"min_chars({min_chars}) 不能大于 max_chars({max_chars})")

        self._max_chars = max_chars
        self._min_chars = min_chars

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

        text = text.strip()
        total_chars = len(text)

        # 如果文本不超过最大长度，直接返回单个分块
        if total_chars <= self._max_chars:
            seg = TextSegment(
                text=text,
                index=0,
                start_char=0,
                end_char=total_chars,
                char_count=total_chars,
            )
            return SegmentResult(
                segments=[seg],
                total_chars=total_chars,
            )

        # 按语言选择标点集
        sentence_endings, pause_marks = self._get_punctuation_sets(lang)

        # 第一步：按句末标点切分为句子
        sentences = self._split_by_sentence_endings(text, lang)

        # 第二步：将句子合并为不超过 max_chars 的分块
        segments = self._merge_into_segments(sentences, sentence_endings, pause_marks)

        # 更新分块的位置信息
        result_segments: list[TextSegment] = []
        current_pos = 0
        for i, seg_text in enumerate(segments):
            seg_text = seg_text.strip()
            if not seg_text:
                continue
            seg_len = len(seg_text)
            # 在原文中查找位置（简化处理：按顺序累积）
            start = current_pos
            end = start + seg_len
            current_pos = end

            result_segments.append(
                TextSegment(
                    text=seg_text,
                    index=len(result_segments),
                    start_char=start,
                    end_char=end,
                    char_count=seg_len,
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

    def _split_by_sentence_endings(
        self,
        text: str,
        lang: str,
    ) -> list[str]:
        """按句末标点切分为句子。

        保留标点在句子末尾。

        Args:
            text: 待切分文本。
            lang: 语言代码。

        Returns:
            句子列表。
        """
        if lang == "zh":
            pattern = self._re_zh_sentence
        elif lang == "en":
            pattern = self._re_en_sentence
        elif lang == "ja":
            pattern = self._re_ja_sentence
        elif lang == "ko":
            pattern = self._re_ko_sentence
        else:
            pattern = self._re_zh_sentence

        # 使用 split 保留分隔符
        result: list[str] = []
        last_end = 0
        for match in pattern.finditer(text):
            end = match.end()
            sentence = text[last_end:end]
            if sentence.strip():
                result.append(sentence)
            last_end = end

        # 处理剩余文本
        if last_end < len(text):
            remaining = text[last_end:]
            if remaining.strip():
                result.append(remaining)

        return result if result else [text]

    def _merge_into_segments(
        self,
        sentences: list[str],
        sentence_endings: set[str],
        pause_marks: set[str],
    ) -> list[str]:
        """将句子合并为不超过 max_chars 的分块。

        合并策略：
            1. 累积句子直到接近 max_chars
            2. 如果单个句子超过 max_chars，在停顿标点处二次切分
            3. 如果仍超长，强制按 max_chars 截断

        Args:
            sentences: 句子列表。
            sentence_endings: 句末标点集合。
            pause_marks: 停顿标点集合。

        Returns:
            分块文本列表。
        """
        segments: list[str] = []
        current_chunk: list[str] = []
        current_length = 0

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            sentence_len = len(sentence)

            # 单个句子就超过 max_chars：需要二次切分
            if sentence_len > self._max_chars:
                # 先保存当前累积的分块
                if current_chunk:
                    segments.append("".join(current_chunk))
                    current_chunk = []
                    current_length = 0

                # 二次切分超长句子
                sub_segments = self._split_long_sentence(
                    sentence, pause_marks
                )
                segments.extend(sub_segments)
                continue

            # 检查加入此句子是否会超限
            if current_length + sentence_len > self._max_chars and current_chunk:
                # 当前分块已满，保存并开始新分块
                segments.append("".join(current_chunk))
                current_chunk = [sentence]
                current_length = sentence_len
            else:
                # 加入当前分块
                current_chunk.append(sentence)
                current_length += sentence_len

        # 保存最后一个分块
        if current_chunk:
            segments.append("".join(current_chunk))

        # 合并过短的分块（< min_chars）到前一个分块
        if self._min_chars > 0 and len(segments) > 1:
            merged: list[str] = []
            for seg in segments:
                if merged and len(seg) < self._min_chars:
                    # 合并到前一个
                    merged[-1] = merged[-1] + seg
                else:
                    merged.append(seg)
            segments = merged

        return segments

    def _split_long_sentence(
        self,
        sentence: str,
        pause_marks: set[str],
    ) -> list[str]:
        """将超长句子在停顿标点处二次切分。

        Args:
            sentence: 超长句子。
            pause_marks: 停顿标点集合。

        Returns:
            子段列表。
        """
        if len(sentence) <= self._max_chars:
            return [sentence]

        result: list[str] = []
        current_start = 0
        last_pause = -1

        for i, ch in enumerate(sentence):
            # 记录最近的停顿标点位置
            if ch in pause_marks:
                last_pause = i + 1  # 包含标点

            # 达到最大长度
            if i - current_start + 1 >= self._max_chars:
                if last_pause > current_start:
                    # 在最近的停顿标点处断开
                    result.append(sentence[current_start:last_pause])
                    current_start = last_pause
                    last_pause = -1
                else:
                    # 没有停顿标点，尝试在空格处断开
                    space_pos = sentence.rfind(" ", current_start, i + 1)
                    if space_pos > current_start:
                        result.append(sentence[current_start:space_pos])
                        current_start = space_pos + 1
                    else:
                        # 强制截断
                        result.append(sentence[current_start : current_start + self._max_chars])
                        current_start = current_start + self._max_chars

        # 保存剩余部分
        if current_start < len(sentence):
            result.append(sentence[current_start:])

        return [s for s in result if s.strip()]


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
            raise ValueError(
                f"fade_duration_ms 不能为负数，得到: {fade_duration_ms}"
            )
        if default_sample_rate <= 0:
            raise ValueError(
                f"default_sample_rate 必须为正数，得到: {default_sample_rate}"
            )

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
                overlap_region = (
                    result[-actual_fade:] * fade_out + current[:actual_fade] * fade_in
                )

                # 拼接：前段（去尾部）+ 重叠区域 + 后段（去头部）
                result = np.concatenate([
                    result[:-actual_fade],
                    overlap_region,
                    current[actual_fade:],
                ])

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
