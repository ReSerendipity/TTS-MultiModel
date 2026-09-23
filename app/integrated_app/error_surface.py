"""对外错误面：把异常文本压成不含服务端路径、长度受限的提示。

Security [D6]：
    路由层把 ``str(exc)`` 原文写进响应体会泄露绝对路径、SQL 片段与堆栈细节。
    ``routes/model.py`` 已有这套脱敏逻辑（S-R6），但它是私有的，其它路由各自
    ``str(exc)``，等于同一类漏洞修了一处、漏了七处。本模块把它提为唯一出口，
    新增路由请调用 :func:`safe_error_message` 而不是 ``str(exc)``。

注意：``html.escape`` 只防 XSS，不防信息泄露，不能替代本模块。
"""

from __future__ import annotations

import asyncio
import re

from .exceptions import (
    EngineSwitchError,
    InsufficientVRAMError,
    ModelLoadError,
    TTSError,
)

#: 匹配 Windows 绝对路径（``C:\\...``）与 Unix 多级路径（``a/b/c``）
SENSITIVE_PATH_PATTERN = re.compile(r"[A-Za-z]:\\[^\s\"'<>|*?]+|/(?:[^\s\"'<>|*?]+/)+[^\s\"'<>|*?]*")

#: 对外消息的最大字符数
ERROR_MESSAGE_MAX_LENGTH = 200


def redact_paths(message: str, max_length: int = ERROR_MESSAGE_MAX_LENGTH) -> str:
    """把文本里的文件路径替换为 ``[PATH]`` 并截断长度。

    先脱敏再截断：截断可能把路径切成半截，反而留下更难识别的残片。

    Args:
        message:    原始文本（可能来自异常消息或日志行）。
        max_length: 返回消息的最大字符数。

    Returns:
        脱敏后的文本。
    """
    if not message:
        return ""
    msg = SENSITIVE_PATH_PATTERN.sub("[PATH]", message)
    if len(msg) > max_length:
        msg = msg[:max_length] + "..."
    return msg


def safe_error_message(exc: BaseException | None, max_length: int = ERROR_MESSAGE_MAX_LENGTH) -> str:
    """对异常消息脱敏，得到可安全返回给客户端的字符串。

    Args:
        exc:        异常对象；``None`` 表示未知错误。
        max_length: 返回消息的最大字符数。

    Returns:
        脱敏后的错误消息。
    """
    if exc is None:
        return "未知错误"

    # 以下四条领域异常历史上直接返回未脱敏的 str(exc)，而这几个类恰恰最常把
    # 文件路径写进消息（模型加载/切换/显存报错都带 model/ 下的路径）。
    if isinstance(exc, InsufficientVRAMError):
        return f"显存不足：{redact_paths(str(exc), max_length)}"
    if isinstance(exc, EngineSwitchError):
        return f"引擎切换失败：{redact_paths(str(exc), max_length)}"
    if isinstance(exc, ModelLoadError):
        return f"模型加载失败：{redact_paths(str(exc), max_length)}"
    if isinstance(exc, TTSError):
        return redact_paths(str(exc), max_length)
    if isinstance(exc, FileNotFoundError):
        return "文件不存在或已被删除"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "操作超时，请稍后重试"
    if isinstance(exc, PermissionError):
        return "权限不足，无法访问所需资源"
    if isinstance(exc, OSError):
        return f"系统错误：{redact_paths(str(exc), max_length)}"

    return redact_paths(str(exc), max_length)
