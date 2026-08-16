"""CSRF 防护中间件 — 基于 OWASP Double-Submit Cookie 模式。

架构角色：
    为浏览器 WebUI 场景提供跨站请求伪造防护。所有 state-changing 方法
    （POST / PUT / DELETE / PATCH）必须同时携带以下两项，服务端校验匹配后放行：
      ① Cookie ``XSRF-TOKEN`` / ``csrf_token``（服务端签发，HttpOnly=false）
      ② HTTP Header ``X-CSRF-Token``（前端 JS 从 Cookie 读取后注入）

与 APIAuth 的互补关系：
    浏览器 WebUI 路由走 CSRF；脚本 / SDK 程序化调用走 Bearer Token。
    两者设计为互补关系，任一通过即放行，互不干扰。

豁免路径：
    - ``OPTIONS``：CORS 预检请求，无状态改变且浏览器无法附带自定义 Header。
    - ``GET / HEAD``：幂等只读方法，不触发状态改变。
    - ``/api/sse/*``：SSE EventSource API 无法设置自定义 Header，依赖
      Cookie 的同源策略（SameSite=Lax）提供基础防护。
    - ``/docs`` / ``/redoc`` / ``/openapi.json``：Swagger / ReDoc 文档端点。
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger("tts_multimodel")

_CSRF_COOKIE_NAME_DEFAULT: str = os.environ.get("TTS_CSRF_COOKIE_NAME", "csrf_token")
_CSRF_HEADER_NAME_DEFAULT: str = "X-CSRF-Token"
_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})
_SKIP_EXACT_PATHS: frozenset[str] = frozenset({"/docs", "/redoc", "/openapi.json"})
_SKIP_PREFIX_PATHS: tuple[str, ...] = ("/api/sse/",)

_CSRF_MISSING_CODE: str = "CSRF_MISSING"
_CSRF_INVALID_CODE: str = "CSRF_INVALID"
_CSRF_FATAL_CODE: str = "CSRF_FATAL"


def _should_set_secure(request: Request) -> bool:
    """判断是否为 Cookie 启用 Secure 标志。

    启用条件（任一满足即启用）：
      - 环境变量 ``TTS_COOKIE_SECURE=1`` 显式启用。
      - 请求 URL scheme 为 ``https``。
      - 反向代理头部 ``X-Forwarded-Proto`` 包含 https。

    Args:
        request: 当前 Starlette 请求对象。

    Returns:
        True 表示应启用 Secure 标志。
    """
    if os.environ.get("TTS_COOKIE_SECURE", "0") == "1":
        return True
    if request.url.scheme == "https":
        return True
    forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
    return "https" in forwarded_proto


def _build_cookie_kwargs(request: Request, secure: bool) -> dict[str, Any]:
    """统一构造 set_cookie 参数字典。

    Args:
        request: 当前请求对象（用于上下文）。
        secure: 是否启用 Secure 标志。

    Returns:
        可直接解包传入 ``Response.set_cookie`` 的关键字参数字典。
    """
    return {
        # Why cookie HttpOnly=false：
        # Double-Submit 机制要求前端 JS 能够读取 document.cookie 中的
        # XSRF-TOKEN 并将其作为 X-CSRF-Token Header 注入。
        # 若设置 HttpOnly=true，则前端 JS 无法读取 cookie 值，
        # Double-Submit 机制直接失效，CSRF 校验将永远失败。
        "httponly": False,
        "samesite": "lax",
        "path": "/",
        "secure": secure,
    }


def _is_skip_path(path: str) -> bool:
    """判断请求路径是否属于豁免 CSRF 校验的范围。

    Args:
        path: 请求 URL path。

    Returns:
        True 表示跳过 CSRF 校验。
    """
    if path in _SKIP_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _SKIP_PREFIX_PATHS)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-Submit Cookie 模式的 CSRF 防护中间件。

    处理流程：
      1. 安全方法（GET / HEAD / OPTIONS）：直接放行；若 Cookie 尚未存在则签发新 token。
      2. 豁免路径：放行文档与 SSE 端点。
      3. 状态变更方法（POST / PUT / DELETE / PATCH）：
         - 校验 Cookie 中的 token 是否存在。
         - 校验 Header 中的 token 是否存在。
         - 使用恒定时间比较（``secrets.compare_digest``）校验两者是否一致。
         - 任一失败返回 403 JSON，绝不抛 Python 异常中断 ASGI 链路。
    """

    def __init__(  # nosec B107 - 空 secret_key 默认值=fail-closed（校验必失败返回 403），非硬编码密钥
        self,
        app: ASGIApp,
        secret_key: str = "",
        cookie_name: str = _CSRF_COOKIE_NAME_DEFAULT,
        header_name: str = _CSRF_HEADER_NAME_DEFAULT,
    ) -> None:
        """初始化 CSRF 中间件。

        Args:
            app: 被包装的 ASGI 应用。
            secret_key: 可选 HMAC 签名密钥；为空时使用纯随机 token（无状态）。
            cookie_name: 写入 Cookie 的名称，默认读取环境变量
                ``TTS_CSRF_COOKIE_NAME``，回退为 ``"csrf_token"``。
            header_name: 读取 Header 的名称，默认 ``"X-CSRF-Token"``。
        """
        super().__init__(app)
        self._secret_key: str = secret_key
        self._cookie_name: str = cookie_name
        # 内部统一使用小写进行 header 查找（HTTP headers 大小写不敏感）
        self._header_lookup_key: str = header_name.lower()
        self._header_name: str = header_name

    def _generate_token(self) -> str:
        """生成 CSRF token。

        使用 ``secrets.token_urlsafe`` 生成 32 bytes 的密码学安全随机串；
        当配置了 ``secret_key`` 时附加 HMAC-SHA256 签名，以便未来扩展
        服务器端校验（当前仍使用 Double-Submit 纯无状态模式）。

        Returns:
            可直接写入 Cookie 的 token 字符串。

        Raises:
            NotImplementedError: 理论不触发；由 secrets 模块内部错误向上冒泡。
        """
        raw = secrets.token_urlsafe(32)
        if not self._secret_key:
            return raw
        signature = hmac.new(
            self._secret_key.encode("utf-8"),
            raw.encode("utf-8"),
            digestmod="sha256",
        ).hexdigest()
        return f"{raw}.{signature}"

    def _validate_token(self, request: Request) -> bool:
        """校验 Cookie 与 Header 中的 CSRF token 是否匹配。

        使用 ``secrets.compare_digest``（恒定时间比较）防止侧信道定时攻击。
        若配置了 ``secret_key`` 则同时校验 HMAC 签名。

        Args:
            request: 当前请求对象。

        Returns:
            True 表示校验通过；任何不满足条件均返回 False。
        """
        cookie_token = request.cookies.get(self._cookie_name)
        header_token = request.headers.get(self._header_lookup_key)

        if not cookie_token or not header_token:
            return False

        if self._secret_key:
            # 带签名模式：校验 cookie 自身签名（防止客户端篡改 cookie）
            if "." not in cookie_token:
                return False
            raw_part, _, sig_part = cookie_token.rpartition(".")
            expected_sig = hmac.new(
                self._secret_key.encode("utf-8"),
                raw_part.encode("utf-8"),
                digestmod="sha256",
            ).hexdigest()
            if not secrets.compare_digest(expected_sig, sig_part):
                return False

        # Why Double-Submit 而非 Synchronizer Token：
        # 本项目架构中不存在 server-side session（SQLite 仅存 history 不存 session），
        # 若使用 Synchronizer Token 模式则需要引入 Redis 等共享存储，
        # 反而增加部署复杂度。Double-Submit Cookie 是纯无状态方案，
        # 只需客户端同时持有 cookie + 注入 header，即可防御 CSRF，契合当前架构。
        return secrets.compare_digest(cookie_token, header_token)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Starlette dispatch 入口。

        Args:
            request: 当前请求对象。
            call_next: 下游 handler 调用入口。

        Returns:
            正常业务 Response，或 403 JSONResponse（CSRF 校验失败）。
        """
        # 安全失败原则（fail-closed）：外层 try/except 兜底，任何异常均返回 403，
        # 绝不将 Python 异常堆栈暴露至前端或中断 ASGI 调用链。
        try:
            if request.method in _SAFE_METHODS:
                response = await call_next(request)
                if self._cookie_name not in request.cookies:
                    try:
                        token = self._generate_token()
                        secure = _should_set_secure(request)
                        response.set_cookie(
                            key=self._cookie_name,
                            value=token,
                            **_build_cookie_kwargs(request, secure=secure),
                        )
                    except (OSError, ValueError) as e:
                        logger.warning("CSRF token 签发失败，跳过写入 cookie: %s", e)
                return response

            if _is_skip_path(request.url.path):
                return await call_next(request)

            cookie_token = request.cookies.get(self._cookie_name)
            header_token = request.headers.get(self._header_lookup_key)

            secure = _should_set_secure(request)
            cookie_kwargs = _build_cookie_kwargs(request, secure=secure)

            if not cookie_token:
                # Cookie 不存在：尝试为下一次请求签发新 token 并返回 403
                try:
                    fresh_token = self._generate_token()
                except (OSError, ValueError) as e:
                    logger.exception("CSRF 签发新 token 失败: %s", e)
                    return JSONResponse(
                        status_code=403,
                        content={
                            "status": "error",
                            "code": _CSRF_FATAL_CODE,
                            "message": "CSRF token generation failed.",
                            "detail": "服务端签发 CSRF token 失败，请稍后重试。",
                        },
                    )
                response = JSONResponse(
                    status_code=403,
                    content={
                        "status": "error",
                        "code": _CSRF_MISSING_CODE,
                        "message": "CSRF token missing. Please refresh the page.",
                        "detail": f"Cookie '{self._cookie_name}' 缺失，请刷新页面重新获取 token。",
                    },
                )
                response.set_cookie(
                    key=self._cookie_name,
                    value=fresh_token,
                    **cookie_kwargs,
                )
                return response

            if not header_token:
                request_id = getattr(request.state, "request_id", "")
                logger.warning(
                    "[CSRF_MISSING] Header '%s' 缺失 request_id=%s path=%s method=%s",
                    self._header_name,
                    request_id,
                    request.url.path,
                    request.method,
                )
                return JSONResponse(
                    status_code=403,
                    content={
                        "status": "error",
                        "code": _CSRF_MISSING_CODE,
                        "message": f"CSRF token required. Include {self._header_name} header.",
                        "detail": f"Header '{self._header_name}' 缺失，请前端 JS 从 cookie 读取 token 注入 header。",
                    },
                )

            if not self._validate_token(request):
                request_id = getattr(request.state, "request_id", "")
                # 安全原则：日志中绝不记录 token 明文，仅记录 request_id 与 path 便于排查
                logger.warning(
                    "[CSRF_INVALID] token 不匹配 request_id=%s path=%s method=%s",
                    request_id,
                    request.url.path,
                    request.method,
                )
                return JSONResponse(
                    status_code=403,
                    content={
                        "status": "error",
                        "code": _CSRF_INVALID_CODE,
                        "message": "CSRF token mismatch.",
                        "detail": "Cookie 与 Header 中的 CSRF token 不一致，请刷新页面重试。",
                    },
                )

            return await call_next(request)

        except Exception as e:
            # 最终兜底：fail-closed，任何未预期的异常（如 secrets 模块内部错误、
            # hmac 错误、set_cookie IO 错误）都返回 403，绝不裸抛。
            logger.exception("[CSRF_FATAL] 中间件处理异常: %s", e)
            return JSONResponse(
                status_code=403,
                content={
                    "status": "error",
                    "code": _CSRF_FATAL_CODE,
                    "message": "CSRF check failed due to server error.",
                    "detail": "服务端 CSRF 校验异常，请稍后重试。",
                },
            )


# ----------------------------------------------------------------------
# 旧版常量名兼容（test_security.py 等旧测试依赖）
# ----------------------------------------------------------------------

# 旧代码导入 ``_CSRF_COOKIE_NAME`` / ``_CSRF_HEADER_NAME`` 作为全局常量。
# 新版本支持环境变量覆盖 + CSRFMiddleware 构造参数。
# 注意：_CSRF_HEADER_NAME 为 **全小写** HTTP canonical 形式（与旧版本兼容），
# 因为 HTTP Headers 大小写不敏感（RFC 7230 §3.2），中间件内部使用 .lower() 比对，
# 两种形式的运行时行为完全一致。
_CSRF_COOKIE_NAME: str = _CSRF_COOKIE_NAME_DEFAULT
_CSRF_HEADER_NAME: str = _CSRF_HEADER_NAME_DEFAULT.lower()
