# GPT-SoVITS & dots.tts 手动集成指南

> 最后更新：2026-07-31
>
> 本文档指导将 GPT-SoVITS 和 dots.tts 两个 TTS 引擎完整接入 TTS MultiModel 项目。
> 请按顺序逐步完成，每一步标注了"预期结果"用于验证。

---

## 目录

- [0. 当前状态总览](#0-当前状态总览)
- [1. 权重下载与放置（唯一手动下载步骤）](#1-权重下载与放置)
- [2. 依赖安装](#2-依赖安装)
- [3. 路径常量注册（config.py）](#3-路径常量注册configpy)
- [4. 引擎懒导入注册（engine_interface.py）](#4-引擎懒导入注册engine_interfacepy)
- [5. 通用引擎状态容器（model_registry.py）](#5-通用引擎状态容器model_registrypy)
- [6. 通用加载/切换/卸载分支（model_manager.py）](#6-通用加载切换卸载分支model_managerpy)
- [7. 加载路由适配（routes/model.py）](#7-加载路由适配routesmodelpy)
- [8. 生成路由聚合（routes/generate/__init__.py）](#8-生成路由聚合routesgenerate__init__py)
- [9. Tab 路由注册（routes/tabs.py）](#9-tab-路由注册routestabspy)
- [10. 首页 UI — 侧边栏与引擎切换按钮（base.html）](#10-首页-ui--侧边栏与引擎切换按钮basehtml)
- [11. 国际化（locales/zh.json, en.json）](#11-国际化localeszhjson-enjson)
- [12. 下载脚本（scripts/）](#12-下载脚本scripts)
- [13. 验证步骤](#13-验证步骤)
- [附录 A. 完整改动文件清单](#附录-a-完整改动文件清单)
- [附录 B. 架构说明 — 通用引擎 vs 专属引擎](#附录-b-架构说明--通用引擎-vs-专属引擎)

---

## 0. 当前状态总览

磁盘上 **已经存在** 的新文件（无需再创建）：

| 文件 | 用途 |
|------|------|
| `app/integrated_app/engines/gptsovits_engine.py` | GPT-SoVITS 进程内引擎适配器（TTSEngine 协议） |
| `app/integrated_app/engines/dotstts_engine.py` | dots.tts 进程内引擎适配器（TTSEngine 协议） |
| `app/integrated_app/routes/generate/generic/__init__.py` | 通用引擎生成路由聚合 |
| `app/integrated_app/routes/generate/generic/clone.py` | `POST /api/generate/generic/clone` 端点 |
| `app/integrated_app/templates/tabs/gptsovits_clone.html` | GPT-SoVITS 克隆 Tab 模板 |
| `app/integrated_app/templates/tabs/dotstts_clone.html` | dots.tts 克隆 Tab 模板 |
| `scripts/download_gptsovits.py` | GPT-SoVITS 权重下载脚本 |
| `scripts/download_dotstts.py` | dots.tts 权重下载脚本 |

**已有配置**：

| 文件 | 状态 |
|------|------|
| `config.yaml` → `models.engines.gptsovits` / `dotstts` | ✅ 已声明引擎规格（display_name / vram_gb / languages 等） |

**仍需手动修改的文件**（本文档的步骤 3–11 逐一覆盖）：

| 序号 | 文件 | 改动概述 |
|------|------|----------|
| 3 | `app/integrated_app/config.py` | 新增 `GPTSOVITS_MODEL_PATH` / `DOTSTTS_MODEL_PATH` |
| 4 | `app/integrated_app/engine_interface.py` | 在 `_register_builtin_engines()` 中懒导入注册两引擎 |
| 5 | `app/integrated_app/model_registry.py` | 新增通用引擎容器 `_engines` 及相关方法 |
| 6 | `app/integrated_app/model_manager.py` | 新增 `_load_generic_engine` + 通用分支 |
| 7 | `app/integrated_app/routes/model.py` | `/load` 端点支持通用引擎 |
| 8 | `app/integrated_app/routes/generate/__init__.py` | 导入 `generic` 包 |
| 9 | `app/integrated_app/routes/tabs.py` | Tab 注册 + 字符上限 + persona |
| 10 | `app/integrated_app/templates/base.html` | 模型切换按钮 + 侧边栏 |
| 11 | `app/integrated_app/locales/zh.json` + `en.json` | i18n 键值 |

---

## 1. 权重下载与放置

这是**唯一需要联网下载的操作**。

### 1.1 GPT-SoVITS

```bash
# 运行下载脚本（需已安装 modelscope）
python scripts/download_gptsovits.py

# 或手动下载后放入以下目录：
model/GPT-SoVITS/
├── *.ckpt                        # GPT 自回归模型权重
├── *.pth                         # SoVITS 声学模型权重
├── chinese-hubert-base/          # 中文 HuBERT 特征提取器
│   └── (bert 模型文件...)
└── chinese-roberta-wwm-ext-large/ # 中文 RoBERTa BERT
    └── (bert 模型文件...)
```

> **验证**：确认 `model/GPT-SoVITS/` 目录下存在 `.ckpt`、`.pth` 文件
> 以及 `chinese-hubert-base/`、`chinese-roberta-wwm-ext-large/` 两个子目录。

### 1.2 dots.tts

```bash
# 运行下载脚本
python scripts/download_dotstts.py

# 或手动下载后放入以下目录：
model/dots.tts/
└── (dots.tts-soar 权重快照文件...)
```

> **验证**：确认 `model/dots.tts/` 目录非空，包含模型权重文件。

---

## 2. 依赖安装

两个引擎有各自独立的依赖，**不能省略**。

```bash
# GPT-SoVITS 推理依赖（复用 reference_repos 中的代码）
pip install -r reference_repos/GPT-SoVITS/requirements.txt

# dots.tts 推理依赖（标准 pip 包）
pip install dots.tts
```

> **验证**：
> ```bash
> python -c "from GPT_SoVITS.TTS_infer_pack.TTS import TTS; print('GPT-SoVITS OK')"
> python -c "from dots_tts.runtime import DotsTtsRuntime; print('dots.tts OK')"
> ```
> 若任一命令报 `ModuleNotFoundError`，说明对应依赖未安装成功。

---

## 3. 路径常量注册（config.py）

**文件**：`app/integrated_app/config.py`

**操作**：在 `INDEXTTS2_MODEL_PATH` 定义之后（约第 70 行），追加以下内容：

```python
# --- GPT-SoVITS Model Paths ---
GPTSOVITS_MODEL_PATH = os.path.join(PRETRAINED_DIR, "GPT-SoVITS")

# --- dots.tts Model Paths ---
DOTSTTS_MODEL_PATH = os.path.join(PRETRAINED_DIR, "dots.tts")
```

**原因**：`engines/gptsovits_engine.py` 第 39 行和 `engines/dotstts_engine.py` 第 38 行
分别 `from ..config import GPTSOVITS_MODEL_PATH` / `DOTSTTS_MODEL_PATH`，缺少此常量会导致引擎懒导入失败。

> **验证**：运行 `python -c "from integrated_app.config import GPTSOVITS_MODEL_PATH, DOTSTTS_MODEL_PATH; print(GPTSOVITS_MODEL_PATH, DOTSTTS_MODEL_PATH)"`，应输出两个路径。

---

## 4. 引擎懒导入注册（engine_interface.py）

**文件**：`app/integrated_app/engine_interface.py`

**操作**：在 `_register_builtin_engines()` 函数中，紧接 `indextts2` 注册块之后、
`_register_builtin_engines()` 调用之前，添加两段懒导入注册：

```python
    # GPT-SoVITS - 少样本/零样本克隆引擎（纯懒导入）
    # WHY 纯懒导入：其推理依赖（GPT_SoVITS 包、权重）可能未安装/未下载，
    # 启动期直接 import 会阻断应用启动；懒导入确保缺失时不影响其他引擎。
    engine_registry.register(
        "gptsovits",
        lazy_module=".engines.gptsovits_engine:GPTSoVITSEngine",
        display_name="GPT-SoVITS",
        vram_requirement=4.0,
        languages=["zh", "en", "ja", "ko", "yue"],
        supported_features=["clone", "streaming"],
        sample_rate=32000,
        requires_gpu=True,
        quality="high",
    )

    # dots.tts - 48kHz 高保真零样本克隆引擎（纯懒导入）
    engine_registry.register(
        "dotstts",
        lazy_module=".engines.dotstts_engine:DotsTTSEngine",
        display_name="dots.tts",
        vram_requirement=8.0,
        languages=["zh", "en"],
        supported_features=["clone", "streaming"],
        sample_rate=48000,
        requires_gpu=True,
        quality="high",
    )
```

**关键参数说明**：
- `lazy_module`：格式 `"module_path:ClassName"`，路径相对于 `app/integrated_app/`。
  引擎类首次被 `engine_registry.get("gptsovits")` 调用时才触发 import。
- `vram_requirement`：显存预检基线（GB），GPT-SoVITS 约 4GB，dots.tts 约 8GB。
- `supported_features`：前端 UI 可据此显示引擎能力标签。

> **验证**：运行以下 Python 代码：
> ```python
> import sys; sys.path.insert(0, 'bin')
> from integrated_app.engine_interface import engine_registry
> print(sorted(engine_registry.list_engines()))
> # 应输出 ['dotstts', 'gptsovits', 'indextts2', 'voxcpm2']
> print(engine_registry.get_metadata('gptsovits'))
> # 应输出包含 display_name='GPT-SoVITS', vram_requirement=4.0 的字典
> ```

---

## 5. 通用引擎状态容器（model_registry.py）

**文件**：`app/integrated_app/model_registry.py`

本步骤为 `ModelRegistry` 类添加通用引擎实例管理能力，**无需修改 VoxCPM2/IndexTTS2
的专属状态字段**，新增引擎走通用容器 `_engines` 即可。

### 5.1 在 `__init__` 中添加通用容器字段

在 `self._voxcpm2_engine_instance` 之后（约第 249 行），追加：

```python
        # --- 通用引擎实例容器（新式引擎，按名称索引） ---
        # WHY: VoxCPM2/IndexTTS2 因历史原因拥有专属状态字段（_voxcpm_model / _indextts2_engine），
        # 而通过 config.yaml + engine_registry 声明式接入的新引擎（如 gptsovits、dotstts）
        # 统一存放于本字典，key 为引擎名，value 为实现 TTSEngine 协议的引擎实例。
        # 这样新增引擎无需再为 ModelRegistry 添加专属字段，实现"零改动扩展"。
        self._engines: dict[str, Any] = {}
```

### 5.2 扩展 `model_loaded` 属性

将 `model_loaded` 属性中的 return 语句改为：

```python
    @property
    def model_loaded(self) -> bool:
        with self._lock:
            return (self._voxcpm_model is not None
                    or self._indextts2_engine is not None
                    or bool(self._engines))
```

### 5.3 添加通用引擎管理方法

在 `set_indextts2_loaded` 方法之后（约第 465 行），追加以下四个方法：

```python
    def set_engine_loaded(self, name: str, instance: Any) -> None:
        """原子性设置通用新式引擎的已加载状态，并触发 SSE engine_switch 事件。"""
        with self._lock:
            self._engines[name] = instance
            self._current_engine = name
            self._current_type = name
            self._current_size = name
        self._notify_sse()

    def clear_engine(self, name: str) -> None:
        """原子性清除指定通用新式引擎的实例引用，并触发 SSE 通知。"""
        with self._lock:
            self._engines.pop(name, None)
        self._notify_sse()

    def get_engine_instance(self, name: str) -> Any:
        """获取指定名称的通用新式引擎实例（线程安全）。"""
        with self._lock:
            return self._engines.get(name)

    def get_all_engine_instances(self) -> dict[str, Any]:
        """获取所有已加载的通用新式引擎实例快照（浅拷贝）。"""
        with self._lock:
            return dict(self._engines)
```

### 5.4 在 `clear_all` 中清理通用容器

在 `clear_all` 方法的 `self._voxcpm2_engine_instance = None` 之后，追加：

```python
            self._engines.clear()
```

### 5.5 扩展 `is_engine_ready`

在 `is_engine_ready` 方法的 `elif engine == EngineName.INDEXTTS2.value` 分支之后，
`return False` 之前，追加：

```python
        # 通用新式引擎：委托引擎实例自身的 is_ready()
        if engine:
            inst = self.get_engine_instance(engine)
            if inst is not None:
                try:
                    return bool(inst.is_ready())
                except Exception:
                    return True
```

### 5.6 扩展 `get_current_engine`

在 `get_current_engine` 方法的 `return self.indextts2_engine` 之后，`return None` 之前，
追加：

```python
        # 通用新式引擎：直接返回通用容器中的实例
        current = self.current_engine
        if current:
            inst = self.get_engine_instance(current)
            if inst is not None:
                return inst
```

### 5.7 扩展 `get_current_model_info`

在 `get_current_model_info` 方法中 IndexTTS2 的 elif 分支之后，
`return {"ready": False}` 之前，追加：

```python
            elif engine and self._engines.get(engine) is not None:
                info = {
                    "engine": self._current_engine,
                    "type": self._current_type,
                    "size": self._current_size,
                    "ready": True,
                }
```

### 5.8 扩展 `switch_to` 校验

将 `switch_to` 方法中的白名单校验逻辑替换为（约第 697 行）：

```python
    def switch_to(self, engine: str) -> None:
        if engine not in EngineName._value2member_map_:
            # 通用新式引擎：允许已注册到 engine_registry 的声明式引擎名
            try:
                from .engine_interface import engine_registry
                registered = engine_registry.is_registered(engine)
            except Exception:
                registered = False
            if not registered:
                raise ValueError(f"Unknown engine: {engine!r}")
        with self._lock:
            self._current_engine = engine
        self._notify_sse()
```

> **验证**：运行以下 Python 代码：
> ```python
> import sys; sys.path.insert(0, 'bin')
> from integrated_app.model_registry import registry, ModelRegistry
> # 测试通用容器
> registry.set_engine_loaded("gptsovits", object())
> assert registry.get_engine_instance("gptsovits") is not None
> assert len(registry.get_all_engine_instances()) == 1
> registry.clear_engine("gptsovits")
> assert registry.get_engine_instance("gptsovits") is None
> print("model_registry 通用容器 OK")
> ```

---

## 6. 通用加载/切换/卸载分支（model_manager.py）

**文件**：`app/integrated_app/model_manager.py`

本步骤为 model_manager 添加对通用引擎的支持，使 `/api/model/load`、`switch_engine`、
`unload_model`、`_rollback_engine` 均能处理 gptsovits / dotstts。

### 6.1 导入 EngineLoadError

在 `exceptions` 导入语句中（约第 70 行），新增 `EngineLoadError`：

```python
from .exceptions import (
    EngineLoadError,
    EngineSwitchError,
    InsufficientVRAMError,
    TTSError,
)
```

### 6.2 泛化 `_validate_engine_name`

将 `_validate_engine_name` 函数体（约第 1179 行）替换为：

```python
    engine_name = engine_name.strip()
    if engine_name not in EngineName._value2member_map_:
        # 通用新式引擎：允许已注册到 engine_registry 的声明式引擎
        try:
            from .engine_interface import engine_registry
            registered = engine_registry.is_registered(engine_name)
        except Exception:
            registered = False
        if not registered:
            raise EngineSwitchError(f"不支持的引擎: {engine_name}")
    return engine_name
```

### 6.3 在 `unload_model` 中清理通用引擎

在 `unload_model` 函数的 "Unload IndexTTS2 engine" 代码块之后、
`_persona_embedding_cache.clear()` 之前，追加：

```python
            # Unload 通用新式引擎（gptsovits / dotstts 等）
            for gname, ginst in registry.get_all_engine_instances().items():
                if ginst is not None:
                    try:
                        ginst.unload()
                    except Exception as e:
                        logger.warning(f"{gname} 卸载失败: {e}")
                registry.clear_engine(gname)
```

### 6.4 在 `switch_engine` 中添加通用加载分支

在 `switch_engine` 函数体的"阶段 ⑤：加载新引擎"处（约第 1516 行），
在 `elif engine_name == EngineName.INDEXTTS2.value:` 分支之后，追加：

```python
            else:
                # 通用新式引擎（声明式注册）
                for status_tuple in _load_generic_engine(engine_name):
                    yield status_tuple
```

### 6.5 新增 `_load_generic_engine` 函数

在 `_load_voxcpm2_engine` 函数之后（约第 1575 行），添加整个函数：

```python
def _load_generic_engine(
    engine_name: str,
) -> Generator[tuple[str, None, None, None], None, None]:
    """加载通过 engine_registry 声明式注册的通用新式引擎（内部辅助生成器）。

    流程：
        1. 从 engine_registry 解析引擎类（触发懒导入）。
        2. 实例化（无参构造，引擎内部从 config 读取权重路径）。
        3. 调用 ``engine.load()`` 加载权重到显存/内存。
        4. 通过 ``registry.set_engine_loaded`` 注册到全局状态。
    """
    from .engine_interface import engine_registry

    _progress_mgr.update_phase("正在加载新引擎...")
    status_text: str = f"正在解析引擎 {engine_name}..."
    logger.info(f"[引擎切换] {status_text}")
    yield status_text, None, None, None

    engine_class: Any = engine_registry.get(engine_name)
    if engine_class is None:
        raise EngineLoadError(
            f"引擎 '{engine_name}' 无法解析（未注册或依赖缺失）。"
            "请确认已安装对应依赖并下载模型权重。"
        )

    status_text = f"正在加载 {engine_name} 模型..."
    logger.info(f"[引擎切换] {status_text}")
    yield status_text, None, None, None

    start_time: float = time.time()
    engine: Any = engine_class()
    engine.load()
    load_time: float = time.time() - start_time
    logger.info(f"[引擎切换] {engine_name} 加载完成，耗时 {load_time:.1f}s")

    registry.set_engine_loaded(engine_name, engine)

    # VRAM 记录（best-effort）
    try:
        from .gpu_backend import GPUBackend, GPUBackendManager
        monitor: Any = get_health_monitor()
        if GPUBackendManager.detect_backend() != GPUBackend.CPU:
            vram_mb: float = GPUBackendManager.memory_allocated() / (1024**2)
            monitor.record_vram_usage(vram_mb)
            monitor.set_model_status("ready")
    except Exception as e:
        logger.debug(f"[{engine_name}] VRAM 记录失败: {e}")

    status_text = f"{engine_name} 引擎就绪"
    logger.info(f"[引擎切换] {status_text}")
    yield status_text, None, None, None
```

### 6.6 在 `_rollback_engine` 中添加通用回滚

在 `_rollback_engine` 函数的 `elif prev_engine == EngineName.INDEXTTS2.value:` 分支之后、
`else:` 分支之前，追加：

```python
    elif prev_engine:
        # 通用新式引擎回滚：重新走声明式加载流程
        try:
            logger.info(f"[引擎切换] 回滚: 重新加载 {prev_engine} 引擎...")
            for _ in _load_generic_engine(prev_engine):
                pass
            logger.info(f"[引擎切换] 回滚: {prev_engine} 引擎重新加载完成")
        except Exception as reload_err:
            logger.error(f"[引擎切换] 回滚时重新加载 {prev_engine} 失败: {reload_err}")
```

### 6.7 在 `_rollback_engine` 中清理通用引擎引用

在 `_rollback_engine` 的"回滚前清理"块中（`registry.indextts2_engine = None` 之后），
追加：

```python
        for gname in list(registry.get_all_engine_instances().keys()):
            registry.clear_engine(gname)
```

> **验证**：运行以下 Python 代码（无需 GPU，仅验证代码路径可解析）：
> ```python
> import sys; sys.path.insert(0, 'bin')
> from integrated_app.model_manager import _validate_engine_name, _load_generic_engine
> print(_validate_engine_name('gptsovits'))  # 应输出 'gptsovits'
> print(_validate_engine_name('dotstts'))    # 应输出 'dotstts'
> print("model_manager 通用分支 OK")
> ```

---

## 7. 加载路由适配（routes/model.py）

**文件**：`app/integrated_app/routes/model.py`

**操作**：在 `load_model_endpoint` 函数中（约第 221 行），
将 `load_fn` 的赋值逻辑泛化为支持新引擎：

```python
        if engine == "indextts2":
            load_fn = load_indextts2
        elif engine == "voxcpm2":
            load_fn = load_voxcpm2
        else:
            # 通用新式引擎（gptsovits / dotstts 等）：通过 switch_engine 走声明式加载。
            def load_fn() -> Any:
                return switch_engine(engine)
```

（替换原来的 `load_fn = load_indextts2 if engine == "indextts2" else load_voxcpm2`）

> **验证**：启动应用后，在浏览器控制台执行：
> ```javascript
> fetch('/api/model/load', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded','X-CSRF-Token':getCsrfToken()}, body:'engine=gptsovits'}).then(r=>r.json()).then(d=>console.log(d))
> ```
> 应返回 `{status: "error", message: "...引擎...无法解析..."}` 而非 500 错误
> （因为权重尚未下载时会报明确错误而非崩溃）。

---

## 8. 生成路由聚合（routes/generate/__init__.py）

**文件**：`app/integrated_app/routes/generate/__init__.py`

**操作**：在文件的 import 区域追加一行：

```python
from . import generic  # noqa: F401 — 通用引擎克隆路由（gptsovits / dotstts 等）
```

同时在 `__all__` 列表中加入 `"generic"`：

```python
__all__ = ["router", "indextts2", "voxcpm2", "generic"]
```

> **验证**：启动应用后，访问 `http://127.0.0.1:7869/docs`（Swagger），
> 应能在 `/api/generate` 组下看到 `POST /generic/clone` 端点。

---

## 9. Tab 路由注册（routes/tabs.py）

**文件**：`app/integrated_app/routes/tabs.py`

### 9.1 在 `_TAB_TEMPLATES` 中添加新 Tab

在 `_TAB_TEMPLATES` 字典中追加两行：

```python
    "gptsovits_clone": "tabs/gptsovits_clone.html",
    "dotstts_clone": "tabs/dotstts_clone.html",
```

### 9.2 添加新 Tab 集合与字符上限

在 `_INDEXTTS2_TABS` 定义之后，添加：

```python
# 通用新式引擎专属 Tab（gptsovits / dotstts，字符上限 4096）
_GENERIC_ENGINE_TABS: FrozenSet[str] = frozenset(
    {"gptsovits_clone", "dotstts_clone"}
)
```

在 `_common_context` 函数的 `elif tab_name in _INDEXTTS2_TABS:` 分支之后，
`else:` 分支之前，追加：

```python
    elif tab_name in _GENERIC_ENGINE_TABS:
        engine_max_chars = 4096
```

### 9.3 在 persona 列表加载条件中添加新 Tab

将 `load_tab` 函数中 persona 列表加载条件（约第 205 行）改为：

```python
    if tab_name in {"voice_design", "voice_clone", "ultimate_clone", "voxcpm2",
                    "gptsovits_clone", "dotstts_clone"}:
```

> **验证**：启动应用后，浏览器访问：
> ```
> curl -H "HX-Request: true" http://127.0.0.1:7869/tab/gptsovits_clone
> ```
> 应返回 200 + 包含 `gsc-form` 的 HTML 片段（而非 404）。
> 同理测试 `/tab/dotstts_clone`。

---

## 10. 首页 UI — 侧边栏与引擎切换按钮（base.html）

**文件**：`app/integrated_app/templates/base.html`

### 10.1 在 `#model-tabs` 中添加两个引擎切换按钮

在 IndexTTS 2.5 按钮之后、`</div>` 之前，追加：

```html
<button class="model-tab" data-model="gptsovits" onclick="window.switchModel('gptsovits')" role="tab" aria-selected="false" title="{{ "load_model_with_shortcut"|t(lang) }}">
    <svg class="model-tab-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
    <span class="model-tab-label">GPT-SoVITS</span>
</button>
<button class="model-tab" data-model="dotstts" onclick="window.switchModel('dotstts')" role="tab" aria-selected="false" title="{{ "load_model_with_shortcut"|t(lang) }}">
    <svg class="model-tab-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>
    <span class="model-tab-label">dots.tts</span>
</button>
```

### 10.2 在侧边栏中添加两个导航区块

在 IndexTTS2 导航区块结束标签 `</div>` 之后、
`<div class="sidebar-nav-section sidebar-tools-section">` 之前，插入：

```html
<div class="sidebar-nav-section" data-section-model="gptsovits">
    <div class="sidebar-nav-label">GPT-SoVITS · {{ "core_section"|t(lang) }}</div>
    <button class="sidebar-item" data-tab="gptsovits_clone" data-model="gptsovits"
            hx-get="/tab/gptsovits_clone?lang={{ lang }}" hx-target="#tab-content" hx-swap="innerHTML" hx-trigger="click throttle:300ms" hx-indicator="#tab-loading"
            onclick="TTSApp.sidebar.activateTab(this)"
            title="{{ 'tab_gptsovits_clone'|t(lang) }}">
        <svg class="sidebar-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/><path d="M8 22h8"/></svg>
        <span class="sidebar-label-text">{{ "tab_gptsovits_clone" | t(lang) }}</span>
    </button>
</div>
<div class="sidebar-nav-section" data-section-model="dotstts">
    <div class="sidebar-nav-label">dots.tts · {{ "core_section"|t(lang) }}</div>
    <button class="sidebar-item" data-tab="dotstts_clone" data-model="dotstts"
            hx-get="/tab/dotstts_clone?lang={{ lang }}" hx-target="#tab-content" hx-swap="innerHTML" hx-trigger="click throttle:300ms" hx-indicator="#tab-loading"
            onclick="TTSApp.sidebar.activateTab(this)"
            title="{{ 'tab_dotstts_clone'|t(lang) }}">
        <svg class="sidebar-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/><path d="M8 22h8"/></svg>
        <span class="sidebar-label-text">{{ "tab_dotstts_clone" | t(lang) }}</span>
    </button>
</div>
```

> **验证**：启动应用后访问 `http://127.0.0.1:7869/`，顶部应出现
> **GPT-SoVITS** 和 **dots.tts** 两个引擎切换按钮；
> 侧边栏应出现对应导航区块。

---

## 11. 国际化（locales/zh.json, en.json）

### 11.1 zh.json

在 `app/integrated_app/locales/zh.json` 中，"tab_indextts2_clone" 行之后插入：

```json
    "tab_gptsovits_clone": "语音克隆 (GPT-SoVITS)",
    "tab_dotstts_clone": "语音克隆 (dots.tts)",
    "gptsovits_clone_desc": "上传参考音频并填写其转写文本，即可零样本克隆该音色（支持中/英/日/韩/粤）。",
    "dotstts_clone_desc": "上传约10秒参考音频并填写转写文本，48kHz 高保真零样本克隆。",
    "prompt_text_label": "参考音频转写文本",
    "prompt_text_hint": "填写参考音频对应的准确文本可显著提升克隆稳定性（可留空走纯音色克隆）",
    "generic_generate": "开始克隆生成",
```

### 11.2 en.json

在 `app/integrated_app/locales/en.json` 中，"tab_indextts2_clone" 行之后插入：

```json
    "tab_gptsovits_clone": "Voice Clone (GPT-SoVITS)",
    "tab_dotstts_clone": "Voice Clone (dots.tts)",
    "gptsovits_clone_desc": "Upload a reference audio and its transcript for zero-shot voice cloning (zh/en/ja/ko/yue).",
    "dotstts_clone_desc": "Upload ~10s reference audio and its transcript for 48kHz high-fidelity zero-shot cloning.",
    "prompt_text_label": "Reference Audio Transcript",
    "prompt_text_hint": "Providing the exact transcript of the reference audio greatly improves cloning stability (optional).",
    "generic_generate": "Generate Clone",
```

> **验证**：`python -c "import json; json.load(open('app/integrated_app/locales/zh.json',encoding='utf-8')); json.load(open('app/integrated_app/locales/en.json',encoding='utf-8')); print('JSON valid')"`

---

## 12. 下载脚本（scripts/）

两个下载脚本已创建，使用方式：

```bash
# 下载 GPT-SoVITS 权重
python scripts/download_gptsovits.py

# 下载 dots.tts 权重
python scripts/download_dotstts.py
```

两个脚本均：
- 检查 `modelscope` 是否安装，未安装时给出安装提示
- 下载到 `model/<engine>/` 目录
- 支持断点续传，重复运行不会重复下载
- 下载完成后自动校验必需文件是否存在

---

## 13. 验证步骤

完成步骤 1–11 后，按以下流程验证集成是否成功：

### 13.1 启动前验证（无需 GPU）

```bash
# 1. 引擎注册验证
python -c "
import sys; sys.path.insert(0, 'bin')
from integrated_app.engine_interface import engine_registry
engines = sorted(engine_registry.list_engines())
assert 'gptsovits' in engines, f'gptsovits not registered: {engines}'
assert 'dotstts' in engines, f'dotstts not registered: {engines}'
print('引擎注册 OK:', engines)
"

# 2. 配置路径验证
python -c "
import sys; sys.path.insert(0, 'bin')
from integrated_app.config import GPTSOVITS_MODEL_PATH, DOTSTTS_MODEL_PATH
import os
print('GPT-SoVITS 路径:', GPTSOVITS_MODEL_PATH)
print('dots.tts 路径:', DOTSTTS_MODEL_PATH)
print('路径配置 OK')
"

# 3. Tab 注册验证
python -c "
import sys; sys.path.insert(0, 'bin')
from integrated_app.routes.tabs import TAB_ALLOWLIST
assert 'gptsovits_clone' in TAB_ALLOWLIST
assert 'dotstts_clone' in TAB_ALLOWLIST
print('Tab 注册 OK')
"
```

### 13.2 启动应用后验证（浏览器）

1. 启动应用：`python app/clean_launch.py`（或 `start.bat`）
2. 访问 `http://127.0.0.1:7869/`
3. **检查顶部**：出现 GPT-SoVITS / dots.tts 引擎切换按钮
4. **点击 GPT-SoVITS 按钮**：
   - 侧边栏显示 GPT-SoVITS 克隆 Tab
   - 进度条显示加载状态
   - 若无权重/依赖：显示明确的中文错误提示（指引下载/安装）
   - 若有权重/依赖：显示"引擎就绪"
5. **点击 dots.tts 按钮**：同上
6. **打开侧边栏克隆 Tab**：
   - GPT-SoVITS Tab 显示参考音频上传 + prompt_text 输入 + 合成文本 + 语言选择
   - dots.tts Tab 显示参考音频上传 + prompt_text 输入 + 合成文本（无语言选择，自动检测）
7. **上传参考音频 + 输入文本 → 点击生成**（需要引擎就绪）：
   - 输出 WAV 文件并自动播放

### 13.3 单元测试验证

```bash
$env:TRANSFORMERS_OFFLINE="1"
$env:HF_HUB_OFFLINE="1"
$env:MODELSCOPE_OFFLINE="1"
$env:CUDA_VISIBLE_DEVICES=""

.\WPy64-312101\python\python.exe -m pytest `
    tests/test_engine_interface.py `
    tests/test_engine_switch.py `
    tests/test_config_models.py `
    tests/test_app.py `
    -q --no-header `
    -k "not gpu and not cuda and not vram" `
    -m "not integration"
```

预期：全部通过（除预先存在的 `test_tab_voice_design_returns_html_without_htmx` 外）。

---

## 附录 A. 完整改动文件清单

### 新建文件（8 个）

| 文件路径 | 说明 |
|----------|------|
| `app/integrated_app/engines/gptsovits_engine.py` | GPT-SoVITS 进程内引擎适配器 |
| `app/integrated_app/engines/dotstts_engine.py` | dots.tts 进程内引擎适配器 |
| `app/integrated_app/routes/generate/generic/__init__.py` | 通用引擎路由聚合 |
| `app/integrated_app/routes/generate/generic/clone.py` | `POST /api/generate/generic/clone` |
| `app/integrated_app/templates/tabs/gptsovits_clone.html` | GPT-SoVITS 克隆 Tab 模板 |
| `app/integrated_app/templates/tabs/dotstts_clone.html` | dots.tts 克隆 Tab 模板 |
| `scripts/download_gptsovits.py` | GPT-SoVITS 权重下载脚本 |
| `scripts/download_dotstts.py` | dots.tts 权重下载脚本 |

### 修改文件（9 个）

| 文件路径 | 改动概述 | 涉及步骤 |
|----------|----------|----------|
| `app/integrated_app/config.py` | 新增 2 个路径常量 | 3 |
| `app/integrated_app/engine_interface.py` | 懒导入注册 2 个引擎 | 4 |
| `app/integrated_app/model_registry.py` | 新增通用容器 + 5 个方法 + 4 个扩展 | 5 |
| `app/integrated_app/model_manager.py` | 新增导入 + 泛化校验 + 通用加载/卸载/回滚 | 6 |
| `app/integrated_app/routes/model.py` | `/load` 端点支持通用引擎 | 7 |
| `app/integrated_app/routes/generate/__init__.py` | 导入 generic 包 | 8 |
| `app/integrated_app/routes/tabs.py` | Tab 注册 + 字符上限 + persona | 9 |
| `app/integrated_app/templates/base.html` | 模型按钮 + 侧边栏区块 | 10 |
| `app/integrated_app/locales/zh.json` + `en.json` | 7 个 i18n 键值 | 11 |

### 已有配置（无需修改）

| 文件 | 状态 |
|------|------|
| `config.yaml` → `models.engines.gptsovits` | ✅ 已声明 |
| `config.yaml` → `models.engines.dotstts` | ✅ 已声明 |

---

## 附录 B. 架构说明 — 通用引擎 vs 专属引擎

| 维度 | VoxCPM2 / IndexTTS2（专属） | GPT-SoVITS / dots.tts（通用） |
|------|-----------------------------|------------------------------|
| 引擎类 | 在 `engines/` 下，`_init__` 中加载权重 | 在 `engines/` 下，`_init__` 轻量 + `load()` 重量加载 |
| 注册方式 | `engine_interface.py` 中立即/懒导入注册 | 同左（懒导入） |
| 调度层 | `model_manager.py` 中专属 `load_xxx()` 函数 | `_load_generic_engine()` 统一处理 |
| 状态容器 | `ModelRegistry` 中专属字段 `_voxcpm_model` 等 | `ModelRegistry._engines` 通用字典 |
| 生成路由 | 引擎专属子包（`routes/generate/voxcpm2/`） | 通用克隆端点（`routes/generate/generic/clone`） |
| UI | 专属 Tab 模板 + 侧边栏区块 | 复用通用克隆 Tab 模板 |
| 新增引擎成本 | 需改 model_manager / model_registry 多处分支 | **仅需** 引擎类 + config.yaml + engine_interface 注册 |

**推荐新引擎接入方式**：始终走通用路径（步骤 3–11），除非有引擎特定的高级参数
（如 VoxCPM2 的 LoRA、IndexTTS2 的情感向量）才需要扩展专属分支。
