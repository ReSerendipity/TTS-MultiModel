"""信号安全保存模块（Ch2 P1 / Ch8 P1）。

提供优雅关闭（graceful shutdown）的信号处理机制：

- register_signal_handlers(): 注册 SIGTERM/SIGINT 信号处理器
- unregister_signal_handlers(): 恢复原始信号处理器
- check_graceful_shutdown(): 生成循环中定期检查是否请求关闭
- graceful_shutdown_requested: threading.Event 标志

当收到 SIGTERM 或 SIGINT 时：
  1. 设置 graceful_shutdown_requested 事件标志
  2. 正在进行的生成任务通过 check_graceful_shutdown() 感知
  3. 若正在训练，尝试保存检查点后退出
  4. 非训练场景下完成当前推理步骤后安全退出

线程安全：使用 threading.Event 保证跨线程可见性。
"""

from __future__ import annotations

import logging
import signal
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("tts_multimodel")

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------

# 线程安全的事件标志，用于通知各工作线程收到关闭信号
graceful_shutdown_requested = threading.Event()

# 保存原始信号处理器，以便恢复
_original_handlers: dict[int, Any] = {}

# 互斥锁，防止信号处理器注册/注销并发
_registration_lock = threading.Lock()

# 是否已注册信号处理器
_handlers_registered: bool = False

# 训练检查点回调（可选）
_checkpoint_callback: Callable[[], bool] | None = None

# 清理回调列表（可选，用于资源释放）
_cleanup_callbacks: list[Callable[[], None]] = []


# ---------------------------------------------------------------------------
# 信号处理器
# ---------------------------------------------------------------------------


def _signal_handler(signum: int, frame) -> None:
    """信号处理函数，在收到 SIGTERM/SIGINT 时设置关闭标志。

    此函数应尽可能简短且安全：
    - 仅设置 threading.Event 标志
    - 不执行耗时操作
    - 不进行 I/O 操作
    - 不分配内存

    Args:
        signum: 信号编号。
        frame: 当前栈帧（未使用）。
    """
    sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    logger.warning(f"[信号处理] 收到 {sig_name} 信号，请求优雅关闭")

    # 设置关闭标志（线程安全）
    graceful_shutdown_requested.set()

    # 尝试执行训练检查点保存（仅在已注册回调时）
    if _checkpoint_callback is not None:
        try:
            logger.info("[信号处理] 尝试保存训练检查点...")
            success = _checkpoint_callback()
            if success:
                logger.info("[信号处理] 训练检查点保存成功")
            else:
                logger.warning("[信号处理] 训练检查点保存失败")
        except Exception as e:
            logger.error(f"[信号处理] 训练检查点保存异常: {e}")

    # 执行清理回调
    for callback in _cleanup_callbacks:
        try:
            callback()
        except Exception as e:
            logger.error(f"[信号处理] 清理回调执行异常: {e}")


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def register_signal_handlers(
    checkpoint_callback: Callable[[], bool] | None = None,
    cleanup_callbacks: list[Callable[[], None]] | None = None,
) -> None:
    """注册 SIGTERM 和 SIGINT 信号处理器。

    注册后，当进程收到 SIGTERM（kill）或 SIGINT（Ctrl+C）时，
    会设置 graceful_shutdown_requested 事件标志，
    并在注册了回调时尝试保存训练检查点和执行清理。

    此函数是幂等的：多次调用只会注册一次。

    Args:
        checkpoint_callback: 训练检查点保存回调函数。
            无参数，返回 bool 表示是否保存成功。
            通常绑定到训练 tracker 的 save_checkpoint 方法。
        cleanup_callbacks: 清理回调函数列表。
            每个回调无参数无返回值，用于释放资源（如关闭文件、
            停止线程池等）。
    """
    global _checkpoint_callback, _cleanup_callbacks, _handlers_registered

    with _registration_lock:
        if _handlers_registered:
            logger.debug("[信号处理] 信号处理器已注册，跳过重复注册")
            return

        # 保存回调
        if checkpoint_callback is not None:
            _checkpoint_callback = checkpoint_callback
        if cleanup_callbacks is not None:
            _cleanup_callbacks.extend(cleanup_callbacks)

        # Windows 不支持 SIGTERM，仅注册 SIGINT
        signals_to_register = []
        if hasattr(signal, "SIGTERM"):
            signals_to_register.append(signal.SIGTERM)
        if hasattr(signal, "SIGINT"):
            signals_to_register.append(signal.SIGINT)

        for sig in signals_to_register:
            try:
                _original_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, _signal_handler)
                sig_name = signal.Signals(sig).name if hasattr(signal, "Signals") else str(sig)
                logger.info(f"[信号处理] 已注册 {sig_name} 信号处理器")
            except (OSError, ValueError) as e:
                # 某些信号在特定环境下无法注册（如子线程中）
                sig_name = signal.Signals(sig).name if hasattr(signal, "Signals") else str(sig)
                logger.warning(f"[信号处理] 无法注册 {sig_name} 信号处理器: {e}")

        _handlers_registered = True
        logger.info("[信号处理] 信号处理器注册完成")


def unregister_signal_handlers() -> None:
    """恢复原始信号处理器，取消优雅关闭机制。

    调用后，SIGTERM/SIGINT 将恢复为注册前的行为
    （通常为默认的进程终止）。
    同时重置 graceful_shutdown_requested 标志。
    """
    global _checkpoint_callback, _cleanup_callbacks, _handlers_registered

    with _registration_lock:
        if not _handlers_registered:
            logger.debug("[信号处理] 信号处理器未注册，跳过注销")
            return

        for sig, original_handler in _original_handlers.items():
            try:
                signal.signal(sig, original_handler)
                sig_name = signal.Signals(sig).name if hasattr(signal, "Signals") else str(sig)
                logger.info(f"[信号处理] 已恢复 {sig_name} 原始处理器")
            except (OSError, ValueError) as e:
                sig_name = signal.Signals(sig).name if hasattr(signal, "Signals") else str(sig)
                logger.warning(f"[信号处理] 无法恢复 {sig_name} 信号处理器: {e}")

        _original_handlers.clear()
        _checkpoint_callback = None
        _cleanup_callbacks.clear()
        _handlers_registered = False

        # 重置关闭标志
        graceful_shutdown_requested.clear()

        logger.info("[信号处理] 信号处理器已注销")


def check_graceful_shutdown() -> bool:
    """检查是否已收到优雅关闭请求。

    生成循环中应定期调用此函数，在安全点检查是否需要停止。

    用法::

        for chunk in generate_stream(...):
            if check_graceful_shutdown():
                logger.info("收到关闭信号，停止生成")
                break
            yield chunk

    Returns:
        True 表示已请求优雅关闭，生成应尽快安全终止。
        False 表示正常继续。
    """
    return graceful_shutdown_requested.is_set()


def wait_for_shutdown(timeout: float | None = None) -> bool:
    """阻塞等待优雅关闭请求。

    主要用于主线程等待工作线程收到信号后的响应。

    Args:
        timeout: 最大等待秒数。为 None 时无限等待。

    Returns:
        True 表示收到关闭信号，False 表示超时。
    """
    return graceful_shutdown_requested.wait(timeout=timeout)


def reset_shutdown_flag() -> None:
    """重置优雅关闭标志。

    在处理完关闭请求后（如完成检查点保存、清理资源），
    可调用此函数重置标志，以便后续逻辑判断关闭流程已完成。

    注意：此函数不会恢复信号处理器，仅重置事件标志。
    """
    graceful_shutdown_requested.clear()
    logger.debug("[信号处理] 已重置关闭标志")


def is_shutdown_requested() -> bool:
    """查询当前是否已请求优雅关闭（非阻塞）。

    与 check_graceful_shutdown() 相同，但名称更明确，
    适用于非生成场景的检查。

    Returns:
        True 表示已请求关闭。
    """
    return graceful_shutdown_requested.is_set()


# ---------------------------------------------------------------------------
# 训练集成辅助
# ---------------------------------------------------------------------------


def create_training_checkpoint_callback(
    training_state: Any,
    output_dir: str,
) -> Callable[[], bool]:
    """创建训练检查点保存回调函数。

    将训练状态对象与检查点保存逻辑绑定，
    生成符合 register_signal_handlers() 签名的回调函数。

    Args:
        training_state: TrainingState 实例，包含 model、optimizer、
            scheduler、tracker 等。
        output_dir: 检查点输出目录路径。

    Returns:
        检查点保存回调函数，无参数，返回 bool 表示是否成功。
    """
    from pathlib import Path

    import torch

    output_path = Path(output_dir)

    def _save_checkpoint() -> bool:
        """保存训练检查点。"""
        try:
            output_path.mkdir(parents=True, exist_ok=True)

            # 获取模型（处理 DDP 包装）
            model = training_state.generator
            if hasattr(model, "module"):
                model = model.module

            checkpoint = {
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": training_state.optimizer.state_dict(),
                "scheduler_state_dict": (
                    training_state.scheduler.state_dict() if hasattr(training_state.scheduler, "state_dict") else {}
                ),
                "tracker_state_dict": (
                    training_state.tracker.state_dict() if hasattr(training_state.tracker, "state_dict") else {}
                ),
            }

            checkpoint_path = output_path / "shutdown_checkpoint.pt"
            torch.save(checkpoint, str(checkpoint_path))
            logger.info(f"[信号处理] 检查点已保存至: {checkpoint_path}")
            return True

        except Exception as e:
            logger.error(f"[信号处理] 检查点保存失败: {e}")
            return False

    return _save_checkpoint


# ---------------------------------------------------------------------------
# 上下文管理器便捷用法
# ---------------------------------------------------------------------------


class SignalHandlerContext:
    """信号处理器上下文管理器，用于临时注册和自动注销信号处理器。

    用法::

        with SignalHandlerContext(checkpoint_callback=save_fn):
            # 此区间内 SIGTERM/SIGINT 会触发优雅关闭
            train_model(...)
        # 离开上下文后自动恢复原始信号处理器

    Args:
        checkpoint_callback: 训练检查点保存回调。
        cleanup_callbacks: 清理回调列表。
    """

    def __init__(
        self,
        checkpoint_callback: Callable[[], bool] | None = None,
        cleanup_callbacks: list[Callable[[], None]] | None = None,
    ):
        self._checkpoint_callback = checkpoint_callback
        self._cleanup_callbacks = cleanup_callbacks

    def __enter__(self):
        register_signal_handlers(
            checkpoint_callback=self._checkpoint_callback,
            cleanup_callbacks=self._cleanup_callbacks,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        unregister_signal_handlers()
        return False
