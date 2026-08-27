# TTS MultiModel - 模型下载与配置指南

> 最后更新: 2026-07-23

> 本文档详细说明项目所需模型的下载步骤和配置方法

## 概述

TTS MultiModel 使用多个预训练模型实现语音合成和音色克隆功能。以下模型需要**单独下载**并放置到指定目录。

### 快速检查

启动应用前，运行以下命令检查模型是否齐全：

```bash
python -c "import os; models=['VoxCPM2','SenseVoiceSmall','speech_zipenhancer']; print('\n'.join([f'[OK] {m}' if os.path.exists(f'model/{m}') else f'[MISSING] {m}' for m in models]))"
```

或手动检查 `model/` 目录：

```
model/
├── VoxCPM2/              [必需] 主 TTS 模型
├── SenseVoiceSmall/      [必需] 语音识别模型
└── speech_zipenhancer/   [必需] 音频降噪模型
```

---

## 模型 1：VoxCPM2（主 TTS 模型）

### 模型信息

- **用途**: 文本转语音（TTS）
- **大小**: 约 5-8 GB
- **格式**: safetensors / bin
- **需要 GPU**: 是（推荐 6GB+ VRAM）

### 下载来源

#### 方式 A：HuggingFace Hub

```bash
pip install huggingface_hub

# 创建目录
mkdir model\VoxCPM2

# 下载模型
python -c "
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='openbmb/VoxCPM2',
    local_dir='model/VoxCPM2',
    local_dir_use_symlinks=False
)
"
```

#### 方式 B：ModelScope

```bash
pip install modelscope

# 下载模型
python -c "
from modelscope import snapshot_download
snapshot_download(
    model_id='OpenBMB/VoxCPM2',
    cache_dir='model/VoxCPM2'
)
"
```

#### 方式 C：手动下载

1. 访问 [HuggingFace VoxCPM2 页面](https://huggingface.co/openbmb/VoxCPM2)
2. 点击 "Files and versions" 标签
3. 下载所有文件到 `model/VoxCPM2/` 目录

### 目录结构

下载完成后，目录应包含以下文件：

```
model/VoxCPM2/
├── config.json
├── model.safetensors 或 pytorch_model.bin
├── tokenizer.json
├── tokenizer_config.json
├── vocoder/              (如有)
│   ├── config.json
│   └── model.safetensors
└── ...
```

---

## 模型 2：SenseVoiceSmall（ASR 模型）

### 模型信息

- **用途**: 自动语音识别（用于音色克隆时提取参考文本）
- **大小**: 约 300 MB
- **格式**: bin / safetensors

### 下载来源

#### 方式 A：HuggingFace

```bash
python -c "
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='FunAudioLLM/SenseVoiceSmall',
    local_dir='model/SenseVoiceSmall',
    local_dir_use_symlinks=False
)
"
```

#### 方式 B：ModelScope

```bash
python -c "
from modelscope import snapshot_download
snapshot_download(
    model_id='iic/SenseVoiceSmall',
    cache_dir='model/SenseVoiceSmall'
)
"
```

### 目录结构

```
model/SenseVoiceSmall/
├── config.yaml
├── model.pt
├── configuration.json
└── ...
```

---

## 模型 3：speech_zipenhancer（音频降噪模型）

### 模型信息

- **用途**: 音频降噪和增强（提升参考音频质量）
- **大小**: 约 100-200 MB
- **格式**: onnx / bin

### 下载来源

#### 方式 A：ModelScope（推荐）

```bash
pip install modelscope

# 下载模型
python -c "
from modelscope import snapshot_download
snapshot_download(
    model_id='iic/speech_zipenhancer_ans_multiloss_16k_base',
    cache_dir='model/speech_zipenhancer'
)
"
```

> **注意**: speech_zipenhancer 目前仅在 ModelScope 上提供，HuggingFace 暂无此模型。

#### 方式 B：手动下载

1. 访问 [ModelScope speech_zipenhancer 页面](https://modelscope.cn/models/iic/speech_zipenhancer_ans_multiloss_16k_base)
2. 下载所有文件到 `model/speech_zipenhancer/`

### 目录结构

```
model/speech_zipenhancer/
├── config.json
├── model.onnx 或 model.bin
└── ...
```

---

## 模型下载脚本（可选）

如果项目包含 `download_models.py` 脚本，可以使用一键下载：

```bash
python download_models.py
```

该脚本通常会自动：
1. 检查模型是否已存在
2. 从 HuggingFace 或 ModelScope 下载缺失的模型
3. 验证下载的文件

---

## 常见问题

### Q: 下载速度很慢怎么办？

**解决方案**:

1. **使用国内镜像**（如 ModelScope）
   ```bash
   pip install modelscope
   ```

2. **设置 HuggingFace 镜像**
   ```bash
   # Windows
   set HF_ENDPOINT=https://hf-mirror.com

   # Linux/Mac
   export HF_ENDPOINT=https://hf-mirror.com
   ```

3. **使用代理**
   ```bash
   set http_proxy=http://127.0.0.1:7890
   set https_proxy=http://127.0.0.1:7890
   ```

### Q: 下载中断后如何继续？

大多数下载工具支持断点续传：

```python
# HuggingFace 自动继续中断的下载
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='...',
    local_dir='...',
    resume_download=True  # 启用断点续传
)
```

### Q: 磁盘空间不足怎么办？

**最小磁盘需求**:

| 模型 | 大小 | 运行时额外需求 |
|------|------|----------------|
| VoxCPM2 | ~8 GB | ~10 GB (GPU + RAM) |
| SenseVoiceSmall | ~300 MB | ~500 MB |
| speech_zipenhancer | ~200 MB | ~300 MB |
| **总计** | **~8.5 GB** | **~11 GB** |

**建议**: 预留至少 20 GB 的可用空间。

### Q: 如何验证模型文件完整性？

```python
import os

def check_model(path):
    """检查模型目录是否存在且不为空"""
    if not os.path.exists(path):
        return False, f"Directory not found: {path}"
    files = os.listdir(path)
    if not files:
        return False, f"Directory is empty: {path}"
    return True, f"OK ({len(files)} files)"

models = {
    "VoxCPM2": "model/VoxCPM2",
    "SenseVoiceSmall": "model/SenseVoiceSmall",
    "speech_zipenhancer": "model/speech_zipenhancer",
}

for name, path in models.items():
    ok, msg = check_model(path)
    status = "[OK]" if ok else "[ERROR]"
    print(f"{status} {name}: {msg}")
```

### Q: 模型文件已存在但仍报错？

检查以下几点：

1. **目录结构正确** - 模型文件应直接放在 `model/<ModelName>/` 下
2. **权限正确** - 确保应用有读取权限
3. **文件完整** - 检查文件大小是否与预期一致

---

## 离线环境部署

如果目标机器无法联网：

1. 在有网络的机器上下载所有模型
2. 打包 `model/` 目录
   ```bash
   tar -czf model.tar.gz model/
   ```
3. 传输到目标机器并解压
   ```bash
   tar -xzf model.tar.gz
   ```

---

## 配置说明

### config.yaml

模型加载路径在 `config.yaml` 中配置：

```yaml
# 模型路径配置
model_paths:
  voxcpm2: model/VoxCPM2
  asr: model/SenseVoiceSmall
  denoiser: model/speech_zipenhancer

# 生成参数
generation:
  cfg_value: 2.0
  inference_timesteps: 10
  normalize: true
  denoise: true
```

### 环境变量

项目默认使用**离线模式**，不从网络下载模型：

```python
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['MODELSCOPE_OFFLINE'] = '1'
```

如需在运行时下载模型，将这些环境变量设为 `'0'`。

---

## 模型更新

### 更新 VoxCPM2

1. 备份当前模型
   ```bash
   move model\VoxCPM2 model\VoxCPM2_backup
   ```

2. 下载新版本到 `model/VoxCPM2/`

3. 测试新版本

4. 确认无误后可删除备份

### 版本兼容性

| 应用版本 | VoxCPM2 版本 | SenseVoiceSmall | 备注 |
|----------|--------------|-----------------|------|
| v1.x | v0.x | latest | 初始版本 |
| v2.x | v1.x | latest | 支持 LoRA 微调 |

---

## 相关文档

- [模型扩展指南](MODEL_EXTENSION_GUIDE.md) - 如何添加新的 TTS 引擎
- [README.md](../README.md) - 项目概述和快速入门
- [UI 开发指南](UI开发指南_README.md) - Web UI 开发指南
