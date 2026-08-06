"""GPU 状态与显存 API 路由。

架构说明：
- 供 WebUI 顶部状态栏（实时显存/利用率/温度）与 Settings → GPU 面板使用
- 路径前缀：``/api/system``
- 封装 ``gpu_backend`` / ``gpu_utils`` / ``monitor.HealthMonitor`` 提供统一响应结构
- 接口清单：
  * ``GET /api/system/gpu/status`` — 实时 GPU 状态（设备名/显存总量·已用·空闲·百分比/利用率/温度）
  * ``GET /api/system/gpu/history`` — 最近 60 秒显存采样曲线（HealthMonitor._vram_samples）
  * ``POST /api/system/gpu/cleanup`` — 手动触发显存清理（free_gpu_memory + torch.cuda.empty_cache）
"""

import contextlib
import logging
import subprocess
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

logger = logging.getLogger("tts_multimodel")

router = APIRouter(prefix="/api/system", tags=["system"])


# ---------------------------------------------------------------------------
# Pydantic 响应模型
# ---------------------------------------------------------------------------


class GPUStatusResponse(BaseModel):
    """GPU 实时状态响应模型。

    Attributes:
        device_name: 设备型号名，如 ``"NVIDIA GeForce RTX 3060"``；无 GPU 时为 ``"CPU"``。
        vram_total_mb: 显存总量（MB）。
        vram_used_mb: 已分配显存（MB）。
        vram_free_mb: 空闲显存（MB）= total - used。
        vram_percent: 显存占用百分比（0-100）。
        utilization_gpu_pct: GPU 计算核心利用率百分比（0-100）。
        temperature_c: GPU 温度（摄氏度）。仅 NVML 可用时返回有效值，否则为 null。
    """

    device_name: str = Field(default="CPU", description="GPU 设备名，无 GPU 时为 'CPU'")
    vram_total_mb: float = Field(default=0.0, description="显存总量（MB）")
    vram_used_mb: float = Field(default=0.0, description="已用显存（MB）")
    vram_free_mb: float = Field(default=0.0, description="空闲显存（MB）")
    vram_percent: float = Field(default=0.0, description="显存占用百分比（0-100）")
    utilization_gpu_pct: float = Field(default=0.0, description="GPU 核心利用率（0-100）")
    temperature_c: float | None = Field(default=None, description="GPU 温度（℃），NVML 不可用时为 null")


class GPUSamplePoint(BaseModel):
    """显存采样点。

    Attributes:
        ts: Unix 时间戳（毫秒）。
        used_mb: 采样时刻已用显存（MB）。
    """

    ts: int = Field(description="采样时间戳（毫秒）")
    used_mb: float = Field(description="已用显存（MB）")


class GPUHistoryResponse(BaseModel):
    """显存历史曲线响应。

    Attributes:
        samples: 采样点列表，按时间升序。
        sample_count: 实际样本数。
    """

    samples: list[GPUSamplePoint] = Field(default_factory=list, description="采样点列表")
    sample_count: int = Field(default=0, description="样本数")


# ---------------------------------------------------------------------------
# 内部辅助：GPU 设备索引
# ---------------------------------------------------------------------------


def _get_gpu_device() -> int:
    """获取当前使用的 GPU 设备索引。

    Returns:
        int: GPU 索引号（0 起始）；后端不可用或出错时回退 0。
    """
    try:
        import torch

        from ...gpu_backend import GPUBackendManager

        if not GPUBackendManager.is_available():
            return 0

        device = GPUBackendManager.get_device()
        if isinstance(device, torch.device):
            return device.index if device.index is not None else 0
        return int(device) if device is not None else 0
    except (OSError, RuntimeError, ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# NVML 句柄缓存 + 失败冷却
# ---------------------------------------------------------------------------

_nvml_state: dict[str, Any] = {
    "handle": None,
    "initialized": False,
    "init_time": 0.0,
    "init_failed": False,
    "last_error": None,
    "device_index": 0,
    "failure_time": 0.0,
}
_nvml_lock = threading.Lock()

_NVML_CACHE_TTL: int = 300
_NVML_FAIL_COOLDOWN: int = 60


def _get_nvml_handle() -> Any | None:
    """获取并缓存 NVML 设备句柄，含失败冷却期。

    失败后 60 秒内跳过重新初始化，避免每个 API 调用都走一次失败的加载流程
    （pynvml/nvidia-smi 初始化失败一次约 100ms，高并发场景下累积可观）。
    """
    global _nvml_state

    with _nvml_lock:
        current_time = time.time()

        # 缓存命中且未过期
        if _nvml_state["initialized"] and _nvml_state["handle"] is not None and not _nvml_state["init_failed"]:
            if current_time - _nvml_state["init_time"] < _NVML_CACHE_TTL:
                return _nvml_state["handle"]
            # 过期重置，准备重新初始化
            _nvml_state["initialized"] = False
            _nvml_state["handle"] = None

        # 失败冷却期
        if _nvml_state["init_failed"]:
            last_failure = _nvml_state.get("failure_time", 0)
            if current_time - last_failure < _NVML_FAIL_COOLDOWN:
                return None
            _nvml_state["init_failed"] = False

        try:
            import pynvml

            if not _nvml_state["initialized"]:
                try:
                    pynvml.nvmlInit()
                except pynvml.NVMLError_LibraryNotLoaded:
                    pass
                except (pynvml.NVMLError, OSError, RuntimeError) as init_err:
                    _nvml_state["init_failed"] = True
                    _nvml_state["failure_time"] = current_time
                    _nvml_state["last_error"] = str(init_err)
                    logger.warning(f"NVML 库初始化失败: {init_err}")
                    return None

                _nvml_state["initialized"] = True
                _nvml_state["init_time"] = current_time

            device_idx = _get_gpu_device()
            _nvml_state["device_index"] = device_idx

            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(device_idx)
                _nvml_state["handle"] = handle
                return handle
            except (pynvml.NVMLError, OSError, RuntimeError) as handle_err:
                if device_idx != 0:
                    try:
                        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                        _nvml_state["handle"] = handle
                        _nvml_state["device_index"] = 0
                        logger.info(f"NVML 回退到 GPU #0（原 #{device_idx} 失败）")
                        return handle
                    except (pynvml.NVMLError, OSError, RuntimeError) as fb_err:
                        _nvml_state["init_failed"] = True
                        _nvml_state["failure_time"] = current_time
                        _nvml_state["last_error"] = str(fb_err)
                        logger.warning(f"NVML GPU #0 回退失败: {fb_err}")
                        return None
                else:
                    _nvml_state["init_failed"] = True
                    _nvml_state["failure_time"] = current_time
                    _nvml_state["last_error"] = str(handle_err)
                    logger.warning(f"NVML 句柄获取失败: {handle_err}")
                    return None

        except ImportError:
            _nvml_state["init_failed"] = True
            _nvml_state["failure_time"] = current_time
            _nvml_state["last_error"] = "pynvml not installed"
            return None
        except Exception as exc:  # noqa: BLE001
            _nvml_state["init_failed"] = True
            _nvml_state["failure_time"] = current_time
            _nvml_state["last_error"] = str(exc)
            logger.error(f"NVML 初始化意外错误: {exc}", exc_info=True)
            return None


# ---------------------------------------------------------------------------
# 内部辅助：GPU 利用率
# ---------------------------------------------------------------------------


def _get_gpu_utilization_from_nvml() -> int | None:
    """通过 NVML 读取 GPU 计算核心利用率。"""
    try:
        handle = _get_nvml_handle()
        if handle is None:
            return None

        import pynvml

        util_rates = pynvml.nvmlDeviceGetUtilizationRates(handle)
        return int(util_rates.gpu)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"NVML GPU 利用率读取失败: {exc}")
        return None


def _get_gpu_temperature_from_nvml() -> float | None:
    """通过 NVML 读取 GPU 温度（℃）。

    Why 不通过 torch 取温度：
        PyTorch 本身没有温度 API，只有 NVIDIA NVML（Management Library）提供
        温度传感器读取；AMD / Apple MPS / CPU 场景下 NVML 均不可用，
        此时 temperature_c 返回 null，前端根据该字段是否为空隐藏温度显示。
    """
    try:
        handle = _get_nvml_handle()
        if handle is None:
            return None

        import pynvml

        temp_c = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        return float(temp_c)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"NVML GPU 温度读取失败: {exc}")
        return None


def _get_gpu_utilization_from_nvidia_smi() -> int | None:
    """通过 nvidia-smi CLI 作为 NVML 不可用时的回退方案。"""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            first_line = result.stdout.strip().splitlines()[0].strip()
            return int(first_line)
        return None
    except FileNotFoundError:
        return None
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return None


def _get_gpu_utilization() -> int:
    """获取 GPU 核心利用率百分比（统一入口，NVML → nvidia-smi → 0）。"""
    try:
        from ...gpu_backend import GPUBackend, GPUBackendManager

        backend = GPUBackendManager.detect_backend()
        if backend != GPUBackend.CUDA:
            return 0
    except (OSError, RuntimeError, ImportError):
        return 0

    nvml_util = _get_gpu_utilization_from_nvml()
    if nvml_util is not None:
        return nvml_util

    smi_util = _get_gpu_utilization_from_nvidia_smi()
    if smi_util is not None:
        return smi_util

    return 0


# ---------------------------------------------------------------------------
# 1. GET /gpu/status — 实时状态
# ---------------------------------------------------------------------------


@router.get(
    "/gpu/status",
    summary="GPU 实时状态",
    description="设备名/显存/利用率/温度，无 GPU 时返回 CPU 回退结构",
    response_model=GPUStatusResponse,
)
def gpu_status() -> GPUStatusResponse:
    """返回当前 GPU（或 CPU 模式）的实时状态。

    所有异常都被捕获并降级，接口永远 200 返回（即使 NVML/pynvml/torch 全部不可用）。
    """
    resp = GPUStatusResponse()

    # --- 设备名 + 显存：优先使用 gpu_backend ---
    try:
        from ...gpu_backend import GPUBackend, GPUBackendManager

        backend = GPUBackendManager.detect_backend()
        if backend == GPUBackend.CPU:
            resp.device_name = "CPU"
        else:
            device = _get_gpu_device()
            resp.device_name = GPUBackendManager.get_device_name(device) or f"{backend.value.upper()} Device"
            props = GPUBackendManager.get_device_properties(device)
            total = props.get("total_memory", 0)
            allocated = GPUBackendManager.memory_allocated(device)
            reserved = GPUBackendManager.memory_reserved(device)
            used = max(allocated, reserved)
            free = max(total - used, 0)

            resp.vram_total_mb = round(total / (1024 * 1024), 2)
            resp.vram_used_mb = round(used / (1024 * 1024), 2)
            resp.vram_free_mb = round(free / (1024 * 1024), 2)
            if total > 0:
                resp.vram_percent = round(used / total * 100, 2)
    except (OSError, RuntimeError, ValueError, TypeError, ImportError) as exc:
        logger.debug(f"gpu_backend 取显存失败: {exc}")

    # --- 利用率 ---
    try:
        util = _get_gpu_utilization()
        resp.utilization_gpu_pct = round(float(util), 1)
    except (OSError, RuntimeError, ValueError):
        resp.utilization_gpu_pct = 0.0

    # --- 温度（仅 NVML） ---
    resp.temperature_c = _get_gpu_temperature_from_nvml()

    # --- 若 gpu_backend 没取到（如 pynvml-only 环境），再走 nvidia-smi ---
    if resp.vram_total_mb <= 0:
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = [p.strip() for p in result.stdout.strip().splitlines()[0].split(",")]
                if len(parts) >= 4:
                    resp.device_name = parts[0] or resp.device_name
                    try:
                        resp.vram_total_mb = round(float(parts[1]), 2)
                        resp.vram_used_mb = round(float(parts[2]), 2)
                        resp.vram_free_mb = round(float(parts[3]), 2)
                        if resp.vram_total_mb > 0:
                            resp.vram_percent = round(resp.vram_used_mb / resp.vram_total_mb * 100, 2)
                    except ValueError:
                        pass
                    if len(parts) >= 5:
                        with contextlib.suppress(ValueError):
                            resp.utilization_gpu_pct = round(float(parts[4]), 1)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError, IndexError, ValueError):
            pass

    return resp


# ---------------------------------------------------------------------------
# 2. GET /gpu/history — 显存采样历史（60 秒）
# ---------------------------------------------------------------------------


@router.get(
    "/gpu/history",
    summary="显存采样历史",
    description="最近 60 秒显存曲线，采样点 <60 条时返回已有样本不抛错",
    response_model=GPUHistoryResponse,
)
def gpu_history() -> GPUHistoryResponse:
    """返回 HealthMonitor 中缓存的显存采样点。

    Why 采样点不足不报错：
        应用刚启动或长时间无 GPU 操作时，HealthMonitor 样本不足 60 条是常态，
        返回已有样本 + 200 比抛 404 / 空错误更符合前端连续曲线渲染预期。
    """
    samples: list[GPUSamplePoint] = []
    now_ms = int(time.time() * 1000)

    try:
        from ...monitor import get_health_monitor

        hm = get_health_monitor()
        raw: list[float] = list(getattr(hm, "_vram_samples", []))

        # 按样本数倒推时间戳（采样间隔以 1s 估算，保证前端曲线有合理的时间轴）
        n = len(raw)
        step_ms = 1000
        base_ts = now_ms - (n - 1) * step_ms
        for i, used_mb in enumerate(raw):
            samples.append(GPUSamplePoint(ts=base_ts + i * step_ms, used_mb=round(float(used_mb), 2)))

        # 只保留最近 60 条
        if len(samples) > 60:
            samples = samples[-60:]
    except (OSError, RuntimeError, AttributeError, TypeError, ValueError) as exc:
        logger.debug(f"GPU 历史采样读取失败: {exc}")

    return GPUHistoryResponse(samples=samples, sample_count=len(samples))


# ---------------------------------------------------------------------------
# 3. POST /gpu/cleanup — 手动显存清理
# ---------------------------------------------------------------------------


@router.post(
    "/gpu/cleanup",
    summary="手动清理显存",
    description="触发 free_gpu_memory + torch.cuda.empty_cache，推理中返回 409 Conflict",
)
def gpu_cleanup() -> dict[str, Any]:
    """手动触发 GPU 显存清理。

    Why 必须手动触发而不是定时自动清理：
        ``torch.cuda.empty_cache()`` 会同步等待当前 GPU 上所有 kernel 执行完毕，
        如果在推理过程中调用，会让正在运行的扩散/解码 kernel 被强制打断，
        导致生成长度不足、音频爆音或直接失败。因此必须由用户在 UI 上确认
        "当前没有生成任务"后手动点击触发，且本函数在执行前先检查
        ``model_registry.registry.is_generating`` 标志。
    """
    # --- 前置：检查是否正在推理 ---
    try:
        from ...model_registry import registry

        if getattr(registry, "is_generating", False):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="推理中，无法清理显存，请等生成完成后再尝试",
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"cleanup: 推理状态检查失败，仍尝试清理: {exc}")

    released_mb: float = 0.0

    # --- 第一步：调用分层清理 ---
    try:
        from ...gpu_utils import free_gpu_memory

        before: float = 0.0
        try:
            from ...gpu_backend import GPUBackendManager

            device = _get_gpu_device()
            before = max(
                GPUBackendManager.memory_allocated(device),
                GPUBackendManager.memory_reserved(device),
            )
        except (OSError, RuntimeError, ImportError, AttributeError):
            pass

        free_gpu_memory()

        try:
            from ...gpu_backend import GPUBackendManager

            device = _get_gpu_device()
            after = max(
                GPUBackendManager.memory_allocated(device),
                GPUBackendManager.memory_reserved(device),
            )
            released_mb = round(max(before - after, 0.0) / (1024 * 1024), 2)
        except (OSError, RuntimeError, ImportError, AttributeError):
            pass
    except (OSError, RuntimeError, ImportError) as exc:
        logger.warning(f"gpu_utils.free_gpu_memory 调用失败: {exc}")

    # --- 第二步：兜底 torch.cuda.empty_cache ---
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except (OSError, RuntimeError, ImportError) as exc:
        logger.debug(f"torch.cuda.empty_cache 调用失败: {exc}")

    logger.info(f"[GPU Cleanup] 显存清理完成，约释放 {released_mb} MB")
    return {
        "status": "ok",
        "released_mb": released_mb,
        "message": "显存清理完成" if released_mb > 0 else "已执行清理，无可释放显存",
    }
