"""VoxCPM LoRA 微调训练模块。

本模块为 WebUI 中 LoRA 微调 Tab 提供完整的训练管线支持，封装了从数据加载、
加速器配置、数据打包、状态管理到进度追踪的全流程能力。

主要模块与核心类/函数：

- accelerator.py
    - ``Accelerator``: 兼容旧版训练脚本的加速器封装（VoxCPM minicpm-audio 风格）
    - ``TrainingAccelerator``: 训练加速器统一封装层，负责设备/精度/分布式/梯度累积
    - ``_NativeAccelerator``: HuggingFace Accelerate 未安装时的原生 torch 单卡回退实现

- config.py
    - ``TrainingConfig``: 单次 LoRA 训练的完整参数集合（Pydantic 模型）
    - ``DatasetConfig``: 数据集配置（采样率、时长过滤、切分比例等）
    - ``LoRAConfig``: LoRA 适配层配置（目标模块、rank、alpha、dropout、bias）
    - ``OptimizerConfig``: 优化器配置（类型、学习率、权重衰减、Adam betas）
    - ``get_default_config()``: 生成经验最优的默认训练配置
    - ``load_training_config()`` / ``save_training_config()``: 配置文件 JSON/YAML 读写
    - ``parse_args_with_config()``: 统一解析 CLI 参数和 YAML 配置（向后兼容 argbind）

- data.py
    - ``HFVoxCPMDataset``: 从本地目录读取 wav+txt 对 / metadata.jsonl 的 PyTorch Dataset
    - ``BatchProcessor``: 将 DatasetEntry 列表处理为模型可直接 forward 的 batch 张量
    - ``DatasetEntry``: 单条训练样本的结构化描述（NamedTuple）
    - ``create_dataloaders()``: 根据 TrainingConfig 构建 train / eval DataLoader
    - ``load_audio_text_datasets()``: 从 json manifest 加载 HuggingFace Dataset（legacy）
    - ``build_dataloader()``: 将 HuggingFace Dataset 包装为 DataLoader（legacy）

- packers.py
    - ``AudioFeatureProcessingPacker``: VoxCPM 多模态 batch 打包器（text/audio mask、position ids、loss mask 等）
    - ``LengthSortedBatchPacker``: 按样本长度排序后贪心打包，降低 padding 浪费
    - ``DynamicBucketPacker``: 按时长阈值动态分桶，对 Flash Attention 更友好

- state.py
    - ``TrainingState``: 训练状态数据类（运行时容器 + 可序列化断点续训字段）
    - ``StateManager``:  checkpoint 保存/加载/清理管理器，支持原子写与断点续训

- tracker.py
    - ``TrainingTracker``: 训练进度追踪器（SSE 实时推送 + TensorBoard 记录 + ETA 估算）

训练流程概要::

    1. 调用 get_default_config(data_dir) 或 load_training_config(path) 得到 TrainingConfig
    2. create_dataloaders(cfg, tokenizer, ...) 构建 train/eval DataLoader
    3. 创建 TrainingAccelerator(device, precision, grad_accum_steps)
    4. accelerator.prepare(model, optimizer, train_loader, scheduler)
    5. 创建 StateManager(output_dir) 和 TrainingTracker(...)
    6. 主循环：for each batch -> forward -> loss.backward -> optimizer.step -> tracker.on_step_end
    7. 每个 epoch 结束：tracker.on_epoch_end -> state_manager.save(state, ...)
    8. 训练结束：tracker.on_train_end -> state_manager.clean_old_keep_last_n()
"""

from .accelerator import Accelerator
from .data import (
    BatchProcessor,
    HFVoxCPMDataset,
    build_dataloader,
    load_audio_text_datasets,
)
from .state import TrainingState
from .tracker import TrainingTracker

__all__ = [
    "Accelerator",
    "TrainingTracker",
    "HFVoxCPMDataset",
    "BatchProcessor",
    "TrainingState",
    "load_audio_text_datasets",
    "build_dataloader",
]
