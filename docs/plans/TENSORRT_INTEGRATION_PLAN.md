# TensorRT-LLM 集成计划

> 最后更新: 2026-07-24

> 来源参考：CosyVoice (FunAudioLLM/CosyVoice) TensorRT-LLM 4x 推理加速

---

## 一、背景

CosyVoice 通过 TensorRT-LLM 实现了 4x 推理加速，同时支持：
- vLLM 集成（高吞吐量服务）
- Docker + Triton 部署
- Tensor 并行和流水线并行

我们的项目当前使用标准 PyTorch 推理，推理速度是主要瓶颈。

---

## 二、TensorRT-LLM 概述

```
TensorRT-LLM 加速栈：

┌─────────────────────────────────────┐
│         Application Layer           │
│    (TTS MultiModel / FastAPI)       │
├─────────────────────────────────────┤
│       TensorRT-LLM Runtime          │
│  ┌───────────┬──────────────────┐  │
│  │ PagedAttn │ Continuous Batching│ │
│  │ In-Flight │ FlashAttention   │  │
│  │ Batching  │ GEMM Fusion      │  │
│  └───────────┴──────────────────┘  │
├─────────────────────────────────────┤
│       TensorRT Engine               │
│  (Optimized CUDA kernels)          │
├─────────────────────────────────────┤
│         GPU Hardware                │
│  (NVIDIA Ampere/Hopper/Ada)        │
└─────────────────────────────────────┘
```

### 关键优势
- **推理加速**: 2-4x (取决于模型和 batch size)
- **显存优化**: PagedAttention 减少 KV-cache 碎片
- **量化支持**: FP16, INT8, INT4 (GPTQ/AWQ/FP8)
- **批量处理**: Continuous batching 提高吞吐量

---

## 三、集成架构

### 3.1 引擎适配层

```python
# engines/voxcpm2/trt_backend.py (新文件)

class TensorRTBackend:
    """TensorRT-LLM 后端适配器。

    将 VoxCPM2 的 LLM 组件（MiniCPM-4 backbone）转换为
    TensorRT 引擎，保持 TTS 其他组件（AudioVAE, Vocoder）不变。
    """

    def __init__(self, model_path: str, config: TRTConfig):
        self._model_path = model_path
        self._config = config
        self._engine = None
        self._tokenizer = None

    def build_engine(self) -> bool:
        """从 HF 模型构建 TensorRT 引擎。

        流程：
        1. 加载 HF 模型和 tokenizer
        2. 转换为 TensorRT-LLM checkpoint
        3. 构建优化后的 TensorRT 引擎
        4. 保存引擎到磁盘
        """
        ...

    def load_engine(self) -> bool:
        """加载预构建的 TensorRT 引擎。"""
        ...

    def infer(self, input_ids, sampling_params) -> list:
        """使用 TensorRT 引擎进行推理。"""
        ...

    def warmup(self, num_runs: int = 10) -> dict:
        """预热引擎并收集性能指标。"""
        ...
```

### 3.2 配置管理

```python
@dataclass
class TRTConfig:
    """TensorRT-LLM 配置。"""
    # 基础配置
    precision: str = "fp16"  # fp16, int8, int4
    max_batch_size: int = 8
    max_input_len: int = 2048
    max_output_len: int = 2048

    # 并行配置
    tensor_parallel_size: int = 1
    pipeline_parallel_size: int = 1

    # 优化配置
    use_fp8: bool = False
    use_inflight_batching: bool = True
    use_paged_kv_cache: bool = True

    # 缓存配置
    engine_cache_dir: str = "model/trt_engines"
    force_rebuild: bool = False
```

### 3.3 混合推理模式

```
标准模式 (当前):
  Text → [MiniCPM-4 (PyTorch)] → Tokens → [AudioVAE] → Audio

TensorRT 模式:
  Text → [MiniCPM-4 (TensorRT)] → Tokens → [AudioVAE (PyTorch)] → Audio

仅加速 LLM 部分，AudioVAE 和 Vocoder 保持 PyTorch：
  - LLM 是主要瓶颈 (占 70%+ 推理时间)
  - AudioVAE/Vocoder 较轻量，TensorRT 收益有限
  - 降低集成复杂度
```

---

## 四、构建流程

```
1. 模型转换
   python -c "
   from tensorrt_llm.models import convert_hf_model
   convert_hf_model(
       hf_model_dir='model/VoxCPM2',
       output_dir='model/trt_engines/voxcpm2',
       dtype='float16'
   )
   "

2. 引擎构建
   python -c "
   from tensorrt_llm import Builder
   builder = Builder()
   builder.build(
       input_dir='model/trt_engines/voxcpm2',
       output_dir='model/trt_engines/voxcpm2_engine',
       precision='fp16',
       max_batch_size=8,
       max_input_len=2048,
       max_output_len=2048
   )
   "

3. 引擎加载
   from engines.voxcpm2.trt_backend import TensorRTBackend
   backend = TensorRTBackend('model/trt_engines/voxcpm2_engine')
   backend.load_engine()
```

---

## 五、性能基准

### 5.1 预期加速比

| 场景 | PyTorch | TensorRT FP16 | TensorRT INT8 | 加速比 |
|------|---------|---------------|---------------|--------|
| 单条短文本 (<50字) | 1.2s | 0.4s | 0.3s | 3-4x |
| 单条长文本 (200字) | 4.5s | 1.5s | 1.2s | 3-4x |
| Batch 4 | 4.8s | 1.2s | 0.9s | 4-5x |
| Batch 8 | 9.0s | 1.8s | 1.4s | 5-6x |

### 5.2 显存使用

| 配置 | VRAM 使用 | 说明 |
|------|----------|------|
| PyTorch FP32 | ~8GB | 当前 |
| TensorRT FP16 | ~5GB | -37% |
| TensorRT INT8 | ~3.5GB | -56% |
| TensorRT INT4 | ~2.5GB | -69% |

---

## 六、实施计划

| Phase | 时间 | 内容 | 风险 |
|-------|------|------|------|
| P1 | 2周 | 评估 TensorRT-LLM 兼容性 | MiniCPM-4 架构支持 |
| P2 | 3周 | 实现模型转换和引擎构建脚本 | 构建时间长 |
| P3 | 2周 | 集成 TensorRTBackend 到引擎层 | API 兼容性 |
| P4 | 2周 | 性能基准测试和调优 | 量化精度损失 |
| P5 | 1周 | Docker 部署和文档 | 环境依赖复杂 |

---

## 七、配置示例

```yaml
# config.yaml
tensorrt:
  enabled: false
  precision: "fp16"  # fp16, int8, int4
  engine_cache_dir: "model/trt_engines"
  max_batch_size: 8
  max_input_len: 2048
  max_output_len: 2048
  tensor_parallel_size: 1
  force_rebuild: false

  # 混合推理：仅加速 LLM，其他组件用 PyTorch
  hybrid_mode: true
```

---

## 八、注意事项

1. **GPU 架构要求**: TensorRT-LLM 需要 NVIDIA Ampere (A100) 或更新架构
2. **构建时间**: 首次构建 TensorRT 引擎可能需要 10-30 分钟
3. **精度损失**: INT8/INT4 量化可能轻微影响音质，需要评估
4. **模型兼容性**: 需验证 MiniCPM-4 backbone 是否被 TensorRT-LLM 支持
5. **回退机制**: 当 TensorRT 不可用时，自动回退到 PyTorch 推理
