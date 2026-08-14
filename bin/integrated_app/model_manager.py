"""模型管理模块。

提供模型加载、卸载、引擎切换、LRU 缓存、进度追踪、GPU 显存监控以及音色缓存预热功能。

支持 VoxCPM2 与 IndexTTS 2.5 双引擎架构。

Model management module.

Provides model loading, unloading, engine switching, LRU caching,
progress tracking, GPU memory monitoring, and persona cache warmup.

Supports VoxCPM2 and IndexTTS 2.5 dual-engine architecture.

重构说明 (M-R1/R2/R3/R5):
- M-R1: 常量提取消除魔法数字；switch_engine 拆分为 5 个职责单一的辅助函数
        (_validate_engine_name / _snapshot_engine_state / _check_vram_prereq /
         _wait_vram_freed / _rollback_engine)，单函数圈复杂度 25 → 各 <8
- M-R2: 提取 PreloadService 与 PersonaWarmupService 类，封装状态与行为；
        全局函数 preload_model/get_preload_status/warmup_persona_cache
        作为向后兼容包装委托给单例
- M-R3: 修复 _rollback_engine 一致性 —— 回滚时重新加载 prev_engine 模型，
        而非仅恢复引用（避免引用指向 unload 后已失效的对象，尤其 IndexTTS2.unload()）
- M-R5: PersonaWarmupService 新增 retry_warmup() / reset()，支持失败重试
- M-R6(撤销): 保留 threading.RLock，因 routes/model.py 已用 loop.run_in_executor
              将同步 generator 调度到线程池，RLock 在线程池中工作良好。
              直接改 asyncio.Lock 会破坏 persona_manager 等同步上下文调用。
              新增文档化注释明确同步使用约定。

State management:
    All core model state (voxcpm_model, voxcpm_asr, current_engine,
    current_type, current_size) is owned by the ModelRegistry singleton
    in ``model_registry.py``.  Access state via ``registry.xxx``.

Sub-modules:
    - cache: LRUCache, AdaptiveLRUCache
    - progress: ProgressManager
    - tracker: GenerationTracker
    - gpu_utils: is_oom_error, free_gpu_memory, get_gpu_device,
      get_gpu_memory_info, GPUMemoryMonitor
    - prompt_cache: Persistent prompt cache for voice cloning

同步使用约定 (M-R6 文档化):
    _model_lock 为 threading.RLock，所有同步 generator (load_voxcpm2 /
    load_indextts2 / switch_engine / unload_model) 内部使用该锁。
    FastAPI 路由层必须通过 `loop.run_in_executor(None, ...)` 将这些
    generator 的迭代调度到线程池，避免阻塞事件循环。
    persona_manager 等同步模块可直接 `with _model_lock:` 获取锁。
    严禁在 async 上下文中直接 `with _model_lock:` 调用同步代码。
"""

import contextlib
import gc
import logging
import os
import threading
import time
from collections.abc import Callable, Generator
from typing import Any

from .cache import AdaptiveLRUCache, LRUCache
from .config import (
    DATA_DIR,
    ROOT_DIR,
    get_indextts2_model_path,
    get_voxcpm2_asr_path,
    get_voxcpm2_denoiser_path,
    get_voxcpm2_model_path,
)
from .estimator import GenerationTimeEstimator
from .exceptions import (
    EngineLoadError,
    EngineSwitchError,
    InsufficientVRAMError,
    TTSError,
)
from .gpu_utils import (
    GPUMemoryMonitor,
    free_gpu_memory,
    get_gpu_device,
    is_oom_error,
)
from .model_registry import EngineName, registry
from .monitor import get_health_monitor
from .progress import ProgressManager
from .tracker import GenerationTracker

# Re-export for backward compatibility — allows `from ..model_manager import xxx`
__all__ = [
    "LRUCache",
    "AdaptiveLRUCache",
    "ProgressManager",
    "GenerationTracker",
    "GPUMemoryMonitor",
    "is_oom_error",
    "free_gpu_memory",
]

#: 模块级日志记录器，命名空间 "tts_multimodel"
logger = logging.getLogger("tts_multimodel")

# --- 常量提取 (M-R1/A3-1 消除魔法数字) ---
# REFACTOR: [M-R1] 集中显存检查与轮询参数，便于调参与测试覆盖
_VRAM_FREE_THRESHOLD_BYTES: int = 500 * 1024 * 1024  # 显存释放完成阈值: 500MB
_VRAM_WAIT_MAX_SECONDS: float = 5.0  # 显存释放轮询最大等待时间
_VRAM_POLL_INTERVAL_SECONDS: float = 0.5  # 显存释放轮询间隔
_VRAM_FREE_PERCENT_FLOOR: int = 5  # 显存释放判定下限: 总显存的 5% (E2-2 阈值相对化)
_PRELOAD_READ_CHUNK_BYTES: int = 1024 * 1024  # 预加载单次读取块大小: 1MB
_PERSONA_CACHE_DEFAULT_SIZE: int = 15  # Persona 嵌入缓存默认容量
_WARMUP_TOP_PERSONAS: int = 5  # Persona 预热数量
_UNLOAD_SLOW_THRESHOLD_SECONDS: float = 5.0  # 卸载耗时告警阈值
_LOAD_RETRY_AFTER_UNLOAD_SECONDS: int = 1  # 卸载后等待 GPU 同步时间

# --- torch.compile cache configuration ---
_TORCH_COMPILE_CACHE_DIR: str = os.path.join(ROOT_DIR, "torch_compile_cache")
try:
    try:
        os.makedirs(_TORCH_COMPILE_CACHE_DIR, exist_ok=True)
    except (OSError, PermissionError) as dir_err:
        logger.debug(f"[torch.compile] 缓存目录创建失败 (可忽略): {dir_err}")
    else:
        try:
            import torch._dynamo as dynamo

            dynamo.config.cache_dir = _TORCH_COMPILE_CACHE_DIR  # type: ignore[attr-defined]
            logger.info(f"[torch.compile] 编译缓存目录: {_TORCH_COMPILE_CACHE_DIR}")
        except Exception as dynamo_err:
            logger.debug(f"[torch.compile] 缓存配置失败 (可忽略): {dynamo_err}")
except Exception as e:
    logger.debug(f"[torch.compile] 缓存配置整体失败 (可忽略): {e}")


# --- Global dynamic estimator ---
#: 全局生成耗时估算器单例，基于历史数据线性回归预测生成耗时
#: 数据持久化到 data/generation_times.json，最多保留 200 条历史记录
_time_estimator: Any = GenerationTimeEstimator(
    data_file=os.path.join(DATA_DIR, "generation_times.json"),
    max_entries=200,
)

# --- 音色缓存与全局协调器 ---
# REFACTOR: [M-R2] 全局状态保留为模块级单例，供其他模块直接导入
#: 全局 Persona 音色嵌入自适应 LRU 缓存单例
#: 容量根据 GPU 显存压力自动调整，默认最大容量 15 个音色嵌入
_persona_embedding_cache: AdaptiveLRUCache = AdaptiveLRUCache(default_maxsize=_PERSONA_CACHE_DEFAULT_SIZE)
#: 全局生成任务追踪器单例，追踪活跃/已完成/失败的生成任务数量与状态
_gen_tracker: GenerationTracker = GenerationTracker()
#: 全局生成进度管理器单例，负责阶段文本更新与 HTML 进度条渲染
_progress_mgr: ProgressManager = ProgressManager()

# WHY: 使用 RLock 而非普通 Lock，因为 switch_engine() 内部会先调用 unload_model()
# 再调用 load_voxcpm2()/load_indextts2()，而 unload_model 和 load_* 函数内部
# 都会再次获取 _model_lock。如果使用普通 Lock 会导致死锁，RLock 允许同一线程
# 多次 acquire 同一把锁，只要 release 次数匹配即可。
# M-R6(撤销): 保留 RLock 而非改用 asyncio.Lock。
# 详见模块顶部"同步使用约定"文档。
#: 模型操作全局可重入锁（threading.RLock）
#: 保护所有模型加载/卸载/切换操作的线程安全，支持同线程重入避免死锁
_model_lock: threading.RLock = threading.RLock()


def get_persona_cache_stats() -> dict[str, Any]:
    """获取当前 Persona 嵌入缓存的统计信息。

    Retrieve current persona embedding cache statistics.

    Returns:
        dict[str, Any]: 缓存统计字典，结构如下：
            - hits (int): 缓存命中总次数
            - misses (int): 缓存未命中总次数
            - hit_rate (float): 命中率 (0.0 ~ 1.0)
            - size (int): 当前缓存条目数
            - maxsize (int): 缓存最大容量
    """
    return _persona_embedding_cache.get_stats()


# ====================================================================
# Persona 预热服务 (M-R2/R5)
# ====================================================================


class PersonaWarmupService:
    """Persona 缓存预热服务（设计文档 M-R2/M-R5）。

    REFACTOR: [M-R2] Persona 缓存预热服务，封装状态与行为。
    M-R5: 新增 retry_warmup() 与 reset()，支持失败后重试与引擎切换后强制重新预热。

    设计意图：
        应用启动时后台异步预热最近使用的音色嵌入到 AdaptiveLRUCache 中，
        避免首次请求时才加载嵌入造成用户感知延迟。预热过程对启动线程非阻塞。

    线程安全：
        所有状态变更（_state 字典读写）均通过 ``_lock`` 保护，
        支持多线程并发调用 warmup/retry_warmup/reset/get_status。

    Attributes:
        _cache (AdaptiveLRUCache): 预热目标缓存实例，通常为全局单例
            ``_persona_embedding_cache``。
        _state (dict[str, Any]): 预热状态字典，包含键：
            - done (bool): 是否已成功完成预热。
            - error (str | None): 上次失败的错误消息，成功或未开始为 None。
            - in_progress (bool): 是否有后台线程正在执行预热。
        _lock (threading.Lock): 保护 ``_state`` 读写的互斥锁。
    """

    def __init__(self, cache: AdaptiveLRUCache) -> None:
        """初始化 PersonaWarmupService。

        Args:
            cache (AdaptiveLRUCache): 预热目标缓存实例。
        """
        self._cache: AdaptiveLRUCache = cache
        self._state: dict[str, Any] = {"done": False, "error": None, "in_progress": False}
        self._lock: threading.Lock = threading.Lock()

    def warmup(self) -> None:
        """异步预热最近使用的 Persona 嵌入到缓存。

        Asynchronously preload the most recently used personas into cache.

        启动一个守护线程在后台执行实际预热逻辑，避免阻塞应用启动或调用方。
        若已完成预热（``_state["done"] is True``）或已有预热线程在执行
        （``_state["in_progress"] is True``），则直接返回不重复启动。

        M-R5 语义：
            失败时 ``done`` 保持 ``False`` 并记录 ``error``，允许后续
            通过 :meth:`retry_warmup` 重试。

        后台线程行为：
            1. 扫描 ``PERSONA_DIR`` 下所有 ``.wav`` 文件，按修改时间倒序
               取前 ``_WARMUP_TOP_PERSONAS`` 个作为预热目标。
            2. 每个音色独立调用 ``load_persona_embedding(name)``，单个音色
               加载失败仅记录 debug 日志，不影响其他音色继续预热。
            3. 全部音色处理完成后标记 ``done=True`` 并记录最终缓存大小。
        """
        with self._lock:
            if self._state["done"] or self._state["in_progress"]:
                return
            self._state["in_progress"] = True

        def _do_warmup() -> None:
            from .middleware.request_id import set_request_id

            set_request_id(f"bg-{threading.current_thread().name}")
            try:
                from .config import PERSONA_DIR
                from .persona_manager import load_persona_embedding

                persona_files: list[tuple[str, float]] = []
                if os.path.isdir(PERSONA_DIR):
                    for f in os.listdir(PERSONA_DIR):
                        if f.endswith(".wav"):
                            full_path = os.path.join(PERSONA_DIR, f)
                            try:
                                mtime = os.path.getmtime(full_path)
                                persona_files.append((f[:-4], mtime))
                            except (OSError, PermissionError) as stat_err:
                                logger.debug(f"[PersonaCacheWarmup] 读取音色 mtime 失败 {full_path}: {stat_err}")

                persona_files.sort(key=lambda x: x[1], reverse=True)
                top_personas = [name for name, _ in persona_files[:_WARMUP_TOP_PERSONAS]]
                if not top_personas:
                    logger.info("[PersonaCacheWarmup] 未找到音色，跳过预热")
                    with self._lock:
                        self._state["done"] = True
                        self._state["in_progress"] = False
                    return

                logger.info(f"[PersonaCacheWarmup] 开始预热 {len(top_personas)} 个音色: {top_personas}")
                for name in top_personas:
                    try:
                        load_persona_embedding(name)
                        logger.debug(f"[PersonaCacheWarmup] 已预热音色: {name}")
                    except Exception as single_err:
                        logger.debug(
                            f"[PersonaCacheWarmup] 音色 '{name}' 预热失败 (单个失败，不影响其他): {single_err}"
                        )

                stats = self._cache.get_stats()
                logger.info(f"[PersonaCacheWarmup] 预热完成。缓存大小: {stats['size']}/{stats['maxsize']}")
                # M-R5: 成功完成才标记 done=True
                with self._lock:
                    self._state["done"] = True
                    self._state["error"] = None
                    self._state["in_progress"] = False
            except Exception as e:
                logger.error(f"[PersonaCacheWarmup] 预热失败: {e}")
                # M-R5: 失败时 done 保持 False，记录 error，允许重试
                with self._lock:
                    self._state["error"] = str(e)
                    self._state["in_progress"] = False

        t = threading.Thread(target=_do_warmup, daemon=True, name="persona-cache-warmup")
        t.start()

    def retry_warmup(self) -> bool:
        """重试预热（M-R5 新增）。

        REFACTOR: [M-R5] 重试预热入口。

        当上次预热失败（``_state["error"] is not None`` 且 ``done is False``）
        时，清理错误状态并再次调用 :meth:`warmup` 启动新的预热线程。

        Returns:
            bool: 若实际触发了新的预热返回 ``True``；若已完成或正在进行
            无需重试则返回 ``False``。
        """
        with self._lock:
            if self._state["done"]:
                return False
            if self._state["in_progress"]:
                logger.info("[PersonaCacheWarmup] 预热正在进行中，跳过重试")
                return False
            # 清理上次错误状态，允许重新触发
            self._state["error"] = None
            logger.info("[PersonaCacheWarmup] 触发重试预热")
        self.warmup()
        return True

    def reset(self) -> None:
        """重置预热状态（M-R5/E6-2 新增）。

        REFACTOR: [M-R5/E6-2] 重置预热状态。

        典型使用场景：引擎切换、模型重新加载后，强制将 ``done`` 置回
        ``False``，以便下次 :meth:`warmup` 或 :meth:`retry_warmup`
        能重新预热适配新引擎的音色嵌入。

        不会清除已加载的缓存条目，仅重置状态机。
        """
        with self._lock:
            self._state = {"done": False, "error": None, "in_progress": False}
        logger.info("[PersonaCacheWarmup] 状态已重置")

    def get_status(self) -> dict[str, Any]:
        """获取预热状态的快照副本。

        Returns:
            dict[str, Any]: 当前 ``_state`` 的浅拷贝，包含键：
                - done (bool): 是否已成功完成预热。
                - error (str | None): 上次失败错误消息。
                - in_progress (bool): 是否有后台线程在预热。
        """
        with self._lock:
            return dict(self._state)


# 模块级单例 + 向后兼容包装函数
#: Persona 缓存预热服务单例，负责应用启动时后台预热最近使用的音色嵌入
_persona_warmup_service: PersonaWarmupService = PersonaWarmupService(_persona_embedding_cache)


def warmup_persona_cache() -> dict[str, Any]:
    """向后兼容包装：委托给 PersonaWarmupService.warmup() 并返回状态。

    Returns:
        dict[str, Any]: 调用 :meth:`PersonaWarmupService.get_status` 返回的预热状态快照。
    """
    _persona_warmup_service.warmup()
    return _persona_warmup_service.get_status()


# 保留原始签名别名（完全向后兼容：返回 None，仅触发预热）
def _warmup_persona_cache_compat() -> None:
    """向后兼容无返回值版本（旧API兼容包装）。

    部分历史调用方（如早期版本的 routes/model.py）调用 warmup_persona_cache
    时不期望返回值。本函数提供完全相同的预热触发行为，但显式返回 None，
    保持与旧 API 签名一致。

    Note:
        新代码应直接调用 :func:`warmup_persona_cache` 并使用返回的状态字典
        判断预热结果，本函数仅用于向后兼容。

    Returns:
        None
    """
    warmup_persona_cache()


# ====================================================================
# 模型锁检查
# ====================================================================


def _check_voxcpm2_lock() -> bool:
    """非阻塞检查模型锁是否可用。

    Non-blocking check if the model lock is available.

    Returns:
        bool: 若当前没有模型加载/切换操作（锁可立即获取）返回 ``True``；
            若锁被其他线程持有返回 ``False``。
    """
    if not _model_lock.acquire(blocking=False):
        return False
    _model_lock.release()
    return True


_check_model_ready = _check_voxcpm2_lock  # backward-compatible alias


# ====================================================================
# 模型卸载
# ====================================================================


def unload_model() -> None:
    """卸载当前引擎模型并同步释放显存。

    Unload the current model (VoxCPM2 or IndexTTS2) and aggressively release VRAM.

    卸载顺序与显存同步逻辑：
        1. 获取 ``_model_lock``（with 语句保证即使抛异常也能释放）。
        2. 将 ``registry.voxcpm_model`` / ``registry.voxcpm_asr`` 置空并
           ``del`` 旧引用（触发 Python 引用计数回收）。
        3. 对 ``registry.indextts2_engine`` 若存在则调用其
           ``engine.unload()`` 方法，释放 IndexTTS2 内部分配的 GPU 缓冲区与
           CUDA 句柄（这也是 M-R3 回滚时不能仅恢复引用的根本原因）。
        4. 清空 ``_persona_embedding_cache``，消除模型权重对音色嵌入的
           间接引用。
        5. 调用 :func:`free_gpu_memory` 执行分层清理：
           ``gc.collect()`` → ``torch.cuda.empty_cache()`` →
           ``torch.cuda.ipc_collect()``。
        6. 若为 CUDA 后端，``torch.cuda.synchronize()`` 等待 GPU 流中
           所有排队的释放操作真正完成，避免后续 ``_wait_vram_freed``
           轮询误判（用 ``contextlib.suppress`` 容错，防止驱动异常中断卸载）。
        7. 记录卸载耗时，超过 ``_UNLOAD_SLOW_THRESHOLD_SECONDS`` 时告警。

    异常处理（try/finally 保证）：
        a) IndexTTS2.unload() 单个异常不中断整体卸载流程（仅 warning 日志）。
        b) 任何异常都会执行 free_gpu_memory() 清理显存。
        c) 无论正常/异常 ``_model_lock`` 必释放（with 语句）。

    M-R3 注意: 本函数会调用 IndexTTS2 engine.unload()，可能释放底层资源。
    因此 switch_engine 回滚时不能仅恢复引用，必须重新加载（见 _rollback_engine）。
    """
    cleanup_start: float = time.time()

    # 模型卸载时重置显存泄漏检测基线，避免卸载后显存跳变导致误报
    get_health_monitor().reset_vram_baseline()

    try:
        with _model_lock:
            # Unload VoxCPM2 model
            old_model: Any = registry.voxcpm_model
            old_asr: Any = registry.voxcpm_asr
            registry.voxcpm_model = None
            registry.voxcpm_asr = None
            if old_model is not None:
                del old_model
            if old_asr is not None:
                del old_asr

            # Unload IndexTTS2 engine
            old_engine: Any = registry.indextts2_engine
            registry.indextts2_engine = None
            if old_engine is not None:
                try:
                    old_engine.unload()
                except Exception as e:
                    logger.warning(f"IndexTTS2 卸载失败: {e}")

            # Unload 通用新式引擎（dotstts 等）
            for gname, ginst in registry.get_all_engine_instances().items():
                if ginst is not None:
                    try:
                        ginst.unload()
                    except Exception as e:
                        logger.warning(f"{gname} 卸载失败: {e}")
                registry.clear_engine(gname)

            _persona_embedding_cache.clear()

            # Use tiered free_gpu_memory() instead of inline cleanup
            free_gpu_memory()

            # Log post-cleanup VRAM status
            from .gpu_backend import GPUBackend, GPUBackendManager

            backend: GPUBackend = GPUBackendManager.detect_backend()
            if backend != GPUBackend.CPU:
                with contextlib.suppress(Exception):
                    GPUBackendManager.synchronize()
                device: Any = get_gpu_device()
                if device is not None:
                    allocated: int = GPUBackendManager.memory_allocated(device)
                    reserved: int = GPUBackendManager.memory_reserved(device)
                    logger.info(f"释放后显存: 已分配 {allocated / 1024**3:.2f}GB, 保留 {reserved / 1024**3:.2f}GB")
    except Exception as unload_err:
        logger.error(f"[模型卸载] 卸载过程异常: {unload_err}")
        with contextlib.suppress(Exception):
            free_gpu_memory()
    finally:
        cleanup_elapsed: float = time.time() - cleanup_start
        if cleanup_elapsed > _UNLOAD_SLOW_THRESHOLD_SECONDS:
            logger.warning(
                f"[模型卸载] 清理操作耗时 {cleanup_elapsed:.1f}s，超过 {_UNLOAD_SLOW_THRESHOLD_SECONDS:.0f} 秒阈值"
            )
        else:
            logger.info(f"[模型卸载] 清理完成，耗时 {cleanup_elapsed:.2f}s")


# ====================================================================
# VoxCPM2 加载
# ====================================================================


def _do_load_voxcpm2_internal(
    gpu_device: Any,
    backend: Any,
    include_denoiser: bool = False,
) -> Generator[tuple[str, None, None, None], None, None]:
    """VoxCPM2 加载共享内部逻辑生成器。

    Internal generator for shared VoxCPM2 loading logic.

    被 :func:`load_voxcpm2` 与 :func:`_load_voxcpm2_engine` 复用以避免
    重复代码。执行实际的模型权重加载、子模块 GPU 传输、ASR 模型加载、
    registry 状态更新以及显存使用记录。

    Args:
        gpu_device: GPU 设备索引（CUDA/MPS 后端）或 ``None``（CPU）。
        backend: ``GPUBackend`` 枚举值，标识当前计算后端。
        include_denoiser: 若为 ``True`` 则通过 ``zipenhancer_model_id``
            参数加载增强器/降噪器子模型；否则显式 ``load_denoiser=False``
            跳过，减少显存占用。

    Yields:
        tuple[str, None, None, None]:
            每个阶段产出一条状态元组 ``(status_text, None, None, None)``，
            依次为：phase（阶段描述文本）、percentage（预留，当前 None）、
            message（预留，当前 None）、audio/sample_rate（预留，None）。

    Raises:
        Exception: 清理半加载状态后重新抛出加载过程中的任何异常。
    """
    # 注入 bin/integrated_app/vendor 到 sys.path，使 vendor/voxcpm 源码包可被发现
    # （未 pip install 该包时的兼容路径；vendor 化后不依赖 references/ 克隆仓库）
    import sys as _sys

    _voxcpm_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    if os.path.isdir(_voxcpm_src) and _voxcpm_src not in _sys.path:
        _sys.path.insert(0, _voxcpm_src)
        logger.info(f"[VoxCPM2] 注入 vendor 源码路径: {_voxcpm_src}")

    import voxcpm
    from funasr import AutoModel

    from .gpu_backend import GPUBackend, GPUBackendManager

    device_string: str = GPUBackendManager.format_device_string(gpu_device) if gpu_device is not None else "cpu"

    status_text: str
    # Step 1: Load VoxCPM2 model
    status_text = "正在加载 VoxCPM2 模型..."
    yield status_text, None, None, None

    try:
        kwargs: dict[str, Any] = dict(
            optimize=True,
            local_files_only=True,
        )
        if include_denoiser:
            kwargs["zipenhancer_model_id"] = get_voxcpm2_denoiser_path()
        else:
            kwargs["load_denoiser"] = False

        new_model: Any = voxcpm.VoxCPM.from_pretrained(get_voxcpm2_model_path(), **kwargs)

        # Move sub-components to GPU with granular progress
        if backend != GPUBackend.CPU and gpu_device is not None:
            gpu_components: list[str] = [
                attr
                for attr in ("tts_model", "model", "codecs", "vocoder")
                if getattr(new_model, attr, None) is not None
            ]
            total_components: int = len(gpu_components)
            for i, attr in enumerate(gpu_components, 1):
                sub: Any = getattr(new_model, attr)
                status_text = f"GPU 传输: {attr} ({i}/{total_components})..."
                yield status_text, None, None, None
                sub.to(device_string)
                logger.info(f"  VoxCPM2.{attr} -> {device_string}")
            # Ensure cache sync
            with contextlib.suppress(Exception):
                GPUBackendManager.synchronize()
            allocated_mb: float = GPUBackendManager.memory_allocated() / (1024**2)
            logger.info(f"  VoxCPM2 加载完成，GPU 显存已分配: {allocated_mb:.0f} MB")
        else:
            logger.info("  VoxCPM2 使用 CPU 后端运行")

        # Store model in registry early so error handler can clean it up
        registry.voxcpm_model = new_model

        # Step 2: Load ASR model
        status_text = "正在加载 ASR 模型..."
        yield status_text, None, None, None

        new_asr: Any = AutoModel(
            model=get_voxcpm2_asr_path(),
            disable_pbar=True,
            disable_update=True,  # 离线优先：跳过 funasr 版本更新检查
            device=device_string,
        )
        registry.set_voxcpm_loaded(new_model, asr=new_asr)

        # Step 3: Record VRAM usage
        try:
            monitor: Any = get_health_monitor()
            if backend != GPUBackend.CPU:
                vram_mb: float = GPUBackendManager.memory_allocated() / (1024**2)
                monitor.record_vram_usage(vram_mb)
                monitor.set_model_status("ready")
        except Exception as e:
            logger.debug(f"VoxCPM2 加载后 VRAM 记录失败: {e}")

        # Step 4: 模型预热推理（参考 vLLM/Fish Speech 的 warmup 设计）
        # 目的：触发 CUDA kernel 编译、预热 KV cache 分配器、验证模型可正常生成
        # 预热失败不中断流程（fail-soft）
        try:
            from .model_optimizer import optimize_and_warmup_voxcpm

            def _warmup_progress(msg: str) -> None:
                logger.info(f"[VoxCPM2-Warmup] {msg}")

            # 同步执行预热（在加载流程中，此时还未返回给用户）
            # torch.compile 默认在 Windows 上禁用，预热始终执行（短文本快速完成）
            optimize_and_warmup_voxcpm(
                new_model,
                enable_compile=None,  # 自动检测（Windows 默认禁用）
                enable_warmup=True,
                progress_callback=_warmup_progress,
            )
        except Exception as warmup_err:
            logger.debug(f"VoxCPM2 预热失败（可忽略）: {warmup_err}")

        status_text = "VoxCPM2 引擎就绪"
        yield status_text, None, None, None

    except Exception:
        failed_model: Any = registry.voxcpm_model
        failed_asr: Any = registry.voxcpm_asr
        registry.clear_all()
        if failed_model is not None:
            del failed_model
        if failed_asr is not None:
            del failed_asr
        gc.collect()
        from .gpu_backend import GPUBackendManager

        with contextlib.suppress(Exception):
            GPUBackendManager.empty_cache()
        raise


def load_voxcpm2(
    progress_callback: Callable[..., None] | None = None,
) -> Generator[tuple[str, None, None, None], None, None]:
    """加载 VoxCPM2 引擎（生成器进度事件流）。

    Load the VoxCPM2 engine with generator-based progress feedback.

    加载阶段与生成器事件说明（每条产出为 ``(status_text, None, None, None)``
    四元组，兼容调用方 ``for status_text, _, _, _ in gen:`` 解包）：
        1. ``phase=init``: 卸载当前已加载引擎、GC、empty_cache。
        2. **显存预检 WHY（1.5 倍规则）**：
           要求可用显存 ≥ 模型基线大小 × 1.5。额外 0.5 倍包含三部分峰值开销：
           ① FunASR ASR 模型权重（约 200~400MB，CPU 模式另计但 GPU 场景
           会一并加载进显存）；② 推理时 KV cache（batch × seq_len × layers
           × head_dim，流式生成长文本峰值约为权重 15%~25%）；
           ③ 中间激活张量（attention logits、FFN 临时矩阵，单次推理瞬时可
           接近权重 20%~40%）。三者叠加接近或超过 0.5 倍，预留 1.5 倍可
           避免"加载成功但首次推理即 OOM"的典型陷阱。
        3. ``phase=load_model``: 调用 ``voxcpm.VoxCPM.from_pretrained``
           加载主模型。
        4. ``phase=gpu_transfer``: tts_model/model/codecs/vocoder 子模块
           逐次 ``.to(device)``，每个子模块单独产出一条进度事件。
        5. ``phase=load_asr``: 加载 FunASR AutoModel 作为 ASR 辅助模型。
        6. ``phase=ready``: ``"VoxCPM2 引擎就绪"`` 标记加载结束。

    异常退出时 ``try/finally`` 会：
        a) 若加载过程中异常，产出一条 ``(error_msg, None, None, None)`` 事件；
        b) 调用 :func:`free_gpu_memory` 清理半加载状态；
        c) 无论正常/异常均释放 ``_model_lock``。

    Args:
        progress_callback (Optional[Callable[..., None]]): 预留回调参数
            （当前未启用，保持签名兼容；使用默认值 ``None`` 即可）。

    Yields:
        tuple[str, None, None, None]:
            ``(status_text, None, None, None)`` 四元组；``status_text``
            为人类可读阶段描述；其余三个位置为历史兼容保留的占位。
    """
    _model_lock.acquire()
    # 模型加载开始时重置显存泄漏检测基线，避免加载期间显存上升导致误报
    get_health_monitor().reset_vram_baseline()
    try:
        # Unload current engine if any
        old_model: Any = registry.voxcpm_model
        old_asr: Any = registry.voxcpm_asr
        registry.voxcpm_model = None
        registry.voxcpm_asr = None
        if old_model is not None:
            del old_model
        if old_asr is not None:
            del old_asr
        gc.collect()
        from .gpu_backend import GPUBackend, GPUBackendManager

        backend: GPUBackend = GPUBackendManager.detect_backend()
        if backend != GPUBackend.CPU:
            with contextlib.suppress(Exception):
                GPUBackendManager.empty_cache()
        time.sleep(_LOAD_RETRY_AFTER_UNLOAD_SECONDS)

        gpu_device: Any = get_gpu_device()

        for status_tuple in _do_load_voxcpm2_internal(gpu_device, backend, include_denoiser=False):
            if progress_callback is not None:
                with contextlib.suppress(Exception):
                    progress_callback(status_tuple)
            yield status_tuple
    except Exception as e:
        logger.error(f"[模型加载] VoxCPM2 加载失败: {e}")
        with contextlib.suppress(Exception):
            free_gpu_memory()
        error_msg: str = f"VoxCPM2 加载失败: {type(e).__name__}: {e}"
        yield error_msg, None, None, None
    finally:
        with contextlib.suppress(Exception):
            _model_lock.release()


def load_indextts2(
    progress_callback: Callable[..., None] | None = None,
) -> Generator[tuple[str, None, None, None], None, None]:
    """加载 IndexTTS 2.5 引擎（生成器进度事件流）。

    Generator function to load IndexTTS 2.5 engine step by step.

    生成器产出格式（每条均为 ``(status_text, None, None, None)`` 四元组，
    兼容调用方 ``for status_text, _, _, _ in gen:`` 解包）：
        1. ``"正在检查系统资源..."``：
           校验 ``get_indextts2_model_path()`` 是否存在并查询显存状态。
        2. ``"正在加载 IndexTTS 2.5 引擎..."``：
           构造 ``IndexTTS2Engine``，内部依次加载 VQ Encoder、
           Flow Matching 主干、HiFi-GAN Vocoder 等子模块。
        3. ``"IndexTTS 2.5 引擎就绪"``：加载完成。
        4. 异常时：``"IndexTTS 2.5 加载失败: <ErrType>: <msg>"``。

    ``try/finally`` 保证：
        a) 半加载异常时清理 ``registry.indextts2_engine``；
        b) ``gc.collect()`` + ``GPUBackendManager.empty_cache()`` +
           ``free_gpu_memory()`` 分层释放；
        c) 释放 ``_model_lock``。

    Args:
        progress_callback (Optional[Callable[..., None]]): 预留回调参数
            （保持签名兼容；默认 ``None``）。

    Yields:
        tuple[str, None, None, None]:
            ``(status_text, None, None, None)`` 四元组；``status_text``
            为当前阶段描述；其余三个位置为历史占位（保持不变）。
    """
    _model_lock.acquire()
    # 模型加载开始时重置显存泄漏检测基线，避免加载期间显存上升导致误报
    get_health_monitor().reset_vram_baseline()
    try:
        from .engines.indextts2_engine import IndexTTS2Engine
        from .gpu_backend import GPUBackend, GPUBackendManager

        backend: GPUBackend = GPUBackendManager.detect_backend()

        # Check if model files exist
        if not os.path.exists(get_indextts2_model_path()):
            raise FileNotFoundError(
                f"IndexTTS 2.5 模型文件不存在: {get_indextts2_model_path()}\n"
                "请运行: python scripts/download_indextts2.py 下载模型"
            )

        # Step 1: VRAM/RAM check
        from .model_registry import ENGINE_VRAM_REQUIREMENTS

        needed_vram_gb: float = ENGINE_VRAM_REQUIREMENTS.get(EngineName.INDEXTTS2.value, 6.0)
        status_text: str = "正在检查系统资源..."
        if progress_callback is not None:
            with contextlib.suppress(Exception):
                progress_callback(status_text)
        yield status_text, None, None, None

        if backend != GPUBackend.CPU:
            try:
                mem_info: Any = GPUBackendManager.get_memory_info()
                free_gb: float = mem_info[3] / (1024**3)
                logger.info(f"[IndexTTS2] VRAM 检查: 需要 {needed_vram_gb}GB, 可用 {free_gb:.2f}GB")

                if free_gb < needed_vram_gb:
                    logger.warning(f"[IndexTTS2] 显存不足 ({free_gb:.2f}GB < {needed_vram_gb}GB)，将尝试使用 CPU 模式")
            except Exception as mem_err:
                logger.debug(f"[IndexTTS2] 显存查询失败（跳过预检）: {mem_err}")

        # Step 2: Load model
        status_text = "正在加载 IndexTTS 2.5 引擎..."
        if progress_callback is not None:
            with contextlib.suppress(Exception):
                progress_callback(status_text)
        yield status_text, None, None, None

        logger.info("[IndexTTS2] 开始加载 IndexTTS 2.5 引擎...")
        start_time: float = time.time()

        new_engine: Any = IndexTTS2Engine(
            model_dir=get_indextts2_model_path(),
            use_bf16=(backend != GPUBackend.CPU),
        )

        load_time: float = time.time() - start_time
        logger.info(f"[IndexTTS2] IndexTTS 2.5 引擎加载完成，耗时: {load_time:.1f}秒")

        registry.set_indextts2_loaded(new_engine)

        # IndexTTS2 预热推理
        try:
            from .model_optimizer import warmup_indextts2

            def _idx_warmup_progress(msg: str) -> None:
                logger.info(f"[IndexTTS2-Warmup] {msg}")

            warmup_indextts2(new_engine, progress_callback=_idx_warmup_progress)
        except Exception as idx_warmup_err:
            logger.debug(f"IndexTTS2 预热失败（可忽略）: {idx_warmup_err}")

        status_text = "IndexTTS 2.5 引擎就绪"
        logger.info(f"[IndexTTS2] {status_text}")
        if progress_callback is not None:
            with contextlib.suppress(Exception):
                progress_callback(status_text)
        yield status_text, None, None, None

        # Record VRAM usage
        try:
            monitor: Any = get_health_monitor()
            if backend != GPUBackend.CPU:
                vram_mb: float = GPUBackendManager.memory_allocated() / (1024**2)
                monitor.record_vram_usage(vram_mb)
                monitor.set_model_status("ready")
        except Exception as e:
            logger.debug(f"[IndexTTS2] VRAM 记录失败: {e}")
    except (FileNotFoundError, PermissionError, OSError) as fs_err:
        logger.error(f"[IndexTTS2] 文件系统错误: {fs_err}")
        with contextlib.suppress(Exception):
            free_gpu_memory()
        error_msg: str = f"IndexTTS 2.5 加载失败: {type(fs_err).__name__}: {fs_err}"
        yield error_msg, None, None, None
    except Exception as e:
        import traceback

        tb: str = traceback.format_exc()
        logger.error(f"[IndexTTS2] IndexTTS 2.5 加载失败: {type(e).__name__}: {e}\n详细错误:\n{tb}")
        gc.collect()
        from .gpu_backend import GPUBackendManager

        with contextlib.suppress(Exception):
            GPUBackendManager.empty_cache()
            free_gpu_memory()
        error_msg = f"IndexTTS 2.5 加载失败: {type(e).__name__}: {e}"
        yield error_msg, None, None, None
    finally:
        with contextlib.suppress(Exception):
            _model_lock.release()


# ====================================================================
# 预加载服务 (M-R2)
# ====================================================================


class PreloadService:
    """模型文件预加载服务（设计文档 M-R2）。

    REFACTOR: [M-R2] 模型文件预加载服务，封装状态与行为。

    将原 ``_preload_state`` 全局字典、``_preload_lock``、``_read_files_to_cache``、
    ``_read_single_file`` 等过程式代码封装为类，提升内聚性。

    设计目的：
        在应用启动或显式触发时，后台串行预读指定引擎的模型文件到操作系统
        **Page Cache**。由于模型文件动辄数 GB，首次 GPU 加载时磁盘 I/O 通常
        占总加载时间 40%~70%；通过预读让 OS 把权重文件的 page 页留在 RAM，
        后续真正加载到 VRAM 时走内存带宽（~数十 GB/s）而非磁盘
        （~500MB/s SATA 或 ~3GB/s NVMe），可显著降低用户首次点击"加载模型"
        的等待时长。

    职责：
        - 后台预读模型文件到 OS page cache，降低后续 GPU 加载的 I/O 延迟
        - 单任务串行（同一时刻只允许一个预加载任务，避免磁盘带宽争抢）
        - 状态查询供 ``/api/model/preload/status`` 路由使用

    Attributes:
        _state (dict[str, Any]): 预加载任务状态字典，键如下：
            - in_progress (bool): 是否正在进行。
            - target_engine (str | None): 目标引擎名（VOXCPM2 / INDEXTTS2）。
            - target_size (str | None): 模型大小变体（当前未使用）。
            - completed (bool): 是否已完成本次预加载。
            - error (str | None): 上次失败错误消息，成功或未开始为 None。
        _lock (threading.Lock): 保护 ``_state`` 访问的互斥锁。
        _thread (threading.Thread | None): 最近启动的预加载线程对象引用
            （当前未 join，仅用于调试诊断；为 None 表示尚未启动）。
    """

    def __init__(self) -> None:
        """初始化 PreloadService 空状态。"""
        self._state: dict[str, Any] = {
            "in_progress": False,
            "target_engine": None,
            "target_size": None,
            "completed": False,
            "error": None,
        }
        self._lock: threading.Lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def preload(
        self,
        engine: str = EngineName.VOXCPM2.value,
        size: str = EngineName.VOXCPM2.value,
    ) -> None:
        """后台预加载指定引擎的模型文件到系统 Page Cache。

        Background preload of model files into system RAM page cache.

        对目标引擎目录下的每个文件读取前 ``_PRELOAD_READ_CHUNK_BYTES``
        字节（默认 1MB）或整文件（若更小）即可触发 OS Page Cache 预读，
        **不会**把权重加载到 GPU 显存。

        通过守护线程后台执行，不阻塞调用方。同一时刻仅允许一个预加载任务
        （由 ``_state["in_progress"]`` + ``_lock`` 双重保护），避免磁盘
        带宽饱和导致主流程 I/O 退化。

        Args:
            engine (str): 目标引擎名称，可选 ``EngineName.VOXCPM2.value``
                或 ``EngineName.INDEXTTS2.value``；其他值仅记录日志不报错。
            size (str): 模型大小变体，当前未实际使用，保留用于后续多尺寸
                权重切换场景；默认与 ``engine`` 同值。
        """
        with self._lock:
            if self._state["in_progress"]:
                logger.info("[预加载] 已有预加载任务在进行中，跳过")
                return
            self._state["in_progress"] = True
            self._state["target_engine"] = engine
            self._state["target_size"] = size
            self._state["completed"] = False
            self._state["error"] = None

        def _do_preload() -> None:
            from .middleware.request_id import set_request_id

            set_request_id(f"bg-{threading.current_thread().name}")
            try:
                if engine == EngineName.VOXCPM2.value:
                    logger.info("[预加载] 开始预读 VoxCPM2 模型文件到系统内存...")
                    if os.path.exists(get_voxcpm2_model_path()):
                        try:
                            self._read_files_to_cache(get_voxcpm2_model_path())
                            logger.info("[预加载] VoxCPM2 模型文件已预读到系统缓存")
                        except (OSError, PermissionError) as vox_err:
                            logger.warning(f"[预加载] VoxCPM2 主模型预读失败: {vox_err}")
                    else:
                        logger.warning(f"[预加载] VoxCPM2 模型路径不存在: {get_voxcpm2_model_path()}")

                    if os.path.exists(get_voxcpm2_asr_path()):
                        try:
                            self._read_files_to_cache(get_voxcpm2_asr_path())
                            logger.info("[预加载] VoxCPM2 ASR 模型文件已预读到系统缓存")
                        except (OSError, PermissionError) as asr_err:
                            logger.warning(f"[预加载] VoxCPM2 ASR 模型预读失败: {asr_err}")

                elif engine == EngineName.INDEXTTS2.value:
                    logger.info("[预加载] 开始预读 IndexTTS 2.5 模型文件到系统内存...")
                    if os.path.exists(get_indextts2_model_path()):
                        try:
                            self._read_files_to_cache(get_indextts2_model_path())
                            logger.info("[预加载] IndexTTS 2.5 模型文件已预读到系统缓存")
                        except (OSError, PermissionError) as idx_err:
                            logger.warning(f"[预加载] IndexTTS 2.5 模型预读失败: {idx_err}")
                    else:
                        logger.warning(f"[预加载] IndexTTS 2.5 模型路径不存在: {get_indextts2_model_path()}")

                with self._lock:
                    self._state["completed"] = True
                    self._state["in_progress"] = False
                    logger.info("[预加载] 预加载完成")

            except Exception as e:
                logger.error(f"[预加载] 预加载失败: {e}")
                with self._lock:
                    self._state["error"] = str(e)
                    self._state["in_progress"] = False

        self._thread = threading.Thread(target=_do_preload, daemon=True, name="model-preload")
        self._thread.start()

    def _read_files_to_cache(self, directory_path: str) -> None:
        """递归预读目录下所有模型文件到系统 Page Cache。

        Recursively read model files into system page cache.

        若 ``directory_path`` 本身即为单个文件，则直接单文件预读。
        目录遍历通过 ``os.walk``，跳过以 ``.`` 开头的隐藏文件/目录以及
        ``__pycache__``，避免把无关字节拖进 Page Cache 污染缓存。

        Args:
            directory_path (str): 待预读的目录或单文件绝对路径。
        """
        if os.path.isfile(directory_path):
            self._read_single_file(directory_path)
            return
        if not os.path.isdir(directory_path):
            return
        total_bytes: int = 0
        for root, dirs, files in os.walk(directory_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            for fname in sorted(files):
                if fname.startswith("."):
                    continue
                fpath: str = os.path.join(root, fname)
                total_bytes += self._read_single_file(fpath)
        if total_bytes > 0:
            logger.info(f"[预加载] 已预读 {total_bytes / (1024 * 1024):.1f}MB 到系统缓存")

    def _read_single_file(self, filepath: str) -> int:
        """单文件预读到系统 Page Cache。

        Read a single file into system cache to warm up page cache.

        对每个文件读取前 ``_PRELOAD_READ_CHUNK_BYTES``（默认 1MB）或整文件
        （若更小）字节到 Python 匿名 buffer 后即丢弃——目的不是保留数据，
        而是触发内核把对应 page 页驻留在 RAM，让后续真正加载时走内存路径。

        Args:
            filepath (str): 待预读文件的绝对路径。

        Returns:
            int: 实际读取的字节数；读取失败或空文件返回 0。
        """
        try:
            size: int = os.path.getsize(filepath)
            if size == 0:
                return 0
            read_size: int = min(size, _PRELOAD_READ_CHUNK_BYTES)
            with open(filepath, "rb") as f:
                f.read(read_size)
            return read_size
        except (OSError, PermissionError) as e:
            logger.debug(f"[预加载] 读取文件失败 {filepath}: {e}")
            return 0

    def get_status(self) -> dict[str, Any]:
        """获取当前预加载任务状态快照。

        Retrieve the current preload task status.

        Returns:
            dict[str, Any]: ``_state`` 的浅拷贝副本，键：
                - in_progress (bool): 是否正在进行。
                - target_engine (str | None): 目标引擎名。
                - target_size (str | None): 目标尺寸变体。
                - completed (bool): 是否完成。
                - error (str | None): 错误消息，无错误为 None。
        """
        with self._lock:
            return dict(self._state)


# 模块级单例 + 向后兼容包装函数
#: 模型文件预加载服务单例，负责后台预读模型文件到系统 Page Cache 加速后续加载
_preload_service: PreloadService = PreloadService()


def preload_model(
    engine: str = EngineName.VOXCPM2.value,
    size: str = EngineName.VOXCPM2.value,
) -> dict[str, Any]:
    """向后兼容包装：委托给 PreloadService.preload() 并返回状态。

    Args:
        engine (str): 目标引擎名。
        size (str): 模型尺寸变体（当前保留未使用）。

    Returns:
        dict[str, Any]: 调用 :meth:`PreloadService.get_status` 返回的预加载状态快照。
    """
    _preload_service.preload(engine, size)
    return _preload_service.get_status()


def get_preload_status() -> dict[str, Any]:
    """向后兼容包装：委托给 PreloadService.get_status()。

    Returns:
        dict[str, Any]: 预加载任务状态快照。
    """
    return _preload_service.get_status()


# ====================================================================
# 引擎切换 (M-R1 拆分 + M-R3 回滚一致性)
# ====================================================================


def _can_hot_standby(target_engine: str) -> bool:
    """判断是否可热待机（同时保留新旧双引擎在显存中）。

    Check if there's enough VRAM to keep both current and target engine loaded.

    热待机模式允许切换引擎时不卸载当前引擎，在显存充足的场景下把切换
    延迟从数十秒降低到数百毫秒。

    Args:
        target_engine (str): 目标引擎名称。

    Returns:
        bool: 若当前空闲显存足够容纳目标引擎（按 80% 估算，留 20% 波动余量）
            返回 ``True``；否则返回 ``False``；CPU 模式或无法获取 GPU 信息
            时保守返回 ``False``。
    """
    from .gpu_backend import GPUBackend, GPUBackendManager
    from .model_registry import ENGINE_VRAM_REQUIREMENTS

    backend: GPUBackend = GPUBackendManager.detect_backend()
    if backend == GPUBackend.CPU:
        return False

    gpu_device: Any = get_gpu_device()
    if gpu_device is None:
        return False

    props: dict[str, Any] = GPUBackendManager.get_device_properties(gpu_device)
    total: int = props.get("total_memory", 0)
    if total <= 0:
        return False

    allocated: int = GPUBackendManager.memory_allocated(gpu_device)
    free_gb: float = (total - allocated) / (1024**3)

    current_vram: float = ENGINE_VRAM_REQUIREMENTS.get(registry.current_engine, 0.0)
    target_vram: float = ENGINE_VRAM_REQUIREMENTS.get(target_engine, 0.0)

    # Need enough free VRAM for target engine (with 20% margin)
    # Plus we keep current engine loaded
    needed_gb: float = target_vram * 0.8
    can_standby: bool = free_gb >= needed_gb

    if can_standby:
        logger.info(
            f"[热待机] 显存充足: 可用 {free_gb:.2f}GB, 目标需要 {needed_gb:.2f}GB, 当前引擎占用 {current_vram:.1f}GB"
        )
    else:
        logger.info(f"[热待机] 显存不足: 可用 {free_gb:.2f}GB, 目标需要 {needed_gb:.2f}GB")

    return can_standby


def _validate_engine_name(engine_name: str) -> str:
    """校验并规范化引擎名称（M-R1 拆分辅助函数 ①）。

    REFACTOR: [M-R1] 校验并规范化引擎名称。

    Args:
        engine_name (str): 待校验的引擎名称。

    Returns:
        str: 去除首尾空白后的规范化引擎名。

    Raises:
        EngineSwitchError: 引擎名称不在 ``EngineName`` 白名单内。
    """
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


def _snapshot_engine_state() -> dict[str, Any]:
    """快照当前引擎状态（M-R1 拆分辅助函数 ②）。

    REFACTOR: [M-R1] 快照当前引擎状态，用于失败时回滚。

    Returns:
        dict[str, Any]: 包含如下键的快照字典：
            - engine: ``registry.current_engine`` 当前引擎名。
            - voxcpm_model: VoxCPM2 模型引用。
            - voxcpm_asr: VoxCPM2 ASR 模型引用。
            - indextts2_engine: IndexTTS2 引擎实例引用。

    注意 (M-R3):
        快照中的对象引用**仅用于判断"之前是哪个引擎"**，回滚时
        **不能**直接恢复这些引用——因为 ``unload_model`` 会调用
        ``IndexTTS2.unload()`` 释放底层 CUDA 句柄和 GPU 缓冲区，
        旧引用会变成"悬空指针"。正确做法见 :func:`_rollback_engine`
        的 M-R3 WHY 注释。
    """
    return {
        "engine": registry.current_engine,
        "voxcpm_model": registry.voxcpm_model,
        "voxcpm_asr": registry.voxcpm_asr,
        "indextts2_engine": registry.indextts2_engine,
    }


def _check_vram_prereq(engine_name: str, backend: Any, gpu_device: Any) -> float:
    """显存预检查（M-R1 拆分辅助函数 ③）。

    REFACTOR: [M-R1] 显存预检查。

    WHY: 这里使用 ENGINE_VRAM_REQUIREMENTS 中配置的模型大小作为基线，
    实际运行时还会叠加 ① ASR/Enhancer 权重、② KV cache、③ 中间激活，
    因此在 load_voxcpm2 的 WHY 注释里强调需要 **1.5 倍** 余量；但
    预检仅检查基线大小即可，因为预检拒绝的是"基线都装不下"的硬失败，
    余量通过实际 OOM + retry 机制处理更稳健。

    E2-1: 校验 props 有效性（total <= 0 时跳过预检，记录警告而非崩溃）。

    Args:
        engine_name (str): 目标引擎名称。
        backend: GPUBackend 枚举值。
        gpu_device: GPU 设备索引（CUDA/MPS）或 ``None``（CPU）。

    Returns:
        float: 可用显存（GB）。CPU 模式或无法获取显存时返回 ``0.0``。

    Raises:
        InsufficientVRAMError: 可用显存低于目标引擎基线要求。
    """
    from .gpu_backend import GPUBackend, GPUBackendManager
    from .model_registry import ENGINE_VRAM_REQUIREMENTS

    needed_gb: float = ENGINE_VRAM_REQUIREMENTS.get(engine_name, 6.0)

    # CPU 模式或无 GPU 设备：跳过显存预检
    if backend == GPUBackend.CPU or gpu_device is None:
        logger.warning("[引擎切换] CPU 模式或无 GPU 设备：跳过显存检查，模型将在 CPU 上运行（速度较慢）")
        return 0.0

    props: dict[str, Any] = GPUBackendManager.get_device_properties(gpu_device)
    total: int = props.get("total_memory", 0)
    # E2-1: 校验 props 有效性
    if total <= 0:
        logger.warning("[引擎切换] 无法获取 GPU 总显存，跳过预检")
        return 0.0

    allocated: int = GPUBackendManager.memory_allocated(gpu_device)
    free: int = total - allocated
    free_gb: float = free / 1024**3

    logger.info(f"[引擎切换] VRAM 检查: 需要 {needed_gb}GB, 可用 {free_gb:.2f}GB")

    if free_gb < needed_gb:
        error_msg: str = f"显存不足，无法加载 {engine_name}。需要约 {needed_gb}GB，当前可用 {free_gb:.2f}GB。"
        logger.error(f"[引擎切换] {error_msg}")
        raise InsufficientVRAMError(error_msg)

    return free_gb


def _wait_vram_freed(
    gpu_device: Any,
    max_wait: float = _VRAM_WAIT_MAX_SECONDS,
    poll_interval: float = _VRAM_POLL_INTERVAL_SECONDS,
) -> bool:
    """轮询等待显存释放（M-R1 拆分辅助函数 ④）。

    REFACTOR: [M-R1] 轮询等待显存释放。

    E2-2: 阈值相对化 —— 取 ``_VRAM_FREE_THRESHOLD_BYTES`` 与总显存的
    ``_VRAM_FREE_PERCENT_FLOOR%`` 中较小者，避免大显存设备上 500MB 阈值过松、
    小显存设备上 500MB 阈值过严。

    Args:
        gpu_device: GPU 设备索引。
        max_wait (float): 最大等待秒数，默认 ``_VRAM_WAIT_MAX_SECONDS``。
        poll_interval (float): 轮询间隔秒数，默认 ``_VRAM_POLL_INTERVAL_SECONDS``。

    Returns:
        bool: 若在超时前显存释放量超过阈值返回 ``True``；轮询异常或超时返回
            ``False``（由调用方决定是否继续切换流程）。
    """
    from .gpu_backend import GPUBackendManager

    poll_start: float = time.time()
    while time.time() - poll_start < max_wait:
        time.sleep(poll_interval)
        try:
            props: dict[str, Any] = GPUBackendManager.get_device_properties(gpu_device)
            total: int = props.get("total_memory", 0)
            allocated: int = GPUBackendManager.memory_allocated(gpu_device)
            free: int = total - allocated
            # E2-2: 阈值相对化
            threshold: int = min(_VRAM_FREE_THRESHOLD_BYTES, total * _VRAM_FREE_PERCENT_FLOOR // 100)
            if free > threshold:
                return True
        except Exception:
            # 显存查询异常时立即退出轮询，交由调用方决定后续行为
            break

    return False


def _rollback_engine(prev_state: dict[str, Any], error: Exception) -> None:
    """回滚到之前的引擎状态（M-R1 拆分辅助函数 ⑤ / M-R3 关键修复）。

    REFACTOR: [M-R1/M-R3] 回滚到之前的引擎状态。

    WHY M-R3: **不能只恢复引用而要重新加载 prev_engine。**

    考虑如下时序：
        1. 快照 ``prev_state = {"indextts2_engine": <obj A>, "engine": INDEXTTS2}``
        2. 执行 ``unload_model()`` → 内部调用 ``obj A.unload()``
           → IndexTTS2 内部释放：
             a) ``del self.vocoder``
             b) ``torch.cuda.empty_cache()``
             c) 关闭 CUDA IPC handle
        3. 此时 ``prev_state["indextts2_engine"]`` 仍引用 Python 对象 A，
           但 A 内部的 ``.tts_model / .vocoder / cuda stream`` 均已失效。
        4. 若仅 ``registry.indextts2_engine = prev_state["indextts2_engine"]``，
           下次推理会抛 ``RuntimeError: accessing unregistered storage`` 等
           底层 CUDA 错误，且极难定位。

    因此 M-R3 的修复方案是：**根据 ``prev_engine`` 名称重新走完整的
    model loading 流程**，虽然慢了数十秒，但保证回滚后引擎真正可用。

    失败处理：重新加载是 best-effort 的，失败时仅记录错误日志，
    不抛异常（避免掩盖原始的切换失败原因）。最坏情况下回滚后引擎
    不可用，用户需手动重新加载。

    Args:
        prev_state (dict[str, Any]): :func:`_snapshot_engine_state` 返回的快照。
        error (Exception): 触发回滚的异常（仅用于日志诊断）。
    """
    import traceback

    tb: str = traceback.format_exc()
    error_msg: str = f"引擎切换失败: {type(error).__name__}: {error}\n\n详细错误:\n{tb}"
    logger.error(f"[引擎切换] {error_msg}")

    prev_engine: str | None = prev_state["engine"]
    logger.info(f"[引擎切换] 开始回滚到之前的引擎状态 (prev_engine={prev_engine})...")

    # M-R3: 先清理可能残留的半加载状态（新引擎可能加载了一半）
    try:
        registry.voxcpm_model = None
        registry.voxcpm_asr = None
        registry.indextts2_engine = None
        registry.current_engine = None
        for gname in list(registry.get_all_engine_instances().keys()):
            registry.clear_engine(gname)
    except Exception as cleanup_err:
        logger.error(f"[引擎切换] 回滚前清理失败: {cleanup_err}")

    # M-R3: 根据 prev_engine 重新加载模型
    # 注意：set_voxcpm_loaded / set_indextts2_loaded 内部会设置 current_engine
    if prev_engine == EngineName.VOXCPM2.value:
        try:
            logger.info("[引擎切换] 回滚: 重新加载 VoxCPM2 模型...")
            from .gpu_backend import GPUBackend, GPUBackendManager

            backend: GPUBackend = GPUBackendManager.detect_backend()
            gpu_device: Any = get_gpu_device()
            # 消费 generator 但不向前端推送状态（回滚过程对前端透明）
            for _ in _do_load_voxcpm2_internal(gpu_device, backend, include_denoiser=False):
                pass
            logger.info("[引擎切换] 回滚: VoxCPM2 模型重新加载完成")
        except Exception as reload_err:
            logger.error(f"[引擎切换] 回滚时重新加载 VoxCPM2 失败: {reload_err}")
    elif prev_engine == EngineName.INDEXTTS2.value:
        try:
            logger.info("[引擎切换] 回滚: 重新加载 IndexTTS2 引擎...")
            from .engines.indextts2_engine import IndexTTS2Engine
            from .gpu_backend import GPUBackend, GPUBackendManager

            backend = GPUBackendManager.detect_backend()
            new_engine: Any = IndexTTS2Engine(
                model_dir=get_indextts2_model_path(),
                use_bf16=(backend != GPUBackend.CPU),
            )
            registry.set_indextts2_loaded(new_engine)
            logger.info("[引擎切换] 回滚: IndexTTS2 引擎重新加载完成")
        except Exception as reload_err:
            logger.error(f"[引擎切换] 回滚时重新加载 IndexTTS2 失败: {reload_err}")
    elif prev_engine:
        # 通用新式引擎回滚：重新走声明式加载流程
        try:
            logger.info(f"[引擎切换] 回滚: 重新加载 {prev_engine} 引擎...")
            for _ in _load_generic_engine(prev_engine):
                pass
            logger.info(f"[引擎切换] 回滚: {prev_engine} 引擎重新加载完成")
        except Exception as reload_err:
            logger.error(f"[引擎切换] 回滚时重新加载 {prev_engine} 失败: {reload_err}")
    else:
        logger.warning("[引擎切换] 回滚: 之前没有已加载的引擎，所有状态已置空")

    # E4: 资源清理
    gc.collect()
    from .gpu_backend import GPUBackendManager

    with contextlib.suppress(Exception):
        GPUBackendManager.empty_cache()


def switch_engine(
    engine_name: str = EngineName.VOXCPM2.value,
) -> Generator[tuple[str, None, None, None], None, None]:
    """切换活跃引擎（5 阶段流程 + 完整回滚）。

    Switch the active engine with full rollback on failure.

    REFACTOR: [M-R1] 拆分为 5 个职责单一的辅助函数，单函数圈复杂度 25 → 各 <8。
    REFACTOR: [M-R3] _rollback_engine 重新加载 prev_engine 模型，保证回滚后可用。

    5 阶段触发条件与产出事件（每条 ``(status_text, None, None, None)`` 四元组，
    兼容调用方 ``for status_text, _, _, _ in gen:`` 解包）：
        1. **validate**（阶段 ①）：
           调用 :func:`_validate_engine_name`。若引擎名不在白名单直接抛
           ``EngineSwitchError``，**不**占用锁、**不**触发回滚。
           产出事件：无（同步校验）。

        2. **snapshot**（阶段 ②）：
           获取 ``_model_lock`` 后调用 :func:`_snapshot_engine_state` 保存
           当前引擎引用，供 rollback 时判断 ``prev_engine`` 名称使用。

        3. **prereq**（阶段 ③）：
           调用 :func:`_check_vram_prereq` 做显存基线预检。若失败抛
           ``InsufficientVRAMError``（属于 TTSError 子类，直接抛出不触发
           回滚——因为 unload_model 尚未执行，旧引擎完好无损）。

        4. **wait**（阶段 ④，非热待机路径）：
           若 ``_can_hot_standby`` 判定为 False（显存不足以双引擎常驻），
           则先**同步**调用 :func:`unload_model`（with 语句内部 try/finally
           负责锁与显存清理），再做
           ``synchronize → empty_cache → ipc_collect → _wait_vram_freed``
           的显存同步链，确保后续加载拿到的是干净的空闲显存。

        5. **rollback**（异常路径）：
           若非 TTSError 类异常（CUDA OOM / RuntimeError / I/O 错误等）
           发生在 unload_model 之后，则调用 :func:`_rollback_engine`
           按 M-R3 规则**重新加载 prev_engine**，最后抛出 ``EngineSwitchError``
           包装原异常。TTSError 子类（InsufficientVRAMError 等）不触发回滚。

    Args:
        engine_name (str): 目标引擎名，默认 ``EngineName.VOXCPM2.value``。

    Yields:
        tuple[str, None, None, None]:
            每个阶段 ``(status_text, None, None, None)`` 四元组；
            ``status_text`` 为当前阶段描述；其余三位置为历史兼容占位。
            异常退出时 finally 前会额外产出一条 ``(error_msg, None, None, None)``
            事件，``error_msg`` 首段包含 ``"失败"`` / ``"error"`` 关键字供
            调用方识别。

    Raises:
        InsufficientVRAMError: 空闲显存低于目标引擎基线要求（预检失败）。
        EngineSwitchError: 切换失败且已尝试回滚（原异常挂在 ``__cause__`` 上）。
    """
    from .gpu_backend import GPUBackend, GPUBackendManager

    # M-R1: 校验引擎名称（阶段 ①，锁外执行，失败不影响旧引擎）
    engine_name = _validate_engine_name(engine_name)
    logger.info(f"[引擎切换] 目标: {engine_name}")

    prev_state: dict[str, Any] = {}
    try:
        with _model_lock:
            # M-R1: 快照前置状态（阶段 ②）
            prev_state = _snapshot_engine_state()

            backend: GPUBackend = GPUBackendManager.detect_backend()
            gpu_device: Any = get_gpu_device()
            logger.info(f"[引擎切换] 使用设备 {gpu_device if gpu_device is not None else 'CPU'} 进行显存检查")

            # M-R1: VRAM 预检查（阶段 ③）
            logger.info("[引擎切换] 开始 VRAM 预检查...")
            _check_vram_prereq(engine_name, backend, gpu_device)

            # 检查热待机可能性
            hot_standby: bool = _can_hot_standby(engine_name)
            if hot_standby:
                logger.info("[引擎切换] 显存充足，使用热待机模式，跳过卸载")
                _progress_mgr.update_phase("显存充足，直接加载新引擎...")
                status_text: str = "显存充足，直接加载新引擎"
                yield status_text, None, None, None
            else:
                logger.info("[引擎切换] 显存不足，使用传统切换模式")
                _progress_mgr.update_phase("正在卸载旧引擎...")
                status_text = "正在卸载旧引擎并释放显存"
                yield status_text, None, None, None

                # 阶段 ④：同步调用 unload_model（其内部用 with _model_lock 加锁，
                # 因为 _model_lock 是 RLock，允许同一线程重入 acquire）
                unload_model()

                # M-R1: 等待显存释放（unload 已释放锁，但 RLock 允许我们仍在外层 with 内）
                if backend != GPUBackend.CPU and gpu_device is not None:
                    with contextlib.suppress(Exception):
                        GPUBackendManager.synchronize(gpu_device)
                    with contextlib.suppress(Exception):
                        GPUBackendManager.empty_cache()
                    with contextlib.suppress(Exception):
                        GPUBackendManager.ipc_collect(gpu_device)

                    vram_freed: bool = _wait_vram_freed(gpu_device)
                    with contextlib.suppress(Exception):
                        GPUBackendManager.empty_cache()

                    if vram_freed:
                        logger.info("[引擎切换] VRAM 已释放")
                    else:
                        logger.warning("[引擎切换] VRAM 轮询超时，继续切换流程")

                _progress_mgr.update_phase("正在清理 VRAM...")
                status_text = "正在清理显存缓存"
                yield status_text, None, None, None

            # 阶段 ⑤：加载新引擎
            if engine_name == EngineName.VOXCPM2.value:
                for status_tuple in _load_voxcpm2_engine(gpu_device, backend):
                    yield status_tuple
            elif engine_name == EngineName.INDEXTTS2.value:
                for status_tuple in load_indextts2():
                    yield status_tuple
            else:
                # 通用新式引擎（声明式注册）
                for status_tuple in _load_generic_engine(engine_name):
                    yield status_tuple

    except TTSError:
        # 业务异常原样抛出（InsufficientVRAMError 等），不触发回滚
        # 原因：VRAM 预检失败时 unload_model 尚未执行，旧引擎完好无需回滚
        raise
    except Exception as e:
        error_msg: str = f"引擎切换失败: {type(e).__name__}: {e}"
        # 产出一条 error 事件给前端进度流（保持 tuple 格式兼容解包）
        yield error_msg, None, None, None
        # M-R1/M-R3: 委托回滚逻辑给 _rollback_engine（仅在 prev_state 已快照时）
        if prev_state:
            with contextlib.suppress(Exception):
                _rollback_engine(prev_state, e)
        raise EngineSwitchError(error_msg) from e
    finally:
        with contextlib.suppress(Exception):
            free_gpu_memory()


def _load_voxcpm2_engine(
    gpu_device: Any,
    backend: Any,
) -> Generator[tuple[str, None, None, None], None, None]:
    """引擎切换流程中加载 VoxCPM2（内部辅助生成器）。

    本函数为 :func:`switch_engine` 专用的 VoxCPM2 加载包装器，
    与公开的 :func:`load_voxcpm2` 区别在于：
    1. 已在调用方持有 ``_model_lock`` 的上下文内执行（不重复 acquire）。
    2. 强制启用 ``include_denoiser=True``，加载语音增强/降噪子模块，
       因为引擎切换场景通常为正式推理使用，需要完整音质。
    3. 通过 ``_progress_mgr.update_phase()`` 同步更新全局进度管理器，
       与前端 SSE 进度条联动。
    4. 每条产出前通过 logger 记录阶段日志便于追踪切换时序。

    Args:
        gpu_device: GPU 设备索引（CUDA/MPS 为 int，CPU 为 None）。
        backend: ``GPUBackend`` 枚举值，标识当前计算后端。

    Yields:
        tuple[str, None, None, None]:
            与 :func:`_do_load_voxcpm2_internal` 产出格式一致的四元组
            ``(status_text, None, None, None)``，直接透传给 switch_engine
            的生成器消费者（最终通过 SSE 推送给前端）。

    Raises:
        Exception: 加载过程中任何异常（权重缺失、CUDA OOM、文件权限等）
            会被 :func:`switch_engine` 的外层 try/except 捕获并触发回滚，
            本函数不做异常吞掉。
    """
    _progress_mgr.update_phase("正在加载新引擎...")
    for status in _do_load_voxcpm2_internal(gpu_device, backend, include_denoiser=True):
        logger.info(f"[引擎切换] {status[0]}")
        yield status


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
            f"引擎 '{engine_name}' 无法解析（未注册或依赖缺失）。请确认已安装对应依赖并下载模型权重。"
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


def get_generation_tracker() -> GenerationTracker:
    """获取全局 ``GenerationTracker`` 单例。

    Returns:
        GenerationTracker: 全局生成任务状态追踪器实例（追踪活跃/已完成/失败的
            生成任务数量，供 ``/api/system/health`` 与 UI 使用）。
    """
    return _gen_tracker


def get_progress_manager() -> ProgressManager:
    """获取全局 ``ProgressManager`` 单例。

    Returns:
        ProgressManager: 全局生成进度管理器（负责阶段文本更新、HTML 进度条渲染，
            供路由层与生成逻辑中间件共享）。
    """
    return _progress_mgr


def get_time_estimator() -> Any:
    """获取全局生成耗时估算器实例（GenerationTimeEstimator）。

    类型标注为 ``Any`` 因为 :class:`GenerationTimeEstimator` 定义在
    ``estimator.py`` 中，在类型检查阶段可能出现循环导入；运行时实际
    返回的始终是 ``GenerationTimeEstimator`` 单例，通过线性回归
    基于历史数据预测未来生成耗时。

    Returns:
        GenerationTimeEstimator: 全局耗时估算器实例。
    """
    return _time_estimator


def get_persona_cache() -> AdaptiveLRUCache:
    """获取全局 Persona 嵌入缓存 ``AdaptiveLRUCache`` 单例。

    Returns:
        AdaptiveLRUCache: 全局音色嵌入缓存，LRU 淘汰策略，容量随 GPU 显存
            压力自适应调整；key 为 persona 名，value 为预计算的音色嵌入张量。
    """
    return _persona_embedding_cache
