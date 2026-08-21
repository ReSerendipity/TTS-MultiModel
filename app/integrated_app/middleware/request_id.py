"""请求 ID 中间件 — 为每个入站 HTTP 请求分配全局唯一标识符。

架构角色：
    本模块实现 ASGI 全局请求 ID 中间件，为每个入站请求分配 UUID4（16 hex）
    request_id，并完成三件事：
      1. 将 request_id 注入 Python logging 上下文（通过 ContextVar + 线程本地镜像）
         使整条链路（middleware → route → model_manager → SSE 事件）的日志都能
         自动携带 request_id，用于 ELK/EFK 日志聚合与分布式链路追踪。
      2. 在响应中写入 ``X-Request-ID`` 头，供前端 / 反向代理关联。
      3. 对外暴露 ``get_request_id`` / ``set_request_id`` 工具函数，供后台线程
         （如模型加载、Persona 预热）主动发布其关联 ID。

中间件注册位置：
    在 ``app_server.create_app()`` 中被注册为 **第一个** 中间件。
    Why：后续 CSRF / Auth / error_handler 等所有中间件与路由 handler 的
    logger 输出均需要 request_id 做链路追踪，因此必须最先注入上下文。
"""

from __future__ import annotations

import contextvars
import logging
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")

_REQUEST_ID_SANITIZER = re.compile(r"[^A-Za-z0-9_\-]")
_MAX_REQUEST_ID_LEN: int = 64

logger = logging.getLogger("tts_multimodel.request_id")

_request_id_local = threading.local()


def get_request_id() -> str:
    """从异步上下文（ContextVar）获取当前 request_id。

    Returns:
        当前上下文关联的 request_id 字符串；若未设置则返回空字符串。
    """
    return _request_id_var.get()


def set_request_id(request_id: str) -> None:
    """发布 request_id 到 ContextVar 与线程本地镜像。

    用于后台线程（如模型加载、Persona 预热），使得其日志记录携带相同的
    关联 ID。在事件循环内 ContextVar 会自动传播；在线程池 / 普通线程中
    通过线程本地镜像保证 :class:`RequestIDLogFilter` 仍能取到值。

    Args:
        request_id: 需要发布的请求 ID 字符串。
    """
    _request_id_var.set(request_id)
    _request_id_local.request_id = request_id


def _sanitize_request_id(raw: str) -> str:
    """清理入站 X-Request-ID，防止日志注入。

    剥离控制字符 / 换行并限制最大长度，避免恶意客户端通过伪造
    ``\\n`` 等字符在日志文件中插入假日志条目。

    Args:
        raw: 原始入站 header 值。

    Returns:
        清理后的字符串。为空或全部非法字符时返回空串。
    """
    if not raw:
        return ""
    cleaned = _REQUEST_ID_SANITIZER.sub("", raw)[:_MAX_REQUEST_ID_LEN]
    return cleaned


class RequestIDMiddleware(BaseHTTPMiddleware):
    """为每个 HTTP 请求-响应循环附加全局唯一 request_id。

    处理顺序：
      1. 读取入站 ``X-Request-ID``（若存在则先清理），否则生成 UUID4。
      2. 写入 ContextVar + 线程本地镜像。
      3. 将 request_id 挂到 ``request.state`` 供业务 handler 使用。
      4. 调用下游。
      5. 回写响应头 ``X-Request-ID``（若已存在则不覆盖，兼容重复注册场景）。
      6. finally 中重置 ContextVar 与线程本地，避免 worker 线程复用导致 ID 泄漏。
    """

    def __init__(
        self,
        app: ASGIApp,
        header_name: str = "X-Request-ID",
    ) -> None:
        """初始化中间件。

        Args:
            app: 被包装的 ASGI 应用。
            header_name: 请求 / 响应头名称，默认 ``X-Request-ID``。
        """
        super().__init__(app)
        self._header_name: str = header_name

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Starlette 风格 dispatch，为请求注入 request_id 并回写响应头。

        Args:
            request: Starlette 请求对象。
            call_next: 下游 handler / 中间件调用入口。

        Returns:
            附加了 X-Request-ID 响应头的 Response 对象。
        """
        raw_id = request.headers.get(self._header_name, "")
        sanitized = _sanitize_request_id(raw_id)

        request_id: str
        if sanitized:
            request_id = sanitized
        else:
            try:
                # Why UUID4 16 hex 而非自增 int：
                # 分布式 / 多进程 / 多副本部署下自增 int 会冲突，
                # UUID4（取 16 hex 共 64 bit）碰撞概率极低且无需中心化发号器，
                # 可直接用于跨实例的日志聚合（ELK/EFK）追踪。
                request_id = uuid.uuid4().hex[:16]
            except (ValueError, TypeError) as e:
                # uuid 生成失败的理论兜底：使用纳秒时间戳回退
                # 确保 request_id 始终存在，不影响正常请求链路
                logger.debug("uuid.uuid4() 生成失败，使用时间戳回退: %s", e)
                request_id = f"req-{time.time_ns()}"

        token = _request_id_var.set(request_id)
        _request_id_local.request_id = request_id
        try:
            # Why 先注入 logging 上下文再 call_next：
            # 整个请求生命周期内（路由 handler → model_manager → SSE 事件推送）
            # 任何 logger.info / logger.error 都自动携带 request_id，
            # 无需在每处函数签名中手动传递 request_id 参数。
            request.state.request_id = request_id
            response = await call_next(request)

            # 兼容场景：中间件被重复注册或上游网关已设置 X-Request-ID
            # 时不覆盖，保留原始（最外层）ID 以保持链路连续性
            if self._header_name not in response.headers:
                response.headers[self._header_name] = request_id
            return response
        finally:
            try:
                _request_id_var.reset(token)
            except (ValueError, LookupError):
                logger.debug("ContextVar token 已被重置，跳过")
            _request_id_local.request_id = "-"

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Any],
        send: Callable[[Any], Awaitable[None]],
    ) -> None:
        """ASGI 调用入口，透传到 BaseHTTPMiddleware 的标准实现。

        Args:
            scope: ASGI scope 字典。
            receive: ASGI receive 可调用对象。
            send: ASGI send 可调用对象。
        """
        await super().__call__(scope, receive, send)


class RequestIDLogFilter(logging.Filter):
    """日志过滤器，将当前 request_id 注入每条 LogRecord。

    取值优先级：
      1. ContextVar（由中间件或 set_request_id 设置）——首选。
      2. 线程本地镜像（由后台线程通过 set_request_id 设置）。
      3. 兜底字符串 ``"-"``。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """向 record 注入 request_id 字段。

        Args:
            record: 待处理的日志记录。

        Returns:
            始终返回 True，让记录继续传递给后续 handler。
        """
        if not hasattr(record, "request_id"):
            rid = _request_id_var.get("")
            if not rid:
                rid = getattr(_request_id_local, "request_id", "-")
            record.request_id = rid
        return True
