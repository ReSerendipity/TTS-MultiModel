"""LoRA 微调训练管理路由模块。

架构说明：
    本模块提供 VoxCPM2 模型 LoRA 微调训练的 REST API（前缀 ``/api/training``），
    对应 Training Tab 的训练启动、停止、日志实时查看功能。训练状态信息
    （是否运行中）通过 ``/log`` 端点响应中的 ``running`` 字段一并返回，
    无需单独的 status 端点，减少前端轮询请求数。

    接口清单：
    - POST /api/training/start  -> 创建训练任务，以 asyncio 子进程异步启动训练脚本
    - POST /api/training/stop   -> 终止当前训练子进程（先 SIGTERM 优雅终止，超时 SIGKILL）
    - GET  /api/training/log    -> 获取训练日志（实时追加的 stdout 缓存，含 running 状态）

    训练脚本：``scripts/train_voxcpm_finetune.py``（通过 asyncio subprocess 执行，
    stdout/stderr 合并重定向到 PIPE 实时读取）

路径前缀：
    ``/api/training``（通过 ``APIRouter(prefix="/api/training")`` 注册）

权限 / CSRF：
    POST 端点（/start、/stop）为 state-changing 请求，由 ``CSRFMiddleware``
    统一校验 ``X-CSRF-Token`` 头；GET /log 为只读端点无需 CSRF。

并发约束：
    同一时刻只允许运行一个训练任务（通过全局 ``_training_process`` 单例 +
    ``_is_training_running()`` 判断），重复启动返回 409 Conflict。
"""

import asyncio
import contextlib
import json
import logging
import os
import sys
from typing import Any

import aiofiles
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

try:
    import yaml
except ImportError:
    yaml = None

logger = logging.getLogger("tts_multimodel.training")
router = APIRouter(prefix="/api/training", tags=["training"])

_training_process: asyncio.subprocess.Process | None = None
_training_reader_task: asyncio.Task | None = None
_training_log: str = ""
_training_log_lock = asyncio.Lock()
_MAX_LOG_LENGTH = 1_000_000

# R7: Why rank 默认 8、alpha 默认 16（2×）
# LoRA 论文（Hu et al. 2021）与 Aghajanyan 等人的经验公式表明 alpha/rank=2
# 是最稳定的权重缩放因子，是训练收敛速度与模型泛化能力 tradeoff 的经验最优解。
DEFAULT_LORA_RANK: int = 8
DEFAULT_LORA_ALPHA: float = 16.0


def _validate_path(base_dir: str, user_path: str) -> str:
    """校验 user_path 解析后仍位于 base_dir 之内，防止路径穿越。

    Args:
        base_dir: 允许的根目录（真实路径）。
        user_path: 用户输入的相对路径。

    Returns:
        解析后的绝对真实路径。

    Raises:
        ValueError: 检测到路径遍历尝试时抛出。
    """
    joined = os.path.realpath(os.path.join(base_dir, user_path))
    base = os.path.realpath(base_dir)
    if not joined.startswith(base + os.sep) and joined != base:
        raise ValueError(f"Path traversal detected: {user_path}")
    return joined


async def _detect_sample_rate(pretrained_path: str) -> int:
    """从预训练模型 config.json 自动检测 sample_rate。

    Args:
        pretrained_path: 预训练模型根目录路径。

    Returns:
        检测到的采样率（Hz），失败时回退 44100。
    """
    config_file = os.path.join(pretrained_path, "config.json")
    if not os.path.isfile(config_file):
        logger.warning(f"在 {config_file} 未找到 config.json，使用默认 sample_rate=44100")
        return 44100
    try:
        async with aiofiles.open(config_file, encoding="utf-8") as f:
            content: str = await f.read()
            cfg: dict[str, Any] = json.loads(content)
        sr: int = int(cfg["audio_vae_config"]["sample_rate"])
        logger.info(f"自动检测 sample_rate={sr} 来自 {config_file}")
        return sr
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        logger.warning(f"从 {config_file} 检测 sample_rate 失败: {e}，使用默认值 44100")
        return 44100


async def _detect_out_sample_rate(pretrained_path: str) -> int:
    """从预训练模型 config.json 检测 out_sample_rate。

    Args:
        pretrained_path: 预训练模型根目录路径。

    Returns:
        检测到的输出采样率；未配置或失败时返回 0。
    """
    config_file = os.path.join(pretrained_path, "config.json")
    if not os.path.isfile(config_file):
        return 0
    try:
        async with aiofiles.open(config_file, encoding="utf-8") as f:
            content: str = await f.read()
            cfg: dict[str, Any] = json.loads(content)
        out_sr = cfg.get("audio_vae_config", {}).get("out_sample_rate")
        return int(out_sr) if out_sr else 0
    except (KeyError, ValueError, json.JSONDecodeError):
        return 0


def _validate_training_params(body: dict[str, Any]) -> list[str]:
    """校验训练接口的数值参数范围。

    Args:
        body: 请求 JSON body 字典。

    Returns:
        错误信息列表；全部校验通过时返回空列表。
    """
    errors: list[str] = []

    _RANGE_CHECKS: dict[str, tuple[type, bool, float, bool, float, str]] = {
        "learning_rate": (float, True, 0.0, True, 1.0, "> 0 and <= 1.0"),
        "num_iters": (int, False, 1, True, 100000, ">= 1 and <= 100000"),
        "batch_size": (int, False, 1, True, 32, ">= 1 and <= 32"),
        "grad_accum_steps": (int, False, 1, True, 64, ">= 1 and <= 64"),
        "save_interval": (int, False, 100, True, 50000, ">= 100 and <= 50000"),
        "log_interval": (int, False, 1, True, 1000, ">= 1 and <= 1000"),
        "weight_decay": (float, False, 0.0, True, 1.0, ">= 0 and <= 1.0"),
        "warmup_steps": (int, False, 0, True, 10000, ">= 0 and <= 10000"),
        "max_grad_norm": (float, True, 0.0, True, 100.0, "> 0 and <= 100.0"),
        "num_workers": (int, False, 0, True, 16, ">= 0 and <= 16"),
        "valid_interval": (int, False, 100, True, 50000, ">= 100 and <= 50000"),
    }

    for param, (typ, lo_strict, lo, hi_strict, hi, desc) in _RANGE_CHECKS.items():
        if param in body:
            try:
                val = typ(body[param])
            except (ValueError, TypeError):
                errors.append(f"{param} must be {typ.__name__}, got {body[param]!r}")
                continue
            lo_ok = val > lo if lo_strict else val >= lo
            hi_ok = val <= hi if hi_strict else val < hi
            if not (lo_ok and hi_ok):
                errors.append(f"{param} must be {desc}, got {val}")

    if "max_steps" in body:
        try:
            val = int(body["max_steps"])
            if not (0 <= val <= 100000):
                errors.append(f"max_steps must be >= 0 and <= 100000, got {val}")
        except (ValueError, TypeError):
            errors.append(f"max_steps must be int, got {body['max_steps']!r}")

    return errors


def _is_training_running() -> bool:
    """判断当前是否存在运行中的训练子进程。

    Returns:
        True 表示训练仍在运行（returncode 为 None）。
    """
    return _training_process is not None and _training_process.returncode is None


async def _read_training_output(process: asyncio.subprocess.Process) -> None:
    """异步读取训练子进程的 stdout，追加到全局日志缓存。

    Args:
        process: 已启动的 asyncio 子进程对象（需 stdout=PIPE）。
    """
    global _training_log
    if process.stdout is None:
        return
    try:
        while True:
            line: bytes = await process.stdout.readline()
            if not line:
                break
            decoded: str = line.decode("utf-8", errors="replace")
            async with _training_log_lock:
                _training_log += decoded
                if len(_training_log) > _MAX_LOG_LENGTH:
                    _training_log = _training_log[-_MAX_LOG_LENGTH:]
    except (asyncio.CancelledError, OSError, ValueError) as read_err:
        async with _training_log_lock:
            _training_log += f"\nLog read error: {read_err}\n"


@router.post("/start", summary="开始训练", description="启动 VoxCPM2 LoRA 微调训练子进程")
async def start_training(request: Request) -> JSONResponse:
    """启动 LoRA 微调训练。

    训练执行模型（Why 注释）：
        训练启动后以独立 asyncio 子进程运行（不占用 FastAPI 请求线程）。
        原因：LoRA 微调 100 epoch 约 1-2 小时，HTTP 请求 timeout 默认 60s
        不允许同步等待；子进程方式保证请求快速返回，应用重启时子进程自动退出。

    Args:
        request: FastAPI 请求对象，body JSON 字段：
            - pretrained_path: 预训练模型路径
            - train_manifest: 训练数据清单文件路径（必选）
            - val_manifest: 验证数据清单路径（可选）
            - save_path: LoRA 权重保存目录
            - learning_rate / num_iters / batch_size / grad_accum_steps 等训练参数
            - lora: LoRA 配置字典（rank、alpha 等）

    Returns:
        JSONResponse:
            成功: {"status": "ok", "process_id": int} HTTP 200
            失败: {"status": "error", "message": str, "errors": List[str]} HTTP 4xx

    Raises:
        TTSValidationError: 参数校验失败。
        TrainingError: 训练脚本不存在或子进程启动失败。
    """
    global _training_process, _training_reader_task, _training_log

    if _is_training_running():
        return JSONResponse(
            {"status": "error", "message": "已有训练任务进行中"},
            status_code=409,
        )

    try:
        body: dict[str, Any] = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as json_err:
        return JSONResponse(
            {"status": "error", "message": f"Invalid JSON: {json_err}"},
            status_code=400,
        )

    validation_errors: list[str] = _validate_training_params(body)
    if validation_errors:
        return JSONResponse(
            {"status": "error", "message": "Parameter validation failed", "errors": validation_errors},
            status_code=422,
        )

    pretrained_path: str = body.get("pretrained_path", "pretrained_models/VoxCPM2")
    train_manifest: str = body.get("train_manifest", "")
    val_manifest: str = body.get("val_manifest", "")
    save_path: str = body.get("save_path", "lora/my_lora")
    learning_rate: float = float(body.get("learning_rate", 1e-4))
    num_iters: int = int(body.get("num_iters", 2000))
    batch_size: int = int(body.get("batch_size", 1))
    grad_accum_steps: int = int(body.get("grad_accum_steps", 1))
    save_interval: int = int(body.get("save_interval", 1000))
    log_interval: int = int(body.get("log_interval", 10))
    lora_config: dict[str, Any] = body.get("lora", {})
    weight_decay: float = float(body.get("weight_decay", 0.01))
    warmup_steps: int = int(body.get("warmup_steps", 100))
    max_grad_norm: float = float(body.get("max_grad_norm", 1.0))
    num_workers: int = int(body.get("num_workers", 2))
    valid_interval: int = int(body.get("valid_interval", 1000))
    lambdas: dict[str, float] = body.get("lambdas", {"loss/diff": 1.0, "loss/stop": 1.0})

    if not train_manifest:
        return JSONResponse(
            {"status": "error", "message": "train_manifest is required"},
            status_code=400,
        )

    project_root: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    train_script: str = os.path.join(project_root, "scripts", "train_voxcpm_finetune.py")

    if not os.path.isfile(train_script):
        return JSONResponse(
            {"status": "error", "message": f"Training script not found: {train_script}"},
            status_code=400,
        )

    pretrained_dir: str = os.path.join(project_root, "pretrained_models")
    try:
        pretrained_path = _validate_path(pretrained_dir, pretrained_path)
    except ValueError as path_err:
        return JSONResponse({"status": "error", "message": str(path_err)}, status_code=400)

    try:
        train_manifest = _validate_path(project_root, train_manifest)
    except ValueError as path_err:
        return JSONResponse({"status": "error", "message": str(path_err)}, status_code=400)

    try:
        save_path = _validate_path(project_root, save_path)
    except ValueError as path_err:
        return JSONResponse({"status": "error", "message": str(path_err)}, status_code=400)

    sample_rate: int = await _detect_sample_rate(pretrained_path)
    out_sample_rate: int = await _detect_out_sample_rate(pretrained_path)

    user_sr_raw = body.get("sample_rate")
    if user_sr_raw is not None:
        user_sr: int = int(user_sr_raw)
        if user_sr != sample_rate:
            logger.warning(f"用户 sample_rate={user_sr} 与自动检测值 {sample_rate} 不同，使用自动检测值")
    else:
        user_sr = sample_rate

    os.makedirs(save_path, exist_ok=True)
    checkpoints_dir: str = os.path.join(save_path, "checkpoints")
    logs_dir: str = os.path.join(save_path, "logs")
    os.makedirs(checkpoints_dir, exist_ok=True)
    os.makedirs(logs_dir, exist_ok=True)

    resolved_max_steps: int = int(body.get("max_steps", 0)) or int(num_iters)

    config: dict[str, Any] = {
        "pretrained_path": pretrained_path,
        "train_manifest": train_manifest,
        "val_manifest": val_manifest if val_manifest else "",
        "sample_rate": int(user_sr),
        "out_sample_rate": int(out_sample_rate),
        "batch_size": int(batch_size),
        "grad_accum_steps": int(grad_accum_steps),
        "num_workers": int(num_workers),
        "num_iters": int(num_iters),
        "log_interval": int(log_interval),
        "valid_interval": int(valid_interval),
        "save_interval": int(save_interval),
        "learning_rate": float(learning_rate),
        "weight_decay": float(weight_decay),
        "warmup_steps": int(warmup_steps),
        "max_steps": resolved_max_steps,
        "max_grad_norm": float(max_grad_norm),
        "save_path": checkpoints_dir,
        "tensorboard": logs_dir,
        "lambdas": lambdas,
    }

    if lora_config:
        config["lora"] = lora_config

    config_path: str = os.path.join(save_path, "train_config.yaml")
    try:
        import yaml as _yaml

        async with aiofiles.open(config_path, "w", encoding="utf-8") as f:
            await f.write(_yaml.dump(config, default_flow_style=False, allow_unicode=True))
    except ImportError:
        async with aiofiles.open(config_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(config, indent=2, ensure_ascii=False))
        logger.warning("未安装 PyYAML，已改为保存 JSON 格式配置")

    cmd: list[str] = [sys.executable, train_script, "--config_path", config_path]

    async with _training_log_lock:
        _training_log = f"Starting training: {' '.join(cmd)}\n"

    try:
        _training_process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=project_root,
            env={**os.environ, "TOKENIZERS_PARALLELISM": "false"},
        )

        _training_reader_task = asyncio.create_task(_read_training_output(_training_process))

        return JSONResponse({"status": "ok", "process_id": _training_process.pid})
    except (OSError, RuntimeError, ValueError) as spawn_err:
        logger.error(f"训练启动失败: {spawn_err}")
        _training_process = None
        return JSONResponse(
            {"status": "error", "message": f"训练启动失败，请检查配置和日志: {spawn_err}"},
            status_code=500,
        )


@router.post("/stop", summary="停止训练", description="停止正在进行的训练（先 SIGTERM 优雅终止，超时 SIGKILL）")
async def stop_training() -> JSONResponse:
    """终止当前运行中的训练子进程。

    停止策略（Why 注释）：
        先发送 SIGTERM 让训练循环有机会保存 checkpoint；
        10s 超时未退出再 SIGKILL。不直接 thread.kill（或 subprocess.kill），
        避免权重文件写半损坏。训练循环 step 后每 50 step 建议检查一次 stop 信号。

    Returns:
        JSONResponse: {"status": "ok", "message": str} HTTP 200
    """
    global _training_process, _training_reader_task
    if _is_training_running():
        assert _training_process is not None  # nosec B101 - 内部不变量断言（运行中则进程对象非空），非用户输入校验
        _training_process.terminate()
        try:
            await asyncio.wait_for(_training_process.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            _training_process.kill()
            await _training_process.wait()
        if _training_reader_task is not None:
            _training_reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _training_reader_task
            _training_reader_task = None
        return JSONResponse({"status": "ok", "message": "Training stopped"})
    return JSONResponse({"status": "ok", "message": "No training running"})


@router.get("/log", summary="训练日志", description="获取训练子进程的实时 stdout 日志缓存")
async def get_training_log() -> JSONResponse:
    """获取训练日志缓存。

    Returns:
        JSONResponse:
            {
                "log": str,      日志文本（最近 1MB 截断）
                "running": bool  当前是否仍在训练中
            }
    """
    global _training_process, _training_log
    running: bool = _is_training_running()
    async with _training_log_lock:
        log: str = _training_log
    return JSONResponse({"log": log, "running": running})
