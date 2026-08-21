"""全局异常处理器 — 标准化所有异常为统一 JSON 响应格式。

架构角色：
    作为 FastAPI 应用的最后一道防线，捕获所有业务与框架异常并转换为
    结构一致的 JSON 响应（统一字段：status / code / message / detail /
    status_code / request_id）。通过 ``register_error_handlers(app)`` 在
    ``app_server.create_app()`` 中一次性注册所有处理器。

四类异常与响应映射：
    ① :class:`TTSError` 及其子类：
        status_code = ``e.status_code``，
        code = ``e.code``，
        message = ``e.message``。
    ② :class:`RequestValidationError`（Pydantic）+ :class:`ValidationError`：
        400 / 422 ``VALIDATION_ERROR``，detail 转为字段级错误列表，
        提示文案适配中文语境。
    ③ :class:`StarletteHTTPException`：
        code = ``HTTP_{status}``（如 ``HTTP_404``），
        透传 status 与 detail。
    ④ 兜底 ``Exception``：
        500 ``UNKNOWN_ERROR`` / ``INTERNAL_ERROR``，
        ``logger.exception`` 记录完整堆栈，
        **绝不** 将 exc.args / 堆栈 / 文件路径返回给前端。

专门类型增强（S-R5）：
    - ``sqlite3.OperationalError`` → 503 Service Unavailable + Retry-After
    - ``asyncio.TimeoutError`` / ``TimeoutError`` → 504 Gateway Timeout
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError

try:
    # FastAPI <0.110: ValidationError 从 fastapi.exceptions 导出
    from fastapi.exceptions import ValidationError as _FastAPIValidationError  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - 仅新版 FastAPI 走此分支
    # FastAPI >=0.110: 表单级 ValidationError 实际已统一由 Pydantic 抛出，
    # 此处用 pydantic.ValidationError 兜底保持老代码兼容
    try:
        from pydantic import ValidationError as _FastAPIValidationError  # type: ignore[no-redef,assignment]
    except ImportError:  # pragma: no cover - Pydantic 是 FastAPI 硬依赖
        _FastAPIValidationError = Exception  # type: ignore[assignment,misc]
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..exceptions import TTSError

logger = logging.getLogger(__name__)

_SQLITE_RETRY_AFTER_SECONDS: int = 5
_SQLITE_LOCKED_KEYWORDS: tuple[str, ...] = ("locked", "busy")
_SQLITE_DISK_KEYWORDS: tuple[str, ...] = ("disk", "no space", "full", "readonly", "read-only")


def _get_request_id(request: Request) -> str:
    """安全地从 request.state 获取 request_id，不存在则返回空串。

    Args:
        request: FastAPI/Starlette 请求对象。

    Returns:
        请求 ID 字符串；未设置时返回空串。
    """
    try:
        return str(getattr(request.state, "request_id", ""))
    except (AttributeError, ValueError):
        return ""


def _build_error_response(
    code: str,
    message: str,
    status_code: int,
    detail: Any = None,
    request_id: str = "",
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """构建统一格式的错误 JSON 响应。

    统一响应结构保证前端 i18n、错误展示 SDK 能以一致方式解析：
    ``status`` 固定为 ``"error"``，``code`` 用于前端按 key 查字典，
    ``message`` 为英文 fallback，``detail`` 承载结构化详情（如字段错误列表）。

    Args:
        code: 错误码，如 ``MODEL_NOT_LOADED`` / ``CSRF_INVALID`` / ``HTTP_404``。
        message: 英文或默认中文提示，作为 code 查询失败的 fallback。
        status_code: HTTP 状态码。
        detail: 可选结构化详情（字段错误列表、友好提示等）。
        request_id: 可选链路追踪 ID，注入响应体便于排查。
        extra: 可选附加字段（如 retry_after），合并到 content 顶层。
        headers: 可选自定义响应头（如 Retry-After）。

    Returns:
        构造完成的 :class:`JSONResponse`，永远不会抛出 Python 异常。
    """
    content: dict[str, Any] = {
        "status": "error",
        "code": code,
        "message": message,
        "status_code": status_code,
    }
    if detail is not None:
        content["detail"] = detail
    if request_id:
        content["request_id"] = request_id
    if extra:
        # 显式遍历 key 避免覆盖结构字段
        for k, v in extra.items():
            if k not in content:
                content[k] = v

    try:
        return JSONResponse(
            status_code=status_code,
            content=content,
            headers=headers or None,
        )
    except (TypeError, ValueError, UnicodeEncodeError) as e:
        # JSON 序列化失败兜底：使用最简单的结构，确保总有 HTTP 响应给客户端
        logger.error("_build_error_response JSON 序列化失败: %s", e)
        return JSONResponse(
            status_code=500,
            content={"code": "FATAL_ERROR", "message": "Fatal error handler failure"},
        )


def _parse_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """安全解析 Pydantic ``errors()`` 为字段级列表。

    原 ``exc.errors()`` 返回结构可能随 Pydantic 版本变化，因此解析全程
    包裹 try/except，失败时回退为原始结构字符串形式。

    Args:
        exc: Pydantic RequestValidationError 实例。

    Returns:
        字段级错误列表，每项含 ``field`` / ``message`` / ``type``。
    """
    result: list[dict[str, Any]] = []
    raw_errors = exc.errors()
    try:
        for error in raw_errors:
            try:
                loc_parts = error.get("loc", ())
                field_name = ".".join(str(x) for x in loc_parts) if loc_parts else "request"
                result.append(
                    {
                        "field": field_name,
                        "message": str(error.get("msg", "Unknown validation error")),
                        "type": str(error.get("type", "unknown")),
                    }
                )
            except (KeyError, TypeError, ValueError) as e:
                logger.debug("单条 validation error 解析失败，回退原始形式: %s", e)
                result.append(
                    {
                        "field": "__raw__",
                        "message": str(error),
                        "type": "parse_fallback",
                    }
                )
    except (TypeError, AttributeError) as e:
        # 整个 errors() 结构异常：直接整体转字符串，不影响返回 422
        logger.warning("validation errors() 整体结构异常: %s", e)
        result = [
            {
                "field": "__all__",
                "message": str(raw_errors),
                "type": "structure_fallback",
            }
        ]
    return result


def _build_sqlite_error_response(exc: sqlite3.OperationalError) -> JSONResponse:
    """构建 SQLite OperationalError 的 503 JSON 响应。

    根据错误关键字（locked / busy / disk / no space）返回友好的用户提示，
    同时注入 ``Retry-After`` 响应头，引导反向代理 / 客户端做指数退避重试。

    Args:
        exc: SQLite OperationalError 实例。

    Returns:
        503 Service Unavailable JSONResponse。
    """
    error_msg = str(exc)
    error_lower = error_msg.lower()

    if any(kw in error_lower for kw in _SQLITE_LOCKED_KEYWORDS):
        user_message = "系统繁忙，请稍后重试"
        error_code = "database_locked"
    elif any(kw in error_lower for kw in _SQLITE_DISK_KEYWORDS):
        user_message = "存储空间不足，请联系管理员"
        error_code = "disk_error"
    else:
        user_message = "数据库服务暂时不可用，请稍后重试"
        error_code = "database_unavailable"

    return _build_error_response(
        code=error_code,
        message=user_message,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=user_message,
        extra={"retry_after": _SQLITE_RETRY_AFTER_SECONDS},
        headers={"Retry-After": str(_SQLITE_RETRY_AFTER_SECONDS)},
    )


def _build_timeout_error_response(exc: Exception) -> JSONResponse:
    """构建 Timeout 异常的 504 Gateway Timeout JSON 响应。

    Args:
        exc: asyncio.TimeoutError 或内置 TimeoutError 实例。

    Returns:
        504 Gateway Timeout JSONResponse。
    """
    return _build_error_response(
        code="gateway_timeout",
        message="请求处理超时，请尝试缩短文本或稍后重试",
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        detail=f"{type(exc).__name__}: request timed out",
    )


async def _tts_error_handler(request: Request, exc: TTSError) -> JSONResponse:
    """TTSError 及其子类处理器。

    业务层自定义异常（ModelLoadError / InsufficientVRAMError /
    GenerationError 等）透传 status_code / code / message 三个字段。

    Args:
        request: 当前请求对象。
        exc: TTSError 子类实例。

    Returns:
        标准化 JSONResponse。
    """
    try:
        request_id = _get_request_id(request)
        logger.warning(
            "TTSError code=%s status=%s message=%s request_id=%s",
            exc.code,
            exc.status_code,
            exc.message,
            request_id,
        )
        return _build_error_response(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
            detail=getattr(exc, "detail", None),
            request_id=request_id,
        )
    except Exception as inner:
        # Why handler 内部再套 try/except：
        # 若异常处理器自身在构建响应时又抛出新异常（如 getattr 访问不存在属性、
        # JSON 不可序列化对象等），绝不能裸抛 Python 堆栈到前端。
        # 此处做最终降级：返回最精简的 FATAL_ERROR 500 响应。
        logger.exception("TTSError handler 内部异常: %s", inner)
        return JSONResponse(
            status_code=500,
            content={"code": "FATAL_ERROR"},
        )


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Pydantic RequestValidationError 处理器。

    Args:
        request: 当前请求对象。
        exc: Pydantic 验证异常。

    Returns:
        422 Unprocessable Entity JSONResponse，含字段级错误列表。
    """
    try:
        request_id = _get_request_id(request)
        logger.warning("Validation error request_id=%s exc=%s", request_id, exc)
        errors = _parse_validation_errors(exc)
        return _build_error_response(
            code="VALIDATION_ERROR",
            message="请求参数验证失败",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=errors,
            request_id=request_id,
        )
    except Exception as inner:
        logger.exception("ValidationError handler 内部异常: %s", inner)
        return JSONResponse(
            status_code=500,
            content={"code": "FATAL_ERROR"},
        )


async def _fastapi_validation_error_handler(request: Request, exc: _FastAPIValidationError) -> JSONResponse:
    """FastAPI 表单级 ValidationError 处理器（部分版本路径与 RequestValidationError 不同）。

    Args:
        request: 当前请求对象。
        exc: FastAPI/Pydantic ValidationError 实例（兼容新旧 FastAPI）。

    Returns:
        422 Unprocessable Entity JSONResponse。
    """
    try:
        request_id = _get_request_id(request)
        logger.warning("FastAPI ValidationError request_id=%s exc=%s", request_id, exc)
        try:
            raw_errors = exc.errors()
            parsed: list[dict[str, Any]] = []
            for err in raw_errors:
                try:
                    loc = err.get("loc", ())
                    parsed.append(
                        {
                            "field": ".".join(str(x) for x in loc) if loc else "request",
                            "message": str(err.get("msg", "")),
                            "type": str(err.get("type", "")),
                        }
                    )
                except (KeyError, TypeError, ValueError):
                    parsed.append({"field": "__raw__", "message": str(err), "type": "fallback"})
            detail: Any = parsed
        except (AttributeError, TypeError, ValueError):
            detail = str(exc)
        return _build_error_response(
            code="VALIDATION_ERROR",
            message="请求参数验证失败",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
            request_id=request_id,
        )
    except Exception as inner:
        logger.exception("FastAPI ValidationError handler 内部异常: %s", inner)
        return JSONResponse(
            status_code=500,
            content={"code": "FATAL_ERROR"},
        )


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """StarletteHTTPException 处理器（404 / 405 等框架级 HTTP 错误）。

    Args:
        request: 当前请求对象。
        exc: Starlette HTTP 异常。

    Returns:
        与 exc.status_code 一致的标准化 JSONResponse。
    """
    try:
        request_id = _get_request_id(request)
        logger.info(
            "StarletteHTTPException status=%s detail=%s request_id=%s",
            exc.status_code,
            exc.detail,
            request_id,
        )
        return _build_error_response(
            code=f"HTTP_{exc.status_code}",
            message=str(exc.detail) if exc.detail else f"HTTP {exc.status_code}",
            status_code=exc.status_code,
            detail=exc.detail,
            request_id=request_id,
        )
    except Exception as inner:
        logger.exception("StarletteHTTPException handler 内部异常: %s", inner)
        return JSONResponse(
            status_code=500,
            content={"code": "FATAL_ERROR"},
        )


async def sqlite_error_handler(request: Request, exc: sqlite3.OperationalError) -> JSONResponse:
    """SQLite OperationalError 专用处理器。

    可在 ``app_server.py`` 中显式注册：
    ``app.add_exception_handler(sqlite3.OperationalError, sqlite_error_handler)``
    即使未注册，:func:`generic_error_handler` 也会通过类型判断走相同逻辑兜底。

    Args:
        request: 当前请求对象。
        exc: SQLite OperationalError 实例。

    Returns:
        503 Service Unavailable JSONResponse。
    """
    try:
        request_id = _get_request_id(request)
        logger.warning(
            "SQLite OperationalError request_id=%s exc=%s",
            request_id,
            exc,
            exc_info=True,
        )
        resp = _build_sqlite_error_response(exc)
        if request_id:
            try:
                body = json.loads(resp.body.decode("utf-8")) if isinstance(resp.body, (bytes, bytearray)) else {}
                if isinstance(body, dict) and "request_id" not in body:
                    body["request_id"] = request_id
                    resp.body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            except (ValueError, UnicodeDecodeError, AttributeError):
                pass
        return resp
    except Exception as inner:
        logger.exception("SQLite handler 内部异常: %s", inner)
        return JSONResponse(
            status_code=500,
            content={"code": "FATAL_ERROR"},
        )


async def timeout_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Timeout 异常专用处理器（asyncio.TimeoutError / 内置 TimeoutError）。

    可在 ``app_server.py`` 中显式注册：
    ``app.add_exception_handler(asyncio.TimeoutError, timeout_error_handler)``
    ``app.add_exception_handler(TimeoutError, timeout_error_handler)``

    Args:
        request: 当前请求对象。
        exc: Timeout 异常实例。

    Returns:
        504 Gateway Timeout JSONResponse。
    """
    try:
        request_id = _get_request_id(request)
        logger.warning(
            "Timeout error type=%s exc=%s request_id=%s",
            type(exc).__name__,
            exc,
            request_id,
            exc_info=True,
        )
        resp = _build_timeout_error_response(exc)
        if request_id:
            try:
                body = json.loads(resp.body.decode("utf-8")) if isinstance(resp.body, (bytes, bytearray)) else {}
                if isinstance(body, dict) and "request_id" not in body:
                    body["request_id"] = request_id
                    resp.body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            except (ValueError, UnicodeDecodeError, AttributeError):
                pass
        return resp
    except Exception as inner:
        logger.exception("Timeout handler 内部异常: %s", inner)
        return JSONResponse(
            status_code=500,
            content={"code": "FATAL_ERROR"},
        )


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """通用异常兜底处理器。

    按顺序判断特殊类型返回更准确的状态码：
      - ``sqlite3.OperationalError`` → 503
      - ``asyncio.TimeoutError`` / ``TimeoutError`` → 504
      - 其他异常 → 500 INTERNAL_ERROR

    Why 兜底 500 不返回 exc.args / 堆栈给前端：
        异常堆栈可能泄漏文件路径、环境变量、Persona 数据、SQL 语句等敏感信息。
        安全合规要求：前端仅展示友好提示，完整堆栈仅通过 ``logger.exception``
        写入服务端日志，由运维 / 开发者通过日志系统查询。

    Args:
        request: 当前请求对象。
        exc: 未被前述专门处理器捕获的任意异常。

    Returns:
        对应状态码的标准化 JSONResponse，永远 500/503/504 之一。
    """
    try:
        request_id = _get_request_id(request)

        if isinstance(exc, sqlite3.OperationalError):
            logger.warning(
                "SQLite OperationalError (generic handler) request_id=%s exc=%s",
                request_id,
                exc,
                exc_info=True,
            )
            resp = _build_sqlite_error_response(exc)
            if request_id:
                try:
                    body = json.loads(resp.body.decode("utf-8")) if isinstance(resp.body, (bytes, bytearray)) else {}
                    if isinstance(body, dict) and "request_id" not in body:
                        body["request_id"] = request_id
                        resp.body = json.dumps(body, ensure_ascii=False).encode("utf-8")
                except (ValueError, UnicodeDecodeError, AttributeError):
                    pass
            return resp

        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            logger.warning(
                "Timeout error (generic handler) type=%s exc=%s request_id=%s",
                type(exc).__name__,
                exc,
                request_id,
                exc_info=True,
            )
            resp = _build_timeout_error_response(exc)
            if request_id:
                try:
                    body = json.loads(resp.body.decode("utf-8")) if isinstance(resp.body, (bytes, bytearray)) else {}
                    if isinstance(body, dict) and "request_id" not in body:
                        body["request_id"] = request_id
                        resp.body = json.dumps(body, ensure_ascii=False).encode("utf-8")
                except (ValueError, UnicodeDecodeError, AttributeError):
                    pass
            return resp

        # Why TTSError code/message/status_code 三字段分离：
        # 前端 i18n 通过 code 作为字典 key 查询中文 / 英文 / 日文 / 韩文提示，
        # message 字段作为英文 fallback；抛出异常的业务代码完全无需感知多语言，
        # 实现异常抛出与国际化文案渲染的解耦。
        #
        # 此处兜底为 500，完整堆栈仅记录到日志，绝不返回给前端。
        logger.error(
            "Unhandled exception type=%s exc=%s request_id=%s",
            type(exc).__name__,
            exc,
            request_id,
            exc_info=True,
        )
        return _build_error_response(
            code="INTERNAL_ERROR",
            message="服务器内部错误，请稍后重试",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=None,
            request_id=request_id,
        )
    except Exception as inner:
        logger.exception("generic_error_handler 自身异常，终极降级: %s", inner)
        return JSONResponse(
            status_code=500,
            content={"code": "FATAL_ERROR"},
        )


# ---- 向后兼容别名 ----
# 外部模块（如 app_server.py / 旧的测试）可能使用以下名称导入，
# 在此统一绑定到规范命名实现，保持 100% 向后兼容。
tts_error_handler = _tts_error_handler
validation_error_handler = _validation_error_handler


def register_error_handlers(app: FastAPI) -> None:
    """主入口：在 FastAPI 应用实例上一次性注册所有异常处理器。

    注册顺序：先注册更具体的类型，最后注册通用 ``Exception``。
    FastAPI 会按"最具体匹配优先"原则分派，但显式按顺序注册更清晰。

    Args:
        app: FastAPI 应用实例。
    """
    app.add_exception_handler(TTSError, _tts_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    # 兼容 FastAPI 新老版本：老版本从 fastapi.exceptions.ValidationError 抛出，
    # 新版本统一为 pydantic.ValidationError，此处 _FastAPIValidationError 已
    # 在模块顶部做了双路径兼容导入。
    app.add_exception_handler(_FastAPIValidationError, _fastapi_validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(sqlite3.OperationalError, sqlite_error_handler)
    app.add_exception_handler(asyncio.TimeoutError, timeout_error_handler)
    app.add_exception_handler(TimeoutError, timeout_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)
