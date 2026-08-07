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
from voxcpm.core import VoxCPM

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

    提醒用户优先使用子命令语法（voxcpm design|clone|batch）而不是
    根级参数，根级参数将在未来版本中移除。
    """
    logger.warning("Legacy root CLI arguments are deprecated. Prefer `voxcpm design|clone|batch ...`.")


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


def load_model(args) -> VoxCPM:
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
    start_time = time.time()

    logger.info(f"\n{'=' * 50}")
    logger.info(f"Batch processing: {total} tasks | Format: {output_format.upper()} | Engine: {engine or 'auto'}")
    logger.info(f"{'=' * 50}\n")

    for i, task in enumerate(tasks, 1):
        text = task["text"]
        control = task.get("control") or args.control
        task_output_name = task.get("output")

        try:
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

            # 确定输出文件名
            if task_output_name:
                out_name = task_output_name
                if not out_name.endswith(f".{output_format}"):
                    out_name = f"{out_name}.{output_format}"
            else:
                out_name = f"output_{i:03d}.{output_format}"

            output_file = output_dir / out_name
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
        f"Batch complete: {success_count}/{total} succeeded, {fail_count} failed "
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


def _build_parser():
    """构建并返回完整的 argparse 参数解析器。

    解析器结构：
        - 子命令：design / clone / batch（推荐用法）
        - 根级参数：兼容旧版 CLI 的遗留参数（会触发弃用警告）

    Returns:
        argparse.ArgumentParser: 配置完成的参数解析器实例。
    """
    parser = argparse.ArgumentParser(
        description="VoxCPM CLI - Multi-engine voice design, cloning, and batch processing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  voxcpm design --text "Hello world" --output out.wav
  voxcpm design --text "Hello world" --control "warm female voice" --output out.wav
  voxcpm clone --text "Hello" --reference-audio ref.wav --output out.wav
  voxcpm batch --input texts.txt --output-dir ./outs --reference-audio ref.wav
        """,
    )

    subparsers = parser.add_subparsers(dest="command")

    design_parser = subparsers.add_parser("design", help="Generate speech with voice design")
    _add_common_generation_args(design_parser)
    _add_prompt_reference_args(design_parser)
    _add_model_args(design_parser)
    _add_lora_args(design_parser)
    design_parser.add_argument("--output", "-o", required=True, help="Output audio file path")

    clone_parser = subparsers.add_parser("clone", help="Clone a voice with reference/prompt audio")
    _add_common_generation_args(clone_parser)
    _add_prompt_reference_args(clone_parser)
    _add_model_args(clone_parser)
    _add_lora_args(clone_parser)
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
    import sys as _sys

    _sys.stderr.write(
        "TTS_MultiModel CLI v2.1.0 © ReSerendipity, Apache 2.0\n"
        "Official: https://github.com/ReSerendipity/TTS_MultiModel\n"
        "⚠️  Legal: 请勿用于诈骗、伪造身份等非法活动。\n"
        "   $schema: https://github.com/ReSerendipity/TTS_MultiModel/v2.1.0/schema/output.json\n"
    )
    _sys.stderr.flush()

    parser = _build_parser()
    args = parser.parse_args()

    validate_ranges(args, parser)

    if args.command == "design":
        if not args.text:
            parser.error("`design` requires --text")
        return cmd_design(args, parser)

    if args.command == "clone":
        if not args.text or not args.output:
            parser.error("`clone` requires --text and --output")
        return cmd_clone(args, parser)

    if args.command == "batch":
        return cmd_batch(args, parser)

    return _dispatch_legacy(args, parser)


if __name__ == "__main__":
    main()
