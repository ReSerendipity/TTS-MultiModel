"""持续容量采样后台任务（SRE 评估 P2-3 落地）。

原报告缺口：容量工具仅离线脚本、无持续监控。本模块在应用 lifespan 启动一个
低开销周期任务，周期性采集 GPU/CPU 用量，写入内存环形缓冲，供 ``/api/system/capacity``
与告警规则（vram_usage_warn）消费。采样失败静默跳过，绝不干扰推理主路径。

Why 与 monitor.py 的 GPU 采样区分：
    monitor.py 的 ``record_vram_usage`` 仅在访问 /health 时按需触发，非时序化；
    本模块提供**固定频率时间序列**，用于刻画趋势与容量预警。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Any

logger = logging.getLogger("tts_multimodel")

_MAX_SAMPLES = 360  # 保留最近 360 个点（默认 30s 间隔 ≈ 3 小时）
_DEFAULT_INTERVAL = 30.0

_samples: deque[dict[str, Any]] = deque(maxlen=_MAX_SAMPLES)
_local_lock = threading.Lock()


def _snapshot() -> dict[str, Any]:
    """采集一次容量快照。"""
    snap: dict[str, Any] = {"ts": int(time.time() * 1000)}
    try:
        from ..monitor import get_health_monitor

        hm = get_health_monitor()
        report = hm.get_metrics()
        gpu = report.get("gpu")
        if isinstance(gpu, dict) and gpu.get("mode") != "cpu" and "vram_used_mb" in gpu:
            snap["vram_used_mb"] = float(gpu.get("vram_used_mb", 0))
            snap["vram_total_mb"] = float(gpu.get("vram_total_mb", 0))
            snap["vram_usage_pct"] = float(gpu.get("vram_usage_pct", 0))
        else:
            snap["vram_usage_pct"] = 0.0
    except Exception as exc:  # noqa: BLE001
        logger.debug("[capacity] GPU 快照失败: %s", exc)
        snap["vram_usage_pct"] = 0.0

    try:
        import psutil

        snap["cpu_percent"] = float(psutil.cpu_percent(interval=0))
        mem = psutil.virtual_memory()
        snap["mem_percent"] = float(mem.percent)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[capacity] CPU 快照失败: %s", exc)

    return snap


def get_capacity_history(limit: int | None = None) -> list[dict[str, Any]]:
    """返回容量采样历史（可选限制条数）。"""
    with _local_lock:
        items = list(_samples)
    if limit is not None and limit > 0:
        items = items[-limit:]
    return items


def get_capacity_latest() -> dict[str, Any]:
    """返回最近一次容量快照。"""
    with _local_lock:
        return dict(_samples[-1]) if _samples else {}


async def capacity_sampling_loop(interval: float = _DEFAULT_INTERVAL, stop_event: asyncio.Event | None = None) -> None:
    """容量采样后台循环。

    Args:
        interval: 采样间隔（秒）。
        stop_event: 可选停止事件（shutdown 时置位）。
    """
    logger.info("[capacity] 容量采样后台任务已启动（间隔 %.0fs）", interval)
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            snap = _snapshot()
            with _local_lock:
                _samples.append(snap)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[capacity] 采样异常（忽略）: %s", exc)
        try:
            if stop_event is not None:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            else:
                await asyncio.sleep(interval)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
    logger.info("[capacity] 容量采样后台任务已停止")
