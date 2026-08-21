#!/usr/bin/env python3
# ruff: noqa: E402
"""
TTS MultiModel - VoxCPM LoRA 微调训练脚本
==========================================

项目名称: TTS MultiModel (多引擎语音合成平台)
主要功能: 对 VoxCPM/VoxCPM2 模型进行 LoRA 微调或全参数微调训练
核心技术栈: PyTorch + Transformers + Accelerate + TensorBoard + argbind

支持的训练模式:
    1. LoRA 微调 - 低秩适配，仅训练少量参数，显存占用低，适合定制音色
    2. 全参数微调 - 更新所有权重（AudioVAE 除外），效果更好但需要更多显存

训练特性:
    - 自动检测 VoxCPM1/VoxCPM2 架构
    - 混合精度训练（bfloat16）加速训练并节省显存
    - 梯度累积支持大 batch size 训练
    - 余弦学习率调度 + Warmup
    - 梯度裁剪防止梯度爆炸
    - 自动断点续训（latest checkpoint）
    - 信号处理优雅保存（SIGINT/SIGTERM）
    - TensorBoard 日志记录（loss、lr、梯度范数、音频样本）
    - 定期验证和样本音频生成
    - safetensors 格式权重保存（更安全快速）
    - 多 GPU 分布式训练支持

数据格式:
    使用 HuggingFace Datasets 格式，需要 manifest 文件指向音频和文本数据。
    支持音频长度过滤避免 OOM。

使用方法:
    方式 1: 使用 YAML 配置文件
        python scripts/train_voxcpm_finetune.py --config_path=configs/lora_config.yaml

    方式 2: 命令行参数
        python scripts/train_voxcpm_finetune.py \
            --pretrained_path=model/VoxCPM2 \
            --train_manifest=data/train_manifest.json \
            --val_manifest=data/val_manifest.json \
            --lora.rank=8 \
            --batch_size=2 \
            --learning_rate=1e-4 \
            --num_iters=10000

依赖要求:
    pip install torch transformers accelerate tensorboardX argbind safetensors librosa matplotlib

输出目录结构:
    checkpoints/
    ├── latest/                    # 最新检查点软链接/副本
    │   ├── lora_weights.safetensors  # LoRA 权重（LoRA 模式）
    │   ├── model.safetensors         # 模型权重（全参数模式）
    │   ├── optimizer.pth             # 优化器状态
    │   ├── scheduler.pth             # 学习率调度器状态
    │   ├── training_state.json       # 训练状态（当前步数）
    │   └── lora_config.json          # LoRA 配置（LoRA 模式）
    ├── step_0001000/             # 每 save_interval 步保存的检查点
    └── logs/                     # TensorBoard 日志目录

硬件建议:
    - LoRA 微调: 8GB+ VRAM（batch_size=1, grad_accum_steps=4）
    - 全参数微调: 16GB+ VRAM
    - 推荐 NVIDIA GPU（支持 CUDA 和 bfloat16）
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

import contextlib
import os
import signal

import argbind
import torch
from tensorboardX import SummaryWriter
from torch.optim import AdamW
from transformers import get_cosine_schedule_with_warmup

os.environ["TOKENIZERS_PARALLELISM"] = "false"

try:
    from safetensors.torch import save_file

    SAFETENSORS_AVAILABLE = True
except ImportError:
    SAFETENSORS_AVAILABLE = False
    print("Warning: safetensors not available, will use pytorch format", file=sys.stderr)

import json

from voxcpm.model import VoxCPM2Model, VoxCPMModel
from voxcpm.model.voxcpm import LoRAConfig as LoRAConfigV1
from voxcpm.model.voxcpm2 import LoRAConfig as LoRAConfigV2
from voxcpm.training import (
    Accelerator,
    BatchProcessor,
    TrainingTracker,
    build_dataloader,
    load_audio_text_datasets,
)


@argbind.bind(without_prefix=True)
def train(
    pretrained_path: str,
    train_manifest: str,
    val_manifest: str = "",
    sample_rate: int = 16_000,
    out_sample_rate: int = 0,  # AudioVAE decoder output rate; used for TensorBoard audio logging
    batch_size: int = 1,
    grad_accum_steps: int = 1,
    num_workers: int = 2,
    num_iters: int = 100_000,
    log_interval: int = 100,
    valid_interval: int = 1_000,
    save_interval: int = 10_000,
    learning_rate: float = 1e-4,
    weight_decay: float = 1e-2,
    warmup_steps: int = 1_000,
    max_steps: int = 100_000,
    max_batch_tokens: int = 0,
    save_path: str = "checkpoints",
    tensorboard: str = "",
    lambdas: dict[str, float] | None = None,
    lora: dict = None,
    config_path: str = "",
    max_grad_norm: float = 0.0,  # gradient clipping; 0 = disabled (backward compat)
    # Distribution options (for LoRA checkpoints)
    hf_model_id: str = "",  # HuggingFace model ID (e.g., "openbmb/VoxCPM1.5")
    distribute: bool = False,  # If True, save hf_model_id as base_model; otherwise save pretrained_path
):
    """
    VoxCPM 模型训练主函数

    功能说明:
        执行完整的模型训练流程，包括数据加载、模型初始化、训练循环、验证、
        检查点保存和日志记录。支持 LoRA 微调和全参数微调两种模式，自动检测
        VoxCPM1/VoxCPM2 模型架构，支持多 GPU 分布式训练。

    Args:
        pretrained_path: 预训练模型路径，包含 config.json、tokenizer 等文件
        train_manifest: 训练数据 manifest 文件路径（JSON 格式）
        val_manifest: 验证数据 manifest 文件路径，为空则不进行验证
        sample_rate: 音频采样率，必须与模型 AudioVAE 编码器的采样率匹配（默认 16000）
        out_sample_rate: 输出音频采样率（用于 TensorBoard 音频记录），0 表示使用模型默认值
        batch_size: 每个 micro-batch 的样本数量，显存不足时减小此值
        grad_accum_steps: 梯度累积步数，等效 batch_size = batch_size * grad_accum_steps
        num_workers: DataLoader 数据加载的工作线程数
        num_iters: 总训练迭代步数
        log_interval: 日志记录间隔（步数），每 N 步记录一次 loss 和 lr
        valid_interval: 验证间隔（步数），每 N 步执行一次验证并生成样本音频
        save_interval: 检查点保存间隔（步数），每 N 步保存一次模型
        learning_rate: 初始学习率，LoRA 微调建议 1e-4，全参数微调建议更小值
        weight_decay: AdamW 权重衰减系数
        warmup_steps: 学习率 Warmup 步数，在这些步内学习率线性增加到初始值
        max_steps: 最大训练步数，若 > 0 则覆盖 num_iters
        max_batch_tokens: 单 batch 最大 token 数限制，用于过滤过长样本防止 OOM，0 表示不限制
        save_path: 检查点保存目录路径
        tensorboard: TensorBoard 日志目录，为空则使用 {save_path}/logs
        lambdas: 损失函数字典，键为 "loss/diff"、"loss/stop" 等，值为对应的权重
        lora: LoRA 配置字典，包含 rank、alpha 等参数；为空则执行全参数微调
        config_path: YAML 配置文件路径（由 argbind 自动处理，一般不需手动传）
        max_grad_norm: 梯度裁剪最大范数，0 表示禁用裁剪
        hf_model_id: HuggingFace 模型 ID（如 "openbmb/VoxCPM1.5"），用于分发 LoRA 权重
        distribute: 是否保存 hf_model_id 作为基础模型引用（用于 LoRA 分享）

    训练流程:
        1. 初始化 Accelerator（支持分布式训练和混合精度）
        2. 创建保存目录和 TensorBoard writer
        3. 自动检测模型架构（VoxCPM1/VoxCPM2）并加载预训练模型
        4. 加载和预处理训练/验证数据集（文本 tokenization）
        5. 可选：按 token 长度过滤过长样本防止 OOM
        6. 构建 DataLoader 和 BatchProcessor
        7. 初始化优化器（AdamW）和学习率调度器（余弦退火+Warmup）
        8. 加载最新检查点实现断点续训
        9. 注册 SIGINT/SIGTERM 信号处理器，异常终止时保存检查点
        10. 进入训练循环：
            - 梯度累积前向/反向传播
            - 梯度裁剪
            - 优化器步进和学习率更新
            - 定期记录日志、验证、保存检查点
        11. 训练结束保存最终检查点，关闭 TensorBoard

    LoRA 配置示例:
        lora = {
            "rank": 8,           # LoRA 秩，越大效果越好但参数越多
            "alpha": 16,         # LoRA alpha 缩放因子
            "dropout": 0.05,     # LoRA dropout 率
            "target_modules": [...]  # 目标注入模块
        }

    注意事项:
        - 训练前确保预训练模型完整，sample_rate 与模型匹配
        - 首次训练建议使用小数据集测试流程是否正常
        - 使用 Ctrl+C 可安全中断训练，自动保存当前进度
        - 训练日志可通过 TensorBoard 查看: tensorboard --logdir=checkpoints/logs
    """
    if lambdas is None:
        lambdas = {"loss/diff": 1.0, "loss/stop": 1.0}
    _ = config_path

    # Validate distribution options
    if lora is not None and distribute and not hf_model_id:
        raise ValueError("hf_model_id is required when distribute=True")

    accelerator = Accelerator(amp=True)

    save_dir = Path(save_path)
    tb_dir = Path(tensorboard) if tensorboard else save_dir / "logs"

    # Only create directories on rank 0 to avoid race conditions
    if accelerator.rank == 0:
        save_dir.mkdir(parents=True, exist_ok=True)
        tb_dir.mkdir(parents=True, exist_ok=True)
    accelerator.barrier()  # Wait for directory creation

    writer = SummaryWriter(log_dir=str(tb_dir)) if accelerator.rank == 0 else None
    tracker = TrainingTracker(writer=writer, log_file=str(save_dir / "train.log"), rank=accelerator.rank)

    # Auto-detect model architecture from config.json
    with open(os.path.join(pretrained_path, "config.json"), encoding="utf-8") as _f:
        _arch = json.load(_f).get("architecture", "voxcpm").lower()
    _model_cls = VoxCPM2Model if _arch == "voxcpm2" else VoxCPMModel
    LoRAConfig = LoRAConfigV2 if _arch == "voxcpm2" else LoRAConfigV1
    if accelerator.rank == 0:
        print(f"Detected architecture: {_arch} -> {_model_cls.__name__}", file=sys.stderr)
    base_model = _model_cls.from_local(
        pretrained_path, optimize=False, training=True, lora_config=LoRAConfig(**lora) if lora else None
    )
    tokenizer = base_model.text_tokenizer

    expected_sr = base_model.audio_vae.sample_rate
    assert sample_rate == expected_sr, (
        f"sample_rate mismatch: config says {sample_rate}, but the AudioVAE encoder expects {expected_sr}. "
        f"Please set sample_rate: {expected_sr} in your training config. "
    )

    train_ds, val_ds = load_audio_text_datasets(
        train_manifest=train_manifest,
        val_manifest=val_manifest,
        sample_rate=sample_rate,
    )

    def tokenize(batch):
        """文本分词批处理函数，用于 datasets.map() 的 batched 模式。

        将批次中的文本列表使用模型的 tokenizer 进行分词，转换为 token ID 序列。
        这是 HuggingFace datasets 库 map 方法要求的批处理函数格式。

        Args:
            batch: 数据集批次字典，必须包含 "text" 键，值为文本字符串列表。

        Returns:
            dict: 包含 "text_ids" 键的字典，值为分词后的 token ID 列表的列表。
        """
        text_list = batch["text"]
        text_ids = [tokenizer(text) for text in text_list]
        return {"text_ids": text_ids}

    train_ds = train_ds.map(tokenize, batched=True, remove_columns=["text"])
    # Save original validation texts for audio generation display
    val_texts = None
    if val_ds is not None:
        val_texts = list(val_ds["text"])  # Save original texts
        val_ds = val_ds.map(tokenize, batched=True, remove_columns=["text"])

    dataset_cnt = int(max(train_ds["dataset_id"])) + 1 if "dataset_id" in train_ds.column_names else 1
    num_train_samples = len(train_ds)

    # ------------------------------------------------------------------ #
    # Optional: filter samples by estimated token count to avoid OOM
    # Enabled when max_batch_tokens > 0:
    #   max_sample_len = max_batch_tokens // batch_size
    #   Samples exceeding this length will be dropped
    # ------------------------------------------------------------------ #
    if max_batch_tokens and max_batch_tokens > 0:
        from voxcpm.training.data import compute_sample_lengths

        audio_vae_fps = base_model.audio_vae.sample_rate / base_model.audio_vae.hop_length
        est_lengths = compute_sample_lengths(
            train_ds,
            audio_vae_fps=audio_vae_fps,
            patch_size=base_model.config.patch_size,
        )
        max_sample_len = max_batch_tokens // batch_size if batch_size > 0 else max(est_lengths)
        keep_indices = [i for i, L in enumerate(est_lengths) if max_sample_len >= L]

        if len(keep_indices) < len(train_ds) and accelerator.rank == 0:
            tracker.print(
                f"Filtering {len(train_ds) - len(keep_indices)} / {len(train_ds)} "
                f"training samples longer than {max_sample_len} tokens "
                f"(max_batch_tokens={max_batch_tokens})."
            )
        train_ds = train_ds.select(keep_indices)

    train_loader = build_dataloader(
        train_ds,
        accelerator=accelerator,
        batch_size=batch_size,
        num_workers=num_workers,
        drop_last=True,
    )
    val_loader = (
        build_dataloader(
            val_ds,
            accelerator=accelerator,
            batch_size=batch_size,
            num_workers=num_workers,
            drop_last=False,
        )
        if val_ds is not None
        else None
    )

    batch_processor = BatchProcessor(
        config=base_model.config,
        audio_vae=base_model.audio_vae,
        dataset_cnt=dataset_cnt,
        device=accelerator.device,
    )
    # Save audio_vae and output sample rate for audio generation.
    # Prefer model's actual output rate; fall back to YAML out_sample_rate or encode rate.
    audio_vae_for_gen = base_model.audio_vae
    out_sr = base_model.sample_rate  # decoder output rate (e.g. 48000 for V2)
    if out_sr == 0 and out_sample_rate > 0:
        out_sr = out_sample_rate
    del base_model.audio_vae
    model = accelerator.prepare_model(base_model)
    unwrapped_model = accelerator.unwrap(model)
    unwrapped_model.train()

    # Only print param info on rank 0 to avoid cluttered output
    if accelerator.rank == 0:
        for name, param in model.named_parameters():
            print(name, param.requires_grad, file=sys.stderr)

    optimizer = AdamW(
        (p for p in model.parameters() if p.requires_grad),
        lr=learning_rate,
        weight_decay=weight_decay,
    )

    # Cosine + warmup scheduler from transformers:
    # - num_warmup_steps: warmup steps
    # - num_training_steps: total training steps (outer step count)
    total_training_steps = max_steps if max_steps > 0 else num_iters
    scheduler = get_cosine_schedule_with_warmup(
        optimizer,
        num_warmup_steps=warmup_steps,
        num_training_steps=total_training_steps,
    )

    # All ranks load the same checkpoint to keep model and optimizer state in sync.
    start_step = load_checkpoint(model, optimizer, scheduler, save_dir, rank=accelerator.rank)
    accelerator.barrier()

    if start_step > 0 and accelerator.rank == 0:
        tracker.print(f"Resuming training from step {start_step}")

    # Resume tracker for signal handler to read current step
    resume = {"step": start_step}

    # Register signal handler to save checkpoint on termination (SIGTERM/SIGINT)
    def _signal_handler(
        signum,
        frame,
        _model=model,
        _optim=optimizer,
        _sched=scheduler,
        _save_dir=save_dir,
        _pretrained=pretrained_path,
        _hf_id=hf_model_id,
        _dist=distribute,
        _resume=resume,
        _rank=accelerator.rank,
    ):
        """信号处理函数：在收到终止信号时保存检查点并优雅退出。

        捕获 SIGTERM 和 SIGINT 信号（如 Ctrl+C、kill 命令、容器停止等），
        在进程终止前自动保存当前训练进度的检查点，避免训练成果丢失。
        仅在 rank 0（主进程）执行保存操作，其他进程直接退出。

        Args:
            signum: 信号编号（SIGTERM=15, SIGINT=2）。
            frame: 当前栈帧对象（未使用，但 signal 模块要求此参数）。
            _model: 模型对象（通过默认参数捕获闭包变量，避免延迟绑定问题）。
            _optim: 优化器对象。
            _sched: 学习率调度器对象。
            _save_dir: 检查点保存目录。
            _pretrained: 预训练模型路径。
            _hf_id: HuggingFace 模型 ID（用于 LoRA 合并导出）。
            _dist: 是否为分布式训练模式。
            _resume: 包含当前训练步数的字典引用，用于获取最新 step。
            _rank: 当前进程的分布式 rank。
        """
        try:
            cur_step = int(_resume.get("step", start_step))
        except Exception:
            cur_step = start_step
        if _rank == 0:
            print(f"Signal {signum} received. Saving checkpoint at step {cur_step} ...", file=sys.stderr)
            try:
                save_checkpoint(_model, _optim, _sched, _save_dir, cur_step, _pretrained, _hf_id, _dist)
                print("Checkpoint saved. Exiting.", file=sys.stderr)
            except Exception as e:
                print(f"Error saving checkpoint on signal: {e}", file=sys.stderr)
        os._exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # Manual epoch management instead of itertools.cycle to support DistributedSampler.set_epoch()
    grad_accum_steps = max(int(grad_accum_steps), 1)
    data_epoch = 0
    train_iter = iter(train_loader)

    def get_next_batch():
        """
        获取下一个训练 batch，处理 epoch 边界和分布式采样器

        功能说明:
            从训练数据迭代器获取下一个 batch。当迭代器耗尽（一个 epoch 结束）时，
            自动重置迭代器，并在分布式训练模式下调用 DistributedSampler.set_epoch()
            确保每个 epoch 的数据顺序不同（避免数据重复问题）。

        Returns:
            dict: 包含 text_tokens、audio_feats 等字段的训练 batch 字典
        """
        nonlocal train_iter, data_epoch
        try:
            return next(train_iter)
        except StopIteration:
            data_epoch += 1
            # Key: set DistributedSampler epoch to ensure different data order each epoch
            sampler = getattr(train_loader, "sampler", None)
            if hasattr(sampler, "set_epoch"):
                sampler.set_epoch(data_epoch)
            train_iter = iter(train_loader)
            return next(train_iter)

    with tracker.live():
        for step in range(start_step, num_iters):
            # update resume step so signal handler can save current progress
            resume["step"] = step
            tracker.step = step
            optimizer.zero_grad(set_to_none=True)

            # Gradient accumulation: accumulate gradients over micro-batches before optimizer step
            loss_dict = {}
            for micro_step in range(grad_accum_steps):
                batch = get_next_batch()
                processed = batch_processor(batch)

                # Only sync gradients on the last micro-batch
                # Use no_sync() for intermediate steps to reduce communication overhead
                is_last_micro_step = micro_step == grad_accum_steps - 1
                sync_context = contextlib.nullcontext() if is_last_micro_step else accelerator.no_sync()

                with sync_context:
                    with accelerator.autocast(dtype=torch.bfloat16):
                        outputs = model(
                            processed["text_tokens"],
                            processed["text_mask"],
                            processed["audio_feats"],
                            processed["audio_mask"],
                            processed["loss_mask"],
                            processed["position_ids"],
                            processed["labels"],
                            progress=step / max(1, num_iters),
                        )

                    total_loss = 0.0
                    for key, value in outputs.items():
                        if key.startswith("loss/"):
                            weight = lambdas.get(key, 1.0)
                            loss_value = value * weight / grad_accum_steps
                            total_loss = total_loss + loss_value
                            # Record raw loss from last micro-batch for logging
                            loss_dict[key] = value.detach()

                    # Accumulate gradients (normalized by grad_accum_steps)
                    accelerator.backward(total_loss)

            # After all micro-batches, do unscale / grad_norm / step
            scaler = getattr(accelerator, "scaler", None)
            if scaler is not None:
                scaler.unscale_(optimizer)
            effective_max_norm = max_grad_norm if max_grad_norm > 0 else 1e9
            grad_norm = torch.nn.utils.clip_grad_norm_(unwrapped_model.parameters(), max_norm=effective_max_norm)

            accelerator.step(optimizer)
            accelerator.update()
            scheduler.step()

            if step % log_interval == 0 or step == num_iters - 1:
                loss_values = {k: v.item() if isinstance(v, torch.Tensor) else float(v) for k, v in loss_dict.items()}
                loss_values["lr"] = float(optimizer.param_groups[0]["lr"])
                # Account for all GPUs when converting steps to epochs.
                epoch = (step * grad_accum_steps * batch_size * accelerator.world_size) / max(1, num_train_samples)
                loss_values["epoch"] = float(epoch)
                loss_values["grad_norm"] = float(grad_norm)
                tracker.log_metrics(loss_values, split="train")

            if val_loader is not None and (step % valid_interval == 0 or step == num_iters - 1):
                validate(
                    model,
                    val_loader,
                    batch_processor,
                    accelerator,
                    tracker,
                    lambdas,
                    writer=writer,
                    step=step,
                    val_ds=val_ds,
                    audio_vae=audio_vae_for_gen,
                    sample_rate=sample_rate,
                    out_sample_rate=out_sr,
                    val_texts=val_texts,
                    tokenizer=tokenizer,
                    valid_interval=valid_interval,
                )

            if (step % save_interval == 0 or step == num_iters - 1) and accelerator.rank == 0:
                save_checkpoint(model, optimizer, scheduler, save_dir, step, pretrained_path, hf_model_id, distribute)

    if accelerator.rank == 0:
        save_checkpoint(model, optimizer, scheduler, save_dir, num_iters, pretrained_path, hf_model_id, distribute)
    if writer:
        writer.close()


def validate(
    model,
    val_loader,
    batch_processor,
    accelerator,
    tracker,
    lambdas,
    writer=None,
    step=0,
    val_ds=None,
    audio_vae=None,
    sample_rate=22050,
    out_sample_rate=0,
    val_texts=None,
    tokenizer=None,
    valid_interval=1000,
):
    """
    模型验证函数：计算验证集损失并生成样本音频

    功能说明:
        在验证集上评估模型性能，计算各项损失指标（扩散损失、停止损失等），
        并可选地生成样本音频和 Mel 频谱图记录到 TensorBoard，用于监控训练效果。
        最多验证 10 个 batch 以控制验证时间。

    Args:
        model: 待验证的模型对象
        val_loader: 验证数据 DataLoader
        batch_processor: BatchProcessor 实例，用于处理原始数据为模型输入
        accelerator: Accelerator 实例，用于分布式训练和混合精度
        tracker: TrainingTracker 实例，用于日志记录
        lambdas: 损失权重字典，与训练时保持一致
        writer: TensorBoard SummaryWriter 实例，None 表示不记录音频和图表
        step: 当前训练步数，用于日志标记
        val_ds: 验证数据集（原始数据集，用于提取参考音频）
        audio_vae: AudioVAE 实例，用于音频解码生成
        sample_rate: 音频输入采样率（编码器侧）
        out_sample_rate: 音频输出采样率（解码器侧），0 表示使用 sample_rate
        val_texts: 验证集原始文本列表，用于样本生成
        tokenizer: 文本分词器
        valid_interval: 验证间隔步数（仅用于日志参考）

    验证流程:
        1. 切换模型到 eval 模式
        2. 遍历验证集（最多 10 个 batch），计算各项损失
        3. 分布式环境下聚合所有 GPU 的损失指标
        4. 通过 tracker 记录验证损失
        5. 可选：生成 2 个样本音频，记录到 TensorBoard
        6. 可选：生成参考音频和生成音频的对比 Mel 频谱图
        7. 恢复模型到 train 模式

    注意事项:
        - 验证在 torch.no_grad() 下进行，不计算梯度
        - 使用 bfloat16 混合精度加速验证
        - 音频生成失败不影响验证流程，仅打印警告
    """
    from collections import defaultdict

    import numpy as np  # noqa: F401

    model.eval()
    total_losses = []
    sub_losses = defaultdict(list)  # Track individual sub-losses
    num_batches = 0
    max_val_batches = 10

    with torch.no_grad():
        for batch in val_loader:
            if num_batches >= max_val_batches:
                break
            processed = batch_processor(batch)
            with accelerator.autocast(dtype=torch.bfloat16):
                outputs = model(
                    processed["text_tokens"],
                    processed["text_mask"],
                    processed["audio_feats"],
                    processed["audio_mask"],
                    processed["loss_mask"],
                    processed["position_ids"],
                    processed["labels"],
                    progress=0.0,
                    sample_generate=False,
                )
            total = 0.0
            for key, value in outputs.items():
                if key.startswith("loss/"):
                    weighted_loss = lambdas.get(key, 1.0) * value
                    total += weighted_loss
                    sub_losses[key].append(value.detach())
            total_losses.append(total.detach())
            num_batches += 1

    if total_losses:
        # Compute mean total loss
        mean_total_loss = torch.stack(total_losses).mean()
        accelerator.all_reduce(mean_total_loss)

        # Compute mean of each sub-loss
        val_metrics = {"loss/total": mean_total_loss.item()}
        for key, values in sub_losses.items():
            mean_sub_loss = torch.stack(values).mean()
            accelerator.all_reduce(mean_sub_loss)
            val_metrics[key] = mean_sub_loss.item()

        tracker.log_metrics(val_metrics, split="val")

    # Generate sample audio for TensorBoard display
    if writer is not None and val_ds is not None and audio_vae is not None and accelerator.rank == 0:
        try:
            generate_sample_audio(
                model,
                val_ds,
                audio_vae,
                writer,
                step,
                accelerator,
                sample_rate,
                out_sample_rate=out_sample_rate,
                val_texts=val_texts,
                tokenizer=tokenizer,
                valid_interval=valid_interval,
                tracker=tracker,
            )
        except Exception as e:
            tracker.print(f"[Warning] Failed to generate sample audio: {e}")
            import io
            import traceback

            buf = io.StringIO()
            traceback.print_exc(file=buf)
            tracker.print(buf.getvalue())
    else:
        # Log why audio generation was skipped
        missing = []
        if writer is None:
            missing.append("writer")
        if val_ds is None:
            missing.append("val_ds")
        if audio_vae is None:
            missing.append("audio_vae")
        if missing and accelerator.rank == 0:
            tracker.print(f"[Warning] Skip audio generation: missing {', '.join(missing)}")

    model.train()


def compute_mel_spectrogram(audio_np, sample_rate, n_mels=128):
    """
    使用 librosa 计算 Mel 频谱图（dB 刻度）

    功能说明:
        将原始音频波形转换为 Mel 频谱图，并转换为分贝刻度用于可视化。
        Mel 频谱图反映了人耳对不同频率的感知特性，适合用于语音质量评估。

    Args:
        audio_np: numpy 数组格式的音频波形数据，形状为 (n_samples,)
        sample_rate: 音频采样率
        n_mels: Mel 频带数量，默认 128

    Returns:
        numpy.ndarray: Mel 频谱图（dB 刻度），形状为 (n_mels, n_frames)
    """
    import librosa
    import numpy as np

    audio_np = audio_np.flatten().astype(np.float32)
    mel = librosa.feature.melspectrogram(y=audio_np, sr=sample_rate, n_mels=n_mels, fmax=sample_rate // 2)
    return librosa.power_to_db(mel, ref=np.max)


def create_mel_figure(gen_audio_np, gen_mel, sample_rate, step=None, ref_audio_np=None, ref_mel=None):
    """
    创建 Mel 频谱图对比可视化图表

    功能说明:
        使用 matplotlib 生成 Mel 频谱图。如果提供了参考音频，则生成上下对比图：
        上方为参考音频（Ground Truth，绿色标题），下方为生成音频（红色标题）。
        如果没有参考音频，则只显示生成音频的频谱图。图表使用 Agg 后端，
        无需 GUI 环境即可运行，适合服务器端 TensorBoard 记录。

    Args:
        gen_audio_np: 生成的音频波形 numpy 数组
        gen_mel: 生成音频的 Mel 频谱图
        sample_rate: 音频采样率
        step: 当前训练步数，显示在标题中（可选）
        ref_audio_np: 参考音频波形 numpy 数组（可选，用于对比模式）
        ref_mel: 参考音频的 Mel 频谱图（可选，用于对比模式）

    Returns:
        matplotlib.figure.Figure: 生成的图表对象，可直接写入 TensorBoard

    图表布局:
        - 对比模式: 2行1列子图，上参考、下生成，附带颜色条
        - 单图模式: 单个子图，显示生成音频频谱图
        - 时间轴以秒为单位，频率轴为 Mel 刻度
    """
    import matplotlib

    matplotlib.use("Agg")
    import librosa.display
    import matplotlib.pyplot as plt

    fmax = sample_rate // 2
    step_str = f" @ Step {step}" if step is not None else ""

    if ref_audio_np is not None and ref_mel is not None:
        # Comparison mode: reference vs generated
        fig, (ax_ref, ax_gen) = plt.subplots(2, 1, figsize=(12, 8))

        img_ref = librosa.display.specshow(
            ref_mel, sr=sample_rate, x_axis="time", y_axis="mel", fmax=fmax, cmap="viridis", ax=ax_ref
        )
        ax_ref.set_title(
            f"Reference (GT) - {len(ref_audio_np) / sample_rate:.2f}s{step_str}",
            fontsize=10,
            fontweight="bold",
            color="#28A745",
        )
        plt.colorbar(img_ref, ax=ax_ref, format="%+2.0f dB", pad=0.02)

        img_gen = librosa.display.specshow(
            gen_mel, sr=sample_rate, x_axis="time", y_axis="mel", fmax=fmax, cmap="viridis", ax=ax_gen
        )
        ax_gen.set_title(
            f"Generated - {len(gen_audio_np) / sample_rate:.2f}s", fontsize=10, fontweight="bold", color="#DC3545"
        )
        plt.colorbar(img_gen, ax=ax_gen, format="%+2.0f dB", pad=0.02)
    else:
        # Single figure mode: show generated only
        fig, ax = plt.subplots(figsize=(12, 4))
        img = librosa.display.specshow(
            gen_mel, sr=sample_rate, x_axis="time", y_axis="mel", fmax=fmax, cmap="viridis", ax=ax
        )
        ax.set_title(f"Generated - {len(gen_audio_np) / sample_rate:.2f}s{step_str}", fontsize=11, fontweight="bold")
        plt.colorbar(img, ax=ax, format="%+2.0f dB", pad=0.02)

    plt.tight_layout()
    return fig


def normalize_audio(audio_np):
    """
    音频响度归一化到 [-0.9, 0.9] 范围

    功能说明:
        将音频波形的峰值归一化到 0.9，避免削波（clipping）失真，
        同时保留动态范围。归一化后的音频适合播放和 TensorBoard 记录。

    Args:
        audio_np: numpy 数组格式的音频波形

    Returns:
        numpy.ndarray: 归一化后的音频波形，幅值范围在 [-0.9, 0.9]
        注意：静音音频（全零）直接返回，不做处理避免除零错误
    """
    import numpy as np

    max_val = np.abs(audio_np).max()
    return audio_np / max_val * 0.9 if max_val > 0 else audio_np


def generate_sample_audio(
    model,
    val_ds,
    audio_vae,
    writer,
    step,
    accelerator,
    sample_rate=22050,
    out_sample_rate=0,
    val_texts=None,
    tokenizer=None,
    pretrained_path=None,
    valid_interval=1000,
    tracker=None,
):
    """
    生成验证样本音频并记录到 TensorBoard

    功能说明:
        从验证集中选取固定的 2 个样本，使用当前模型生成语音，
        并将生成音频、参考音频、Mel 频谱对比图记录到 TensorBoard，
        用于直观监控训练过程中音频质量的变化。

    Args:
        model: 当前训练的模型（会临时切换到 eval 模式）
        val_ds: 验证数据集，用于提取参考音频
        audio_vae: AudioVAE 解码器，用于将潜变量解码为音频波形
        writer: TensorBoard SummaryWriter 实例
        step: 当前训练步数
        accelerator: Accelerator 实例
        sample_rate: 编码器输入采样率
        out_sample_rate: 解码器输出采样率，0 则使用 sample_rate
        val_texts: 验证集原始文本列表
        tokenizer: 文本分词器（保留参数，当前未使用）
        pretrained_path: 预训练模型路径（保留参数）
        valid_interval: 验证间隔（保留参数）
        tracker: TrainingTracker 实例，用于日志输出

    处理流程:
        1. 选择前 2 个验证样本
        2. 加载参考音频（如存在），必要时重采样
        3. 临时切换模型到 eval 模式，挂载 AudioVAE
        4. 使用 bfloat16 混合精度生成音频
        5. 归一化生成音频
        6. 记录生成音频和参考音频到 TensorBoard
        7. 计算并记录 Mel 频谱对比图
        8. 恢复模型到训练状态

    注意事项:
        - 所有异常都会被捕获，避免影响主训练流程
        - 生成完成后在 finally 块中确保恢复模型状态
        - 即使只有主进程（rank 0）执行音频生成
    """
    import numpy as np

    log = tracker.print if tracker else print
    num_samples = min(2, len(val_ds))
    log(f"[Audio] Starting audio generation for {num_samples} samples at step {step}")

    unwrapped_model = accelerator.unwrap(model)
    # Determine the correct output sample rate for generated audio.
    # out_sample_rate is the decoder output rate (e.g. 48kHz for V2);
    # sample_rate is the encoder input rate (e.g. 16kHz for V2).
    gen_sr = out_sample_rate if out_sample_rate > 0 else sample_rate

    for i in range(num_samples):
        sample = val_ds[i]
        text = val_texts[i] if val_texts and i < len(val_texts) else "Hello, this is a test."

        # Load reference audio
        ref_audio_np = None
        try:
            if "audio" in sample and isinstance(sample["audio"], dict) and "array" in sample["audio"]:
                ref_audio_np = np.array(sample["audio"]["array"], dtype=np.float32)
                ref_sr = sample["audio"].get("sampling_rate", sample_rate)
                if ref_sr != sample_rate:
                    import torchaudio.functional as F

                    ref_audio_np = (
                        F.resample(torch.from_numpy(ref_audio_np).unsqueeze(0), ref_sr, sample_rate).squeeze(0).numpy()
                    )
                log(f"[Audio] Loaded reference audio for sample {i}: duration={len(ref_audio_np) / sample_rate:.2f}s")
        except Exception as e:
            log(f"[Warning] Failed to load reference audio: {e}")

        # Preserve the original mode so validation failures do not leak into training.
        prev_training = unwrapped_model.training
        try:
            # Inference setup
            unwrapped_model.eval()
            # unwrapped_model.to(torch.bfloat16)
            unwrapped_model.audio_vae = audio_vae.to(torch.float32)

            log(f"[Audio] Generating sample {i} with text: '{text[:50]}...'")
            autocast_ctx = (
                torch.autocast(device_type="cuda", dtype=torch.bfloat16)
                if torch.cuda.is_available()
                else contextlib.nullcontext()
            )
            with torch.no_grad(), autocast_ctx:
                generated = unwrapped_model.generate(target_text=text, inference_timesteps=10, cfg_value=2.0)

            # Restore training setup
            # unwrapped_model.to(torch.float32)
            # unwrapped_model.audio_vae = None

            if generated is None or len(generated) == 0:
                log(f"[Warning] Generated audio is empty for sample {i}")
                continue

            # Process generated audio
            gen_audio_np = (
                generated.cpu().float().numpy().flatten()
                if isinstance(generated, torch.Tensor)
                else np.array(generated, dtype=np.float32).flatten()
            )
            gen_audio_np = normalize_audio(gen_audio_np)

            tag = f"val_sample_{i}"
            writer.add_audio(f"{tag}/generated_audio", gen_audio_np, global_step=step, sample_rate=gen_sr)
            log(f"[Audio] Generated audio for sample {i}: duration={len(gen_audio_np) / gen_sr:.2f}s")

            # Log reference audio (at encoder input rate, which is what val_ds provides)
            if ref_audio_np is not None:
                writer.add_audio(
                    f"{tag}/reference_audio", normalize_audio(ref_audio_np), global_step=step, sample_rate=sample_rate
                )

            # Generate mel spectrogram figure
            try:
                mel_gen = compute_mel_spectrogram(gen_audio_np, gen_sr)
                mel_ref = compute_mel_spectrogram(ref_audio_np, sample_rate) if ref_audio_np is not None else None
                fig = create_mel_figure(gen_audio_np, mel_gen, gen_sr, step, ref_audio_np, mel_ref)
                writer.add_figure(f"{tag}/mel_spectrogram", fig, global_step=step)
                log(f"[Audio] Created mel spectrogram figure for sample {i}")
            except Exception as e:
                log(f"[Warning] Failed to create mel spectrogram: {e}")

        except Exception as e:
            log(f"[Warning] Failed to generate audio for sample {i}: {e}")
            import traceback

            traceback.print_exc()

        finally:
            # Always restore the training state, even if generation fails.
            try:
                # unwrapped_model.to(torch.float32)
                unwrapped_model.audio_vae = None
                if prev_training:
                    unwrapped_model.train()
                else:
                    unwrapped_model.eval()
            except Exception as e:
                log(f"[Warning] Failed to restore model state: {e}")


def load_checkpoint(model, optimizer, scheduler, save_dir: Path, rank: int = 0):
    """
    加载最新检查点以恢复训练

    功能说明:
        从 save_dir 目录加载最新的训练检查点，支持断点续训。
        所有 rank（分布式进程）都会调用此函数以保持状态同步。
        支持 LoRA 权重和全模型权重两种格式，优先使用 safetensors。

    Args:
        model: 模型对象，加载权重到此模型
        optimizer: 优化器对象，加载优化器状态
        scheduler: 学习率调度器对象，加载调度器状态
        save_dir: 检查点保存目录路径
        rank: 当前进程的分布式 rank，仅 rank 0 打印日志

    Returns:
        int: 恢复训练的起始步数，0 表示未找到检查点（从头开始训练）

    加载顺序:
        1. 优先检查 latest/ 目录（最新检查点副本）
        2. LoRA 模式: 加载 lora_weights.safetensors 或 lora_weights.ckpt
        3. 全参数模式: 加载 model.safetensors 或 pytorch_model.bin
        4. 加载 optimizer.pth（优化器状态）
        5. 加载 scheduler.pth（调度器状态）
        6. 读取 training_state.json 获取步数
        7. 兼容旧格式: 查找 step_xxxxxx 目录取最大步数

    注意事项:
        - 使用 strict=False 加载权重，允许 LoRA 模式下缺失/多余的键
        - 权重默认加载到 CPU，避免 GPU 显存问题
        - 找不到检查点时静默返回 0，不报错
    """
    latest_folder = save_dir / "latest"
    if not latest_folder.exists():
        return 0

    unwrapped = model.module if hasattr(model, "module") else model
    lora_cfg = unwrapped.lora_config

    # Load model weights
    if lora_cfg is not None:
        # LoRA: load lora_weights
        lora_weights_path = latest_folder / "lora_weights.safetensors"
        if not lora_weights_path.exists():
            lora_weights_path = latest_folder / "lora_weights.ckpt"

        if lora_weights_path.exists():
            if lora_weights_path.suffix == ".safetensors":
                from safetensors.torch import load_file

                state_dict = load_file(str(lora_weights_path))
            else:
                ckpt = torch.load(lora_weights_path, map_location="cpu")
                state_dict = ckpt.get("state_dict", ckpt)

            unwrapped.load_state_dict(state_dict, strict=False)
            if rank == 0:
                print(f"Loaded LoRA weights from {lora_weights_path}", file=sys.stderr)
    else:
        # Full finetune: load model.safetensors or pytorch_model.bin
        model_path = latest_folder / "model.safetensors"
        if not model_path.exists():
            model_path = latest_folder / "pytorch_model.bin"

        if model_path.exists():
            if model_path.suffix == ".safetensors":
                from safetensors.torch import load_file

                state_dict = load_file(str(model_path))
            else:
                ckpt = torch.load(model_path, map_location="cpu")
                state_dict = ckpt.get("state_dict", ckpt)

            unwrapped.load_state_dict(state_dict, strict=False)
            if rank == 0:
                print(f"Loaded model weights from {model_path}", file=sys.stderr)

    # Load optimizer state
    optimizer_path = latest_folder / "optimizer.pth"
    if optimizer_path.exists():
        optimizer.load_state_dict(torch.load(optimizer_path, map_location="cpu"))
        if rank == 0:
            print(f"Loaded optimizer state from {optimizer_path}", file=sys.stderr)

    # Load scheduler state
    scheduler_path = latest_folder / "scheduler.pth"
    if scheduler_path.exists():
        scheduler.load_state_dict(torch.load(scheduler_path, map_location="cpu"))
        if rank == 0:
            print(f"Loaded scheduler state from {scheduler_path}", file=sys.stderr)

    state_path = latest_folder / "training_state.json"
    if state_path.exists():
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        resume_step = int(state.get("step", 0))
        if rank == 0:
            print(f"Resuming from step {resume_step}", file=sys.stderr)
        return resume_step

    # Fallback for older checkpoints without metadata.
    step_folders = [d for d in save_dir.iterdir() if d.is_dir() and d.name.startswith("step_")]
    if step_folders:
        steps = [int(d.name.split("_")[1]) for d in step_folders]
        resume_step = max(steps)
        if rank == 0:
            print(f"Resuming from step {resume_step}", file=sys.stderr)
        return resume_step

    return 0


def save_checkpoint(
    model,
    optimizer,
    scheduler,
    save_dir: Path,
    step: int,
    pretrained_path: str = None,
    hf_model_id: str = "",
    distribute: bool = False,
):
    """
    保存训练检查点

    功能说明:
        根据训练模式（LoRA 或全参数微调）采用不同策略保存检查点：
        - LoRA 微调: 仅保存 lora_A/lora_B 权重矩阵，体积小
        - 全参数微调: 保存除 AudioVAE 外的所有权重
        同时保存优化器状态、调度器状态和训练元数据，支持断点续训。
        保存完成后更新 latest/ 目录指向最新检查点。

    Args:
        model: 当前训练的模型对象
        optimizer: 优化器对象，保存其状态字典
        scheduler: 学习率调度器对象，保存其状态字典
        save_dir: 检查点根目录
        step: 当前训练步数，用于命名检查点目录
        pretrained_path: 预训练模型路径（全参数微调时用于复制配置文件）
        hf_model_id: HuggingFace 模型 ID（LoRA 分发模式时保存引用）
        distribute: 是否使用分发模式（True 时保存 hf_model_id 而非本地路径）

    保存内容:
        LoRA 模式:
            - lora_weights.safetensors: LoRA 权重（safetensors 格式）
            - lora_config.json: LoRA 配置和基础模型信息
        全参数模式:
            - model.safetensors: 模型权重（不含 AudioVAE）
            - 复制预训练目录的配置文件: config.json、tokenizer 等
        通用文件:
            - optimizer.pth: 优化器状态
            - scheduler.pth: 学习率调度器状态
            - training_state.json: 包含当前步数的元数据
        - latest/: 最新检查点的完整副本（用于快速恢复）

    目录命名规则:
        step_{step:07d}/，例如 step_0001000/ 表示第 1000 步的检查点。
    """
    import shutil

    save_dir.mkdir(parents=True, exist_ok=True)
    tag = f"step_{step:07d}"
    folder = save_dir / tag
    folder.mkdir(parents=True, exist_ok=True)

    unwrapped = model.module if hasattr(model, "module") else model
    full_state = unwrapped.state_dict()
    lora_cfg = unwrapped.lora_config

    if lora_cfg is not None:
        # LoRA finetune: save only lora_A/lora_B weights
        state_dict = {k: v for k, v in full_state.items() if "lora_" in k}
        if SAFETENSORS_AVAILABLE:
            save_file(state_dict, folder / "lora_weights.safetensors")
        else:
            torch.save({"state_dict": state_dict}, folder / "lora_weights.ckpt")

        # Save LoRA config and base model path to a separate JSON file
        # If distribute=True, save hf_model_id; otherwise save local pretrained_path
        base_model_to_save = hf_model_id if distribute else (str(pretrained_path) if pretrained_path else None)
        lora_info = {
            "base_model": base_model_to_save,
            "lora_config": lora_cfg.model_dump() if hasattr(lora_cfg, "model_dump") else vars(lora_cfg),
        }
        with open(folder / "lora_config.json", "w", encoding="utf-8") as f:
            json.dump(lora_info, f, indent=2, ensure_ascii=False)
    else:
        # Full finetune: save non-vae weights to model.safetensors
        state_dict = {k: v for k, v in full_state.items() if not k.startswith("audio_vae.")}
        if SAFETENSORS_AVAILABLE:
            save_file(state_dict, folder / "model.safetensors")
        else:
            torch.save({"state_dict": state_dict}, folder / "pytorch_model.bin")

        # Copy config files from pretrained path
        if pretrained_path:
            pretrained_dir = Path(pretrained_path)
            files_to_copy = [
                "config.json",
                "audiovae.pth",
                "audiovae.safetensors",
                "tokenizer.json",
                "special_tokens_map.json",
                "tokenizer_config.json",
            ]
            for fname in files_to_copy:
                src = pretrained_dir / fname
                if src.exists():
                    shutil.copy2(src, folder / fname)

    torch.save(optimizer.state_dict(), folder / "optimizer.pth")
    torch.save(scheduler.state_dict(), folder / "scheduler.pth")
    with open(folder / "training_state.json", "w", encoding="utf-8") as f:
        json.dump({"step": int(step)}, f)

    # Update (or create) a `latest` folder by copying the most recent checkpoint
    latest_link = save_dir / "latest"
    try:
        if latest_link.exists():
            shutil.rmtree(latest_link)
        shutil.copytree(folder, latest_link)
    except Exception:
        print(f"Warning: failed to update latest checkpoint at {latest_link}", file=sys.stderr)


if __name__ == "__main__":
    from voxcpm.training.config import load_yaml_config

    args = argbind.parse_args()
    config_file = args.get("config_path")
    # If YAML config provided, use YAML args to call train
    if config_file:
        yaml_args = load_yaml_config(config_file)
        train(**yaml_args)
    else:
        # Otherwise use command line args (parsed by argbind)
        with argbind.scope(args):
            train()
