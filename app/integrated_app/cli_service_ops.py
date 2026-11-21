#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI 运维域适配层：模型 / 引擎发现 / 下载管理 / LoRA / 训练（ADR-0005 P3）。

全部直调服务层与纯函数，无需 FastAPI：
    - model：status / load / unload / switch
    - engines：list / info
    - download：catalog / status / start / uninstall（callback 式进度打印）
    - lora：list / load / unload / toggle / state
    - train：start / stop / log（子进程管理，PID 与日志落在 save_path）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from integrated_app.cli_output import Result, log, warn

# ---------------------------------------------------------------------------
# 模型管理
# ---------------------------------------------------------------------------


def cmd_model_status(args: Any) -> Result:
    """当前引擎/加载状态/显存占用。"""
    from integrated_app.service_layer import get_model_service

    status = get_model_service().get_model_status()
    data = status.to_dict() if hasattr(status, "to_dict") else dict(vars(status))
    return Result.success(data=data, command="model-status")


def cmd_model_load(args: Any) -> Result:
    """加载指定引擎（voxcpm2 / indextts2 / indextts20）。"""
    from integrated_app.service_layer import get_model_service

    engine = getattr(args, "engine", "voxcpm2") or "voxcpm2"
    log(f"加载引擎 {engine} ...")
    lr = get_model_service().load_model(engine)
    data = lr.to_dict() if hasattr(lr, "to_dict") else dict(vars(lr))
    if not lr.success:
        return Result.failure(f"加载失败: {lr.message}", "NOT_READY", command="model-load")
    return Result.success(data=data, command="model-load")


def cmd_model_unload(args: Any) -> Result:
    """卸载当前模型，释放显存。"""
    from integrated_app.service_layer import get_model_service

    get_model_service().unload_model()
    return Result.success(data={"message": "已卸载当前模型"}, command="model-unload")


def cmd_model_switch(args: Any) -> Result:
    """切换活跃引擎（5 阶段流程 + 回滚）。"""
    from integrated_app.service_layer import get_model_service

    engine = getattr(args, "engine", "voxcpm2") or "voxcpm2"
    log(f"切换引擎到 {engine} ...")
    sr = get_model_service().switch_engine(engine)
    data = sr.to_dict() if hasattr(sr, "to_dict") else dict(vars(sr))
    if not sr.success:
        return Result.failure(f"切换失败: {sr.message}", "NOT_READY", command="model-switch")
    return Result.success(data=data, command="model-switch")


# ---------------------------------------------------------------------------
# 引擎发现
# ---------------------------------------------------------------------------


def cmd_engines_list(args: Any) -> Result:
    """列出所有注册引擎及其能力声明。"""
    from integrated_app.routes.generate.engine_discovery import list_engines

    return Result.success(data=list_engines(), command="engines-list")


def cmd_engines_info(args: Any) -> Result:
    """查看单个引擎的元数据与能力。"""
    from integrated_app.routes.generate.engine_discovery import engine_info

    engine_id = getattr(args, "engine_id", "")
    if not engine_id:
        return Result.failure("需要 --engine-id", "USAGE_ERROR", command="engines-info")
    try:
        info = engine_info(engine_id)
    except Exception as exc:  # noqa: BLE001 - 未知引擎/内部异常统一转业务失败
        return Result.failure(f"查询引擎失败: {exc}", "BUSINESS_ERROR", command="engines-info")
    # engine_info 在未知引擎时返回 JSONResponse（HTTP 语义），CLI 转成业务失败
    if not isinstance(info, dict):
        detail = getattr(info, "body", info)
        return Result.failure(f"引擎不可用: {engine_id}（{detail}）", "BUSINESS_ERROR", command="engines-info")
    return Result.success(data=info, command="engines-info")


# ---------------------------------------------------------------------------
# 下载管理
# ---------------------------------------------------------------------------


def cmd_download_catalog(args: Any) -> Result:
    """可下载清单：环境包 + 各模型（含是否已就位）。"""
    from integrated_app.download_service import catalog_view

    return Result.success(data=catalog_view(), command="download-catalog")


def cmd_download_status(args: Any) -> Result:
    """环境包与模型的安装状态。"""
    from integrated_app.download_service import build_status

    return Result.success(data=build_status(), command="download-status")


def cmd_download_start(args: Any) -> Result:
    """下载并安装环境包（runtime）或指定模型。

    长任务：同步等待完成，进度逐条打印到 stderr（--json 时仍保持 stdout 纯净）。
    """
    from integrated_app.download_service import install_model, install_runtime, scan_models

    kind = getattr(args, "kind", "") or ""
    target = getattr(args, "target", "") or ""

    if kind not in ("runtime", "model"):
        return Result.failure("--kind 必须是 runtime 或 model", "USAGE_ERROR", command="download-start")

    if kind == "model":
        available = {str(m["name"]) for m in scan_models()}
        if not target:
            return Result.failure("kind=model 时需要 --target", "USAGE_ERROR", command="download-start")
        if target not in available:
            return Result.failure(
                f"未知模型: {target}；可用: {', '.join(sorted(available))}", "USAGE_ERROR", command="download-start"
            )

    last_report: dict[str, Any] = {"stage": "", "pct": -1}

    def on_progress(stage: str, ratio: float, message: str) -> None:
        pct = int(max(0.0, min(1.0, ratio)) * 100)
        # 同一阶段每 5% 打一行，避免刷屏
        if stage != last_report["stage"] or pct >= last_report["pct"] + 5 or pct == 100:
            last_report["stage"], last_report["pct"] = stage, pct
            log(f"[{stage}] {pct}% {message}")

    log(f"开始安装 {kind}{':' + target if target else ''} ...")
    started = time.time()
    if kind == "runtime":
        meta = install_runtime(on_progress=on_progress)
    else:
        meta = install_model(target, on_progress=on_progress)

    elapsed = round(time.time() - started, 1)
    return Result.success(
        data={"kind": kind, "target": target, "elapsed_seconds": elapsed, "meta": meta or {}},
        command="download-start",
    )


def cmd_download_uninstall(args: Any) -> Result:
    """卸载环境包或指定模型（释放磁盘）。"""
    from integrated_app.download_service import uninstall_model, uninstall_runtime

    kind = getattr(args, "kind", "") or ""
    target = getattr(args, "target", "") or ""
    if kind not in ("runtime", "model"):
        return Result.failure("--kind 必须是 runtime 或 model", "USAGE_ERROR", command="download-uninstall")

    if kind == "runtime":
        uninstall_runtime()
        return Result.success(data={"uninstalled": "runtime"}, command="download-uninstall")
    if not target:
        return Result.failure("kind=model 时需要 --target", "USAGE_ERROR", command="download-uninstall")
    uninstall_model(target)
    return Result.success(data={"uninstalled": f"model:{target}"}, command="download-uninstall")


# ---------------------------------------------------------------------------
# LoRA
# ---------------------------------------------------------------------------


def cmd_lora_list(args: Any) -> Result:
    """列出 lora/ 下可用的检查点。"""
    from integrated_app.config import LORA_DIR

    checkpoints: list[dict[str, Any]] = []
    if os.path.isdir(LORA_DIR):
        for name in sorted(os.listdir(LORA_DIR)):
            ckpt = os.path.join(LORA_DIR, name)
            if not os.path.isdir(ckpt):
                continue
            info: dict[str, Any] = {"name": name, "path": os.path.abspath(ckpt)}
            cfg = os.path.join(ckpt, "adapter_config.json")
            if os.path.isfile(cfg):
                try:
                    with open(cfg, encoding="utf-8") as fh:
                        meta = json.load(fh)
                    info.update(
                        {
                            "base_model": meta.get("base_model_name_or_path"),
                            "r": meta.get("r"),
                            "lora_alpha": meta.get("lora_alpha"),
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - 配置损坏不应阻断列表
                    info["config_error"] = str(exc)
            checkpoints.append(info)
    return Result.success(data={"count": len(checkpoints), "checkpoints": checkpoints}, command="lora-list")


def cmd_lora_load(args: Any) -> Result:
    """加载 LoRA 权重（需 VoxCPM2 已加载）。"""
    from integrated_app.engines.voxcpm2.lora import fn_voxcpm_load_lora

    path = getattr(args, "path", "") or ""
    if not path or not os.path.exists(path):
        return Result.failure(f"LoRA 路径不存在: {path}", "USAGE_ERROR", command="lora-load")
    ok = fn_voxcpm_load_lora(path)
    if not ok:
        return Result.failure("LoRA 加载失败（检查权重格式与引擎状态）", "BUSINESS_ERROR", command="lora-load")
    return Result.success(data={"loaded": os.path.abspath(path)}, command="lora-load")


def cmd_lora_unload(args: Any) -> Result:
    """卸载 LoRA，回滚基础权重。"""
    from integrated_app.engines.voxcpm2.lora import fn_voxcpm_unload_lora

    ok = fn_voxcpm_unload_lora()
    if not ok:
        return Result.failure("LoRA 卸载失败", "BUSINESS_ERROR", command="lora-unload")
    return Result.success(data={"unloaded": True}, command="lora-unload")


def cmd_lora_toggle(args: Any) -> Result:
    """启用/禁用当前 LoRA（不卸载权重）。"""
    from integrated_app.engines.voxcpm2.lora import fn_voxcpm_set_lora_enabled

    enabled = getattr(args, "enabled", None)
    if enabled is None:
        return Result.failure("需要 --enable 或 --disable", "USAGE_ERROR", command="lora-toggle")
    ok = fn_voxcpm_set_lora_enabled(bool(enabled))
    if not ok:
        return Result.failure("LoRA 状态切换失败", "BUSINESS_ERROR", command="lora-toggle")
    return Result.success(data={"enabled": bool(enabled)}, command="lora-toggle")


def cmd_lora_state(args: Any) -> Result:
    """当前 LoRA 状态（loaded/enabled/meta）。"""
    from integrated_app.engines.voxcpm2.lora import fn_voxcpm_get_lora_state

    return Result.success(data=fn_voxcpm_get_lora_state(), command="lora-state")


# ---------------------------------------------------------------------------
# 训练（子进程）
# ---------------------------------------------------------------------------

_TRAIN_CONFIG_NAME = "train_config.json"
_TRAIN_PID_NAME = "train.pid"


def _train_paths(save_path: str) -> tuple[str, str, str]:
    """返回 (config 路径, pid 路径, 日志目录)。"""
    return (
        os.path.join(save_path, _TRAIN_CONFIG_NAME),
        os.path.join(save_path, _TRAIN_PID_NAME),
        os.path.join(save_path, "logs"),
    )


def cmd_train_start(args: Any) -> Result:
    """启动 LoRA 微调训练子进程（后台运行，PID 与日志落盘）。"""
    from integrated_app.config import PROJECT_ROOT

    save_path = os.path.abspath(getattr(args, "save_path", "") or os.path.join(PROJECT_ROOT, "lora", "finetune"))
    os.makedirs(save_path, exist_ok=True)
    os.makedirs(os.path.join(save_path, "checkpoints"), exist_ok=True)
    logs_dir = os.path.join(save_path, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    config_path, pid_path, _ = _train_paths(save_path)
    if os.path.isfile(pid_path):
        try:
            old_pid = int(Path(pid_path).read_text(encoding="utf-8").strip())
        except Exception:
            old_pid = 0
        if old_pid and _pid_alive(old_pid):
            return Result.failure(
                f"已有训练在运行（PID {old_pid}）；如需重启请先 train stop", "BUSINESS_ERROR", command="train-start"
            )

    cfg: dict[str, Any] = {
        "pretrained_path": os.path.abspath(
            getattr(args, "pretrained_path", "") or os.path.join(PROJECT_ROOT, "model", "VoxCPM2")
        ),
        "train_manifest": os.path.abspath(getattr(args, "train_manifest", "") or ""),
        "val_manifest": os.path.abspath(getattr(args, "val_manifest", "") or ""),
        "save_path": save_path,
        "sample_rate": int(getattr(args, "sample_rate", 48000) or 48000),
        "out_sample_rate": int(getattr(args, "sample_rate", 48000) or 48000),
        "batch_size": int(getattr(args, "batch_size", 1) or 1),
        "grad_accum_steps": int(getattr(args, "grad_accum", 1) or 1),
        "num_workers": int(getattr(args, "num_workers", 0) or 0),
        "num_iters": int(getattr(args, "num_iters", 1000) or 1000),
        "log_interval": int(getattr(args, "log_interval", 10) or 10),
        "valid_interval": int(getattr(args, "valid_interval", 200) or 200),
        "save_interval": int(getattr(args, "save_interval", 200) or 200),
        "learning_rate": float(getattr(args, "learning_rate", 1e-4) or 1e-4),
        "weight_decay": float(getattr(args, "weight_decay", 0.01) or 0.01),
    }
    if not cfg["train_manifest"]:
        return Result.failure("需要 --train-manifest（训练清单文件）", "USAGE_ERROR", command="train-start")
    if not os.path.isfile(cfg["train_manifest"]):
        return Result.failure(f"训练清单不存在: {cfg['train_manifest']}", "USAGE_ERROR", command="train-start")

    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)

    train_script = os.path.join(PROJECT_ROOT, "scripts", "train_voxcpm_finetune.py")
    if not os.path.isfile(train_script):
        return Result.failure("训练脚本不存在: scripts/train_voxcpm_finetune.py", "NOT_READY", command="train-start")

    log_path = os.path.join(logs_dir, f"train_{int(time.time())}.log")
    log_file = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 - 交给子进程持有
    try:
        proc = subprocess.Popen(  # noqa: S603 - 固定脚本 + 固定参数，无 shell
            [sys.executable, train_script, "--config_path", config_path],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=PROJECT_ROOT,
        )
    except Exception as exc:  # noqa: BLE001 - 启动失败统一转业务失败
        log_file.close()
        return Result.failure(f"训练进程启动失败: {exc}", "BUSINESS_ERROR", command="train-start")

    with open(pid_path, "w", encoding="utf-8") as fh:
        fh.write(str(proc.pid))

    return Result.success(
        data={
            "pid": proc.pid,
            "config_path": config_path,
            "log_path": log_path,
            "save_path": save_path,
            "message": "训练已在后台启动；用 `train log` 查看进度，`train stop` 终止",
        },
        command="train-start",
    )


def _pid_alive(pid: int) -> bool:
    """判断 PID 是否存活（跨平台，Windows 用 OpenProcess 语义由 os.kill(0) 替代）。"""
    try:
        if os.name == "nt":
            # Windows 上 os.kill(pid, 0) 会抛 OSError；用 tasklist 的轻量替代：
            # ctypes 检查进程句柄更可靠，但为免引入新依赖，用 psutil（已是项目依赖）
            import psutil

            return psutil.pid_exists(pid)
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _resolve_save_path(args: Any) -> str:
    """解析训练输出目录。

    注意不能用 ``os.path.abspath(getattr(..., "") or "")``：空串会被解析成
    当前工作目录，导致误读 <cwd>/logs 下的应用日志。必须先判 None/空再取默认。
    """
    raw = getattr(args, "save_path", None)
    if raw:
        return os.path.abspath(str(raw))
    from integrated_app.config import PROJECT_ROOT

    return os.path.join(PROJECT_ROOT, "lora", "finetune")


def cmd_train_stop(args: Any) -> Result:
    """终止训练子进程（SIGTERM，超时后强杀）。"""
    save_path = _resolve_save_path(args)
    _, pid_path, _ = _train_paths(save_path)
    if not os.path.isfile(pid_path):
        return Result.failure(f"未找到 PID 文件: {pid_path}", "BUSINESS_ERROR", command="train-stop")
    try:
        pid = int(Path(pid_path).read_text(encoding="utf-8").strip())
    except Exception:
        return Result.failure(f"PID 文件损坏: {pid_path}", "BUSINESS_ERROR", command="train-stop")

    if not _pid_alive(pid):
        os.remove(pid_path)
        return Result.success(data={"pid": pid, "message": "进程已不存在，已清理 PID 文件"}, command="train-stop")

    try:
        import psutil

        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except psutil.TimeoutExpired:
            warn("训练进程 10s 未退出，强制终止")
            proc.kill()
        os.remove(pid_path)
    except Exception as exc:  # noqa: BLE001
        return Result.failure(f"终止训练失败: {exc}", "BUSINESS_ERROR", command="train-stop")

    return Result.success(data={"pid": pid, "stopped": True}, command="train-stop")


def cmd_train_log(args: Any) -> Result:
    """读取训练日志尾部。"""
    save_path = _resolve_save_path(args)
    _, pid_path, logs_dir = _train_paths(save_path)
    if not os.path.isdir(logs_dir):
        return Result.failure(f"日志目录不存在: {logs_dir}", "BUSINESS_ERROR", command="train-log")

    logs = sorted(
        (os.path.join(logs_dir, n) for n in os.listdir(logs_dir) if n.endswith(".log")),
        key=os.path.getmtime,
    )
    if not logs:
        return Result.failure("暂无训练日志", "BUSINESS_ERROR", command="train-log")

    latest = logs[-1]
    tail_n = int(getattr(args, "tail", 80) or 80)
    with open(latest, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    tail_lines = [line.rstrip("\n") for line in lines[-tail_n:]]

    running = False
    if os.path.isfile(pid_path):
        try:
            running = _pid_alive(int(Path(pid_path).read_text(encoding="utf-8").strip()))
        except Exception:
            running = False

    return Result.success(
        data={
            "log_path": os.path.abspath(latest),
            "running": running,
            "total_lines": len(lines),
            "lines": tail_lines,
        },
        command="train-log",
    )
