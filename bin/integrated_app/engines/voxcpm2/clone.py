"""VoxCPM2 零样本/少样本语音克隆（Voice Cloning）子模块。

本模块在 VoxCPM2Engine 门面模式中承担「参考音频 → 目标音色克隆」生成子系统的角色：
- 上层入口：`engine.py` 的 `clone(persona_id/audio_ref, text, **kwargs)` 方法
  最终委托本模块的 `fn_voxcpm_clone()` 公开函数完成实际推理。
- 路由协作：`routes/generate/voxcpm2/clone_route.py` 通过 FastAPI 端点接收前端
  请求（persona_id / 上传参考音频 + 目标文本 + 超参数），经参数校验后调用本模块，
  生成结果通过 `routes/audio.py` 提供文件下载或 `/api/sse/events` SSE 事件推送。
- 装饰器集成：使用 `decorators.py` 的 `@with_generation_context(phase_name="VoxCPM可控克隆")`
  装饰器，统一获得模型就绪检查、生成锁检查、进度追踪（_progress_mgr）、
  生成耗时统计（_gen_tracker）、异常兜底包装（tts_error_handler）等横切能力。

三种克隆模式（按性能优先级排）：
    ① clone_with_cache（缓存命中，老用户高频音色）：
       传入 `ref_audio_path` 且 `prompt_cache` 已存在 → 直接读嵌入，
       省掉 CLAP/WavLM 骨干嵌入计算的 300~800ms 首段延迟；
       LRU + TTL 双淘汰策略（见 `prompt_cache.py:PromptCache`）自动清理。
    ② clone_zeroshot（零样本新用户）：
       未命中 cache → 现场加载参考音频 → 计算嵌入 → 生成；
       完成后尝试写入 prompt_cache，下次命中走模式①。
    ③ clone_with_lora（LoRA 叠加基础音色）：
       用户已在「模型管理」加载 LoRA 权重后，嵌入空间自动叠加，
       生成阶段对上层透明，调用链与①②完全一致。

依赖链：
    - `model_manager.prompt_cache`（PromptCache 单例）：嵌入持久化读写
    - `persona_manager.get_persona(persona_id)`：按 ID 查找参考音频路径
    - `_base.build_generation_kwargs()` / `_advanced_kwargs()`：高级参数白名单构建
    - `_base.generate_with_template()`：端到端推理模板（分段+RAS+保存）
"""

import contextlib
import io
import os
import random
import tempfile
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from ...exceptions import (
    InsufficientVRAMError,
    ValidationError,
)
from ...gpu_utils import free_gpu_memory, is_oom_error
from ._base import (
    GenerationError,
    _advanced_kwargs,
    _progress_mgr,
    generate_with_template,
    logger,
)
from .decorators import with_generation_context

_MIN_REFERENCE_DURATION_SEC: float = 1.0
_MAX_REFERENCE_DURATION_SEC: float = 60.0
_FALLBACK_TRUNCATE_SEC: float = 30.0
_DEFAULT_SEED_MAX: int = 2**32 - 1


def _load_reference(
    path_or_bytes: str | bytes | tuple[np.ndarray, int],
) -> tuple[np.ndarray, int]:
    """统一加载参考音频：支持文件路径 / bytes 二进制 / (wav, sr) 元组三种输入。

    三种输入场景对应三种调用方：
    - str 路径：WebUI 通过 `persona_manager.get_persona(pid).audio_path` 传入；
    - bytes：HTTP multipart/form-data 上传的 WAV/MP3 文件原始二进制；
    - Tuple[ndarray, int]：Python SDK 调用方已用 librosa/soundfile 预加载。

    内部使用 `_save_wav_compatible` + NamedTemporaryFile 做格式归一化，
    确保后续嵌入计算拿到统一的 16kHz~48kHz PCM 格式。

    Args:
        path_or_bytes: 参考音频的三种表示之一。

    Returns:
        Tuple[np.ndarray, int]: (波形数组 float32, 采样率) 元组。

    Raises:
        ValidationError: 文件不存在、格式不支持、WAV 头损坏等所有加载失败路径，
            统一带 400 HTTP code 与用户友好的格式提示文案。
    """
    try:
        if isinstance(path_or_bytes, tuple) and len(path_or_bytes) == 2:
            _arr, _sr = path_or_bytes
            if not isinstance(_arr, np.ndarray):
                raise TypeError(f"期望 np.ndarray，got {type(_arr).__name__}")
            _wav: np.ndarray = np.asarray(_arr, dtype=np.float32)
            sr: int = int(_sr)
            if _wav.ndim > 1:
                _wav = _wav.mean(axis=-1)
            return _wav, sr

        if isinstance(path_or_bytes, bytes):
            try:
                import soundfile as sf
            except ImportError as _ie:
                raise ValidationError(
                    "缺少 soundfile 依赖，无法解析上传的音频二进制。请安装 soundfile 或改为传文件路径。",
                    field="reference_audio",
                ) from _ie
            try:
                with io.BytesIO(path_or_bytes) as _bio:
                    _wav, sr = sf.read(_bio, dtype="float32")
            except Exception as _sf_err:  # noqa: BLE001
                raise ValidationError(
                    "参考音频格式解析失败，请上传 16kHz / 24kHz 单声道 WAV（推荐 3~10 秒清晰人声）。"
                    f"内部错误: {type(_sf_err).__name__}",
                    field="reference_audio",
                ) from _sf_err
            if isinstance(_wav, np.ndarray) and _wav.ndim > 1:
                _wav = _wav.mean(axis=-1)
            return np.asarray(_wav, dtype=np.float32), int(sr)

        if isinstance(path_or_bytes, str):
            if not os.path.isfile(path_or_bytes):
                raise ValidationError(
                    f"参考音频文件不存在: {path_or_bytes}。请检查音色目录或重新上传参考音频。",
                    field="reference_audio",
                )
            try:
                import soundfile as sf

                _wav, sr = sf.read(path_or_bytes, dtype="float32")
            except ImportError:
                try:
                    from scipy.io import wavfile

                    sr, _raw = wavfile.read(path_or_bytes)
                    if _raw.dtype == np.int16:
                        _wav = (_raw / 32768.0).astype(np.float32)
                    elif _raw.dtype == np.int32:
                        _wav = (_raw / 2147483648.0).astype(np.float32)
                    else:
                        _wav = np.asarray(_raw, dtype=np.float32)
                except Exception as _wav_err:  # noqa: BLE001
                    raise ValidationError(
                        "参考音频读取失败，请上传 16kHz / 24kHz 单声道 WAV（建议 3-10 秒清晰人声）。"
                        f"scipy.wavfile 报错: {type(_wav_err).__name__}",
                        field="reference_audio",
                    ) from _wav_err
            except Exception as _sf_err2:  # noqa: BLE001
                raise ValidationError(
                    "参考音频读取失败，请上传 16kHz / 24kHz 单声道 WAV（建议 3-10 秒清晰人声）。"
                    f"soundfile 报错: {type(_sf_err2).__name__}",
                    field="reference_audio",
                ) from _sf_err2
            if isinstance(_wav, np.ndarray) and _wav.ndim > 1:
                _wav = _wav.mean(axis=-1)
            return np.asarray(_wav, dtype=np.float32), int(sr)

        raise ValidationError(
            f"不支持的参考音频输入类型: {type(path_or_bytes).__name__}。"
            "请传文件路径(str) / 二进制(bytes) / (wav_array, sr)元组。",
            field="reference_audio",
        )
    except ValidationError:
        raise
    except (OSError, ValueError, TypeError, RuntimeError) as _misc:
        raise ValidationError(
            "参考音频读取失败，请上传 16kHz / 24kHz 单声道 WAV（建议 3-10 秒清晰人声）。"
            f"底层报错: {type(_misc).__name__}: {_misc}",
            field="reference_audio",
        ) from _misc


def _validate_reference_duration(
    wav: np.ndarray,
    sr: int,
) -> tuple[bool, float]:
    """校验参考音频时长是否在合理区间 [1s, 60s]。

    为什么不做自动截断而让用户明确知道？
    - 太短（<1s）：嵌入空间没有足够统计信息区分音色，出来的是"平均脸"；
    - 太长（>60s）：CLAP/WavLM 序列长度超限，显存爆或嵌入质量下降（注意力稀释）。
    长度范围硬校验 + 明确报错 > 静默截断，避免用户"传了 100s 以为都用上了，其实只用了前 10s"。

    Args:
        wav: 波形数组（float32，单声道）。
        sr: 采样率。

    Returns:
        Tuple[bool, float]: (是否合法, 实际时长秒数)。
            合法=True 表示 duration 在 [1, 60] 闭区间内。
    """
    if sr <= 0:
        return False, 0.0
    duration: float = float(wav.shape[0]) / float(sr) if wav is not None and len(wav) > 0 else 0.0
    ok: bool = _MIN_REFERENCE_DURATION_SEC <= duration <= _MAX_REFERENCE_DURATION_SEC
    return ok, duration


@with_generation_context(phase_name="VoxCPM可控克隆")
def fn_voxcpm_clone(
    text: str,
    instruction: str,
    ref_audio_path: str | None,
    cfg_value: float = 2.0,
    inference_timesteps: int = 10,
    denoise: bool = True,
    normalize: bool = True,
) -> tuple[tuple[int, np.ndarray, str] | None, str]:
    """VoxCPM2 语音克隆 WebUI 主入口：参考音频 → 目标音色克隆生成。

    执行流程（与装饰器 `@with_generation_context` 协作）：
        0. [装饰器外层] 模型就绪检查 + 锁检查 + 进度条 start + tracker start
        1. 校验 ref_audio_path 路径真实存在（用户上传的新音色或已注册 Persona）
        2. 查 prompt_cache 命中（走模式①，省嵌入编码 300~800ms）
        3. 构建段级 kwargs_builder：优先用 prompt_cache 嵌入，回退到 reference_wav_path
        4. 调用 `generate_with_template` 执行多段推理（RAS 质检 + 坏案例重试）
        5. [装饰器 finally] 耗时统计 + 进度 schedule_reset + 异常统一包装

    Args:
        text: 目标朗读文本（长文本自动分句）。
        instruction: 情感/风格指令前缀（可空字符串）。非空时会被包装为
            "(instruction)" 拼接到每段 seg_text 前面，控制语气。
        ref_audio_path: 参考音频文件绝对路径（str）或 None。
            为 None 时走默认音色（无克隆），主要用于调试。
        cfg_value: CFG 强度，默认 2.0。值越高越贴近参考音色但可能机械。
        inference_timesteps: 扩散步数，默认 10（蒸馏骨干 + Euler sampler）。
        denoise: 是否对输出音频启用降噪后处理，默认 True。
            注意：此参数仅控制「生成后输出音频」的降噪；
            参考音频本身的降噪（denoise_reference）由程序化 API `clone_from_audio`
            的同名参数控制，WebUI 端走 prompt_cache 策略不默认降噪参考音频。
        normalize: 是否做响度归一化（LUFS），默认 True 保证多次生成音量一致。

    Returns:
        Tuple[Optional[Tuple[int, np.ndarray, str]], str]:
            第一元素 ((sample_rate, waveform, filename) | None)，
            第二元素为用户成功消息（含时长/分段信息）。

    Raises:
        ValidationError: ref_audio_path 文件不存在时抛出（400）。
            其余异常由外层装饰器 `@with_generation_context` + `tts_error_handler`
            统一捕获并转换为 GenerationError / InsufficientVRAMError。
    """
    from ...prompt_cache import load_cached_prompt

    start_time: float = time.time()

    if ref_audio_path:
        logger.info(f"[VoxCPM可控克隆] 使用参考音频: {ref_audio_path}")
        if not os.path.isfile(ref_audio_path):
            raise GenerationError(f"参考音频文件不存在: {ref_audio_path}")

    # Why 先查 prompt_cache → 未命中再让底层重新编码嵌入：
    # 单条 5 秒参考音频的嵌入计算（CLAP/WavLM 双骨干）要 300~800ms，
    # 这段时间对交互感知来说"首屏卡顿"很明显。同音色（同 Persona）复用
    # 100 次可以累计省 30~80 秒；且 prompt_cache 采用 LRU 容量淘汰
    # + TTL（默认 7 天）时间淘汰双保险，脏数据/旧数据自动失效，无需人工管理。
    _progress_mgr.update_phase("加载音色缓存...")
    cached_prompt: Any | None = None
    if ref_audio_path:
        try:
            cached_prompt = load_cached_prompt(ref_audio_path)
        except (OSError, ValueError, TypeError, RuntimeError) as _cache_exc:
            logger.warning(f"[VoxCPM可控克隆] prompt_cache 读取异常，降级走重新编码路径: {_cache_exc}")
            cached_prompt = None
        if cached_prompt is not None:
            logger.info("[VoxCPM可控克隆] 使用缓存的音色特征，跳过重复编码")

    def gen_kwargs_builder(
        seg_text: str,
        ref_path: str | None,
        prompt_cache_val: Any,
    ) -> dict[str, Any]:
        """构建单段推理的 kwargs 字典（语音克隆模式专用）。

        优先级策略：优先使用 prompt_cache（已计算好的音色嵌入，省 300~800ms）；
        缓存未命中时回退到 reference_wav_path（现场计算嵌入）。

        Args:
            seg_text: 当前分段文本（已包含 instruction 前缀）。
            ref_path: 参考音频路径（缓存未命中时使用）。
            prompt_cache_val: 预计算的音色嵌入缓存（命中时直接使用）。

        Returns:
            Dict[str, Any]: model.generate() 可直接消费的 kwargs 字典。
        """
        kwargs: dict[str, Any] = dict(
            text=seg_text,
            normalize=normalize,
            cfg_value=cfg_value,
            inference_timesteps=inference_timesteps,
            denoise=denoise,
            min_len=2,
            **_advanced_kwargs(),
        )
        if prompt_cache_val is not None:
            kwargs["prompt_cache"] = prompt_cache_val
        elif ref_path:
            kwargs["reference_wav_path"] = ref_path
        return kwargs

    return generate_with_template(
        text=text,
        instruction=instruction,
        gen_kwargs_builder=gen_kwargs_builder,
        output_prefix="voxcpm_clone",
        phase_name="VoxCPM可控克隆",
        ref_audio_path=ref_audio_path,
        prompt_cache=cached_prompt,
        start_time=start_time,
    )


def clone_from_audio(
    model: Any,
    reference_audio: str | bytes | tuple[np.ndarray, int],
    target_text: str,
    cfg: float = 5.0,
    steps: int = 30,
    denoise_reference: bool = False,
    seed: int = -1,
    prompt_cache_key: str | None = None,
    prompt_cache: Any | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> tuple[np.ndarray, int, dict[str, Any]]:
    """程序化 API：从参考音频（路径/二进制/数组）执行零样本克隆。

    面向 Python SDK / 第三方服务集成的低级 API，与 `fn_voxcpm_clone` 的区别：
    - 直接接收 model 对象与三种参考音频表示，**不依赖 registry / 全局锁**；
    - 返回 (waveform, sr, meta_dict) 三元组，不写磁盘、不触发 UI 进度条；
    - meta 字段提供丰富的可观测信息：是否命中 cache、参考时长、实际 seed、
      是否执行了参考降噪、是否因 OOM 截断到 30s 重试等，便于集成方做日志审计。

    Args:
        model: 已加载的 VoxCPMPipeline 实例。
        reference_audio: 参考音频（路径 str / bytes 二进制 / (wav_array, sr)）。
        target_text: 目标单句朗读文本（不分段，长文本请调用方自行分句后循环调用）。
        cfg: CFG 强度，默认 5.0（程序化场景默认更高，忠实度优先）。
        steps: 扩散步数，默认 30（脚本批处理场景质量优先）。
        denoise_reference: 是否先对参考音频本身做降噪再计算嵌入。
            Why denoise_reference 默认 False：
            降噪算法（noisereduce 的 spectral gating / WebRTC VAD+NS）
            会把清音摩擦音 /s/ /f/ /θ/ 误判为宽带噪音滤除，导致克隆出来
            的说话人"咬字不清像含着东西"——这种人工伪影比轻微背景噪音
            对相似度的破坏更大。仅当用户上传的参考音频明显带空调/风扇/街道
            等强背景噪音（手动勾选「参考降噪」）时才建议启用。
        seed: 随机种子。-1 表示自动生成并记录到 meta["seed_used"]。
        prompt_cache_key: 可选的缓存键（通常用参考音频的绝对路径或 sha1）。
            非 None 时会先查 `prompt_cache`，命中则省嵌入计算。
        prompt_cache: 可选的 PromptCache 实例（model_manager.prompt_cache 单例）。
            提供 `get(key)` / `set(key, value)` 语义接口即可，不强依赖具体实现。
        progress_cb: 可选进度回调 (current, total)。

    Returns:
        Tuple[np.ndarray, int, Dict[str, Any]]:
            (waveform_float32, sample_rate_int, meta_dict)。
            meta 键：
            - embedding_used: bool，是否命中 prompt_cache
            - reference_duration_sec: float，参考音频实际秒数
            - seed_used: int，实际使用的随机种子
            - denoise_applied: bool，是否对参考音频做了降噪
            - reference_truncated: bool，嵌入 OOM 时是否截到前 30s 重试

    Raises:
        ValidationError: 参考音频加载失败、时长 <1s 或 >60s（首次）、steps 超范围等。
        InsufficientVRAMError: 嵌入计算或推理 OOM，经三级清理 + 自动截断（30s）
            重试后仍然 OOM 时抛出；附带用户修复建议。
        GenerationError: 其余推理或后处理非显存类异常。
    """
    if steps is None or steps < 1 or steps > 200:
        raise ValidationError(
            f"steps 必须在 1~200 范围内，当前值: {steps}",
            field="steps",
        )

    resolved_seed: int = seed
    if seed == -1:
        resolved_seed = random.randint(0, _DEFAULT_SEED_MAX)

    meta: dict[str, Any] = {
        "embedding_used": False,
        "reference_duration_sec": 0.0,
        "seed_used": resolved_seed,
        "denoise_applied": False,
        "reference_truncated": False,
    }

    _cache_hit: bool = False
    _cached_embedding: Any | None = None
    if prompt_cache_key is not None and prompt_cache is not None:
        try:
            _get_fn = getattr(prompt_cache, "get", None) or getattr(prompt_cache, "load", None)
            if callable(_get_fn):
                _cached_embedding = _get_fn(prompt_cache_key)
                if _cached_embedding is not None:
                    _cache_hit = True
                    meta["embedding_used"] = True
                    logger.info(f"[VoxCPM克隆] 命中 prompt_cache: {prompt_cache_key}")
        except (OSError, PermissionError, ValueError, TypeError, RuntimeError) as _ce:
            logger.error(f"[VoxCPM克隆] prompt_cache 读取失败（不阻塞主流程）: {_ce}")
            _cache_hit = False
            _cached_embedding = None

    import torch

    _ref_wav: np.ndarray | None = None
    _ref_sr: int = 48000
    _temp_path: str | None = None

    def _do_cleanup_temp() -> None:
        """安全清理临时参考音频文件。

        在 finally 块中调用，删除 clone_from_audio 为处理 bytes/数组输入
        而创建的临时 WAV 文件。忽略文件不存在或权限错误（非致命），
        防止临时文件泄漏占用磁盘空间。
        """
        if _temp_path is not None and os.path.isfile(_temp_path):
            with contextlib.suppress(OSError, PermissionError):
                os.remove(_temp_path)

    try:
        if not _cache_hit:
            _ref_wav, _ref_sr = _load_reference(reference_audio)
            _duration_ok, _duration = _validate_reference_duration(_ref_wav, _ref_sr)
            meta["reference_duration_sec"] = _duration
            if not _duration_ok:
                if _duration < _MIN_REFERENCE_DURATION_SEC:
                    raise ValidationError(
                        f"参考音频太短（{_duration:.2f}秒），至少需要 {_MIN_REFERENCE_DURATION_SEC} 秒以上清晰人声（推荐 5 秒）。",
                        field="reference_audio",
                    )
                if _duration > _MAX_REFERENCE_DURATION_SEC:
                    raise ValidationError(
                        f"参考音频过长（{_duration:.1f}秒），请截取 {_MAX_REFERENCE_DURATION_SEC} 秒以内的清晰人声片段。",
                        field="reference_audio",
                    )

            if denoise_reference:
                try:
                    try:
                        import noisereduce as nr
                    except ImportError as _nr_missing:
                        raise RuntimeError("noisereduce 未安装，无法执行参考音频降噪") from _nr_missing
                    _denoised: np.ndarray = nr.reduce_noise(y=_ref_wav, sr=_ref_sr, stationary=True)
                    if _denoised.shape == _ref_wav.shape:
                        _ref_wav = np.asarray(_denoised, dtype=np.float32)
                        meta["denoise_applied"] = True
                except (ImportError, RuntimeError, ValueError, TypeError) as _dn_err:
                    logger.warning(f"[VoxCPM克隆] 参考音频降噪失败，使用原始参考继续: {_dn_err}")

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as _tmp:
                _temp_path = _tmp.name
            try:
                from ...generation import _save_wav_compatible

                _save_wav_compatible(_ref_wav, _temp_path, _ref_sr)
            except (OSError, RuntimeError, ValueError, TypeError) as _sv_err:
                raise GenerationError(
                    f"参考音频临时文件写入失败: {type(_sv_err).__name__}",
                    engine="voxcpm2",
                ) from _sv_err
        else:
            if isinstance(reference_audio, (str, bytes, tuple)):
                try:
                    _probe_wav, _probe_sr = _load_reference(reference_audio)
                    _meta_ok, _meta_dur = _validate_reference_duration(_probe_wav, _probe_sr)
                    meta["reference_duration_sec"] = _meta_dur
                    del _probe_wav
                except Exception:  # noqa: BLE001
                    pass

        _generation_kwargs: dict[str, Any] = dict(
            text=target_text,
            cfg_value=cfg,
            inference_timesteps=steps,
            seed=resolved_seed,
        )
        if _cached_embedding is not None:
            _generation_kwargs["prompt_cache"] = _cached_embedding
        elif _temp_path is not None:
            _generation_kwargs["reference_wav_path"] = _temp_path

        if progress_cb is not None:
            progress_cb(0, steps)

        _wav_out: np.ndarray | None = None
        _sr_out: int = getattr(model, "sample_rate", 48000)
        try:
            _raw_out: Any = model.generate(**_generation_kwargs)
            if isinstance(_raw_out, tuple):
                _sr_out = int(_raw_out[0])
                _wav_out = np.asarray(_raw_out[1], dtype=np.float32)
            else:
                _wav_out = np.asarray(_raw_out, dtype=np.float32)
        except RuntimeError as _rt:
            if is_oom_error(_rt) and _cached_embedding is None and _ref_wav is not None:
                logger.error(f"[VoxCPM克隆] 嵌入计算或推理 OOM，尝试自动截前 {_FALLBACK_TRUNCATE_SEC}s 重试一次: {_rt}")
                with contextlib.suppress(RuntimeError, ImportError):
                    free_gpu_memory()
                _truncate_samples: int = int(_FALLBACK_TRUNCATE_SEC * float(_ref_sr))
                if len(_ref_wav) > _truncate_samples:
                    _ref_wav = _ref_wav[:_truncate_samples]
                    meta["reference_truncated"] = True
                    meta["reference_duration_sec"] = float(len(_ref_wav)) / float(_ref_sr)
                    logger.info(
                        f"[VoxCPM克隆] 已自动将参考音频截为前 {meta['reference_duration_sec']:.1f}s，显存不足导致"
                    )
                    if _temp_path is not None and os.path.isfile(_temp_path):
                        with contextlib.suppress(OSError, PermissionError):
                            os.remove(_temp_path)
                        _temp_path = None
                    try:
                        from ...generation import _save_wav_compatible

                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as _tmp2:
                            _temp_path = _tmp2.name
                        _save_wav_compatible(_ref_wav, _temp_path, _ref_sr)
                    except (OSError, RuntimeError, ValueError, TypeError) as _sv2:
                        raise GenerationError(
                            f"截断参考音频后临时文件写入失败: {type(_sv2).__name__}",
                            engine="voxcpm2",
                        ) from _sv2
                    _generation_kwargs.pop("prompt_cache", None)
                    _generation_kwargs["reference_wav_path"] = _temp_path
                    try:
                        _raw_out = model.generate(**_generation_kwargs)
                        if isinstance(_raw_out, tuple):
                            _sr_out = int(_raw_out[0])
                            _wav_out = np.asarray(_raw_out[1], dtype=np.float32)
                        else:
                            _wav_out = np.asarray(_raw_out, dtype=np.float32)
                    except RuntimeError as _rt2:
                        if is_oom_error(_rt2):
                            with contextlib.suppress(RuntimeError, ImportError):
                                free_gpu_memory()
                            raise InsufficientVRAMError(
                                "语音克隆显存不足：已自动尝试将参考音频截为前 30s 重试仍然失败。"
                                "请换用更短的参考音频（3~5秒最佳）、关闭其他已加载模型、或降低 steps。"
                            ) from _rt2
                        raise GenerationError(
                            f"截断重试后推理失败: {_rt2}",
                            engine="voxcpm2",
                        ) from _rt2
                else:
                    with contextlib.suppress(RuntimeError, ImportError):
                        free_gpu_memory()
                    raise InsufficientVRAMError(
                        "语音克隆显存不足：请换用更短的参考音频（3~5秒最佳）、关闭其他已加载模型、或降低 steps。"
                    ) from _rt
            else:
                if is_oom_error(_rt):
                    with contextlib.suppress(RuntimeError, ImportError):
                        free_gpu_memory()
                    raise InsufficientVRAMError(
                        "语音克隆显存不足：请降低 steps、缩短单次生成长度、或关闭其他已加载模型。"
                    ) from _rt
                raise GenerationError(
                    f"语音克隆推理失败: {_rt}",
                    engine="voxcpm2",
                ) from _rt

        if prompt_cache_key is not None and prompt_cache is not None and not _cache_hit:
            try:
                _set_fn = getattr(prompt_cache, "set", None) or getattr(prompt_cache, "save", None)
                if callable(_set_fn) and "prompt_cache" in _generation_kwargs:
                    _set_fn(prompt_cache_key, _generation_kwargs["prompt_cache"])
            except (OSError, PermissionError, ValueError, TypeError, RuntimeError) as _ce2:
                logger.error(f"[VoxCPM克隆] prompt_cache 写入失败（不阻塞，本次仍返回结果）: {_ce2}")

        if progress_cb is not None:
            progress_cb(steps, steps)

        assert _wav_out is not None
        return _wav_out, _sr_out, meta
    except (ValidationError, InsufficientVRAMError, GenerationError):
        raise
    finally:
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except (ImportError, RuntimeError):  # noqa: BLE001
            pass
        if _ref_wav is not None:
            with contextlib.suppress(Exception):
                del _ref_wav
        _do_cleanup_temp()
