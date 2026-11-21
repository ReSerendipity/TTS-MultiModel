#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI → 各引擎适配层（ADR-0005 P1）。

承接 ``cli_service.py`` 的 VoxCPM2 design/clone，补齐其余动作：

    - VoxCPM2：ultimate（极致克隆）/ prompt-continue（音频续写）/ script（剧本工坊）
    - IndexTTS2：emotion（情感合成，8 维向量 / 情感音频 / 情感文本）
    - Breeze TTS 2：design / clone / direction
    - Edge-TTS：在线兜底合成
    - Voicebox：音色转换（音频→音频）
    - Step-Audio-EditX：音频编辑（情绪/风格/副语言/语速）
    - 后处理：变速 / 响度 / 人声增强 / 静音裁剪（纯函数）

所有入口返回 ``cli_output.Result``，供 ``emit()`` 统一渲染。重型依赖一律
惰性导入，``--help`` 不应触发模型加载。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from integrated_app.cli_output import Result, log
from integrated_app.cli_service import (
    _copy_to_requested_output,
    _ensure_engine_ready,
    _setup_offline_env,
)

# ---------------------------------------------------------------------------
# 通用辅助
# ---------------------------------------------------------------------------


def _save_dir() -> str:
    """返回项目输出目录（引擎落盘默认位置）。"""
    from integrated_app.config import SAVE_DIR

    return SAVE_DIR


def _default_output(prefix: str, ext: str = "wav") -> str:
    """生成 outputs/ 下的默认输出路径。

    部分引擎（IndexTTS2 / Voicebox / Step-Audio）在 output_path=None 时会写到
    系统临时目录，容易被清理、也不利于 agent 稳定引用；CLI 统一改为落到项目
    outputs/ 下。
    """
    target_dir = _save_dir()
    Path(target_dir).mkdir(parents=True, exist_ok=True)
    return os.path.join(target_dir, f"{prefix}_{int(time.time() * 1000)}.{ext}")


def _ensure_voxcpm2() -> Result | None:
    """确保 VoxCPM2 已加载；失败返回 Result.failure，成功返回 None。"""
    return _ensure_engine_ready("voxcpm2")


def _resolve_audio_path(ref: str) -> str:
    """把引擎返回的第三元（文件名或路径）解析成实际路径。

    引擎约定不一致：有的返回纯文件名（需拼 ``SAVE_DIR``），有的返回绝对路径
    或带目录的相对路径。这里统一识别。
    """
    if not ref:
        return ""
    if os.path.isabs(ref) or os.sep in ref or "/" in ref:
        return ref
    return os.path.join(_save_dir(), ref)


def _write_wav_fallback(path: str, audio: Any, sample_rate: int) -> str:
    """当引擎只返回数组、未落盘时，把音频写到目标路径。"""
    import soundfile as sf

    if not path:
        Path(_save_dir()).mkdir(parents=True, exist_ok=True)
        path = os.path.join(_save_dir(), f"cli_{int(time.time() * 1000)}.wav")
    else:
        Path(os.path.dirname(path) or ".").mkdir(parents=True, exist_ok=True)
    sf.write(path, audio, int(sample_rate))
    return path


def _audio_tuple_to_result(
    raw: Any,
    *,
    command: str,
    output: str | None = None,
    engine: str = "",
    params: dict[str, Any] | None = None,
    audio_first: bool = False,
) -> Result:
    """把引擎的 ``((sr|audio)...)`` 元组结果统一转成 Result。

    Args:
        raw: 引擎返回值，约定为 ``(audio_info, message)``。
        command: 命令名，写入结果信封。
        output: 用户 ``-o`` 指定路径；给出时复制过去。
        engine: 引擎名，写入 data。
        params: 实际使用的参数，写入 data。
        audio_first: True 表示元组顺序为 ``(audio, sample_rate, ref)``
            （Breeze 约定）；False 表示 ``(sample_rate, audio, ref)``
            （VoxCPM2 / IndexTTS 约定）。
    """
    try:
        audio_info, message = raw
        if not isinstance(audio_info, (tuple, list)) or len(audio_info) < 3:
            return Result.failure(f"引擎未返回音频: {message}", "BUSINESS_ERROR", command=command)
        if audio_first:
            audio, sample_rate, ref = audio_info[0], int(audio_info[1]), str(audio_info[2] or "")
        else:
            sample_rate, audio, ref = int(audio_info[0]), audio_info[1], str(audio_info[2] or "")
    except Exception as exc:  # noqa: BLE001 - 引擎返回结构不可控，统一转业务失败
        return Result.failure(f"引擎返回格式异常: {exc!r}", "BUSINESS_ERROR", command=command)

    path = _resolve_audio_path(ref)
    duration = 0.0
    if audio is not None:
        try:
            duration = len(audio) / sample_rate if sample_rate else 0.0
        except TypeError:
            duration = 0.0
    if not path or not os.path.isfile(path):
        if audio is None:
            return Result.failure("引擎未产出音频文件", "BUSINESS_ERROR", command=command)
        path = _write_wav_fallback(path, audio, sample_rate)

    data: dict[str, Any] = {
        "audio_path": os.path.abspath(path),
        "message": str(message),
        "duration": round(duration, 3),
        "engine": engine,
        "params": params or {},
    }
    result = Result.success(data=data, command=command)
    if output:
        result = _copy_to_requested_output(result, output)
    return result


def _read_text_file(path: str, parser: Any, label: str) -> str:
    """读取文本文件（剧本/批量输入），失败时抛 parser.error。"""
    from pathlib import Path as _P

    p = _P(path)
    if not p.is_file():
        parser.error(f"{label} 不存在: {path}")
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# VoxCPM2 扩展动作
# ---------------------------------------------------------------------------


def run_ultimate(args: Any, parser: Any) -> Result:
    """VoxCPM2 极致克隆：参考音频 + 其转写文本，音色保真度更高。"""
    from integrated_app.engines.voxcpm2.ultimate import fn_voxcpm_ultimate_clone

    _setup_offline_env()
    ready = _ensure_voxcpm2()
    if ready is not None:
        ready.command = "ultimate"
        return ready

    ref = getattr(args, "reference_audio", None)
    if not ref or not os.path.isfile(ref):
        return Result.failure(f"参考音频不存在: {ref}", "USAGE_ERROR", command="ultimate")

    params = {
        "instruction": getattr(args, "control", "") or "",
        "ref_audio_path": ref,
        "advanced_cfg": getattr(args, "cfg_value", 2.0),
        "advanced_norm": bool(getattr(args, "normalize", False)),
        "advanced_denoise": float(getattr(args, "denoise_strength", 0.5)),
        "advanced_steps": getattr(args, "inference_timesteps", 10),
        "advanced_seed": getattr(args, "seed", -1),
        "ref_text": getattr(args, "ref_text", "") or "",
    }
    log("执行极致克隆 ...")
    raw = fn_voxcpm_ultimate_clone(
        args.text,
        params["instruction"],
        params["ref_audio_path"],
        params["advanced_cfg"],
        params["advanced_norm"],
        params["advanced_denoise"],
        params["advanced_steps"],
        params["advanced_seed"],
        params["ref_text"],
    )
    return _audio_tuple_to_result(
        raw, command="ultimate", output=getattr(args, "output", None), engine="voxcpm2", params=params
    )


def run_prompt_continue(args: Any, parser: Any) -> Result:
    """VoxCPM2 Prompt 续写：给定音频与其文本，延续同一说话人语气继续生成。"""
    from integrated_app.engines.voxcpm2.prompt import fn_voxcpm_prompt_continue

    _setup_offline_env()
    ready = _ensure_voxcpm2()
    if ready is not None:
        ready.command = "prompt-continue"
        return ready

    prompt_wav = getattr(args, "prompt_audio", None)
    prompt_text = getattr(args, "prompt_text", None)
    if not prompt_wav or not os.path.isfile(prompt_wav):
        return Result.failure(f"提示音频不存在: {prompt_wav}", "USAGE_ERROR", command="prompt-continue")
    if not prompt_text:
        prompt_file = getattr(args, "prompt_file", None)
        if prompt_file and os.path.isfile(prompt_file):
            prompt_text = Path(prompt_file).read_text(encoding="utf-8").strip()
        else:
            return Result.failure(
                "续写模式需要 --prompt-text 或 --prompt-file", "USAGE_ERROR", command="prompt-continue"
            )

    params = {"prompt_wav": prompt_wav, "prompt_text": prompt_text}
    log("执行 Prompt 续写 ...")
    raw = fn_voxcpm_prompt_continue(args.text, prompt_wav, prompt_text)
    return _audio_tuple_to_result(
        raw, command="prompt-continue", output=getattr(args, "output", None), engine="voxcpm2", params=params
    )


def run_script(args: Any, parser: Any) -> Result:
    """VoxCPM2 剧本工坊：按 ``[角色]台词`` 解析多角色对话并批量配音。

    角色 → 音色解析顺序（与 Web 一致）：
        1. ``--persona-map ROLE=AUDIO``（本次新增，AI agent 可直接给映射，不依赖音色库）
        2. 音色库 ``personas/`` 中同名角色（由引擎内部 ``get_persona_map`` 提供）
    两者都没有的角色会整行失败，CLI 会在 warnings 中提示缺失角色。
    """
    import re

    from integrated_app.engines.voxcpm2.script import fn_voxcpm_script_studio

    _setup_offline_env()
    ready = _ensure_voxcpm2()
    if ready is not None:
        ready.command = "script"
        return ready

    script_text = _read_text_file(args.input, parser, "剧本文件")

    # --persona-map ROLE=AUDIO 显式映射（可重复）
    persona_map: dict[str, Any] | None = None
    raw_maps = getattr(args, "persona_map", None)
    if raw_maps:
        persona_map = {}
        for item in raw_maps:
            if "=" not in str(item):
                return Result.failure(f"--persona-map 需为 ROLE=AUDIO 形式: {item}", "USAGE_ERROR", command="script")
            role, audio = str(item).split("=", 1)
            role, audio = role.strip(), audio.strip()
            if not role or not os.path.isfile(audio):
                return Result.failure(f"--persona-map 音频不存在: {audio}", "USAGE_ERROR", command="script")
            persona_map[role] = {"wav": audio, "text": ""}

    params = {
        "advanced_cfg": getattr(args, "cfg_value", 2.0),
        "advanced_norm": bool(getattr(args, "normalize", False)),
        "advanced_denoise": float(getattr(args, "denoise_strength", 0.5)),
        "advanced_steps": getattr(args, "inference_timesteps", 10),
        "advanced_seed": getattr(args, "seed", -1),
        "lang": getattr(args, "lang", "中文") or "中文",
        "persona_map": sorted(persona_map) if persona_map else None,
    }
    log(f"执行剧本工坊（{len(script_text)} 字符）...")
    raw = fn_voxcpm_script_studio(
        script_text,
        params["advanced_cfg"],
        params["advanced_norm"],
        params["advanced_denoise"],
        params["advanced_steps"],
        params["advanced_seed"],
        params["lang"],
        persona_map,
    )
    result = _audio_tuple_to_result(
        raw, command="script", output=getattr(args, "output", None), engine="voxcpm2", params=params
    )
    # 部分行失败时给出可操作告警（角色缺音色是常见原因）
    msg = str(result.data.get("message", "")) if result.ok else ""
    m = re.search(r"失败\s*(\d+)\s*段", msg)
    if m and int(m.group(1)) > 0:
        result.warnings.append(
            f"剧本有 {m.group(1)} 段因角色缺少音色而失败；请用 --persona-map 角色=音频 指定，"
            "或先把音色保存到音色库（personas/）"
        )
    return result


# ---------------------------------------------------------------------------
# IndexTTS2 情感合成
# ---------------------------------------------------------------------------


def run_emotion(args: Any, parser: Any) -> Result:
    """IndexTTS 情感合成：参考音色 + 情感文本/音频/8 维向量。

    引擎由 ``--engine`` 决定：``indextts2``（2.5，默认）或 ``indextts20``（2.0）。
    两者共用同一推理代码包与引擎槽位，仅权重目录不同。
    """
    _setup_offline_env()
    engine_name = getattr(args, "engine", None) or "indextts2"
    label = "IndexTTS 2.0" if engine_name == "indextts20" else "IndexTTS 2.5"
    ready = _ensure_engine_ready(engine_name)
    if ready is not None:
        ready.command = "emotion"
        return ready

    from integrated_app.model_registry import registry

    engine = registry.get_current_engine()
    if engine is None:
        return Result.failure(f"{label} 引擎实例不可用", "NOT_READY", command="emotion")

    ref = getattr(args, "reference_audio", None)
    if not ref or not os.path.isfile(ref):
        return Result.failure(f"参考音频不存在: {ref}", "USAGE_ERROR", command="emotion")

    emo_text = getattr(args, "emotion_text", None)
    emo_audio = getattr(args, "emotion_audio", None)
    emo_vector = None
    vector_raw = getattr(args, "emotion_vector", None)
    if vector_raw:
        try:
            emo_vector = [float(x) for x in str(vector_raw).split(",") if x.strip()]
        except ValueError:
            return Result.failure("--emotion-vector 需为逗号分隔的 8 个浮点数", "USAGE_ERROR", command="emotion")
        if len(emo_vector) != 8:
            return Result.failure(
                f"--emotion-vector 需要 8 个值，实际 {len(emo_vector)}", "USAGE_ERROR", command="emotion"
            )

    if not any((emo_text, emo_audio, emo_vector)):
        return Result.failure(
            "情感合成需至少提供 --emotion-text / --emotion-audio / --emotion-vector 之一",
            "USAGE_ERROR",
            command="emotion",
        )

    params = {
        "reference_audio": ref,
        "emotion_text": emo_text,
        "emotion_audio": emo_audio,
        "emotion_vector": emo_vector,
        "emo_alpha": getattr(args, "emotion_alpha", 0.8),
        "duration_factor": getattr(args, "duration_factor", 1.0),
        "lang": getattr(args, "lang", None),
    }
    log(f"执行 {label} 情感合成 ...")
    # 默认落到 outputs/（引擎在 output_path=None 时会写系统临时目录）
    target = getattr(args, "output", None) or _default_output(f"{engine_name}_emotion")
    sample_rate, audio, path = engine.synthesize(
        text=args.text,
        spk_audio_prompt=ref,
        output_path=target,
        emo_audio_prompt=emo_audio,
        emo_vector=emo_vector,
        emo_text=emo_text,
        duration_factor=params["duration_factor"],
        lang=params["lang"],
        emo_alpha=params["emo_alpha"],
        use_emo_text=bool(emo_text),
        seed=getattr(args, "seed", None),
    )
    return _audio_tuple_to_result(
        ((sample_rate, audio, path), f"{label} 情感合成完成"),
        command="emotion",
        engine=engine_name,
        params=params,
    )


# ---------------------------------------------------------------------------
# Breeze TTS 2
# ---------------------------------------------------------------------------


def _load_breeze() -> tuple[Any, Result | None]:
    """惰性实例化并加载 Breeze 引擎。"""
    from integrated_app.engines.breeze_tts2.engine import BreezeTTSEngine

    engine = BreezeTTSEngine()
    if not engine.is_ready():
        log("加载 Breeze TTS 2（首次较慢）...")
        engine.load()
    return engine, None


def _has_cjk(text: str) -> bool:
    """文本是否含 CJK 表意文字（用于 Breeze 的指令-文本语言一致性校验）。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _check_breeze_instruction_language(text: str, instruction: str, command: str) -> Result | None:
    """Breeze 要求 instruction 语言与目标 text 一致，否则会静默产出极短废音频。

    模型卡原文：「Match the instruction language to the target text」。实测中文
    text + 英文 instruction 只出 1.6 秒音频，却仍返回 ok:true——属静默失败。
    此处提前拦截并给出明确错误。clone 模式无 instruction，不校验。

    Returns:
        不匹配时返回 Result.failure（USAGE_ERROR），匹配/无指令时返回 None。
    """
    if not instruction.strip() or not text.strip():
        return None
    if _has_cjk(text) != _has_cjk(instruction):
        text_lang = "中文" if _has_cjk(text) else "英文"
        ins_lang = "中文" if _has_cjk(instruction) else "英文"
        return Result.failure(
            f"Breeze 要求 --control 的语言与 --text 一致：当前 --text 是{text_lang}，"
            f"--control 是{ins_lang}。语言不一致会静默产出极短音频。"
            f"请把 --control 改成{text_lang}描述（例："
            f"{'一位低沉成熟的男声，平稳温暖' if _has_cjk(text) else 'a warm, thoughtful young woman with a clear voice'}）。",
            "USAGE_ERROR",
            command=command,
        )
    return None


def run_breeze(args: Any, parser: Any, mode: str) -> Result:
    """Breeze TTS 2：design / clone / direction 三模式。"""
    _setup_offline_env()

    text = args.text
    instruction = getattr(args, "control", "") or ""
    seed = getattr(args, "seed", -1)
    cfg_scale = getattr(args, "cfg_scale", 4.0)

    # 语言一致性是纯参数校验，放在引擎加载之前（避免白等数十秒加载再失败）
    if mode in ("design", "direction"):
        mismatch = _check_breeze_instruction_language(text, instruction, f"breeze-{mode}")
        if mismatch is not None:
            return mismatch

    # transformers 5.x 下的 Breeze 兼容层产物已人工试听确认是噪音；
    # 委托给隔离的 4.57.3 venv 执行（4.x 环境原生正常，无需委托）。
    from integrated_app.breeze_runtime import run_isolated_breeze, should_delegate

    if should_delegate():
        data = run_isolated_breeze(args, mode)
        if data is None:
            return Result.failure("Breeze 隔离环境调用异常", "INTERNAL_ERROR", command=f"breeze-{mode}")
        if "error" in data:
            return Result.failure(data["error"], data.get("error_code", "BUSINESS_ERROR"), command=f"breeze-{mode}")
        # 子进程 data 已含 audio_path / duration / engine / params；
        # engine 必须放在 data 内（Result 顶层无该字段）。
        data.setdefault("engine", "breeze-tts2")
        result = Result.success(data=data, command=f"breeze-{mode}")
        # 委托路径直接返回，绕过了 main() 的 -o 复制；在此补上，
        # 否则用户指定的 --output 不生效（音频只落在 service 默认路径）。
        if getattr(args, "output", None):
            from integrated_app.cli_service import _copy_to_requested_output

            # 用绝对路径，避免父进程 cwd 与子进程不一致导致路径漂移
            out_path = os.path.abspath(os.path.expanduser(str(args.output)))
            result = _copy_to_requested_output(result, out_path)
        return result

    try:
        engine, err = _load_breeze()
    except Exception as exc:  # noqa: BLE001 - 依赖/权重缺失统一转未就绪
        return Result.failure(f"Breeze 引擎加载失败: {exc}", "NOT_READY", command=f"breeze-{mode}")
    if err is not None:
        return err

    if mode == "design":
        params = {"instruction": instruction, "cfg_scale": cfg_scale, "seed": seed}
        log("Breeze voice design ...")
        raw = engine.generate_voice_design(text, instruction=instruction, cfg_scale=cfg_scale, seed=seed)
        engine_name = "breeze-tts2"
    elif mode == "clone":
        ref = getattr(args, "reference_audio", None)
        if not ref or not os.path.isfile(ref):
            return Result.failure(f"参考音频不存在: {ref}", "USAGE_ERROR", command="breeze-clone")
        ref_text = getattr(args, "ref_text", "") or ""
        if not ref_text:
            return Result.failure(
                "Breeze 克隆需要 --ref-text（参考音频的准确转写）", "USAGE_ERROR", command="breeze-clone"
            )
        params = {"reference_audio": ref, "ref_text": ref_text, "seed": seed}
        log("Breeze voice clone ...")
        raw = engine.generate_voice_clone(text, reference_audio_path=ref, ref_text=ref_text, seed=seed)
        engine_name = "breeze-tts2"
    else:  # direction
        ref = getattr(args, "reference_audio", None)
        if not ref or not os.path.isfile(ref):
            return Result.failure(f"参考音频不存在: {ref}", "USAGE_ERROR", command="breeze-direction")
        if not instruction.strip():
            return Result.failure(
                "Breeze direction 需要 --control（语气/情感指令）", "USAGE_ERROR", command="breeze-direction"
            )
        params = {"reference_audio": ref, "instruction": instruction, "cfg_scale": cfg_scale, "seed": seed}
        log("Breeze voice direction ...")
        raw = engine.generate_ultimate_clone(
            text,
            instruction=instruction,
            ref_audio_path=ref,
            advanced_cfg=cfg_scale,
            advanced_seed=seed,
            ref_text=getattr(args, "ref_text", "") or "",
        )
        engine_name = "breeze-tts2"

    return _audio_tuple_to_result(
        raw,
        command=f"breeze-{mode}",
        output=getattr(args, "output", None),
        engine=engine_name,
        params=params,
        audio_first=True,
    )


# ---------------------------------------------------------------------------
# Edge-TTS 在线兜底
# ---------------------------------------------------------------------------


def run_edge(args: Any, parser: Any) -> Result:
    """Edge-TTS 在线合成（无需本地权重，产物为 MP3）。"""
    import asyncio

    from integrated_app.routes.generate.utils import _synthesize_edge_tts_wav

    _setup_offline_env()
    lang = getattr(args, "lang", "zh") or "zh"
    log(f"Edge-TTS 在线合成（lang={lang}）...")
    try:
        filename, size_bytes = asyncio.run(_synthesize_edge_tts_wav(args.text, lang))
    except Exception as exc:  # noqa: BLE001 - 网络/依赖失败统一转业务失败
        return Result.failure(f"Edge-TTS 合成失败: {exc}", "BUSINESS_ERROR", command="edge")

    path = _resolve_audio_path(filename)
    data = {
        "audio_path": os.path.abspath(path),
        "message": f"Edge-TTS 合成完成（{size_bytes} 字节）",
        "duration": 0.0,
        "engine": "edge-tts",
        "params": {"lang": lang, "size_bytes": size_bytes},
    }
    result = Result.success(data=data, command="edge")
    output = getattr(args, "output", None)
    if output:
        result = _copy_to_requested_output(result, output)
    return result


# ---------------------------------------------------------------------------
# Voicebox 音色转换 / Step-Audio 音频编辑
# ---------------------------------------------------------------------------


def run_convert(args: Any, parser: Any) -> Result:
    """Voicebox（OpenVoice）音色转换：保留内容与韵律，替换音色。"""
    _setup_offline_env()
    source = getattr(args, "source_audio", None)
    target = getattr(args, "target_audio", None)
    for label, path in (("源音频", source), ("目标音色音频", target)):
        if not path or not os.path.isfile(path):
            return Result.failure(f"{label}不存在: {path}", "USAGE_ERROR", command="convert")

    try:
        # 复用路由层单例工厂：它实现了 shared/portable 双模式的模型路径解析，
        # 避免 CLI 另写一套配置逻辑产生漂移。其 request 形参在函数体内未使用，
        # 纯 CLI 场景传 None 安全。
        from integrated_app.routes.generate.voicebox.convert import _get_voicebox_engine

        engine, err = _get_voicebox_engine(None)  # type: ignore[arg-type]  # request 形参未使用
        if engine is None:
            return Result.failure(f"Voicebox 加载失败: {err}", "NOT_READY", command="convert")
    except Exception as exc:  # noqa: BLE001
        return Result.failure(f"Voicebox 加载失败: {exc}", "NOT_READY", command="convert")

    tau = getattr(args, "tau", 0.3)
    log(f"执行音色转换（tau={tau}）...")
    res = engine.voice_conversion(
        source_audio=source,
        target_audio=target,
        output_path=getattr(args, "output", None) or _default_output("voicebox_convert"),
        tau=tau,
    )
    if not getattr(res, "success", False):
        return Result.failure(f"音色转换失败: {res.message}", "BUSINESS_ERROR", command="convert")
    return Result.success(data=res.to_dict(), command="convert")


def run_edit(args: Any, parser: Any) -> Result:
    """Step-Audio-EditX 音频编辑：情绪/风格/副语言/语速。"""
    _setup_offline_env()
    source = getattr(args, "source_audio", None)
    if not source or not os.path.isfile(source):
        return Result.failure(f"源音频不存在: {source}", "USAGE_ERROR", command="edit")
    source_text = getattr(args, "source_text", None)
    if not source_text:
        return Result.failure("音频编辑需要 --source-text（源音频的准确转写）", "USAGE_ERROR", command="edit")

    edit_type = getattr(args, "edit_type", None)
    if not edit_type:
        return Result.failure("音频编辑需要 --edit-type", "USAGE_ERROR", command="edit")

    try:
        # 同 convert：复用路由层工厂保证模型路径与 Web 一致（request 未使用，传 None）
        from integrated_app.routes.generate.step_audio_editx.edit import _get_editx_engine

        engine, err = _get_editx_engine(None)  # type: ignore[arg-type]  # request 形参未使用
        if engine is None:
            return Result.failure(
                f"Step-Audio-EditX 加载失败（可能缺少 vLLM 或权重）: {err}", "NOT_READY", command="edit"
            )
    except Exception as exc:  # noqa: BLE001
        return Result.failure(f"Step-Audio-EditX 加载失败（可能缺少 vLLM 或权重）: {exc}", "NOT_READY", command="edit")

    edit_info = getattr(args, "edit_info", "") or ""
    log(f"执行音频编辑（type={edit_type}, info={edit_info}）...")
    res = engine.edit_audio(
        source_audio=source,
        source_text=source_text,
        edit_type=edit_type,
        edit_info=edit_info,
        target_text=getattr(args, "target_text", None),
        output_path=getattr(args, "output", None) or _default_output("stepaudio_edit"),
    )
    if not getattr(res, "success", False):
        return Result.failure(f"音频编辑失败: {res.message}", "BUSINESS_ERROR", command="edit")
    return Result.success(data=res.to_dict(), command="edit")


# ---------------------------------------------------------------------------
# 后处理（纯函数，无需模型）
# ---------------------------------------------------------------------------


def run_postprocess(args: Any, parser: Any) -> Result:
    """对已有音频做后处理：变速 / 响度归一 / 人声增强 / 静音裁剪 / 降噪。"""
    import soundfile as sf

    from integrated_app.audio_processing import enhance_audio

    src = getattr(args, "audio", None)
    if not src or not os.path.isfile(src):
        return Result.failure(f"音频文件不存在: {src}", "USAGE_ERROR", command="postprocess")

    audio, sample_rate = sf.read(src)
    params: dict[str, Any] = {
        "tempo_factor": getattr(args, "tempo", 1.0),
        "target_lufs": getattr(args, "lufs", -16.0),
        "voice_enhancement": bool(getattr(args, "enhance", False)),
        "trim_silence": bool(getattr(args, "trim_silence", False)),
        "denoise": bool(getattr(args, "denoise", False)),
        "normalize": bool(getattr(args, "normalize", True)),
    }
    log("执行音频后处理 ...")
    processed = enhance_audio(
        audio,
        int(sample_rate),
        normalize=params["normalize"],
        tempo_factor=params["tempo_factor"],
        voice_enhancement=params["voice_enhancement"],
        target_lufs=params["target_lufs"],
        trim_silence=params["trim_silence"],
        denoise=params["denoise"],
    )

    output = getattr(args, "output", None)
    if not output:
        stem = Path(src).stem
        output = os.path.join(str(Path(src).parent), f"{stem}_post.wav")
    Path(os.path.dirname(output) or ".").mkdir(parents=True, exist_ok=True)
    sf.write(output, processed, int(sample_rate))

    data = {
        "audio_path": os.path.abspath(output),
        "message": "后处理完成",
        "duration": round(len(processed) / sample_rate, 3) if sample_rate else 0.0,
        "engine": "postprocess",
        "params": params,
        "source_audio": os.path.abspath(src),
    }
    return Result.success(data=data, command="postprocess")
