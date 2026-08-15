"""LoRA 训练的数据集封装、Batch 处理与 DataLoader 构建。

training/ 目录对应 WebUI 中 LoRA 微调 Tab 的训练任务；scripts/train_voxcpm_finetune.py
会在启动训练前首先调用本模块：
  1. ``HFVoxCPMDataset`` 从 ``data_dir`` 扫描 .wav + 同名 .txt 对（或 metadata.jsonl），
     做时长过滤与 train/eval 切分；
  2. ``BatchProcessor`` 对 batch 执行 Mel 谱提取 → 文本 tokenize → 时长对齐 → padding；
  3. ``create_dataloaders`` / ``build_dataloader`` 构建带 DistributedSampler 的
     DataLoader（由 accelerator 提供）。

数据格式：
  - 最简：``data_dir`` 下放一对对 ``sample001.wav`` + ``sample001.txt``（txt 中是对应文本）
  - 进阶：在 ``data_dir`` 下放 ``metadata.jsonl``，每行形如
    ``{"audio_file": "sample001.wav", "text": "你好", "speaker_id": "spk_1", "duration": 2.3}``
"""

from __future__ import annotations

import json
import logging
import math
import random
import statistics
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import argbind
import torch
from datasets import Audio, DatasetDict, load_dataset
from datasets import Dataset as HFDataset
from torch.utils.data import DataLoader
from torch.utils.data import Dataset as TorchDataset

try:
    from typing import Literal
except ImportError:  # pragma: no cover
    Literal = Any  # type: ignore[misc,assignment]

try:
    import soundfile as sf  # type: ignore[import]
except ImportError:  # pragma: no cover - torchaudio/librosa 兜底
    sf = None  # type: ignore[assignment]

from ..model.voxcpm import VoxCPMConfig
from ..modules.audiovae import AudioVAE
from .packers import AudioFeatureProcessingPacker

DEFAULT_TEXT_COLUMN = "text"
DEFAULT_AUDIO_COLUMN = "audio"
DEFAULT_ID_COLUMN = "dataset_id"

logger = logging.getLogger("tts_multimodel.training.data")


class DatasetEntry(NamedTuple):
    """单条训练样本的结构化描述（HFVoxCPMDataset 返回的条目）。

    Attributes:
        audio_path: wav 文件绝对路径
        text: 对应文本（已 strip）
        speaker_id: 可选说话人 ID（多说话人数据集用）
        duration: 音频实际时长（秒），用于排序打包 / 过滤
    """

    audio_path: Path
    text: str
    speaker_id: str | None
    duration: float


# ---------------------------------------------------------------------- #
# 新版本：基于本地 data_dir 扫描的 DatasetEntry 风格 Dataset
# ---------------------------------------------------------------------- #
class HFVoxCPMDataset(TorchDataset[DatasetEntry]):
    """从本地 data_dir 读取 wav+txt 对 / metadata.jsonl 的 PyTorch Dataset。

    同时保留了对原始 HuggingFace Dataset 对象的薄封装（__init__ 第一个参数为
    HF Dataset 时走 legacy 分支），以满足 100% 向后兼容。

    Args:
        data_dir: 数据集目录（Path）或 HuggingFace Dataset 对象（legacy）
        split: "train" / "eval" 切分
        split_ratio: train 集占比（0.7~0.99）
        sample_rate: 采样率（仅用于校验 / 预估时长）
        min_duration: 最短有效时长，小于则丢弃
        max_duration: 最长有效时长，超过则丢弃
        seed: 切分随机种子（保证多次跑切分一致）
    """

    # Legacy 薄封装
    _legacy_dataset: HFDataset | None = None
    # 新风格条目
    _entries: list[DatasetEntry]
    _skipped_paths: list[tuple[Path, str]]
    _total_scanned: int = 0

    def __init__(
        self,
        data_dir: Path | str | HFDataset,
        split: Literal["train", "eval"] = "train",  # noqa: UP037
        split_ratio: float = 0.9,
        sample_rate: int = 16000,
        min_duration: float = 1.0,
        max_duration: float = 20.0,
        seed: int = 42,
    ) -> None:
        """初始化 VoxCPM 训练数据集。

        支持两种构造方式：
        - 新风格：传入 Path/str 作为 data_dir，自动扫描 wav+txt 对或 metadata.jsonl
        - Legacy：传入 HuggingFace Dataset 对象，走旧版兼容分支

        Args:
            data_dir: 数据集目录路径或 HuggingFace Dataset 对象（legacy 兼容）
            split: 数据集切分，"train" 或 "eval"
            split_ratio: 训练集占比（0.7~0.99），剩余部分为验证集
            sample_rate: 目标采样率（Hz），用于校验和预估时长
            min_duration: 最短有效音频时长（秒），小于该值的样本会被过滤
            max_duration: 最长有效音频时长（秒），超过该值的样本会被过滤
            seed: 随机切分种子，保证多次运行切分结果一致

        Raises:
            ValueError: 数据集目录不存在或没有可用样本时抛出
        """
        # Legacy：第一个参数是 HuggingFace Dataset -> 原样保留（老代码兼容）
        if isinstance(data_dir, HFDataset) or (
            hasattr(data_dir, "column_names") and hasattr(data_dir, "__getitem__") and hasattr(data_dir, "__len__")
        ):
            self._legacy_dataset = data_dir  # type: ignore[assignment]
            self._entries = []
            self._skipped_paths = []
            return
        # New：Path/str -> 扫描本地目录
        data_dir = Path(data_dir)
        if not data_dir.exists():
            raise ValueError(f"数据集目录不存在: {data_dir}")
        entries_raw, skipped, total = self._scan_data_dir(
            data_dir=data_dir,
            sample_rate=sample_rate,
            min_duration=min_duration,
            max_duration=max_duration,
        )
        self._skipped_paths = skipped
        self._total_scanned = total
        if not entries_raw:
            raise ValueError(
                f"数据集目录 {data_dir} 下没有可用的 wav/txt 对，请按格式准备数据集，"
                f"或下载示例数据集。扫描到 {total} 个 wav，其中 {len(skipped)} 个因损坏/"
                f"时长越界被跳过，0 个可用。"
            )
        # 固定种子切分，保证 train/eval 一致
        rng = random.Random(seed)
        rng.shuffle(entries_raw)
        cutoff = max(1, int(len(entries_raw) * split_ratio))
        if split == "train":
            self._entries = entries_raw[:cutoff]
        else:
            self._entries = entries_raw[cutoff:] if len(entries_raw) > cutoff else entries_raw[-1:]
        # Why min_duration=1.0 / max_duration=20.0：
        # < 1s 的样本几乎不含有效语音信息（除了静音只有 0.2s 人声），训练贡献极小；
        # > 20s 的样本会让 batch padding 浪费 80%（其他样本 2s 要补 18s 静音），
        # 且单 batch 显存容易触发 AGENTS §6 的 90% 显存熔断。单卡 12GB 的
        # VoxCPM2 在 batch_size=2 时，20s 样本组合正好是安全边界。

    # ------------------------------------------------------------------ #
    # 扫描 helpers
    # ------------------------------------------------------------------ #
    def _scan_data_dir(
        self,
        data_dir: Path,
        sample_rate: int,
        min_duration: float,
        max_duration: float,
    ) -> tuple[list[DatasetEntry], list[tuple[Path, str]], int]:
        """扫描 data_dir：优先 metadata.jsonl，其次 wav+txt 对。

        Returns:
            (valid_entries, skipped[(path, reason)], total_scanned)
        """
        valid: list[DatasetEntry] = []
        skipped: list[tuple[Path, str]] = []
        meta_path = data_dir / "metadata.jsonl"
        if meta_path.exists():
            lines = meta_path.read_text(encoding="utf-8").splitlines()
            total = 0
            for line_no, raw in enumerate(lines, 1):
                raw = raw.strip()
                if not raw:
                    continue
                total += 1
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError as e:
                    skipped.append((meta_path, f"metadata 第{line_no}行 JSON 错误: {e}"))
                    continue
                audio_rel = obj.get("audio_file")
                text = obj.get("text")
                if not audio_rel or text is None:
                    skipped.append((meta_path, f"metadata 第{line_no}行缺少 audio_file/text"))
                    continue
                audio_path = data_dir / audio_rel if not Path(audio_rel).is_absolute() else Path(audio_rel)
                if not audio_path.exists():
                    skipped.append((audio_path, "wav 文件不存在"))
                    continue
                duration = obj.get("duration")
                if duration is None:
                    duration = self._safe_read_duration(audio_path)
                    if duration is None:
                        skipped.append((audio_path, "wav 损坏/无法读取时长"))
                        continue
                speaker_id = obj.get("speaker_id")
                if duration < min_duration or duration > max_duration:
                    skipped.append((audio_path, f"时长 {duration:.2f}s 越界 [{min_duration},{max_duration}]"))
                    continue
                valid.append(
                    DatasetEntry(
                        audio_path=audio_path,
                        text=str(text).strip(),
                        speaker_id=str(speaker_id) if speaker_id is not None else None,
                        duration=float(duration),
                    )
                )
            return valid, skipped, total
        # wav+txt 对 fallback
        wav_files = sorted(p for p in data_dir.glob("*.wav"))
        total = len(wav_files)
        for wav in wav_files:
            txt_path = wav.with_suffix(".txt")
            if not txt_path.exists():
                skipped.append((wav, "缺少同名 .txt 文本"))
                continue
            try:
                text = txt_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError) as e:
                skipped.append((wav, f"txt 读取失败: {e}"))
                continue
            if not text:
                skipped.append((wav, "文本为空"))
                continue
            duration = self._safe_read_duration(wav)
            if duration is None:
                skipped.append((wav, "wav 损坏/无法读取时长"))
                continue
            if duration < min_duration or duration > max_duration:
                skipped.append((wav, f"时长 {duration:.2f}s 越界 [{min_duration},{max_duration}]"))
                continue
            valid.append(
                DatasetEntry(
                    audio_path=wav,
                    text=text,
                    speaker_id=None,
                    duration=float(duration),
                )
            )
        return valid, skipped, total

    @staticmethod
    def _safe_read_duration(path: Path) -> float | None:
        """读取音频时长，损坏时返回 None 并记录。"""
        try:
            if sf is not None:
                info = sf.info(str(path))
                return float(info.duration)
            # sf 缺失 -> 用 torchaudio
            try:
                import torchaudio  # type: ignore[import]

                info = torchaudio.info(str(path))
                return float(info.num_frames) / float(info.sample_rate)
            except Exception:  # noqa: BLE001
                pass
            # 兜底：直接用 librosa
            try:
                import librosa  # type: ignore[import]

                duration = librosa.get_duration(filename=str(path))
                return float(duration)
            except Exception:  # noqa: BLE001
                return None
        except Exception as e:  # noqa: BLE001
            logger.warning("损坏样本已跳过: %s, 原因: %s", path, e)
            return None

    # ------------------------------------------------------------------ #
    # Dataset 接口
    # ------------------------------------------------------------------ #
    def __len__(self) -> int:
        """返回数据集中的样本数量。

        Returns:
            样本总数（train 或 eval split 的大小）
        """
        if self._legacy_dataset is not None:
            return len(self._legacy_dataset)
        return len(self._entries)

    def __getitem__(self, idx: int) -> DatasetEntry:
        """获取指定索引的训练样本。

        Legacy 分支会将 HuggingFace Dataset 的一行包装成 DatasetEntry；
        新风格直接返回预扫描的 DatasetEntry。

        Args:
            idx: 样本索引

        Returns:
            DatasetEntry 结构化样本
        """
        # Legacy 分支：把 HF Dataset 的一行包装成 DatasetEntry
        if self._legacy_dataset is not None:
            item = self._legacy_dataset[idx]
            audio = item.get(DEFAULT_AUDIO_COLUMN, {})
            text = str(item.get(DEFAULT_TEXT_COLUMN, "")).strip()
            audio_path: Path
            if isinstance(audio, dict) and "path" in audio and audio["path"]:
                audio_path = Path(audio["path"])
            else:
                audio_path = Path(f"sample_{idx}.wav")
            duration = 0.0
            if isinstance(audio, dict):
                sr = audio.get("sampling_rate", 16000)
                arr = audio.get("array")
                if arr is not None and hasattr(arr, "__len__") and sr:
                    duration = float(len(arr)) / float(sr)
            spk = item.get("speaker_id") or item.get(DEFAULT_ID_COLUMN)
            return DatasetEntry(
                audio_path=audio_path,
                text=text,
                speaker_id=str(spk) if spk is not None else None,
                duration=duration,
            )
        return self._entries[idx]

    def stats(self) -> dict[str, Any]:
        """返回数据集统计：总数 / 跳过数 / 时长 min/max/avg / speaker 数。

        Returns:
            包含 total_count / skipped_count / min_duration / max_duration /
            avg_duration / speaker_count / skipped_samples 键的字典
        """
        if self._legacy_dataset is not None:
            # Legacy：能给多少算多少
            total = len(self._legacy_dataset)
            return {
                "total_count": total,
                "skipped_count": 0,
                "min_duration": None,
                "max_duration": None,
                "avg_duration": None,
                "speaker_count": None,
                "skipped_samples": [],
            }
        if not self._entries:
            return {
                "total_count": 0,
                "skipped_count": len(self._skipped_paths),
                "min_duration": 0.0,
                "max_duration": 0.0,
                "avg_duration": 0.0,
                "speaker_count": 0,
                "skipped_samples": [(str(p), r) for p, r in self._skipped_paths[:100]],
            }
        durations = [e.duration for e in self._entries]
        speakers = {e.speaker_id for e in self._entries if e.speaker_id}
        return {
            "total_count": len(self._entries),
            "skipped_count": len(self._skipped_paths),
            "min_duration": min(durations),
            "max_duration": max(durations),
            "avg_duration": statistics.fmean(durations),
            "speaker_count": len(speakers),
            "skipped_samples": [(str(p), r) for p, r in self._skipped_paths[:100]],
        }

    # ------------------------------------------------------------------ #
    # Legacy padding 工具（保留给 collate_fn）
    # ------------------------------------------------------------------ #
    @staticmethod
    def pad_sequences(seqs: list[torch.Tensor], pad_value: float) -> torch.Tensor:
        """把变长 1D tensor padding 到 batch 最长长度。

        Args:
            seqs: 变长 1D tensor 列表
            pad_value: padding 填充值（一般 -100，对应 CrossEntropy ignore_index）

        Returns:
            [B, T] 形状 padding 后的 tensor
        """
        if not seqs:
            return torch.empty(0)
        max_len = max(int(seq.shape[0]) for seq in seqs)
        padded: list[torch.Tensor] = []
        for seq in seqs:
            if seq.shape[0] < max_len:
                pad_width = (0, max_len - int(seq.shape[0]))
                seq = torch.nn.functional.pad(seq, pad_width, value=pad_value)
            padded.append(seq)
        return torch.stack(padded, dim=0)

    @classmethod
    def collate_fn(cls, batch: list[dict[str, Any]]) -> dict[str, torch.Tensor]:
        """Legacy collate_fn：把 ``__getitem__`` 返回的 dict 批量化。

        保留以兼容旧训练代码调用路径；新代码推荐使用 BatchProcessor。
        """
        text_tensors = [torch.tensor(sample["text_ids"], dtype=torch.int32) for sample in batch]
        audio_tensors = [torch.tensor(sample["audio_array"], dtype=torch.float32) for sample in batch]
        dataset_ids = torch.tensor(
            [sample.get(DEFAULT_ID_COLUMN, 0) for sample in batch],
            dtype=torch.int32,
        )
        is_prompts = [bool(sample.get("is_prompt", False)) for sample in batch]

        text_padded = cls.pad_sequences(text_tensors, pad_value=-100)
        audio_padded = cls.pad_sequences(audio_tensors, pad_value=-100.0)
        task_ids = torch.ones(text_padded.size(0), dtype=torch.int32)
        return {
            "text_tokens": text_padded,
            "audio_tokens": audio_padded,
            "task_ids": task_ids,
            "dataset_ids": dataset_ids,
            "is_prompts": is_prompts,
        }

    # Legacy property 访问
    @property
    def dataset(self) -> Any:
        """Legacy 别名：返回 HuggingFace Dataset（legacy 构造时有效）。"""
        return self._legacy_dataset


# ---------------------------------------------------------------------- #
# BatchProcessor：DatasetEntry list -> 模型输入 dict
# ---------------------------------------------------------------------- #
class BatchProcessor:
    """把 DatasetEntry 列表处理成模型可直接 forward 的 batch 张量。

    处理链路：
        wav 读取 & resample → AudioVAE 编码得到 latent → tokenizer.encode 文本 →
        拼接 [text|audio_start|audio|audio_end] → padding 到 max_length

    Args:
        tokenizer: 文本 tokenizer（需实现 encode / __call__）
        feature_extractor: 预留的音频特征提取器接口（当前使用 AudioVAE，保留该参数兼容）
        sample_rate: 目标采样率
        max_length: batch 允许的最大序列长度（文本 token + audio token + 2）
        config: VoxCPMConfig（如传入，则内部再建 AudioVAE Packer 兼容 legacy 调用）
        audio_vae: AudioVAE 实例（与 config 配合时使用）
        dataset_cnt: 说话人/数据集 id 上限
        device: 计算设备
    """

    def __init__(
        self,
        tokenizer: Any | None = None,
        feature_extractor: Any | None = None,
        sample_rate: int = 16000,
        max_length: int = 1024,
        *,
        config: VoxCPMConfig | None = None,
        audio_vae: AudioVAE | None = None,
        dataset_cnt: int = 1,
        device: torch.device | None = None,
    ) -> None:
        """初始化 BatchProcessor。

        处理链路：wav 读取 & resample → AudioVAE 编码得到 latent → tokenizer.encode 文本 →
        拼接 [text|audio_start|audio|audio_end] → padding 到 max_length。

        支持两种构造路径：
        - Legacy 路径：传入 config + audio_vae + dataset_cnt，内部创建 AudioFeatureProcessingPacker
        - 简化路径：仅传 tokenizer/feature_extractor，返回简化的 text_tokens/audio_array/durations

        Args:
            tokenizer: 文本 tokenizer（需实现 encode / __call__）
            feature_extractor: 预留的音频特征提取器接口（当前使用 AudioVAE，保留该参数兼容）
            sample_rate: 目标采样率（Hz）
            max_length: batch 允许的最大序列长度（文本 token + audio token + 2）
            config: VoxCPMConfig（如传入，则内部创建 AudioVAE Packer 兼容 legacy 调用）
            audio_vae: AudioVAE 实例（与 config 配合时使用）
            dataset_cnt: 说话人/数据集 id 上限
            device: 计算设备
        """
        self.tokenizer = tokenizer
        self.feature_extractor = feature_extractor
        self.sample_rate = int(sample_rate)
        self.max_length = int(max_length)
        self._device: torch.device | None = device
        # Legacy 构造路径（config + audio_vae + dataset_cnt 全给时）
        self._legacy_packer: AudioFeatureProcessingPacker | None = None
        self._legacy_audio_vae: AudioVAE | None = audio_vae
        if config is not None and audio_vae is not None:
            if device is not None:
                audio_vae.to(device)
            self._legacy_packer = AudioFeatureProcessingPacker(
                dataset_cnt=max(dataset_cnt, 1),
                max_len=config.max_length,
                patch_size=config.patch_size,
                feat_dim=config.feat_dim,
                audio_vae=audio_vae,
            )
            self._device = device

    def _load_audio(self, path: Path) -> tuple[torch.Tensor, int]:
        """安全读取 wav，损坏时抛出 LibsndfileError 类异常。"""
        if sf is not None:
            arr, sr = sf.read(str(path), always_2d=True)
            audio = torch.from_numpy(arr[:, 0]).float()
            return audio, int(sr)
        try:
            import torchaudio  # type: ignore[import]

            waveform, sr = torchaudio.load(str(path))
            return waveform[0].float(), int(sr)
        except Exception:  # pragma: no cover - 再试 librosa
            try:
                import librosa  # type: ignore[import]

                arr, sr = librosa.load(str(path), sr=None, mono=True)
                return torch.from_numpy(arr).float(), int(sr)
            except Exception as nested:
                raise RuntimeError(f"读取音频失败: {path}") from nested

    def _tokenize(self, text: str) -> list[int]:
        """调用 tokenizer 编码，超长时按 2x max_length 阈值警告 + 截断。"""
        if self.tokenizer is None:
            # 没有 tokenizer 时，直接用字符级 char code（仅兜底）
            return [ord(c) for c in text[: self.max_length]]
        try:
            if callable(getattr(self.tokenizer, "encode", None)):
                ids = self.tokenizer.encode(text, add_special_tokens=False)
            else:
                out = self.tokenizer(text, add_special_tokens=False, return_tensors="np")
                ids = list(out["input_ids"][0])
        except Exception as e:  # noqa: BLE001
            logger.warning("文本 tokenize 失败 '%s': %s，使用字符级兜底", text[:40], e)
            return [ord(c) for c in text[: self.max_length]]
        hard_cap = max(32, self.max_length * 2)
        if len(ids) > hard_cap:
            logger.warning(
                "文本超长 %d tokens，已截断为 max_length=%d（文本前 60 字: %s...）",
                len(ids),
                self.max_length,
                text[:60],
            )
            ids = ids[: self.max_length]
        return ids

    def __call__(self, batch: list[DatasetEntry]) -> dict[str, torch.Tensor]:
        """将 DatasetEntry 列表转为模型输入张量。

        当本 BatchProcessor 由 legacy 路径（config + audio_vae）构造时，内部走
        ``AudioFeatureProcessingPacker``，以保证与现有训练脚本的 collate 输出字段
        完全一致；否则返回一个简化的 text_tokens / audio_array / lengths 字典，
        方便上层按需要自行 pack。
        """
        # Legacy 路径：先处理成 collate_fn 输出的 dict 再交给 packer
        if self._legacy_packer is not None and self._legacy_audio_vae is not None and self._device is not None:
            # 把 batch 中每个 entry 转成 legacy dict 形式
            legacy_batch: list[dict[str, Any]] = []
            for entry in batch:
                try:
                    wav_arr, sr = self._load_audio(entry.audio_path)
                except Exception as e:  # noqa: BLE001
                    logger.warning("损坏样本已跳过: %s, 原因: %s", entry.audio_path, e)
                    continue
                # 简单重采样：若 sr != sample_rate 直接线性插值（够用于训练时长预估）
                if sr != self.sample_rate:
                    wav_arr = (
                        torch.nn.functional.interpolate(
                            wav_arr.unsqueeze(0).unsqueeze(0),
                            scale_factor=float(self.sample_rate) / float(sr),
                            mode="linear",
                            align_corners=False,
                        )
                        .squeeze(0)
                        .squeeze(0)
                    )
                text_ids = self._tokenize(entry.text)
                legacy_batch.append(
                    {
                        "text_ids": text_ids,
                        "audio_array": wav_arr.numpy(),
                        "audio_sampling_rate": self.sample_rate,
                        DEFAULT_ID_COLUMN: 0,
                        "is_prompt": False,
                    }
                )
            if not legacy_batch:
                return {"empty": torch.tensor([1], dtype=torch.int32)}
            collated = HFVoxCPMDataset.collate_fn(legacy_batch)
            # packer 需要在 device 上
            audio_tokens = collated["audio_tokens"].to(self._device)
            text_tokens = collated["text_tokens"].to(self._device)
            task_ids = collated["task_ids"].to(self._device)
            dataset_ids = collated["dataset_ids"].to(self._device)
            return self._legacy_packer(
                audio_tokens=audio_tokens,
                text_tokens=text_tokens,
                task_ids=task_ids,
                dataset_ids=dataset_ids,
                is_prompts=collated["is_prompts"],
            )
        # 简化路径：返回 token / audio / duration 字典
        text_tokens_list: list[torch.Tensor] = []
        audio_arrays: list[torch.Tensor] = []
        durations: list[float] = []
        for entry in batch:
            try:
                wav_arr, sr = self._load_audio(entry.audio_path)
            except Exception as e:  # noqa: BLE001
                logger.warning("损坏样本已跳过: %s, 原因: %s", entry.audio_path, e)
                continue
            if sr != self.sample_rate:
                wav_arr = (
                    torch.nn.functional.interpolate(
                        wav_arr.unsqueeze(0).unsqueeze(0),
                        scale_factor=float(self.sample_rate) / float(sr),
                        mode="linear",
                        align_corners=False,
                    )
                    .squeeze(0)
                    .squeeze(0)
                )
            audio_arrays.append(wav_arr)
            ids = self._tokenize(entry.text)
            text_tokens_list.append(torch.tensor(ids, dtype=torch.int32))
            durations.append(entry.duration)
        if not text_tokens_list:
            return {"empty": torch.tensor([1], dtype=torch.int32)}
        text_padded = HFVoxCPMDataset.pad_sequences(text_tokens_list, pad_value=0)
        audio_padded = HFVoxCPMDataset.pad_sequences(audio_arrays, pad_value=0.0)
        return {
            "text_tokens": text_padded,
            "audio_tokens": audio_padded,
            "durations": torch.tensor(durations, dtype=torch.float32),
        }

    @property
    def device(self) -> torch.device:
        """获取当前 BatchProcessor 绑定的计算设备。

        Returns:
            torch.device，未指定时默认为 CPU
        """
        if self._device is None:
            return torch.device("cpu")
        return self._device

    @property
    def dataset_cnt(self) -> int:
        """获取说话人/数据集数量上限。

        Returns:
            数据集数量，legacy packer 模式下返回实际配置值，否则为 1
        """
        if self._legacy_packer is None:
            return 1
        return int(self._legacy_packer.dataset_cnt)

    @property
    def audio_vae(self) -> AudioVAE | None:
        """获取 AudioVAE 实例（legacy 模式下有效）。

        Returns:
            AudioVAE 实例或 None
        """
        return self._legacy_audio_vae

    @property
    def packer(self) -> AudioFeatureProcessingPacker | None:
        """获取 AudioFeatureProcessingPacker 实例（legacy 模式下有效）。

        Returns:
            AudioFeatureProcessingPacker 实例或 None
        """
        return self._legacy_packer


# ---------------------------------------------------------------------- #
# DataLoader 构建
# ---------------------------------------------------------------------- #
if TYPE_CHECKING:
    from .config import TrainingConfig


def create_dataloaders(
    cfg: TrainingConfig,  # type: ignore[valid-type]  # 延后 import 防循环
    tokenizer: Any | None = None,
    feature_extractor: Any | None = None,
    accelerator: Any | None = None,
    num_workers: int = 0,
) -> tuple[DataLoader, DataLoader]:
    """根据 TrainingConfig 同时构建 train / eval 两个 DataLoader。

    Why num_workers 默认 0：
    Windows 下 DataLoader 多 worker spawn 模式经常卡住（pickle 错误 / 管道断开），
    且 LoRA 训练每步需要 1s 以上，完全抵消了多 worker 预读取节省的 ~10ms I/O 开销。
    Linux 用户可手动传 num_workers=4 获得更高吞吐，Windows 默认以稳定优先。

    Args:
        cfg: TrainingConfig（含 dataset / batch_size 等配置）
        tokenizer: 文本 tokenizer（传给 BatchProcessor）
        feature_extractor: 特征提取器（传给 BatchProcessor）
        accelerator: TrainingAccelerator 或 legacy Accelerator（提供 prepare_dataloader）
        num_workers: DataLoader worker 数，默认 0（主进程读取）

    Returns:
        (train_dataloader, eval_dataloader)
    """
    from .config import DatasetConfig  # local 防循环

    ds_cfg: DatasetConfig = cfg.dataset
    train_ds = HFVoxCPMDataset(
        data_dir=ds_cfg.data_dir,
        split="train",
        split_ratio=ds_cfg.split_ratio,
        sample_rate=ds_cfg.sample_rate,
        min_duration=ds_cfg.min_duration_sec,
        max_duration=ds_cfg.max_duration_sec,
        seed=cfg.seed,
    )
    eval_ds = HFVoxCPMDataset(
        data_dir=ds_cfg.data_dir,
        split="eval",
        split_ratio=ds_cfg.split_ratio,
        sample_rate=ds_cfg.sample_rate,
        min_duration=ds_cfg.min_duration_sec,
        max_duration=ds_cfg.max_duration_sec,
        seed=cfg.seed,
    )
    train_stats = train_ds.stats()
    eval_stats = eval_ds.stats()
    logger.info(
        "数据集加载完成: train=%d, eval=%d, 跳过=%d",
        train_stats["total_count"],
        eval_stats["total_count"],
        train_stats["skipped_count"] + eval_stats["skipped_count"],
    )
    batch_proc = BatchProcessor(
        tokenizer=tokenizer,
        feature_extractor=feature_extractor,
        sample_rate=ds_cfg.sample_rate,
        max_length=1024,
    )

    def _collate(batch: list[DatasetEntry]) -> dict[str, torch.Tensor]:
        return batch_proc(batch)

    if accelerator is not None and hasattr(accelerator, "prepare_dataloader"):
        train_loader = accelerator.prepare_dataloader(
            train_ds,
            batch_size=int(cfg.batch_size),
            num_workers=num_workers,
            shuffle=True,
            collate_fn=_collate,
            drop_last=False,
        )
        eval_loader = accelerator.prepare_dataloader(
            eval_ds,
            batch_size=int(cfg.batch_size),
            num_workers=num_workers,
            shuffle=False,
            collate_fn=_collate,
            drop_last=False,
        )
    else:
        train_loader = DataLoader(
            train_ds,
            batch_size=int(cfg.batch_size),
            shuffle=True,
            num_workers=num_workers,
            collate_fn=_collate,
            drop_last=False,
            pin_memory=True,
        )
        eval_loader = DataLoader(
            eval_ds,
            batch_size=int(cfg.batch_size),
            shuffle=False,
            num_workers=num_workers,
            collate_fn=_collate,
            drop_last=False,
            pin_memory=True,
        )
    return train_loader, eval_loader


# ---------------------------------------------------------------------- #
# Legacy：基于 HuggingFace manifest 的工具函数（100% 保留）
# ---------------------------------------------------------------------- #
@argbind.bind()
def load_audio_text_datasets(
    train_manifest: str,
    val_manifest: str = "",
    text_column: str = DEFAULT_TEXT_COLUMN,
    audio_column: str = DEFAULT_AUDIO_COLUMN,
    dataset_id_column: str = DEFAULT_ID_COLUMN,
    sample_rate: int = 16_000,
    num_proc: int = 1,
) -> tuple[HFDataset, HFDataset | None]:
    """从 json manifest（HF datasets json 格式）加载 HuggingFace Dataset。

    Usage (与 minicpm-audio 一致)::

        train_ds, val_ds = load_audio_text_datasets("train.jsonl", "val.jsonl")

    Args:
        train_manifest: 训练集 jsonl 路径
        val_manifest: 可选验证集 jsonl 路径
        text_column: 文本列名
        audio_column: 音频列名
        dataset_id_column: 数据集 / speaker id 列名
        sample_rate: 目标采样率
        num_proc: HF datasets cast_column 多进程数

    Returns:
        (train_dataset, val_dataset_or_None)
    """
    data_files: dict[str, str] = {"train": train_manifest}
    if val_manifest:
        data_files["validation"] = val_manifest

    dataset_dict: DatasetDict = load_dataset("json", data_files=data_files)  # nosec B615 - 仅本地 jsonl manifest（data_files 为本地路径），不涉及 HF Hub 下载

    def prepare(ds: HFDataset) -> HFDataset:
        if audio_column not in ds.column_names:
            raise ValueError(f"Expected '{audio_column}' column in manifest.")
        ds = ds.cast_column(audio_column, Audio(sampling_rate=sample_rate))
        if audio_column != DEFAULT_AUDIO_COLUMN:
            ds = ds.rename_column(audio_column, DEFAULT_AUDIO_COLUMN)
        if text_column != DEFAULT_TEXT_COLUMN:
            ds = ds.rename_column(text_column, DEFAULT_TEXT_COLUMN)
        if dataset_id_column and dataset_id_column in ds.column_names:
            if dataset_id_column != DEFAULT_ID_COLUMN:
                ds = ds.rename_column(dataset_id_column, DEFAULT_ID_COLUMN)
        else:
            ds = ds.add_column(DEFAULT_ID_COLUMN, [0] * len(ds))
        return ds

    train_ds = prepare(dataset_dict["train"])
    val_ds = prepare(dataset_dict["validation"]) if "validation" in dataset_dict else None
    return train_ds, val_ds


def compute_sample_lengths(
    ds: HFDataset,
    audio_vae_fps: int = 25,
    patch_size: int = 1,
) -> list[int]:
    """预估每个样本经 packer 之后的序列总长度（text+audio）。

    用于 packer 的 max_batch_tokens / 过滤超长样本；算法与
    ``AudioFeatureProcessingPacker`` / AudioVAE 对齐：
      text_len + ceil(duration * audio_vae_fps / patch_size) + 2

    Args:
        ds: 包含 ``text_ids`` 列（必要）与可选 ``duration`` 列的 HF Dataset
        audio_vae_fps: AudioVAE 每秒帧数（默认 25，对应 hop 40ms at 24kHz）
        patch_size: VoxCPM patch 合并数（默认 1）

    Returns:
        每个样本的预估总 token 长度列表
    """
    text_ids_list = ds["text_ids"]
    text_lens = [len(t) for t in text_ids_list]

    has_duration = "duration" in ds.column_names
    durations: list[float]
    if has_duration:
        durations_raw = ds["duration"]
        durations = [float(d) if d is not None else 0.0 for d in durations_raw]
    else:
        durations = []
        for i in range(len(ds)):
            try:
                audio = ds[i][DEFAULT_AUDIO_COLUMN]
                sr = int(audio.get("sampling_rate", 16000))
                arr = audio.get("array")
                if arr is not None and hasattr(arr, "__len__") and sr:
                    durations.append(float(len(arr)) / float(sr))
                else:
                    durations.append(0.0)
            except Exception as e:  # noqa: BLE001
                logger.debug("compute_sample_lengths 读音频失败 i=%d: %s", i, e)
                durations.append(0.0)

    lengths: list[int] = []
    for text_len, duration in zip(text_lens, durations):
        t_vae = math.ceil(float(duration) * audio_vae_fps)
        t_seq = math.ceil(t_vae / max(1, patch_size))
        total_len = int(text_len) + int(t_seq) + 2
        lengths.append(total_len)
    return lengths


def build_dataloader(
    hf_dataset: HFDataset,
    *,
    accelerator: Any,
    batch_size: int,
    num_workers: int,
    drop_last: bool = False,
) -> DataLoader:
    """Legacy：把 HuggingFace Dataset 包成 DataLoader（Accelerator 会加 DistributedSampler）。

    保留给现有 train_voxcpm_finetune.py 使用；新代码推荐使用 ``create_dataloaders``。

    Args:
        hf_dataset: HuggingFace Dataset（已 tokenize 好）
        accelerator: 含 prepare_dataloader 方法的 Accelerator
        batch_size: 每卡 batch 大小
        num_workers: DataLoader worker 数
        drop_last: 是否丢弃最后一个不完整 batch

    Returns:
        准备好的 DataLoader
    """
    torch_dataset = HFVoxCPMDataset(hf_dataset)
    return accelerator.prepare_dataloader(
        torch_dataset,
        batch_size=batch_size,
        num_workers=num_workers,
        shuffle=True,
        collate_fn=HFVoxCPMDataset.collate_fn,
        drop_last=drop_last,
    )
