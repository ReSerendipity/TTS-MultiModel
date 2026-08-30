"""模型管理 REST API 路由模块。

架构说明：
    本模块是 ``model_manager`` 与 ``model_registry`` 的薄代理层，
    对应前端 Settings 页与模型加载控制按钮提供后端能力；所有写操作
    （load / unload / switch / LoRA）通过 ``model_manager`` 的内部
    RLock 串行化执行，符合 AGENTS.md §6「单 Worker 串行」硬约束，
    防止并发显存占用触发 CUDA OOM。

路径前缀：
    ``/api/model``（通过 ``APIRouter(prefix="/api/model")`` 注册）

接口清单（向后兼容 100%）：
    GET    /status
    POST   /load                  Form(engine)
    POST   /unload
    POST   /switch                Form(engine)
    POST   /preload
    GET    /preload/status
    POST   /lora/load              JSON(lora_path)
    POST   /lora/unload
    POST   /lora/toggle            JSON(enabled)
    GET    /lora/state
    GET    /lora/list
    GET    /download_hints
    （取消生成：POST /api/generate/cancel 由 generate/voxcpm2/streaming.py
      在共享 /api/generate 前缀下提供；本模块曾以双重 /api 前缀重复注册，
      实际路径 /api/model/api/generate/cancel 永远无法命中，已于 2026-08 移除）

权限 / CSRF：
    所有 POST/PUT/DELETE 路由均为 state-changing 请求，由
    ``middleware/csrf.py`` 的 ``CSRFMiddleware`` 统一校验
    ``X-CSRF-Token`` 头；前端 JS 读取 ``csrf_token`` Cookie 后自动注入，
    **本路由层无需单独豁免或二次校验**。
"""

import asyncio
import json
import logging
import os
import re
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..engines.voxcpm2_engine import (
    fn_voxcpm_get_lora_state,
    fn_voxcpm_load_lora,
    fn_voxcpm_set_lora_enabled,
    fn_voxcpm_unload_lora,
)
from ..exceptions import (
    EngineSwitchError,
    GenerationError,
    InsufficientVRAMError,
    ModelLoadError,
    TTSError,
)
from ..gpu_utils import (
    GPUMemoryMonitor,  # noqa: F401 - 供 _get_vram_mb 间接调用
    free_gpu_memory,
    is_oom_error,
)
from ..model_manager import (
    _gen_tracker,
    get_preload_status,
    load_indextts2,
    load_indextts20,
    load_voxcpm2,
    preload_model,
    switch_engine,
    unload_model,
)
from ..model_registry import registry
from ..persona_manager import get_total_persona_count

router = APIRouter(prefix="/api/model", tags=["model"])

logger = logging.getLogger("tts_multimodel.model_routes")

# S-R6: 错误消息脱敏 — 匹配 Windows/Unix 文件路径
_SENSITIVE_PATH_PATTERN = re.compile(r"[A-Za-z]:\\[^\s\"'<>|*?]+|/(?:[^\s\"'<>|*?]+/)+[^\s\"'<>|*?]*")
_ERROR_MESSAGE_MAX_LENGTH = 200


def _safe_error_message(exc: Exception, max_length: int = _ERROR_MESSAGE_MAX_LENGTH) -> str:
    """对错误消息进行脱敏，避免向客户端泄露敏感信息。

    Security [D6]：
        错误消息可能包含文件路径、SQL 语句、堆栈细节等敏感信息。

    Args:
        exc:        异常对象。
        max_length: 返回消息的最大字符数。

    Returns:
        脱敏后的错误消息字符串。
    """
    if exc is None:
        return "未知错误"

    if isinstance(exc, InsufficientVRAMError):
        return f"显存不足：{str(exc)[:max_length]}"
    if isinstance(exc, EngineSwitchError):
        return f"引擎切换失败：{str(exc)[:max_length]}"
    if isinstance(exc, ModelLoadError):
        return f"模型加载失败：{str(exc)[:max_length]}"
    if isinstance(exc, TTSError):
        return str(exc)[:max_length]
    if isinstance(exc, FileNotFoundError):
        return "文件不存在或已被删除"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "操作超时，请稍后重试"
    if isinstance(exc, PermissionError):
        return "权限不足，无法访问所需资源"
    if isinstance(exc, OSError):
        msg = _SENSITIVE_PATH_PATTERN.sub("[PATH]", str(exc))
        return f"系统错误：{msg[:max_length]}"

    msg = _SENSITIVE_PATH_PATTERN.sub("[PATH]", str(exc))
    if len(msg) > max_length:
        msg = msg[:max_length] + "..."
    return msg


def _get_vram_used_mb() -> int:
    """获取当前进程已占用的 GPU 显存（MB）。失败时返回 0。"""
    try:
        info = GPUMemoryMonitor.get_vram_info()
        return int(info.get("allocated_mb", 0))
    except Exception as exc:  # noqa: BLE001
        logger.debug("获取显存占用失败: %s", exc)
        return 0


def _is_lora_enabled() -> bool:
    """判断当前是否启用了 LoRA（VoxCPM2 专有）。"""
    if registry.current_engine != "voxcpm2":
        return False
    try:
        state = fn_voxcpm_get_lora_state()
        if isinstance(state, dict):
            return bool(state.get("enabled", False) or state.get("loaded", False))
        return bool(state)
    except Exception as exc:  # noqa: BLE001
        logger.debug("查询 LoRA 状态失败: %s", exc)
        return False


# Why 所有 load/switch/unload 都走 model_manager 的串行 RLock：
#   AGENTS.md §6 硬约束「单 Worker 串行」：若前端并发点 2 次 load
#   /api/model/load?voxcpm2 + /api/model/load?indextts2 会同时占用
#   两份 GPU 显存，立即触发 CUDA OOM。model_manager 在函数内部通过
#   串行锁让第二个请求排队等待第一个完成，保证任意时刻只有一次显存操作。
@router.get("/status", summary="模型状态", description="获取当前引擎、加载状态、VRAM、persona 数量等")
async def model_status(request: Request) -> Response:
    """获取当前模型加载状态与 GPU 资源信息。

    Args:
        request: FastAPI Request 对象（保留签名兼容性，未直接使用）。

    Returns:
        JSON，字段包含：
        - loaded / engine / voxcpm2_loaded / indextts2_loaded / queue
          （原有字段，100% 向后兼容）
        - model_status / current_engine / vram_used_mb / persona_count /
          lora_enabled（新增字段，老前端忽略未知 key）
    """
    voxcpm2_loaded = registry.voxcpm_model is not None
    indextts2_loaded = registry.indextts2_engine is not None
    loaded = voxcpm2_loaded or indextts2_loaded
    current_engine = registry.current_engine or ""

    try:
        persona_count: int = int(get_total_persona_count())
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取 persona_count 失败: %s", exc)
        persona_count = 0

    # Why vram_used_mb 而非百分比：
    #   前端可视化需要与任务管理器「NVIDIA GPU」列的 MB 绝对值对齐，
    #   百分比无法直观判断「6GB/12GB/24GB 卡的实际余量。
    vram_used_mb = _get_vram_used_mb()

    payload: dict[str, Any] = {
        "loaded": loaded,
        "engine": current_engine,
        "voxcpm2_loaded": voxcpm2_loaded,
        "indextts2_loaded": indextts2_loaded,
        "queue": _gen_tracker.status_text(),
        "model_status": "loaded" if loaded else "unloaded",
        "current_engine": current_engine,
        "vram_used_mb": vram_used_mb,
        "persona_count": persona_count,
        "lora_enabled": _is_lora_enabled(),
    }
    return JSONResponse(payload)


@router.post("/load", summary="加载模型", description="加载指定 TTS 引擎到 GPU")
async def load_model_endpoint(request: Request, engine: str = Form("voxcpm2")) -> Response:
    """加载指定引擎（voxcpm2 / indextts2）。

    CSRF：
        由 ``CSRFMiddleware`` 校验 ``X-CSRF-Token`` 请求头。
    SSE：
        加载过程中多次写入 ``request.app.state.model_load_state``
        并通过 ``event_bus.notify()`` 广播进度，前端可实时显示加载阶段。

    Args:
        request: FastAPI Request（供中间件使用）。
        engine:  Form 参数，目标引擎名，默认 ``voxcpm2``。

    Returns:
        JSON：``{"status": "ok"|"error", "message": ..., "engine": ...}``。
        加载失败时 status=error，HTTP 状态码仍为 200（前端通过 status 字段判断，
        兼容老逻辑）；遇到显式 ``InsufficientVRAMError`` 等由 error_handler 统一
        转 503。
    """
    MAX_RETRIES = 2
    try:
        loop = asyncio.get_running_loop()
        if engine == "indextts2":
            load_fn = load_indextts2
        elif engine == "indextts20":
            load_fn = load_indextts20
        elif engine == "voxcpm2":
            load_fn = load_voxcpm2
        else:
            # 通用新式引擎（generic_tts_engine 等）：通过 switch_engine 走声明式加载。
            def load_fn() -> Any:
                return switch_engine(engine)

        from .sse import event_bus

        def _notify_load(step: str, status: str = "in_progress", error: str | None = None) -> None:
            request.app.state.model_load_state = {
                "active": True,
                "step": step,
                "status": status,
                "error": error,
                "engine": engine,
            }
            event_bus.notify()

        _notify_load("正在初始化...")

        def _run_load() -> list:
            """在线程池中执行模型加载操作，实时推送进度"""
            results: list = []
            gen = load_fn()
            for status_text, _, _, _ in gen:
                results.append(status_text)
                _notify_load(status_text)
            return results

        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                results = await loop.run_in_executor(None, _run_load)
                if results:
                    last_msg = results[-1]
                    if "失败" in last_msg or "error" in last_msg.lower():
                        logger.error("模型加载失败 (attempt %d/%d): %s", attempt, MAX_RETRIES, last_msg)
                        if attempt < MAX_RETRIES and is_oom_error(RuntimeError(last_msg)):
                            _notify_load("显存不足，正在清理后重试...")
                            free_gpu_memory()
                            continue
                        _notify_load(last_msg, status="failed", error=last_msg)
                        safe_msg = _SENSITIVE_PATH_PATTERN.sub("[PATH]", last_msg)
                        return JSONResponse({"status": "error", "message": safe_msg, "engine": engine})
                    _notify_load(last_msg, status="completed")
                    return JSONResponse({"status": "ok", "message": last_msg, "engine": engine})
                _notify_load("加载失败：无状态返回", status="failed", error="no status")
                return JSONResponse({"status": "error", "message": "Model load returned no status"})
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if is_oom_error(exc):
                    logger.warning(
                        "Model load attempt %d/%d failed due to OOM: %s. Retrying...",
                        attempt,
                        MAX_RETRIES,
                        exc,
                        exc_info=True,
                    )
                    if attempt < MAX_RETRIES:
                        _notify_load(f"显存不足，正在清理后重试 ({attempt}/{MAX_RETRIES})...")
                    free_gpu_memory()
                    continue
                if isinstance(exc, ImportError):
                    _notify_load("加载失败：模型文件缺失", status="failed", error=str(exc))
                    raise ModelLoadError(f"模型文件缺失: {_safe_error_message(exc)}") from exc
                if isinstance(exc, RuntimeError) and is_oom_error(exc):
                    _notify_load("显存不足，加载失败", status="failed", error=str(exc))
                    raise InsufficientVRAMError(str(exc)) from exc
                if isinstance(exc, (InsufficientVRAMError, ModelLoadError, TTSError)):
                    _notify_load(f"加载失败：{exc}", status="failed", error=str(exc))
                    raise
                _notify_load(f"加载异常：{exc}", status="failed", error=str(exc))
                raise GenerationError(f"模型加载异常: {_safe_error_message(exc)}") from exc

        safe_error = _safe_error_message(last_error) if last_error else "unknown error"
        logger.error("模型加载在 %d 次重试后失败: %s", MAX_RETRIES, last_error, exc_info=True)
        _notify_load(f"加载失败（重试{MAX_RETRIES}次后）", status="failed", error=safe_error)
        return JSONResponse(
            {"status": "error", "message": f"Model load failed after {MAX_RETRIES} retry attempts (OOM): {safe_error}"}
        )
    except (InsufficientVRAMError, ModelLoadError, TTSError):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("模型加载失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": _safe_error_message(exc)})


@router.post("/unload", summary="卸载模型", description="从 GPU 卸载当前模型，释放显存")
async def unload_model_endpoint(request: Request) -> Response:
    """卸载当前引擎，释放 GPU 显存。

    CSRF：
        由 ``CSRFMiddleware`` 校验 ``X-CSRF-Token`` 请求头。

    Args:
        request: FastAPI Request。

    Returns:
        JSON：``{"status": "ok"|"error", "message": ...}``。
    """
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, unload_model)
        return JSONResponse({"status": "ok", "message": "Model unloaded, VRAM released"})
    except TTSError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("模型卸载失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": _safe_error_message(exc)})


@router.post("/preload", summary="预加载模型", description="后台触发预加载模型到 GPU")
async def preload_model_endpoint(request: Request) -> Response:
    """Fire-and-forget 触发后台模型预加载。

    CSRF：
        由 ``CSRFMiddleware`` 校验 ``X-CSRF-Token`` 请求头。

    Args:
        request: FastAPI Request（从 Body JSON 读取 engine / size）。

    Returns:
        JSON：``{"status": "ok"|"error", "message": ...}``。
    """
    try:
        body = await request.json()
        engine = body.get("engine", "voxcpm2")
        size = body.get("size", "voxcpm2")
        preload_model(engine, size)
        return JSONResponse(
            {
                "status": "ok",
                "message": f"Preload started for {engine} ({size})",
            }
        )
    except TTSError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("模型预加载失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": _safe_error_message(exc)})


@router.get("/preload/status", summary="预加载状态", description="查询模型预加载进度")
async def preload_status_endpoint() -> Response:
    """查询当前预加载任务状态。

    Returns:
        JSON：``{"status": "ok", "preload": {...}}``。
    """
    try:
        status = get_preload_status()
        return JSONResponse({"status": "ok", "preload": status})
    except Exception as exc:  # noqa: BLE001
        logger.error("预加载状态查询失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": _safe_error_message(exc)})


@router.post("/switch", summary="切换引擎", description="切换当前激活的 TTS 引擎")
async def switch_engine_endpoint(request: Request, engine: str = Form(...)) -> Response:
    """切换当前激活的 TTS 引擎（voxcpm2 / indextts2）。

    CSRF：
        由 ``CSRFMiddleware`` 校验 ``X-CSRF-Token`` 请求头。
    SSE：
        切换过程中多次写入 ``request.app.state.engine_switch_state``
        并通过 ``event_bus.notify()`` 广播进度。

    Args:
        request: FastAPI Request。
        engine:  Form 参数，目标引擎名。

    Returns:
        JSON：成功时 status=ok + message + engine；失败时 status=error +
        rolled_back=True + 回滚后的 engine。
    """
    prev_engine = registry.current_engine
    try:
        request.app.state.engine_switch_state = {
            "active": True,
            "step": "开始切换引擎...",
            "status": "in_progress",
            "error": None,
            "engine": engine,
        }
        from .sse import event_bus

        event_bus.notify()

        def _run_switch() -> str:
            results: list = []
            for status_text, _, _, _ in switch_engine(engine):
                request.app.state.engine_switch_state = {
                    "active": True,
                    "step": status_text,
                    "status": "in_progress",
                    "error": None,
                    "engine": engine,
                }
                event_bus.notify()
                results.append(status_text)
            return results[-1] if results else "done"

        loop = asyncio.get_running_loop()
        final_status = await loop.run_in_executor(None, _run_switch)

        request.app.state.engine_switch_state = {
            "active": True,
            "step": final_status,
            "status": "completed",
            "error": None,
            "engine": registry.current_engine,
        }
        event_bus.notify()
        return JSONResponse({"status": "ok", "message": final_status, "engine": registry.current_engine})
    except (EngineSwitchError, InsufficientVRAMError, TTSError):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("引擎切换失败: %s", exc, exc_info=True)
        rolled_back_engine = registry.current_engine if registry.current_engine else prev_engine
        rollback_msg = f"已自动回滚到 {rolled_back_engine} 引擎" if rolled_back_engine else ""

        safe_err = _safe_error_message(exc)
        request.app.state.engine_switch_state = {
            "active": True,
            "step": f"切换失败 - {rollback_msg}",
            "status": "failed",
            "error": safe_err,
            "engine": rolled_back_engine,
        }
        try:
            from .sse import event_bus

            event_bus.notify()
        except Exception as bus_exc:  # noqa: BLE001
            logger.debug("SSE notify 失败: %s", bus_exc)

        error_detail = safe_err + (f"\n\n{rollback_msg}" if rollback_msg else "")
        return JSONResponse(
            {
                "status": "error",
                "message": error_detail,
                "engine": rolled_back_engine,
                "rolled_back": True,
            }
        )


# ---------------------------------------------------------------------------
# LoRA 管理（VoxCPM2 专有）
# ---------------------------------------------------------------------------


def _ensure_voxcpm2_only() -> None:
    """若当前引擎非 VoxCPM2 则抛出 ValidationError（400）。"""
    if registry.current_engine != "voxcpm2":
        raise HTTPException(
            status_code=400,
            detail="当前引擎不支持 LoRA，请切换到 VoxCPM2",
        )


@router.post("/lora/load", summary="加载 LoRA", description="加载 LoRA 权重到当前 VoxCPM2 模型")
async def lora_load_endpoint(request: Request) -> Response:
    """加载 LoRA 适配器（仅 VoxCPM2 专有）。

    CSRF：
        由 ``CSRFMiddleware`` 校验 ``X-CSRF-Token``。

    Args:
        request: FastAPI Request（Body JSON 读取 ``lora_path``）。

    Returns:
        JSON：``{"status": "ok"|"error", "message": ...}``。非 VoxCPM2 时
        返回 400 + ValidationError。
    """
    try:
        _ensure_voxcpm2_only()
        body = await request.json()
        lora_path = body.get("lora_path", "")
        if not lora_path:
            return JSONResponse({"status": "error", "message": "lora_path is required"})
        success = fn_voxcpm_load_lora(lora_path)
        if success:
            return JSONResponse({"status": "ok", "message": f"LoRA loaded: {lora_path}"})
        return JSONResponse({"status": "error", "message": "LoRA load returned False"})
    except HTTPException:
        raise
    except TTSError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("LoRA 加载失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": _safe_error_message(exc)})


@router.post("/lora/unload", summary="卸载 LoRA", description="卸载当前 LoRA 权重")
async def lora_unload_endpoint(request: Request) -> Response:
    """卸载 LoRA 适配器。

    CSRF：由 ``CSRFMiddleware`` 校验 ``X-CSRF-Token``。

    Args:
        request: FastAPI Request。

    Returns:
        JSON。非 VoxCPM2 返回 400。
    """
    try:
        _ensure_voxcpm2_only()
        success = fn_voxcpm_unload_lora()
        if success:
            return JSONResponse({"status": "ok", "message": "LoRA unloaded"})
        return JSONResponse({"status": "error", "message": "LoRA unload returned False"})
    except HTTPException:
        raise
    except TTSError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("LoRA 卸载失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": _safe_error_message(exc)})


@router.post("/lora/toggle", summary="切换 LoRA", description="启用或禁用 LoRA 权重")
async def lora_toggle_endpoint(request: Request) -> Response:
    """启用/禁用 LoRA 适配器。

    CSRF：由 ``CSRFMiddleware`` 校验 ``X-CSRF-Token``。

    Args:
        request: FastAPI Request（Body JSON 读取 ``enabled`` 布尔值）。

    Returns:
        JSON。非 VoxCPM2 返回 400。
    """
    try:
        _ensure_voxcpm2_only()
        body = await request.json()
        enabled = body.get("enabled", False)
        success = fn_voxcpm_set_lora_enabled(enabled)
        status_str = "enabled" if enabled else "disabled"
        if success:
            return JSONResponse({"status": "ok", "message": f"LoRA {status_str}"})
        return JSONResponse({"status": "error", "message": f"LoRA {status_str} failed"})
    except HTTPException:
        raise
    except TTSError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("LoRA 切换失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": _safe_error_message(exc)})


@router.get("/lora/state", summary="LoRA 状态", description="获取当前 LoRA 启用/加载状态")
async def lora_state_endpoint() -> Response:
    """获取当前 LoRA 状态。

    Returns:
        JSON。非 VoxCPM2 时返回 loaded=False + message（兼容老前端）。
    """
    try:
        if registry.current_engine != "voxcpm2":
            return JSONResponse(
                {
                    "status": "ok",
                    "state": {"loaded": False, "message": "LoRA is only available for VoxCPM2 engine"},
                }
            )
        state = fn_voxcpm_get_lora_state()
        return JSONResponse({"status": "ok", "state": state})
    except Exception as exc:  # noqa: BLE001
        logger.error("LoRA 状态查询失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": _safe_error_message(exc)})


@router.get("/lora/list", summary="LoRA 列表", description="列出可用的 LoRA 检查点")
async def lora_list_endpoint() -> Response:
    """列出 ``LORA_DIR`` 下可用的 LoRA 检查点。

    Returns:
        JSON：``{"status": "ok", "checkpoints": [{name, path, base_model, r, lora_alpha}]}``。
    """
    try:
        from ..config import LORA_DIR

        checkpoints: list = []
        if os.path.isdir(LORA_DIR):
            for name in sorted(os.listdir(LORA_DIR)):
                ckpt_dir = os.path.join(LORA_DIR, name)
                if not os.path.isdir(ckpt_dir):
                    continue
                info: dict[str, Any] = {"name": name, "path": ckpt_dir}
                config_path = os.path.join(ckpt_dir, "adapter_config.json")
                if os.path.isfile(config_path):
                    try:
                        with open(config_path, encoding="utf-8") as f:
                            cfg = json.load(f)
                        info["base_model"] = cfg.get("base_model_name_or_path", "")
                        info["r"] = cfg.get("r", "")
                        info["lora_alpha"] = cfg.get("lora_alpha", "")
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("LoRA adapter_config.json 解析失败 (%s): %s", config_path, exc)
                checkpoints.append(info)
        return JSONResponse({"status": "ok", "checkpoints": checkpoints})
    except Exception as exc:  # noqa: BLE001
        logger.error("LoRA 列表查询失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": _safe_error_message(exc)})


@router.get("/download_hints", summary="模型下载提示", description="返回缺失模型的下载命令与链接")
async def model_download_hints() -> Response:
    """返回缺失模型的下载提示（引擎列表 + 命令 + URL）。

    Returns:
        JSON：``{"status": "ok", "all_models_available": bool, "hints": [...]}``。
    """
    try:
        from ..config import get_download_hints

        hints = get_download_hints()
        all_ok = len(hints) == 0
        return JSONResponse(
            {
                "status": "ok",
                "all_models_available": all_ok,
                "hints": hints,
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("获取下载提示失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": "获取下载提示失败"}, status_code=500)
