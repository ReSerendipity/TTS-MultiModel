# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""轻量级 API 速率限制中间件。

P2 安全修复：防止单 IP 狂发生成请求打爆 GPU 资源。
使用滑动窗口算法，纯内存实现，无需外部依赖。

配置项（通过 config.yaml 的 rate_limit 节或环境变量）:
    - enabled: 是否启用（默认 true）
    - requests_per_minute: 每分钟最大请求数（默认 10，仅限 /api/generate/* 路径）
    - burst: 允许的突发请求数（默认 5）
"""

import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger("tts_multimodel")

#: 需要速率限制的路径前缀列表
_RATE_LIMITED_PREFIXES = ("/api/generate/", "/api/model/load", "/api/model/unload")

#: 默认配置
_DEFAULT_REQUESTS_PER_MINUTE = 10
_DEFAULT_BURST = 5
_WINDOW_SECONDS = 60.0


class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于滑动窗口的 API 速率限制中间件。

    仅对 ``_RATE_LIMITED_PREFIXES`` 中的路径生效，其他路径不受限制。
    使用 IP 地址作为限流 key，在内存中维护每个 IP 的请求时间戳列表。

    Attributes:
        requests_per_minute: 每分钟允许的最大请求数。
        burst: 允许的突发请求数（在窗口开始时允许的初始请求数）。
    """

    def __init__(
        self,
        app: ASGIApp,
        enabled: bool = True,
        requests_per_minute: int = _DEFAULT_REQUESTS_PER_MINUTE,
        burst: int = _DEFAULT_BURST,
    ) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.max_requests = max(requests_per_minute, 1)
        self.burst = max(burst, 1)
        # IP -> list of timestamps
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.time()

    def _get_client_ip(self, request: Request) -> str:
        """提取客户端 IP 地址。

        优先读取 X-Forwarded-For 头（反向代理场景），
        回退到 request.client.host。

        Args:
            request: FastAPI 请求对象。

        Returns:
            客户端 IP 地址字符串。
        """
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            # 取第一个 IP（最原始的客户端）
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _is_rate_limited(self, path: str) -> bool:
        """判断请求路径是否需要速率限制。

        Args:
            path: 请求路径。

        Returns:
            True 表示需要限流。
        """
        return any(path.startswith(prefix) for prefix in _RATE_LIMITED_PREFIXES)

    def _cleanup_old_entries(self) -> None:
        """定期清理过期的请求记录，防止内存泄漏。

        每隔 5 分钟执行一次，清理超过 2 倍窗口时间的记录。
        """
        now = time.time()
        if now - self._last_cleanup < 300:  # 5 分钟
            return
        self._last_cleanup = now
        cutoff = now - _WINDOW_SECONDS * 2
        empty_keys: list[str] = []
        for ip, timestamps in self._requests.items():
            self._requests[ip] = [t for t in timestamps if t > cutoff]
            if not self._requests[ip]:
                empty_keys.append(ip)
        for key in empty_keys:
            del self._requests[key]

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """中间件调度逻辑：对生成相关路径执行速率限制。

        Args:
            request: FastAPI 请求对象。
            call_next: 下一个中间件/路由处理器。

        Returns:
            正常响应或 429 Too Many Requests 响应。
        """
        if not self.enabled or not self._is_rate_limited(request.url.path):
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        now = time.time()

        self._cleanup_old_entries()

        # 清理当前 IP 的过期记录
        timestamps = self._requests[client_ip]
        cutoff = now - _WINDOW_SECONDS
        self._requests[client_ip] = [t for t in timestamps if t > cutoff]
        timestamps = self._requests[client_ip]

        # 检查是否超限
        if len(timestamps) >= self.max_requests:
            retry_after = int(_WINDOW_SECONDS - (now - timestamps[0])) + 1
            logger.warning(
                "[RateLimit] IP %s 请求频率超限: %d/%d (60s)，路径: %s",
                client_ip,
                len(timestamps),
                self.max_requests,
                request.url.path,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "status": "error",
                    "message": f"请求频率超限，每分钟最多 {self.max_requests} 次生成请求",
                    "retry_after": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

        # 记录本次请求
        timestamps.append(now)
        return await call_next(request)
