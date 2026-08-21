"""VoxCPM2 流式生成子模块。

架构说明：
    本模块实现 VoxCPM2 引擎的长文本流式生成能力。将超长文本按 sentence/标点
    切分为多个短片段后分段推理，每段生成完毕立即推送到前端（SSE 或 HTTP chunked），
    使用户等待首段音频的 TTFB（Time To First Byte）< 2s，而非整段生成完成的 > 30s。

外部调用方：
    - routes/generate/voxcpm2/streaming.py 的 StreamingResponse 异步生成器，
      调用本模块的同步 Generator 后包装为 async 供 FastAPI 消费。
    - VoxCPM2Engine.stream_generate() 委托 fn_voxcpm_streaming() 执行。

两种流式模式：
    ① SSE 帧（默认）：每段 yield 结构化 dict，形如
       ``{"type": "segment", "data": {"index": i, "audio_b64": ..., "duration_ms": ...}}``
    ② Binary-chunked（SDK）：直接 yield ``bytes(wav_chunk)``，省去 base64 编码，
       带宽节省约 33%，适合服务端 SDK 或本地脚本消费。
"""

import base64
import io
import threading
import time
from collections.abc import Callable, Generator
from typing import (
    Any,
    Literal,
    NamedTuple,
)

import numpy as np

from ._base import (
    GenerationError,
    _advanced_kwargs,
    _progress_mgr,
    cleanup_temp_files,
    logger,
    split_text_for_tts,
)
from .decorators import with_generation_context
from ...exceptions import ContentSafetyError
from ...security.content_safety import check_safety

StreamingMode = Literal["sse", "binary"]


class SegmentResult(NamedTuple):
    index: int
    text: str
    audio: np.ndarray
    sample_rate: int
    duration_ms: int
    seed_used: int


"""流式生成单段结果 NamedTuple，封装一段音频的完整生成信息。

Attributes:
    index: 段索引（从 0 开始），用于前端排序展示。
    text: 该段对应的文本内容。
    audio: 生成的音频波形 numpy 数组（float32）。
    sample_rate: 音频采样率（通常为 24000 或 48000）。
    duration_ms: 音频时长（毫秒），用于前端进度条计算。
    seed_used: 该段生成实际使用的随机种子（支持 per-chunk seed 复现）。
"""


def split_text_for_streaming(
    long_text: str,
    segment_chars: int = 100,
    split_on: tuple[str, ...] = ("。", "！", "？", ".", "!", "?", "\n", "；", ";"),
) -> list[str]:
    """将长文本按语义边界 + 字符窗口切分为流式生成友好的短片段。

    Why segment_chars 默认 100（而不是 300）：
        首段 TTFB（Time To First Byte）是流式体验的核心指标。100 字文本在
        VoxCPM2 30 步推理下耗时约 1.5s，能让用户在 < 2s 内听到第一段声音，
        达到"感觉不卡"的心理学阈值。若设为 300 字则单段约 4.5s，容易让用户
        怀疑是否卡死。更短分段的代价是拼接后句子边界稍有生硬，可通过 UI 暴露
        滑竿让用户在 50-500 字范围内自选。

    切分策略：
        优先在 ``segment_chars +/- 30%`` 的字符窗口范围内，从右向左寻找
        ``split_on`` 列出的标点作为断点；窗口内找不到标点时再按
        ``segment_chars`` 硬切。这样既控制每段长度，又避免硬切把
        "我去超市买了一个很漂"截断 -> 下一段"亮的苹果"导致模型推理时
        缺少上下文的语义损失。

    Args:
        long_text: 待切分的原始长文本。
        segment_chars: 每段目标字符数，默认 100。实际段长会在 segment_chars
            +/- 30% 范围内向最近标点靠拢。
        split_on: 允许作为断句边界的标点元组，优先级从高到低即元组顺序。

    Returns:
        List[str]: 切分后的文本片段列表，每段均已 strip()，空串不会出现在
            结果中。
    """
    if not long_text or not long_text.strip():
        return []

    text = long_text.strip()
    n = len(text)
    if n <= segment_chars:
        return [text]

    segments: list[str] = []
    cursor = 0
    window = max(1, int(segment_chars * 0.3))

    while cursor < n:
        end_target = min(cursor + segment_chars, n)
        if end_target >= n:
            segments.append(text[cursor:n].strip())
            break

        search_start = max(cursor + 1, end_target - window)
        search_end = min(n, end_target + window)

        best_pos = -1
        for punct in split_on:
            for pos in range(search_end - 1, search_start - 1, -1):
                if text[pos] == punct:
                    best_pos = pos
                    break
            if best_pos != -1:
                break

        cut = best_pos + 1 if best_pos != -1 else end_target

        seg = text[cursor:cut].strip()
        if seg:
            segments.append(seg)
        cursor = cut

    return segments


def _wav_to_bytes(wav: np.ndarray, sample_rate: int) -> bytes:
    """将 numpy 音频数组序列化为 WAV 格式的 bytes。

    Args:
        wav: 音频数据，float32/float64 范围 [-1, 1] 或 int16。
        sample_rate: 采样率。

    Returns:
        bytes: 标准 WAV 格式的二进制数据。

    Raises:
        ValueError: 音频数组为空时抛出。
    """
    if wav is None or len(wav) == 0:
        raise ValueError("音频数组为空，无法序列化")
    try:
        import soundfile as sf
    except ImportError as e:
        logger.warning("[streaming] soundfile 不可用，无法序列化 WAV")
        raise RuntimeError("soundfile 未安装，无法序列化 WAV") from e

    # P0 安全修复：序列化前强制嵌入水印，用于生成内容来源追溯。
    # source_id 为代码常量，不可通过配置篡改。
    try:
        from ...watermark import WATERMARK_SOURCE_ID, watermark_audio

        wav_wm, wm_meta = watermark_audio(
            wav.astype(np.float32) if wav.dtype != np.float32 else wav,
            sample_rate,
            enable=True,
            source_id=WATERMARK_SOURCE_ID,
        )
        if wm_meta.get("watermarked"):
            logger.debug("[streaming] 水印嵌入成功: snr=%.1fdB", wm_meta.get("snr_db", 0.0))
        wav = wav_wm
    except Exception as wm_exc:
        logger.debug("[streaming] 水印嵌入异常（已忽略）: %s", wm_exc)

    buf = io.BytesIO()
    sf.write(buf, wav, sample_rate, format="WAV")
    return buf.getvalue()


def stream_generate(
    model: Any,
    segments: list[str],
    persona_id: str | None = None,
    reference_audio: Any | None = None,
    cfg: float = 5.0,
    steps: int = 30,
    seed: int = -1,
    mode: str = "sse",
    denoise_reference: bool = False,
    progress_cb: Callable[[int, int], None] | None = None,
    stop_event: threading.Event | None = None,
) -> Generator[dict[str, Any] | bytes, None, dict[str, Any]]:
    """流式分段生成并实时产出 SSE dict 或 WAV binary chunk。

    单段异常容错：
        某段推理触发 CUDA OOM（RuntimeError）时，本段以 SSEEvent type='error'
        推送，调用 free_gpu_memory() 清理后下一段继续，避免前几段已生成的
        结果白做。整条 SSE 连接不会因单段 OOM 被强断。

    Args:
        model: 已加载的 VoxCPM2 模型实例（registry.voxcpm_model），需具有
            generate() 或 generate_streaming() 方法。
        segments: split_text_for_streaming() 输出的分段文本列表。
        persona_id: 音色 ID，用于查找预计算嵌入；若提供 reference_audio
            则优先使用后者。
        reference_audio: 参考音频路径或 (wav, sr) 元组，克隆模式下使用。
        cfg: Classifier-Free Guidance 强度，越大风格越强但可能出现爆音。
        steps: 推理采样步数，越多质量越高但越慢。
        seed: 随机种子，-1 表示随机，正数可复现同一段输出。
        mode: "sse" 或 "binary"，控制 yield 的数据类型。
        denoise_reference: 是否对参考音频做预去噪处理。
        progress_cb: 可选进度回调，签名 (current_idx, total_segments)。
        stop_event: 可选 threading.Event，每段生成前检查，若 set() 则终止
            流，通常与前端 SSE 断开的清理逻辑联动。

    Yields:
        Union[Dict[str, Any], bytes]: mode='sse' 时 yield SSE 事件 dict
            （含 type/data 键）；mode='binary' 时每段 yield WAV 二进制 bytes。

    Returns:
        Dict[str, Any]: 通过 StopIteration.value 返回汇总信息，键包括
            total_segments、total_duration_ms、all_seed_used、final_audio_url
            （最后 SSE event='complete' 会消耗此值）。

    Raises:
        ValueError: segments 为空或 mode 不合法时抛出。
        GenerationError: 全部段均推理失败（无任何成功音频）时抛出。
    """
    from ...gpu_utils import free_gpu_memory

    if not segments:
        raise ValueError("stream_generate: segments 不能为空")
    if mode not in ("sse", "binary"):
        raise ValueError(f"stream_generate: mode 必须是 'sse' 或 'binary'，实际为 '{mode}'")

    total = len(segments)
    all_seed_used: list[int] = []
    total_duration_ms = 0
    success_count = 0
    sample_rate = 48000
    merged_audio: np.ndarray | None = None
    temp_files: list[str] = []

    try:
        for idx, seg_text in enumerate(segments):
            if stop_event is not None and stop_event.is_set():
                logger.info(f"[streaming] stop_event 已触发，终止流式生成（{idx}/{total}）")
                break
            if progress_cb is not None:
                try:
                    progress_cb(idx, total)
                except (TypeError, RuntimeError) as cb_exc:
                    logger.warning(f"[streaming] progress_cb 调用异常: {cb_exc}")

            seg_text = seg_text.strip()
            if not seg_text:
                continue

            try:
                ref_path: str | None = None
                if isinstance(reference_audio, str):
                    ref_path = reference_audio
                kwargs: dict[str, Any] = dict(
                    text=seg_text,
                    reference_wav_path=ref_path if ref_path else "",
                    normalize=True,
                    cfg_value=cfg,
                    inference_timesteps=steps,
                    denoise=denoise_reference,
                    min_len=2,
                    seed=seed if seed != -1 else -1,
                    **_advanced_kwargs(),
                )
                if hasattr(model, "generate_streaming"):
                    chunks_collected: list[np.ndarray] = []
                    for chunk in model.generate_streaming(**kwargs):
                        chunks_collected.append(chunk)
                    wav = np.concatenate(chunks_collected) if chunks_collected else np.array([], dtype=np.float32)
                else:
                    wav = model.generate(**kwargs)

                if wav is None or len(wav) == 0:
                    raise RuntimeError("模型返回空音频")

                dur_ms = int(len(wav) / sample_rate * 1000)
                actual_seed = seed if seed != -1 else (idx * 1000 + 1)
                all_seed_used.append(actual_seed)
                total_duration_ms += dur_ms
                success_count += 1

                merged_audio = wav if merged_audio is None else np.concatenate([merged_audio, wav])

                if mode == "sse":
                    wav_bytes = _wav_to_bytes(wav, sample_rate)
                    audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
                    yield {
                        "type": "segment",
                        "data": {
                            "index": idx,
                            "text": seg_text,
                            "audio_b64": audio_b64,
                            "duration_ms": dur_ms,
                            "seed_used": actual_seed,
                        },
                    }
                else:
                    yield _wav_to_bytes(wav, sample_rate)

            except RuntimeError as run_exc:
                oom_keywords = (
                    "out of memory",
                    "outofmemoryerror",
                    "cuda error",
                    "cublas_status_alloc_failed",
                )
                is_oom = any(k in str(run_exc).lower() for k in oom_keywords)
                if is_oom:
                    logger.error(f"[streaming] 第 {idx}/{total} 段 CUDA OOM: {run_exc}，尝试释放显存后继续")
                    try:
                        free_gpu_memory()
                    except (RuntimeError, ValueError) as free_exc:
                        logger.warning(f"[streaming] 显存释放异常: {free_exc}")
                    if mode == "sse":
                        yield {
                            "type": "error",
                            "data": {
                                "index": idx,
                                "message": f"段 {idx} 显存不足已跳过: {type(run_exc).__name__}",
                            },
                        }
                    continue
                logger.exception(f"[streaming] 第 {idx}/{total} 段推理 RuntimeError")
                if mode == "sse":
                    yield {
                        "type": "error",
                        "data": {
                            "index": idx,
                            "message": f"段 {idx} 推理失败: {type(run_exc).__name__}: {run_exc}",
                        },
                    }
                continue
            except (ValueError, TypeError) as val_exc:
                logger.warning(f"[streaming] 第 {idx}/{total} 段参数异常: {val_exc}")
                if mode == "sse":
                    yield {
                        "type": "error",
                        "data": {
                            "index": idx,
                            "message": f"段 {idx} 参数错误: {type(val_exc).__name__}: {val_exc}",
                        },
                    }
                continue
            except GeneratorExit:
                raise
            except Exception as exc:
                logger.exception(f"[streaming] 第 {idx}/{total} 段未预期异常: {type(exc).__name__}")
                if mode == "sse":
                    yield {
                        "type": "error",
                        "data": {
                            "index": idx,
                            "message": f"段 {idx} 未预期错误: {type(exc).__name__}",
                        },
                    }
                continue

        if success_count == 0:
            raise GenerationError("stream_generate: 全部段均推理失败，无有效音频输出")

        final_audio_url = ""
        if merged_audio is not None and len(merged_audio) > 0:
            try:
                from ._base import SAVE_DIR, _save_wav_compatible

                timestamp = int(time.time())
                final_path = f"{SAVE_DIR}/streaming_merged_{timestamp}.wav"
                _save_wav_compatible(merged_audio, final_path, sample_rate)
                final_audio_url = f"/api/audio/file/{int(time.time())}_{len(merged_audio)}.wav"
            except (OSError, ValueError) as save_exc:
                logger.warning(f"[streaming] 合并音频保存失败: {save_exc}")
                final_audio_url = ""

        summary: dict[str, Any] = {
            "total_segments": total,
            "success_segments": success_count,
            "total_duration_ms": total_duration_ms,
            "all_seed_used": all_seed_used,
            "final_audio_url": final_audio_url,
        }
        return summary

    finally:
        if temp_files:
            try:
                cleanup_temp_files(temp_files)
            except (OSError, ValueError) as cl_exc:
                logger.warning(f"[streaming] 临时文件清理异常: {cl_exc}")
        if stop_event is not None:
            try:
                stop_event.set()
            except (RuntimeError, AttributeError) as se_exc:
                logger.warning(f"[streaming] stop_event.set() 异常: {se_exc}")
        try:
            _progress_mgr.cancel()
        except (RuntimeError, AttributeError) as pm_exc:
            logger.debug(f"[streaming] progress cancel 异常: {pm_exc}")


@with_generation_context(phase_name="VoxCPM流式生成")
def fn_voxcpm_streaming(
    text: str,
    ref_audio_path: str | None = None,
    cfg_value: float = 2.0,
    inference_timesteps: int = 10,
    denoise: bool = True,
    seed: int = -1,
):
    """VoxCPM2 流式生成入口函数（对外向后兼容 API）。

    保留原有签名与返回行为：单段直接返回 model.generate_streaming() 的 Generator
    或单段 wav 数组；多段返回 all_chunks 列表（元素为单段 wav）。

    Args:
        text: 待合成的长文本。
        ref_audio_path: 参考音频文件路径，None 使用默认音色。
        cfg_value: CFG 指导强度，默认 2.0。
        inference_timesteps: 推理步数，默认 10。
        denoise: 是否对参考音频预处理去噪。
        seed: 随机种子，-1 表示随机。

    Returns:
        Union[Generator[Any, None, None], np.ndarray, List[np.ndarray]]:
            单段情况：原生流式返回 Generator，否则返回单段 wav 数组；
            多段情况返回 wav 数组列表。

    Raises:
        GenerationError: 用户取消或底层推理异常时抛出，通过上层
            tts_error_handler 统一包装。
    """
    from ...model_registry import registry

    start_time = time.time()

    # 内容安全门禁：流式路径不经过 normalize_text，需在推理前显式拦截
    _safety_result = check_safety(text)
    if not _safety_result.is_safe:
        raise ContentSafetyError(
            f"文本未通过内容安全检测（{_safety_result.category.value}，置信度 {_safety_result.confidence:.2f}），已拒绝合成。",
            category=_safety_result.category.value,
        )

    _progress_mgr.update_phase("文本分割中...")
    segments = split_text_for_tts(text)
    total = len(segments)

    _progress_mgr.start(total_segments=total, phase="VoxCPM2 流式推理中...")

    if total == 1:
        _progress_mgr.advance_segment("流式推理生成中...")
        logger.info(f"[VoxCPM流式生成] 第 1/1 段，使用 {'reference_wav' if ref_audio_path else '默认音色'} 模式...")

        if hasattr(registry.voxcpm_model, "generate_streaming"):
            return registry.voxcpm_model.generate_streaming(
                text=segments[0],
                reference_wav_path=ref_audio_path if ref_audio_path else "",
                normalize=True,
                cfg_value=cfg_value,
                inference_timesteps=inference_timesteps,
                denoise=denoise,
                min_len=2,
                **_advanced_kwargs(),
            )
        else:
            logger.warning("[VoxCPM流式生成] 模型不支持 streaming，回退到常规生成")
            wav = registry.voxcpm_model.generate(
                text=segments[0],
                reference_wav_path=ref_audio_path if ref_audio_path else "",
                normalize=True,
                cfg_value=cfg_value,
                inference_timesteps=inference_timesteps,
                denoise=denoise,
                min_len=2,
                **_advanced_kwargs(),
            )
            _progress_mgr.complete()
            return wav

    all_chunks = []
    for idx, seg in enumerate(segments):
        if _progress_mgr.should_stop():
            logger.info("[VoxCPM流式] 生成已被用户取消")
            raise GenerationError("生成已取消")
        seg = seg.strip()
        if not seg:
            continue

        _progress_mgr.advance_segment(f"第 {idx + 1}/{total} 段推理中...")
        elapsed = time.time() - start_time
        if idx > 0:
            avg = elapsed / idx
            remaining = avg * (total - idx)
            logger.info(f"[VoxCPM流式生成] 第 {idx + 1}/{total} 段，已耗时 {elapsed:.1f}s，预计剩余 {remaining:.1f}s")
        else:
            logger.info(f"[VoxCPM流式生成] 第 {idx + 1}/{total} 段...")

        if hasattr(registry.voxcpm_model, "generate_streaming"):
            for chunk in registry.voxcpm_model.generate_streaming(
                text=seg,
                reference_wav_path=ref_audio_path if ref_audio_path else "",
                normalize=True,
                cfg_value=cfg_value,
                inference_timesteps=inference_timesteps,
                denoise=denoise,
                min_len=2,
                **_advanced_kwargs(),
            ):
                all_chunks.append(chunk)
        else:
            wav = registry.voxcpm_model.generate(
                text=seg,
                reference_wav_path=ref_audio_path if ref_audio_path else "",
                normalize=True,
                cfg_value=cfg_value,
                inference_timesteps=inference_timesteps,
                denoise=denoise,
                min_len=2,
                **_advanced_kwargs(),
            )
            all_chunks.append(wav)

    _progress_mgr.complete()
    return all_chunks
