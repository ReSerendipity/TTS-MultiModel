"""VoxCPM2 Prompt 延续生成子模块。

架构说明：
    本模块实现 VoxCPM2 引擎的 Prompt 延续模式（Prompt Continuation / Voice
    Prompting）。用户提供一段"已读文本 + 对应音频"的参考对（prompt），模型
    接着音频的语气 / 语速节奏 / 情感起伏 / 停顿位置继续朗读后续文本，而
    不仅仅是克隆音色。

与 clone 模式的区别：
    - clone 模式：仅克隆"音色"（声音像谁），输出的说话节奏、情感、停顿
      由 cfg 和采样步数自由决定。
    - Prompt 延续模式：克隆的是"音色 + 语速节奏 + 情感起伏 + 停顿位置"
      （不仅像谁，连说话的方式都一样）。

应用场景：
    小说朗读中把"第一章的结尾 30s 节选作为 prompt，第二章接着读"，可以
    保持整本书的朗读风格 / 人物语气 / 叙事节奏高度连贯，避免每章换风格。

法律警告（P3 安全合规）：
    ⚠️  本模块的 Prompt 延续功能可基于已有音频自动生成"后续内容"，可能产生
    衍生作品。请确保您拥有参考音频的合法授权（如自有版权内容、公有领域
    作品、或已获得版权方许可）。将受版权保护的歌曲、有声书、影视配音等
    音频作为 prompt 输入并生成衍生内容，可能构成侵权行为。使用者自行承担
    因不当使用产生的全部法律责任。
    Apache License 2.0 不授予版权许可，仅授予专利授权与代码使用许可。
"""

import os
import time
from collections.abc import Callable
from typing import (
    Any,
    NamedTuple,
)

import numpy as np
from pydantic import ValidationError

from ._base import (
    SAVE_DIR,
    EngineSwitchError,
    GenerationError,
    _advanced_kwargs,
    _gen_tracker,
    _progress_mgr,
    _save_wav_compatible,
    logger,
    tts_error_handler,
)


class PromptPair(NamedTuple):
    prompt_text: str
    prompt_audio: str | tuple[np.ndarray, int]


"""Prompt 延续模式的参考音频-文本对 NamedTuple。

用于 Prompt Continuation 模式，提供一段"已读文本 + 对应音频"作为风格参考，
模型会接着该音频的语气、语速、情感继续朗读后续文本。

Attributes:
    prompt_text: 参考音频对应的文本内容（与 prompt_audio 严格对齐）。
    prompt_audio: 参考音频，支持两种格式：
        - str: 音频文件路径（会自动加载）；
        - Tuple[np.ndarray, int]: (波形数组, 采样率) 的元组（已加载的音频数据）。
"""


def _load_audio_fallback(path: str) -> tuple[np.ndarray, int]:
    """依次尝试 librosa / soundfile / torchaudio 三种加载器读取音频。

    三种加载器对格式的支持范围略有差异：torchaudio 对 mp3 / flac 较好，
    soundfile 对 wav / ogg 稳定， librosa 是兜底。全部失败时抛 ValueError。

    Args:
        path: 音频文件路径。

    Returns:
        Tuple[np.ndarray, int]: (波形数据, 采样率)。

    Raises:
        FileNotFoundError: 路径不存在。
        ValueError: 三种加载器全部失败。
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"参考音频不存在: {path}")

    errors: list[str] = []

    try:
        import librosa

        wav, sr = librosa.load(path, sr=None, mono=True)
        if wav.dtype != np.float32:
            wav = wav.astype(np.float32)
        return wav, int(sr)
    except ImportError as exc:
        errors.append(f"librosa: ImportError({exc})")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"librosa: {type(exc).__name__}({exc})")

    try:
        import soundfile as sf

        wav, sr = sf.read(path, always_2d=False, dtype="float32")
        if wav.ndim > 1:
            wav = wav.mean(axis=1)
        return wav.astype(np.float32), int(sr)
    except ImportError as exc:
        errors.append(f"soundfile: ImportError({exc})")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"soundfile: {type(exc).__name__}({exc})")

    try:
        import torchaudio

        waveform, sr = torchaudio.load(path)
        wav = waveform.squeeze(0).numpy().astype(np.float32)
        return wav, int(sr)
    except ImportError as exc:
        errors.append(f"torchaudio: ImportError({exc})")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"torchaudio: {type(exc).__name__}({exc})")

    raise ValueError(
        "参考音频加载失败（三种加载器均不可用）。"
        f"错误详情: {'; '.join(errors)}。请安装 librosa / soundfile 或 torchaudio 之一。"
    )


def validate_prompt_pair(
    pair: PromptPair,
    max_prompt_seconds: float = 30.0,
) -> tuple[bool, str]:
    """校验 Prompt 参考对的文本长度与音频时长是否匹配。

    Why max_prompt_seconds 限制 30s（不是 120s）：
        Prompt 的 CrossAttention 上下文长度为 O(n^2) 复杂度，30s 音频
        约 1500 字，CrossAttention 中间激活峰值已达约 8GB 显存；超过 60s
        会直接触发 12GB 以下卡的 OOM。30s 是大多数小说"一章结尾节选"的
        合理长度，在体验与显存之间取得平衡。

    Why 文本-音频对齐校验：
        用户容易把"10 秒读了 100 字"的音频与"500 字的 prompt_text"配错对。
        这种错配对会让模型以为"说话者每秒读 50 字"，续集会生成"机关枪语速"。
        校验时若字数/秒 > 15 或 < 1 就判为不匹配，提前提醒用户检查。

    Args:
        pair: PromptPair，含 prompt_text（文本）与 prompt_audio
            （路径 str 或 (wav, sr) 元组）。
        max_prompt_seconds: 允许的最大参考音频时长，秒。默认 30.0。

    Returns:
        Tuple[bool, str]: (是否通过, 原因 / 附加信息)。
            通过时原因为 "OK"，并附带对齐分数 (JSON 字符串的可读提示)。
    """
    text = pair.prompt_text.strip()
    if not text:
        return False, "prompt_text 不能为空"

    text_chars = len(text)

    try:
        if isinstance(pair.prompt_audio, str):
            wav, sr = _load_audio_fallback(pair.prompt_audio)
        elif isinstance(pair.prompt_audio, tuple) and len(pair.prompt_audio) == 2:
            wav, sr = pair.prompt_audio
            if wav is None or len(wav) == 0:
                return False, "prompt_audio 波形数组为空"
            sr = int(sr)
        else:
            return False, (
                f"prompt_audio 必须是文件路径(str)或(wav, sr)元组，实际为 {type(pair.prompt_audio).__name__}"
            )
    except FileNotFoundError as exc:
        return False, f"prompt_audio 文件不存在: {exc}"
    except (ValueError, OSError, RuntimeError) as exc:
        return False, f"prompt_audio 读取失败: {type(exc).__name__}: {exc}"

    duration = len(wav) / float(sr) if sr > 0 else 0.0
    if duration <= 0:
        return False, "prompt_audio 时长为 0，无法作为参考"
    if duration > max_prompt_seconds:
        return False, (
            f"参考音频时长 {duration:.1f}s 超过上限 {max_prompt_seconds:.1f}s。"
            "请截短后重试（CrossAttention 上下文受显存限制）。"
        )

    chars_per_sec = text_chars / duration
    if chars_per_sec > 15.0:
        return False, (
            f"文本与音频不匹配：文本 {text_chars} 字 / 音频 {duration:.1f}s = "
            f"{chars_per_sec:.1f} 字/秒（正常 3-8 字/秒，上限 15）。"
            "请确认 prompt_text 是否与音频实际内容一致。"
        )
    if chars_per_sec < 1.0:
        return False, (
            f"文本与音频不匹配：文本 {text_chars} 字 / 音频 {duration:.1f}s = "
            f"{chars_per_sec:.1f} 字/秒（正常 3-8 字/秒，下限 1）。"
            "请确认 prompt_text 是否与音频实际内容一致。"
        )

    score = 1.0 - abs(chars_per_sec - 5.0) / 10.0
    score = max(0.0, min(1.0, score))
    return True, (
        f"OK | 对齐分数={score:.2f} | 文本={text_chars}字 | 音频={duration:.1f}s | 语速={chars_per_sec:.1f}字/秒"
    )


def generate_with_prompt_continuation(
    model: Any,
    prompt_pair: PromptPair,
    continuation_text: str,
    cfg: float = 5.0,
    steps: int = 30,
    seed: int = -1,
    denoise_reference: bool = False,
    progress_cb: Callable[[int, int], None] | None = None,
) -> tuple[np.ndarray, int, dict[str, Any]]:
    """基于 Prompt 参考对生成延续音频。

    OOM 降级策略：
        若首次生成因 CrossAttention 上下文过大触发 OOM，自动将 prompt_audio
        截短为前 15s 并重试（meta 记录 "prompt 已自动截短为前 15s，显存不足"）。
        二次仍失败则抛 InsufficientVRAMError。

    Args:
        model: 已加载的 VoxCPM2 模型实例。
        prompt_pair: PromptPair 参考对（文本 + 音频路径或数组）。
        continuation_text: 要接着朗读的后续文本。
        cfg: Classifier-Free Guidance 强度。默认 5.0。
        steps: 推理采样步数。默认 30。
        seed: 随机种子，-1 表示随机。
        denoise_reference: 是否对参考音频做预去噪。默认 False。
        progress_cb: 可选进度回调 (phase_code, total)。

    Returns:
        Tuple[np.ndarray, int, Dict[str, Any]]:
            (continuation_waveform, sample_rate, meta)。
            meta 含 prompt_aligned_score、seed_used、retry_count、
            prompt_truncated_seconds 等字段。

    Raises:
        ValidationError: Prompt 参考对校验（文本-音频匹配 / 音频加载）失败。
        GenerationError: 二次 OOM / 推理失败。
    """
    from ...exceptions import InsufficientVRAMError
    from ...gpu_utils import free_gpu_memory

    ok, reason = validate_prompt_pair(prompt_pair)
    if not ok:
        logger.warning(f"[prompt] validate_prompt_pair 未通过: {reason}")
        raise ValidationError.from_exception_data(
            title="Prompt 参考对校验失败",
            line_errors=[{"loc": ("prompt_pair",), "msg": reason, "type": "value_error"}],
        )

    aligned_score_str = reason.split("对齐分数=")[-1].split(" |")[0] if "对齐分数=" in reason else "0.5"
    try:
        prompt_aligned_score = float(aligned_score_str)
    except ValueError:
        prompt_aligned_score = 0.5

    if isinstance(prompt_pair.prompt_audio, str):
        prompt_wav, prompt_sr = _load_audio_fallback(prompt_pair.prompt_audio)
        prompt_path = prompt_pair.prompt_audio
    else:
        prompt_wav, prompt_sr = prompt_pair.prompt_audio
        prompt_path = ""

    if progress_cb is not None:
        try:
            progress_cb(0, 3)
        except (TypeError, RuntimeError) as exc:
            logger.warning(f"[prompt] progress_cb(prepare) 异常: {exc}")

    sample_rate = 48000
    actual_seed = seed if seed != -1 else int(time.time()) & 0x7FFFFFFF
    meta: dict[str, Any] = {
        "prompt_aligned_score": prompt_aligned_score,
        "seed_used": actual_seed,
        "retry_count": 0,
        "prompt_truncated_seconds": 0.0,
        "sample_rate": sample_rate,
    }

    if prompt_sr != sample_rate:
        try:
            import librosa

            prompt_wav = librosa.resample(
                prompt_wav.astype(np.float64),
                orig_sr=float(prompt_sr),
                target_sr=float(sample_rate),
            ).astype(np.float32)
        except ImportError:
            logger.warning(f"[prompt] librosa 未安装，无法重采样 prompt_sr={prompt_sr}→{sample_rate}")

    max_attempts = 2
    wav_out: np.ndarray | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt == 1:
                effective_prompt_wav = prompt_wav
            else:
                cutoff = int(sample_rate * 15.0)
                if len(prompt_wav) > cutoff:
                    logger.info("[prompt] 二次尝试：将参考音频自动截短为前 15s 以降低显存压力")
                    effective_prompt_wav = prompt_wav[:cutoff]
                    meta["prompt_truncated_seconds"] = 15.0
                    meta["retry_count"] = attempt - 1
                else:
                    effective_prompt_wav = prompt_wav

            _progress_mgr.update_phase(f"Prompt 延续推理中（尝试 {attempt}/{max_attempts}）...")
            logger.info(
                f"[prompt] 生成延续文本 continuation_text={continuation_text[:40]}..."
                f" prompt={prompt_pair.prompt_text[:40]}..."
            )

            tmp_prompt_path = prompt_path
            if attempt > 1 and not prompt_path:
                tmp_dir = SAVE_DIR
                tmp_name = f"prompt_truncated_tmp_{int(time.time())}.wav"
                tmp_prompt_path = os.path.join(tmp_dir, tmp_name)
                try:
                    _save_wav_compatible(effective_prompt_wav, tmp_prompt_path, sample_rate)
                    meta["tmp_prompt_path"] = tmp_prompt_path
                except (OSError, ValueError) as exc:
                    logger.warning(f"[prompt] 临时截短音频保存失败: {exc}")
                    tmp_prompt_path = ""

            if tmp_prompt_path and os.path.isfile(tmp_prompt_path):
                wav_out = model.generate(
                    text=continuation_text,
                    prompt_wav_path=tmp_prompt_path,
                    prompt_text=prompt_pair.prompt_text,
                    normalize=True,
                    cfg_value=cfg,
                    inference_timesteps=steps,
                    denoise=denoise_reference,
                    min_len=2,
                    seed=actual_seed,
                    **_advanced_kwargs(),
                )
            else:
                wav_out = model.generate(
                    text=continuation_text,
                    prompt_text=prompt_pair.prompt_text,
                    normalize=True,
                    cfg_value=cfg,
                    inference_timesteps=steps,
                    denoise=denoise_reference,
                    min_len=2,
                    seed=actual_seed,
                    **_advanced_kwargs(),
                )

            if wav_out is None or len(wav_out) == 0:
                raise RuntimeError("generate 返回空音频")
            break

        except RuntimeError as exc:
            oom_keys = ("out of memory", "outofmemoryerror", "cuda error")
            is_oom = any(k in str(exc).lower() for k in oom_keys)
            if is_oom and attempt < max_attempts:
                logger.warning(f"[prompt] 第 {attempt} 次 OOM: {exc}，释放显存后尝试截短 prompt 重试")
                try:
                    free_gpu_memory()
                except (RuntimeError, ValueError) as free_exc:
                    logger.warning(f"[prompt] free_gpu_memory 异常: {free_exc}")
                time.sleep(0.3)
                continue
            if is_oom:
                logger.exception("[prompt] 二次 OOM，抛 InsufficientVRAMError")
                raise InsufficientVRAMError(
                    message=(
                        "Prompt 延续模式显存不足：即使将参考音频截短为前 15s 仍 OOM。"
                        "请使用更短的参考音频（< 10s）或关闭其他显存占用程序。"
                    )
                ) from exc
            logger.exception(f"[prompt] 第 {attempt} 次 RuntimeError(非 OOM)")
            raise GenerationError(f"Prompt 延续生成 RuntimeError: {type(exc).__name__}: {exc}") from exc
        except (ValueError, TypeError) as exc:
            logger.warning(f"[prompt] 参数 / 输入异常: {exc}")
            raise GenerationError(f"Prompt 延续参数错误: {type(exc).__name__}: {exc}") from exc
        except OSError as exc:
            logger.warning(f"[prompt] 文件 IO 异常: {exc}")
            raise GenerationError(f"Prompt 延续文件操作失败: {exc}") from exc

    if wav_out is None or len(wav_out) == 0:
        raise GenerationError("Prompt 延续生成失败：最终输出为空音频")

    if progress_cb is not None:
        try:
            progress_cb(3, 3)
        except (TypeError, RuntimeError) as exc:
            logger.warning(f"[prompt] progress_cb(complete) 异常: {exc}")

    meta["duration_ms"] = int(len(wav_out) / sample_rate * 1000)
    return wav_out.astype(np.float32), sample_rate, meta


def fn_voxcpm_prompt_continue(text: str, prompt_wav_path: str, prompt_text: str) -> tuple[tuple | None, str]:
    """VoxCPM2 Prompt 延续生成对外兼容入口。

    保留原签名 ``(text, prompt_wav_path, prompt_text) -> ((sr, wav, filename), message)``，
    不改变任何调用参数与返回结构，内部委托新实现完成校验与 OOM 降级。

    Args:
        text: 要接着朗读的后续文本。
        prompt_wav_path: 参考音频文件路径。
        prompt_text: 参考音频对应的已朗读文本。

    Returns:
        tuple[tuple | None, str]: ((sample_rate, wav, filename), message)。
            第一元素为 None 的路径仅保留类型兼容，实际失败抛异常。
    """
    from ...model_manager import _check_voxcpm2_lock
    from ...model_registry import registry

    if registry.voxcpm_model is None:
        raise EngineSwitchError("请先切换并加载 VoxCPM2 引擎")

    @tts_error_handler
    def _wrapped(text, prompt_wav_path, prompt_text):
        """Prompt 延续模式的内部包装函数（带 tts_error_handler 异常装饰器）。

        负责：
        1. 检查 VoxCPM2 模型锁状态，防止加载/切换过程中调用；
        2. 启动生成追踪器和进度条；
        3. 委托 _fn_voxcpm_prompt_continue_impl 执行实际推理流程；
        4. finally 块中记录耗时、重置进度条。

        Args:
            text: 要接着朗读的后续文本。
            prompt_wav_path: 参考音频文件路径。
            prompt_text: 参考音频对应的已朗读文本。

        Returns:
            与 fn_voxcpm_prompt_continue 返回值结构相同。
        """
        if not _check_voxcpm2_lock():
            raise GenerationError("模型正在加载或切换中，请稍后再试")
        _gen_tracker.start_generation()
        _progress_mgr.start(total_segments=1, phase="Prompt 延续准备中...")
        start_time = time.time()
        try:
            return _fn_voxcpm_prompt_continue_impl(text, prompt_wav_path, prompt_text, start_time)
        finally:
            elapsed = time.time() - start_time
            _gen_tracker.end_generation(elapsed)
            _progress_mgr.schedule_reset(delay_seconds=120)
            logger.info(f"[VoxCPM Prompt延续] 生成耗时 {elapsed:.1f} 秒")

    return _wrapped(text, prompt_wav_path, prompt_text)


def _fn_voxcpm_prompt_continue_impl(
    text: str, prompt_wav_path: str, prompt_text: str, start_time: float = 0
) -> tuple[tuple | None, str]:
    """fn_voxcpm_prompt_continue 的实际实现（含进度与日志追踪）。

    与 generate_with_prompt_continuation() 的职责划分：
        - 本函数：保留原流程，直接使用 model.generate(prompt_wav_path=..., ...)
          保证与原 fn_voxcpm_prompt_continue_impl 的调用链完全一致，
          向后兼容旧代码路径。
        - generate_with_prompt_continuation()：供新路由 / SDK 调用，
          提供完整的对齐校验、OOM 截短重试与结构化 meta 返回。

    Args:
        text: 续读文本。
        prompt_wav_path: 参考音频文件路径。
        prompt_text: 参考音频对应的已朗读文本。
        start_time: 外层传入的起始时间戳，用于日志估算剩余时间。

    Returns:
        tuple[tuple | None, str]: ((sample_rate, wav, filename), message)。
    """
    from ...model_registry import registry

    _progress_mgr.update_phase("Prompt 延续推理中...")
    logger.info(f"[VoxCPM Prompt延续] Prompt: {prompt_text[:50]}...")

    wav = registry.voxcpm_model.generate(
        text=text,
        prompt_wav_path=prompt_wav_path,
        prompt_text=prompt_text,
        normalize=True,
        cfg_value=2.0,
        inference_timesteps=10,
        denoise=True,
        min_len=2,
        **_advanced_kwargs(),
    )

    duration_sec = len(wav) / 48000 if len(wav) > 0 else 0
    timestamp = int(time.time())
    out_path = os.path.join(SAVE_DIR, f"voxcpm_prompt_continue_{timestamp}.wav")
    _save_wav_compatible(wav, out_path, 48000)
    filename = os.path.basename(out_path)
    _progress_mgr.complete()
    logger.info(f"[VoxCPM Prompt延续] 音频已保存: {out_path}，时长 {duration_sec:.1f}s")
    return (48000, wav, filename), f"生成成功！音频时长 {duration_sec:.1f} 秒。"
