"""模型管理模块（薄门面，M-R7 拆分，2026-08-17）。

提供模型加载、卸载、引擎切换、LRU 缓存、进度追踪、GPU 显存监控以及音色缓存预热。
支持 VoxCPM2 与 IndexTTS 2.5 双引擎架构。

M-R7: 实现拆分至 model_manager_core 子包：
- state: 共享状态/常量/单例（_model_lock、_persona_embedding_cache 等）
- load:   load_voxcpm2 / load_indextts2 / PreloadService / PersonaWarmupService
- unload: unload_model / _check_voxcpm2_lock
- switch: switch_engine 及 5 个辅助函数
本文件仅做 re-export，保持 `from integrated_app.model_manager import xxx`
全部既有导入路径不变。

State management:
    All core model state is owned by the ModelRegistry singleton in
    ``model_registry.py``.  Access state via ``registry.xxx``.

同步使用约定: _model_lock 为 threading.RLock，见 model_manager_core/state.py。
"""

from typing import Any

from .cache import AdaptiveLRUCache, LRUCache
from .gpu_utils import GPUMemoryMonitor, free_gpu_memory, is_oom_error
from .model_manager_core.load import (  # noqa: F401
    PersonaWarmupService,
    PreloadService,
    _do_load_voxcpm2_internal,
    _warmup_persona_cache_compat,
    get_persona_cache_stats,
    get_preload_status,
    load_indextts2,
    load_indextts20,
    load_voxcpm2,
    preload_model,
    warmup_persona_cache,
)
from .model_manager_core.state import *  # noqa: F401,F403
from .model_manager_core.switch import (  # noqa: F401
    _can_hot_standby,
    _check_vram_prereq,
    _load_generic_engine,
    _load_voxcpm2_engine,
    _rollback_engine,
    _snapshot_engine_state,
    _validate_engine_name,
    _wait_vram_freed,
    switch_engine,
)
from .model_manager_core.unload import (  # noqa: F401
    _check_voxcpm2_lock,
    unload_all_models,
    unload_model,
)
from .progress import ProgressManager
from .tracker import GenerationTracker

__all__ = [
    "LRUCache",
    "AdaptiveLRUCache",
    "ProgressManager",
    "GenerationTracker",
    "GPUMemoryMonitor",
    "is_oom_error",
    "free_gpu_memory",
]


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
