"""线程安全的模型状态单例中心 —— 连接 model_manager 调度层、routes API 层与具体引擎实例之间的"状态总线"。

本模块是整个 TTS_MultiModel 应用的模型状态权威来源（Single Source of Truth），承担以下核心职责：

架构角色
--------
1. **状态总线**：作为 model_manager（调度/加载层）与 routes（API 接口层）之间的共享状态枢纽，
   所有模型加载、切换、卸载操作均通过本模块的单例对象进行状态传播。
2. **线程安全保护**：所有核心状态字段通过 ``threading.RLock`` 进行细粒度保护，
   读/写操作均通过 ``@property`` 访问器在锁内完成，保证多线程环境下的可见性与原子性。
3. **SSE 事件联动**：每次状态变更（set_* / clear_* / switch_to）后自动触发
   ``_notify_sse()`` 向 ``/api/sse/events`` 推送 ``engine_switch`` 事件，
   前端无需轮询即可感知模型状态变化。
4. **Singleton 单例实现**：采用 ``__new__`` + ``_init_done`` 双重门闩模式，
   确保全局唯一实例且避免 ``__init__`` 重复执行造成状态重置。

RLock 保护粒度
--------------
- 单字段读/写：``@property`` 访问器每次获取/释放 RLock（细粒度，低竞争）。
- 多字段批量更新：``set_voxcpm_loaded`` / ``clear_voxcpm`` 等方法在一次 RLock 持有时
  间内完成全部字段赋值（原子性，避免中间状态暴露）。
- 查询方法：``is_*_ready`` / ``get_current_model_info`` 在锁内读取所需字段后立即释放。

SSE 事件联动机制
----------------
所有会引起外部可见状态变化的公共方法（set_* / clear_* / switch_to）均在锁释放后
调用 ``_notify_sse()``，触发 ``event_bus.notify()`` 广播。前端通过 EventSource
连接 ``/api/sse/events`` 接收 ``engine_switch`` / ``status`` 类型事件并刷新 UI。

Singleton 实现方式
------------------
采用经典的双重门闩（Double-Checked Locking 的无锁变体）：
1. ``_instance`` 类属性保存唯一实例引用。
2. ``__new__`` 检查 ``_instance`` 为 None 时创建实例，后续调用直接返回已有引用。
3. ``_init_done`` 类属性作为门闩：首次 ``__init__`` 执行后置 True，后续调用直接 return。
   （仅靠 ``_instance`` 不够，因为 ``__new__`` 返回非 None 时 Python 仍会自动调用 ``__init__``。）

使用示例::

    from .model_registry import registry

    # 单字段读（线程安全）
    model = registry.voxcpm_model

    # 单字段写（线程安全）
    registry.voxcpm_model = new_model

    # 批量原子更新 + SSE 通知
    registry.set_voxcpm_loaded(model, asr=asr_model)
    registry.set_indextts2_loaded(engine)

    # 就绪查询
    if registry.is_engine_ready():
        engine = registry.get_current_engine()
【职责】模型加载状态与规格登记（EngineName 枚举、VRAM 规格、已加载标记）。【边界】不执行加载动作（动作在 model_manager）；不做 YAML 读写。

"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Any

#: 模块级日志记录器，命名空间 "tts_multimodel"
logger = logging.getLogger("tts_multimodel")


class EngineName(str, Enum):
    """引擎名称枚举，定义系统支持的所有 TTS 引擎标识符。

    继承自 ``str`` 使得枚举值可直接作为字符串使用（无需 .value 转换），
    便于与 JSON 配置、HTTP 请求参数、路由路径等字符串上下文无缝交互。

    枚举成员：
        VOXCPM2 (str): "voxcpm2" —— VoxCPM 2.x 核心多模态 TTS 引擎，
            支持语音设计、零样本克隆、终极克隆、剧本工坊、流式生成、
            Prompt 续写、LoRA 微调等全功能。
        INDEXTTS2 (str): "indextts2" —— IndexTTS 2.5 情感控制引擎，
            支持零样本克隆、8 维情感向量控制、时长控制，显存占用更低。

    使用示例::

        >>> engine = EngineName.VOXCPM2
        >>> engine == "voxcpm2"  # True，可直接与字符串比较
        >>> print(engine.value)   # "voxcpm2"
    """

    VOXCPM2 = "voxcpm2"
    INDEXTTS2 = "indextts2"


#: 引擎标识符到前端 UI 显示名称的映射字典。
#: Key 为 EngineName 枚举的 value（字符串），Value 为人类可读的显示名称，
#: 供 WebUI 引擎选择下拉框、状态提示、日志输出等场景使用。
#: 可通过 :func:`load_engine_specs_from_config` 从 config.yaml 动态覆盖。
ENGINE_DISPLAY_NAMES: dict[str, str] = {
    EngineName.VOXCPM2.value: "VoxCPM2",
    EngineName.INDEXTTS2.value: "IndexTTS 2.5",
}

#: 各引擎的基线显存需求字典（单位 GB，浮点数）。
#: Key 为引擎标识符字符串，Value 为加载该引擎所需的最小显存基线值。
#: 该值仅为模型权重本身的估算大小，实际推理时需要 1.5 倍安全裕度。
#: 默认值：voxcpm2=6.5GB，indextts2=6.0GB。
#: 可通过 :func:`load_engine_specs_from_config` 从 config.yaml 动态覆盖。
ENGINE_VRAM_REQUIREMENTS: dict[str, float] = {
    EngineName.VOXCPM2.value: 6.5,
    EngineName.INDEXTTS2.value: 6.0,
}

# --- 声明式引擎规格缓存（由 load_engine_specs_from_config 填充） ---
# 存储从 config.yaml 加载的 EngineSpecConfig 对象，key 为引擎名，value 为配置实例。
# 该缓存为模块级全局字典，避免每次查询引擎规格都重新解析配置文件。
_engine_specs: dict[str, Any] = {}


def load_engine_specs_from_config(config_models_module: Any | None = None) -> None:
    """从 config_models 的 EngineSpecConfig 加载引擎规格，填充声明式缓存。

    对齐 VoiceBox 的声明式 ModelConfig 设计：引擎元数据（显存需求、
    显示名称、支持特性等）由 config.yaml 驱动，而非硬编码。
    新增引擎只需在 config.yaml 的 models.engines 中声明即可。

    Args:
        config_models_module: 可选的 config_models 模块引用。
            若为 ``None`` 则延迟从当前包导入。主要用于测试场景下注入 Mock 模块。
    """
    global _engine_specs
    try:
        try:
            from .config import get_config

            app_config = get_config()
            if app_config and app_config.models and app_config.models.engines:
                for name, spec in app_config.models.engines.items():
                    _engine_specs[name] = spec
                    ENGINE_DISPLAY_NAMES[name] = spec.display_name or name
                    ENGINE_VRAM_REQUIREMENTS[name] = spec.vram_gb
                logger.info(
                    f"[ModelRegistry] 已从配置加载 {len(_engine_specs)} 个引擎规格: {list(_engine_specs.keys())}"
                )
            else:
                logger.debug("[ModelRegistry] 配置中未找到引擎规格，使用默认值")
        except Exception as e:
            logger.debug(f"[ModelRegistry] 加载引擎规格失败（使用默认值）: {e}")
    except Exception as e:
        logger.warning(f"[ModelRegistry] load_engine_specs_from_config 异常: {e}", exc_info=True)


def get_engine_spec(name: str) -> Any | None:
    """获取指定引擎的声明式规格配置。

    Args:
        name: 引擎名称（如 ``"voxcpm2"``、``"indextts2"``）。

    Returns:
        对应的 EngineSpecConfig 实例；若未在缓存中找到则返回 ``None``。
    """
    return _engine_specs.get(name)


class ModelRegistry:
    """线程安全的模型状态单例注册表。

    **Singleton 模式**：始终通过模块级 ``registry`` 对象或 ``ModelRegistry()``
    构造函数获取实例，两者返回同一个全局对象。

    **_lock 语义**：使用 ``threading.RLock``（可重入锁）而非普通 Lock，
    允许同一个线程在嵌套调用（如 property setter 内部调用批量方法）时
    不会自我死锁。RLock 保证同一线程可多次 acquire，计数为 0 时真正释放。

    **线程安全约定**：
    - 所有核心状态字段（``_voxcpm_model``、``_indextts2_engine``、``_current_engine`` 等）
      均为私有属性，外部代码**必须**通过对应的 ``@property`` getter/setter 访问。
    - 扩展状态字段（``voxcpm_enhancer_model``、``persona_manager`` 等）目前未加锁，
      由调用方自行保证写入时的线程安全，或通过批量方法在 ``_lock`` 保护下赋值。
    - 读多写少场景下 RLock 的性能开销可忽略。

    **批量方法原子性**：
    ``set_voxcpm_loaded``、``clear_voxcpm``、``clear_all`` 等方法在一次
    RLock 持有时间内完成全部字段赋值，保证外部线程不会观察到"部分更新"
    的中间状态（例如 model 已赋值但 current_engine 仍为旧值）。
    """

    _instance: ModelRegistry | None = None
    _init_done: bool = False

    # ------------------------------------------------------------------
    # Singleton 实现
    # ------------------------------------------------------------------

    def __new__(cls) -> ModelRegistry:
        """创建或返回 Singleton 实例（双检锁模式第一步）。

        Why 双重门闩模式：
            Python 在调用 ``__new__`` 返回非 None 实例后，**无论是否为新创建**，
            都会自动调用 ``__init__``。若仅用 ``_instance`` 判断，
            每次 ``ModelRegistry()`` 都会重复执行 ``__init__``，
            导致已初始化的状态（锁、字典字段）被重置为空。
            因此需要配合 ``_init_done`` 类属性形成第二道门闩。

        Returns:
            ModelRegistry: 全局唯一的 ModelRegistry 实例。
        """
        # Why: 只靠 _instance 不够，因为 __new__ 返回非 None 后 Python
        # 仍会自动调用 __init__，若没有 _init_done 门闩会导致状态被反复重置。
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """初始化 Singleton 实例（双检锁模式第二步，仅执行一次）。

        首次 ``ModelRegistry()`` 调用时初始化以下状态：
        - ``_lock``: 可重入锁（RLock）保护所有核心状态字段的线程安全访问
        - 核心模型状态: ``_voxcpm_model``、``_voxcpm_asr``、``_current_engine`` 等
        - IndexTTS2 状态: ``_indextts2_engine``、``_indextts2_model_path``
        - 懒实例化缓存: ``_voxcpm2_engine_instance``
        - 扩展状态: persona_manager、ffmpeg_pool、gen_tracker、progress_mgr 等

        后续调用 ``ModelRegistry()`` 时因 ``_init_done`` 为 True 直接 return，
        不会重置任何字段，保证单例状态的连续性。
        """
        # Why: 双重门闩的第二道防线 —— 即使 __new__ 每次都返回同一实例，
        # __init__ 仍会被重复调用；本标志确保初始化逻辑只执行一次。
        if ModelRegistry._init_done:
            return
        ModelRegistry._init_done = True

        self._lock: threading.RLock = threading.RLock()

        # --- Core model state (property-backed, thread-safe) ---
        self._voxcpm_model: Any = None
        self._voxcpm_asr: Any = None
        self._current_engine: str | None = None
        self._current_type: str = ""
        self._current_size: str = ""

        # --- IndexTTS 2.5 state (property-backed, thread-safe) ---
        self._indextts2_engine: Any = None
        self._indextts2_model_path: str = ""

        # --- VoxCPM2Engine 懒实例化缓存（get_current_engine 中创建，clear_voxcpm/clear_all 中清理） ---
        self._voxcpm2_engine_instance: Any = None

        # --- 通用引擎实例容器（新式引擎，按名称索引） ---
        # WHY: VoxCPM2/IndexTTS2 因历史原因拥有专属状态字段（_voxcpm_model / _indextts2_engine），
        # 而通过 config.yaml + engine_registry 声明式接入的新引擎（如 generic_tts_engine）
        # 统一存放于本字典，key 为引擎名，value 为实现 TTSEngine 协议的引擎实例。
        # 这样新增引擎无需再为 ModelRegistry 添加专属字段，实现"零改动扩展"。
        self._engines: dict[str, Any] = {}

        # --- Extended VoxCPM2 state ---
        self.voxcpm_enhancer_model: Any = None
        self.voxcpm_ultimate: bool = False
        self.voxcpm_voiceclone_enabled: bool = False
        self.voxcpm_control_enabled: bool = False

        # --- Cache & memory management ---
        self.voice_embed_cache: Any = None
        self.memory_monitor: Any = None
        self.cache_size: int = 15

        # --- Persona management ---
        self.persona_manager: Any = None
        self.persona_mode: str | None = None
        self.selected_persona: str | None = None

        # --- FFmpeg pool ---
        self.ffmpeg_pool: Any = None

        # --- Tracking & progress ---
        self.gen_tracker: Any = None
        self.progress_mgr: Any = None

    # ------------------------------------------------------------------
    # 重置（仅用于测试）
    # ------------------------------------------------------------------

    @classmethod
    def _reset(cls) -> None:
        """重置 Singleton 状态，使下一次 ``ModelRegistry()`` 调用重新初始化。

        **仅用于单元测试/集成测试场景。**

        Warning:
            生产环境严禁调用本方法。若在多线程并发运行时重置，会导致已持有
            registry 引用的线程继续操作旧实例（状态丢失），而新线程获得
            全新空实例（数据不一致），属于典型的竞态条件。
            测试框架（pytest）在 fixture teardown 中使用时，应确保所有
            生成任务已结束、无其他线程持有 registry 引用。
        """
        cls._instance = None
        cls._init_done = False

    # ------------------------------------------------------------------
    # 核心状态属性（线程安全 property 访问器）
    # ------------------------------------------------------------------

    @property
    def voxcpm_model(self) -> Any:
        """VoxCPM2 主模型实例。读/写均在 RLock 保护下完成。"""
        with self._lock:
            return self._voxcpm_model

    @voxcpm_model.setter
    def voxcpm_model(self, value: Any) -> None:
        with self._lock:
            self._voxcpm_model = value

    @property
    def voxcpm_asr(self) -> Any:
        """VoxCPM2 ASR 辅助模型实例。读/写均在 RLock 保护下完成。"""
        with self._lock:
            return self._voxcpm_asr

    @voxcpm_asr.setter
    def voxcpm_asr(self, value: Any) -> None:
        with self._lock:
            self._voxcpm_asr = value

    @property
    def indextts2_engine(self) -> Any:
        """IndexTTS 2.5 引擎实例（包含模型 + 推理管线）。读/写均在 RLock 保护下完成。"""
        with self._lock:
            return self._indextts2_engine

    @indextts2_engine.setter
    def indextts2_engine(self, value: Any) -> None:
        with self._lock:
            self._indextts2_engine = value

    @property
    def current_engine(self) -> str | None:
        """当前激活引擎名称（``"voxcpm2"`` / ``"indextts2"`` / ``None``）。"""
        with self._lock:
            return self._current_engine

    @current_engine.setter
    def current_engine(self, value: str | None) -> None:
        with self._lock:
            self._current_engine = value

    @property
    def current_type(self) -> str:
        """当前激活模型的类型标识（与 engine 同名，预留未来区分变体）。"""
        with self._lock:
            return self._current_type

    @current_type.setter
    def current_type(self, value: str) -> None:
        with self._lock:
            self._current_type = value

    @property
    def current_size(self) -> str:
        """当前激活模型的尺寸变体标识（如 ``"base"`` / ``"large"``，预留扩展）。"""
        with self._lock:
            return self._current_size

    @current_size.setter
    def current_size(self, value: str) -> None:
        with self._lock:
            self._current_size = value

    @property
    def model_loaded(self) -> bool:
        """派生只读属性：任一引擎模型实例存在时返回 ``True``。

        **含义**：本属性不代表"引擎可用于推理"（需同时 current_engine 匹配），
        仅表示"显存/内存中存在已加载的模型对象"。完整就绪判断请使用
        :meth:`is_voxcpm_ready` / :meth:`is_indextts2_ready` / :meth:`is_engine_ready`。
        """
        with self._lock:
            return self._voxcpm_model is not None or self._indextts2_engine is not None or bool(self._engines)

    # ------------------------------------------------------------------
    # SSE 通知辅助
    # ------------------------------------------------------------------

    def _notify_sse(self) -> None:
        """通知 SSE 事件总线：模型状态已变更。

        Why 在锁外调用 SSE 通知：
            ``event_bus.notify()`` 内部可能有自己的锁（队列锁、订阅者迭代锁），
            若在持有 ModelRegistry._lock 的同时进入 event_bus 的锁，而其他
            线程以相反顺序加锁（先拿 event_bus 锁再读 registry 属性拿 _lock），
            会形成经典的"锁顺序反转"死锁。因此必须先释放 _lock，再调用 SSE。
            代价是可能出现"状态已变更但 SSE 延迟推送"的短暂窗口，这是可接受的
            最终一致性模型（SSE 本身就是异步推送）。
        """
        try:
            from .routes.sse import event_bus

            event_bus.notify()
        except ImportError as e:
            # routes.sse 未导入（例如 headless 模式或测试环境未初始化路由）—— 属于预期场景，debug 级别即可
            logger.debug(f"[ModelRegistry] SSE 模块不可用，跳过通知 (ImportError): {e}")
        except Exception as e:
            # 其他未知异常（event_bus 内部错误、订阅者回调异常等）—— 需要记录堆栈便于排查
            logger.warning(f"[ModelRegistry] SSE 通知失败 (可忽略): {e}", exc_info=True)

    # ------------------------------------------------------------------
    # 批量更新辅助方法（单次 RLock 获取，原子性多字段赋值）
    # ------------------------------------------------------------------

    def set_voxcpm_loaded(
        self,
        model: Any,
        asr: Any = None,
        enhancer_model: Any = None,
        ultimate: bool = False,
        voiceclone: bool = False,
        control: bool = False,
    ) -> None:
        """原子性设置 VoxCPM2 全套已加载状态，并触发 SSE engine_switch 事件。

        在单次 RLock 持有时间内完成以下操作：
        1. 写入主模型 ``_voxcpm_model``、ASR 模型 ``_voxcpm_asr``、增强器模型。
        2. 设置特性标志位（ultimate / voiceclone / control）。
        3. 将 ``current_engine / current_type / current_size`` 全部切换为 ``"voxcpm2"``。

        Note:
            本方法**仅修改状态**，真正的模型加载/卸载由 :mod:`model_manager`
            调度层完成。状态变更后自动调用 :meth:`_notify_sse` 推送
            ``engine_switch`` 事件到前端。

        Args:
            model: 已加载的 VoxCPM2 主模型实例。
            asr: 已加载的 ASR 辅助模型实例（可选）。
            enhancer_model: 语音增强器模型实例（可选）。
            ultimate: 是否启用终极克隆模式。
            voiceclone: 是否启用语音克隆特性。
            control: 是否启用细粒度语音控制特性。
        """
        with self._lock:
            self._voxcpm_model = model
            self._voxcpm_asr = asr
            self.voxcpm_enhancer_model = enhancer_model
            self.voxcpm_ultimate = ultimate
            self.voxcpm_voiceclone_enabled = voiceclone
            self.voxcpm_control_enabled = control
            self._current_engine = EngineName.VOXCPM2.value
            self._current_type = EngineName.VOXCPM2.value
            self._current_size = EngineName.VOXCPM2.value
        self._notify_sse()

    def set_indextts2_loaded(self, engine: Any) -> None:
        """原子性设置 IndexTTS 2.5 已加载状态，并触发 SSE engine_switch 事件。

        在单次 RLock 持有时间内完成：
        1. 写入引擎实例 ``_indextts2_engine``。
        2. 将 ``current_engine / current_type / current_size`` 全部切换为 ``"indextts2"``。

        Note:
            本方法**仅修改状态**，真正的引擎加载由 :mod:`model_manager` 完成。
            状态变更后自动调用 :meth:`_notify_sse` 推送 ``engine_switch`` 事件。

        Args:
            engine: 已加载并初始化完成的 IndexTTS2Engine 实例。
        """
        with self._lock:
            self._indextts2_engine = engine
            self._current_engine = EngineName.INDEXTTS2.value
            self._current_type = EngineName.INDEXTTS2.value
            self._current_size = EngineName.INDEXTTS2.value
        self._notify_sse()

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

    def clear_voxcpm(self) -> None:
        """原子性清除所有 VoxCPM2 相关引用与标志位，并触发 SSE 通知。

        **设计意图 —— 不重置 current_engine**：
            本方法故意不修改 ``current_engine / current_type / current_size``。
            这是为了支持 :meth:`model_manager.switch_engine` 的渐进式状态迁移：
            先在后台加载新引擎 -> ``set_*_loaded`` 切换 current_engine ->
            再调用本方法清理旧引擎数据。若此处重置 current_engine 为 None，
            会导致切换过程中出现短暂的"无引擎"状态窗口，前端 UI 闪烁。
            如需完全重置，请使用 :meth:`clear_all`。
        """
        with self._lock:
            self._voxcpm_model = None
            self._voxcpm_asr = None
            self.voxcpm_enhancer_model = None
            self.voxcpm_ultimate = False
            self.voxcpm_voiceclone_enabled = False
            self.voxcpm_control_enabled = False
            self._voxcpm2_engine_instance = None
        self._notify_sse()

    def clear_indextts2(self) -> None:
        """原子性清除 IndexTTS 2.5 引擎引用，并触发 SSE 通知。

        **设计意图 —— 不重置 current_engine**：
            同 :meth:`clear_voxcpm`，为渐进式引擎切换保留 current_engine 状态。
            这样即使 IndexTTS2 被卸载，前端仍能显示"上次使用引擎为 indextts2"
            的元信息，用户点击"重新加载"时无需再次选择引擎类型。
        """
        with self._lock:
            self._indextts2_engine = None
        self._notify_sse()

    def clear_all(self) -> None:
        """原子性重置全部核心状态至默认值，并触发 SSE 通知。

        相比 :meth:`clear_voxcpm` + :meth:`clear_indextts2`，本方法额外：
        - 将 ``current_engine`` 置为 ``None``
        - 将 ``current_type`` / ``current_size`` 重置为空字符串
        """
        with self._lock:
            self._voxcpm_model = None
            self._voxcpm_asr = None
            self._indextts2_engine = None
            self._current_engine = None
            self._current_type = ""
            self._current_size = ""
            self.voxcpm_enhancer_model = None
            self.voxcpm_ultimate = False
            self.voxcpm_voiceclone_enabled = False
            self.voxcpm_control_enabled = False
            self._voxcpm2_engine_instance = None
            self._engines.clear()
        self._notify_sse()

    # ------------------------------------------------------------------
    # 就绪查询辅助方法
    # ------------------------------------------------------------------

    def is_voxcpm_ready(self) -> bool:
        """判断 VoxCPM2 引擎是否就绪可用于推理。

        **就绪标准**：
        1. ``_voxcpm_model`` 不为 ``None``（模型确实已加载到显存/内存）。
        2. ``_current_engine == "voxcpm2"``（当前激活引擎确实是 VoxCPM2）。

        两者必须同时满足，避免以下误判：
        - 模型已加载但 current_engine 已切走（处于"卸载过渡态"）。
        - current_engine 设为 voxcpm2 但模型加载失败（状态位已改但对象为空）。

        Returns:
            满足两个条件时返回 ``True``，否则 ``False``。
        """
        with self._lock:
            return self._voxcpm_model is not None and self._current_engine == EngineName.VOXCPM2.value

    def is_indextts2_ready(self) -> bool:
        """判断 IndexTTS 2.5 引擎是否就绪可用于推理。

        **就绪标准**：
        1. ``_indextts2_engine`` 不为 ``None``（引擎管线确实已初始化）。
        2. ``_current_engine == "indextts2"``（当前激活引擎确实是 IndexTTS2）。

        Returns:
            满足两个条件时返回 ``True``，否则 ``False``。
        """
        with self._lock:
            return self._indextts2_engine is not None and self._current_engine == EngineName.INDEXTTS2.value

    def is_engine_ready(self) -> bool:
        """判断当前激活引擎是否就绪。

        根据 ``current_engine`` 值分发到对应的 engine-specific 检查方法。
        若 ``current_engine`` 为 ``None`` 或未知值，直接返回 ``False``。

        Returns:
            当前引擎就绪时返回 ``True``，否则 ``False``。
        """
        with self._lock:
            engine = self._current_engine
        if engine == EngineName.VOXCPM2.value:
            return self.is_voxcpm_ready()
        elif engine == EngineName.INDEXTTS2.value:
            return self.is_indextts2_ready()
        # 通用新式引擎：委托引擎实例自身的 is_ready()
        if engine:
            inst = self.get_engine_instance(engine)
            if inst is not None:
                try:
                    return bool(inst.is_ready())
                except Exception:
                    return True
        return False

    # ------------------------------------------------------------------
    # 引擎信息与实例获取
    # ------------------------------------------------------------------

    def get_current_model_info(self) -> dict[str, Any]:
        """返回当前激活引擎的完整信息字典。

        锁内读取状态字段后立即释放，保证一致性快照；spec 字段来自
        :data:`_engine_specs` 声明式引擎规格缓存（由
        :func:`load_engine_specs_from_config` 从 config.yaml 加载填充）。

        返回结构示例（VoxCPM2）::

            {
                "engine": "voxcpm2",
                "type": "voxcpm2",
                "size": "voxcpm2",
                "ready": True,
                "is_ultimate": True,
                "voiceclone_enabled": True,
                "control_enabled": False,
                "spec": {
                    "display_name": "VoxCPM2",
                    "vram_gb": 6.5,
                    "ram_gb": 16.0,
                    ...
                }
            }

        Returns:
            信息字典。无激活引擎时返回 ``{"ready": False}``。
        """
        with self._lock:
            engine = self._current_engine
            if engine == EngineName.VOXCPM2.value and self._voxcpm_model is not None:
                info: dict[str, Any] = {
                    "engine": self._current_engine,
                    "type": self._current_type,
                    "size": self._current_size,
                    "ready": True,
                    "is_ultimate": self.voxcpm_ultimate,
                    "voiceclone_enabled": self.voxcpm_voiceclone_enabled,
                    "control_enabled": self.voxcpm_control_enabled,
                }
            elif (
                engine == EngineName.INDEXTTS2.value
                and self._indextts2_engine is not None
                or engine
                and self._engines.get(engine) is not None
            ):
                info = {
                    "engine": self._current_engine,
                    "type": self._current_type,
                    "size": self._current_size,
                    "ready": True,
                }
            else:
                return {"ready": False}

        spec = _engine_specs.get(engine)
        if spec is not None:
            info["spec"] = {
                "display_name": spec.display_name,
                "vram_gb": spec.vram_gb,
                "ram_gb": spec.ram_gb,
                "languages": spec.languages,
                "quality": spec.quality,
                "license": spec.license,
                "supported_features": spec.supported_features,
                "sample_rate": spec.sample_rate,
            }
        return info

    def get_current_engine(self) -> Any | None:
        """获取实现了 TTSEngine 协议的当前引擎实例。

        **VoxCPM2 懒实例化 + 缓存策略**：
            Why 不把 VoxCPM2Engine 作为 _voxcpm_model 的包装立即创建：
            1. **减少内存占用**：VoxCPM2Engine 对象包含大量方法引用、装饰器、
               子模块导入链，在仅需要 ASR 预热（加载模型但不进行 TTS 推理）的
               场景下，延迟创建引擎包装可节省几十 MB 的 Python 对象堆内存。
            2. **加载与实例化解耦**：model_manager.load_model 阶段专注于
               把权重搬到 GPU（最耗时的步骤），VoxCPM2Engine 的 __init__
               可以在首次真正推理时再执行，缩短"启动 -> UI 显示模型已加载"的
               感知延迟。
            3. **ASR 预热阶段可跳过**：某些部署场景只需要 ASR 功能，不需要
               TTS 引擎，懒实例化避免创建无用对象。
            实例首次创建后缓存到 ``_voxcpm2_engine_instance``，后续调用直接复用。
            ``clear_voxcpm`` / ``clear_all`` 会清理该缓存。

        Returns:
            - VoxCPM2Engine 实例（current_engine == voxcpm2 且模型已加载时）。
            - IndexTTS2 引擎实例（current_engine == indextts2 且引擎已加载时）。
            - ``None``：无激活引擎，或引擎实例创建失败。
        """
        if self.current_engine == EngineName.VOXCPM2.value and self.voxcpm_model is not None:
            if not hasattr(self, "_voxcpm2_engine_instance") or self._voxcpm2_engine_instance is None:
                try:
                    from .engines.voxcpm2.engine import VoxCPM2Engine

                    self._voxcpm2_engine_instance = VoxCPM2Engine()
                except Exception:
                    # Why logger.exception + return None：
                    # 路由层直接调用本方法获取引擎，若此处抛出未捕获异常，
                    # 会导致 HTTP 500。捕获后记录完整堆栈并返回 None，
                    # 调用方（routes / model_manager）可根据 None 做降级处理
                    # （如返回 "引擎初始化失败，请重试" 的用户友好消息）。
                    logger.exception("[ModelRegistry] VoxCPM2Engine 懒实例化失败")
                    return None
            return self._voxcpm2_engine_instance
        elif self.current_engine == EngineName.INDEXTTS2.value and self.indextts2_engine is not None:
            return self.indextts2_engine
        # 通用新式引擎：直接返回通用容器中的实例
        current = self.current_engine
        if current:
            inst = self.get_engine_instance(current)
            if inst is not None:
                return inst
        return None

    def switch_to(self, engine: str) -> None:
        """切换当前激活引擎的状态位（纯状态操作，不负责实际加载/卸载）。

        Note:
            本方法**只改状态位**，不执行任何模型加载或卸载动作。
            完整的引擎切换生命周期由 :meth:`model_manager.switch_engine`
            编排：加载新引擎 -> 验证就绪 -> 调用本方法切换 current_engine ->
            卸载旧引擎。这种分离设计使得状态注册表不依赖任何加载逻辑，
            保持单一职责。

        Args:
            engine: 目标引擎名称，必须是 :class:`EngineName` 枚举中的有效值。

        Raises:
            ValueError: 传入未知引擎名称时抛出。
        """
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

    def get_engine_display_name(self, engine: str | None = None) -> str:
        """获取指定引擎的显示名称（未指定时返回当前引擎的显示名称）。

        Args:
            engine: 引擎标识符。若为 ``None`` 则使用 :attr:`current_engine`；
                若 current_engine 也为 ``None`` 则回退到 ``"None"``。

        Returns:
            对应的人类可读名称（如 ``"VoxCPM2"``、``"IndexTTS 2.5"``）。
            未在 :data:`ENGINE_DISPLAY_NAMES` 中找到时原样返回 engine 字符串。
        """
        eng = engine or self.current_engine or ""
        return ENGINE_DISPLAY_NAMES.get(eng, eng or "None")


# ------------------------------------------------------------------
# MultiEngineRegistry — 多引擎并发管理扩展
# ------------------------------------------------------------------


class MultiEngineRegistry:
    """多引擎注册表扩展 — 支持同时加载和独立管理多个引擎实例。

    基于 ``ModelRegistry`` 的通用引擎容器（``_engines`` 字典），
    提供引擎级别的生命周期管理和状态查询。

    设计原则:
        - 不破坏现有 ``registry`` 单例的线程安全约定
        - 通用引擎容器（``_engines``）与专属字段（``_voxcpm_model`` 等）共存
        - 新增引擎只需通过 ``register_engine`` 注册，无需修改本类

    Usage::

        from .model_registry import multi_engine_registry
        multi_engine_registry.register_engine("generic_tts_engine", engine_instance)
        info = multi_engine_registry.get_engine_info("generic_tts_engine")
    """

    def __init__(self, registry_instance: ModelRegistry | None = None) -> None:
        """初始化多引擎注册表。

        Args:
            registry_instance: 可选的 ModelRegistry 实例。
                默认使用全局 ``registry`` 单例。
        """
        self._registry = registry_instance or registry

    def register_engine(self, name: str, instance: Any) -> None:
        """注册一个引擎实例到通用容器。

        等同于 ``registry.set_engine_loaded(name, instance)``，
        但语义更明确（面向多引擎管理而非引擎切换）。

        Args:
            name: 引擎名称。
            instance: 实现 TTSEngine 协议的引擎实例。
        """
        self._registry.set_engine_loaded(name, instance)

    def unregister_engine(self, name: str) -> None:
        """从通用容器中移除引擎实例。

        等同于 ``registry.clear_engine(name)``。

        Args:
            name: 要移除的引擎名称。
        """
        self._registry.clear_engine(name)

    def get_engine(self, name: str) -> Any:
        """获取指定引擎的实例。

        Args:
            name: 引擎名称。

        Returns:
            引擎实例，不存在时返回 None。
        """
        return self._registry.get_engine_instance(name)

    def get_all_engines(self) -> dict[str, Any]:
        """获取所有已注册的通用引擎实例快照。

        Returns:
            引擎名称到实例的字典（浅拷贝）。
        """
        return self._registry.get_all_engine_instances()

    def is_engine_loaded(self, name: str) -> bool:
        """检查指定引擎是否已加载。

        Args:
            name: 引擎名称。

        Returns:
            引擎已加载时返回 True。
        """
        return self._registry.get_engine_instance(name) is not None

    def get_engine_info(self, name: str) -> dict[str, Any]:
        """获取指定引擎的信息字典。

        Args:
            name: 引擎名称。

        Returns:
            引擎信息字典，包含 spec 字段（如有）。
        """
        instance = self._registry.get_engine_instance(name)
        if instance is None:
            return {"name": name, "loaded": False}
        info: dict[str, Any] = {
            "name": name,
            "loaded": True,
        }
        spec = _engine_specs.get(name)
        if spec is not None:
            info["spec"] = {
                "display_name": spec.display_name,
                "vram_gb": spec.vram_gb,
                "ram_gb": spec.ram_gb,
                "languages": spec.languages,
                "quality": spec.quality,
                "license": spec.license,
                "supported_features": spec.supported_features,
                "sample_rate": spec.sample_rate,
            }
        return info

    def get_loaded_engine_names(self) -> list[str]:
        """获取所有已加载引擎的名称列表。

        Returns:
            引擎名称列表（包括通用容器和专属字段中的引擎）。
        """
        names = list(self._registry.get_all_engine_instances().keys())
        if self._registry.voxcpm_model is not None:
            names.append(EngineName.VOXCPM2.value)
        if self._registry.indextts2_engine is not None:
            names.append(EngineName.INDEXTTS2.value)
        return names


# ------------------------------------------------------------------
# 模块级单例 —— 全局权威访问入口
# ------------------------------------------------------------------
registry = ModelRegistry()

#: 多引擎注册表单例（面向多引擎并发管理场景）
multi_engine_registry = MultiEngineRegistry()
