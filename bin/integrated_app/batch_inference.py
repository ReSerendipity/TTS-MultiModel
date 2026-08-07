"""
批量推理工具 - 提供高效的批量 TTS 推理能力。

在显存允许的范围内，将多个文本片段批量送入模型推理，
相比逐句推理可显著减少 GPU kernel launch 开销，提升整体吞吐量。
包含动态 batch size 调整、显存自适应和错误回退机制。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import torch

logger = logging.getLogger("tts_multimodel")


@dataclass
class BatchInferenceResult:
    """批量推理单个结果项。

    Attributes:
        index: 在原始批量中的索引。
        success: 是否推理成功。
        audio: 生成的音频张量，失败时为 None。
        error: 失败时的错误信息，成功时为 None。
        elapsed_ms: 该项目推理耗时（毫秒）。
    """

    index: int
    success: bool
    audio: torch.Tensor | None = None
    error: str | None = None
    elapsed_ms: float = 0.0


@dataclass
class BatchInferenceStats:
    """批量推理统计信息。

    Attributes:
        total_items: 总项目数。
        successful: 成功数量。
        failed: 失败数量。
        total_elapsed_ms: 总耗时（毫秒）。
        avg_per_item_ms: 平均每个项目耗时（毫秒）。
        batch_size_used: 使用的 batch size。
        vram_peak_mb: 峰值显存占用（MB）。
    """

    total_items: int = 0
    successful: int = 0
    failed: int = 0
    total_elapsed_ms: float = 0.0
    avg_per_item_ms: float = 0.0
    batch_size_used: int = 0
    vram_peak_mb: float = 0.0


class BatchInferencer:
    """批量推理器，支持动态 batch size 和显存自适应。

    通过将多个推理请求合并为一次批量前向传播，减少 GPU
    内核启动开销和 CPU-GPU 同步次数，从而提升吞吐量。

    Attributes:
        max_batch_size: 最大允许的 batch size。
        max_vram_mb: 单批次最大显存占用预算（MB）。
        _current_batch_size: 当前自适应调整后的 batch size。
    """

    def __init__(
        self,
        max_batch_size: int = 4,
        max_vram_mb: float = 2048,
        min_batch_size: int = 1,
    ) -> None:
        """初始化批量推理器。

        Args:
            max_batch_size: 最大 batch size，默认 4。
            max_vram_mb: 单批次最大显存预算（MB），默认 2048。
            min_batch_size: 最小 batch size，默认 1。
        """
        self.max_batch_size = max_batch_size
        self.min_batch_size = min_batch_size
        self.max_vram_mb = max_vram_mb
        self._current_batch_size = max_batch_size

    @property
    def current_batch_size(self) -> int:
        """获取当前自适应 batch size。

        Returns:
            当前使用的 batch size。
        """
        return self._current_batch_size

    def _get_free_vram_mb(self, device: torch.device) -> float:
        """获取设备当前可用显存（MB）。

        Args:
            device: PyTorch 设备对象。

        Returns:
            可用显存（MB），CPU 设备返回无穷大。
        """
        if device.type == "cuda":
            free, total = torch.cuda.mem_get_info(device)
            return free / (1024 * 1024)
        return float("inf")

    def _log_vram(self, device: torch.device, label: str) -> None:
        """记录当前显存使用情况。

        Args:
            device: PyTorch 设备对象。
            label: 日志标签。
        """
        if device.type == "cuda" and logger.isEnabledFor(10):  # DEBUG
            allocated = torch.cuda.memory_allocated(device) / (1024 * 1024)
            reserved = torch.cuda.memory_reserved(device) / (1024 * 1024)
            logger.debug(f"[VRAM] {label}: allocated={allocated:.0f}MB, reserved={reserved:.0f}MB")

    def _shrink_batch(self) -> int:
        """减小 batch size（OOM 后回退）。

        Returns:
            调整后的 batch size。
        """
        new_size = max(self.min_batch_size, self._current_batch_size - 1)
        if new_size < self._current_batch_size:
            logger.warning(f"批量推理 OOM，batch size {self._current_batch_size} -> {new_size}")
            self._current_batch_size = new_size
        return self._current_batch_size

    def _try_grow_batch(self) -> int:
        """尝试增大 batch size（连续成功后扩张）。

        Returns:
            调整后的 batch size。
        """
        new_size = min(self.max_batch_size, self._current_batch_size + 1)
        if new_size > self._current_batch_size:
            logger.debug(f"批量推理连续成功，batch size {self._current_batch_size} -> {new_size}")
            self._current_batch_size = new_size
        return self._current_batch_size

    @torch.no_grad()
    def run(
        self,
        items: list[dict[str, Any]],
        inference_fn: Callable[[list[dict[str, Any]]], list[torch.Tensor]],
        device: torch.device | None = None,
        on_item_done: Callable[[BatchInferenceResult], None] | None = None,
    ) -> tuple[list[BatchInferenceResult], BatchInferenceStats]:
        """执行批量推理。

        将输入 items 按当前 batch size 分批次送入 inference_fn。
        如果某批次 OOM，自动减小 batch size 并逐项目重试失败批次。

        Args:
            items: 推理输入项列表，每项为参数字典。
            inference_fn: 批量推理函数，接收一个批次的参数字典列表，
                返回对应数量的音频张量列表。
            device: 推理设备，用于显存监控。
            on_item_done: 单个项目完成时的回调函数。

        Returns:
            (结果列表, 统计信息) 元组。结果列表索引与 items 一一对应。
        """
        if not items:
            return [], BatchInferenceStats(total_items=0)

        device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        all_results: dict[int, BatchInferenceResult] = {}
        stats = BatchInferenceStats(total_items=len(items), batch_size_used=self._current_batch_size)

        vram_before = torch.cuda.memory_allocated(device) if device.type == "cuda" else 0
        t_start = time.perf_counter()

        consecutive_successes = 0

        # 按批次处理
        offset = 0
        while offset < len(items):
            batch_size = min(self._current_batch_size, len(items) - offset)
            batch_items = items[offset : offset + batch_size]
            batch_indices = list(range(offset, offset + batch_size))

            self._log_vram(device, f"批次 {offset} 前")

            try:
                t_batch_start = time.perf_counter()
                outputs = inference_fn(batch_items)
                t_batch_elapsed = (time.perf_counter() - t_batch_start) * 1000

                per_item_ms = t_batch_elapsed / len(batch_items)

                for bi, audio in enumerate(outputs):
                    idx = batch_indices[bi]
                    result = BatchInferenceResult(
                        index=idx,
                        success=True,
                        audio=audio,
                        elapsed_ms=per_item_ms,
                    )
                    all_results[idx] = result
                    stats.successful += 1
                    if on_item_done:
                        on_item_done(result)

                self._log_vram(device, f"批次 {offset} 后")
                consecutive_successes += 1

                # 连续 5 批次成功后尝试增大 batch size
                if consecutive_successes >= 5:
                    self._try_grow_batch()
                    consecutive_successes = 0
                    stats.batch_size_used = self._current_batch_size

                offset += batch_size

            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                logger.warning(f"批次 {offset} OOM，已清空缓存，减小 batch size 重试")

                new_bs = self._shrink_batch()
                stats.batch_size_used = new_bs
                consecutive_successes = 0

                if new_bs == 1:
                    # batch_size=1 时逐项目重试，失败的项目标记为错误
                    for bi, item in enumerate(batch_items):
                        idx = batch_indices[bi]
                        try:
                            t_item = time.perf_counter()
                            out = inference_fn([item])
                            item_ms = (time.perf_counter() - t_item) * 1000
                            result = BatchInferenceResult(index=idx, success=True, audio=out[0], elapsed_ms=item_ms)
                            all_results[idx] = result
                            stats.successful += 1
                        except Exception as e:
                            result = BatchInferenceResult(index=idx, success=False, error=str(e))
                            all_results[idx] = result
                            stats.failed += 1
                            logger.error(f"项目 {idx} 推理失败: {e}")
                        if on_item_done:
                            on_item_done(result)
                    offset += batch_size
                # 如果 new_bs > 1，外层 while 循环会用更小 batch_size 重试该批次

            except Exception as e:
                logger.error(f"批次 {offset} 推理异常: {e}")
                for bi in range(len(batch_items)):
                    idx = batch_indices[bi]
                    result = BatchInferenceResult(index=idx, success=False, error=str(e))
                    all_results[idx] = result
                    stats.failed += 1
                    if on_item_done:
                        on_item_done(result)
                offset += batch_size

        total_elapsed = (time.perf_counter() - t_start) * 1000
        stats.total_elapsed_ms = total_elapsed
        stats.avg_per_item_ms = total_elapsed / len(items) if items else 0

        if device.type == "cuda":
            vram_after = torch.cuda.memory_allocated(device)
            stats.vram_peak_mb = max(vram_before, vram_after) / (1024 * 1024)

        # 按原始顺序排列结果
        ordered = [all_results[i] for i in range(len(items))]
        return ordered, stats

    @torch.no_grad()
    def run_simple(
        self,
        texts: list[str],
        inference_fn: Callable[[str], torch.Tensor],
        device: torch.device | None = None,
    ) -> list[torch.Tensor | None]:
        """简化的批量推理接口，逐项目执行但共享 no_grad 上下文。

        适用于不支持批量输入的模型，提供统一接口和错误处理。

        Args:
            texts: 待推理文本列表。
            inference_fn: 单文本推理函数。
            device: 推理设备。

        Returns:
            音频张量列表，失败项为 None。
        """
        results: list[torch.Tensor | None] = []
        for text in texts:
            try:
                audio = inference_fn(text)
                results.append(audio)
            except Exception as e:
                logger.error(f"文本 '{text[:30]}...' 推理失败: {e}")
                results.append(None)
        return results


_default_inferencer: BatchInferencer | None = None


def get_batch_inferencer(
    max_batch_size: int = 4,
    max_vram_mb: float = 2048,
) -> BatchInferencer:
    """获取全局默认批量推理器（单例模式）。

    Args:
        max_batch_size: 最大 batch size。
        max_vram_mb: 最大显存预算（MB）。

    Returns:
        BatchInferencer 单例。
    """
    global _default_inferencer
    if _default_inferencer is None:
        _default_inferencer = BatchInferencer(max_batch_size=max_batch_size, max_vram_mb=max_vram_mb)
    return _default_inferencer
