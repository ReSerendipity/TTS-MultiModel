"""系统健康检查与生成统计 API 路由。

架构说明：
- 供 k8s liveness/readiness probe 与 WebUI Settings 页的 System Stats 面板使用
- 路径前缀：``/api/system``
- 接口清单：
  * ``GET /api/system/health/ping`` — liveness 探针（内存级快速响应，不访问 DB/GPU）
  * ``GET /api/system/health/ready`` — readiness 探针（深度检查：模型加载状态 + DB 连通性 + GPU）
  * ``GET /api/system/stats`` — 生成统计（累计次数/成功率/平均耗时/OOM 重试/熔断次数）
  * ``GET /api/system/health/gpu-leak`` — 调用 HealthMonitor 检查显存泄漏预警
  * ``GET /api/system/health`` — 向后兼容的完整健康报告（WebUI Settings 页面原接口）
  * ``POST /api/system/shutdown`` — 优雅关闭服务器（向后兼容）
"""

import asyncio
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any

import psutil
from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger("tts_multimodel")

router = APIRouter(prefix="/api/system", tags=["system"])

from .gpu import _get_gpu_device, _get_gpu_utilization  # noqa: E402

_SESSION_START_TS: float = time.time()
_SESSION_START: str = datetime.now().isoformat()

_generation_counter: dict[str, int] = {"total": 0, "success": 0, "failed": 0}
_counter_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Pydantic 响应模型
# ---------------------------------------------------------------------------


class SystemStatsResponse(BaseModel):
    """生成统计响应模型。

    Attributes:
        total_generations: 累计生成尝试总次数（成功 + 失败）。
        total_errors: 累计生成失败次数。
        success_rate: 生成成功率百分比（0-100）。
        avg_gen_time_ms: 平均单次生成耗时（毫秒）。
        total_oom_retries: 累计 OOM 自动重试次数。
        circuit_breaker_trips: 累计显存熔断触发次数。
        uptime_seconds: 进程运行时间（秒）。
    """

    total_generations: int = Field(default=0, description="累计生成尝试次数")
    total_errors: int = Field(default=0, description="累计生成失败次数")
    success_rate: float = Field(default=100.0, description="成功率（0-100）")
    avg_gen_time_ms: float = Field(default=0.0, description="平均生成耗时（毫秒）")
    total_oom_retries: int = Field(default=0, description="累计 OOM 重试次数")
    circuit_breaker_trips: int = Field(default=0, description="熔断触发次数")
    uptime_seconds: float = Field(default=0.0, description="运行时间（秒）")


# ---------------------------------------------------------------------------
# 内部计数辅助函数（向后兼容，供其他模块导入）
# ---------------------------------------------------------------------------


def increment_generation(success: bool = True) -> None:
    """增加一次生成计数。

    Args:
        success: 本次生成是否成功。
    """
    with _counter_lock:
        _generation_counter["total"] += 1
        if success:
            _generation_counter["success"] += 1
        else:
            _generation_counter["failed"] += 1


def get_generation_stats() -> dict[str, int]:
    """获取内存中的生成计数快照。

    Returns:
        Dict[str, int]: 包含 ``total`` / ``success`` / ``failed`` 三个键。
    """
    with _counter_lock:
        return dict(_generation_counter)


def _uptime_seconds() -> float:
    """计算当前进程运行时间。

    Returns:
        float: 自进程启动以来的秒数。
    """
    return round(time.time() - _SESSION_START_TS, 3)


# ---------------------------------------------------------------------------
# 1. Liveness: /health/ping
# ---------------------------------------------------------------------------


@router.get("/health/ping", summary="Liveness 探针", description="极快内存级响应，不访问 DB/GPU，供 k8s liveness 使用")
def ping() -> dict[str, Any]:
    """存活探针：只返回当前时间戳，不做任何 I/O。

    Why 不访问 DB/GPU：
        k8s 默认 liveness probe 10 秒一次，若每次调用 nvml 查询（10~50ms/次），
        一小时 360 次就累计 1.8s GPU 占用；此处只做内存级响应，耗时 <1ms，
        对推理零干扰。

    Returns:
        固定结构 ``{"status": "ok", "ts": <unix_ms>}``。
    """
    return {"status": "ok", "ts": int(time.time() * 1000)}


# ---------------------------------------------------------------------------
# 2. Readiness: /health/ready
# ---------------------------------------------------------------------------


@router.get(
    "/health/ready", summary="Readiness 探针", description="深度健康检查：模型加载状态 + DB 连通性 + GPU 可用性"
)
async def ready() -> dict[str, Any]:
    """就绪探针：检查模型加载状态、DB 连通性与 GPU 可用性。

    Why 只查 ``registry.current_engine is not None`` 而不做一次推理：
        k8s readiness probe 默认 10 秒一次，若每次都跑一次 VoxCPM2 推理
        （约 1s），会造成持续 GPU 负载并与实际用户请求抢资源。
        ``registry.current_engine`` 是原子指针读，耗时 ~1μs，
        足够表达"模型已加载、可以接收流量"的语义。

    Returns:
        200 OK + JSON，``status`` 取值 ``ready`` / ``degraded``。
        degraded 模式下 WebUI 显示黄色告警但应用仍可用（生成正常但 history 不入库）。
    """
    result: dict[str, Any] = {
        "status": "ready",
        "model_loaded": False,
        "db_connected": False,
        "gpu_available": False,
        "current_engine": "none",
        "uptime_seconds": _uptime_seconds(),
    }

    # --- 模型加载状态（原子读） ---
    try:
        from ...model_registry import registry

        result["model_loaded"] = registry.current_engine is not None and registry.is_engine_ready()
        result["current_engine"] = registry.current_engine or "none"
    except Exception as exc:  # noqa: BLE001 — 兜底为未就绪，不中断探针
        logger.warning(f"[readiness] 模型状态检查失败: {exc}")
        result["model_loaded"] = False

    # --- DB 连通性检查（OperationalError -> degraded，不是 500） ---
    try:
        from ...history_db import get_history_db

        db = get_history_db()
        # 只跑一条轻量 SELECT 1，不加锁，不触发表扫描
        cursor = db._execute("SELECT 1")
        cursor.fetchone()
        result["db_connected"] = True
    except sqlite3.OperationalError as exc:
        # DB 文件损坏 / 锁超时：返回 degraded 而非 500，让流量继续进入（生成可以，history 不记录）
        result["status"] = "degraded"
        result["db_connected"] = False
        logger.error(f"[readiness] DB 连通性失败（degraded 模式）: {exc}")
    except Exception as exc:  # noqa: BLE001
        result["status"] = "degraded"
        result["db_connected"] = False
        logger.error(f"[readiness] DB 未知错误（degraded 模式）: {exc}")

    # --- GPU 可用性 ---
    try:
        from ...gpu_backend import GPUBackend, GPUBackendManager

        backend = GPUBackendManager.detect_backend()
        if backend == GPUBackend.CPU:
            result["gpu_available"] = False
        else:
            device = _get_gpu_device()
            props = GPUBackendManager.get_device_properties(device)
            result["gpu_available"] = props.get("total_memory", 0) > 0
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[readiness] GPU 检查失败（非关键）: {exc}")
        result["gpu_available"] = False

    return result


# ---------------------------------------------------------------------------
# 3. 生成统计: /stats
# ---------------------------------------------------------------------------


@router.get(
    "/stats",
    summary="生成统计",
    description="累计生成统计：总数/成功率/平均耗时/OOM/熔断次数",
    response_model=SystemStatsResponse,
)
def get_stats() -> SystemStatsResponse:
    """返回全局生成统计指标。"""
    resp = SystemStatsResponse(uptime_seconds=_uptime_seconds())

    # 从 HealthMonitor 取指标（全局事实源）
    try:
        from ...monitor import get_health_monitor

        hm = get_health_monitor()
        report = hm.get_metrics() if hasattr(hm, "get_metrics") else hm.get_health_report()

        resp.total_generations = int(report.get("total_generations", 0))
        resp.total_errors = int(report.get("total_errors", 0))
        resp.total_oom_retries = int(report.get("total_oom_retries", 0))
        resp.circuit_breaker_trips = int(report.get("circuit_breaker_trips", 0))

        if resp.total_generations > 0:
            resp.success_rate = round((resp.total_generations - resp.total_errors) / resp.total_generations * 100, 2)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[stats] HealthMonitor 取数失败，回退内存计数器: {exc}")
        # 回退到本文件的内存计数器（保证接口永不抛错）
        mem_stats = get_generation_stats()
        resp.total_generations = mem_stats["total"]
        resp.total_errors = mem_stats["failed"]
        if resp.total_generations > 0:
            resp.success_rate = round(mem_stats["success"] / resp.total_generations * 100, 2)

    # 平均生成耗时
    try:
        from ...model_manager import _gen_tracker

        resp.avg_gen_time_ms = round(_gen_tracker.avg_gen_time * 1000, 1)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[stats] 平均耗时读取失败: {exc}")
        resp.avg_gen_time_ms = 0.0

    return resp


# ---------------------------------------------------------------------------
# 3.5 队列状态: /queue
# ---------------------------------------------------------------------------


@router.get("/queue", summary="生成队列状态", description="异步生成任务队列状态：排队数/活跃数/已完成/已取消")
def get_queue_status() -> dict[str, Any]:
    """返回生成任务队列的当前状态（参考 VoiceBox 队列监控设计）。

    Returns:
        队列状态 JSON，包含 ``queue_size``、``active``、``completed``、
        ``cancelled``、``has_active_generation`` 等字段。
    """
    try:
        from ...task_queue import get_queue_status as _get_qstatus

        return _get_qstatus()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[queue] 队列状态读取失败: {exc}")
        return {
            "queue_size": 0,
            "active": None,
            "completed": 0,
            "cancelled": 0,
            "has_active_generation": False,
            "available": False,
        }


# ---------------------------------------------------------------------------
# 4. GPU 泄漏预警: /health/gpu-leak
# ---------------------------------------------------------------------------


@router.get(
    "/health/gpu-leak",
    summary="GPU 显存泄漏检查",
    description="调用 HealthMonitor.check_memory_leak()，无泄漏返回 null",
)
def gpu_leak() -> dict[str, Any]:
    """检查潜在 GPU 显存泄漏。

    CUDA 不可用时返回 ``{"status": "no_gpu"}``，不抛 500 错误。
    """
    try:
        from ...gpu_backend import GPUBackend, GPUBackendManager

        backend = GPUBackendManager.detect_backend()
        if backend == GPUBackend.CPU:
            return {"status": "no_gpu", "warning": None}
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[gpu-leak] GPU 后端检测失败: {exc}")
        return {"status": "no_gpu", "warning": None}

    try:
        from ...monitor import get_health_monitor

        hm = get_health_monitor()
        warning: str | None = hm.check_memory_leak()
        return {"status": "ok", "warning": warning}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[gpu-leak] 泄漏检测异常: {exc}")
        return {"status": "error", "warning": None}


# ---------------------------------------------------------------------------
# 5. 向后兼容: /health（原完整健康报告，Settings 页面仍使用）
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    summary="健康检查（完整）",
    description="向后兼容：返回 GPU/CPU/模型/缓存完整健康报告，供 Settings 页 System Stats 使用",
)
async def get_health() -> dict[str, Any]:
    """完整健康报告（原接口，100% 向后兼容）。"""
    from ...monitor import get_health_monitor

    health_monitor = get_health_monitor()
    report = health_monitor.get_health_report()

    health: dict[str, Any] = {
        "gpu": {"memory_used_mb": 0, "memory_total_mb": 0, "memory_percent": 0, "gpu_util": 0, "trend": "stable"},
        "cpu": {"memory_used_mb": 0, "memory_total_mb": 0, "percent": 0},
        "model": {
            "current_engine": "none",
            "model_size": "none",
            "status": report.get("model_status", "unknown"),
            "load_time": None,
        },
        "stats": {
            "total_generations": report.get("total_generations", 0),
            "total_errors": report.get("total_errors", 0),
            "total_oom_retries": report.get("total_oom_retries", 0),
            "average_time": 0.0,
            "success_rate": report.get("success_rate", 100.0),
            "session_start": _SESSION_START,
            "uptime_seconds": report.get("uptime_seconds", 0),
        },
    }

    # --- GPU 显存 ---
    try:
        from ...gpu_backend import GPUBackend, GPUBackendManager

        backend = GPUBackendManager.detect_backend()
        if backend != GPUBackend.CPU:
            device_idx = _get_gpu_device()
            props = GPUBackendManager.get_device_properties(backend=backend, index=device_idx)
            total = props.get("total_memory", 0)
            allocated = GPUBackendManager.memory_allocated(backend=backend)
            reserved = GPUBackendManager.memory_reserved(backend=backend)
            used = max(allocated, reserved)
            health["gpu"]["memory_used_mb"] = round(used / (1024 * 1024), 1)
            health["gpu"]["memory_total_mb"] = round(total / (1024 * 1024), 1)
            if total > 0:
                health["gpu"]["memory_percent"] = round(used / total * 100, 2)
            try:
                gpu_util = _get_gpu_utilization()
                health["gpu"]["gpu_util"] = round(float(gpu_util), 2)
                logger.debug(f"GPU 利用率: {gpu_util}%")
            except (RuntimeError, ValueError) as exc:
                health["gpu"]["gpu_util"] = 0
                logger.warning(f"GPU 利用率检查失败: {exc}")
            try:
                health_monitor.record_vram_usage(health["gpu"]["memory_used_mb"])
            except Exception as exc:
                logger.debug(f"非关键错误: {exc}")
    except ImportError:
        pass
    except Exception as exc:
        logger.debug(f"GPU 健康检查失败: {exc}")

    # nvidia-smi fallback（PyTorch 后端拿不到时）
    if health["gpu"]["memory_total_mb"] == 0:
        try:
            proc = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                "--query-gpu=memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise
            if proc.returncode == 0 and stdout:
                output = stdout.decode("utf-8", errors="replace").strip()
                parts = output.split(",")
                if len(parts) >= 2:
                    total_mb = float(parts[0].strip())
                    used_mb = float(parts[1].strip())
                    health["gpu"]["memory_used_mb"] = round(used_mb, 1)
                    health["gpu"]["memory_total_mb"] = round(total_mb, 1)
                    if total_mb > 0:
                        health["gpu"]["memory_percent"] = round(used_mb / total_mb * 100, 2)
                    if len(parts) >= 3:
                        health["gpu"]["gpu_util"] = round(float(parts[2].strip()), 2)
                    else:
                        health["gpu"]["gpu_util"] = 0
                    try:
                        health_monitor.record_vram_usage(health["gpu"]["memory_used_mb"])
                    except Exception as exc:
                        logger.debug(f"非关键错误: {exc}")
        except (OSError, RuntimeError, asyncio.TimeoutError, ValueError) as exc:
            logger.debug(f"nvidia-smi GPU 检查失败: {exc}")

    # --- CPU ---
    try:
        cpu_mem = psutil.virtual_memory()
        health["cpu"]["memory_used_mb"] = round((cpu_mem.total - cpu_mem.available) / (1024 * 1024), 1)
        health["cpu"]["memory_total_mb"] = round(cpu_mem.total / (1024 * 1024), 1)
        health["cpu"]["percent"] = round(psutil.cpu_percent(interval=0), 1)
    except (OSError, RuntimeError) as exc:
        logger.debug(f"CPU 健康检查失败: {exc}")

    # --- 模型状态 ---
    try:
        from ...model_manager import (
            _gen_tracker,
            get_persona_cache_stats,
        )
        from ...model_registry import ENGINE_DISPLAY_NAMES, registry

        engine_name = registry.current_engine or "none"
        engine_display = ENGINE_DISPLAY_NAMES.get(engine_name, engine_name)

        health["model"]["current_engine"] = engine_display
        health["model"]["model_size"] = registry.current_size or "none"
        health["model"]["status"] = "ready" if registry.is_engine_ready() else "not_loaded"
        health["model"]["load_time"] = _SESSION_START
        health["model"]["voxcpm2_loaded"] = registry.voxcpm_model is not None
        health["model"]["indextts2_loaded"] = registry.indextts2_engine is not None

        if "gpu" in report:
            health["gpu"]["trend"] = report["gpu"].get("trend", "stable")
            if "leak_warning" in report["gpu"]:
                health["gpu"]["leak_warning"] = report["gpu"]["leak_warning"]

        gen_stats = get_generation_stats()
        health["stats"]["total_generations"] = gen_stats["total"]
        health["stats"]["average_time"] = round(_gen_tracker.avg_gen_time, 1)
        total = gen_stats["total"]
        health["stats"]["success_rate"] = round((gen_stats["success"] / total * 100) if total > 0 else 100.0, 1)

        try:
            cache_stats = get_persona_cache_stats()
            health["cache"] = {
                "hit_rate": cache_stats["hit_rate"],
                "hits": cache_stats["hits"],
                "misses": cache_stats["misses"],
                "size": cache_stats["size"],
                "maxsize": cache_stats["maxsize"],
            }
        except (OSError, RuntimeError, KeyError, TypeError) as exc:
            logger.debug(f"缓存统计检查失败: {exc}")
            health["cache"] = {"hit_rate": 0.0, "hits": 0, "misses": 0, "size": 0, "maxsize": 0}
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"模型健康检查失败: {exc}")
        if "cache" not in health:
            health["cache"] = {"hit_rate": 0.0, "hits": 0, "misses": 0, "size": 0, "maxsize": 0}

    return health


# ---------------------------------------------------------------------------
# 6. 向后兼容: /shutdown
# ---------------------------------------------------------------------------


@router.post("/shutdown", summary="优雅关闭服务器", description="请求服务器优雅关闭，延迟 1 秒后停止进程")
def shutdown_server() -> dict[str, Any]:
    """请求服务器优雅关闭。

    在后台延迟执行，给响应留出返回时间。
    """
    logger.info("[SHUTDOWN] 收到关闭请求，将在 1 秒后关闭服务器...")

    def _do_shutdown() -> None:
        time.sleep(1)
        os._exit(0)

    threading.Thread(target=_do_shutdown, daemon=True).start()
    return {"status": "ok", "message": "服务器正在关闭..."}


# ---------------------------------------------------------------------------
# 延迟引入 sqlite3（部分最小化环境可能不使用 history_db）
# ---------------------------------------------------------------------------
import sqlite3  # noqa: E402
