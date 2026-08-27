# 训练模块文档

> TTS_MultiModel LoRA 微调训练模块使用指南
>
> **最后更新**：2026-08-01

---

## 1. 概述

训练模块位于 `app/integrated_app/training/`，用于对 VoxCPM2 模型进行 LoRA 微调，
以定制特定说话人的音色。

### 支持的训练方式

- **LoRA 微调**：低秩适配器注入，不修改原始权重
- **单 GPU 训练**：支持 CUDA 和 MPS 后端
- **混合精度训练**：支持 bf16/fp16

---

## 2. 模块结构

| 文件 | 职责 |
|------|------|
| `accelerator.py` | 训练加速器，管理设备混合精度 |
| `config.py` | 训练配置（学习率、batch size、LoRA 参数等） |
| `data.py` | `HFVoxCPMDataset` 数据集 + `BatchProcessor` 批处理 |
| `packers.py` | 数据打包器，将音频+文本打包为训练样本 |
| `state.py` | `TrainingState` 状态管理（model/optimizer/scheduler） |
| `tracker.py` | `TrainingTracker` 进度追踪与日志 |

---

## 3. 数据准备

### 数据格式

训练数据使用 JSONL（JSON Lines）格式，每行一个样本：

```json
{"audio_path": "/path/to/audio1.wav", "text": "你好世界", "speaker": "speaker1"}
{"audio_path": "/path/to/audio2.wav", "text": "今天天气真好", "speaker": "speaker1"}
```

### 数据要求

- 音频格式：WAV（16-bit, 24kHz, 单声道）
- 音频长度：3-30 秒
- 文本：与音频内容准确对应
- 样本数量：建议 50-500 条

---

## 4. 训练流程

### 4.1 通过 UI 训练

1. 打开 WebUI，切换到「LoRA 训练」标签页
2. 填写训练配置：
   - 预训练模型路径
   - 训练数据 JSONL 文件路径
   - 输出目录
   - 学习率（推荐 1e-4）
   - 最大迭代次数（推荐 1000）
   - batch size（推荐 4）
   - LoRA rank（推荐 8）
   - LoRA alpha（推荐 16）
3. 点击「开始训练」

### 4.2 通过 API 训练

```bash
curl -X POST http://127.0.0.1:7869/api/training/start \
  -H "Content-Type: application/json" \
  -d '{
    "pretrained_model_path": "model/VoxCPM2",
    "train_manifest": "data/train.jsonl",
    "val_manifest": "data/val.jsonl",
    "output_dir": "lora/my_voice",
    "learning_rate": 0.0001,
    "max_iters": 1000,
    "batch_size": 4,
    "lora_rank": 8,
    "lora_alpha": 16
  }'
```

### 4.3 训练监控

- 训练进度通过 SSE 事件流推送
- 事件类型：`training_progress` / `training_complete` / `training_error`
- 训练日志显示在 UI 的「训练日志」区域

---

## 5. LoRA 权重使用

训练完成后，LoRA 权重保存在输出目录中。

### 加载 LoRA

1. 将 LoRA 权重文件放入 `lora/` 目录
2. 在 WebUI 的「设置」标签页中，从 LoRA 下拉列表选择
3. 点击「加载」

### 启用/禁用 LoRA

- 加载后默认启用
- 可通过「切换」按钮临时禁用/启用
- 禁用后使用原始模型权重推理

---

## 6. 训练参数调优

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| learning_rate | 1e-4 | 过高导致过拟合，过低训练慢 |
| max_iters | 500-2000 | 数据量大时减少 |
| batch_size | 4-8 | 显存不足时降低 |
| grad_accum | 1-4 | 模拟更大 batch size |
| lora_rank | 8-16 | 更高 rank = 更强表达能力 |
| lora_alpha | 16-32 | 通常为 rank 的 2 倍 |
| save_interval | 200 | 定期保存检查点 |
| log_interval | 10 | 日志频率 |

---

## 7. 信号安全

训练过程中收到 SIGTERM/SIGINT 时：

1. `signal_handlers.py` 设置优雅关闭标志
2. 训练循环检查 `check_graceful_shutdown()`
3. 保存检查点到 `shutdown_checkpoint.pt`
4. 安全退出

---

## 8. 常见问题

### Q: 训练 OOM？

- 降低 batch_size
- 降低 grad_accum
- 关闭其他 GPU 程序
- 使用 CPU 模式（非常慢）

### Q: 训练 loss 不下降？

- 检查数据质量（音频清晰度、文本准确性）
- 调整学习率（尝试 5e-5 或 2e-4）
- 增加 max_iters

### Q: LoRA 效果不明显？

- 增加 lora_rank
- 增加训练数据
- 增加 max_iters
- 确保参考音频质量

---

## 相关文件

| 文件 | 职责 |
|------|------|
| `training/accelerator.py` | 训练加速器 |
| `training/config.py` | 训练配置 |
| `training/data.py` | 数据加载 |
| `training/packers.py` | 数据打包 |
| `training/state.py` | 状态管理 |
| `training/tracker.py` | 进度追踪 |
| `signal_handlers.py` | 优雅关闭 |
| `routes/training.py` | 训练 API 路由 |
