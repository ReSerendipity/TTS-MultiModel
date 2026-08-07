"""异步生成任务队列模块 —— 确保串行 TTS 推理以避免 GPU 竞争。

参考 VoiceBox 的 task_queue.py 实现，提供：
1. 异步串行队列：同一时间只运行一个 TTS 推理任务，避免 GPU 显存竞争
2. 任务取消机制：支持取消排队中或运行中的生成任务
3. 任务状态追踪：跟踪排队/运行中的任务
4. 后台任务管理：防止 fire-and-forget 任务被 GC 回收

架构说明：
    - 使用 asyncio.Queue 作为任务队列
    - 单 worker 协程串行处理任务
    - 支持取消排队任务（标记后跳过）和运行中任务（asyncio.Task.cancel()）
    - 异常安全：worker 异常时自动将任务标记为失败
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
import traceback
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("tts_multimodel")

_background_tasks: set[asyncio.Task] = set()


@dataclass
class GenerationJob:
    """排队中的生成任务项。

    Attributes:
        generation_id: 此生成任务的唯一标识符。
        coro: 此生成任务要执行的协程。
        created_at: 任务入队时的单调时间戳（用于等待时长统计）。
    """

    generation_id: str
    coro: Coroutine[Any, Any, Any]
    created_at: float = field(default_factory=time.monotonic)


_generation_queue: asyncio.Queue[GenerationJob] | None = None
_generation_worker_task: asyncio.Task | None = None
_queued_generation_ids: set[str] = set()
_running_generation_tasks: dict[str, asyncio.Task] = {}
_cancelled_generation_ids: set[str] = set()
_lock = asyncio.Lock()


def create_background_task(coro: Coroutine) -> asyncio.Task:
    """创建后台任务并防止其被垃圾回收。

    Args:
        coro: 要作为后台任务运行的协程。

    Returns:
        创建的 asyncio.Task 实例。
    """
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def _generation_worker() -> None:
    """Worker 协程，逐个处理生成任务。

    作为后台任务运行，确保同一时间只有一个 TTS 推理运行，
    以避免 GPU 显存竞争。
    """
    while True:
        try:
            job = await _generation_queue.get()
        except asyncio.CancelledError:
            logger.info("[TaskQueue] 生成 worker 已取消")
            break
        except Exception as exc:
            logger.error("[TaskQueue] 从队列获取任务时出错: %s", exc)
            await asyncio.sleep(0.1)
            continue

        try:
            if job.generation_id in _cancelled_generation_ids:
                async with _lock:
                    _cancelled_generation_ids.discard(job.generation_id)
                    _queued_generation_ids.discard(job.generation_id)
                wait_seconds = time.monotonic() - job.created_at
                logger.debug("[TaskQueue] 跳过已取消的任务: %s (排队 %.2fs)", job.generation_id, wait_seconds)
                # 安全关闭协程，防止资源泄漏
                if inspect.getcoroutinestate(job.coro) == "CORO_CREATED":
                    job.coro.close()
                _generation_queue.task_done()
                continue

            wait_seconds = time.monotonic() - job.created_at
            logger.debug("[TaskQueue] 开始处理任务: %s (排队 %.2fs)", job.generation_id, wait_seconds)

            task = asyncio.create_task(job.coro)
            async with _lock:
                _running_generation_tasks[job.generation_id] = task
                _queued_generation_ids.discard(job.generation_id)

            try:
                await task
            except asyncio.CancelledError:
                logger.info("[TaskQueue] 生成已取消: %s", job.generation_id)
                if not task.cancelled():
                    task.cancel()
            except Exception as exc:
                logger.error(
                    "[TaskQueue] 生成任务 %s 失败: %s\n%s",
                    job.generation_id,
                    exc,
                    traceback.format_exc(),
                )
                await _notify_generation_failed(job.generation_id, str(exc))
        except Exception as exc:
            logger.error("[TaskQueue] Worker 处理任务时出错: %s", exc)
            traceback.print_exc()
            await _force_fail_if_active(
                job.generation_id,
                f"Worker 意外退出: {exc}",
            )
        finally:
            async with _lock:
                _running_generation_tasks.pop(job.generation_id, None)
                _queued_generation_ids.discard(job.generation_id)
                _cancelled_generation_ids.discard(job.generation_id)
            _generation_queue.task_done()


async def _notify_generation_failed(generation_id: str, error: str) -> None:
    """通过 SSE 通知生成失败。

    Args:
        generation_id: 失败的生成 ID。
        error: 错误消息。
    """
    try:
        from .routes.sse import SSEEvent, event_bus

        event_bus.notify(
            SSEEvent(
                type="generation_failed",
                data={
                    "generation_id": generation_id,
                    "error": error,
                },
            )
        )
    except Exception:
        pass


async def _force_fail_if_active(generation_id: str, error: str) -> None:
    """尽力恢复 —— 将活跃生成标记为失败。

    在 worker 未能写入终止状态而退出时调用。

    Args:
        generation_id: 要标记为失败的生成 ID。
        error: 描述失败的错误消息。
    """
    try:
        logger.error("[TaskQueue] 强制标记生成失败 %s: %s", generation_id, error)
        await _notify_generation_failed(generation_id, error)
    except Exception:
        traceback.print_exc()


async def enqueue_generation(generation_id: str, coro: Coroutine) -> None:
    """将生成协程添加到串行队列。

    Args:
        generation_id: 此生成的唯一标识符。
        coro: 要执行的协程。

    Raises:
        RuntimeError: 队列尚未初始化时抛出。
    """
    if _generation_queue is None:
        coro.close()
        raise RuntimeError("生成队列尚未初始化。请先调用 init_queue()。")

    async with _lock:
        _queued_generation_ids.add(generation_id)

    job = GenerationJob(generation_id=generation_id, coro=coro)
    try:
        await _generation_queue.put(job)
    except Exception:
        async with _lock:
            _queued_generation_ids.discard(generation_id)
            _cancelled_generation_ids.discard(generation_id)
        job.coro.close()
        raise
    logger.debug("[TaskQueue] 生成已入队: %s (队列深度: %d)", generation_id, _generation_queue.qsize())


def cancel_generation(generation_id: str) -> str | None:
    """取消排队中或运行中的生成任务（如果仍活跃）。

    Args:
        generation_id: 要取消的生成 ID。

    Returns:
        "running" 表示任务正在运行并已取消，
        "queued" 表示任务在排队中并已标记为取消，
        None 表示未找到该任务。
    """
    running_task = _running_generation_tasks.get(generation_id)
    if running_task is not None and not running_task.done():
        running_task.cancel()
        logger.info("[TaskQueue] 已取消运行中的生成: %s", generation_id)
        return "running"

    if generation_id in _queued_generation_ids:
        _queued_generation_ids.discard(generation_id)
        _cancelled_generation_ids.add(generation_id)
        logger.info("[TaskQueue] 已标记排队中的生成为取消: %s", generation_id)
        return "queued"

    return None


def is_generation_active(generation_id: str) -> bool:
    """检查生成当前是否活跃（排队中或运行中）。

    Args:
        generation_id: 要检查的生成 ID。

    Returns:
        如果生成正在排队或运行中返回 True。
    """
    return generation_id in _queued_generation_ids or generation_id in _running_generation_tasks


def get_queue_status() -> dict[str, Any]:
    """获取当前队列状态。

    Returns:
        包含队列状态信息的字典：
        - queued_count: 排队任务数
        - running_count: 运行中任务数（始终为 0 或 1）
        - queued_ids: 排队生成 ID 列表
        - running_ids: 运行中生成 ID 列表
    """
    return {
        "queued_count": len(_queued_generation_ids),
        "running_count": len(_running_generation_tasks),
        "queued_ids": list(_queued_generation_ids),
        "running_ids": list(_running_generation_tasks.keys()),
    }


async def _drain_and_close_pending_coros() -> None:
    """清空队列中尚未处理的 job 并关闭其协程，防止资源泄漏。

    在强制重建队列（``init_queue(force=True)``）或关闭队列（``shutdown_queue``）
    前调用：worker 被取消后，队列中可能残留从未启动的协程（例如排队中即被
    取消、或 worker 未及取出的任务），若直接丢弃队列，这些协程永远不会被
    await/close，产生 ``RuntimeWarning: coroutine was never awaited`` 且泄漏资源。
    """
    queue = _generation_queue
    if queue is None:
        return
    while True:
        try:
            job = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        try:
            # 仅关闭从未启动的协程；已启动的由 asyncio.Task 取消流程负责
            if inspect.getcoroutinestate(job.coro) == "CORO_CREATED":
                job.coro.close()
        finally:
            queue.task_done()


async def init_queue(force: bool = False) -> None:
    """初始化生成队列并启动 worker。

    必须在应用启动期间调用一次（在运行中的事件循环内）。

    Args:
        force: 如果为 True，在重新初始化前取消现有 worker 和任务。
    """
    global _generation_queue, _generation_worker_task
    global _queued_generation_ids, _running_generation_tasks, _cancelled_generation_ids

    if _generation_worker_task is not None and not _generation_worker_task.done():
        if not force:
            logger.debug("[TaskQueue] 队列已初始化")
            return
        logger.info("[TaskQueue] 强制重新初始化队列")
        _generation_worker_task.cancel()
        for task in list(_running_generation_tasks.values()):
            if not task.done():
                task.cancel()
        with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(_generation_worker_task, timeout=2.0)
        # 关闭旧队列中残留的未处理协程，防止资源泄漏（never awaited 警告）
        await _drain_and_close_pending_coros()

    _generation_queue = asyncio.Queue()
    _queued_generation_ids = set()
    _running_generation_tasks = {}
    _cancelled_generation_ids = set()
    _generation_worker_task = create_background_task(_generation_worker())
    logger.info("[TaskQueue] 生成队列已初始化")


async def shutdown_queue() -> None:
    """优雅关闭生成队列。

    取消 worker 任务和所有运行中的生成。
    """
    global _generation_queue, _generation_worker_task

    logger.info("[TaskQueue] 正在关闭生成队列")

    for task in list(_running_generation_tasks.values()):
        if not task.done():
            task.cancel()

    if _generation_worker_task is not None and not _generation_worker_task.done():
        _generation_worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(_generation_worker_task, timeout=2.0)

    # 关闭队列中残留的未处理协程，防止资源泄漏（never awaited 警告）
    await _drain_and_close_pending_coros()

    _generation_queue = None
    _generation_worker_task = None
    _queued_generation_ids.clear()
    _running_generation_tasks.clear()
    _cancelled_generation_ids.clear()

    logger.info("[TaskQueue] 生成队列已关闭")


# ======================================================================
# PerEngineQueueManager — 每引擎独立队列管理器
# ======================================================================


class PerEngineQueueManager:
    """每引擎独立队列管理器 — 支持不同引擎的生成任务并行处理。

    传统单队列模式下，所有引擎的生成任务共享一个 worker，
    如果 VoxCPM2 正在推理，IndexTTS2 的请求必须等待。
    本管理器为每个引擎创建独立的队列和 worker，允许不同引擎并行推理。

    注意:
        并行推理需要足够的显存同时容纳多个引擎。
        在显存受限时，应保持使用全局单队列（``enqueue_generation``）。

    Usage::

        from .task_queue import per_engine_queue_manager
        await per_engine_queue_manager.init_engine_queue("voxcpm2")
        await per_engine_queue_manager.enqueue("voxcpm2", "gen-1", my_coro())
    """

    def __init__(self) -> None:
        """初始化每引擎队列管理器。"""
        self._engine_queues: dict[str, asyncio.Queue[GenerationJob]] = {}
        self._engine_workers: dict[str, asyncio.Task] = {}
        self._engine_running: dict[str, dict[str, asyncio.Task]] = {}

    async def init_engine_queue(self, engine: str) -> None:
        """初始化指定引擎的独立队列和 worker。

        Args:
            engine: 引擎名称。
        """
        if engine in self._engine_queues:
            logger.debug("[PerEngineQueue] 队列已存在: %s", engine)
            return

        self._engine_queues[engine] = asyncio.Queue()
        self._engine_running[engine] = {}
        worker = create_background_task(self._engine_worker(engine))
        self._engine_workers[engine] = worker
        logger.info("[PerEngineQueue] 队列已初始化: %s", engine)

    async def _engine_worker(self, engine: str) -> None:
        """指定引擎的 worker 协程。"""
        queue = self._engine_queues[engine]
        running = self._engine_running[engine]
        while True:
            try:
                job = await queue.get()
            except asyncio.CancelledError:
                logger.info("[PerEngineQueue] worker 已取消: %s", engine)
                break
            except Exception as exc:
                logger.error("[PerEngineQueue] 获取任务出错 %s: %s", engine, exc)
                await asyncio.sleep(0.1)
                continue

            try:
                task = asyncio.create_task(job.coro)
                running[job.generation_id] = task
                try:
                    await task
                except asyncio.CancelledError:
                    logger.info("[PerEngineQueue] 生成已取消: %s", job.generation_id)
                except Exception as exc:
                    logger.error("[PerEngineQueue] 生成失败 %s: %s", job.generation_id, exc)
                    await _notify_generation_failed(job.generation_id, str(exc))
            except Exception as exc:
                logger.error("[PerEngineQueue] worker 出错 %s: %s", engine, exc)
            finally:
                running.pop(job.generation_id, None)
                queue.task_done()

    async def enqueue(self, engine: str, generation_id: str, coro: Coroutine) -> None:
        """将生成任务添加到指定引擎的队列。

        Args:
            engine: 引擎名称。
            generation_id: 生成任务 ID。
            coro: 要执行的协程。

        Raises:
            RuntimeError: 引擎队列未初始化时抛出。
        """
        if engine not in self._engine_queues:
            coro.close()
            raise RuntimeError(f"引擎 {engine} 的队列尚未初始化。请先调用 init_engine_queue()。")
        job = GenerationJob(generation_id=generation_id, coro=coro)
        await self._engine_queues[engine].put(job)
        logger.debug(
            "[PerEngineQueue] 已入队 %s -> %s (深度: %d)", generation_id, engine, self._engine_queues[engine].qsize()
        )

    def cancel(self, engine: str, generation_id: str) -> str | None:
        """取消指定引擎中的生成任务。

        Args:
            engine: 引擎名称。
            generation_id: 生成任务 ID。

        Returns:
            "running" / "queued" / None
        """
        running = self._engine_running.get(engine, {})
        task = running.get(generation_id)
        if task is not None and not task.done():
            task.cancel()
            return "running"
        return None

    def get_status(self, engine: str) -> dict[str, Any]:
        """获取指定引擎的队列状态。

        Args:
            engine: 引擎名称。

        Returns:
            队列状态字典。
        """
        queue = self._engine_queues.get(engine)
        running = self._engine_running.get(engine, {})
        return {
            "engine": engine,
            "queued_count": queue.qsize() if queue else 0,
            "running_count": len(running),
            "running_ids": list(running.keys()),
        }

    async def shutdown_engine(self, engine: str) -> None:
        """关闭指定引擎的队列和 worker。

        Args:
            engine: 引擎名称。
        """
        self._engine_queues.pop(engine, None)
        worker = self._engine_workers.pop(engine, None)
        running = self._engine_running.pop(engine, {})

        for task in running.values():
            if not task.done():
                task.cancel()

        if worker is not None and not worker.done():
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(worker, timeout=2.0)

        logger.info("[PerEngineQueue] 队列已关闭: %s", engine)

    async def shutdown_all(self) -> None:
        """关闭所有引擎的队列和 worker。"""
        engines = list(self._engine_queues.keys())
        for engine in engines:
            await self.shutdown_engine(engine)


#: 每引擎队列管理器单例
per_engine_queue_manager = PerEngineQueueManager()
