#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI 系统域适配层 + serve 兜底（ADR-0005 P4）。

CLI 直调优先；对于强绑 HTTP 的面（OpenAI `/v1` 兼容、SSE、HTMX 页面）提供
``serve`` 子命令拉起完整 FastAPI，实现 100% 功能覆盖：

    - serve：启动 uvicorn（同一进程内模型状态可跨请求保持）
    - system health / gpu / logs / settings：只读运维视图
"""

from __future__ import annotations

import os
import platform
import sys
import time
from typing import Any

from integrated_app.cli_output import Result, log


def cmd_serve(args: Any) -> int:
    """启动完整 FastAPI 服务（覆盖 /v1、SSE、页面等 HTTP 专属能力）。

    这是"双模式混合"的 HTTP 侧：阻塞运行直到 Ctrl+C。启动信息与访问地址
    写 stderr，stdout 不被占用（服务自身日志亦走 stderr）。
    """
    from integrated_app.app_server import run_server

    host = getattr(args, "host", "127.0.0.1") or "127.0.0.1"
    port = int(getattr(args, "port", 7869) or 7869)
    log(f"启动 TTSMultiModel 服务: http://{host}:{port}（Ctrl+C 停止）")
    log("HTTP 专属能力：OpenAI 兼容 /v1/audio/speech、SSE /api/sse/events、Web UI 页面")
    try:
        run_server(ip=host, port=port)
    except KeyboardInterrupt:
        log("服务已停止")
    return 0


def _mask_secrets(obj: Any, _depth: int = 0) -> Any:
    """递归脱敏：键名含 token/secret/password/key 的值替换为 ***。"""
    if _depth > 8:
        return obj
    if isinstance(obj, dict):
        masked: dict[str, Any] = {}
        for key, value in obj.items():
            lowered = str(key).lower()
            if any(word in lowered for word in ("token", "secret", "password", "api_key", "hmac", "signing_key")):
                masked[key] = "***" if value not in (None, "") else value
            else:
                masked[key] = _mask_secrets(value, _depth + 1)
        return masked
    if isinstance(obj, list):
        return [_mask_secrets(item, _depth + 1) for item in obj[:50]]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def cmd_system_health(args: Any) -> Result:
    """环境健康快照：平台 / Python / GPU / 模型可用性。"""
    import psutil

    from integrated_app.config import check_models_available

    gpu: dict[str, Any] = {"available": False}
    try:
        from integrated_app.gpu_utils import get_gpu_device, get_gpu_memory_info

        device = get_gpu_device()
        if device is not None:
            total, allocated, reserved, free = get_gpu_memory_info()
            gpu = {
                "available": True,
                "device_index": device,
                "total_gb": round(total / 1024**3, 2) if total else 0.0,
                "allocated_gb": round(allocated / 1024**3, 2) if allocated else 0.0,
                "reserved_gb": round(reserved / 1024**3, 2) if reserved else 0.0,
                "free_gb": round(free / 1024**3, 2) if free else 0.0,
            }
    except Exception as exc:  # noqa: BLE001 - GPU 探测失败不应影响整体健康
        gpu = {"available": False, "error": str(exc)}

    try:
        models_ok, missing = check_models_available()
    except Exception as exc:  # noqa: BLE001
        models_ok, missing = False, [f"检查失败: {exc}"]

    vm = psutil.virtual_memory()
    data = {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory": {
            "total_gb": round(vm.total / 1024**3, 2),
            "available_gb": round(vm.available / 1024**3, 2),
            "percent": vm.percent,
        },
        "gpu": gpu,
        "models": {"ok": bool(models_ok), "missing": list(missing)[:20]},
        "timestamp": int(time.time()),
    }
    result = Result.success(data=data, command="system-health")
    if not models_ok:
        result.warnings.append(f"缺少 {len(missing)} 个模型文件；用 `download catalog` 查看可下载项")
    return result


def cmd_system_gpu(args: Any) -> Result:
    """GPU 显存与设备信息。"""
    try:
        from integrated_app.gpu_utils import get_gpu_device, get_gpu_memory_info

        device = get_gpu_device()
        if device is None:
            return Result.success(
                data={"available": False, "message": "未检测到可用 GPU（将使用 CPU / Edge-TTS）"},
                command="system-gpu",
            )
        total, allocated, reserved, free = get_gpu_memory_info()
        data = {
            "available": True,
            "device_index": device,
            "total_gb": round(total / 1024**3, 2) if total else 0.0,
            "allocated_gb": round(allocated / 1024**3, 2) if allocated else 0.0,
            "reserved_gb": round(reserved / 1024**3, 2) if reserved else 0.0,
            "free_gb": round(free / 1024**3, 2) if free else 0.0,
        }
        return Result.success(data=data, command="system-gpu")
    except Exception as exc:  # noqa: BLE001
        return Result.failure(f"GPU 查询失败: {exc}", "BUSINESS_ERROR", command="system-gpu")


def cmd_system_logs(args: Any) -> Result:
    """读取应用日志尾部（默认 logs/app.log）。"""
    from integrated_app.config import PROJECT_ROOT

    explicit = getattr(args, "file", None)
    if explicit:
        log_path = os.path.abspath(explicit)
    else:
        logs_dir = os.path.join(PROJECT_ROOT, "logs")
        if not os.path.isdir(logs_dir):
            return Result.failure(f"日志目录不存在: {logs_dir}", "BUSINESS_ERROR", command="system-logs")
        candidates = sorted(
            (os.path.join(logs_dir, n) for n in os.listdir(logs_dir) if n.endswith(".log")),
            key=lambda p: os.path.getmtime(p),
            reverse=True,
        )
        if not candidates:
            return Result.failure("暂无日志文件", "BUSINESS_ERROR", command="system-logs")
        log_path = candidates[0]

    if not os.path.isfile(log_path):
        return Result.failure(f"日志文件不存在: {log_path}", "BUSINESS_ERROR", command="system-logs")

    tail_n = int(getattr(args, "tail", 100) or 100)
    with open(log_path, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    return Result.success(
        data={
            "log_path": log_path,
            "total_lines": len(lines),
            "lines": [line.rstrip("\n") for line in lines[-tail_n:]],
        },
        command="system-logs",
    )


def cmd_system_settings(args: Any) -> Result:
    """当前配置快照（敏感字段已脱敏）。"""
    from integrated_app.config import get_config

    cfg = get_config()
    raw: dict[str, Any] = {}
    try:
        pyd = cfg.pydantic_config
        raw = pyd.model_dump() if hasattr(pyd, "model_dump") else dict(pyd)
    except Exception as exc:  # noqa: BLE001
        return Result.failure(f"配置读取失败: {exc}", "BUSINESS_ERROR", command="system-settings")

    data = _mask_secrets(raw)
    data["_paths"] = {
        "project_root": getattr(cfg, "project_root", ""),
        "model_dir": os.path.join(str(getattr(cfg, "project_root", "")), "model"),
    }
    return Result.success(data=data, command="system-settings")
