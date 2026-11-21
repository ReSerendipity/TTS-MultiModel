#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI → service_layer 适配层（ADR-0005 P0）。

把现有 CLI 的 argparse 参数映射到统一服务层（service_layer）调用，返回
``cli_output.Result``。本模块是"架构重构先行"的核心：design/clone 不再
直调 vendor VoxCPM，而是走 ``get_generation_service()``，从而：

    - ``--engine`` 真正生效（voxcpm2 / indextts2）；
    - 自动获得 registry / 内容安全 / 版本记录 / 统一结果结构；
    - 输出可直接 JSON 化，供 AI agent 解析。

显式 ``--model-path``（非默认）时回退到 legacy 路径并给出提示，因为
service_layer 的模型路径由 config.yaml 统一管理，不支持按调用覆盖。
"""

from __future__ import annotations

import os
from typing import Any

from integrated_app.cli_output import Result, log, result_from_generation, warn

# 默认模型路径（与 config.yaml 的 models.voxcpm2.model_path 一致）
_DEFAULT_VOXCPM_PATH = "model/VoxCPM2"


def _setup_offline_env() -> None:
    """设置离线环境，与 start_portable.py 对齐（桌面包/离线场景必需）。"""
    for key in ("TRANSFORMERS_OFFLINE", "HF_HUB_OFFLINE", "MODELSCOPE_OFFLINE"):
        os.environ[key] = "1"
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "True")


def _uses_custom_model_path(args: Any) -> bool:
    """是否显式指定了非默认的本地模型路径（service 不支持覆盖，需回退 legacy）。"""
    mp = getattr(args, "model_path", None)
    if not mp:
        return False
    norm = str(mp).replace("\\", "/").rstrip("/").lower()
    return not norm.endswith(_DEFAULT_VOXCPM_PATH.lower())


def _resolve_engine(args: Any) -> str:
    """解析目标引擎：优先 --engine，其余默认 voxcpm2。

    对非字符串值（如测试中的 Mock）回落默认值，避免把对象当引擎名传入。
    """
    engine = getattr(args, "engine", None)
    if isinstance(engine, str) and engine.strip():
        return engine.strip()
    return "voxcpm2"


def _ensure_engine_ready(engine: str) -> Result | None:
    """确保目标引擎已加载；失败时返回 Result.failure，成功返回 None。"""
    from integrated_app.service_layer import get_model_service

    ms = get_model_service()
    status = ms.get_model_status()
    # ModelStatus.ready 才是真正可用
    if status.ready and (not status.engine or status.engine == engine):
        log(f"引擎 {status.engine} 已就绪，复用")
        return None

    log(f"加载引擎 {engine} ...")
    lr = ms.load_model(engine)
    if not lr.success:
        return Result.failure(
            f"模型加载失败: {lr.message}",
            error_code="NOT_READY",
            data={"engine": engine},
        )
    return None


def run_design(args: Any, parser: Any) -> Result:
    """design 走统一服务层。"""
    from integrated_app.service_layer import get_generation_service

    _setup_offline_env()
    engine = _resolve_engine(args)
    if _uses_custom_model_path(args):
        warn("检测到自定义 --model-path，service 路径忽略该参数；如需完全自定义请去掉 --json 使用 legacy 路径")
    ready = _ensure_engine_ready(engine)
    if ready is not None:
        ready.command = "design"
        return ready

    svc = get_generation_service()
    # 注意：control 作为独立 instruction 传（正确语义），不再拼进 text
    gen = svc.generate_voice_design(
        text=args.text,
        instruction=getattr(args, "control", None) or "",
        cfg_value=getattr(args, "cfg_value", 2.0),
        inference_timesteps=getattr(args, "inference_timesteps", 10),
        denoise=getattr(args, "denoise", False),
        normalize=getattr(args, "normalize", False),
    )
    result = result_from_generation(gen, command="design")
    # service 自行决定输出文件名；若用户指定 -o，复制到目标路径
    output = getattr(args, "output", None)
    if output:
        result = _copy_to_requested_output(result, output)
    return result


def run_clone(args: Any, parser: Any) -> Result:
    """clone 走统一服务层（零样本克隆；prompt 续写暂回退 legacy）。"""
    from integrated_app.service_layer import get_generation_service

    _setup_offline_env()
    engine = _resolve_engine(args)
    if _uses_custom_model_path(args):
        warn("检测到自定义 --model-path，service 路径忽略该参数")
    ready = _ensure_engine_ready(engine)
    if ready is not None:
        ready.command = "clone"
        return ready

    svc = get_generation_service()
    # prompt 续写模式（--prompt-audio）service_layer.generate_voice_clone 不支持
    # prompt_wav；走 fn_voxcpm_prompt_continue 需要 registry 已就绪，此处先告警并
    # 以 reference_audio 路径调用，prompt 续写留给 P1 的 generate prompt-continue。
    if getattr(args, "prompt_audio", None) and not getattr(args, "reference_audio", None):
        warn("service 路径暂不支持 prompt 续写（--prompt-audio），本次按普通克隆处理；完整支持见 P1")

    gen = svc.generate_voice_clone(
        text=args.text,
        reference_audio=getattr(args, "reference_audio", None),
        instruction=getattr(args, "control", None) or "",
        cfg_value=getattr(args, "cfg_value", 2.0),
        inference_timesteps=getattr(args, "inference_timesteps", 10),
        denoise=getattr(args, "denoise", False),
        normalize=getattr(args, "normalize", False),
    )
    result = result_from_generation(gen, command="clone")
    output = getattr(args, "output", None)
    if output:
        result = _copy_to_requested_output(result, output)
    return result


def _copy_to_requested_output(result: Result, output: str) -> Result:
    """把 service 生成的音频复制到用户 -o 指定路径，并更新 data.audio_path。"""
    import shutil
    from pathlib import Path

    if not result.ok:
        return result
    src = result.data.get("audio_path")
    if not src or not os.path.isfile(src):
        warn(f"service 返回的音频路径不存在，跳过 -o 复制: {src}")
        return result
    dst = Path(output)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, str(dst))
        result.data["audio_path"] = str(dst.resolve())
        result.data["service_audio_path"] = src
    except Exception as e:
        warn(f"复制音频到 -o 目标失败: {e}")
    return result
