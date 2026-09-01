"""
middleware/security_headers.py — 安全响应头中间件

对应安全评估 M-02：项目此前未下发任何安全响应头（CSP / nosniff /
frame-ancestors / Referrer-Policy），且 CORS ``allowed_origins`` 中混入了
``ws://127.0.0.1:*`` 这类非法的 HTTP origin（WebSocket 不受 CORS 约束，该条目
既无效又容易被误读为"通配端口"）。

本模块统一补齐安全响应头。CSP 默认策略经过收敛：禁用 object/embed（防插件
执行）、``frame-ancestors 'none'``（防点击劫持）、``base-uri 'self'``（防
base 标签劫持相对 URL），同时**保留** ``'unsafe-inline'`` —— 因为现有前端
``static/js/app.js`` 大量使用内联 ``onclick`` 处理器与内联 ``style`` 属性，
一刀切会直接破坏 UI。待前端改为事件委托 + 外部样式后可收紧。

置于中间件栈最外层，确保中间件自身产生的 401/403/429 响应也携带安全头。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# 默认 CSP（保留 unsafe-inline 以兼容现有前端内联事件与内联样式）
_DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

# 与安全相关的固定响应头
_BASE_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """为所有 HTTP 响应注入安全响应头。

    Args:
        app: ASGI 应用。
        config: 可选的 AppConfig；为空时回退全局单例。
            读取 ``config.security.headers.enabled`` 与 ``.csp``。
    """

    def __init__(self, app, config: Any = None) -> None:
        super().__init__(app)
        self._config = config

    def _get_headers_config(self) -> Any | None:
        if self._config is not None:
            return getattr(getattr(self._config, "security", None), "headers", None)
        try:
            from ..config import get_config

            cfg = get_config()
            if cfg is None:
                return None
            return getattr(getattr(cfg, "security", None), "headers", None)
        except Exception:  # noqa: BLE001 - 配置不可用时按"已启用"处理，安全默认开
            return None

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        cfg = self._get_headers_config()
        # 未配置时安全默认为"开启"
        enabled = True if cfg is None else bool(getattr(cfg, "enabled", True))
        if not enabled:
            return response

        for key, value in _BASE_HEADERS.items():
            response.headers.setdefault(key, value)

        csp = getattr(cfg, "csp", "") if cfg is not None else ""
        response.headers.setdefault("Content-Security-Policy", csp or _DEFAULT_CSP)

        return response


__all__ = ["SecurityHeadersMiddleware", "_DEFAULT_CSP"]
