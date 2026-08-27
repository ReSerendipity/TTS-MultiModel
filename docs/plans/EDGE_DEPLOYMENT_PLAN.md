# 边缘设备部署策略

> 最后更新: 2026-07-24
>
> 来源参考：VoxCPM (OpenBMB/VoxCPM) llama.cpp-omni / Chatterbox Nano 边缘部署

---

## 一、背景

VoxCPM 生态提供了 llama.cpp-omni 方案支持边缘设备部署，Chatterbox Nano 版本可在 8 CPU cores 上达到 3x realtime。

当前我们的项目需要 GPU 才能运行，边缘设备部署将大幅扩展应用场景。

---

## 二、目标设备矩阵

```
┌─────────────────────────────────────────────────────────┐
│                    目标设备矩阵                          │
├──────────┬──────────┬──────────┬──────────┬─────────────┤
│ 设备类型  │ CPU      │ RAM      │ 存储     │ 推理目标    │
├──────────┼──────────┼──────────┼──────────┼─────────────┤
│ 桌面 PC  │ 8+ cores │ 16GB+    │ 50GB SSD │ RTF <0.5   │
│ 笔记本   │ 4+ cores │ 8GB+     │ 20GB     │ RTF <1.0   │
│ 树莓派5  │ 4 cores  │ 8GB      │ 32GB SD  │ RTF <3.0   │
│ Jetson   │ 6 cores  │ 8GB      │ 64GB     │ RTF <0.3   │
│ 手机     │ 8 cores  │ 6GB+     │ 10GB     │ RTF <2.0   │
└──────────┴──────────┴──────────┴──────────┴─────────────┘
```

---

## 三、技术方案

### 3.1 量化与压缩

```
模型压缩流水线：

FP32 (原始) ──> FP16 (半精度) ──> INT8 (动态量化) ──> INT4 (GPTQ/AWQ)
  ~8GB VRAM      ~4GB VRAM         ~2GB VRAM          ~1GB VRAM
  1.0x RTF       1.5x RTF          2.0x RTF           3.0x RTF

GGUF 格式 (llama.cpp 兼容):
  FP16 GGUF: ~4GB
  Q8_0 GGUF: ~2GB
  Q4_0 GGUF: ~1GB
  Q4_K_M GGUF: ~0.8GB (推荐)
```

### 3.2 ONNX Runtime 后端

```python
# engines/onnx_backend.py (新文件)

class ONNXBackend:
    """ONNX Runtime 推理后端。

    将 PyTorch 模型转换为 ONNX 格式，使用 ONNX Runtime
    在 CPU 和移动设备上高效推理。
    """

    def __init__(self):
        self._session = None
        self._provider = "CPUExecutionProvider"

    def convert_model(self, pytorch_model, output_path: str) -> str:
        """将 PyTorch 模型转换为 ONNX 格式。"""
        ...

    def load_model(self, onnx_path: str) -> bool:
        """加载 ONNX 模型。"""
        ...

    def infer(self, input_ids: np.ndarray) -> np.ndarray:
        """ONNX 推理。"""
        ...
```

### 3.3 llama.cpp 后端

```python
# engines/llamacpp_backend.py (新文件)

class LlamaCppBackend:
    """llama.cpp 推理后端。

    使用 GGUF 格式模型，支持 CPU 和混合 CPU+GPU 推理。
    适合边缘设备和低资源场景。
    """

    def __init__(self, model_path: str, n_ctx: int = 2048, n_threads: int = 4):
        self._model_path = model_path
        self._n_ctx = n_ctx
        self._n_threads = n_threads
        self._llm = None

    def load(self) -> bool:
        """加载 GGUF 模型。"""
        try:
            from llama_cpp import Llama
            self._llm = Llama(
                model_path=self._model_path,
                n_ctx=self._n_ctx,
                n_threads=self._n_threads,
                verbose=False,
            )
            return True
        except ImportError:
            return False

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        """生成文本。"""
        if not self._llm:
            return ""
        output = self._llm(prompt, max_tokens=max_tokens)
        return output["choices"][0]["text"]
```

---

## 四、部署架构

```
边缘部署架构：

┌──────────────────────────────────┐
│        Application Layer          │
│  (Web UI / CLI / API Client)     │
├──────────────────────────────────┤
│        Inference Adapter          │
│  ┌─────┬──────┬────────┬──────┐ │
│  │PyTorch│ONNX │llama.cpp│TRT │ │
│  │(GPU) │(CPU) │ (CPU)   │(GPU)│ │
│  └─────┴──────┴────────┴──────┘ │
├──────────────────────────────────┤
│        Model Quantization         │
│  (FP16/INT8/INT4/GGUF)          │
├──────────────────────────────────┤
│        Hardware Abstraction        │
│  (CUDA/MPS/CPU/NEON/x86)        │
└──────────────────────────────────┘
```

---

## 五、性能基准预估

| 设备 | 量化级别 | 10秒音频生成时间 | 内存占用 |
|------|---------|-----------------|---------|
| RTX 3060 (GPU) | FP16 | ~0.8s | ~4GB VRAM |
| i7-12700 (CPU) | INT8 | ~3s | ~4GB RAM |
| i7-12700 (CPU) | INT4 | ~2s | ~2.5GB RAM |
| Jetson Orin (GPU) | FP16 | ~1.2s | ~3GB VRAM |
| Raspberry Pi 5 (CPU) | INT4 | ~15s | ~2GB RAM |
| iPhone 15 Pro (CPU) | INT4 | ~5s | ~1.5GB RAM |

---

## 六、实施路径

| Phase | 时间 | 内容 |
|-------|------|------|
| P1 | 4周 | INT8/INT4 量化工具链 |
| P2 | 3周 | ONNX Runtime 后端 |
| P3 | 4周 | llama.cpp 后端 + GGUF 转换 |
| P4 | 3周 | 边缘设备适配和测试 |
| P5 | 2周 | 打包发布 (Docker/独立可执行文件) |

---

## 七、配置示例

```yaml
# config.yaml
edge_deployment:
  enabled: false
  backend: "auto"  # auto / pytorch / onnx / llamacpp

  onnx:
    quantization: "int8"
    providers: ["CPUExecutionProvider"]

  llamacpp:
    model_path: ""  # GGUF 模型路径
    n_ctx: 2048
    n_threads: 4  # CPU 线程数
    n_gpu_layers: 0  # GPU offload 层数 (0=纯CPU)
```
