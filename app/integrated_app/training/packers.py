"""数据打包 / 拼接 / Packed Dataset 算法。

training/ 目录对应 WebUI 中 LoRA 微调 Tab 的训练任务；scripts/train_voxcpm_finetune.py
在准备 batch 时会调用本模块：
  - ``AudioFeatureProcessingPacker``：将 padding-based 批次的 text_tokens / audio_tokens
    打包为 VoxCPM 模型要求的 packed 多模态表示（text mask / audio mask / position ids /
    loss mask / labels 等）；
  - ``LengthSortedBatchPacker`` / ``DynamicBucketPacker``：在进入 padding 之前按序列
    长度"分桶 + 拼接"，通过 Chunked Attention 思路减少 padding 浪费，配合 Flash
    Attention 可让变长序列训练吞吐提升 2-3×。

为什么不直接用固定长度 padding：
小样本（2s）在 batch 里占 20s 的固定长度时，90% token 都是 <pad>，Attention 计算
有 90% 浪费；packer 通过把多条短样本首尾拼接 / 等长分桶，padding 比例可降到 <10%，
显存占用下降、单步训练速度显著提升。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

import torch
import torch.nn as nn
from einops import rearrange

try:
    from typing import Literal
except ImportError:  # pragma: no cover
    Literal = Any  # type: ignore[misc,assignment]

from .data import DatasetEntry  # noqa: F401  # 供外部 import 时使用

logger = logging.getLogger("tts_multimodel.training.packers")


# ---------------------------------------------------------------------- #
# Protocol / 辅助类型
# ---------------------------------------------------------------------- #
class _AudioVAE(Protocol):
    """AudioVAE 协议：AudioFeatureProcessingPacker 使用的最小接口。"""

    hop_length: int
    sample_rate: int

    def encode(self, wav: torch.Tensor, sample_rate: int) -> torch.Tensor: ...


# ---------------------------------------------------------------------- #
# LengthSortedBatchPacker：按长度排序后贪心填桶
# ---------------------------------------------------------------------- #
class LengthSortedBatchPacker:
    """按样本长度排序后贪心打包，使每个 batch 内总 tokens ≤ max_batch_tokens。

    典型做法：先按音频时长（或预估 token 数）从大到小排序，然后依次把样本
    放入第一个能容纳下的 batch 桶里，从而把 padding 比例降到最低。

    Args:
        max_batch_tokens: 每个 batch 允许的总 token 数上限（audio+text tokens）
        sort_key: 从 DatasetEntry 计算"估算 token 数"的函数，默认按 duration*50
                  （24kHz 下 1s≈50 VAE frames）
    """

    def __init__(
        self,
        max_batch_tokens: int = 3000,
        sort_key: Callable[[DatasetEntry], int] = lambda e: max(1, int(e.duration * 50)),
    ) -> None:
        """初始化按长度排序的贪心打包器。

        典型做法：先按音频时长（或预估 token 数）从大到小排序，然后依次把样本
        放入第一个能容纳下的 batch 桶里（best-fit），从而把 padding 比例降到最低。

        Args:
            max_batch_tokens: 每个 batch 允许的总 token 数上限（audio+text tokens）
            sort_key: 从 DatasetEntry 计算"估算 token 数"的函数，默认按 duration*50
                      （24kHz 下 1s≈50 VAE frames）
        """
        # Why max_batch_tokens 默认 3000：
        # 24kHz 音频下 1 token ≈ 20ms；3000 tokens ≈ 60s 总音频时长；
        # batch_size=2 时平均 30s/条。实测 VoxCPM2 单卡 12GB 下 3500 tokens
        # 会触发 OOM（中间激活峰值），3000 留出约 15% 裕度，正好贴合
        # AGENTS §6 的 90% 显存熔断约束。
        if max_batch_tokens <= 0:
            logger.warning("max_batch_tokens 非法（%d），回退默认 3000", max_batch_tokens)
            max_batch_tokens = 3000
        self.max_batch_tokens: int = int(max_batch_tokens)
        self.sort_key: Callable[[DatasetEntry], int] = sort_key

    def pack(self, entries: list[DatasetEntry]) -> list[list[DatasetEntry]]:
        """把 entries 按贪心打包成 batch 列表。

        Args:
            entries: 原始 DatasetEntry 列表（顺序任意）

        Returns:
            批次列表，每个 batch 的总估算 token 数 ≤ max_batch_tokens
            （极个别单条远超上限的样本会独立成一个 batch，避免丢弃数据）
        """
        if not entries:
            return []
        # 计算每个样本长度，过滤异常条目（sort_key 异常 -> 跳过）
        scored: list[tuple[int, DatasetEntry]] = []
        for e in entries:
            try:
                n = int(self.sort_key(e))
                if n < 0:
                    logger.warning(
                        "LengthSortedBatchPacker: sort_key 返回负数（%d），条目 %s 已跳过",
                        n,
                        e.audio_path.name if e.audio_path else "<unknown>",
                    )
                    continue
                scored.append((max(1, n), e))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "LengthSortedBatchPacker: sort_key 异常，条目 %s 已跳过: %s",
                    e.audio_path.name if e.audio_path else "<unknown>",
                    exc,
                )
        if not scored:
            return []
        # 按长度从大到小排序（大的先装桶，碎片最小化）
        scored.sort(key=lambda x: x[0], reverse=True)
        batches: list[list[DatasetEntry]] = []
        batch_loads: list[int] = []
        for length, entry in scored:
            placed = False
            # 找到第一个能塞进去的 batch（best-fit）
            best_idx = -1
            best_remain = 1 << 62
            for i, load in enumerate(batch_loads):
                remain = self.max_batch_tokens - load
                if remain >= length and remain < best_remain:
                    best_idx = i
                    best_remain = remain
            if best_idx >= 0:
                batches[best_idx].append(entry)
                batch_loads[best_idx] += length
                placed = True
            if not placed:
                # 新建一个 batch
                batches.append([entry])
                batch_loads.append(length)
        return batches


# ---------------------------------------------------------------------- #
# DynamicBucketPacker：按时长阈值分桶（1s/5s/10s/20s）
# ---------------------------------------------------------------------- #
class DynamicBucketPacker:
    """按时长阈值动态分桶（默认 [1,5,10,20]s 四桶）。

    桶内样本长度接近，padding 浪费最小；同时每个桶独立组成 batch，
    对 Flash Attention / Paged Attention 更友好（每个 batch 长度方差小）。

    Args:
        bucket_boundaries_sec: 桶的上界（秒），最后一个桶为 (last_boundary, +inf)
        max_batch_tokens: 每个 batch 的 token 上限（语义同 LengthSortedBatchPacker）
        sort_key: 从 DatasetEntry 得到 token 估算的函数
    """

    def __init__(
        self,
        bucket_boundaries_sec: tuple[float, ...] = (1.0, 5.0, 10.0, 20.0),
        max_batch_tokens: int = 3000,
        sort_key: Callable[[DatasetEntry], int] = lambda e: max(1, int(e.duration * 50)),
    ) -> None:
        """初始化动态时长分桶打包器。

        桶内样本长度接近，padding 浪费最小；同时每个桶独立组成 batch，
        对 Flash Attention / Paged Attention 更友好（每个 batch 长度方差小）。

        Args:
            bucket_boundaries_sec: 桶的上界（秒），最后一个桶为 (last_boundary, +inf)
            max_batch_tokens: 每个 batch 的 token 上限（语义同 LengthSortedBatchPacker）
            sort_key: 从 DatasetEntry 得到 token 估算的函数
        """
        if max_batch_tokens <= 0:
            logger.warning("max_batch_tokens 非法（%d），回退默认 3000", max_batch_tokens)
            max_batch_tokens = 3000
        self.bucket_boundaries: tuple[float, ...] = tuple(bucket_boundaries_sec)
        self.max_batch_tokens: int = int(max_batch_tokens)
        self.sort_key: Callable[[DatasetEntry], int] = sort_key

    def _bucket_index(self, duration: float) -> int:
        """根据音频时长返回所属桶的索引。

        Args:
            duration: 音频时长（秒）

        Returns:
            桶索引（0 到 len(bucket_boundaries)）
        """
        for i, upper in enumerate(self.bucket_boundaries):
            if duration <= float(upper):
                return i
        return len(self.bucket_boundaries)

    def pack(self, entries: list[DatasetEntry]) -> list[list[DatasetEntry]]:
        """按时长分桶后，在每个桶内再按贪心打包。

        Args:
            entries: 任意顺序的 DatasetEntry 列表

        Returns:
            批次列表，每个 batch 都来自同一个桶（长度方差小）
        """
        if not entries:
            return []
        n_buckets = len(self.bucket_boundaries) + 1
        buckets: list[list[DatasetEntry]] = [[] for _ in range(n_buckets)]
        for e in entries:
            try:
                dur = float(e.duration)
            except (TypeError, ValueError) as exc:
                logger.warning("DynamicBucketPacker: duration 异常跳过 %s: %s", e.audio_path, exc)
                continue
            if dur < 0:
                logger.warning("DynamicBucketPacker: 负时长跳过 %s (duration=%s)", e.audio_path, dur)
                continue
            bidx = self._bucket_index(dur)
            buckets[bidx].append(e)
        # 每个桶内复用 LengthSortedBatchPacker 的贪心打包
        sub_packer = LengthSortedBatchPacker(max_batch_tokens=self.max_batch_tokens, sort_key=self.sort_key)
        result: list[list[DatasetEntry]] = []
        for bucket in buckets:
            if bucket:
                result.extend(sub_packer.pack(bucket))
        return result


# ---------------------------------------------------------------------- #
# AudioFeatureProcessingPacker：VoxCPM 原始 packer（100% 保留 + 增强）
# ---------------------------------------------------------------------- #
class AudioFeatureProcessingPacker:
    """将 padding-based batch 打包成 VoxCPM 模型可用的多模态 packed 表示。

    保留 minicpm-audio 风格的全部字段：``text_tokens`` / ``audio_feats`` /
    ``text_mask`` / ``audio_mask`` / ``loss_mask`` / ``position_ids`` /
    ``labels`` / ``audio_task_ids`` / ``audio_dataset_ids`` /
    ``audio_duration_consumed`` / ``text_token_consumed``，确保原有
    train_voxcpm_finetune.py 的 forward / loss 计算完全不用修改。

    Args:
        dataset_cnt: 说话人 / 数据集 id 的上限（用于 audio_duration_consumed 形状）
        max_len: 输出序列允许的最大长度（超过会截断前面部分）
        patch_size: VoxCPM patch_size（AudioVAE 帧合并数）
        feat_dim: AudioVAE 特征维度
        audio_vae: 实现了 encode 方法的 AudioVAE 实例
    """

    def __init__(
        self,
        dataset_cnt: int,
        max_len: int,
        patch_size: int,
        feat_dim: int,
        audio_vae: _AudioVAE,
    ) -> None:
        """初始化 VoxCPM 多模态特征打包器。

        保留 minicpm-audio 风格的全部输出字段，确保原有 train_voxcpm_finetune.py
        的 forward / loss 计算完全不用修改。

        Args:
            dataset_cnt: 说话人 / 数据集 id 的上限（用于 audio_duration_consumed 形状）
            max_len: 输出序列允许的最大长度（超过会截断前面部分）
            patch_size: VoxCPM patch_size（AudioVAE 帧合并数）
            feat_dim: AudioVAE 特征维度
            audio_vae: 实现了 encode 方法的 AudioVAE 实例
        """
        self.audio_start_id = 101
        self.audio_end_id = 102
        # unused now
        self.audio_prompt_start_id = 103
        self.audio_prompt_end_id = 104
        self.text_eos_token_id = 2

        self.patch_size = int(patch_size)
        self.patch_len = int(audio_vae.hop_length * self.patch_size)
        self.feat_dim = int(feat_dim)
        self.dataset_cnt = max(int(dataset_cnt), 1)
        self.max_len = int(max_len)
        self.audio_vae: _AudioVAE = audio_vae

        self.process_functions: dict[str, Callable[..., Any]] = {"tts": self.process_tts_data}
        self.task_id_map: dict[str, int] = {"tts": 1}
        self.id_to_task: dict[int, str] = {idx: usage for usage, idx in self.task_id_map.items()}

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _first_pad_position(tokens: torch.Tensor) -> int | None:
        """找到第一个 pad（-100）位置，不存在时返回 None。"""
        positions = (tokens == -100).nonzero(as_tuple=True)
        if positions[0].numel() == 0:
            return None
        return int(positions[0][0])

    def unpad_text_tokens(self, tokens: torch.Tensor) -> torch.Tensor:
        """去除文本 token 尾部的 padding（-100）。

        Args:
            tokens: 文本 token 张量（一维，尾部 padding 值为 -100）

        Returns:
            去除 padding 后的有效 token 张量
        """
        pad_pos = self._first_pad_position(tokens)
        return tokens if pad_pos is None else tokens[:pad_pos]

    def unpad_audio_tokens(self, tokens: torch.Tensor) -> torch.Tensor:
        """去除音频 token 尾部的 padding（-100）。

        Args:
            tokens: 音频 token/波形张量（一维，尾部 padding 值为 -100）

        Returns:
            去除 padding 后的有效音频张量
        """
        pad_pos = self._first_pad_position(tokens)
        return tokens if pad_pos is None else tokens[:pad_pos]

    def encode_audio(self, wav: torch.Tensor) -> torch.Tensor:
        """使用 AudioVAE 编码原始波形为 latent 特征。

        AudioVAE.encode 期望形状 ``[B, 1, T']``，返回 ``[B, D, T]``；
        这里转置为 ``[B, T, D]`` 以匹配下游 packer 期望。

        Args:
            wav: 形状 ``[T]`` 的单通道波形

        Returns:
            形状 ``[1, T', D]`` 的 latent 特征
        """
        wav = wav.unsqueeze(0)  # [1, T]
        wav = wav.unsqueeze(1)  # [1, 1, T]
        wav_len = wav.size(-1)
        if wav_len % self.patch_len != 0:
            padding_size = self.patch_len - wav_len % self.patch_len
            wav = torch.nn.functional.pad(wav, (0, padding_size))

        with torch.no_grad():
            try:
                z = self.audio_vae.encode(wav, self.audio_vae.sample_rate)  # [1, D, T']
            except Exception as exc:  # noqa: BLE001
                # AudioVAE 推理偶发数值异常，兜底返回 0 特征（训练主循环不应被中断）
                logger.warning("AudioVAE.encode 非致命异常，使用零特征兜底: %s", exc)
                t_vae = max(1, wav_len // self.audio_vae.hop_length // self.patch_size * self.patch_size)
                z = torch.zeros(
                    (1, self.feat_dim, max(1, t_vae)),
                    dtype=wav.dtype,
                    device=wav.device,
                )
            feat = z.transpose(1, 2)  # [1, T', D]
        return feat

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #
    def __call__(
        self,
        audio_tokens: torch.Tensor,
        text_tokens: torch.Tensor,
        task_ids: torch.Tensor,
        dataset_ids: torch.Tensor,
        is_prompts: list[bool],
    ) -> dict[str, torch.Tensor]:
        """Padding-based batching：每条样本独立处理后再 pad 到共同长度。

        结果张量形状统一为 ``[B, T, ...]``，T 不超过 ``self.max_len``。

        Args:
            audio_tokens: ``[B, T_a]`` 原始音频（padding=-100）
            text_tokens:  ``[B, T_t]`` 文本 id（padding=-100）
            task_ids:    ``[B]`` 任务类型 id（tts=1）
            dataset_ids: ``[B]`` 说话人/数据集 id（从 0 起）
            is_prompts:  ``[B]`` 是否为 prompt（prompt 音频部分 loss 被 mask）

        Returns:
            打包好的多模态 batch 字典
        """
        device = audio_tokens.device
        max_dataset_id = int(dataset_ids.max().item()) if dataset_ids.numel() > 0 else -1
        dataset_cnt = max(self.dataset_cnt, max_dataset_id + 1)

        text_tokens_list: list[torch.Tensor] = []
        audio_feats_list: list[torch.Tensor] = []
        text_mask_list: list[torch.Tensor] = []
        audio_mask_list: list[torch.Tensor] = []
        loss_mask_list: list[torch.Tensor] = []
        labels_list: list[torch.Tensor] = []
        audio_task_ids_list: list[torch.Tensor] = []
        audio_dataset_ids_list: list[torch.Tensor] = []
        lengths: list[int] = []

        audio_duration_consumed = torch.zeros(dataset_cnt, dtype=torch.float32, device=device)
        text_token_consumed = torch.zeros(dataset_cnt, dtype=torch.float32, device=device)

        for audio_token, text_token, task_id, dataset_idx, is_prompt in zip(
            audio_tokens, text_tokens, task_ids.tolist(), dataset_ids.tolist(), is_prompts
        ):
            unpad_audio_token = self.unpad_audio_tokens(audio_token).to(torch.float32)
            unpad_text_token = self.unpad_text_tokens(text_token)
            usage = self.id_to_task.get(int(task_id), "tts")
            proc_fn = self.process_functions.get(usage, self.process_tts_data)
            try:
                (
                    packed_text,
                    audio_feat,
                    text_mask,
                    audio_mask,
                    loss_mask,
                    labels,
                    audio_duration,
                    text_token_count,
                ) = proc_fn(unpad_audio_token, unpad_text_token, is_prompt)
            except Exception as exc:  # noqa: BLE001
                # 单条样本 pack 失败绝不能让 2 小时训练白跑：记录后用空样本占位
                logger.warning(
                    "单条样本 pack 失败（dataset_idx=%s），占位跳过: %s",
                    dataset_idx,
                    exc,
                )
                empty_len = 8
                packed_text = torch.zeros(empty_len, dtype=torch.int32, device=device)
                audio_feat = torch.zeros(
                    (empty_len, self.patch_size, self.feat_dim),
                    dtype=torch.float32,
                    device=device,
                )
                text_mask = torch.ones(empty_len, dtype=torch.int32, device=device)
                audio_mask = torch.zeros(empty_len, dtype=torch.int32, device=device)
                loss_mask = torch.zeros(empty_len, dtype=torch.int32, device=device)
                labels = torch.zeros(empty_len, dtype=torch.int32, device=device)
                audio_duration = 0.0
                text_token_count = 0

            audio_duration_consumed[dataset_idx] += audio_duration
            text_token_consumed[dataset_idx] += text_token_count

            audio_task_id = torch.zeros_like(audio_mask)
            audio_task_id[audio_mask == 1] = self.task_id_map.get(usage, 1)

            audio_dataset_id = torch.zeros_like(audio_mask)
            audio_dataset_id[audio_mask == 1] = dataset_idx + 1

            text_tokens_list.append(packed_text)
            text_mask_list.append(text_mask)
            audio_feats_list.append(audio_feat)
            audio_mask_list.append(audio_mask)
            loss_mask_list.append(loss_mask)
            labels_list.append(labels)
            audio_task_ids_list.append(audio_task_id)
            audio_dataset_ids_list.append(audio_dataset_id)
            lengths.append(int(packed_text.shape[0]))

        max_len = min(self.max_len, max(lengths)) if lengths else self.max_len

        def pad_1d(x: torch.Tensor, pad_value: int = 0) -> torch.Tensor:
            if x.size(0) >= max_len:
                return x[:max_len]
            pad = torch.full((max_len - x.size(0),), pad_value, dtype=x.dtype, device=x.device)
            return torch.cat([x, pad], dim=0)

        def pad_3d(x: torch.Tensor) -> torch.Tensor:
            # x: [T, P, D]
            if x.size(0) >= max_len:
                return x[:max_len]
            pad = torch.zeros(
                (max_len - x.size(0),) + x.shape[1:],
                dtype=x.dtype,
                device=x.device,
            )
            return torch.cat([x, pad], dim=0)

        if lengths:
            text_tokens_batch = torch.stack([pad_1d(t, pad_value=0) for t in text_tokens_list], dim=0)
            text_mask_batch = torch.stack([pad_1d(m, pad_value=0) for m in text_mask_list], dim=0)
            audio_feats_batch = torch.stack([pad_3d(f) for f in audio_feats_list], dim=0)
            audio_mask_batch = torch.stack([pad_1d(m, pad_value=0) for m in audio_mask_list], dim=0)
            loss_mask_batch = torch.stack([pad_1d(m, pad_value=0) for m in loss_mask_list], dim=0)
            labels_batch = torch.stack([pad_1d(lbl, pad_value=0) for lbl in labels_list], dim=0)
            audio_task_ids_batch = torch.stack([pad_1d(t, pad_value=0) for t in audio_task_ids_list], dim=0)
            audio_dataset_ids_batch = torch.stack([pad_1d(d, pad_value=0) for d in audio_dataset_ids_list], dim=0)

            position_ids_list = []
            for L in lengths:
                L_clip = min(L, max_len)
                pos = torch.arange(0, L_clip, device=device)
                if L_clip < max_len:
                    pad = torch.zeros(max_len - L_clip, dtype=pos.dtype, device=device)
                    pos = torch.cat([pos, pad], dim=0)
                position_ids_list.append(pos)
            position_ids = torch.stack(position_ids_list, dim=0)
        else:
            # Empty batch fallback（ shouldn't really happen）
            text_tokens_batch = torch.zeros((0, self.max_len), dtype=torch.int32, device=device)
            text_mask_batch = torch.zeros_like(text_tokens_batch)
            audio_feats_batch = torch.zeros(
                (0, self.max_len, self.patch_size, self.feat_dim),
                dtype=torch.float32,
                device=device,
            )
            audio_mask_batch = torch.zeros_like(text_tokens_batch)
            loss_mask_batch = torch.zeros_like(text_tokens_batch)
            labels_batch = torch.zeros_like(text_tokens_batch)
            audio_task_ids_batch = torch.zeros_like(text_tokens_batch)
            audio_dataset_ids_batch = torch.zeros_like(text_tokens_batch)
            position_ids = torch.zeros_like(text_tokens_batch)

        audio_duration_consumed = audio_duration_consumed.to(torch.long)
        text_token_consumed = text_token_consumed.to(torch.long)

        return {
            "text_tokens": text_tokens_batch,
            "audio_feats": audio_feats_batch,
            "text_mask": text_mask_batch,
            "audio_mask": audio_mask_batch,
            "loss_mask": loss_mask_batch,
            "position_ids": position_ids,
            "labels": labels_batch,
            "audio_task_ids": audio_task_ids_batch,
            "audio_dataset_ids": audio_dataset_ids_batch,
            "audio_duration_consumed": audio_duration_consumed,
            "text_token_consumed": text_token_consumed,
        }

    # ------------------------------------------------------------------ #
    # Feature extraction helpers
    # ------------------------------------------------------------------ #
    def extract_audio_feats(self, audio_data: torch.Tensor) -> tuple[torch.Tensor, float]:
        """对单条音频执行 AudioVAE 编码 + patch reshape。

        Args:
            audio_data: ``[T]`` 形状的原始波形

        Returns:
            (audio_feats ``[1, T_patch, P, D]``, audio_duration_sec)
        """
        audio_feats = self.encode_audio(audio_data)
        if audio_feats.size(1) % self.patch_size != 0:
            audio_feats_ = audio_feats.transpose(1, 2)
            pad_amount = self.patch_size - audio_feats.size(1) % self.patch_size
            padding = nn.functional.pad(audio_feats_, (0, pad_amount))
            audio_feats = padding.transpose(1, 2)

        audio_duration = float(audio_feats.size(1)) / 25.0
        audio_feats = rearrange(audio_feats, "b (t p) c -> b t p c", p=self.patch_size)
        return audio_feats, audio_duration

    def process_tts_data(
        self,
        audio_token: torch.Tensor,
        text_token: torch.Tensor,
        is_prompt: bool = False,
    ) -> tuple[
        torch.Tensor,
        torch.Tensor,
        torch.Tensor,
        torch.Tensor,
        torch.Tensor,
        torch.Tensor,
        float,
        int,
    ]:
        """TTS 任务专用的单样本打包函数。

        拼接顺序::

            [text tokens] + [audio_start_id] + [audio patch tokens] + [audio_end_id]

        对应的 masks / labels / task ids 按同样的布局构造。

        Args:
            audio_token: ``[T_a]`` 已去 padding 的音频波形
            text_token:  ``[T_t]`` 已去 padding 的文本 id 序列
            is_prompt:   是否仅作为参考 prompt（此时音频部分 loss_mask=0）

        Returns:
            8 元组：packed_text, audio_feat, text_mask, audio_mask, loss_mask,
            labels, audio_duration_sec, text_token_count
        """
        text_token_info = torch.cat(
            [
                text_token,
                torch.tensor(
                    [self.audio_prompt_start_id if is_prompt else self.audio_start_id],
                    dtype=torch.int32,
                    device=text_token.device,
                ),
            ],
            dim=-1,
        )
        text_token_count = int(text_token.numel())
        text_length = int(text_token_info.shape[0])
        audio_feat_info, audio_duration = self.extract_audio_feats(audio_token)
        audio_feat_info = audio_feat_info.squeeze(0)
        audio_length = int(audio_feat_info.shape[0])

        text_pad_token = torch.zeros(audio_length, dtype=torch.int32, device=text_token.device)
        text_token_info = torch.cat(
            [
                text_token_info,
                text_pad_token,
                torch.tensor(
                    [self.audio_prompt_end_id if is_prompt else self.audio_end_id],
                    dtype=torch.int32,
                    device=text_token.device,
                ),
            ]
        )
        audio_pad_feat = torch.zeros(
            (text_length, self.patch_size, audio_feat_info.size(-1)),
            dtype=torch.float32,
            device=text_token.device,
        )
        audio_feat_info = torch.cat([audio_pad_feat, audio_feat_info, audio_pad_feat[0:1, ...]], dim=0)

        text_mask = (
            torch.cat([torch.ones(text_length), torch.zeros(audio_length), torch.ones(1)])
            .type(torch.int32)
            .to(text_token.device)
        )
        audio_mask = (
            torch.cat([torch.zeros(text_length), torch.ones(audio_length), torch.zeros(1)])
            .type(torch.int32)
            .to(text_token.device)
        )
        loss_mask = (
            torch.cat(
                [
                    torch.zeros(text_length),
                    torch.zeros(audio_length) if is_prompt else torch.ones(audio_length),
                    torch.zeros(1),
                ]
            )
            .type(torch.int32)
            .to(text_token.device)
        )

        labels = torch.zeros(text_length + audio_length + 1).type(torch.int32).to(text_token.device)
        labels[-2] = 1

        return (
            text_token_info,
            audio_feat_info,
            text_mask,
            audio_mask,
            loss_mask,
            labels,
            audio_duration,
            text_token_count,
        )
