#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI 统一输出层：结构化结果信封 + 人类/JSON 双渲染器。

设计目标（ADR-0005）：
    - AI agent 可通过 ``--json`` 得到机器可读、稳定 schema 的结果。
    - 日志/警告一律走 stderr，stdout 只承载结果数据，便于管道与解析。
    - 退出码语义化，agent 可据此判断失败类别。

所有 handler 返回 ``Result``；输出由 ``emit()`` 统一处理。本模块不依赖
FastAPI / Request，可在纯 CLI 环境直接 import。
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 语义化退出码
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_BUSINESS_ERROR = 1  # 业务失败（生成失败、模型加载失败等）
EXIT_USAGE_ERROR = 2  # 参数错误（argparse 也用 2）
EXIT_NOT_READY = 3  # 环境未就绪（模型/依赖/授权缺失）


@dataclass
class Result:
    """命令执行结果的统一信封。

    Attributes:
        ok: 是否成功。
        data: 成功时的结构化数据（audio_path / duration / engine / params 等）。
        error: 失败时的可读错误信息。
        error_code: 机器可读的错误类别（agent 据此分支）。
        warnings: 非致命告警列表。
        command: 产生该结果的命令名（便于 agent 关联）。
    """

    ok: bool = True
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    error_code: str = ""
    warnings: list[str] = field(default_factory=list)
    command: str = ""

    @classmethod
    def success(cls, data: dict[str, Any] | None = None, **kwargs: Any) -> Result:
        """构造成功结果。"""
        return cls(ok=True, data=dict(data or {}), **kwargs)

    @classmethod
    def failure(cls, error: str, error_code: str = "BUSINESS_ERROR", **kwargs: Any) -> Result:
        """构造失败结果。"""
        return cls(ok=False, error=error, error_code=error_code, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        """转换为 JSON 可序列化字典，键顺序稳定。"""
        payload: dict[str, Any] = {
            "ok": self.ok,
            "command": self.command,
        }
        if self.ok:
            payload["data"] = self.data
        else:
            payload["error"] = self.error
            payload["error_code"] = self.error_code
        if self.warnings:
            payload["warnings"] = self.warnings
        return payload

    def exit_code(self) -> int:
        """按错误类别映射到语义化退出码。"""
        if self.ok:
            return EXIT_OK
        mapping = {
            "USAGE_ERROR": EXIT_USAGE_ERROR,
            "NOT_READY": EXIT_NOT_READY,
            "ENVIRONMENT": EXIT_NOT_READY,
            "CONSENT_REQUIRED": EXIT_NOT_READY,
        }
        return mapping.get(self.error_code, EXIT_BUSINESS_ERROR)


def log(message: str, *, level: str = "INFO") -> None:
    """写日志到 stderr（不污染 stdout 的 JSON 输出）。"""
    sys.stderr.write(f"[{level}] {message}\n")
    sys.stderr.flush()


def warn(message: str) -> None:
    """写警告日志到 stderr。"""
    log(message, level="WARN")


# JSON 模式下保存的原始 stdout（第三方库如 funasr 在 import 时会 print 到 stdout，
# 必须隔离，否则 JSON 流被污染、不可解析）。
_ORIGINAL_STDOUT: Any = None


def begin_json_mode() -> None:
    """进入 JSON 模式：把 sys.stdout 重定向到 stderr。

    此后所有第三方 ``print()`` 都落到 stderr；最终结果由 :func:`emit` 写回
    原始 stdout，保证 stdout 只有一行合法 JSON。
    """
    global _ORIGINAL_STDOUT
    if _ORIGINAL_STDOUT is None:
        _ORIGINAL_STDOUT = sys.stdout
        sys.stdout = sys.stderr


def _result_stream() -> Any:
    """返回承载结果数据的流（JSON 模式用原始 stdout）。"""
    return _ORIGINAL_STDOUT if _ORIGINAL_STDOUT is not None else sys.stdout


def emit(result: Result, *, as_json: bool = False) -> int:
    """统一输出结果并返回退出码。

    Args:
        result: 命令执行结果。
        as_json: True 时输出单行 JSON 到原始 stdout；否则输出人类可读文本。

    Returns:
        语义化退出码，供 ``sys.exit()`` 使用。
    """
    for w in result.warnings:
        warn(w)

    out = _result_stream()
    if as_json:
        out.write(json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
    else:
        if result.ok:
            _render_human_success(result, out)
        else:
            sys.stderr.write(f"错误 [{result.error_code}]: {result.error}\n")
    out.flush()
    sys.stderr.flush()
    return result.exit_code()


def _render_human_success(result: Result, out: Any = None) -> None:
    """人类可读的成功输出：逐行列出 data 键值。"""
    stream = out if out is not None else sys.stdout
    if not result.data:
        stream.write("完成\n")
        return
    for key, value in result.data.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        stream.write(f"{key}: {value}\n")


def result_from_generation(gen_result: Any, *, command: str = "") -> Result:
    """把 service_layer 的 ``GenerationResult`` 转成统一 Result。

    兼容 ``to_dict()`` 与 dataclass 两种形态，便于后续接入其他服务结果。
    """
    try:
        payload = gen_result.to_dict() if hasattr(gen_result, "to_dict") else asdict(gen_result)
    except Exception:
        payload = {"message": str(gen_result)}
    return Result.success(data=payload, command=command)
