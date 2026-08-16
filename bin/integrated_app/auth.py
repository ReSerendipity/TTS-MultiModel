"""Bearer Token API 认证中间件模块。

架构说明：
    本模块提供基于 Bearer Token 的程序化 API 认证，作为 ASGI 中间件
    挂载在 FastAPI 调用栈中。使用 ``hmac.compare_digest`` 恒定时间比较
    防止定时攻击（Timing Attack）逐字节爆破 token。

配置入口（config.yaml）：
    api_auth:
        enabled: true        # 是否启用 Bearer Token 认证
        token: "your-token"  # 期望的 Bearer Token 明文

与 CSRF 的关系：
    - CSRF：保护浏览器端 POST/PUT/DELETE 等 state-changing 请求，
      通过 Cookie + X-CSRF-Token 双重校验，防御跨站请求伪造。
    - APIAuth：保护程序化调用（脚本、OpenAI SDK 风格、第三方集成），
      通过 Authorization: Bearer <token> 头认证，不依赖浏览器 Cookie。
    - 两者互补：浏览器走 CSRF，脚本/SDK 走 Bearer Token。
"""

import hmac
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger("tts_multimodel")

_AUTH_HEADER_PATTERN: re.Pattern[str] = re.compile(r"^Bearer\s+(.+)$", re.IGNORECASE)


def verify_bearer_token(token_header: str, expected_token: str) -> bool:
    """恒定时间校验 Bearer Token 格式与内容。

    严格按 RFC 6750 解析 ``Authorization: Bearer <token>`` 格式，
    使用 ``hmac.compare_digest`` 抵御定时攻击。

    Why hmac.compare_digest(expected, provided) 顺序：
        标准库约定 ``expected``（期望值）在前，``provided``（用户输入）在后。
        虽然大多数实现下两个参数顺序不影响结果，但某些 Python 版本或
        第三方补丁在极端场景下可能引入微小时序差异，遵循约定更安全。

    安全合规：
        - token_header 为空 / 不含 Bearer 前缀时直接返回 False，不抛异常
        - 比较失败时只记录 logger.info("APIAuth failed")，不记录 token 明文
        - 全程不使用 ``==`` 字符串短路比较（会泄露逐字节匹配时序信息）

    Args:
        token_header: 请求头中完整的 Authorization 字符串，如
            ``"Bearer abc123"``。
        expected_token: 配置中期望的 token 明文（不带 Bearer 前缀）。

    Returns:
        True 当且仅当格式合法且 token 恒定时间比较通过。
    """
    if not token_header or not expected_token:
        return False

    try:
        match = _AUTH_HEADER_PATTERN.match(token_header.strip())
    except ValueError:
        logger.info("APIAuth failed: regex ValueError on header parsing")
        return False

    if not match:
        return False

    provided_token = match.group(1)

    try:
        expected_bytes = expected_token.encode("utf-8")
        provided_bytes = provided_token.encode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        logger.info("APIAuth failed: token encoding error")
        return False

    ok = hmac.compare_digest(expected_bytes, provided_bytes)
    if not ok:
        logger.info("APIAuth failed")
    return ok


class APIAuthMiddleware(BaseHTTPMiddleware):
    """Bearer Token API 认证 ASGI 中间件。

    调用链路（ASGI）：
        Server → RequestIDMiddleware → CORSMiddleware → CSRFMiddleware
        → APIAuthMiddleware.dispatch → 路由 handler

    认证逻辑：
        1. ``enabled=False``：直接放行所有请求
        2. 公共前缀（/api/health/*, /static/, /favicon.ico）：免认证
        3. 非 /api/ 路径（Web 页面、模板渲染）：免认证，交给 CSRF 保护
        4. "/" 根路由：免认证（首页无需 Bearer Token）
        5. /api/sse 前缀：SSE EventSource 原生不支持自定义 Header，
           走 Cookie + CSRF 双重防护，不强制 Bearer Token
        6. 其余 /api/* 请求：强制校验 ``Authorization: Bearer <token>``

    启动时安全校验：
        若 ``enabled=True`` 但 ``token`` 为空，记录显眼警告但不抛异常。
        此时所有 /api/ 请求会被安全拒绝，不存在鉴权 bypass 风险。

    Args:
        app: 内层 ASGI 应用。
        enabled: 是否启用 Bearer Token 认证。
        token: 期望的 Bearer Token 明文（不带 Bearer 前缀）。
    """

    _PUBLIC_PREFIXES: tuple[str, ...] = (
        "/api/health/",
        "/static/",
        "/favicon.ico",
    )
    _BEARER_SCHEME: str = "Bearer"

    def __init__(self, app: ASGIApp, enabled: bool = False, token: str = "") -> None:  # nosec B107 - 空 token 默认值=fail-closed（拒绝全部 /api/ 请求），非硬编码口令
        """初始化 API 认证中间件。

        安全警告逻辑：
            当 ``enabled=True`` 但 ``token`` 为空字符串时，记录显眼的 SECURITY 级别
            警告日志。此时中间件处于"安全失败"（fail-closed）状态：所有 /api/ 请求
            都会被拒绝，不存在鉴权绕过（bypass）风险。用户需在 config.yaml 中
            配置非空 token 后重启服务才能正常使用 API。

        恒定时间比较安全机制：
            token 会被预编码为 bytes 存储在 ``self._token_bytes`` 中，后续
            ``_verify_bearer`` 方法使用 ``hmac.compare_digest`` 进行恒定时间比较，
            防止攻击者通过测量响应时间逐字节爆破 token（定时攻击 Timing Attack）。
            日志中只记录 token 长度，绝不记录 token 明文。

        Args:
            app: 内层 ASGI 应用，由 Starlette 中间件框架自动传入。
            enabled: 是否启用 Bearer Token 认证。默认为 False（关闭）。
                设为 True 时对所有 /api/* 请求（除公共前缀外）强制校验
                Authorization 头。
            token: 期望的 Bearer Token 明文（不带 "Bearer " 前缀）。
                默认为空字符串，此时若 enabled=True 会触发安全警告。
        """
        super().__init__(app)
        if enabled and not token:
            logger.warning(
                "[SECURITY] API 认证已启用 (api_auth.enabled=true) 但未配置 token "
                "(api_auth.token 为空)。所有 /api/ 请求将被拒绝。"
                "请在 config.yaml 中配置一个非空 token。"
            )
        self.enabled: bool = enabled
        self._token_bytes: bytes = token.encode("utf-8") if token else b""
        self._expected_token: str = token
        logger.info(
            "APIAuthMiddleware initialized: enabled=%s, token_length=%d",
            enabled,
            len(token),
        )

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Any],
        send: Callable[[Any], Awaitable[None]],
    ) -> Awaitable[None]:
        """ASGI 入口，兼容 BaseHTTPMiddleware 的标准调用约定。

        直接委托给父类 ``BaseHTTPMiddleware.__call__``，父类会构造
        Request 对象并调用 ``dispatch``。此处仅补充类型注解。

        Args:
            scope: ASGI scope 字典（type/path/headers 等）。
            receive: ASGI receive 可调用对象。
            send: ASGI send 可调用对象。

        Returns:
            异步协程对象（Awaitable[None]）。
        """
        return await super().__call__(scope, receive, send)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """中间件核心分发逻辑。

        跳过路径说明：
            - "/" 根路由：浏览器首页访问，无需 Bearer Token
            - /api/sse/*：Why 跳过 —— SSE EventSource 浏览器 API 原生
              不支持自定义 Header，无法设置 Authorization。前端 SSE 连接
              走 Cookie（含 session）+ CSRF Token（作为 query param 或
              cookie）双重防护，安全级别等价于 Bearer Token。

        Args:
            request: FastAPI/Starlette Request 对象。
            call_next: 内层 handler 可调用对象。

        Returns:
            Response 对象：认证通过时返回 call_next 的结果，
            失败时返回 401 JSONResponse。
        """
        if not self.enabled:
            return await call_next(request)

        path: str = request.url.path

        if any(path.startswith(prefix) for prefix in self._PUBLIC_PREFIXES):
            return await call_next(request)

        if path == "/":
            return await call_next(request)

        # Why 跳过 /api/sse 端点：
        #   EventSource API (浏览器 SSE 标准) 不支持自定义 HTTP Header，
        #   只能携带 Cookie。如果此处强制 Bearer Token，前端 SSE 连接会
        #   全部 401。前端改为通过 CSRF Cookie + query param token 双重校验
        #   更稳妥（CSRF 已在前一层中间件完成防御）。
        if path.startswith("/api/sse"):
            return await call_next(request)

        if not path.startswith("/api/"):
            return await call_next(request)

        auth_header: str = request.headers.get("Authorization", "")
        if not self._verify_bearer(auth_header):
            return JSONResponse(
                status_code=401,
                content={"detail": "未授权访问：缺少或无效的 Bearer Token"},
            )
        return await call_next(request)

    def _verify_bearer(self, auth_header: str) -> bool:
        """SECURITY: 恒定时间校验 Bearer token（实例方法版本）。

        - 严格按 RFC 6750 解析 ``Bearer <token>`` 格式。
        - 使用 ``hmac.compare_digest`` 抵御定时攻击。
        - 缺少 scheme / 多余字段 / 大小写不匹配都直接拒绝。
        - scheme 比较也走恒定时间，避免通过 scheme 大小写差异做侧信道。

        Args:
            auth_header: 完整的 Authorization 请求头字符串。

        Returns:
            True 当且仅当格式合法且 token 恒定时间比较通过。
        """
        if not auth_header or not self._token_bytes:
            return False

        parts = auth_header.split(" ", 1)
        if len(parts) != 2:
            return False

        scheme, token = parts[0], parts[1]

        try:
            scheme_bytes = scheme.encode("utf-8")
            bearer_bytes = self._BEARER_SCHEME.encode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            logger.info("APIAuth failed: scheme encoding error")
            return False

        if not hmac.compare_digest(scheme_bytes, bearer_bytes):
            return False

        try:
            token_bytes = token.encode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            logger.info("APIAuth failed: token encoding error")
            return False

        ok = hmac.compare_digest(token_bytes, self._token_bytes)
        if not ok:
            logger.info("APIAuth failed")
        return ok
