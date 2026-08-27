# TTS MultiModel - 模型扩展指南

> 最后更新: 2026-07-31

> 本文档说明如何向 TTS MultiModel 项目添加新的 TTS 模型引擎。
> 已对齐当前真实架构（声明式引擎注册表 + 通用化调度层）。

## 项目架构概述

TTS MultiModel 采用**声明式插件引擎架构**，核心设计原则：

- **引擎接口协议化** - 所有引擎必须实现 `TTSEngine` 或 `ControllableTTSEngine` 协议（Protocol）
- **声明式注册** - 引擎通过 `engine_interface.engine_registry` 懒导入注册 + `config.yaml` 声明规格，无需侵入调度层
- **通用化调度** - `model_manager` 对未在 `EngineName` 白名单中的引擎走 `_load_generic_engine` 通用加载路径，`model_registry` 用通用容器 `_engines` 保存实例，因此**新增引擎无需修改 model_manager/model_registry 的分支**
- **构造轻量、显式 load()** - 声明式引擎遵循"无参构造 + `load()` 加载权重"契约，由调度层统一调用

### 核心组件

```
app/integrated_app/
├── engine_interface.py      # TTSEngine 协议 + InMemoryEngineRegistry（engine_registry 单例）
├── model_registry.py        # 模型状态单例；通用引擎存于 _engines 容器
├── model_manager.py         # 加载/卸载/切换；_load_generic_engine 通用加载路径
├── config.py                # 路径常量（如 <ENGINE>_MODEL_PATH）
├── config_models.py         # EngineSpecConfig（config.yaml 的 models.engines 映射）
└── engines/
    ├── voxcpm2/             # VoxCPM2 引擎子包（参考示例）
    ├── indextts2_engine.py  # IndexTTS2 引擎（单文件参考示例）
    └── dotstts_engine.py    # dots.tts 引擎（声明式接入示例）
```

> 说明：VoxCPM2/IndexTTS2 因历史原因在 model_manager/model_registry 中保留专属分支；
> **新引擎应走声明式通用路径**（如 dotstts），无需触碰这两个模块。

---

## 引擎接口协议

所有 TTS 引擎必须实现以下接口之一：

### 1. 基础接口：`TTSEngine`

适用于标准 TTS 模型（文本转语音）

```python
from typing import Protocol, Generator, Tuple, Optional

class TTSEngine(Protocol):
    def is_ready(self) -> bool:
        """检查引擎是否加载并准备就绪"""
        ...

    def load(self) -> None:
        """加载模型到内存/GPU"""
        ...

    def unload(self) -> None:
        """卸载模型，释放 GPU 内存"""
        ...

    def generate_voice_design(
        self,
        text: str,
        instruction: str = "",
        normalize: bool = True,
    ) -> Tuple[str, str]:
        """从文本生成语音
        返回: (音频文件路径, 状态消息)
        """
        ...

    def generate_voice_clone(
        self,
        text: str,
        reference_audio_path: Optional[str] = None,
        instruction: str = "",
        normalize: bool = True,
        **kwargs,
    ) -> Tuple[str, str]:
        """使用参考音频克隆音色
        返回: (音频文件路径, 状态消息)
        """
        ...

    def generate_script(
        self,
        text: str,
        speaker_map: dict,
        persona_map: dict = None,
        **kwargs,
    ) -> Tuple[str, str]:
        """从多角色脚本文本生成语音
        返回: (音频文件路径, 状态消息)
        """
        ...

    def generate_streaming(
        self,
        text: str,
        reference_audio_path: Optional[str] = None,
        **kwargs,
    ) -> Generator[Any, None, None]:
        """流式生成（适用于长文本）
        返回: 音频块生成器
        """
        ...
```

### 2. 高级接口：`ControllableTTSEngine`

在 `TTSEngine` 基础上，支持更精细的控制参数：

```python
class ControllableTTSEngine(Protocol):
    # 必须实现 TTSEngine 的所有方法，再加上：

    def generate_ultimate_clone(
        self,
        text: str,
        lang: str,
        ref_audio: str,
        denoise_strength: str,
        use_random_seed: bool,
        cfg_scale: float,
        denoise_steps: int,
        seed: int,
    ) -> Tuple[str, str]:
        """使用完整可控参数生成克隆语音"""
        ...

    def generate_with_prompt(
        self,
        text: str,
        prompt_wav_path: str,
        prompt_text: str,
        cfg_value: float = 2.0,
        inference_timesteps: int = 10,
        normalize: bool = True,
        denoise: bool = True,
        retry_badcase: bool = True,
        retry_badcase_max_times: int = 3,
        retry_badcase_ratio_threshold: float = 6.0,
        min_len: int = 2,
        max_len: int = 4096,
    ) -> Tuple[str, str]:
        """使用提示音频继续生成"""
        ...

    def load_lora(self, lora_weights_path: str) -> Tuple[list, list]:
        """加载 LoRA 微调权重"""
        ...

    def unload_lora(self) -> None:
        """卸载 LoRA 权重"""
        ...
```

---

## 实现新引擎的步骤

### 步骤 1：创建引擎类

在 `app/integrated_app/engines/` 目录下创建新文件，例如 `my_new_engine.py`：

```python
# -*- coding: utf-8 -*-
"""My New TTS Engine"""

import os
import logging
from typing import Tuple, Optional, Generator, Any

logger = logging.getLogger("tts_multimodel")


class MyNewEngine:
    """自定义 TTS 引擎实现"""

    def __init__(self, model_path: str, config: dict):
        """初始化引擎

        Args:
            model_path: 模型文件路径
            config: 引擎配置字典
        """
        self.model_path = model_path
        self.config = config
        self._model = None
        self._is_loaded = False

    def is_ready(self) -> bool:
        """检查模型是否已加载"""
        return self._is_loaded and self._model is not None

    def load(self) -> None:
        """加载模型到内存"""
        try:
            logger.info(f"Loading model from {self.model_path}")
            # 在这里实现模型加载逻辑
            # self._model = load_model(self.model_path)
            self._is_loaded = True
            logger.info("Model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise

    def unload(self) -> None:
        """卸载模型，释放 GPU 内存"""
        try:
            logger.info("Unloading model...")
            # 在这里实现模型卸载逻辑
            # del self._model
            self._model = None
            self._is_loaded = False
            logger.info("Model unloaded successfully")
        except Exception as e:
            logger.error(f"Failed to unload model: {e}")

    def generate_voice_design(
        self,
        text: str,
        instruction: str = "",
        normalize: bool = True,
    ) -> Tuple[str, str]:
        """从文本生成语音"""
        if not self.is_ready():
            return "", "Error: Model not loaded"

        try:
            # 在这里实现语音生成逻辑
            # audio = self._model.synthesize(text, instruction=instruction)
            # audio_path = save_audio(audio)
            return "output.wav", "Voice generated successfully"
        except Exception as e:
            logger.error(f"Generation failed: {e}")
            return "", f"Error: {str(e)}"

    def generate_voice_clone(
        self,
        text: str,
        reference_audio_path: Optional[str] = None,
        instruction: str = "",
        normalize: bool = True,
        **kwargs,
    ) -> Tuple[str, str]:
        """使用参考音频克隆音色"""
        if not self.is_ready():
            return "", "Error: Model not loaded"

        try:
            # 在这里实现音色克隆逻辑
            return "output_clone.wav", "Voice cloned successfully"
        except Exception as e:
            logger.error(f"Clone failed: {e}")
            return "", f"Error: {str(e)}"

    def generate_script(
        self,
        text: str,
        speaker_map: dict,
        persona_map: dict = None,
        **kwargs,
    ) -> Tuple[str, str]:
        """从多角色脚本生成语音"""
        if not self.is_ready():
            return "", "Error: Model not loaded"

        try:
            # 在这里实现多角色脚本生成逻辑
            return "output_script.wav", "Script audio generated successfully"
        except Exception as e:
            logger.error(f"Script generation failed: {e}")
            return "", f"Error: {str(e)}"

    def generate_streaming(
        self,
        text: str,
        reference_audio_path: Optional[str] = None,
        **kwargs,
    ) -> Generator[Any, None, None]:
        """流式生成长文本语音"""
        if not self.is_ready():
            return

        try:
            # 在这里实现流式生成逻辑
            # for chunk in self._model.stream_generate(text):
            #     yield chunk
            pass
        except Exception as e:
            logger.error(f"Streaming failed: {e}")
```

### 步骤 2：注册引擎（懒导入）

在 `app/integrated_app/engine_interface.py` 的 `_register_builtin_engines()` 中
用**懒导入**方式注册（避免启动期强依赖，缺失时不影响其他引擎）：

```python
engine_registry.register(
    "my_new_engine",
    lazy_module=".engines.my_new_engine:MyNewEngine",  # "module:ClassName"
    display_name="My New Engine",
    vram_requirement=6.0,
    languages=["zh", "en"],
    supported_features=["clone"],
    sample_rate=24000,
    requires_gpu=True,
    quality="high",
)
```

> 真实示例见同文件中 `dotstts` 的注册。

### 步骤 3：声明引擎规格（config.yaml）

在根目录 `config.yaml` 的 `models.engines` 下声明规格（驱动显存预检与 UI 渲染）：

```yaml
models:
  engines:
    my_new_engine:
      name: "my_new_engine"
      display_name: "My New Engine"
      model_dir: "MyNewEngine"      # 相对 model/
      vram_gb: 6.0
      ram_gb: 16.0
      languages: ["zh", "en"]
      quality: "high"
      license: "Apache-2.0"
      supported_features: ["clone"]
      sample_rate: 24000
      requires_gpu: true
```

同时在 `config.py` 添加权重路径常量（供引擎自行读取）：

```python
MY_NEW_ENGINE_MODEL_PATH = os.path.join(PRETRAINED_DIR, "MyNewEngine")
```

### 步骤 4：无需修改 model_manager / model_registry

**这是与旧架构最大的区别。** 只要引擎已在 `engine_registry` 注册且实现
"无参构造 + `load()`"契约，`model_manager` 会自动：

- `_validate_engine_name` 放行已注册引擎；
- `switch_engine` / `/api/model/load` 对其调用 `_load_generic_engine`：
  `engine_registry.get(name)` 解析类 → 实例化 → `engine.load()` →
  `registry.set_engine_loaded(name, engine)`；
- `unload_model` 遍历 `registry.get_all_engine_instances()` 调用 `.unload()`；
- 切换失败时 `_rollback_engine` 通过通用路径重载。

**无需**再为 `ModelRegistry` 添加专属字段或 set/clear 方法。

### 步骤 5：生成路由（复用通用克隆端点）

通用新式引擎可直接复用引擎无关的通用克隆端点：

- `POST /api/generate/generic/clone`（`routes/generate/generic/clone.py`）
  调用 `registry.get_current_engine().generate_voice_clone(...)`，
  复用统一执行器（信号量串行、硬超时、OOM 降级、历史入库、SSE 进度）。

若需引擎特定参数/模式，可仿照 `routes/generate/voxcpm2/` 新建子包并在
`routes/generate/__init__.py` 中导入触发注册。

---

## 模型路径配置规范

### 目录结构

每个引擎的模型文件应放在 `model/` 目录下：

```
model/
├── VoxCPM2/              # VoxCPM2 模型文件
├── SenseVoiceSmall/      # ASR 模型文件
├── speech_zipenhancer/   # 音频增强模型
└── MyNewEngine/          # 新引擎模型文件
    ├── config.json       # 模型配置
    ├── model.bin         # 模型权重
    └── tokenizer/        # 分词器（如有）
```

### 配置项命名

遵循以下命名规范：

```python
{ENGINE_NAME}_MODEL_PATH          # 主模型路径
{ENGINE_NAME}_ASR_PATH            # ASR 模型路径（如有）
{ENGINE_NAME}_DENOISER_PATH       # 降噪模型路径（如有）
{ENGINE_NAME}_CONFIG_PATH         # 配置文件路径（如有）
```

---

## 开发注意事项

### GPU 内存管理

- 引擎的 `unload()` 方法必须释放所有 GPU 内存
- 使用 `torch.cuda.empty_cache()` 清理缓存
- 避免在多次生成之间保留不必要的张量

```python
def unload(self) -> None:
    if self._model is not None:
        del self._model
        self._model = None
        self._is_loaded = False
        import torch
        torch.cuda.empty_cache()
        logger.info("GPU memory cleared")
```

### 错误处理

- 所有引擎方法必须捕获异常并返回有意义的错误消息
- 不要向用户抛出未处理的异常
- 使用 logger 记录详细错误信息

### 音频格式

- 输出音频统一使用 `.wav` 格式（16-bit PCM, 22050Hz 或 24000Hz）
- 如需其他格式，在 `audio_processing.py` 中转换

### 日志记录

```python
import logging
logger = logging.getLogger("tts_multimodel")

# 记录关键操作
logger.info("Loading model...")
logger.warning("Model already loaded, skipping")
logger.error(f"Failed to load: {e}")
```

---

## 测试新引擎

### 单元测试

在 `app/test_integration.py` 中添加引擎测试：

```python
def test_my_new_engine():
    """测试新引擎"""
    from app.integrated_app.engines.my_new_engine import MyNewEngine
    
    engine = MyNewEngine(model_path="test/path", config={})
    
    # 测试加载
    engine.load()
    assert engine.is_ready()
    
    # 测试生成
    audio_path, msg = engine.generate_voice_design("Hello, world!")
    assert os.path.exists(audio_path) or msg.startswith("Error")
    
    # 测试卸载
    engine.unload()
    assert not engine.is_ready()
```

### 集成测试

1. 启动应用
2. 在 UI 中切换引擎
3. 测试语音生成功能
4. 检查 GPU 内存是否正确释放

---

## 常见问题

### Q: 引擎初始化失败怎么办？

检查以下几点：
1. 模型路径是否正确
2. 模型文件是否存在
3. 依赖库是否已安装
4. GPU 内存是否充足

### Q: 如何支持多个引擎同时加载？

当前架构设计为**单引擎模式**（节省 GPU 内存）。如需多引擎支持，可修改 `model_manager.py`：

```python
class ModelManager:
    def __init__(self):
        self.engines: Dict[str, TTSEngine] = {}
    
    def load_engine(self, engine_name: str):
        if engine_name not in self.engines:
            engine = ENGINE_REGISTRY[engine_name]()
            engine.load()
            self.engines[engine_name] = engine
```

### Q: 如何处理模型下载？

建议在引擎的 `load()` 方法中检查模型是否存在，如不存在则提供清晰的错误提示：

```python
def load(self) -> None:
    if not os.path.exists(self.model_path):
        raise FileNotFoundError(
            f"Model not found at {self.model_path}\n"
            f"Please download the model and place it in this directory.\n"
            f"See MODEL_DOWNLOAD_GUIDE.md for instructions."
        )
```

---

## 贡献指南

如需将新引擎贡献到主仓库，请确保：

1. 实现所有必需的接口方法
2. 添加单元测试
3. 更新 `README.md` 和本文档
4. 模型文件**不要**提交到 Git（使用 `.gitignore` 排除）
5. 在 PR 中说明引擎的：
   - 功能特性
   - 支持的 TTS 模型
   - 下载和配置步骤
