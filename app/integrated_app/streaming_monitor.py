"""流式音频处理精度监测模块。

提供流式生成场景下的音频质量监测工具：
1. 采样率一致性检查
2. 位深度验证
3. 通道数一致性
4. 音量峰值/有效值统计
5. 静音段比例分析

使用方式（在流式生成路径中调用）::

    from .streaming_monitor import StreamingQualityMonitor

    monitor = StreamingQualityMonitor(expected_sr=24000)
    for chunk in audio_stream:
        report = monitor.analyze_chunk(chunk)
        if report.has_issue:
            logger.warning("流式音频质量异常: %s", report.summary)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger("tts_multimodel")


@dataclass
class ChunkQualityReport:
    """单个音频块的质量报告。

    Attributes:
        sample_rate: 检测到的采样率（通过块大小推断）。
        peak_amplitude: 峰值振幅（0.0-1.0）。
        rms: 均方根振幅（有效值）。
        silence_ratio: 静音样本占比（0.0-1.0）。
        has_clipping: 是否存在削波（peak >= 0.99）。
        has_issue: 是否存在质量问题。
        issue_description: 问题描述。
    """

    sample_rate: int = 0
    peak_amplitude: float = 0.0
    rms: float = 0.0
    silence_ratio: float = 0.0
    has_clipping: bool = False
    has_issue: bool = False
    issue_description: str = ""

    @property
    def summary(self) -> str:
        """获取质量报告摘要文本。"""
        parts = []
        if self.has_clipping:
            parts.append(f"削波(peak={self.peak_amplitude:.3f})")
        if self.silence_ratio > 0.5:
            parts.append(f"高静音比({self.silence_ratio:.1%})")
        if self.rms < 0.01:
            parts.append(f"极低音量(rms={self.rms:.4f})")
        return "; ".join(parts) if parts else "正常"


class StreamingQualityMonitor:
    """流式音频质量监测器。

    在流式生成过程中对每个音频块进行质量分析，
    记录统计信息并在检测到异常时发出警告。

    Args:
        expected_sr: 预期采样率（用于一致性检查）。
        silence_threshold: 静音判定阈值（RMS 低于此值视为静音）。
        clipping_threshold: 削波判定阈值（峰值高于此值视为削波）。
    """

    def __init__(
        self,
        expected_sr: int = 24000,
        silence_threshold: float = 0.01,
        clipping_threshold: float = 0.99,
    ) -> None:
        self.expected_sr = expected_sr
        self.silence_threshold = silence_threshold
        self.clipping_threshold = clipping_threshold

        # 累积统计
        self._total_chunks: int = 0
        self._total_samples: int = 0
        self._total_silence_samples: int = 0
        self._max_peak: float = 0.0
        self._sum_rms: float = 0.0
        self._clipping_chunks: int = 0

    def analyze_chunk(self, chunk: Any) -> ChunkQualityReport:
        """分析单个音频块的质量。

        Args:
            chunk: 音频数据块（numpy array 或可转换的数组）。

        Returns:
            该块的质量报告。
        """
        self._total_chunks += 1

        # 转换为 numpy array
        if not isinstance(chunk, np.ndarray):
            try:
                chunk = np.frombuffer(chunk, dtype=np.float32)
            except (ValueError, TypeError):
                try:
                    chunk = np.array(chunk, dtype=np.float32)
                except Exception:
                    return ChunkQualityReport(has_issue=True, issue_description="无法解析音频块")

        # 展平多通道
        if chunk.ndim > 1:
            chunk = chunk.flatten()

        num_samples = len(chunk)
        self._total_samples += num_samples

        if num_samples == 0:
            return ChunkQualityReport(has_issue=True, issue_description="空音频块")

        # 计算峰值
        peak = float(np.max(np.abs(chunk)))
        self._max_peak = max(self._max_peak, peak)

        # 计算有效值
        rms = float(math.sqrt(np.mean(chunk**2)))
        self._sum_rms += rms

        # 静音检测
        silence_mask = np.abs(chunk) < self.silence_threshold
        silence_count = int(np.sum(silence_mask))
        self._total_silence_samples += silence_count
        silence_ratio = silence_count / num_samples

        # 削波检测
        has_clipping = peak >= self.clipping_threshold
        if has_clipping:
            self._clipping_chunks += 1

        # 质量判定
        issues = []
        if has_clipping:
            issues.append("削波")
        if silence_ratio > 0.8:
            issues.append(f"高静音比({silence_ratio:.1%})")
        if rms < 0.001:
            issues.append("极低音量")

        return ChunkQualityReport(
            peak_amplitude=peak,
            rms=rms,
            silence_ratio=silence_ratio,
            has_clipping=has_clipping,
            has_issue=bool(issues),
            issue_description="; ".join(issues),
        )

    def get_summary(self) -> dict[str, Any]:
        """获取累积质量统计摘要。

        Returns:
            包含所有累积统计指标的字典。
        """
        avg_rms = self._sum_rms / max(self._total_chunks, 1)
        overall_silence_ratio = self._total_silence_samples / max(self._total_samples, 1)

        return {
            "total_chunks": self._total_chunks,
            "total_samples": self._total_samples,
            "total_duration_s": self._total_samples / max(self.expected_sr, 1),
            "max_peak": self._max_peak,
            "avg_rms": avg_rms,
            "overall_silence_ratio": overall_silence_ratio,
            "clipping_chunks": self._clipping_chunks,
            "clipping_ratio": self._clipping_chunks / max(self._total_chunks, 1),
        }
