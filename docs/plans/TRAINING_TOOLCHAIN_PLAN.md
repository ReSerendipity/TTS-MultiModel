# 完整训练工具链规划

> 最后更新: 2026-07-24

> 来源参考：GPT-SoVITS (RVC-Boss/GPT-SoVITS) 完整训练工具链

---

## 一、背景

GPT-SoVITS 拥有目前开源 TTS 项目中最完整的训练工具链，包括：
- 音频预处理（UVR5 伴奏分离、降噪）
- 自动训练集分割
- 多语言 ASR 标注（FunASR、SenseVoice）
- 文本标注工具
- 自动化训练流水线

我们的项目已支持 LoRA 微调，但缺少完整的数据准备工具链。

---

## 二、目标架构

```
训练工具链流程：
                    ┌──────────┐
                    │ 原始音频  │
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ 伴奏分离  │ (UVR5/DEMUCS)
                    │ (可选)   │
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ 降噪增强  │ (speech_zipenhancer)
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ VAD 切分  │ (Silero VAD)
                    │ 自动分割  │
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ ASR 标注  │ (SenseVoice/FunASR)
                    │ 自动转写  │
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ 质量过滤  │ (SNR/时长/重复检测)
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ 数据打包  │ (格式转换)
                    │ JSONL    │
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ LoRA 训练 │ (现有 training/ 模块)
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ 模型评估  │ (MOS/相似度)
                    └──────────┘
```

---

## 三、模块设计

### 3.1 数据预处理模块 (`training/data_prep/`)

```python
# training/data_prep/separator.py - 音频分离
class AudioSeparator:
    """使用 UVR5 或 DEMUCS 进行人声/伴奏分离。"""
    def separate(self, audio_path: str, method: str = "vocals") -> str:
        ...

# training/data_prep/denoiser.py - 降噪
class AudioDenoiser:
    """使用 speech_zipenhancer 或 RNNoise 进行降噪。"""
    def denoise(self, audio_path: str) -> str:
        ...

# training/data_prep/vad_splitter.py - VAD 切分
class VADSplitter:
    """使用 Silero VAD 自动切分长音频为训练片段。"""
    def split(self, audio_path: str, min_duration: float = 1.0,
              max_duration: float = 30.0) -> list[str]:
        ...

# training/data_prep/quality_filter.py - 质量过滤
class QualityFilter:
    """基于 SNR、时长、重复度等指标过滤低质量样本。"""
    def filter(self, audio_paths: list[str], min_snr_db: float = 15.0) -> list[str]:
        ...
```

### 3.2 ASR 标注模块 (`training/data_prep/annotator.py`)

```python
class AutoAnnotator:
    """自动为音频片段生成文本标注。

    支持后端：
    - SenseVoiceSmall (已集成)
    - FunASR Paraformer
    - Whisper (多语言)
    """
    def __init__(self, backend: str = "sensevoice"):
        ...

    def annotate_batch(self, audio_paths: list[str],
                       language: str = "zh") -> list[dict]:
        """批量标注，返回 [{audio_path, text, confidence}, ...]"""
        ...
```

### 3.3 数据打包模块 (`training/data_prep/packer.py`)

```python
class TrainingDataPacker:
    """将标注后的音频数据打包为训练格式。

    输出格式：
    - JSONL (当前格式)
    - HuggingFace Dataset
    - WebDataset (流式加载)
    """
    def pack(self, annotations: list[dict], output_dir: str,
             format: str = "jsonl") -> str:
        ...

    def create_metadata(self, packed_path: str) -> dict:
        """生成数据集元信息（时长统计、语言分布等）。"""
        ...
```

### 3.4 评估模块 (`training/evaluation/`)

```python
class ModelEvaluator:
    """训练后模型评估。

    指标：
    - MOS (Mean Opinion Score) 预测
    - Speaker Similarity (说话人相似度)
    - WER (词错误率)
    - Inference Speed (推理速度)
    """
    def evaluate(self, model_path: str, test_data: list[dict]) -> dict:
        ...
```

---

## 四、Web UI 集成

在 Web UI 中添加"训练工作台"标签页：

```
训练工作台
├── 数据准备
│   ├── 上传原始音频
│   ├── 音频预处理（分离/降噪/切分）
│   ├── ASR 自动标注
│   └── 质量检查与过滤
├── 数据预览
│   ├── 音频播放器
│   ├── 文本编辑
│   └── 标签/分类
├── 训练配置
│   ├── LoRA 参数（rank/alpha/dropout）
│   ├── 训练参数（lr/epochs/batch_size）
│   └── 数据集比例（train/val/test）
├── 训练监控
│   ├── 实时损失曲线
│   ├── 学习率调度
│   └── 预估完成时间
└── 模型评估
    ├── 样本对比
    ├── 相似度评分
    └── 导出/部署
```

---

## 五、实施计划

| Phase | 时间 | 内容 | 依赖 |
|-------|------|------|------|
| P1 | 1-2周 | VAD 切分 + ASR 标注 | SenseVoiceSmall (已有) |
| P2 | 2-3周 | 质量过滤 + 数据打包 | P1 |
| P3 | 1-2周 | 伴奏分离 (UVR5) | 额外模型 |
| P4 | 2周 | Web UI 训练工作台 | P1-P2 |
| P5 | 1周 | 评估模块 | P4 |

---

## 六、配置示例

```yaml
# config.yaml
training_pipeline:
  # 预处理
  preprocessing:
    vad_model: "silero"  # VAD 模型
    min_segment_duration: 1.0  # 最短片段 (秒)
    max_segment_duration: 30.0  # 最长片段 (秒)
    denoise: true  # 降噪
    separate_vocals: false  # 伴奏分离

  # ASR 标注
  annotation:
    backend: "sensevoice"  # sensevoice / funasr / whisper
    language: "zh"
    min_confidence: 0.5  # 最低置信度

  # 质量过滤
  quality:
    min_snr_db: 15.0  # 最低信噪比
    min_duration: 1.0  # 最短时长
    max_duration: 30.0  # 最长时长
    max_duplicate_text: 3  # 同一文本最多出现次数
```
