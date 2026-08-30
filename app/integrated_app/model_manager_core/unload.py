"""model_manager_core.unload — 模型卸载职责（M-R7 拆分，2026-08-17）。

包含 _check_voxcpm2_lock 与 unload_model。
【边界】不做加载（load.py）；切换路径经 switch.py 调用本模块。
"""

from . import state as _state
from .state import *


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

            # Unload 通用新式引擎（generic_tts_engine 等）
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
            from ..gpu_backend import GPUBackend, GPUBackendManager

            backend: GPUBackend = GPUBackendManager.detect_backend()
            if backend != GPUBackend.CPU:
                with contextlib.suppress(Exception):
                    GPUBackendManager.synchronize()
                device: Any = _state.get_gpu_device()
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
# 全量卸载（进程退出清理）
# ====================================================================


def unload_all_models() -> None:
    """卸载所有仍处于加载态的引擎，供 lifespan shutdown 调用。

    WHY 需要它：``app_server.lifespan`` 的 shutdown 分支一直写着
    ``from .model_manager import unload_all_models``，但该函数在 model_manager
    拆分为 model_manager_core 时**丢失了**，全仓只剩调用点没有定义。后果是每次
    优雅关闭都抛 ``ImportError`` 并被外层 ``except`` 吞成一条 error 日志 ——
    模型从未真正卸载、显存不释放，Ctrl+C 重启时新进程会看到残留占用。

    与 :func:`unload_model` 的关系：``unload_model()`` 单次调用即会同时清空
    ``registry.voxcpm_model`` / ``voxcpm_asr`` 与 ``indextts2_engine`` 两个槽位，
    因此正常情况下循环一轮即退出。这里仍用有界轮次兜底，是为了防止某个引擎的
    unload 抛异常导致槽位残留而让关闭流程静默带着显存退出。

    关闭路径不得抛异常中断 uvicorn 退出，因此所有失败都只记日志。
    """
    max_passes: int = 3
    for attempt in range(max_passes):
        if registry.voxcpm_model is None and registry.indextts2_engine is None:
            if attempt:
                logger.info("[全量卸载] 所有引擎槽位已清空")
            return
        try:
            unload_model()
        except Exception as unload_err:  # noqa: BLE001 - 关闭路径不得中断进程退出
            logger.warning("[全量卸载] 第 %s 轮卸载失败: %s", attempt + 1, unload_err)
            return
    logger.warning("[全量卸载] 已达最大卸载轮次 %s，仍有引擎槽位未释放", max_passes)
