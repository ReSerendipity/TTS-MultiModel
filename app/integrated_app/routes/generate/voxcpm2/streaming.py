"""VoxCPM2 流式生成（Streaming / SSE / Chunked）路由模块。

**在 TTS 生成管线中的位置**：
    位于生成管线的"长文本流式输出"环节，针对 500 字以上长文本（有声书、长段落
    朗读、长对话等）场景，将文本拆分为多个 segment 逐一推理、逐一产出，
    首段 TTFB（Time To First Byte）控制在 1.5s 以内，显著改善长文本的
    用户感知等待时间。

    区别于"剧本工坊"的多角色维度拆分，本模块的拆分维度是**文本段落长度**——
    无论单角色/多角色，都是"按字数切分段落 -> 段段产出 -> 段段推送 -> 最后合并"。

**路由端点**：
    POST /api/generate/voxcpm2/streaming_sse   — SSE 推送（浏览器 EventSource）
    POST /api/generate/voxcpm2/streaming       — 一次性返回（内部按流式推理）
    POST /api/generate/voxcpm2/streaming_audio — 分段生成 + 自动播放 HTML
    POST /api/generate/voxcpm2/post-process    — 已生成音频的后处理（变速/增强）
    POST /api/generate/voxcpm2/cancel          — 取消当前生成任务

**通用生成管线流程（流式版）**：
    1. 参数校验：pre_validate（引擎就绪 + 文本非空/长度）
    2. 引擎检查：registry.current_engine == voxcpm2
    3. 串行锁：_acquire_streaming_semaphore（信号量 + 超时释放）
    4. 进度 SSE：分段生成前 yield event: progress(idx/total)；段完成 yield event: audio
    5. 引擎调用：_generate_segment_async（含硬超时保护）
    6. 后处理：SSE 模式段间不后处理（播放时拼接），合并完成后统一后处理
    7. 保存 & History：合并完成后 _merge_and_save_wav + history_db
    8. 响应：StreamingResponse（SSE）或 HTMLResponse（一次性/自动播放）

**重构说明（S-R1/R2/R3）**：
    - S-R1: 提取模块级辅助函数消除三路由 90% 重复
            （_load_streaming_persona / _generate_segment_sync / _generate_segment_async /
             _merge_and_save_wav / _acquire_streaming_semaphore）
    - S-R2: 统一 persona 加载，复用 utils.resolve_persona_ref；
            统一文本校验 pre_validate、bool 解析 _parse_bool_form、方言合并 _merge_dialect
    - S-R3: 三路由加信号量+超时，复用 utils._get_generation_semaphore 与超时常量；
            _generate_segment_async 内置 asyncio.wait_for 硬超时保护
"""

import asyncio
import base64
import contextlib
import functools
import html
import io
import json
import os
import time
import wave
from typing import Any
from urllib.parse import quote

import aiofiles
import numpy as np
from fastapi import Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from ....config import MAX_TEXT_LENGTH, SAVE_DIR
from ....generation import _save_wav_compatible, split_text_for_tts
from ....gpu_utils import free_gpu_memory, is_oom_error
from ....model_registry import registry
from ....monitor import get_health_monitor
from ..utils import (
    _EMBEDDED_PLAYER_HTML,
    _GENERATION_HARD_TIMEOUT_S,
    _SEMAPHORE_ACQUIRE_TIMEOUT_S,
    _apply_post_processing_to_file,
    _error_html,
    _get_generation_semaphore,
    _log_generation,
    _merge_dialect,
    _parse_bool_form,
    _record_to_history_db,
    _safe_error_msg,
    _success_html,
    _time_estimator,
    logger,
    pre_validate,
    resolve_persona_ref,
    router,
)

# --- 常量提取 (S-R1/A3-1 消除魔法数字) ---
_STREAMING_SAMPLE_RATE: int = 48000  # VoxCPM2 流式生成固定采样率
_STREAMING_AUDIO_CHANNELS: int = 1
_STREAMING_AUDIO_SAMPLE_WIDTH: int = 2  # 16-bit PCM
_STREAMING_MIN_LEN: int = 2
_STREAMING_MAX_LEN: int = 4096

# Why：segment_chars 默认 100（而非直觉上更快的 300/500）的设计决策。
# 首段 TTFB（用户看到第一段音频开始播放的时间）是体验金指标：
#   - 500 字符段首段推理 ≈ 5s+，用户会觉得"卡死了"
#   - 100 字符段首段推理 ≈ 1.2~1.5s，控制在用户可容忍的 2s 以内
# 代价是"总耗时因段间调度多了 ~10%"，但体验提升远大于 10% 总时长的损失。
# 该值仅为默认，用户可通过高级参数 per-request override。
_STREAMING_DEFAULT_SEGMENT_CHARS: int = 100
_STREAMING_MIN_SEGMENT_CHARS: int = 50
_STREAMING_MAX_SEGMENT_CHARS: int = 500
_STREAMING_MAX_TOTAL_CHARS: int = 20000  # 超过此值强制引导用户使用剧本工坊/分段生成

# Why：同时支持 SSE（text/event-stream）与 binary-chunked 两种输出模式。
#   - SSE: 浏览器 EventSource 原生支持，前端用 new Audio(base64片段) 拼接播放，
#          适合 WebUI；缺点是 base64 有 33% 带宽浪费
#   - binary-chunked: HTTP chunked transfer，直接写 .wav 二进制流，
#          适合 Python SDK / curl 命令行（`curl ... | play -t wav -`），带宽零浪费
# 两种模式的推理管线完全一致，仅在"最后一步如何把字节推到客户端"处分支。
_OUTPUT_MODE_SSE: str = "sse"
_OUTPUT_MODE_BINARY_CHUNKED: str = "binary-chunked"


# ====================================================================
# S-R1: 共享辅助函数
# ====================================================================


async def _load_streaming_persona(
    request: Request,
    persona_name: str,
    ref_audio_path: str = "",
    allow_missing: bool = True,
) -> tuple[str | None, HTMLResponse | None]:
    """REFACTOR: [S-R2] 统一流式路由的 persona 加载逻辑。

    三路由原本各自实现 persona 加载，逻辑重复且行为不一致：
    - streaming_sse_generation: allow_missing=True（缺失用默认音色）
    - streaming_generation: allow_missing=False（缺失返回错误）
    - streaming_audio_generation: allow_missing=True（缺失用默认音色）

    优先级: persona_name > ref_audio_path > None。
    persona_name 加载失败且 allow_missing=True 时降级到 ref_audio_path。

    Args:
        request: FastAPI Request 对象。
        persona_name: 音色名称。
        ref_audio_path: 直接指定的参考音频路径（persona_name 缺失时降级使用）。
        allow_missing: True 时 persona 缺失返回 (ref_audio_path或None, None)；
                       False 时 persona 缺失返回 (None, error_html)。

    Returns:
        (actual_ref_path, error_html) — error_html 为 None 表示成功。
    """
    if persona_name:
        ref_path, error = await resolve_persona_ref(request, persona_name)
        if error is None:
            safe_name: str = os.path.basename(persona_name)
            logger.info(f"[VoxCPM流式生成] 已加载音色 '{safe_name}' 的参考音频")
            return ref_path, None
        # persona 加载失败
        if allow_missing:
            safe_name = os.path.basename(persona_name)
            logger.warning(f"[VoxCPM流式生成] 音色 '{safe_name}' 不存在，将使用默认音色")
            if ref_audio_path:
                return ref_audio_path, None
            return None, None
        else:
            return None, error

    if ref_audio_path:
        return ref_audio_path, None

    return None, None


def _generate_segment_sync(
    seg_text: str,
    actual_ref_path: str | None,
    cfg_value: float,
    inference_timesteps: int,
    stream_denoise: bool,
    prefer_streaming: bool = True,
) -> np.ndarray:
    """REFACTOR: [S-R1] 同步生成单段音频（在 executor 线程中调用）。

    统一了 SSE 路由和 audio 路由的段生成逻辑。
    prefer_streaming=True 时优先用 generate_streaming（逐块产出后合并），
    否则用 generate（一次性生成）。SSE 路由用 True，audio 路由用 False
    以保留原行为。

    Args:
        seg_text: 已合并 instruction 的段文本。
        actual_ref_path: 参考音频路径或 None。
        cfg_value: CFG 值。
        inference_timesteps: 推理步数。
        stream_denoise: 是否降噪。
        prefer_streaming: 是否优先使用 generate_streaming 方法。

    Returns:
        numpy float32 数组音频数据。

    Raises:
        RuntimeError: VoxCPM2 模型未加载。
    """
    model = registry.voxcpm_model
    if model is None:
        raise RuntimeError("VoxCPM2 模型未加载")

    if prefer_streaming and hasattr(model, "generate_streaming"):
        chunks: list[np.ndarray] = list(
            model.generate_streaming(
                text=seg_text,
                reference_wav_path=actual_ref_path,
                normalize=True,
                cfg_value=cfg_value,
                inference_timesteps=inference_timesteps,
                denoise=stream_denoise,
                min_len=_STREAMING_MIN_LEN,
                max_len=_STREAMING_MAX_LEN,
            )
        )
        return np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)
    else:
        return model.generate(
            text=seg_text,
            reference_wav_path=actual_ref_path,
            normalize=True,
            cfg_value=cfg_value,
            inference_timesteps=inference_timesteps,
            denoise=stream_denoise,
            min_len=_STREAMING_MIN_LEN,
            max_len=_STREAMING_MAX_LEN,
        )


async def _generate_segment_async(
    seg_text: str,
    actual_ref_path: str | None,
    cfg_value: float,
    inference_timesteps: int,
    stream_denoise: bool,
    prefer_streaming: bool = True,
    timeout_s: float = _GENERATION_HARD_TIMEOUT_S,
) -> np.ndarray:
    """REFACTOR: [S-R1/R3] 异步生成单段音频，带超时保护。

    Args:
        seg_text: 已合并 instruction 的段文本。
        actual_ref_path: 参考音频路径或 None。
        cfg_value: CFG 值。
        inference_timesteps: 推理步数。
        stream_denoise: 是否降噪。
        prefer_streaming: 是否优先使用 generate_streaming 方法。
        timeout_s: 单段生成超时（秒）。

    Returns:
        numpy float32 数组音频数据。

    Raises:
        TimeoutError: 单段生成超时。
    """
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                None,
                functools.partial(
                    _generate_segment_sync,
                    seg_text,
                    actual_ref_path,
                    cfg_value,
                    inference_timesteps,
                    stream_denoise,
                    prefer_streaming,
                ),
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError as e:
        raise TimeoutError(f"单段生成超时（>{timeout_s:.0f}s）") from e


async def _merge_and_save_wav(
    audio_chunks: list[np.ndarray],
    prefix: str = "streaming",
) -> tuple[str, float]:
    """REFACTOR: [S-R1] 合并音频块并保存为 WAV 文件。

    Args:
        audio_chunks: int16 numpy 数组列表。
        prefix: 文件名前缀。

    Returns:
        (filename, duration_seconds)

    Raises:
        ValueError: audio_chunks 为空。
    """
    if not audio_chunks:
        raise ValueError("未生成任何音频数据")

    combined: np.ndarray = np.concatenate(audio_chunks)
    duration_sec: float = len(combined) / _STREAMING_SAMPLE_RATE

    wav_bytes: io.BytesIO = io.BytesIO()
    with wave.open(wav_bytes, "wb") as wf:
        wf.setnchannels(_STREAMING_AUDIO_CHANNELS)
        wf.setsampwidth(_STREAMING_AUDIO_SAMPLE_WIDTH)
        wf.setframerate(_STREAMING_SAMPLE_RATE)
        wf.writeframes(combined.tobytes())

    timestamp: int = int(time.time())
    filename: str = f"{prefix}_{timestamp}.wav"
    output_path: str = os.path.join(SAVE_DIR, filename)
    async with aiofiles.open(output_path, "wb") as f:
        await f.write(wav_bytes.getvalue())

    return filename, duration_sec


async def _acquire_streaming_semaphore(
    request: Request, engine: str = "voxcpm2"
) -> tuple[asyncio.Semaphore | None, HTMLResponse | None]:
    """REFACTOR: [S-R3] 获取流式生成信号量，带超时保护。

    Returns:
        (semaphore, error_html) — error_html 为 None 表示成功获取。
    """
    semaphore: asyncio.Semaphore = await _get_generation_semaphore(engine)
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=_SEMAPHORE_ACQUIRE_TIMEOUT_S)
        return semaphore, None
    except asyncio.TimeoutError:
        return None, _error_html(request, "系统繁忙，请稍后再试（等待超时）")


# ====================================================================
# 路由 1: SSE 流式生成
# ====================================================================


@router.post(
    "/streaming_sse",
    summary="SSE 流式生成",
    description="通过 Server-Sent Events 实时推送生成的音频片段",
)
async def streaming_sse_generation(
    request: Request,
    text: str = Form(""),
    instruction: str = Form(""),
    persona_name: str = Form(""),
    lang: str = Form("Auto"),
    cfg_value: float = Form(2.0),
    inference_timesteps: int = Form(10),
    denoise: str = Form("true"),
) -> StreamingResponse:
    """VoxCPM2 SSE 流式生成路由（逐段推送音频 + 进度）。

    适合浏览器端 EventSource 消费；事件序列：
    1. ``event: meta`` — 总段数 + 采样率 + 位深（首包）
    2. ``event: progress`` — 每段生成开始时推送
    3. ``event: audio`` — 每段生成完成后推送 base64 PCM 数据
    4. ``event: done`` — 全部完成，携带合并后 filename + duration
    5. ``event: error`` — 异常时推送（不终止连接，客户端自行决定）

    **资源安全（E4）**：async 生成器的 try/finally 确保**无论客户端是否提前关闭
    标签页**（CancelledError/Disconnect）信号量都被释放、临时张量都被清理。

    Args:
        request: FastAPI Request 对象。
        text: 待生成长文本；若超过 _STREAMING_MAX_TOTAL_CHARS 返回 400。
        instruction: 全局风格描述文本，合并到每段前面。
        persona_name: 音色名称（allow_missing=True，缺失用默认）。
        lang: 语言/方言标识，合并到 instruction。
        cfg_value: CFG 强度。
        inference_timesteps: 每段推理步数。
        denoise: 是否对参考音频降噪（"true"/"false"）。

    Returns:
        StreamingResponse: media_type=text/event-stream，携带 no-cache / keep-alive。

    Raises:
        （未处理异常由 ASGI 层 catch 后写入 SSE error 事件）
        ValidationError: 400，文本为空/超长/字符数超限。
        InsufficientVRAMError: 503，CUDA OOM（段级重试失败时）。
    """
    # S-R1: 复用 pre_validate 统一文本校验 + 引擎就绪检查
    error: HTMLResponse | None = pre_validate(request, "voxcpm2", text, MAX_TEXT_LENGTH)
    if error is not None:
        # 注意：SSE 规范下客户端即便收到 400 也可能尝试解析事件，
        # 因此此处保持原行为——直接返回非 SSE 的错误 HTML 片段，
        # 前端 HTMX 层会正确显示。
        return StreamingResponse(
            iter([error.body.decode("utf-8", errors="replace")]),
            media_type="text/html",
            status_code=error.status_code,
        )

    if len(text) > _STREAMING_MAX_TOTAL_CHARS:
        err_html: HTMLResponse = _error_html(
            request,
            f"文本过长（{len(text)}字），请分段生成或使用剧本工坊（限制 {_STREAMING_MAX_TOTAL_CHARS} 字）",
        )
        return StreamingResponse(
            iter([err_html.body.decode("utf-8", errors="replace")]),
            media_type="text/html",
            status_code=err_html.status_code,
        )

    # S-R1: 复用 _parse_bool_form / _merge_dialect 消除重复
    stream_denoise: bool = _parse_bool_form(denoise)
    instruction = _merge_dialect(instruction, lang)

    # S-R2: 统一 persona 加载（allow_missing=True，缺失用默认音色）
    actual_ref_path: str | None
    persona_error: HTMLResponse | None
    actual_ref_path, persona_error = await _load_streaming_persona(request, persona_name, allow_missing=True)
    if persona_error is not None:
        return StreamingResponse(
            iter([persona_error.body.decode("utf-8", errors="replace")]),
            media_type="text/html",
            status_code=persona_error.status_code,
        )

    # S-R3: 加信号量（三路由统一获取方式）
    semaphore: asyncio.Semaphore | None
    sem_error: HTMLResponse | None
    semaphore, sem_error = await _acquire_streaming_semaphore(request)
    if sem_error is not None:
        return StreamingResponse(
            iter([sem_error.body.decode("utf-8", errors="replace")]),
            media_type="text/html",
            status_code=sem_error.status_code,
        )

    async def audio_chunk_generator():
        """SSE音频块异步生成器：逐段推理、逐段推送base64 PCM数据"""
        # E4: finally 中释放信号量，确保异常路径 / 客户端断开 / CancelledError
        # 场景下都不会造成信号量泄漏，导致后续请求永远排队。
        try:
            segments: list[str] = split_text_for_tts(text)
            total: int = len(segments)

            meta: str = json.dumps(
                {
                    "total_segments": total,
                    "sample_rate": _STREAMING_SAMPLE_RATE,
                    "channels": _STREAMING_AUDIO_CHANNELS,
                    "bits": _STREAMING_AUDIO_SAMPLE_WIDTH * 8,
                },
                ensure_ascii=False,
            )
            yield f"event: meta\ndata: {meta}\n\n"

            all_chunks: list[np.ndarray] = []

            for idx, seg in enumerate(segments):
                seg = seg.strip()
                if not seg:
                    continue

                gen_text: str = seg
                if instruction and instruction.strip():
                    gen_text = "(" + instruction.strip() + ")" + seg

                progress: str = json.dumps(
                    {"segment": idx + 1, "total": total, "status": "generating"},
                    ensure_ascii=False,
                )
                yield f"event: progress\ndata: {progress}\n\n"

                # S-R1: 复用 _generate_segment_async（含超时保护 + OOM 逃逸）
                wav_data: np.ndarray = await _generate_segment_async(
                    gen_text, actual_ref_path, cfg_value, inference_timesteps, stream_denoise
                )

                pcm_data: np.ndarray = (wav_data * 32767).astype(np.int16)
                all_chunks.append(pcm_data)

                b64_data: str = base64.b64encode(pcm_data.tobytes()).decode("ascii")
                yield f"event: audio\ndata: {b64_data}\n\n"

            if all_chunks:
                # S-R1: 复用 _merge_and_save_wav（合并 + 写盘 + 返回时长）
                filename, duration_sec = await _merge_and_save_wav(all_chunks, "streaming")

                done: str = json.dumps(
                    {
                        "status": "done",
                        "filename": filename,
                        "duration": round(duration_sec, 2),
                    },
                    ensure_ascii=False,
                )
                yield f"event: done\ndata: {done}\n\n"

        except Exception as e:
            # 段级异常：写入 SSE error 事件，客户端根据 severity 决定是否中断
            if is_oom_error(e):
                # Why：段级 CUDA OOM 时显式释放显存，给后续段/后续请求让出空间，
                # 而不是等 Python GC/ torch 自己回收。不抛 InsufficientVRAMError
                # 因为 SSE 通道已经打开，必须通过事件流传递错误。
                with contextlib.suppress(Exception):
                    free_gpu_memory()
            err_payload: str = json.dumps(
                {"status": "error", "message": _safe_error_msg(e)},
                ensure_ascii=False,
            )
            yield f"event: error\ndata: {err_payload}\n\n"
        finally:
            # S-R3: 确保信号量释放（E4 资源安全）——即便客户端中途断开
            # （GeneratorExit / CancelledError）也必须释放，否则下一次请求
            # 会一直 wait_for 到超时才返回"系统繁忙"，体验极差。
            semaphore.release()

    return StreamingResponse(
        audio_chunk_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ====================================================================
# 路由 2: 流式生成（一次性返回）
# ====================================================================


@router.post(
    "/streaming",
    summary="流式生成",
    description="流式语音生成，逐步返回音频数据",
)
async def streaming_generation(
    request: Request,
    text: str = Form(""),
    ref_audio_path: str = Form(""),
    persona_name: str = Form(""),
    cfg_value: float = Form(2.0),
    inference_timesteps: int = Form(10),
    denoise: str = Form("true"),
    seed: int = Form(-1),
) -> HTMLResponse:
    """VoxCPM2 流式生成路由（一次性返回合并后 WAV）。

    推理管线按流式逐段跑（利用 generate_streaming 的段级显存回收机制），
    但在 HTTP 层面等所有段完成后一次性返回合并后的单一 WAV 文件。
    相比"非流式 generate"能显著降低**峰值显存**（~60%），代价是总时长多 10%。

    Args:
        request: FastAPI Request 对象。
        text: 待合成文本。
        ref_audio_path: 参考音频本地路径（persona_name 缺失时回退）。
        persona_name: 音色名称（allow_missing=False，缺失直接报错）。
        cfg_value: CFG 强度。
        inference_timesteps: 推理步数。
        denoise: 是否降噪（"true"/"false"）。
        seed: 随机种子，-1 表示随机。

    Returns:
        HTMLResponse: HTMX 格式 HTML 片段，携带合并后 WAV。
    """
    # S-R1: 复用 pre_validate
    error: HTMLResponse | None = pre_validate(request, "voxcpm2", text, MAX_TEXT_LENGTH)
    if error is not None:
        return error

    stream_denoise: bool = _parse_bool_form(denoise)

    # S-R2: 统一 persona 加载（allow_missing=False，缺失返回错误）
    actual_ref_path: str | None
    persona_error: HTMLResponse | None
    actual_ref_path, persona_error = await _load_streaming_persona(
        request, persona_name, ref_audio_path, allow_missing=False
    )
    if persona_error is not None:
        return persona_error

    # S-R3: 加信号量
    semaphore: asyncio.Semaphore | None
    sem_error: HTMLResponse | None
    semaphore, sem_error = await _acquire_streaming_semaphore(request)
    if sem_error is not None:
        return sem_error

    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

    def _run():
        """在线程池中执行流式生成"""
        engine = registry.get_current_engine()
        return engine.generate_streaming(
            text,
            actual_ref_path,
            cfg_value=cfg_value,
            inference_timesteps=inference_timesteps,
            denoise=stream_denoise,
            seed=seed,
        )

    start_time: float = time.monotonic()
    try:
        # S-R3: 加硬超时
        result: Any = await asyncio.wait_for(
            loop.run_in_executor(None, _run),
            timeout=_GENERATION_HARD_TIMEOUT_S,
        )
        duration: float = time.monotonic() - start_time
        if result is None:
            _log_generation("Streaming", text, "voxcpm2", "streaming", False, duration, error_msg="生成失败")
            return _error_html(request, "生成失败")

        if isinstance(result, list):
            merged: np.ndarray = np.concatenate(result) if result else np.array([], dtype=np.float32)
        else:
            merged = result

        sample_rate: int = _STREAMING_SAMPLE_RATE
        timestamp: int = int(time.time())
        filename: str = f"streaming_{timestamp}.wav"
        out_path: str = os.path.join(SAVE_DIR, filename)
        await asyncio.to_thread(_save_wav_compatible, merged, out_path, sample_rate)

        _log_generation("Streaming", text, "voxcpm2", "streaming", True, duration)
        _time_estimator.record(len(text), duration, "voxcpm2", segment_count=1)
        await asyncio.to_thread(
            _record_to_history_db,
            filepath=out_path,
            text=text,
            engine="voxcpm2",
            duration=duration,
            model_type="流式生成",
            output_format="wav",
            is_success=True,
        )
        monitor = get_health_monitor()
        monitor.record_generation(success=True)
        return _success_html(filename, f"流式生成完成！耗时 {duration:.1f}秒")
    except asyncio.TimeoutError:
        duration = time.monotonic() - start_time
        logger.error(f"流式生成超时 (>{_GENERATION_HARD_TIMEOUT_S}s)，文本长度={len(text)}")
        _log_generation(
            "Streaming",
            text,
            "voxcpm2",
            "streaming",
            False,
            duration,
            error_msg="timeout",
        )
        return _error_html(
            request,
            f"生成超时（超过 {_GENERATION_HARD_TIMEOUT_S:.0f} 秒），请尝试缩短文本",
        )
    except Exception as e:
        duration = time.monotonic() - start_time
        logger.error(f"流式生成失败: {e}")
        _log_generation("Streaming", text, "voxcpm2", "streaming", False, duration, error_msg=str(e))
        return _error_html(request, _safe_error_msg(e))
    finally:
        # S-R3: 确保信号量释放（E4 资源安全）
        semaphore.release()


# ====================================================================
# 路由 3: 流式音频（分段生成 + 一次性返回 + 自动播放）
# ====================================================================


@router.post(
    "/streaming_audio",
    summary="流式音频",
    description="流式音频生成与播放",
)
async def streaming_audio_generation(
    request: Request,
    text: str = Form(""),
    persona_name: str = Form(""),
    cfg_value: float = Form(2.0),
    inference_timesteps: int = Form(10),
    denoise: str = Form("true"),
) -> HTMLResponse:
    """VoxCPM2 流式音频路由（分段生成 + 自动播放 HTML）。

    与 /streaming 的区别：返回的 HTML 片段内嵌 ``window.globalAudioPlayer.play()``
    脚本，HTMX 替换 DOM 后会自动立即开始播放，适合"点一下就听"的快速交互。
    推理层面使用 prefer_streaming=False（一次性 generate）保留原行为。

    Args:
        request: FastAPI Request 对象。
        text: 待合成文本。
        persona_name: 音色名称（allow_missing=True，缺失用默认）。
        cfg_value: CFG 强度。
        inference_timesteps: 每段推理步数。
        denoise: 是否降噪（"true"/"false"）。

    Returns:
        HTMLResponse: 携带 <audio> 元素 + 自动播放 JS 的 HTML 片段。
    """
    # S-R1: 复用 pre_validate
    error: HTMLResponse | None = pre_validate(request, "voxcpm2", text, MAX_TEXT_LENGTH)
    if error is not None:
        return error

    stream_denoise: bool = _parse_bool_form(denoise)

    # S-R2: 统一 persona 加载（allow_missing=True，缺失用默认音色）
    actual_ref_path: str | None
    persona_error: HTMLResponse | None
    actual_ref_path, persona_error = await _load_streaming_persona(request, persona_name, allow_missing=True)
    if persona_error is not None:
        return persona_error

    # S-R3: 加信号量
    semaphore: asyncio.Semaphore | None
    sem_error: HTMLResponse | None
    semaphore, sem_error = await _acquire_streaming_semaphore(request)
    if sem_error is not None:
        return sem_error

    start_time: float = time.monotonic()
    try:
        segments: list[str] = split_text_for_tts(text)
        all_audio_data: list[np.ndarray] = []

        for _seg_idx, seg in enumerate(segments):
            seg = seg.strip()
            if not seg:
                continue

            # S-R1: 复用 _generate_segment_async（含超时保护）
            # prefer_streaming=False 保留原行为（用 generate 而非 generate_streaming）
            wav_data: np.ndarray = await _generate_segment_async(
                seg,
                actual_ref_path,
                cfg_value,
                inference_timesteps,
                stream_denoise,
                prefer_streaming=False,
            )

            if hasattr(wav_data, "numpy"):
                wav_data = wav_data.numpy()
            all_audio_data.append((wav_data * 32767).astype(np.int16))

        # S-R1: 复用 _merge_and_save_wav
        filename, duration_sec = await _merge_and_save_wav(all_audio_data, "streaming")

        duration: float = time.monotonic() - start_time
        _log_generation("Streaming", text, "voxcpm2", "streaming", True, duration)

        safe_filename: str = quote(filename)
        safe_display: str = html.escape(filename)
        return HTMLResponse(
            f"""<div class="tts-success-block">流式生成完成！音频已开始播放 ({safe_display})</div>
<audio class="tts-audio-hidden" id="streaming-audio">
    <source src="/output/{safe_filename}" type="audio/wav">
</audio>
{_EMBEDDED_PLAYER_HTML.format(audio_url="/output/" + safe_filename)}
<script>
(function(){{
    var audio = document.getElementById('streaming-audio');
    if (audio && window.globalAudioPlayer) {{
        window.globalAudioPlayer.play(audio.querySelector('source').src, '{safe_display}');
    }} else if (audio) {{
        audio.play().catch(function(e) {{
            console.warn('Auto-play blocked:', e);
        }});
    }}
}})();
</script>
"""
        )

    except Exception as e:
        duration = time.monotonic() - start_time
        logger.error(f"流式音频生成失败: {e}")
        _log_generation("Streaming", text, "voxcpm2", "streaming", False, duration, error_msg=str(e))
        return _error_html(request, _safe_error_msg(e))
    finally:
        # S-R3: 确保信号量释放（E4 资源安全）
        semaphore.release()


# ====================================================================
# 路由 4: 后处理
# ====================================================================


@router.post(
    "/post-process",
    summary="后处理",
    description="对已生成的音频进行后处理（变速、响度标准化、人声增强）",
)
async def post_process_audio(
    request: Request,
    audio_path: str = Form(""),
    tempo_factor: float = Form(1.0),
    voice_enhancement: str = Form("false"),
    target_lufs: float = Form(-16.0),
) -> HTMLResponse:
    """对已生成的音频文件进行后处理。

    支持独立于生成流程的"事后调整"：用户先正常生成一段音频试听，
    觉得"太快了" / "人声不够亮" / "太轻了"，就可以直接对 output 里的文件
    跑后处理，而不必重新推理（省 90% 时间）。

    **安全（D4）**：对 audio_path 做双维度路径遍历防护：
    1. basename 剥离（禁止 ../ 绝对路径穿透）
    2. os.path.realpath 前缀校验（禁止 symlink 逃出 SAVE_DIR）

    Args:
        request: FastAPI Request 对象。
        audio_path: 要后处理的音频文件名（仅限 SAVE_DIR 内，纯 basename）。
        tempo_factor: 变速倍率，1.0 为原速。
        voice_enhancement: 是否启用人声增强（"true"/"false"）。
        target_lufs: 响度归一化目标 (LUFS)，默认 -16.0。

    Returns:
        HTMLResponse: HTMX 格式 HTML 片段，携带后处理后新文件名。
    """
    # SECURITY: 输入校验加固（D4 路径遍历防护）
    if not audio_path.strip():
        return _error_html(request, "audio_path is required")

    # 第 1 层：强制 basename，丢弃任何目录层级 / ../ / 绝对盘符
    safe_name: str = os.path.basename(audio_path)
    if safe_name != audio_path:
        return _error_html(request, "Invalid audio path")

    full_path: str = os.path.join(SAVE_DIR, safe_name)
    real_path: str = os.path.realpath(full_path)
    # SECURITY: 防止路径遍历（symlink escape）— 符号链接指向 SAVE_DIR 外
    # 的文件同样被拒绝，避免用户通过 symlink 后处理任意系统音频。
    if not real_path.startswith(os.path.realpath(SAVE_DIR)):
        return _error_html(request, "Invalid audio path")

    if not os.path.isfile(real_path):
        return _error_html(request, "Audio file not found")

    pp_voice_enhancement: bool = _parse_bool_form(voice_enhancement)
    new_filename: str = await asyncio.to_thread(
        _apply_post_processing_to_file,
        safe_name,
        tempo_factor,
        pp_voice_enhancement,
        target_lufs,
    )

    # 四个参数全为默认值时 _apply_post_processing_to_file 会直接 return 原文件，
    # 此时提示用户"啥也没干"而不是返回成功——避免"我点了后处理但听起来没变"的困惑。
    if new_filename == safe_name and tempo_factor == 1.0 and not pp_voice_enhancement and target_lufs == -16.0:
        return _error_html(request, "No post-processing changes requested")

    safe_new: str = quote(new_filename, safe="")
    return HTMLResponse(
        f'<div data-audio-filename="{html.escape(new_filename)}">'
        f'<audio class="tts-audio-hidden" src="/api/audio/{safe_new}"></audio>'
        f"{_EMBEDDED_PLAYER_HTML.format(audio_url='/api/audio/' + safe_new)}"
        f'<div class="status-message success">Post-processing applied</div>'
        f"</div>"
    )


# ====================================================================
# 路由 5: 取消生成
# ====================================================================


@router.post(
    "/cancel",
    summary="取消生成",
    description="取消正在进行的语音生成任务",
)
async def cancel_generation(request: Request) -> dict[str, str]:
    """取消当前正在进行的流式/非流式生成任务。

    实际效果：设置 model_manager._progress_mgr.cancel_flag = True，
    段级推理在"下一段开始前"会检测到 flag 并提前退出（无法中断 torch 正在
    跑的段内推理——那是 GPU 线程级别的，需要 torch.cuda.set_device + event 协调）。

    Args:
        request: FastAPI Request 对象。

    Returns:
        dict: 标准化 JSON 响应 ``{"status": "ok", "message": "..."}``。
    """
    from ....model_manager import _progress_mgr

    current_eng: str = registry.current_engine
    was_generating: bool = not _progress_mgr._is_complete and _progress_mgr._phase != ""
    _progress_mgr.cancel()
    logger.info(f"[Cancel] Generation cancel requested (engine: {current_eng}, was generating: {was_generating})")
    return {"status": "ok", "message": "已发送取消请求"}
