"""model_manager_core.state — 共享模块级状态（M-R7 拆分，2026-08-17）。

集中保管原 model_manager.py 的导入、常量与全局单例：_model_lock（RLock）、
_persona_embedding_cache、_gen_tracker、_progress_mgr、_time_estimator 及全部
显存/预热常量。子模块（load/unload/switch）一律 `from .state import *` 引用。

同步使用约定：_model_lock 为 threading.RLock；路由层须通过
loop.run_in_executor 调度同步 generator，禁止在 async 上下文直接持锁。
"""
import contextlib
import gc
import logging
import os
import threading
import time
from collections.abc import Callable, Generator
from typing import Any

from ..cache import AdaptiveLRUCache, LRUCache
from ..config import (
    DATA_DIR,
    ROOT_DIR,
    get_indextts2_model_path,
    get_indextts20_model_path,
    get_voxcpm2_asr_path,
    get_voxcpm2_denoiser_path,
    get_voxcpm2_model_path,
)
from ..estimator import GenerationTimeEstimator
from ..exceptions import (
    EngineLoadError,
    EngineSwitchError,
    InsufficientVRAMError,
    TTSError,
)
from ..gpu_utils import (
    GPUMemoryMonitor,
    free_gpu_memory,
    get_gpu_device,
    is_oom_error,
)
from ..model_registry import EngineName, registry
from ..monitor import get_health_monitor
from ..progress import ProgressManager
from ..tracker import GenerationTracker

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

__all__ = [
    "contextlib", "gc", "os", "threading", "time", "Callable", "Generator", "Any",
    "AdaptiveLRUCache", "LRUCache",
    "DATA_DIR", "ROOT_DIR",
    "get_indextts2_model_path", "get_indextts20_model_path", "get_voxcpm2_asr_path",
    "get_voxcpm2_denoiser_path", "get_voxcpm2_model_path",
    "GenerationTimeEstimator",
    "EngineLoadError", "EngineSwitchError", "InsufficientVRAMError", "TTSError",
    "GPUMemoryMonitor", "free_gpu_memory", "get_gpu_device", "is_oom_error",
    "EngineName", "registry",
    "get_health_monitor",
    "ProgressManager", "GenerationTracker",
    "logger",
    "_VRAM_FREE_THRESHOLD_BYTES", "_VRAM_WAIT_MAX_SECONDS",
    "_VRAM_POLL_INTERVAL_SECONDS", "_VRAM_FREE_PERCENT_FLOOR",
    "_PRELOAD_READ_CHUNK_BYTES", "_PERSONA_CACHE_DEFAULT_SIZE",
    "_WARMUP_TOP_PERSONAS", "_UNLOAD_SLOW_THRESHOLD_SECONDS",
    "_LOAD_RETRY_AFTER_UNLOAD_SECONDS",
    "_TORCH_COMPILE_CACHE_DIR",
    "_time_estimator", "_persona_embedding_cache",
    "_gen_tracker", "_progress_mgr", "_model_lock",
]
