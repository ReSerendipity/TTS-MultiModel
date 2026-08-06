"""统一 SSE 事件流端点 — 单 TCP 连接多路复用所有推送事件。

架构角色：
    提供统一 SSE 事件流端点 ``GET /api/sse/events``；前端通过
    ``new EventSource("/api/sse/events")`` 订阅所有后台推送，无需为不同
    事件类型建立多条长连接。支持事件类型：
    ``progress`` / ``complete`` / ``cancelled`` / ``error`` /
    ``status`` / ``engine_switch`` / ``time_estimate`` / ``queue_status``。

事件总线 EventBus 单例：
    生产者（model_manager / ProgressManager / GenerationTracker）通过
    ``event_bus.notify(event_dict)`` 将事件放入所有 subscriber 各自的
    ``asyncio.Queue`` 中；消费者 SSE 流异步生成器 ``async for`` 从队列
    弹出事件，封装为标准 SSE 帧 ``event: {type}\\ndata: {JSON}\\n\\n`` 推送。
    为兼容旧代码，同时保留基于 ``asyncio.Event`` 的 ``notify()`` + ``wait()``
    唤醒机制（不携带 payload，仅用于状态变化时打断轮询 sleep）。

与轮询的替代关系：
    遵循 AGENTS.md §6「SSE 状态推送」要求，前端禁止使用 setInterval 轮询，
    统一使用 EventSource 订阅本端点。心跳 + 事件驱动推送保证实时性的同时，
    将空闲连接 CPU 占用降到接近 0。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, ClassVar

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger("tts_multimodel.sse")

router = APIRouter(tags=["sse"])


@dataclass
class SSEEvent:
    """标准 SSE 事件数据结构。

    所有推送给前端的事件统一包装为该结构，由 ``_format_sse_frame`` 序列化为
    标准 SSE 文本帧（event / id / retry / data 行）。

    Attributes:
        type: 事件类型，对应 SSE ``event:`` 行；前端 EventSource.onmessage 之外
            用 ``addEventListener(type, ...)`` 分派。
        data: 事件负载，序列化为 JSON 后作为 SSE ``data:`` 行。
        id: 可选事件 ID，写入 ``id:`` 行；断线重连时浏览器作为
            ``Last-Event-ID`` 头回传（当前未实现历史回放，留作扩展）。
        retry: 可选浏览器重连间隔（毫秒），写入 ``retry:`` 行。
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    #: 默认浏览器重连间隔（毫秒）。None 时不写 retry 行，浏览器使用内置默认。
    retry: int | None = None


def _format_sse_frame(event: SSEEvent) -> str:
    """将 SSEEvent 序列化为标准 SSE 文本帧。

    格式严格遵循 W3C Server-Sent Events 规范：
      - 空行（``\\n\\n``）表示一帧结束
      - 以冒号开头的行是注释，被客户端忽略

    Args:
        event: 待序列化事件。

    Returns:
        可直接写入 SSE 流的字符串，结尾已包含帧分隔符 ``\\n\\n``。
    """
    lines: list[str] = []
    lines.append(f"event: {event.type}")
    if event.id is not None:
        lines.append(f"id: {event.id}")
    # 默认 retry: 3000ms — 指导浏览器断线重连间隔（None 时浏览器用内置默认 ~3s）
    lines.append(f"retry: {event.retry if event.retry is not None else 3000}")
    try:
        data_json = json.dumps(event.data, ensure_ascii=False)
    except (TypeError, ValueError, UnicodeEncodeError) as e:
        logger.debug("SSEEvent data JSON 序列化失败，回退空对象: %s", e)
        data_json = "{}"
    # data 行若含多行需每行前缀 data:（此处 JSON 单行可安全拼接）
    lines.append(f"data: {data_json}")
    return "\n".join(lines) + "\n\n"


# Why `: ping\n\n` 注释心跳而非 `event: heartbeat`：
# SSE 标准中以冒号开头的行为「注释行」，所有浏览器客户端都会自动忽略，
# 不会触发 onmessage 或任何 addEventListener 回调。
# 若使用 `event: heartbeat` 帧，则会被分派到 onmessage 事件，前端需要额外
# 判断并过滤；纯注释帧既保持 TCP / Nginx 代理链路不被 60s idle 超时切断，
# 又不会干扰上层业务事件分派逻辑，语义更干净。
_SSE_HEARTBEAT_COMMENT: str = ": ping\n\n"


class SSEEventBus:
    """SSE 事件总线 — 订阅队列 + Event 唤醒双模式。

    双模式设计（向后兼容）：
      1. **订阅队列模式（新）**：每个 SSE 连接 ``subscribe()`` 领取独立
         ``asyncio.Queue``；``notify()`` 广播时 payload 投递到所有队列，
         超出容量丢弃最早元素。适用于需要携带事件 payload 的场景。
      2. **Event 唤醒模式（旧）**：仅通过 ``asyncio.Event`` 通知状态变化，
         不携带 payload，消费者被唤醒后主动去 ``_progress_mgr`` 等对象拉取。
         保留用于兼容现有代码的轮询 + 打断机制。
    """

    _DEFAULT_MAX_QUEUE_SIZE: ClassVar[int] = 1000
    _HEARTBEAT_INTERVAL_S: ClassVar[float] = 15.0

    def __init__(self, max_queue_size: int = 1000) -> None:
        """初始化事件总线。

        Args:
            max_queue_size: 每个 subscriber 队列的最大容量；超出时投递新事件
                会静默丢弃最早的队头元素（LRU 丢弃策略），防止慢速客户端
                导致内存无限增长。
        """
        self._max_queue_size: int = max_queue_size or self._DEFAULT_MAX_QUEUE_SIZE
        self._subscribers: dict[str, asyncio.Queue[SSEEvent | dict[str, Any]]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._shutdown: bool = False
        # 兼容旧代码的 Event 唤醒机制
        self._event: asyncio.Event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # 订阅队列模式 API
    # ------------------------------------------------------------------
    async def subscribe(
        self,
        client_id: str | None = None,
    ) -> tuple[str, asyncio.Queue[SSEEvent | dict[str, Any]]]:
        """注册一个新的订阅者，返回 (client_id, queue)。

        Args:
            client_id: 可选指定客户端 ID；若为 None 则自动生成 16 hex。
                显式传入时需调用方保证全局唯一。

        Returns:
            ``(client_id, queue)`` 二元组：consumer 从 queue 中
            ``async for`` 读取 ``SSEEvent`` 或 ``dict``；连接关闭时
            调用 :meth:`unsubscribe` 释放资源。
        """
        async with self._lock:
            if client_id is None or not client_id:
                client_id = secrets.token_hex(8)
            # 防止重复 ID：碰撞时追加后缀
            suffix = 0
            base_id = client_id
            while client_id in self._subscribers:
                suffix += 1
                client_id = f"{base_id}_{suffix}"
            queue: asyncio.Queue[SSEEvent | dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue_size)
            self._subscribers[client_id] = queue
            logger.debug("SSE subscribe client_id=%s total=%d", client_id, len(self._subscribers))
            return client_id, queue

    async def unsubscribe(self, client_id: str) -> None:
        """取消订阅，移除对应队列以防止内存泄漏。

        Args:
            client_id: :meth:`subscribe` 返回的客户端 ID。传空或不存在时
                静默返回，保证幂等。
        """
        if not client_id:
            return
        async with self._lock:
            if client_id in self._subscribers:
                del self._subscribers[client_id]
                logger.debug(
                    "SSE unsubscribe client_id=%s total=%d",
                    client_id,
                    len(self._subscribers),
                )

    def notify(self, event: Any = None) -> None:
        """广播事件到所有订阅者，并触发旧代码的 Event 唤醒。

        线程安全：可从非 asyncio 线程（如线程池中的生成线程）调用。

        Args:
            event: 当传入 ``SSEEvent`` 或 ``dict`` 时，作为 payload 投递到每个
                subscriber 队列；当传入 ``None``（旧代码默认调用方式）时，
                仅触发 Event 唤醒不投递 payload。
        """
        # ---- 订阅队列模式投递 ----
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self._get_loop()

        if event is not None:
            subscribers_snapshot: dict[str, asyncio.Queue[SSEEvent | dict[str, Any]]] = {}
            try:
                # 快照：避免长时间持有锁；self._subscribers 在 Python 中
                # dict.copy 是原子的（GIL 保护），不需要 async lock 做快照
                subscribers_snapshot = self._subscribers.copy()
            except Exception as e:
                logger.debug("SSE notify 订阅者快照失败: %s", e)

            for cid, q in subscribers_snapshot.items():
                try:
                    if q.full():
                        # 单 subscriber queue 满：静默丢弃最早的队头，再放入新的
                        # 使用 logger.debug 级别避免慢速客户端刷爆日志
                        with contextlib.suppress(asyncio.QueueEmpty):
                            q.get_nowait()
                        logger.debug("SSE notify queue 已满，丢弃最早事件 client_id=%s", cid)
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    # 极端并发：put_nowait 仍可能失败（上面 discard 后又被别的协程 put）
                    logger.debug("SSE notify queue 仍然满，跳过本事件 client_id=%s", cid)
                except Exception as e:
                    logger.debug("SSE notify 单订阅者投递异常 client_id=%s: %s", cid, e)

        # ---- 兼容旧代码：Event 唤醒机制 ----
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(self._event.set)
                loop.call_later(0.05, self._event.clear)
                return
            except (RuntimeError, AttributeError):
                pass
        # 降级：直接 set + 稍后 clear（非 event loop 线程时的兜底）
        self._event.set()
        try:
            inner_loop = self._get_loop()
            if inner_loop is not None and inner_loop.is_running():
                inner_loop.call_later(0.05, self._event.clear)
            else:
                time.sleep(0.05)
                self._event.clear()
        except Exception:
            with contextlib.suppress(Exception):
                self._event.clear()

    async def wait(self, timeout: float = 1.0) -> bool:
        """等待事件通知，带超时（旧代码轮询兼容 API）。

        Args:
            timeout: 超时时间（秒），超时后返回 False 不抛异常。

        Returns:
            True 表示在超时窗口内收到通知；False 表示超时。
        """
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _get_loop(self) -> asyncio.AbstractEventLoop | None:
        """获取当前事件循环，缓存以供非 asyncio 线程使用。

        Returns:
            运行中的事件循环；若取不到则返回 None。
        """
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                try:
                    self._loop = asyncio.get_event_loop()
                except RuntimeError:
                    self._loop = None
        return self._loop

    async def _heartbeat_task(self, queue: asyncio.Queue[SSEEvent | dict[str, Any]]) -> None:
        """向指定 queue 周期性注入心跳注释帧（作为特殊 SSEEvent）。

        Why 将心跳也放入队列而非在流生成器中单独 sleep：
            心跳与业务事件共享同一个队列输出点，保证帧输出顺序正确；
            同时连接断开时只需取消本任务并销毁队列即可完全清理。

        Args:
            queue: subscriber 对应的投递队列。
        """
        try:
            while True:
                await asyncio.sleep(self._HEARTBEAT_INTERVAL_S)
                # 心跳以特殊 type='__heartbeat__' 放入队列，流生成器识别后
                # 直接输出注释帧而非 event: 帧
                heartbeat_evt = SSEEvent(type="__heartbeat__", data={})
                try:
                    if queue.full():
                        with contextlib.suppress(asyncio.QueueEmpty):
                            queue.get_nowait()
                    queue.put_nowait(heartbeat_evt)
                except asyncio.QueueFull:
                    pass
        except asyncio.CancelledError:
            # CancelledError 是正常生命周期结束：记录 debug 而非 warning，
            # 避免每次用户关闭标签页都产生告警日志
            logger.debug("SSE heartbeat 任务正常取消")
            raise
        except Exception as e:
            logger.error("SSE heartbeat 任务异常退出: %s", e, exc_info=True)
            raise


# 全局事件总线单例
event_bus: SSEEventBus = SSEEventBus()


def _format_time_estimate(seconds: float, lang: str = "zh") -> str:
    """将秒级时长格式化为人类可读的时间估算文本。

    Args:
        seconds: 剩余秒数。
        lang: 语言代码（zh/en/ja/ko），用于 i18n 查询。

    Returns:
        本地化时间估算字符串，如"约 2 分 30 秒"。
    """
    from ..i18n import t

    if seconds < 10:
        return t("sse_time_few_seconds", lang) if lang != "zh" else "几秒后完成"
    elif seconds < 60:
        about = t("sse_time_about", lang) if lang != "zh" else "约"
        unit_sec = t("sse_time_seconds", lang) if lang != "zh" else "秒"
        return f"{about} {int(seconds)} {unit_sec}"
    else:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        about = t("sse_time_about", lang) if lang != "zh" else "约"
        unit_min = t("sse_time_minutes", lang) if lang != "zh" else "分"
        unit_sec = t("sse_time_seconds", lang) if lang != "zh" else "秒"
        return f"{about} {mins} {unit_min} {secs} {unit_sec}"


@router.get("/api/sse/events", summary="SSE 事件流", description="Server-Sent Events 实时事件推送端点")
async def sse_events(request: Request) -> StreamingResponse:
    """统一 SSE 事件流端点。

    前端通过单一 ``EventSource`` 连接本端点，根据 event 字段分派到不同 UI
    更新逻辑（进度条 / 状态 / 引擎切换 / 时间估算等）。

    生命周期：
      1. ``subscribe`` 获取 client_id 与队列。
      2. 创建心跳后台任务。
      3. 双源合并：队列事件 + 原轮询逻辑（兼容现有状态管理）。
      4. ``request.is_disconnected()`` 检测到浏览器断开时，finally 块
         取消心跳任务并 ``unsubscribe``，防止 subscriber 字典内存泄漏。

    Why 单端点多事件类型而非 /api/sse/progress、/api/sse/engine 多端点：
        Chrome / Edge / Safari 对同域名 EventSource 连接数存在 6 条的硬限制
        （HTTP/1.1 全局 6 连接池共享）。拆分为多个端点会快速耗尽连接配额，
        导致后续 API 请求被阻塞排队。单端点复用 1 条 TCP 连接，配合
        ``event: type`` 行做逻辑多路分发，既节省资源又统一调度策略。

    Args:
        request: FastAPI 请求对象。

    Returns:
        ``StreamingResponse`` (media_type=text/event-stream)。
    """
    from ..config import get_config
    from ..i18n import get_lang
    from ..i18n import t as i18n_t
    from ..model_manager import _gen_tracker, _progress_mgr
    from ..model_registry import registry

    config = get_config()
    sse_cfg = config.pydantic_config.sse
    lang = get_lang(request)

    # ---- 订阅队列 & 心跳 ----
    client_id, event_queue = await event_bus.subscribe()
    heartbeat_task: asyncio.Task[None] | None = asyncio.create_task(
        event_bus._heartbeat_task(event_queue),
        name=f"sse-heartbeat-{client_id}",
    )

    async def event_stream() -> AsyncIterator[str]:
        """SSE事件流内部异步生成器，负责实际的事件推送与心跳维护"""
        gen_start_time: float | None = None
        last_depth: int = 0
        idle_count: int = 0
        last_heartbeat_ts: float = time.time()

        try:
            while True:
                if await request.is_disconnected():
                    break

                # ---- 优先消费订阅队列中的 SSEEvent ----
                queue_event: SSEEvent | dict[str, Any] | None = None
                try:
                    queue_event = event_queue.get_nowait()
                except asyncio.QueueEmpty:
                    queue_event = None

                if queue_event is not None:
                    if isinstance(queue_event, SSEEvent):
                        if queue_event.type == "__heartbeat__":
                            yield _SSE_HEARTBEAT_COMMENT
                            last_heartbeat_ts = time.time()
                        else:
                            yield _format_sse_frame(queue_event)
                    elif isinstance(queue_event, dict):
                        # 兼容 dict 形式：按 {type, data, id, retry} 结构处理
                        evt_type = str(queue_event.get("type", "message"))
                        evt_data = queue_event.get("data", {})
                        if not isinstance(evt_data, dict):
                            evt_data = {"value": evt_data}
                        evt_id = queue_event.get("id")
                        evt_retry = queue_event.get("retry")
                        yield _format_sse_frame(
                            SSEEvent(
                                type=evt_type,
                                data=evt_data if isinstance(evt_data, dict) else {},
                                id=str(evt_id) if evt_id is not None else None,
                                retry=int(evt_retry) if evt_retry is not None else None,
                            )
                        )
                    else:
                        # 不识别类型：作为通用 message 事件输出
                        yield _format_sse_frame(SSEEvent(type="message", data={"payload": str(queue_event)}))
                    # 队列中有事件立即进入下一轮，不进入轮询分支
                    continue

                # ---- 原有轮询逻辑（兼容状态管理）----
                progress_status = _progress_mgr.get_status() if _progress_mgr else {}

                # ---- progress 事件 ----
                if progress_status.get("is_active", False):
                    html = _progress_mgr.get_progress_html()
                    progress_data = json.dumps(
                        {
                            "html": html or "",
                            "phase": progress_status.get("phase", ""),
                            "progress": int(
                                progress_status.get("current_segment", 0)
                                / max(progress_status.get("total_segments", 1), 1)
                                * 100
                            )
                            if progress_status.get("total_segments", 1) > 0
                            else 0,
                            "speed": "",
                            "remaining": "",
                        },
                        ensure_ascii=False,
                    )
                    yield f"event: progress\ndata: {progress_data}\n\n"
                    if progress_status.get("is_complete", False):
                        yield "event: complete\ndata: done\n\n"
                        await asyncio.sleep(1)
                        _progress_mgr.reset()
                        event_bus.notify()

                # ---- cancelled 事件 ----
                if progress_status.get("is_cancelled", False):
                    data = json.dumps(
                        {
                            "status": "cancelled",
                            "message": i18n_t("sse_generation_cancelled", lang),
                        },
                        ensure_ascii=False,
                    )
                    yield f"event: cancelled\ndata: {data}\n\n"

                # ---- error 事件 ----
                if progress_status.get("is_error", False):
                    data = json.dumps(
                        {
                            "status": "error",
                            "message": progress_status.get("phase", "生成失败"),
                        },
                        ensure_ascii=False,
                    )
                    yield f"event: error\ndata: {data}\n\n"

                # ---- status 事件 ----
                tracker_info = _gen_tracker.get_info() if _gen_tracker else {}
                status_text = tracker_info.get("status_text", i18n_t("sse_status_idle", lang))
                eng = registry.current_engine or "none"
                mtype = registry.current_type or "none"
                msize = registry.current_size or "none"
                status_data = json.dumps(
                    {
                        "status_text": status_text,
                        "engine": eng,
                        "model_type": mtype,
                        "model_size": msize,
                        "model_loaded": registry.model_loaded,
                    },
                    ensure_ascii=False,
                )
                yield f"event: status\ndata: {status_data}\n\n"

                # ---- engine_switch 事件 ----
                switch_state = getattr(request.app.state, "engine_switch_state", None)
                if switch_state is None:
                    es_data = json.dumps(
                        {
                            "active": False,
                            "step": "",
                            "status": "idle",
                            "engine": "",
                            "model_size": "None",
                        },
                        ensure_ascii=False,
                    )
                    yield f"event: engine_switch\ndata: {es_data}\n\n"
                else:
                    step = switch_state.get("step", "")
                    sstatus = switch_state.get("status", "in_progress")
                    error = switch_state.get("error", None)
                    engine = switch_state.get("engine", "")
                    from ..model_registry import ENGINE_DISPLAY_NAMES

                    default_size = ENGINE_DISPLAY_NAMES.get(registry.current_engine, registry.current_engine or "None")
                    model_size = switch_state.get("model_size", default_size)
                    es_data = json.dumps(
                        {
                            "active": True,
                            "step": step,
                            "status": sstatus,
                            "error": error,
                            "engine": engine,
                            "model_size": model_size,
                        },
                        ensure_ascii=False,
                    )
                    yield f"event: engine_switch\ndata: {es_data}\n\n"
                    if sstatus in ("completed", "failed") and hasattr(request.app.state, "engine_switch_state"):
                        del request.app.state.engine_switch_state

                # ---- model_load 事件（PF-1: 模型加载细粒度进度）----
                load_state = getattr(request.app.state, "model_load_state", None)
                if load_state is None:
                    ml_data = json.dumps(
                        {
                            "active": False,
                            "step": "",
                            "status": "idle",
                            "engine": "",
                            "error": None,
                        },
                        ensure_ascii=False,
                    )
                    yield f"event: model_load\ndata: {ml_data}\n\n"
                else:
                    ml_step = load_state.get("step", "")
                    ml_status = load_state.get("status", "in_progress")
                    ml_error = load_state.get("error", None)
                    ml_engine = load_state.get("engine", "")
                    ml_data = json.dumps(
                        {
                            "active": True,
                            "step": ml_step,
                            "status": ml_status,
                            "error": ml_error,
                            "engine": ml_engine,
                        },
                        ensure_ascii=False,
                    )
                    yield f"event: model_load\ndata: {ml_data}\n\n"
                    if ml_status in ("completed", "failed") and hasattr(request.app.state, "model_load_state"):
                        del request.app.state.model_load_state

                # ---- time_estimate 事件 ----
                if _gen_tracker:
                    current_depth = tracker_info.get("queue_depth", 0)
                    if current_depth > 0:
                        if last_depth == 0:
                            gen_start_time = time.time()
                        remaining = _gen_tracker.estimate_wait()
                        elapsed = time.time() - gen_start_time if gen_start_time else 0
                        est_text = _format_time_estimate(remaining, lang)
                        te_data = json.dumps(
                            {
                                "status": "generating",
                                "elapsed": round(elapsed, 1),
                                "remaining": round(remaining, 1),
                                "total_est": round(remaining + elapsed, 1),
                                "text": est_text,
                            },
                            ensure_ascii=False,
                        )
                        yield f"event: time_estimate\ndata: {te_data}\n\n"
                    else:
                        if last_depth > 0 and gen_start_time:
                            actual = time.time() - gen_start_time
                            te_data = json.dumps(
                                {
                                    "status": "complete",
                                    "actual": round(actual, 1),
                                    "text": i18n_t("sse_generation_complete", lang),
                                },
                                ensure_ascii=False,
                            )
                            yield f"event: time_estimate\ndata: {te_data}\n\n"
                            gen_start_time = None
                        else:
                            te_data = json.dumps(
                                {
                                    "status": "idle",
                                    "text": tracker_info.get("status_text", i18n_t("sse_status_idle", lang)),
                                },
                                ensure_ascii=False,
                            )
                            yield f"event: time_estimate\ndata: {te_data}\n\n"
                    last_depth = current_depth

                # ---- 兜底心跳（当心跳任务因异常未执行时）----
                if time.time() - last_heartbeat_ts >= sse_cfg.heartbeat_interval:
                    yield _SSE_HEARTBEAT_COMMENT
                    last_heartbeat_ts = time.time()

                # ---- 动态等待间隔 ----
                has_active = False
                if progress_status.get("is_active", False):
                    has_active = True
                if _gen_tracker and tracker_info.get("queue_depth", 0) > 0:
                    has_active = True
                switch_state = getattr(request.app.state, "engine_switch_state", None)
                if switch_state is not None:
                    has_active = True
                load_state_active = getattr(request.app.state, "model_load_state", None)
                if load_state_active is not None:
                    has_active = True

                if has_active:
                    idle_count = 0
                    interval = sse_cfg.active_interval
                else:
                    idle_count += 1
                    interval = min(
                        sse_cfg.idle_base_interval + idle_count * sse_cfg.idle_step,
                        sse_cfg.idle_max_interval,
                    )

                # 等待 event_bus 通知（有 payload 或无 payload 的 Event）
                # 或超时进入下一轮轮询
                await event_bus.wait(timeout=interval)

        except asyncio.CancelledError:
            logger.debug("SSE event_stream 正常取消 client_id=%s", client_id)
            raise
        except Exception as e:
            logger.error("SSE stream error client_id=%s: %s", client_id, e, exc_info=True)
            try:
                error_data = json.dumps(
                    {
                        "status": "error",
                        "message": "SSE 连接异常，请刷新页面重试",
                    },
                    ensure_ascii=False,
                )
                yield f"event: error\ndata: {error_data}\n\n"
            except (TypeError, ValueError, UnicodeEncodeError) as inner:
                logger.debug("SSE error recovery 输出失败: %s", inner)
        finally:
            # ---- 资源清理：防止 subscriber 字典与心跳任务内存泄漏 ----
            if heartbeat_task is not None and not heartbeat_task.done():
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug("SSE heartbeat 任务取消收尾异常: %s", e)
            await event_bus.unsubscribe(client_id)
            logger.debug("SSE event_stream 清理完成 client_id=%s", client_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            # 告知浏览器和反向代理：SSE 连接不应缓冲，断线后 1s 自动重连
            "X-Accel-Redirect": "off",
        },
    )
