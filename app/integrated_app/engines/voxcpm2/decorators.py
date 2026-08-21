"""VoxCPM2 生成函数装饰器模块。

架构说明：
    本模块将 VoxCPM2 各生成函数（design/clone/ultimate/script/streaming/prompt）
    中重复的"模型就绪检查 → 生成锁获取 → 追踪器启停 → 进度管理 → 异常处理 →
    耗时日志"模式抽取为可复用装饰器，避免各子模块重复样板代码。

主要组件：
    with_generation_context: 核心装饰器工厂，统一处理生成上下文管理。

依赖关系：
    - model_registry.registry: 获取 voxcpm_model 实例检查模型是否加载
    - model_manager: 提供 _check_voxcpm2_lock()、_gen_tracker、_progress_mgr
    - exceptions: EngineSwitchError / GenerationError / tts_error_handler
    - _base.logger: 统一日志输出
"""

import functools
import time
from collections.abc import Callable
from typing import Any

from ...exceptions import EngineSwitchError, GenerationError, tts_error_handler
from ...model_manager import _check_voxcpm2_lock, _gen_tracker, _progress_mgr
from ...model_registry import registry
from ._base import logger


def with_generation_context(
    phase_name: str = "",
    check_model: bool = True,
    use_tracker: bool = True,
    use_progress: bool = True,
    cleanup_fn=None,
):
    """VoxCPM2 生成函数上下文管理装饰器工厂。

    统一封装以下横切关注点：
        1. 模型就绪检查：确认 VoxCPM2 模型已加载，未加载则抛 EngineSwitchError
        2. 生成锁检查：防止模型加载/切换过程中并发触发推理导致状态不一致
        3. 生成追踪器：_gen_tracker.start_generation()/end_generation() 统计
        4. 进度管理器：初始化 phase 提示，异常时标记 error，finally 中 schedule_reset
        5. 统一异常处理：通过 tts_error_handler 将未知异常包装为 GenerationError
        6. 耗时日志：记录每次生成的 wall-clock 耗时
        7. 可选清理函数：finally 块中调用 cleanup_fn（如 cleanup_temp_files）

    Args:
        phase_name: 生成阶段名称，用于进度条显示和日志前缀
            （如 "语音设计"、"语音克隆"、"剧本工坊"）。
        check_model: 是否在进入时检查 registry.voxcpm_model 是否为 None。
            默认为 True；子模块内部已做检查时可设为 False 避免重复。
        use_tracker: 是否启用 _gen_tracker 生成统计。默认为 True。
        use_progress: 是否启用 _progress_mgr 进度条管理。默认为 True。
        cleanup_fn: 可选的清理回调函数，在 finally 块中无参数调用。
            典型用途：cleanup_temp_files() 清理本次生成产生的临时音频。

    Returns:
        Callable: 装饰器函数，接受被装饰函数并返回包装后的 wrapper。

    Usage:
        @with_generation_context(phase_name="语音克隆")
        def fn_voxcpm_clone(text, ref_audio, **kwargs):
            # 函数体内只需关注核心生成逻辑
            # 模型检查/锁/进度/异常/耗时已由装饰器统一处理
            wav = model.generate(...)
            return (sr, wav, filename), "生成成功"
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if check_model and registry.voxcpm_model is None:
                raise EngineSwitchError("请先切换并加载 VoxCPM2 引擎")

            if not _check_voxcpm2_lock():
                raise GenerationError("模型正在加载或切换中，请稍后再试")

            if use_tracker:
                _gen_tracker.start_generation()

            if use_progress and phase_name:
                _progress_mgr.start(total_segments=1, phase=f"{phase_name} 准备中...")

            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                return result

            except Exception:
                if use_progress:
                    _progress_mgr.set_error(f"{phase_name} 失败" if phase_name else "生成失败")
                raise

            finally:
                elapsed = time.time() - start_time
                if use_tracker:
                    _gen_tracker.end_generation(elapsed)
                if use_progress:
                    _progress_mgr.schedule_reset(delay_seconds=120)
                if cleanup_fn:
                    cleanup_fn()
                logger.info(f"[{phase_name or func.__name__}] 生成耗时 {elapsed:.1f} 秒")

        return tts_error_handler(wrapper)

    return decorator
