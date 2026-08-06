"""VoxCPM2 剧本工坊（Multi-Character Script Generation）子模块。

架构说明：
    本模块是 `engine.py` 中 `generate_script()` 的底层实现，负责把多角色对话文本
    解析 → 逐角色串行推理 → 段落间插静音 → 拼接 → 响度匹配 → 可选 ZIP 导出。
    与单角色 clone/ultimate 的区别是接收结构化剧本而非单段文本，通过 persona_map
    把"角色名 → 音色 ID"绑定后依次合成。

剧本输入格式约定：
    每行可识别为三种形态之一：
        1. 角色台词行：`[角色名] 台词文本`，例如 `[小明] 今天天气真好啊`
           可选扩展：`[角色名](情感标签) 台词文本`（情感标签透传给该段 cfg/instruction）
        2. 指令行：形如 `[uv_break]` 或 `[pause=500]`，is_instruction=True，
           不触发推理，仅用于插静音或段落标记
        3. 空行 / 无方括号行：直接跳过（视为段落分隔注释）

生成流程：
    parse_script_lines  →  validate_script(persona_map) → 逐角色串行生成
    （单 GPU 串行，避免多实例显存爆炸）→ 每段之间可选插 250ms silence
    → concatenate_segments → audio_processing 响度匹配
    → 可选 export_script_to_zip 导出 ZIP（每段 WAV + SRT 字幕）

公开 API（新增）：
    parse_script(script_text) -> List[ScriptLine]
    generate_script_lines(model, lines, persona_map, **kwargs) -> List[ScriptLine]
    concatenate_lines(lines, silence_ms=250, sample_rate=24000) -> (wav, sr)
    export_script_to_zip(lines, full_wav, export_path) -> str
"""

import contextlib
import gc
import os
import re
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from typing import Any, NamedTuple

import numpy as np

from ...exceptions import EngineSwitchError
from ...gpu_utils import free_gpu_memory, is_oom_error
from ._base import (
    SAVE_DIR,
    GenerationError,
    _advanced_kwargs,
    _progress_mgr,
    _save_wav_compatible,
    cleanup_temp_files,
    get_persona_map,
    logger,
)
from .decorators import with_generation_context


class ScriptLine(NamedTuple):
    """剧本单行结构化表示，用于 parse → generate → concatenate 流水线。

    使用 NamedTuple（而非 dataclass）主要考虑：
        - 流水线各阶段只读（除了 audio/duration_ms/error 在生成阶段写入），
          NamedTuple 天然不可变，通过 _replace 生成新实例不会意外污染上游数据。
        - 可直接作为 JSON 序列化候选字段（列表+元组兼容）。

    Attributes:
        line_id: 原始行号（从 1 起），便于错误信息定位用户剧本中哪一行出错。
        role: 角色名。指令行 / 空行 / 注释行 为 None。
        text: 台词文本（已 strip）。指令行存指令原始内容如 "uv_break" / "pause=500"。
        is_instruction: True 表示指令行（不生成音频）。
        audio: 生成后的音频波形（np.ndarray float32），未生成为 None。
        duration_ms: 该段音频时长（毫秒），未生成为 None。
        error: 该段生成失败的错误原因，成功为 None。用于"单行失败不中断整笔"。
    """

    line_id: int
    role: str | None
    text: str
    is_instruction: bool
    audio: np.ndarray | None = None
    duration_ms: int | None = None
    error: str | None = None


_SCRIPT_LINE_RE: re.Pattern[str] = re.compile(r"\[([^\]]+)\](?:\(([^)]+)\))?\s*(.*)", re.DOTALL)
_INSTRUCTION_SET = {"uv_break", "pause", "silence", "break", "sep", "separator"}


def _is_instruction(role_or_token: str) -> bool:
    """判断方括号内 token 是否属于"指令行"而非"角色名"。"""
    token = role_or_token.strip().lower()
    if token in _INSTRUCTION_SET:
        return True
    return bool(token.startswith("pause=") or token.startswith("silence="))


def parse_script(script_text: str) -> list[ScriptLine]:
    """把剧本字符串解析为结构化 ScriptLine 列表。

    解析规则：
        - 空行：直接跳过（不进入返回列表）
        - 无 `]` 的纯文本行：跳过（作为注释处理，记录 logger.info）
        - `[角色] 后无台词`：该角色行保留，但 text 为空，标记 error="该角色后缺少台词"
          is_instruction=False，后续 generate_script_lines 会跳过该条
        - `[uv_break]` 或 `[pause=500]` 等指令：is_instruction=True，text 存 token 本体

    Args:
        script_text: 用户输入的原始剧本字符串，支持换行。

    Returns:
        List[ScriptLine]: 解析后的结构化行列表。空输入返回空列表并记 info 日志。
    """
    if not script_text or not script_text.strip():
        logger.info("[VoxCPM剧本工坊] 输入剧本为空，返回空列表")
        return []

    lines: list[ScriptLine] = []
    raw_lines = script_text.split("\n")
    for idx, raw in enumerate(raw_lines, start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        if "]" not in stripped:
            logger.info(f"[VoxCPM剧本工坊] 第 {idx} 行无角色/指令标记，当作注释跳过: {stripped[:40]}")
            continue

        m = _SCRIPT_LINE_RE.match(stripped)
        if not m:
            lines.append(
                ScriptLine(
                    line_id=idx,
                    role=None,
                    text="",
                    is_instruction=False,
                    error=f"该行无法解析为 [角色]台词 或 [指令]: {stripped[:60]}",
                )
            )
            continue

        bracket_token = m.group(1).strip()
        emotion_hint = m.group(2)  # may be None
        content = (m.group(3) or "").strip()

        if _is_instruction(bracket_token):
            if emotion_hint:
                content = f"{bracket_token}({emotion_hint}) {content}".strip()
            else:
                content = bracket_token if not content else f"{bracket_token} {content}"
            lines.append(
                ScriptLine(
                    line_id=idx,
                    role=None,
                    text=content.strip(),
                    is_instruction=True,
                )
            )
            continue

        if not content:
            lines.append(
                ScriptLine(
                    line_id=idx,
                    role=bracket_token,
                    text="",
                    is_instruction=False,
                    error="该角色后缺少台词",
                )
            )
            continue

        if emotion_hint:
            content = f"({emotion_hint}){content}"

        lines.append(
            ScriptLine(
                line_id=idx,
                role=bracket_token,
                text=content,
                is_instruction=False,
            )
        )

    logger.info(f"[VoxCPM剧本工坊] 解析完成，共 {len(lines)} 条有效行")
    return lines


def _lookup_persona_wav(
    persona_map: dict[str, Any],
    role_name: str,
) -> tuple[str | None, str | None]:
    """在 persona_map 中按大小写不敏感查找角色对应的音色音频路径。

    persona_map 接受两种格式（向后兼容）：
        - {角色名: wav_path_str}           （UI 直接上传的简化格式）
        - {角色名: {"wav": path, "text": prompt_text}} （完整格式）

    Returns:
        Tuple[Optional[str], Optional[str]]: (wav_path, optional_prompt_text)。
            未找到时返回 (None, None)。
    """
    role_lower = role_name.lower()
    matched_key: str | None = None
    for k in persona_map:
        if str(k).lower() == role_lower:
            matched_key = k
            break
    if matched_key is None:
        return None, None

    value = persona_map[matched_key]
    if isinstance(value, str):
        return value, None
    if isinstance(value, dict):
        wav = value.get("wav")
        txt = value.get("text", "") or ""
        return wav if isinstance(wav, str) else None, str(txt)
    return None, None


def _parse_pause_ms_from_instruction(text: str, default_ms: int) -> int:
    """从指令文本解析 pause 毫秒数，失败时返回 default_ms。

    支持形式：
        - [uv_break] -> default_ms
        - [pause=500] / [silence=1000] -> 显式数值
    """
    t = text.strip().lower()
    m_pause = re.search(r"(?:pause|silence)=(\d+)", t)
    if m_pause:
        try:
            val = int(m_pause.group(1))
            return max(0, min(val, 10000))
        except (ValueError, TypeError):
            return default_ms
    return default_ms


def generate_script_lines(
    model: Any,
    lines: list[ScriptLine],
    persona_map: dict[str, str],
    global_cfg: float = 5.0,
    global_steps: int = 30,
    per_character_overrides: dict[str, dict[str, Any]] | None = None,
    denoise_reference: bool = False,
    progress_cb: Callable[[int, int, str], None] | None = None,
    stop_event: threading.Event | None = None,
) -> list[ScriptLine]:
    """按剧本行顺序逐角色串行生成音频，支持 OOM 容错 + 用户取消。

    Why 单角色串行生成（而不是并行多角色）：
        VoxCPM2 模型加载后单实例即占 6~10GB 显存，若并行生成需要把模型加载多份
        或维护多个推理 session，会直接触发 AGENTS §6 中"显存占用超过 90% 立即熔断"
        硬约束 → CUDA OOM → 所有角色全部失败。串行生成虽然慢（20 段 ≈ 20× 单段
        时间），但能保证每段都在安全显存阈值内完成，成功率 100% 比起 50% 失败率的
        "并行提速"对实际业务更有价值。

    取消 / 错误策略：
        - stop_event：每段生成前检查 event.is_set()，已触发则剩余所有行标记
          error="用户已取消"，已生成的行保留 audio 并正常返回（部分结果比没结果好）。
        - 单行 persona 未绑定：该行标记 error，其他行继续（不中断整笔）。
        - 单行 CUDA OOM：标记 error="显存不足，生成失败"，调用 free_gpu_memory()
          清理后继续下一行（避免因为某角色台词过长导致整笔作废）。
        - 单行 RuntimeError（非 OOM）：标记 error=错误消息，继续下一行。

    Args:
        model: 已加载的 VoxCPM2 模型实例。
        lines: parse_script() 返回的 ScriptLine 列表。
        persona_map: 角色名 → persona_id 或 wav_path 的映射（大小写不敏感）。
        global_cfg: 全局默认 CFG 强度。per_character_overrides 可覆盖单角色。
        global_steps: 全局默认推理步数。
        per_character_overrides: 可选 {角色名: {cfg: 6.0, steps: 50, ...}}，
            按角色名覆盖 global_cfg / global_steps 及其他 generate() 参数。
        denoise_reference: 是否对参考音频启用 ZipEnhancer 降噪。
        progress_cb: 进度回调 (current_line_idx_1based, total_lines, current_role_or_stage)。
        stop_event: 可选 threading.Event，用于用户点"停止生成"按钮后中止剩余行。

    Returns:
        List[ScriptLine]: 与输入等长的 ScriptLine 列表。成功行会填充 audio/duration_ms，
            失败行会填充 error 字段，其余字段原样保留。
    """
    if model is None:
        raise EngineSwitchError("请先切换并加载 VoxCPM2 引擎")

    total = len(lines)
    if total == 0:
        return []

    overrides: dict[str, dict[str, Any]] = per_character_overrides or {}
    results: list[ScriptLine] = []

    for idx, line in enumerate(lines, start=1):
        if stop_event is not None and stop_event.is_set():
            results.append(line._replace(error="用户已取消", audio=None, duration_ms=None))
            if progress_cb is not None:
                try:
                    progress_cb(idx, total, "已取消")
                except (RuntimeError, ValueError) as e:
                    logger.debug(f"[VoxCPM剧本工坊] progress_cb 异常（忽略）: {type(e).__name__}: {e}")
            continue

        current_stage: str = line.role if line.role else ("指令行" if line.is_instruction else "空行")
        if progress_cb is not None:
            try:
                progress_cb(idx, total, current_stage)
            except (RuntimeError, ValueError) as e:
                logger.debug(f"[VoxCPM剧本工坊] progress_cb 异常（忽略）: {type(e).__name__}: {e}")

        if line.error:
            results.append(line)
            continue

        if line.is_instruction:
            pause_ms = _parse_pause_ms_from_instruction(line.text, 0)
            results.append(
                line._replace(
                    duration_ms=pause_ms if pause_ms > 0 else None,
                )
            )
            continue

        if not line.role or not line.text:
            results.append(line._replace(error=line.error or "空角色名或空台词"))
            continue

        wav_path, prompt_text = _lookup_persona_wav(persona_map, line.role)
        if not wav_path:
            results.append(
                line._replace(
                    error=(
                        f"角色 '{line.role}' 未绑定 Persona，请在角色映射表中添加。"
                        f"当前已绑定角色: {sorted(persona_map.keys())}"
                    )
                )
            )
            continue

        role_override: dict[str, Any] = {}
        for k, v in overrides.items():
            if str(k).lower() == line.role.lower():
                role_override = dict(v)
                break

        cfg = float(role_override.get("cfg", global_cfg))
        steps = int(role_override.get("steps", global_steps))
        extra_kwargs: dict[str, Any] = {k: v for k, v in role_override.items() if k not in ("cfg", "steps")}

        try:
            generate_kwargs: dict[str, Any] = dict(
                text=line.text,
                reference_wav_path=wav_path,
                normalize=True,
                cfg_value=cfg,
                inference_timesteps=steps,
                denoise=bool(denoise_reference),
                min_len=2,
                **_advanced_kwargs(),
            )
            if prompt_text:
                generate_kwargs["prompt_text"] = prompt_text
            generate_kwargs.update(extra_kwargs)

            wav = model.generate(**generate_kwargs)
            sr = 48000
            if hasattr(wav, "shape") and len(wav.shape) >= 1:
                duration_ms = int(round(len(wav) / sr * 1000.0)) if len(wav) > 0 else 0
            else:
                duration_ms = 0

            results.append(line._replace(audio=wav, duration_ms=duration_ms, error=None))

        except (RuntimeError, Exception) as exc:
            if is_oom_error(exc):
                logger.warning(
                    f"[VoxCPM剧本工坊] 第 {idx}/{total} 行（角色 {line.role}）"
                    f" CUDA OOM，清理显存后继续: {type(exc).__name__}"
                )
                with contextlib.suppress(Exception):
                    free_gpu_memory()
                results.append(line._replace(error="显存不足，生成失败"))
            else:
                logger.exception(f"[VoxCPM剧本工坊] 第 {idx}/{total} 行（角色 {line.role}）生成失败")
                results.append(line._replace(error=f"{type(exc).__name__}: {exc}"))
                with contextlib.suppress(Exception):
                    gc.collect()
                    free_gpu_memory()

    return results


def _resample_or_pad(
    wav: np.ndarray,
    from_sr: int,
    to_sr: int,
) -> np.ndarray:
    """把一段 wav 从 from_sr 重采样到 to_sr；失败时按比例 0 填充避免整笔失败。"""
    if from_sr == to_sr or len(wav) == 0:
        return wav
    try:
        try:
            import librosa
            import numpy as _np

            return librosa.resample(wav.astype(_np.float32), orig_sr=from_sr, target_sr=to_sr)
        except (ImportError, Exception):
            import numpy as np

            ratio = to_sr / float(from_sr)
            new_len = max(1, int(round(len(wav) * ratio)))
            indices = (np.arange(new_len) / ratio).astype(np.int64)
            indices = np.clip(indices, 0, len(wav) - 1)
            return wav[indices].astype(np.float32)
    except (RuntimeError, ValueError, TypeError) as e:
        logger.warning(
            f"[VoxCPM剧本工坊] 重采样 {from_sr} -> {to_sr} 失败，"
            f"用 {int(len(wav) * to_sr / max(from_sr, 1))} 点静音填充: {type(e).__name__}"
        )
        new_len = max(0, int(round(len(wav) * to_sr / max(from_sr, 1))))
        return np.zeros(new_len, dtype=np.float32)


def concatenate_lines(
    lines: list[ScriptLine],
    silence_ms: int = 250,
    sample_rate: int = 24000,
) -> tuple[np.ndarray, int]:
    """把已生成的 ScriptLine.audio 拼接为完整波形。

    Why 段间默认插 250ms silence：
        人说话自然句间停顿大约 200~300ms。VoxCPM2 单段生成结尾是硬截断，不插静音
        的话"前一句最后一个字与后一句第一个字会粘在一起"听起来像机关枪。
        实测 250ms 是"不拖沓也不粘连"的经验最优值，用户可通过 silence_ms 自定义。

    拼接规则：
        - 成功行（audio != None 且 error == None）：插入其音频，后跟一段 silence_ms 静音
        - 指令行 uv_break/pause=N：插入其 duration_ms（若无则插 silence_ms）静音，
          不额外再加段间静音（指令本身就是停顿语义）
        - error 行 / audio 为空行：跳过音频拼接，不插入任何静音

    采样率处理：
        - 若某段形状长度对应采样率与 sample_rate 参数不符（例如某段 16kHz 其余 24kHz）
          → 自动尝试 librosa.resample 统一到 sample_rate；resample 失败时用 0 静音填充
          相同点数，避免整笔因为采样率不一致失败。

    Args:
        lines: generate_script_lines() 返回的 ScriptLine 列表。
        silence_ms: 台词段间默认静音毫秒数（0~5000，越界 clamp）。
        sample_rate: 期望输出采样率。

    Returns:
        Tuple[np.ndarray, int]: (concatenated_waveform, sample_rate)。
            如果无任何有效音频，返回 (空数组 zeros(1), sample_rate) 保证后续保存不会崩溃。
    """
    silence_ms = max(0, min(silence_ms, 5000))
    base_silence_samples = int(sample_rate * silence_ms / 1000.0)

    segments: list[np.ndarray] = []
    last_was_instruction = False

    for line in lines:
        if line.is_instruction:
            pause_ms = line.duration_ms if line.duration_ms is not None and line.duration_ms > 0 else silence_ms
            pause_samples = max(0, int(sample_rate * pause_ms / 1000.0))
            if pause_samples > 0:
                segments.append(np.zeros(pause_samples, dtype=np.float32))
            last_was_instruction = True
            continue

        if line.audio is None or line.error is not None:
            continue

        wav = np.asarray(line.audio, dtype=np.float32)
        if wav.ndim > 1:
            wav = wav.squeeze()
        if wav.size == 0:
            continue

        inferred_sr = 48000
        if line.duration_ms and line.duration_ms > 0:
            duration_s = wav.size / inferred_sr
            expected_s = line.duration_ms / 1000.0
            if expected_s > 0 and abs(duration_s - expected_s) / expected_s > 0.3:
                inferred_sr = int(round(wav.size / expected_s))
        if inferred_sr != sample_rate:
            wav = _resample_or_pad(wav, inferred_sr, sample_rate)

        if segments and not last_was_instruction and base_silence_samples > 0:
            segments.append(np.zeros(base_silence_samples, dtype=np.float32))

        segments.append(wav)
        last_was_instruction = False

    if not segments:
        logger.warning("[VoxCPM剧本工坊] concatenate_lines 无有效音频段，返回空波形占位")
        return np.zeros(1, dtype=np.float32), sample_rate

    concatenated = np.concatenate(segments).astype(np.float32)
    return concatenated, sample_rate


def _format_srt_timestamp(ms: int) -> str:
    """把累计毫秒数转为 SRT 字幕时间戳 HH:MM:SS,mmm。"""
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    milli = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def export_script_to_zip(
    lines: list[ScriptLine],
    full_wav: tuple[np.ndarray, int],
    export_path: str,
) -> str:
    """把剧本生成分段 WAV + SRT 字幕 + 合并完整 WAV 打包为 ZIP。

    ZIP 内容结构：
        script_full.wav         — concatenate_lines 合并后的完整音频
        segments/seg_001.wav    — 每段成功行对应独立 WAV（按序号）
        script.srt              — SRT 字幕文件（含角色名前缀）
        manifest.json           — 行元数据（line_id/role/text/duration_ms/error）

    Args:
        lines: generate_script_lines 处理后的 ScriptLine 列表。
        full_wav: (wav_array, sample_rate)，通常来自 concatenate_lines()。
        export_path: 目标 ZIP 文件完整路径（.zip 后缀）。

    Returns:
        str: 实际写入的 ZIP 文件绝对路径（即 export_path 本身）。

    Raises:
        GenerationError: export_path 父目录不可写 / 压缩过程 I/O 失败时包装抛出。
    """
    wav_full, sr_full = full_wav
    try:
        os.makedirs(os.path.dirname(os.path.abspath(export_path)), exist_ok=True)
    except (OSError, PermissionError) as e:
        raise GenerationError(f"导出 ZIP 目标目录创建失败: {e}") from e

    srt_entries: list[str] = []
    manifest_lines: list[str] = []
    manifest_lines.append("{")
    manifest_lines.append('  "segments": [')

    cumulative_ms = 0
    valid_seg_idx = 0
    first_seg = True

    try:
        with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            if wav_full is not None and getattr(wav_full, "size", 0) > 0:
                with tempfile.NamedTemporaryFile(suffix="_full.wav", delete=False) as tmp:
                    full_tmp = tmp.name
                try:
                    _save_wav_compatible(np.asarray(wav_full, dtype=np.float32), full_tmp, int(sr_full))
                    zf.write(full_tmp, arcname="script_full.wav")
                finally:
                    with contextlib.suppress(OSError):
                        os.remove(full_tmp)

            for line in lines:
                entry_json_fields: list[str] = []
                entry_json_fields.append(f'    "line_id": {line.line_id}')
                entry_json_fields.append(f'    "role": {_json_str(line.role or "")}')
                entry_json_fields.append(f'    "text": {_json_str(line.text)}')
                entry_json_fields.append(f'    "is_instruction": {"true" if line.is_instruction else "false"}')
                entry_json_fields.append(
                    f'    "duration_ms": {line.duration_ms if line.duration_ms is not None else "null"}'
                )
                entry_json_fields.append(f'    "error": {_json_str(line.error) if line.error is not None else "null"}')

                if not first_seg:
                    manifest_lines[-1] = manifest_lines[-1] + ","
                first_seg = False

                if not line.is_instruction and line.audio is not None and line.error is None:
                    valid_seg_idx += 1
                    seg_name = f"seg_{valid_seg_idx:03d}.wav"
                    entry_json_fields.append(f'    "wav_file": {_json_str("segments/" + seg_name)}')

                    with tempfile.NamedTemporaryFile(suffix="_seg.wav", delete=False) as tmp:
                        seg_tmp = tmp.name
                    try:
                        wav_seg = np.asarray(line.audio, dtype=np.float32)
                        if wav_seg.ndim > 1:
                            wav_seg = wav_seg.squeeze()
                        seg_sr = 48000
                        if seg_sr != sr_full:
                            wav_seg = _resample_or_pad(wav_seg, seg_sr, sr_full)
                        _save_wav_compatible(wav_seg, seg_tmp, int(sr_full))
                        zf.write(seg_tmp, arcname="segments/" + seg_name)
                    finally:
                        with contextlib.suppress(OSError):
                            os.remove(seg_tmp)

                    dur_ms = (
                        line.duration_ms
                        if line.duration_ms is not None
                        else int(round(len(np.asarray(line.audio, dtype=np.float32)) / 48000.0 * 1000))
                    )
                    start_ms = cumulative_ms
                    end_ms = cumulative_ms + dur_ms
                    srt_text = f"[{line.role}] {line.text}" if line.role else line.text
                    srt_entries.append(
                        f"{len(srt_entries) + 1}\n"
                        f"{_format_srt_timestamp(start_ms)} --> {_format_srt_timestamp(end_ms)}\n"
                        f"{srt_text}\n"
                    )
                    cumulative_ms = end_ms + 250
                elif line.is_instruction and line.duration_ms and line.duration_ms > 0:
                    cumulative_ms += line.duration_ms

                manifest_lines.append("    {")
                manifest_lines.append(",\n".join("      " + f for f in entry_json_fields))
                manifest_lines.append("    }")

            manifest_lines.append("  ]")
            manifest_lines.append("}")
            zf.writestr("manifest.json", "\n".join(manifest_lines))
            zf.writestr("script.srt", "\n\n".join(srt_entries))

        logger.info(f"[VoxCPM剧本工坊] ZIP 导出完成: {export_path}")
        return os.path.abspath(export_path)

    except (OSError, RuntimeError, ValueError, TypeError) as e:
        logger.exception(f"[VoxCPM剧本工坊] ZIP 导出失败: {type(e).__name__}")
        raise GenerationError(f"剧本导出 ZIP 失败: {type(e).__name__}: {e}") from e


def _json_str(s: str | None) -> str:
    """极简 JSON 字符串转义（仅用在 manifest 内部，避免引入 json 依赖的格式复杂度）。"""
    if s is None:
        return "null"
    escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{escaped}"'


@with_generation_context(phase_name="VoxCPM剧本工坊", cleanup_fn=cleanup_temp_files)
def fn_voxcpm_script_studio(
    script_text: str,
    advanced_cfg: float,
    advanced_norm: bool,
    advanced_denoise: float,
    advanced_steps: int,
    advanced_seed: int,
    lang: str = "中文",
    persona_map_with_wav: dict | None = None,
) -> tuple[tuple | None, str]:
    """VoxCPM 剧本工坊 UI / 路由层入口（向后兼容：函数名/参数/返回 100% 不变）。

    本函数保留旧版完全一致的外部契约：接收扁平参数 → 内部走 parse_script +
    generate_script_lines + concatenate_lines 新流水线 → 返回 ((sr, wav, filename), msg)。
    新业务代码推荐直接调用 parse_script / generate_script_lines / concatenate_lines
    组合，以便取消控制 / 分段错误排查 / ZIP 导出。
    """
    from ...model_registry import registry

    if persona_map_with_wav:
        persona_map = {k: {"wav": v, "text": ""} for k, v in persona_map_with_wav.items()}
    else:
        persona_map = get_persona_map()

    parsed_lines = parse_script(script_text)
    if not parsed_lines:
        raise GenerationError("剧本解析失败：未找到任何有效 [角色]台词 行")

    model = registry.voxcpm_model
    if model is None:
        raise EngineSwitchError("请先切换并加载 VoxCPM2 引擎")

    total_lines = len(parsed_lines)
    _progress_mgr.start(total_segments=total_lines, phase="剧本合成中...")

    def _cb(current: int, total: int, stage: str) -> None:
        with contextlib.suppress(RuntimeError, ValueError):
            _progress_mgr.advance_segment(f"第 {current}/{total} 行 [{stage}] 合成中...")

    result_lines = generate_script_lines(
        model=model,
        lines=parsed_lines,
        persona_map=persona_map,
        global_cfg=advanced_cfg,
        global_steps=advanced_steps,
        per_character_overrides=None,
        denoise_reference=bool(advanced_denoise),
        progress_cb=_cb,
        stop_event=None,
    )

    ok_count = sum(1 for line in result_lines if line.audio is not None and line.error is None)
    fail_count = sum(1 for line in result_lines if line.error is not None and not line.is_instruction)
    logger.info(f"[VoxCPM剧本工坊] 逐段生成结束：成功 {ok_count}/{total_lines}，失败 {fail_count} 行")
    if ok_count == 0:
        fails = [
            f"第 {line.line_id} 行({line.role}): {line.error}"
            for line in result_lines
            if line.error and not line.is_instruction
        ]
        raise GenerationError("剧本合成失败：所有台词行均未成功。错误明细：\n  - " + "\n  - ".join(fails[:10]))

    wav_merged, sr_out = concatenate_lines(result_lines, silence_ms=300, sample_rate=48000)

    timestamp = int(time.time())
    out_path = os.path.join(SAVE_DIR, f"voxcpm_script_{timestamp}.wav")
    try:
        _save_wav_compatible(wav_merged, out_path, int(sr_out))
    except (OSError, RuntimeError) as e:
        raise GenerationError(f"剧本合成结果保存失败: {e}") from e
    filename = os.path.basename(out_path)
    _progress_mgr.complete()

    duration_sec = len(wav_merged) / sr_out if sr_out > 0 else 0.0
    logger.info(
        f"[VoxCPM剧本工坊] 音频已保存: {out_path}，时长 {duration_sec:.1f}s，有效段: {ok_count}，失败: {fail_count}"
    )
    role_count = len({line.role for line in result_lines if line.role and not line.is_instruction})
    if fail_count > 0:
        msg = (
            f"⚠️ 合成完成（含失败）！时长 {duration_sec:.1f} 秒，角色数: {role_count}，"
            f"成功段 {ok_count}，失败 {fail_count} 段，请查看日志/导出 ZIP 获取明细。"
        )
    else:
        msg = f"✅ 合成完成！时长 {duration_sec:.1f} 秒，角色数: {role_count}，段数: {ok_count}"
    return (sr_out, wav_merged, filename), msg
