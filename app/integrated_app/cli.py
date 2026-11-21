#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""VoxCPM 命令行接口模块。

提供多引擎 TTS 的命令行工具，支持语音设计、语音克隆和批量处理三种模式。

主要功能：
    1. **语音设计（design）**：通过文本描述生成语音，无需参考音频
    2. **语音克隆（clone）**：使用参考音频或提示音频克隆音色
    3. **批量处理（batch）**：从 TXT/JSON/CSV 文件批量生成音频，支持 WAV/MP3 输出

主要类/函数：
    - validate_file_exists / require_file_exists：文件存在性校验
    - validate_ranges：数值参数范围校验
    - load_model：VoxCPM 模型加载（支持本地路径和 HuggingFace Hub）
    - cmd_design / cmd_clone / cmd_batch：三个子命令的处理函数
    - _parse_text_input / _parse_json_input / _parse_csv_input：批量输入解析
    - _build_parser：argparse 参数解析器构建
    - main：CLI 入口函数

依赖关系：
    - argparse：命令行参数解析
    - soundfile：音频文件读写
    - voxcpm.core.VoxCPM：核心 TTS 模型
    - pydub（可选）：MP3 格式导出支持
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import soundfile as sf

# 包上下文兜底：本文件既可被 `python -m integrated_app.cli` 调用，也会被桌面 shim
# 以脚本方式直跑（`bootstrap\python.exe ...\integrated_app\cli.py`）。后者没有包
# 上下文，相对导入会抛 "attempted relative import with no known parent package"。
# 把 integrated_app 的父目录加入 sys.path 后，下面统一使用绝对导入，两种入口都成立
# （开发布局 app\integrated_app、桌面布局 <InstallDir>\app\app\integrated_app 一致）。
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

# vendor 化：优先从项目内 app/integrated_app/vendor 加载 voxcpm 源码包，
# 不依赖 references/ 下随时可能被删除的克隆学习仓库
if os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")):
    _vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    if _vendor_dir not in sys.path:
        sys.path.insert(0, _vendor_dir)

logger = logging.getLogger("tts_multimodel.cli")


DEFAULT_HF_MODEL_ID = "openbmb/VoxCPM2"

# -----------------------------
# Validators
# -----------------------------


def validate_file_exists(file_path: str, file_type: str = "file") -> Path:
    """校验文件是否存在，存在则返回 Path 对象。

    Args:
        file_path: 文件路径字符串。
        file_type: 文件类型描述，用于错误信息提示，默认为 "file"。

    Returns:
        Path: 表示该文件的 Path 对象。

    Raises:
        FileNotFoundError: 当文件不存在时抛出，错误信息包含文件类型和路径。
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"{file_type} '{file_path}' does not exist")
    return path


def require_file_exists(file_path: str, parser: argparse.ArgumentParser, file_type: str = "file") -> Path:
    """校验文件存在性，失败时通过 argparse 报错退出。

    与 validate_file_exists 的区别：本函数捕获 FileNotFoundError 并调用
    parser.error() 以标准 CLI 错误方式退出，而不是向上抛出异常。

    Args:
        file_path: 文件路径字符串。
        parser: argparse.ArgumentParser 实例，用于输出错误信息。
        file_type: 文件类型描述，用于错误信息提示。

    Returns:
        Path: 表示该文件的 Path 对象。

    Raises:
        SystemExit: 当文件不存在时通过 parser.error() 终止程序（退出码 2）。
    """
    try:
        return validate_file_exists(file_path, file_type)
    except FileNotFoundError as exc:
        parser.error(str(exc))


def validate_output_path(output_path: str) -> Path:
    """校验并准备输出路径，自动创建父目录。

    确保输出文件的父目录存在，若不存在则递归创建（parents=True），
    已存在时不报错（exist_ok=True）。

    Args:
        output_path: 输出文件路径字符串。

    Returns:
        Path: 表示输出路径的 Path 对象（父目录已确保存在）。
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def validate_ranges(args, parser):
    """校验数值型命令行参数的取值范围。

    校验 CFG 值、推理步数、LoRA 参数等在合法区间内，不合法时通过
    parser.error() 输出错误信息并退出。

    Args:
        args: argparse 解析后的命名空间对象。
        parser: argparse.ArgumentParser 实例，用于输出错误信息。

    Raises:
        SystemExit: 当任一参数超出范围时终止程序。
    """
    if not (0.1 <= args.cfg_value <= 10.0):
        parser.error("--cfg-value must be between 0.1 and 10.0 (recommended: 1.0–3.0)")

    if not (1 <= args.inference_timesteps <= 100):
        parser.error("--inference-timesteps must be between 1 and 100 (recommended: 4–30)")

    if args.lora_r <= 0:
        parser.error("--lora-r must be a positive integer")

    if args.lora_alpha <= 0:
        parser.error("--lora-alpha must be a positive integer")

    if not (0.0 <= args.lora_dropout <= 1.0):
        parser.error("--lora-dropout must be between 0.0 and 1.0")


def warn_legacy_mode():
    """输出旧版 CLI 参数模式的弃用警告。

    提醒用户优先使用子命令语法（tts-multimodel design|clone|batch）而不是
    根级参数，根级参数将在未来版本中移除。
    """
    logger.warning("Legacy root CLI arguments are deprecated. Prefer `tts-multimodel design|clone|batch ...`.")


def build_final_text(text: str, control: str | None) -> str:
    """构建最终输入文本，将控制指令前缀添加到文本前。

    VoxCPM2 支持通过 (控制指令)文本 格式指定语音风格，本函数负责
    将可选的 control 参数包装为该格式。

    Args:
        text: 原始要合成的文本内容。
        control: 语音控制指令（如 "warm female voice"），为 None 或空字符串时不添加前缀。

    Returns:
        str: 若 control 非空则返回 "(control)text" 格式，否则返回原 text。
    """
    control = (control or "").strip()
    return f"({control}){text}" if control else text


def resolve_prompt_text(args, parser) -> str | None:
    """从命令行参数中解析提示文本（prompt text）。

    支持两种来源：--prompt-text 直接传入文本，或 --prompt-file 从文件读取。
    两者互斥，不能同时使用。

    Args:
        args: argparse 解析后的命名空间对象。
        parser: argparse.ArgumentParser 实例，用于输出错误信息。

    Returns:
        str | None: 解析得到的提示文本（已 strip）；若两者都未提供则返回 None。

    Raises:
        SystemExit: 当同时指定 --prompt-text 和 --prompt-file 时报错退出。
    """
    prompt_text = getattr(args, "prompt_text", None)
    prompt_file = getattr(args, "prompt_file", None)

    if prompt_text and prompt_file:
        parser.error("Use either --prompt-text or --prompt-file, not both.")

    if prompt_file:
        prompt_path = require_file_exists(prompt_file, parser, "prompt text file")
        return prompt_path.read_text(encoding="utf-8").strip()

    if prompt_text:
        return prompt_text.strip()

    return None


def detect_model_architecture(args) -> str | None:
    """检测模型架构类型（voxcpm 或 voxcpm2）。

    检测策略：
        1. 若为本地目录：读取目录下 config.json 的 architecture 字段
        2. 若为 HuggingFace 仓库 ID：根据路径字符串中的关键词推断
           - "voxcpm2" → voxcpm2
           - "voxcpm1.5" / "voxcpm-1.5" / "voxcpm_1.5" → voxcpm

    Args:
        args: argparse 解析后的命名空间对象，需包含 model_path 或 hf_model_id 属性。

    Returns:
        str | None: 检测到的架构名称小写字符串（"voxcpm" 或 "voxcpm2"）；
            无法检测时返回 None。
    """
    model_location = getattr(args, "model_path", None) or getattr(args, "hf_model_id", None)
    if not model_location:
        return None

    if os.path.isdir(model_location):
        config_path = Path(model_location) / "config.json"
        if not config_path.exists():
            return None

        with open(config_path, encoding="utf-8") as f:
            return json.load(f).get("architecture", "voxcpm").lower()

    model_hint = str(model_location).lower()
    if "voxcpm2" in model_hint:
        return "voxcpm2"
    if "voxcpm1.5" in model_hint or "voxcpm-1.5" in model_hint or "voxcpm_1.5" in model_hint:
        return "voxcpm"

    return None


def validate_prompt_related_args(args, parser, prompt_text: str | None):
    """校验提示音频与提示文本的参数组合合法性。

    规则：
        - prompt_text/prompt_file 必须与 prompt_audio 同时使用
        - prompt_audio 必须与 prompt_text/prompt_file 同时使用
        - control 不能与 prompt_text/prompt_file 同时使用（两者互斥）

    Args:
        args: argparse 解析后的命名空间对象。
        parser: argparse.ArgumentParser 实例，用于输出错误信息。
        prompt_text: 已解析的提示文本（来自 resolve_prompt_text）。

    Raises:
        SystemExit: 当参数组合不合法时终止程序。
    """
    if prompt_text and not args.prompt_audio:
        parser.error("--prompt-text/--prompt-file requires --prompt-audio.")

    if args.prompt_audio and not prompt_text:
        parser.error("--prompt-audio requires --prompt-text or --prompt-file.")

    if args.control and prompt_text:
        parser.error("--control cannot be used together with --prompt-text or --prompt-file.")


def validate_reference_support(args, parser):
    """校验参考音频（reference audio）的模型兼容性。

    --reference-audio（零样本克隆）仅 VoxCPM2 架构支持，VoxCPM 1.x 不支持。
    若检测到架构为 voxcpm 但使用了 reference_audio，则报错退出。

    Args:
        args: argparse 解析后的命名空间对象。
        parser: argparse.ArgumentParser 实例，用于输出错误信息。

    Raises:
        SystemExit: 当 VoxCPM 1.x 模型使用 --reference-audio 时终止程序。
    """
    if not getattr(args, "reference_audio", None):
        return

    arch = detect_model_architecture(args)
    if arch == "voxcpm":
        parser.error("--reference-audio is only supported with VoxCPM2 models.")


def validate_design_args(args, parser):
    """校验 design 子命令的参数合法性。

    design 模式（语音设计）不接受任何提示/参考音频，这些参数属于 clone 模式。
    若误用则报错退出。

    Args:
        args: argparse 解析后的命名空间对象。
        parser: argparse.ArgumentParser 实例，用于输出错误信息。

    Raises:
        SystemExit: 当 design 模式使用了 prompt/reference 音频参数时终止程序。
    """
    prompt_text = resolve_prompt_text(args, parser)
    if args.prompt_audio or args.reference_audio or prompt_text:
        parser.error("`design` does not accept prompt/reference audio. Use `clone` instead.")


def validate_clone_args(args, parser):
    """校验 clone 子命令的参数合法性并返回解析后的提示文本。

    组合校验：
        1. 提示音频与提示文本的配对关系（validate_prompt_related_args）
        2. 参考音频的模型架构兼容性（validate_reference_support）
        3. 必须提供 reference_audio 或 prompt_audio+prompt_text 组合

    Args:
        args: argparse 解析后的命名空间对象。
        parser: argparse.ArgumentParser 实例，用于输出错误信息。

    Returns:
        str | None: 解析得到的提示文本。

    Raises:
        SystemExit: 当参数组合不合法时终止程序。
    """
    prompt_text = resolve_prompt_text(args, parser)
    validate_prompt_related_args(args, parser, prompt_text)
    validate_reference_support(args, parser)

    if not args.prompt_audio and not args.reference_audio:
        parser.error("`clone` requires --reference-audio, or --prompt-audio with --prompt-text/--prompt-file.")

    return prompt_text


def validate_batch_args(args, parser):
    """校验 batch 子命令的参数合法性并返回解析后的提示文本。

    批量模式支持可选的 prompt/reference 音频作为全局音色，校验逻辑与
    clone 模式类似，但音频参数为可选。

    Args:
        args: argparse 解析后的命名空间对象。
        parser: argparse.ArgumentParser 实例，用于输出错误信息。

    Returns:
        str | None: 解析得到的提示文本（若提供了 prompt_file/prompt_text）。

    Raises:
        SystemExit: 当参数组合不合法时终止程序。
    """
    prompt_text = resolve_prompt_text(args, parser)
    validate_prompt_related_args(args, parser, prompt_text)
    validate_reference_support(args, parser)
    return prompt_text


# -----------------------------
# Model loading
# -----------------------------


def load_model(args):
    """加载 VoxCPM 模型，支持本地路径和 HuggingFace Hub 两种方式。

    加载流程：
        1. 解析 ZipEnhancer 路径（参数优先，其次环境变量 ZIPENHANCER_MODEL_PATH）
        2. 若提供 LoRA 权重路径，构建 LoRAConfig
        3. 若指定 --model-path，从本地目录加载
        4. 否则从 HuggingFace Hub 下载/加载（--local-files-only 控制是否允许联网）

    Args:
        args: argparse 解析后的命名空间对象，需包含模型路径、LoRA 参数等属性。

    Returns:
        VoxCPM: 加载完成的 VoxCPM 模型实例。

    Raises:
        SystemExit: 模型加载失败时通过 sys.exit(1) 终止程序并记录错误日志。
    """
    logger.info("正在加载 VoxCPM 模型...")

    from voxcpm.core import VoxCPM

    zipenhancer_path = getattr(args, "zipenhancer_path", None) or os.environ.get("ZIPENHANCER_MODEL_PATH", None)

    # 若提供 LoRA 权重路径，构建 LoRA 配置对象
    lora_config = None
    lora_weights_path = getattr(args, "lora_path", None)
    if lora_weights_path:
        from voxcpm.model.voxcpm import LoRAConfig

        lora_config = LoRAConfig(
            enable_lm=not args.lora_disable_lm,
            enable_dit=not args.lora_disable_dit,
            enable_proj=args.lora_enable_proj,
            r=args.lora_r,
            alpha=args.lora_alpha,
            dropout=args.lora_dropout,
        )

        logger.info(
            f"LoRA config: r={lora_config.r}, alpha={lora_config.alpha}, "
            f"lm={lora_config.enable_lm}, dit={lora_config.enable_dit}, proj={lora_config.enable_proj}"
        )

    # 从本地路径加载模型
    if args.model_path:
        try:
            model = VoxCPM(
                voxcpm_model_path=args.model_path,
                zipenhancer_model_path=zipenhancer_path,
                enable_denoiser=not args.no_denoiser,
                optimize=not args.no_optimize,
                lora_config=lora_config,
                lora_weights_path=lora_weights_path,
            )
            logger.info("模型已加载 (本地)。")
            return model
        except Exception as e:
            logger.error(f"模型加载失败 (本地): {e}")
            sys.exit(1)

    # 从 Hugging Face Hub 加载模型
    try:
        model = VoxCPM.from_pretrained(
            hf_model_id=args.hf_model_id,
            load_denoiser=not args.no_denoiser,
            zipenhancer_model_id=zipenhancer_path,
            cache_dir=args.cache_dir,
            local_files_only=args.local_files_only,
            optimize=not args.no_optimize,
            lora_config=lora_config,
            lora_weights_path=lora_weights_path,
        )
        logger.info("模型已加载 (from_pretrained)。")
        return model
    except Exception as e:
        logger.error(f"模型加载失败 (from_pretrained): {e}")
        sys.exit(1)


# -----------------------------
# Commands
# -----------------------------


def _run_single(args, parser, *, text: str, output: str, prompt_text: str | None):
    """执行单条 TTS 生成任务（内部函数，供 design/clone 子命令调用）。

    流程：校验输出路径 → 校验音频文件存在 → 加载模型 → 调用 model.generate()
    → 保存音频文件 → 输出时长信息。

    Args:
        args: argparse 解析后的命名空间对象。
        parser: argparse.ArgumentParser 实例。
        text: 最终要合成的文本（已包含 control 前缀）。
        output: 输出音频文件路径。
        prompt_text: 提示音频对应的文本（延续模式使用，design 模式为 None）。

    Raises:
        SystemExit: 音频文件不存在时通过 require_file_exists 终止。
    """
    output_path = validate_output_path(output)

    if args.prompt_audio:
        require_file_exists(args.prompt_audio, parser, "prompt audio file")
    if args.reference_audio:
        require_file_exists(args.reference_audio, parser, "reference audio file")

    model = load_model(args)

    audio_array = model.generate(
        text=text,
        prompt_wav_path=args.prompt_audio,
        prompt_text=prompt_text,
        reference_wav_path=args.reference_audio,
        cfg_value=args.cfg_value,
        inference_timesteps=args.inference_timesteps,
        normalize=args.normalize,
        denoise=args.denoise and (args.prompt_audio is not None or args.reference_audio is not None),
    )

    sf.write(str(output_path), audio_array, model.tts_model.sample_rate)

    duration = len(audio_array) / model.tts_model.sample_rate
    logger.info(f"音频已保存至: {output_path} ({duration:.2f}s)")


def cmd_design(args, parser):
    """处理 design 子命令：语音设计模式生成音频。

    语音设计模式通过文本描述（--control）控制语音风格，无需参考音频。

    Args:
        args: argparse 解析后的命名空间对象。
        parser: argparse.ArgumentParser 实例。
    """
    validate_design_args(args, parser)
    final_text = build_final_text(args.text, args.control)
    return _run_single(args, parser, text=final_text, output=args.output, prompt_text=None)


def cmd_clone(args, parser):
    """处理 clone 子命令：语音克隆模式生成音频。

    语音克隆模式支持两种方式：
        1. 零样本克隆：使用 --reference-audio 提供参考音频
        2. 提示延续：使用 --prompt-audio + --prompt-text 进行音频续写

    Args:
        args: argparse 解析后的命名空间对象。
        parser: argparse.ArgumentParser 实例。
    """
    prompt_text = validate_clone_args(args, parser)
    final_text = build_final_text(args.text, args.control)
    return _run_single(args, parser, text=final_text, output=args.output, prompt_text=prompt_text)


def cmd_batch(args, parser):
    """处理 batch 子命令：从输入文件批量生成音频。

    支持三种输入格式：
        - TXT：纯文本文件，每行一条待合成文本（# 开头为注释行）
        - JSON：字符串数组 或 对象数组（对象需包含 text 字段，可选 control/output）
        - CSV：需包含 text 列，可选 control/output 列，首行自动识别表头

    输出格式支持 WAV（默认）和 MP3（需安装 pydub）。
    处理过程中显示实时进度、已用时间和预计剩余时间（ETA）。

    Args:
        args: argparse 解析后的命名空间对象，需包含 input、output_dir、format 等。
        parser: argparse.ArgumentParser 实例。

    Raises:
        SystemExit: 输入文件为空或无有效任务时终止程序。
    """
    input_file = require_file_exists(args.input, parser, "input file")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_format = getattr(args, "format", "wav") or "wav"
    engine = getattr(args, "engine", None)

    # 根据文件扩展名解析输入文件
    tasks = _parse_batch_input(input_file, args, parser)

    if not tasks:
        sys.exit("Error: Input file is empty or contains no valid tasks")

    prompt_text = validate_batch_args(args, parser)
    model = load_model(args)

    prompt_audio_path = None
    if args.prompt_audio:
        prompt_audio_path = str(require_file_exists(args.prompt_audio, parser, "prompt audio file"))

    reference_audio_path = None
    if args.reference_audio:
        reference_audio_path = str(require_file_exists(args.reference_audio, parser, "reference audio file"))

    total = len(tasks)
    success_count = 0
    fail_count = 0
    skip_count = 0
    resume_mode = bool(getattr(args, "resume", False))
    start_time = time.time()

    logger.info(f"\n{'=' * 50}")
    logger.info(
        f"Batch processing: {total} tasks | Format: {output_format.upper()} | Engine: {engine or 'auto'}"
        f"{' | resume' if resume_mode else ''}"
    )
    logger.info(f"{'=' * 50}\n")

    for i, task in enumerate(tasks, 1):
        text = task["text"]
        control = task.get("control") or args.control
        task_output_name = task.get("output")

        try:
            # 确定输出文件名（resume 判定需要提前算出来）
            if task_output_name:
                out_name = task_output_name
                if not out_name.endswith(f".{output_format}"):
                    out_name = f"{out_name}.{output_format}"
            else:
                out_name = f"output_{i:03d}.{output_format}"
            output_file = output_dir / out_name

            # resume：已存在且非空视为已完成，直接跳过（断点续跑）
            if resume_mode and output_file.is_file() and output_file.stat().st_size > 0:
                skip_count += 1
                logger.info(f"[{i}/{total}] SKIP (already exists): {out_name}")
                continue

            final_text = build_final_text(text, control)
            audio_array = model.generate(
                text=final_text,
                prompt_wav_path=prompt_audio_path,
                prompt_text=prompt_text,
                reference_wav_path=reference_audio_path,
                cfg_value=args.cfg_value,
                inference_timesteps=args.inference_timesteps,
                normalize=args.normalize,
                denoise=args.denoise and (prompt_audio_path is not None or reference_audio_path is not None),
            )

            sample_rate = model.tts_model.sample_rate

            if output_format == "mp3":
                _save_as_mp3(audio_array, sample_rate, str(output_file))
            else:
                sf.write(str(output_file), audio_array, sample_rate)

            duration = len(audio_array) / sample_rate
            elapsed = time.time() - start_time
            avg_time = elapsed / i
            eta = avg_time * (total - i)
            logger.info(
                f"[{i}/{total}] Saved: {output_file.name} ({duration:.2f}s) | Elapsed: {elapsed:.1f}s | ETA: {eta:.1f}s"
            )
            success_count += 1

        except Exception as e:
            fail_count += 1
            logger.error(f"[{i}/{total}] FAILED: {e}")

    elapsed_total = time.time() - start_time
    logger.info(f"\n{'=' * 50}")
    logger.info(
        f"Batch complete: {success_count}/{total} succeeded, {fail_count} failed, {skip_count} skipped "
        f"| Total time: {elapsed_total:.1f}s | Avg: {elapsed_total / max(success_count, 1):.2f}s/task"
    )
    logger.info(f"Output: {output_dir.resolve()}")
    logger.info(f"{'=' * 50}")


def _parse_batch_input(input_file: Path, args, parser) -> list[dict]:
    """根据文件扩展名解析批量输入文件（内部函数）。

    分发策略：
        - .json → _parse_json_input
        - .csv → _parse_csv_input
        - 其他（默认 .txt）→ _parse_text_input

    Args:
        input_file: 输入文件的 Path 对象。
        args: argparse 命名空间（传递给子解析器）。
        parser: argparse.ArgumentParser 实例。

    Returns:
        list[dict]: 任务列表，每个 dict 至少包含 "text" 键，可选 "control"、"output"。
    """
    suffix = input_file.suffix.lower()

    if suffix == ".json":
        return _parse_json_input(input_file, parser)
    elif suffix == ".csv":
        return _parse_csv_input(input_file, parser)
    else:
        # 默认按纯文本格式处理（每行一条文本）
        return _parse_text_input(input_file)


def _parse_text_input(input_file: Path) -> list[dict]:
    """解析纯文本输入文件，每行一条待合成文本。

    解析规则：
        - 空行自动跳过
        - 以 # 开头的行视为注释，跳过
        - 每行文本 strip() 后作为 "text" 字段

    Args:
        input_file: 输入 TXT 文件的 Path 对象。

    Returns:
        list[dict]: 任务列表，每项为 {"text": "..."} 格式。
    """
    with open(input_file, encoding="utf-8") as f:
        tasks = []
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                tasks.append({"text": line})
    return tasks


def _parse_json_input(input_file: Path, parser) -> list[dict]:
    """解析 JSON 输入文件。

    支持两种 JSON 格式：
        1. 字符串数组：["文本1", "文本2", ...]
        2. 对象数组：[{"text": "...", "control": "...", "output": "..."}, ...]

    非法格式（非数组、缺少 text 字段的对象）会跳过并记录警告。

    Args:
        input_file: 输入 JSON 文件的 Path 对象。
        parser: argparse.ArgumentParser 实例，JSON 解码失败时用于报错退出。

    Returns:
        list[dict]: 任务列表，每项至少包含 "text" 键。

    Raises:
        SystemExit: JSON 格式错误或根元素不是数组时终止程序。
    """
    import json

    try:
        with open(input_file, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        parser.error(f"Invalid JSON in {input_file}: {e}")
        return []

    if not isinstance(data, list):
        parser.error(f"JSON input must be an array, got {type(data).__name__}")
        return []

    tasks = []
    for i, item in enumerate(data):
        if isinstance(item, str):
            tasks.append({"text": item})
        elif isinstance(item, dict):
            if "text" not in item:
                logger.warning(f"JSON item {i} missing 'text' field, skipping")
                continue
            tasks.append(item)
        else:
            logger.warning(f"JSON item {i} is {type(item).__name__}, expected string or object, skipping")

    return tasks


def _parse_csv_input(input_file: Path, parser) -> list[dict]:
    """解析 CSV 输入文件。

    预期列：text（必需）、control（可选）、output（可选）。
    首行自动检测是否为表头：若第一格为 text/content/input/文本/内容 则视为表头行，
    否则将首行作为数据处理。

    Args:
        input_file: 输入 CSV 文件的 Path 对象。
        parser: argparse.ArgumentParser 实例，解析失败时用于报错退出。

    Returns:
        list[dict]: 任务列表，每项至少包含 "text" 键。

    Raises:
        SystemExit: CSV 解析异常时终止程序。
    """
    import csv

    tasks = []
    try:
        with open(input_file, encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header is None:
                return []

            # 检测首行是否为表头
            first_cell = header[0].strip().lower() if header else ""
            if first_cell in ("text", "content", "input", "文本", "内容"):
                # 首行为表头，使用表头定义列映射
                columns = [h.strip().lower() for h in header]
            else:
                # 首行不是表头，按默认列顺序处理并将首行作为数据
                columns = ["text", "control", "output"]
                row_data = {columns[j]: header[j].strip() for j in range(min(len(header), len(columns)))}
                if row_data.get("text"):
                    tasks.append(row_data)

            for row in reader:
                if not row or all(cell.strip() == "" for cell in row):
                    continue
                row_data = {columns[j]: row[j].strip() for j in range(min(len(row), len(columns)))}
                if row_data.get("text"):
                    tasks.append(row_data)

    except Exception as e:
        parser.error(f"Failed to parse CSV {input_file}: {e}")

    return tasks


def _save_as_mp3(audio_array, sample_rate: int, output_path: str) -> None:
    """将音频数组保存为 MP3 格式（内部函数）。

    实现方式：先以 WAV 格式写入内存缓冲区，再通过 pydub 转换为 MP3。
    若 pydub 未安装，则降级保存为 WAV 格式并记录警告。

    Args:
        audio_array: numpy 音频数组（dtype 通常为 float32）。
        sample_rate: 音频采样率（Hz）。
        output_path: 输出 MP3 文件路径。

    Note:
        MP3 导出默认比特率为 192kbps。需要系统安装 ffmpeg 才能正常工作。
    """
    import io

    try:
        from pydub import AudioSegment

        buf = io.BytesIO()
        sf.write(buf, audio_array, sample_rate, format="WAV")
        buf.seek(0)
        audio = AudioSegment.from_wav(buf)
        audio.export(output_path, format="mp3", bitrate="192k")
    except ImportError:
        logger.warning("pydub not installed, falling back to WAV format")
        wav_path = output_path.rsplit(".", 1)[0] + ".wav"
        sf.write(wav_path, audio_array, sample_rate)


# -----------------------------
# Parser
# -----------------------------


def _add_common_generation_args(parser):
    """向 argparse 解析器添加通用生成参数（内部函数）。

    添加的参数：
        --text/-t: 待合成文本
        --control: VoxCPM2 语音控制指令
        --cfg-value: CFG 引导强度（默认 2.0，推荐 1.0-3.0）
        --inference-timesteps: 扩散推理步数（默认 10，推荐 4-30）
        --normalize: 是否启用文本归一化

    Args:
        parser: argparse.ArgumentParser 或子解析器实例。
    """
    parser.add_argument("--text", "-t", help="Text to synthesize")
    parser.add_argument(
        "--control",
        type=str,
        help="Control instruction for VoxCPM2 voice design/cloning",
    )
    parser.add_argument(
        "--cfg-value",
        type=float,
        default=2.0,
        help="CFG guidance scale (float, recommended 1.0–3.0, default: 2.0)",
    )
    parser.add_argument(
        "--inference-timesteps",
        type=int,
        default=10,
        help="Inference steps (int, recommended 4–30, default: 10)",
    )
    parser.add_argument("--normalize", action="store_true", help="Enable text normalization")


def _add_engine_arg(parser):
    """添加 --engine 参数（统一服务层入口，真正生效）。"""
    parser.add_argument(
        "--engine",
        type=str,
        choices=["voxcpm2", "indextts2"],
        default="voxcpm2",
        help="TTS engine to use via the unified service layer (default: voxcpm2)",
    )


def _add_seed_arg(parser):
    """添加 --seed 参数（-1 表示随机）。"""
    parser.add_argument(
        "--seed",
        type=int,
        default=-1,
        help="Random seed (-1 for random, default: -1)",
    )


def _add_prompt_reference_args(parser):
    """向 argparse 解析器添加提示音频和参考音频相关参数（内部函数）。

    添加的参数：
        --prompt-audio/-pa: 提示音频路径（延续模式）
        --prompt-text/-pt: 提示音频对应的文本
        --prompt-file: 从文件读取提示文本
        --reference-audio/-ra: 语音克隆参考音频
        --denoise: 启用提示/参考音频去噪增强

    Args:
        parser: argparse.ArgumentParser 或子解析器实例。
    """
    parser.add_argument(
        "--prompt-audio",
        "-pa",
        help="Prompt audio file path (continuation mode, requires --prompt-text or --prompt-file)",
    )
    parser.add_argument("--prompt-text", "-pt", help="Text corresponding to the prompt audio")
    parser.add_argument("--prompt-file", type=str, help="Text file corresponding to the prompt audio")
    parser.add_argument(
        "--reference-audio",
        "-ra",
        help="Reference audio for voice cloning",
    )
    parser.add_argument(
        "--denoise",
        action="store_true",
        help="Enable prompt/reference speech enhancement",
    )


def _add_model_args(parser):
    """向 argparse 解析器添加模型加载相关参数（内部函数）。

    添加的参数：
        --model-path: 本地模型路径
        --hf-model-id: HuggingFace 仓库 ID（默认 openbmb/VoxCPM2）
        --cache-dir: Hub 下载缓存目录
        --local-files-only: 仅使用本地文件（禁用网络）
        --no-denoiser: 不加载去噪模型
        --no-optimize: 禁用模型加载优化
        --zipenhancer-path: ZipEnhancer 模型路径或仓库 ID

    Args:
        parser: argparse.ArgumentParser 或子解析器实例。
    """
    parser.add_argument("--model-path", type=str, help="Local model path for the selected engine")
    parser.add_argument(
        "--hf-model-id",
        type=str,
        default=DEFAULT_HF_MODEL_ID,
        help=f"Hugging Face repo id (default: {DEFAULT_HF_MODEL_ID})",
    )
    parser.add_argument("--cache-dir", type=str, help="Cache directory for Hub downloads")
    parser.add_argument("--local-files-only", action="store_true", help="Disable network access")
    parser.add_argument("--no-denoiser", action="store_true", help="Disable denoiser model loading")
    parser.add_argument(
        "--no-optimize",
        action="store_true",
        help="Disable model optimization during loading",
    )
    parser.add_argument(
        "--zipenhancer-path",
        type=str,
        help="ZipEnhancer model id or local path (or env ZIPENHANCER_MODEL_PATH)",
    )


def _add_lora_args(parser):
    """向 argparse 解析器添加 LoRA 微调相关参数（内部函数）。

    添加的参数：
        --lora-path: LoRA 权重文件路径
        --lora-r: LoRA 秩（默认 32，正整数）
        --lora-alpha: LoRA alpha 缩放系数（默认 16）
        --lora-dropout: LoRA dropout 率（0.0-1.0，默认 0.0）
        --lora-disable-lm: 不在 LM 层应用 LoRA
        --lora-disable-dit: 不在 DiT 层应用 LoRA
        --lora-enable-proj: 在投影层启用 LoRA

    Args:
        parser: argparse.ArgumentParser 或子解析器实例。
    """
    parser.add_argument("--lora-path", type=str, help="Path to LoRA weights")
    parser.add_argument("--lora-r", type=int, default=32, help="LoRA rank (positive int, default: 32)")
    parser.add_argument(
        "--lora-alpha",
        type=int,
        default=16,
        help="LoRA alpha (positive int, default: 16)",
    )
    parser.add_argument(
        "--lora-dropout",
        type=float,
        default=0.0,
        help="LoRA dropout rate (0.0–1.0, default: 0.0)",
    )
    parser.add_argument("--lora-disable-lm", action="store_true", help="Disable LoRA on LM layers")
    parser.add_argument("--lora-disable-dit", action="store_true", help="Disable LoRA on DiT layers")
    parser.add_argument(
        "--lora-enable-proj",
        action="store_true",
        help="Enable LoRA on projection layers",
    )


def _dispatch_result(fn, args, *, command: str = ""):
    """调用 handler 并把已知异常统一转成 Result（CLI 不向用户抛堆栈）。

    - ``EngineSwitchError`` / ``EngineNotLoadedError`` → ``NOT_READY``（退出码 3）
    - 其他 ``TTSError`` → ``BUSINESS_ERROR``（退出码 1）
    - 未预期异常 → ``BUSINESS_ERROR``，消息带异常类型便于排查
    """
    from integrated_app.cli_output import Result

    try:
        return fn(args)
    except Exception as exc:  # noqa: BLE001 - 统一收口，避免 CLI 崩栈
        try:
            from integrated_app.exceptions import EngineNotLoadedError, EngineSwitchError, TTSError
        except Exception:  # pragma: no cover - 异常模块不可用属极端情况
            return Result.failure(f"内部错误: {type(exc).__name__}: {exc}", "BUSINESS_ERROR", command=command)
        if isinstance(exc, (EngineSwitchError, EngineNotLoadedError)):
            return Result.failure(str(exc), "NOT_READY", command=command)
        if isinstance(exc, TTSError):
            return Result.failure(str(exc), "BUSINESS_ERROR", command=command)
        return Result.failure(f"内部错误: {type(exc).__name__}: {exc}", "BUSINESS_ERROR", command=command)


def _build_parser():
    """构建并返回完整的 argparse 参数解析器。

    解析器结构：
        - 子命令：design / clone / batch（推荐用法）
        - 根级参数：兼容旧版 CLI 的遗留参数（会触发弃用警告）

    Returns:
        argparse.ArgumentParser: 配置完成的参数解析器实例。
    """
    parser = argparse.ArgumentParser(
        description="TTS MultiModel CLI - Multi-engine TTS (AI-agent friendly, --json output)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  tts-multimodel design --text "Hello world" --output out.wav
  tts-multimodel generate design --text "Hello" --engine voxcpm2 --json
  tts-multimodel clone --text "Hello" --reference-audio ref.wav --output out.wav
  tts-multimodel batch --input texts.txt --output-dir ./outs --reference-audio ref.wav
        """,
    )

    # 全局：结构化 JSON 输出（AI agent 用）；日志一律走 stderr
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON to stdout (logs go to stderr)",
    )

    # 全局：传输层选择（复用已运行的常驻服务，详见 cli_remote.py 模块文档）。
    # 与 --json 一样允许写在子命令之后——由 main() 的 argv 预提取实现，
    # 这里声明是为了 help 文案与命名空间默认值。
    parser.add_argument(
        "--server",
        type=str,
        default=None,
        help=(
            "Base URL of a running server (e.g. http://127.0.0.1:7901). "
            "Default: auto-discover via app/.server_port / TTS_CLI_SERVER"
        ),
    )
    parser.add_argument(
        "--no-remote",
        action="store_true",
        dest="no_remote",
        help="Force direct in-process execution, skip server discovery (for CI/tests)",
    )

    subparsers = parser.add_subparsers(dest="command")

    # ---- generate 命令族：统一服务层入口（ADR-0005 P0/P1）----
    generate_parser = subparsers.add_parser("generate", help="Generate speech via unified service layer / engines")
    gen_sub = generate_parser.add_subparsers(dest="generate_command")

    gen_design = gen_sub.add_parser("design", help="Voice design via service layer (--engine effective)")
    _add_common_generation_args(gen_design)
    _add_engine_arg(gen_design)
    gen_design.add_argument("--output", "-o", help="Optional output path (copies from service result)")

    gen_clone = gen_sub.add_parser("clone", help="Voice clone via service layer (--engine effective)")
    _add_common_generation_args(gen_clone)
    _add_prompt_reference_args(gen_clone)
    _add_engine_arg(gen_clone)
    gen_clone.add_argument("--output", "-o", help="Optional output path (copies from service result)")

    # --- VoxCPM2 扩展动作 ---
    gen_ultimate = gen_sub.add_parser("ultimate", help="VoxCPM2 ultimate clone (reference audio + transcript)")
    _add_common_generation_args(gen_ultimate)
    _add_seed_arg(gen_ultimate)
    gen_ultimate.add_argument("--reference-audio", "-ra", required=True, help="Reference audio path")
    gen_ultimate.add_argument("--ref-text", default="", help="Exact transcript of the reference audio")
    gen_ultimate.add_argument(
        "--denoise-strength", type=float, default=0.5, help="Denoise strength 0.0-1.0 (default 0.5)"
    )
    gen_ultimate.add_argument("--output", "-o", help="Optional output path")

    gen_prompt = gen_sub.add_parser("prompt-continue", help="VoxCPM2 prompt continuation (audio + its text)")
    _add_common_generation_args(gen_prompt)
    gen_prompt.add_argument("--prompt-audio", "-pa", required=True, help="Prompt audio path")
    gen_prompt.add_argument("--prompt-text", "-pt", help="Text of the prompt audio")
    gen_prompt.add_argument("--prompt-file", help="File containing the prompt audio transcript")
    gen_prompt.add_argument("--output", "-o", help="Optional output path")

    gen_script = gen_sub.add_parser("script", help="VoxCPM2 script studio ([speaker] line format)")
    gen_script.add_argument("--input", "-i", required=True, help="Script file ([speaker] line per row)")
    gen_script.add_argument("--lang", default="中文", help="Language (default: 中文)")
    _add_seed_arg(gen_script)
    gen_script.add_argument("--cfg-value", type=float, default=2.0, help="CFG scale (default 2.0)")
    gen_script.add_argument("--normalize", action="store_true", help="Enable normalization")
    gen_script.add_argument("--denoise-strength", type=float, default=0.5, help="Denoise strength 0.0-1.0")
    gen_script.add_argument("--inference-timesteps", type=int, default=10, help="Inference steps (default 10)")
    gen_script.add_argument("--output", "-o", help="Optional output path")
    gen_script.add_argument(
        "--persona-map",
        action="append",
        metavar="ROLE=AUDIO",
        help="Map a script role to a reference audio file (repeatable), e.g. --persona-map 旁白=narr.wav",
    )

    # --- IndexTTS2 情感合成 ---
    gen_emotion = gen_sub.add_parser("emotion", help="IndexTTS2 emotion synthesis (text/audio/8-dim vector)")
    gen_emotion.add_argument("--text", "-t", required=True, help="Text to synthesize")
    gen_emotion.add_argument("--reference-audio", "-ra", required=True, help="Speaker reference audio")
    gen_emotion.add_argument("--emotion-text", help="Natural-language emotion description")
    gen_emotion.add_argument("--emotion-audio", help="Emotion reference audio")
    gen_emotion.add_argument("--emotion-vector", help="8 comma-separated floats (happy,angry,sad,...)")
    gen_emotion.add_argument("--emotion-alpha", type=float, default=0.8, help="Emotion strength 0-1 (default 0.8)")
    gen_emotion.add_argument("--duration-factor", type=float, default=1.0, help="Duration scale (default 1.0)")
    gen_emotion.add_argument("--lang", help="Language code")
    _add_seed_arg(gen_emotion)
    gen_emotion.add_argument("--output", "-o", help="Optional output path")

    # --- Breeze TTS 2 ---
    gen_bz_design = gen_sub.add_parser("breeze-design", help="Breeze TTS 2 voice design")
    gen_bz_design.add_argument("--text", "-t", required=True, help="Text to synthesize")
    gen_bz_design.add_argument("--control", help="Voice description instruction")
    gen_bz_design.add_argument("--cfg-scale", type=float, default=4.0, help="CFG scale (default 4.0)")
    _add_seed_arg(gen_bz_design)
    gen_bz_design.add_argument("--output", "-o", help="Optional output path")

    gen_bz_clone = gen_sub.add_parser("breeze-clone", help="Breeze TTS 2 voice clone (needs ref-text)")
    gen_bz_clone.add_argument("--text", "-t", required=True, help="Text to synthesize")
    gen_bz_clone.add_argument("--reference-audio", "-ra", required=True, help="Reference audio path")
    gen_bz_clone.add_argument("--ref-text", required=True, help="Exact transcript of the reference audio")
    _add_seed_arg(gen_bz_clone)
    gen_bz_clone.add_argument("--output", "-o", help="Optional output path")

    gen_bz_dir = gen_sub.add_parser("breeze-direction", help="Breeze TTS 2 voice direction (clone + instruction)")
    gen_bz_dir.add_argument("--text", "-t", required=True, help="Text to synthesize")
    gen_bz_dir.add_argument("--reference-audio", "-ra", required=True, help="Reference audio path")
    gen_bz_dir.add_argument("--control", required=True, help="Direction instruction (tone/emotion/pace)")
    gen_bz_dir.add_argument("--ref-text", default="", help="Transcript of the reference audio")
    gen_bz_dir.add_argument("--cfg-scale", type=float, default=4.0, help="CFG scale (default 4.0)")
    _add_seed_arg(gen_bz_dir)
    gen_bz_dir.add_argument("--output", "-o", help="Optional output path")

    # --- Edge-TTS 在线兜底 ---
    gen_edge = gen_sub.add_parser("edge", help="Edge-TTS online synthesis (no local weights, MP3)")
    gen_edge.add_argument("--text", "-t", required=True, help="Text to synthesize")
    gen_edge.add_argument("--lang", default="zh", help="Language hint (default zh)")
    gen_edge.add_argument("--output", "-o", help="Optional output path")

    # --- Voicebox 音色转换 ---
    gen_convert = gen_sub.add_parser("convert", help="Voicebox timbre conversion (audio to audio)")
    gen_convert.add_argument("--source-audio", required=True, help="Source speech audio")
    gen_convert.add_argument("--target-audio", required=True, help="Target timbre reference audio")
    gen_convert.add_argument("--tau", type=float, default=0.3, help="Conversion strength 0-1 (default 0.3)")
    gen_convert.add_argument("--output", "-o", help="Optional output path")

    # --- Step-Audio-EditX 音频编辑 ---
    gen_edit = gen_sub.add_parser("edit", help="Step-Audio-EditX audio editing (emotion/style/prosody/speed)")
    gen_edit.add_argument("--source-audio", required=True, help="Source audio path")
    gen_edit.add_argument("--source-text", required=True, help="Exact transcript of the source audio")
    gen_edit.add_argument(
        "--edit-type", required=True, choices=["emotion", "style", "paralinguistic", "speed", "vad", "denoise"]
    )
    gen_edit.add_argument("--edit-info", default="", help="Sub-type/label, e.g. happy / serious / faster")
    gen_edit.add_argument("--target-text", help="Target text (may contain [Laughter] tags)")
    gen_edit.add_argument("--output", "-o", help="Optional output path")

    # --- 顶层后处理（不依赖生成模型）---
    post = subparsers.add_parser("postprocess", help="Post-process an existing audio file")
    post.add_argument("--audio", required=True, help="Input audio file")
    post.add_argument("--tempo", type=float, default=1.0, help="Tempo factor (default 1.0)")
    post.add_argument("--lufs", type=float, default=-16.0, help="Target loudness LUFS (default -16)")
    post.add_argument("--enhance", action="store_true", help="Enable voice enhancement")
    post.add_argument("--trim-silence", action="store_true", help="Trim leading/trailing silence")
    post.add_argument("--denoise", action="store_true", help="Enable denoise")
    post.add_argument("--no-normalize", dest="normalize", action="store_false", help="Disable loudness normalization")
    post.add_argument("--output", "-o", help="Output path (default: <input>_post.wav)")

    # ---- 音色库管理（ADR-0005 P2）----
    persona_parser = subparsers.add_parser("persona", help="Manage the voice persona library")
    persona_sub = persona_parser.add_subparsers(dest="persona_command")

    p_list = persona_sub.add_parser("list", help="List personas (optional keyword filter)")
    p_list.add_argument("--keyword", default="", help="Case-insensitive name filter")

    p_show = persona_sub.add_parser("show", help="Show one persona with consent state")
    p_show.add_argument("--name", required=True, help="Persona name")

    p_save = persona_sub.add_parser("save", help="Save a persona from a reference audio")
    p_save.add_argument("--name", required=True, help="Persona name")
    p_save.add_argument("--audio", required=True, help="Reference audio path")
    p_save.add_argument("--ref-text", default="", help="Transcript of the reference audio")
    p_save.add_argument("--overwrite", action="store_true", help="Overwrite if the persona exists")
    p_save.add_argument(
        "--consent", default="self", choices=["self", "granted", "unverified"], help="Voice consent state"
    )

    p_delete = persona_sub.add_parser("delete", help="Delete a persona and its files")
    p_delete.add_argument("--name", required=True, help="Persona name")

    # ---- 历史记录管理（ADR-0005 P2）----
    history_parser = subparsers.add_parser("history", help="Manage generation history")
    history_sub = history_parser.add_subparsers(dest="history_command")

    h_list = history_sub.add_parser("list", help="List history records (paginated)")
    h_list.add_argument("--limit", type=int, default=20, help="Page size (default 20)")
    h_list.add_argument("--offset", type=int, default=0, help="Offset (default 0)")
    h_list.add_argument("--keyword", default="", help="Search keyword")
    h_list.add_argument("--time-filter", default="all", help="Time filter (default all)")
    h_list.add_argument("--duration-filter", default="all", help="Duration filter (default all)")
    h_list.add_argument("--include-hidden", action="store_true", help="Include hidden records")
    h_list.add_argument("--include-missing", action="store_true", help="Include records whose files are gone")

    history_sub.add_parser("stats", help="Aggregate history statistics")

    h_hide = history_sub.add_parser("hide", help="Hide records by filename (recoverable)")
    h_hide.add_argument("--files", required=True, help="Comma-separated filenames")

    h_clear = history_sub.add_parser("clear", help="Clear all history")
    h_clear.add_argument("--hard", action="store_true", help="Physically delete (default: soft hide)")

    h_recycle = history_sub.add_parser("recycle", help="List recycle bin (hidden records)")
    h_recycle.add_argument("--limit", type=int, default=50, help="Page size (default 50)")
    h_recycle.add_argument("--offset", type=int, default=0, help="Offset (default 0)")

    h_restore = history_sub.add_parser("restore", help="Restore records from recycle bin by ID")
    h_restore.add_argument("--ids", required=True, help="Comma-separated record IDs")

    h_purge = history_sub.add_parser("purge", help="Physically purge expired recycle records")
    h_purge.add_argument("--keep-days", type=int, default=30, help="Retention days (default 30)")

    h_export = history_sub.add_parser("export", help="Export history audio files to a ZIP")
    h_export.add_argument("--files", required=True, help="Comma-separated filenames")
    h_export.add_argument("--output", "-o", help="Output ZIP path")

    h_sync = history_sub.add_parser("sync", help="Sync filesystem audio into history DB")
    h_sync.add_argument("--output-dir", help="Directory to scan (default: outputs/)")
    h_sync.add_argument("--since", type=float, default=0.0, help="Only files newer than this mtime")

    # ---- 模型管理（ADR-0005 P3）----
    model_parser = subparsers.add_parser("model", help="Model lifecycle: status / load / unload / switch")
    model_sub = model_parser.add_subparsers(dest="model_command")
    model_sub.add_parser("status", help="Show current engine / ready / VRAM")
    m_load = model_sub.add_parser("load", help="Load an engine into GPU")
    m_load.add_argument("--engine", default="voxcpm2", choices=["voxcpm2", "indextts2", "indextts20"])
    model_sub.add_parser("unload", help="Unload the current model and free VRAM")
    m_switch = model_sub.add_parser("switch", help="Switch the active engine")
    m_switch.add_argument("--engine", default="voxcpm2", choices=["voxcpm2", "indextts2", "indextts20"])

    # ---- 引擎发现 ----
    engines_parser = subparsers.add_parser("engines", help="Discover registered engines and capabilities")
    engines_sub = engines_parser.add_subparsers(dest="engines_command")
    engines_sub.add_parser("list", help="List all registered engines")
    e_info = engines_sub.add_parser("info", help="Show one engine's metadata and capabilities")
    e_info.add_argument("--engine-id", required=True, help="Engine id, e.g. voxcpm2")

    # ---- 下载管理 ----
    download_parser = subparsers.add_parser("download", help="Runtime / model download management")
    download_sub = download_parser.add_subparsers(dest="download_command")
    download_sub.add_parser("catalog", help="Show downloadable runtime and models")
    download_sub.add_parser("status", help="Show install status")
    d_start = download_sub.add_parser("start", help="Install runtime or a model")
    d_start.add_argument("--kind", required=True, choices=["runtime", "model"], help="What to install")
    d_start.add_argument("--target", default="", help="Model key when --kind model")
    d_uninstall = download_sub.add_parser("uninstall", help="Uninstall runtime or a model")
    d_uninstall.add_argument("--kind", required=True, choices=["runtime", "model"])
    d_uninstall.add_argument("--target", default="", help="Model key when --kind model")

    # ---- LoRA ----
    lora_parser = subparsers.add_parser("lora", help="LoRA weights: list / load / unload / toggle / state")
    lora_sub = lora_parser.add_subparsers(dest="lora_command")
    lora_sub.add_parser("list", help="List checkpoints under lora/")
    l_load = lora_sub.add_parser("load", help="Load LoRA weights")
    l_load.add_argument("--path", required=True, help="LoRA checkpoint path")
    lora_sub.add_parser("unload", help="Unload LoRA and roll back base weights")
    l_toggle = lora_sub.add_parser("toggle", help="Enable or disable the loaded LoRA")
    l_toggle.add_argument("--enable", dest="enabled", action="store_true", help="Enable LoRA")
    l_toggle.add_argument("--disable", dest="enabled", action="store_false", help="Disable LoRA")
    l_toggle.set_defaults(enabled=None)
    lora_sub.add_parser("state", help="Show current LoRA state")

    # ---- 训练 ----
    train_parser = subparsers.add_parser("train", help="LoRA fine-tuning: start / stop / log")
    train_sub = train_parser.add_subparsers(dest="train_command")

    t_start = train_sub.add_parser("start", help="Start a training subprocess in background")
    t_start.add_argument("--train-manifest", required=True, help="Training manifest file")
    t_start.add_argument("--pretrained-path", help="Base model dir (default: model/VoxCPM2)")
    t_start.add_argument("--val-manifest", default="", help="Validation manifest file")
    t_start.add_argument("--save-path", help="Output dir (default: lora/finetune)")
    t_start.add_argument("--sample-rate", type=int, default=48000, help="Sample rate (default 48000)")
    t_start.add_argument("--batch-size", type=int, default=1, help="Batch size (default 1)")
    t_start.add_argument("--grad-accum", type=int, default=1, help="Gradient accumulation steps")
    t_start.add_argument("--num-workers", type=int, default=0, help="Dataloader workers")
    t_start.add_argument("--num-iters", type=int, default=1000, help="Training iterations")
    t_start.add_argument("--log-interval", type=int, default=10, help="Log interval")
    t_start.add_argument("--valid-interval", type=int, default=200, help="Validation interval")
    t_start.add_argument("--save-interval", type=int, default=200, help="Checkpoint interval")
    t_start.add_argument("--learning-rate", type=float, default=1e-4, help="Learning rate")
    t_start.add_argument("--weight-decay", type=float, default=0.01, help="Weight decay")

    t_stop = train_sub.add_parser("stop", help="Stop the training subprocess")
    t_stop.add_argument("--save-path", help="Training output dir (to locate PID)")

    t_log = train_sub.add_parser("log", help="Show the training log tail")
    t_log.add_argument("--save-path", help="Training output dir (to locate logs)")
    t_log.add_argument("--tail", type=int, default=80, help="Number of trailing lines (default 80)")

    # ---- serve：拉起完整 FastAPI（覆盖 /v1、SSE、页面等 HTTP 专属能力）----
    serve_parser = subparsers.add_parser("serve", help="Start the full HTTP server (OpenAI /v1, SSE, Web UI)")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=7869, help="Port (default 7869, auto-increment if busy)")

    # ---- 系统运维（只读视图）----
    system_parser = subparsers.add_parser("system", help="Read-only system views: health / gpu / logs / settings")
    system_sub = system_parser.add_subparsers(dest="system_command")
    system_sub.add_parser("health", help="Environment health: platform / memory / GPU / models")
    system_sub.add_parser("gpu", help="GPU device and VRAM usage")
    s_logs = system_sub.add_parser("logs", help="Tail the application log")
    s_logs.add_argument("--file", help="Explicit log file (default: newest under logs/)")
    s_logs.add_argument("--tail", type=int, default=100, help="Number of trailing lines (default 100)")
    system_sub.add_parser("settings", help="Current config snapshot (secrets masked)")

    design_parser = subparsers.add_parser("design", help="Generate speech with voice design")
    _add_common_generation_args(design_parser)
    _add_prompt_reference_args(design_parser)
    _add_model_args(design_parser)
    _add_lora_args(design_parser)
    design_parser.add_argument(
        "--engine",
        type=str,
        choices=["voxcpm2", "indextts2"],
        default=None,
        help="Engine to use (with --service; legacy mode ignores this)",
    )
    design_parser.add_argument(
        "--service",
        action="store_true",
        help="Use unified service layer (recommended for AI agents; --engine effective)",
    )
    design_parser.add_argument("--output", "-o", required=True, help="Output audio file path")

    clone_parser = subparsers.add_parser("clone", help="Clone a voice with reference/prompt audio")
    _add_common_generation_args(clone_parser)
    _add_prompt_reference_args(clone_parser)
    _add_model_args(clone_parser)
    _add_lora_args(clone_parser)
    clone_parser.add_argument(
        "--engine",
        type=str,
        choices=["voxcpm2", "indextts2"],
        default=None,
        help="Engine to use (with --service; legacy mode ignores this)",
    )
    clone_parser.add_argument(
        "--service",
        action="store_true",
        help="Use unified service layer (recommended for AI agents; --engine effective)",
    )
    clone_parser.add_argument("--output", "-o", required=True, help="Output audio file path")

    batch_parser = subparsers.add_parser("batch", help="Batch-generate one line per output file")
    batch_parser.add_argument("--input", "-i", required=True, help="Input text file (one text per line)")
    batch_parser.add_argument("--output-dir", "-od", required=True, help="Output directory")
    batch_parser.add_argument(
        "--control",
        type=str,
        help="Control instruction for VoxCPM2 voice design/cloning",
    )
    _add_prompt_reference_args(batch_parser)
    batch_parser.add_argument(
        "--cfg-value",
        type=float,
        default=2.0,
        help="CFG guidance scale (float, recommended 1.0–3.0, default: 2.0)",
    )
    batch_parser.add_argument(
        "--inference-timesteps",
        type=int,
        default=10,
        help="Inference steps (int, recommended 4–30, default: 10)",
    )
    batch_parser.add_argument("--normalize", action="store_true", help="Enable text normalization")
    batch_parser.add_argument(
        "--format",
        type=str,
        choices=["wav", "mp3"],
        default="wav",
        help="Output audio format (default: wav)",
    )
    batch_parser.add_argument(
        "--engine",
        type=str,
        choices=["voxcpm2", "indextts2"],
        default=None,
        help="TTS engine to use (default: auto)",
    )
    batch_parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume mode: skip items whose output file already exists and is non-empty",
    )
    _add_model_args(batch_parser)
    _add_lora_args(batch_parser)

    # 旧版根级参数（向后兼容）
    parser.add_argument("--input", "-i", help="Input text file (batch mode only)")
    parser.add_argument("--output-dir", "-od", help="Output directory (batch mode only)")
    _add_common_generation_args(parser)
    parser.add_argument("--output", "-o", help="Output audio file path (single or clone mode)")
    _add_prompt_reference_args(parser)
    _add_model_args(parser)
    _add_lora_args(parser)

    return parser


def _dispatch_legacy(args, parser):
    """分发旧版（无子命令）CLI 调用（内部函数）。

    根据根级参数自动判断使用哪个子命令：
        - --input 指定：进入批量模式（需同时指定 --output-dir）
        - 使用 prompt/reference 音频：进入克隆模式
        - 其他情况：进入语音设计模式

    Args:
        args: argparse 解析后的命名空间对象。
        parser: argparse.ArgumentParser 实例。

    Raises:
        SystemExit: 参数组合冲突或必需参数缺失时终止程序。
    """
    warn_legacy_mode()

    if args.input and args.text:
        parser.error("Use either batch mode (--input) or single mode (--text), not both.")

    if args.input:
        if not args.output_dir:
            parser.error("Batch mode requires --output-dir")
        return cmd_batch(args, parser)

    if not args.text or not args.output:
        parser.error("Single-sample legacy mode requires --text and --output")

    if args.prompt_audio or args.prompt_text or args.prompt_file or args.reference_audio:
        return cmd_clone(args, parser)

    return cmd_design(args, parser)


# -----------------------------
# Entrypoint
# -----------------------------


def main():
    """CLI 主入口函数。

    执行流程：
        1. 构建参数解析器（_build_parser）
        2. 解析命令行参数
        3. 校验数值参数范围（validate_ranges）
        4. 根据 command 字段分发到对应子命令处理函数
        5. 若无子命令（旧版用法），调用 _dispatch_legacy 自动分发
    """
    # P2: CLI 品牌化 — stderr 输出版本归属，增加剥离成本
    # 版本单一来源为 pyproject.toml（pip install -e . 后由 importlib.metadata 读取），
    # 未安装（源码直跑）时降级为 dev，避免横幅硬编码版本与实际发布漂移。
    import sys as _sys

    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _pkg_version

        _ver = _pkg_version("tts-multimodel")
    except PackageNotFoundError:
        _ver = "dev"

    _sys.stderr.write(
        f"TTS_MultiModel CLI v{_ver} © ReSerendipity, Apache 2.0\n"
        "Official: https://github.com/ReSerendipity/TTS_MultiModel\n"
        "⚠️  Legal: 请勿用于诈骗、伪造身份等非法活动。\n"
        f"   $schema: https://github.com/ReSerendipity/TTS_MultiModel/{_ver}/schema/output.json\n"
    )
    _sys.stderr.flush()

    # CLI 直跑时没有应用层的 logging 配置，logger.info 全部被丢弃（batch 进度、
    # 加载状态等用户看不到）。这里给 root logger 装一个 stderr handler：
    # 已有 handler 时 basicConfig 是 no-op，不会覆盖引擎自身的日志配置。
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=_sys.stderr)

    # --json 允许出现在任意位置（含子命令之后）：argparse 的全局选项默认只能放在
    # 子命令前，但 AI agent 自然会把 --json 写在末尾。这里先从 argv 中提取并剔除，
    # 再交给 argparse 解析，避免在每个子解析器重复声明造成 dest 默认值互相覆盖。
    argv = list(sys.argv[1:])
    as_json = "--json" in argv
    if as_json:
        argv = [a for a in argv if a != "--json"]

    parser = _build_parser()

    # --server / --no-remote 同样允许写在子命令之后：沿用 --json 的 argv 预提取方案
    from integrated_app.cli_remote import extract_transport_args

    server_arg, no_remote_arg, argv, transport_error = extract_transport_args(argv)
    if transport_error:
        parser.error(transport_error)

    args = parser.parse_args(argv)

    # 预提取结果回填命名空间（argparse 只能解析位于子命令之前的全局参数）
    if server_arg is not None:
        args.server = server_arg
    if no_remote_arg:
        args.no_remote = True

    # JSON 模式：隔离第三方库的 stdout 输出（如 funasr 的版本横幅），
    # 保证 stdout 只有一行合法 JSON，日志/横幅全部走 stderr。
    if as_json:
        from integrated_app.cli_output import begin_json_mode

        begin_json_mode()

    validate_ranges(args, parser)

    # ---- generate 命令族：统一服务层 / 各引擎（ADR-0005 P0/P1）----
    if args.command == "generate":
        from integrated_app import cli_service, cli_service_ext
        from integrated_app.cli_output import emit

        sub = getattr(args, "generate_command", None)
        if sub is None:
            parser.error("`generate` requires a subcommand (use --help to list them)")

        text_required = {
            "design",
            "clone",
            "ultimate",
            "prompt-continue",
            "emotion",
            "breeze-design",
            "breeze-clone",
            "breeze-direction",
            "edge",
        }
        if sub in text_required and not getattr(args, "text", None):
            parser.error(f"`generate {sub}` requires --text")

        # ---- 传输层选择：发现常驻服务时把有模型加载成本的命令改走 HTTP ----
        # 无服务 / 非 HTTP 命令 / --no-remote 时静默走下方直调分支（默认行为不变）。
        from integrated_app import cli_remote

        remote = None
        if sub in cli_remote.HTTP_CAPABLE_SUBS:
            remote = cli_remote.remote_for_args(args)
        if remote is not None and cli_remote.supports_remote(args, sub):
            from integrated_app.cli_output import emit

            return emit(cli_remote.run_generate(args, parser, sub, remote), as_json=as_json)

        if sub == "design":
            result = _dispatch_result(lambda a: cli_service.run_design(a, parser), args, command=sub)
        elif sub == "clone":
            if not getattr(args, "reference_audio", None):
                parser.error("`generate clone` requires --reference-audio")
            result = _dispatch_result(lambda a: cli_service.run_clone(a, parser), args, command=sub)
        elif sub == "ultimate":
            result = _dispatch_result(lambda a: cli_service_ext.run_ultimate(a, parser), args, command=sub)
        elif sub == "prompt-continue":
            result = _dispatch_result(lambda a: cli_service_ext.run_prompt_continue(a, parser), args, command=sub)
        elif sub == "script":
            result = _dispatch_result(lambda a: cli_service_ext.run_script(a, parser), args, command=sub)
        elif sub == "emotion":
            result = _dispatch_result(lambda a: cli_service_ext.run_emotion(a, parser), args, command=sub)
        elif sub == "breeze-design":
            result = _dispatch_result(lambda a: cli_service_ext.run_breeze(a, parser, "design"), args, command=sub)
        elif sub == "breeze-clone":
            result = _dispatch_result(lambda a: cli_service_ext.run_breeze(a, parser, "clone"), args, command=sub)
        elif sub == "breeze-direction":
            result = _dispatch_result(lambda a: cli_service_ext.run_breeze(a, parser, "direction"), args, command=sub)
        elif sub == "edge":
            result = _dispatch_result(lambda a: cli_service_ext.run_edge(a, parser), args, command=sub)
        elif sub == "convert":
            result = _dispatch_result(lambda a: cli_service_ext.run_convert(a, parser), args, command=sub)
        elif sub == "edit":
            result = _dispatch_result(lambda a: cli_service_ext.run_edit(a, parser), args, command=sub)
        else:
            parser.error(f"Unknown generate subcommand: {sub}")
        return emit(result, as_json=as_json)

    # ---- 顶层后处理：纯音频处理，不需要生成模型 ----
    if args.command == "postprocess":
        from integrated_app import cli_service_ext
        from integrated_app.cli_output import emit

        return emit(
            _dispatch_result(lambda a: cli_service_ext.run_postprocess(a, parser), args, command="postprocess"),
            as_json=as_json,
        )

    # ---- 音色库 / 历史记录（ADR-0005 P2）----
    if args.command in ("persona", "history"):
        from integrated_app import cli_service_data
        from integrated_app.cli_output import emit

        sub = getattr(args, f"{args.command}_command", None)
        if sub is None:
            parser.error(f"`{args.command}` requires a subcommand (use --help to list them)")

        handlers = {
            ("persona", "list"): cli_service_data.cmd_persona_list,
            ("persona", "show"): cli_service_data.cmd_persona_show,
            ("persona", "save"): cli_service_data.cmd_persona_save,
            ("persona", "delete"): cli_service_data.cmd_persona_delete,
            ("history", "list"): cli_service_data.cmd_history_list,
            ("history", "stats"): cli_service_data.cmd_history_stats,
            ("history", "hide"): cli_service_data.cmd_history_hide,
            ("history", "clear"): cli_service_data.cmd_history_clear,
            ("history", "recycle"): cli_service_data.cmd_history_recycle,
            ("history", "restore"): cli_service_data.cmd_history_restore,
            ("history", "purge"): cli_service_data.cmd_history_purge,
            ("history", "export"): cli_service_data.cmd_history_export,
            ("history", "sync"): cli_service_data.cmd_history_sync,
        }
        handler = handlers.get((args.command, sub))
        if handler is None:
            parser.error(f"Unknown {args.command} subcommand: {sub}")
        return emit(_dispatch_result(handler, args, command=f"{args.command}-{sub}"), as_json=as_json)

    # ---- 模型 / 引擎 / 下载 / LoRA / 训练（ADR-0005 P3）----
    if args.command in ("model", "engines", "download", "lora", "train"):
        from integrated_app import cli_service_ops
        from integrated_app.cli_output import emit

        sub = getattr(args, f"{args.command}_command", None)
        if sub is None:
            parser.error(f"`{args.command}` requires a subcommand (use --help to list them)")

        handlers = {
            ("model", "status"): cli_service_ops.cmd_model_status,
            ("model", "load"): cli_service_ops.cmd_model_load,
            ("model", "unload"): cli_service_ops.cmd_model_unload,
            ("model", "switch"): cli_service_ops.cmd_model_switch,
            ("engines", "list"): cli_service_ops.cmd_engines_list,
            ("engines", "info"): cli_service_ops.cmd_engines_info,
            ("download", "catalog"): cli_service_ops.cmd_download_catalog,
            ("download", "status"): cli_service_ops.cmd_download_status,
            ("download", "start"): cli_service_ops.cmd_download_start,
            ("download", "uninstall"): cli_service_ops.cmd_download_uninstall,
            ("lora", "list"): cli_service_ops.cmd_lora_list,
            ("lora", "load"): cli_service_ops.cmd_lora_load,
            ("lora", "unload"): cli_service_ops.cmd_lora_unload,
            ("lora", "toggle"): cli_service_ops.cmd_lora_toggle,
            ("lora", "state"): cli_service_ops.cmd_lora_state,
            ("train", "start"): cli_service_ops.cmd_train_start,
            ("train", "stop"): cli_service_ops.cmd_train_stop,
            ("train", "log"): cli_service_ops.cmd_train_log,
        }
        handler = handlers.get((args.command, sub))
        if handler is None:
            parser.error(f"Unknown {args.command} subcommand: {sub}")
        return emit(_dispatch_result(handler, args, command=f"{args.command}-{sub}"), as_json=as_json)

    # ---- serve：完整 HTTP 服务（ADR-0005 P4，阻塞运行）----
    if args.command == "serve":
        from integrated_app.cli_service_system import cmd_serve

        return cmd_serve(args)

    # ---- 系统运维（ADR-0005 P4）----
    if args.command == "system":
        from integrated_app import cli_service_system
        from integrated_app.cli_output import emit

        sub = getattr(args, "system_command", None)
        if sub is None:
            parser.error("`system` requires a subcommand (health | gpu | logs | settings)")

        system_handlers = {
            "health": cli_service_system.cmd_system_health,
            "gpu": cli_service_system.cmd_system_gpu,
            "logs": cli_service_system.cmd_system_logs,
            "settings": cli_service_system.cmd_system_settings,
        }
        handler = system_handlers.get(sub)
        if handler is None:
            parser.error(f"Unknown system subcommand: {sub}")
        return emit(_dispatch_result(handler, args, command=f"system-{sub}"), as_json=as_json)

    # ---- 顶层 design/clone：默认 legacy（保护现有行为），--service 走服务层 ----
    if args.command == "design":
        if not args.text:
            parser.error("`design` requires --text")
        if getattr(args, "service", False) is True:
            from integrated_app.cli_output import emit
            from integrated_app.cli_service import run_design

            return emit(_dispatch_result(lambda a: run_design(a, parser), args, command="design"), as_json=as_json)
        return cmd_design(args, parser)

    if args.command == "clone":
        if not args.text or not args.output:
            parser.error("`clone` requires --text and --output")
        if getattr(args, "service", False) is True:
            from integrated_app.cli_output import emit
            from integrated_app.cli_service import run_clone

            return emit(_dispatch_result(lambda a: run_clone(a, parser), args, command="clone"), as_json=as_json)
        return cmd_clone(args, parser)

    if args.command == "batch":
        # ---- 传输层选择：发现常驻服务时逐条走 HTTP（收益 = 条数 × 单次加载时间）----
        # 解析/校验逻辑复用本模块现有函数（cmd_batch 的同名前置步骤），
        # --resume 跳过判定由 cli_remote.run_batch_tasks 保持一致。
        from integrated_app import cli_remote

        remote = cli_remote.remote_for_args(args)
        if (
            remote is not None
            and not getattr(args, "prompt_audio", None)
            and not getattr(args, "reference_audio", None)
            and not getattr(args, "control", None)
        ):
            # HTTP 的 voxcpm_design 端点强制要求音色描述（引擎 ValidationError），
            # 而 legacy 直调 batch 无 --control 时用 VoxCPM 默认音色——语义不同，
            # 该场景回落直调，保持默认行为完全不变。
            remote = None
        if remote is not None:
            input_file = require_file_exists(args.input, parser, "input file")
            Path(args.output_dir).mkdir(parents=True, exist_ok=True)
            tasks = _parse_batch_input(input_file, args, parser)
            if not tasks:
                sys.exit("Error: Input file is empty or contains no valid tasks")
            prompt_text = validate_batch_args(args, parser)
            return cli_remote.run_batch_tasks(args, tasks, prompt_text, remote)
        return cmd_batch(args, parser)

    return _dispatch_legacy(args, parser)


if __name__ == "__main__":
    # 必须用 sys.exit 传出语义化退出码：桌面 shim 以 `python cli.py` 直跑，
    # 若只调用 main() 而不取返回值，失败场景会错误地以 0 退出，
    # AI agent 无法据退出码判断成功/失败。
    sys.exit(main())
