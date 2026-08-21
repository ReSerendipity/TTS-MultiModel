"""TTS 系统统一异常层次与错误处理模块。

本模块定义了 TTS_MultiModel 项目的完整异常继承体系，是错误处理链条的核心组件：

**架构角色**
    作为系统所有业务异常的唯一来源，提供标准化的错误编码、HTTP 状态码映射
    以及附加元数据字段，确保跨模块错误语义的一致性。

**与 middleware/error_handler 的协作**
    ``middleware/error_handler.py`` 中的全局异常处理器会捕获本模块定义的异常，
    读取 ``code`` / ``status_code`` / ``message`` 等字段，构造标准化的
    JSON 错误响应（形如 ``{"code": "...", "message": "...", "detail": {...}}``）。
    非 ``TTSError`` 子类的异常应通过 ``tts_error_handler`` 装饰器包装后再抛出。

**与 SSE 事件流的集成**
    生成过程中抛出的异常会被 ``routes/generate/*`` 路由捕获，并以
    ``SSE`` ``error`` 事件（``event: error``）推送至前端，事件 data 载荷中
    同样携带 ``code`` / ``message`` / ``status_code`` 字段，前端可据此做
    差异化提示或重试决策。

**HTTP 状态码映射约定**
    ========  ================================================================
    范围      含义与典型异常
    ========  ================================================================
    400       客户端请求错误（ValidationError / EngineNotLoadedError /
              EngineSwitchError / PersonaError 系列 / ModelSwitchError）
    404       资源不存在（PersonaNotFoundError）
    500       服务端内部错误（GenerationError / AudioProcessingError /
              CacheError / TrainingError / 未分类 TTSError）
    503       资源不可用/加载失败（ModelLoadError / InsufficientVRAMError /
              EngineLoadError）
    ========  ================================================================

所有异常均继承自 :class:`TTSError`，确保 ``code`` / ``error_code`` 别名、
``status_code`` / ``message`` 字段及构造签名的统一。
"""

import asyncio
import functools
import logging
from collections.abc import Callable
from typing import Any, TypeVar, cast

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class TTSError(Exception):
    """TTS 系统基础异常类，所有业务异常的共同父类。

    定义了统一的错误编码、HTTP 状态码与消息文本三要素，并维持
    ``error_code`` 作为 ``code`` 的别名以保障向后兼容。

    Attributes:
        message: 人类可读的错误描述文本。
        code: 机器可读的错误编码，供前端/调用方做分支判断。
        status_code: 映射到 HTTP 响应的状态码。
        error_code: ``code`` 的别名，用于兼容旧版本调用方。

    Args:
        message: 错误描述文本，默认为空串。
        code: 错误编码，默认为 ``"TTS_ERROR"``。
        status_code: HTTP 状态码，默认为 ``500``。
    """

    def __init__(
        self,
        message: str = "",
        code: str = "TTS_ERROR",
        status_code: int = 500,
    ) -> None:
        self.message: str = message
        self.code: str = code
        self.status_code: int = status_code
        self.error_code: str = code
        super().__init__(message)


class ModelLoadError(TTSError):
    """模型文件加载失败异常。

    **典型触发场景**：
    - 指定的模型 checkpoint 路径不存在或文件损坏。
    - 模型权重与当前引擎版本不兼容。
    - 模型加载过程中发生底层库报错（如 safetensors 解析失败）。

    对应 HTTP 状态码 ``503 Service Unavailable``，表示资源暂时不可用，
    调用方可在修复路径/版本后重试。

    Args:
        message: 具体加载失败原因，默认为 ``"模型加载失败"``。
    """

    def __init__(self, message: str = "模型加载失败") -> None:
        super().__init__(message, code="MODEL_LOAD_ERROR", status_code=503)


class InsufficientVRAMError(TTSError):
    """显存不足异常。

    **典型触发场景**：
    - 模型加载前预检发现可用显存小于模型大小的 1.5 倍。
    - 推理过程中触发 ``torch.cuda.OutOfMemoryError``，经
      ``gpu_utils.py`` 包装后抛出。
    - 多模型 LRU 缓存驱逐失败，总显存占用超过阈值。

    对应 HTTP 状态码 ``503``，前端可提示用户卸载其他模型、
    降低批大小或切换至 CPU/MPS 后端。

    Args:
        message: 显存不足的详细描述（含可用/需求数值），
            默认为 ``"显存不足"``。
    """

    def __init__(self, message: str = "显存不足") -> None:
        super().__init__(message, code="INSUFFICIENT_VRAM", status_code=503)


class PersonaError(TTSError):
    """音色（Persona）操作通用异常。

    **典型触发场景**：
    - 音色元数据解析失败、音频格式不支持。
    - 音色导入时文件写入权限不足。
    - 音色嵌入计算失败。

    对应 HTTP 状态码 ``400 Bad Request``，子类可根据语义覆盖
    ``status_code``（如 404）。

    Args:
        message: 操作失败的具体描述，默认为 ``"音色操作失败"``。
    """

    def __init__(self, message: str = "音色操作失败") -> None:
        super().__init__(message, code="PERSONA_ERROR", status_code=400)


class PersonaNotFoundError(PersonaError):
    """指定音色不存在异常。

    **典型触发场景**：
    - 通过 persona_id 查询时未找到对应目录或元数据文件。
    - 生成接口传入的音色名称与已注册音色列表不匹配。
    - 音色已被删除但历史记录中仍引用其 ID。

    对应 HTTP 状态码 ``404 Not Found``，继承自 :class:`PersonaError`
    以便统一的音色错误捕获逻辑仍可生效。

    Args:
        message: 详细错误信息，默认为 ``"音色不存在"``。
        persona_id: 缺失的音色标识符，便于前端定位，默认为空串。
    """

    def __init__(
        self,
        message: str = "音色不存在",
        persona_id: str = "",
    ) -> None:
        self.persona_id: str = persona_id
        super().__init__(message)
        self.code = "PERSONA_NOT_FOUND"
        self.error_code = "PERSONA_NOT_FOUND"
        self.status_code = 404


class GenerationError(TTSError):
    """语音生成流程异常。

    **典型触发场景**：
    - 推理过程中发生非显存类的运行时错误。
    - 文本预处理（分句、正则清洗）失败。
    - 后处理（响度归一化、静音裁切）异常。
    - ``tts_error_handler`` 装饰器捕获到未知异常时，统一包装为此类。

    对应 HTTP 状态码 ``500``。

    Attributes:
        engine: 报错时所使用的引擎标识（如 ``"voxcpm2"`` / ``"indextts2"``），
            便于排障。空串表示发生在通用流程中。

    Args:
        message: 具体错误描述，默认为 ``"生成失败"``。
        engine: 引擎名称，默认为空串。
    """

    def __init__(
        self,
        message: str = "生成失败",
        engine: str = "",
    ) -> None:
        self.engine: str = engine
        super().__init__(message, code="GENERATION_ERROR", status_code=500)


class EngineSwitchError(TTSError):
    """引擎切换失败异常。

    **典型触发场景**：
    - 运行时动态切换至未注册的引擎名称。
    - 切换过程中旧引擎卸载失败导致状态不一致。
    - 并发切换请求触发状态机竞态（被串行化逻辑拒绝）。

    对应 HTTP 状态码 ``400 Bad Request``。

    Args:
        message: 切换失败原因，默认为 ``"引擎切换失败"``。
    """

    def __init__(self, message: str = "引擎切换失败") -> None:
        super().__init__(message, code="ENGINE_SWITCH_ERROR", status_code=400)


class EngineLoadError(TTSError):
    """引擎初始化加载失败异常。

    **典型触发场景**：
    - 引擎所需的 Python 依赖包缺失或版本不兼容。
    - 引擎子类构造函数抛出运行时异常。
    - 引擎特定配置项缺失或格式非法。

    与 :class:`ModelLoadError` 的区别：前者针对模型权重文件，
    本异常针对引擎（Python 代码层面）本身的初始化。

    对应 HTTP 状态码 ``503``。

    Attributes:
        engine: 加载失败的引擎名称，空串表示未知。

    Args:
        message: 加载失败的详细描述，必须提供。
        engine: 引擎名称标识，默认为空串。
    """

    def __init__(
        self,
        message: str,
        engine: str = "",
    ) -> None:
        self.engine: str = engine
        super().__init__(message, code="ENGINE_LOAD_ERROR", status_code=503)


class EngineNotLoadedError(TTSError):
    """引擎尚未加载即被调用异常。

    **典型触发场景**：
    - 用户未点击"加载模型"按钮即触发生成。
    - 并发请求中前一个请求卸载了模型，后一个请求随即发起推理。
    - 配置 ``server.auto_load_model: false`` 且未显式预热。

    对应 HTTP 状态码 ``400``，前端收到后可引导用户先加载对应引擎。

    Attributes:
        engine: 被调用但未加载的引擎名称，空串表示当前默认引擎。

    Args:
        message: 提示文案，默认为 ``"引擎未加载，请先加载模型"``。
        engine: 引擎名称，默认为空串。
    """

    def __init__(
        self,
        message: str = "引擎未加载，请先加载模型",
        engine: str = "",
    ) -> None:
        self.engine: str = engine
        super().__init__(message, code="ENGINE_NOT_LOADED", status_code=400)


class ModelNotLoadedError(EngineNotLoadedError):
    """模型尚未加载即被调用异常。

    与 EngineNotLoadedError 功能相同，用于模型层面的未加载检查，
    保持与旧代码导入兼容性。
    """

    pass


class AudioProcessingError(TTSError):
    """音频后处理失败异常。

    **典型触发场景**：
    - 推理返回的张量形状异常，无法转换为 PCM。
    - 响度归一化 / VAD 裁切过程中 librosa/torchaudio 报错。
    - 音频保存到磁盘时 I/O 错误（权限、磁盘满）。

    对应 HTTP 状态码 ``500``。

    Args:
        message: 处理失败的具体原因，必须提供。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code="AUDIO_PROCESSING_ERROR", status_code=500)


class ValidationError(TTSError):
    """请求参数校验失败异常。

    **典型触发场景**：
    - 文本长度超过最大 token 限制。
    - cfg / steps / denoise 等数值超范围。
    - 传入的参考音频格式非 wav/mp3/flac。
    - 必填字段缺失或类型不匹配。

    对应 HTTP 状态码 ``400 Bad Request``。

    Attributes:
        field: 触发错误的表单/JSON 字段名，空串表示整体校验失败。
            前端可据此将错误提示绑定到对应输入控件。

    Args:
        message: 校验失败描述，必须提供。
        field: 字段名，默认为空串。
    """

    def __init__(
        self,
        message: str,
        field: str = "",
    ) -> None:
        self.field: str = field
        super().__init__(message, code="VALIDATION_ERROR", status_code=400)


class ContentSafetyError(TTSError):
    """内容安全检测拦截异常。

    **典型触发场景**：
    - 合成文本命中内容安全关键词（暴力/仇恨/自残/色情/违法犯罪/骚扰）。
    - 文本语义检测（CLIP，可选启用）判定为不安全内容。

    对应 HTTP 状态码 ``400 Bad Request``，由文本前端
    （``text_frontend.normalize_text``）在推理前抛出，
    引擎层将其向上传播，禁止不安全文本进入合成管线。

    Attributes:
        category: 命中的安全类别标识（如 ``"violence"``），空串表示未知。

    Args:
        message: 拦截原因描述，必须提供。
        category: 命中的安全类别标识，默认为空串。
    """

    def __init__(self, message: str, category: str = "") -> None:
        self.category: str = category
        super().__init__(message, code="CONTENT_SAFETY_ERROR", status_code=400)


class ModelSwitchError(TTSError):
    """模型切换失败异常。

    **典型触发场景**：
    - 在 VoxCPM2 内部切换不同尺寸/配置的模型变体失败。
    - 切换目标模型与当前引擎不兼容。
    - 切换时显存不足但未达到 :class:`InsufficientVRAMError` 阈值。

    对应 HTTP 状态码 ``400``。

    Attributes:
        from_engine: 切换前的引擎/模型标识。
        to_engine: 尝试切换到的目标引擎/模型标识。

    Args:
        message: 切换失败详细描述，必须提供。
        from_engine: 源标识，默认为空串。
        to_engine: 目标标识，默认为空串。
    """

    def __init__(
        self,
        message: str,
        from_engine: str = "",
        to_engine: str = "",
    ) -> None:
        self.from_engine: str = from_engine
        self.to_engine: str = to_engine
        super().__init__(message, code="MODEL_SWITCH_ERROR", status_code=400)


class CacheError(TTSError):
    """缓存操作失败异常。

    **典型触发场景**：
    - ``LRUCache`` / ``AdaptiveLRUCache`` 读写磁盘时 I/O 错误。
    - ``prompt_cache.py`` 中 JSON+binary 格式反序列化失败。
    - Persona 嵌入缓存校验和不匹配（文件可能被篡改）。
    - TTL 清理后台线程遇到不可恢复异常。

    对应 HTTP 状态码 ``500``。通常缓存失败不应阻断主流程，调用方
    应在捕获此异常后降级为无缓存路径执行。

    Attributes:
        cache_key: 触发错误的缓存键，空串表示整体缓存层故障。
        operation: 失败的操作类型，如 ``"get"`` / ``"set"`` / ``"evict"``。

    Args:
        message: 缓存失败详细描述，默认为 ``"缓存操作失败"``。
        cache_key: 相关缓存键，默认为空串。
        operation: 操作类型，默认为空串。
    """

    def __init__(
        self,
        message: str = "缓存操作失败",
        cache_key: str = "",
        operation: str = "",
    ) -> None:
        self.cache_key: str = cache_key
        self.operation: str = operation
        super().__init__(message, code="CACHE_ERROR", status_code=500)


class TrainingError(TTSError):
    """LoRA 训练流程异常。

    **典型触发场景**：
    - 训练数据集为空或格式不符合 ``HFVoxCPMDataset`` 要求。
    - 训练过程中 loss 为 NaN / Inf。
    - Checkpoint 保存失败（磁盘满 / 权限不足）。
    - 训练进程被手动终止但清理逻辑失败。

    对应 HTTP 状态码 ``500``。

    Attributes:
        phase: 失败发生的阶段，如 ``"data_load"`` / ``"train_step"`` /
            ``"save_checkpoint"``。空串表示未知阶段。
        run_id: 训练运行的唯一标识符，便于查日志。空串表示未关联到具体 run。

    Args:
        message: 训练失败的具体原因，默认为 ``"训练过程错误"``。
        phase: 失败阶段标识，默认为空串。
        run_id: 训练运行 ID，默认为空串。
    """

    def __init__(
        self,
        message: str = "训练过程错误",
        phase: str = "",
        run_id: str = "",
    ) -> None:
        self.phase: str = phase
        self.run_id: str = run_id
        super().__init__(message, code="TRAINING_ERROR", status_code=500)


def tts_error_handler(func: F) -> F:
    """通用错误包装装饰器，支持同步与异步函数。

    捕获目标函数中抛出的所有非 :class:`TTSError` 异常，记录完整栈追踪后
    统一包装为 :class:`GenerationError` 再抛出；已属于 :class:`TTSError`
    体系的异常则原样透传，以保留其语义与 ``status_code``。

    **使用位置**：路由层（FastAPI 端点）、生成流水线入口、CLI 命令处理器
    等最外层调用边界，避免未捕获异常直接冒泡到 ASGI / 解释器。

    Args:
        func: 被装饰的可调用对象，同步或异步均可。

    Returns:
        与原函数签名一致的包装函数，保留 ``__name__`` / ``__doc__`` 等元信息。
    """

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except TTSError:
            raise
        except Exception as e:
            logger.exception(
                "未预期的异常被 tts_error_handler 捕获，将包装为 GenerationError: %s",
                type(e).__name__,
            )
            raise GenerationError(f"未知错误: {type(e).__name__}: {e}") from e

    @functools.wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except TTSError:
            raise
        except Exception as e:
            logger.exception(
                "未预期的异常被 tts_error_handler 捕获，将包装为 GenerationError: %s",
                type(e).__name__,
            )
            raise GenerationError(f"未知错误: {type(e).__name__}: {e}") from e

    if asyncio.iscoroutinefunction(func):
        return cast(F, async_wrapper)
    return cast(F, sync_wrapper)
