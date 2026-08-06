"""VoxCPM2 零样本语音克隆（Clone/Ultimate/Prompt Continue）路由模块。

**路由前缀与端点**：
    前缀：``/api/generate/voxcpm2``
    - POST /voxcpm_clone           — 可控克隆（标准零样本克隆）
    - POST /voxcpm_ultimate        — 极致克隆（完整参数控制 + seed 复现）
    - POST /voxcpm_prompt_continue — Prompt 延续（音频+文本风格续写）

**HTTP 方法**：
    所有端点仅接受 POST 请求，参数通过 ``multipart/form-data`` 表单提交，
    支持文件上传（参考音频）。

**在 TTS 生成管线中的位置**：
    位于生成管线的"语音克隆"环节，依赖参考音频（reference_audio）提取音色嵌入，
    再合成与参考音频说话人一致的新语音。本模块提供三种克隆模式：
    1. **可控克隆**（voxcpm_clone）：标准零样本克隆，支持 cfg / denoise / norm
    2. **极致克隆**（voxcpm_ultimate）：支持 seed / denoise_strength 等完整参数控制，可复现
    3. **Prompt 延续**（voxcpm_prompt_continue）：基于一段"音频+对应文本"做风格续写

**参考音频输入优先级**（三种路径互斥，按优先级回退）：
    1. ref_audio_upload：用户本次上传的音频文件（优先级最高）
    2. ref_audio_path：服务器本地绝对/相对路径
    3. persona_name：已注册音色名称（通过 persona_manager 查实际 wav 路径）

**异步任务队列与并发控制**：
    - 通过 ``_execute_generation()`` 获取 per-engine asyncio.Semaphore（默认容量 1）
    - 信号量获取超时：120 秒；单次生成硬超时：600 秒
    - GPU 推理在 ``run_in_executor`` 线程池中执行
    - OOM 自动降级：显存不足时自动减半 steps、关闭 denoise 重试（最多 2 次）
    - Prompt 延续端点直接调用 ``_run_with_oom_retry`` 保持向后兼容

**SSE 事件说明**：
    本模块端点均为非流式同步端点，生成进度通过统一 SSE 端点
    ``/api/sse/events`` 推送：
    1. ``status`` — 任务开始，状态更新
    2. ``progress`` — 进度百分比更新（0-100）
    3. ``time_estimate`` — 预计剩余时间
    4. ``complete`` — 生成完成，携带音频 URL
    5. ``error`` / ``cancelled`` — 失败或取消

**通用生成管线流程**：
    1. 参数校验：pre_validate() 统一检查引擎就绪 + 文本非空/长度
    2. 参考音频解析：三级回退优先级链（上传 → 路径 → persona）
    3. 引擎检查：registry.current_engine == voxcpm2
    4. 串行锁：_execute_generation 或 显式 _run_with_oom_retry + semaphore
    5. 进度 SSE：ProgressManager 由底层执行器统一驱动
    6. 引擎调用：generate_voice_clone / generate_ultimate_clone / generate_with_prompt
    7. 后处理：tempo_factor（变速）/ voice_enhancement（人声增强）/ target_lufs（响度归一化）
    8. 保存 & History：写入 SAVE_DIR + history_db.insert（S-R4 统一单例）
    9. 响应：HTMX HTML 片段（含 audio 标签 + 状态消息）

**返回格式**：
    HTTP 200: text/html — HTMX 片段，data-audio-filename 属性 + 隐藏 audio 元素
    HTTP 200 (降级): text/html — 额外显示橙色警告提示（degraded_note）
    HTTP 400: text/html — 错误片段（含 HX-Trigger toast 头）
"""

import os
import time
from typing import Any

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from ....config import MAX_TEXT_LENGTH
from ....model_registry import registry
from ....monitor import get_health_monitor
from ..utils import (
    _apply_post_processing_to_file,
    _error_html,
    _execute_generation,
    _log_generation,
    _merge_dialect,
    _parse_bool_form,
    _partial_success_html,
    _record_to_history_db,
    _run_with_oom_retry,
    _safe_error_msg,
    _success_html,
    _time_estimator,
    logger,
    pre_validate,
    resolve_persona_ref,
    router,
    save_uploaded_audio,
)

# Why：同时支持双模式输入（persona_id 引用 vs UploadFile 上传）的设计决策
# - 老用户场景（已积累 100+ Persona）：直接选 persona_id，加载路径是
#   "50ms 查 persona_manager 本地嵌入缓存"，无需重新上传和重新计算 speaker embedding。
# - 新用户场景（第一次使用）：拖入一个音频文件立即克隆，UploadFile 路径虽然多了
#   "1MB 文件上传 ~200ms 延迟 + 首次 embedding 计算 ~300ms"，但零门槛。
# 两种路径在"解析完 actual_ref_path 字符串"之后完全一致，因此合并为一。
# 注意：prompt_mode 的具体枚举由调用方在 UI 层选择不同端点保证，此处不再额外校验。
_PROMPT_MODE_CLONE: str = "clone"
_PROMPT_MODE_ULTIMATE: str = "ultimate"
_PROMPT_MODE_PROMPT_CONTINUE: str = "prompt_continue"

# Why：denoise 默认关闭（而不是默认打开）。
# 克隆的音质 90% 取决于参考音频本身的质量。noisereduce 类的频谱减法降噪算法
# 很容易"误伤"高频谐波——特别是女声/童声/气声的高频泛音，导致输出变成"机器人声"
# 或"金属感"。只有当用户明确感知到参考音频有底噪并主动勾选"降噪"时才启用，
# 以避免默认行为引入副作用（"我啥也没动，怎么克隆出来不如原来好听"）。
_DEFAULT_DENOISE_ENABLED: bool = False

# 参考音频文件大小上限。超过 50MB 的通常是长录音（> 10 分钟），用于克隆反而容易
# 引入说话人漂移（多段录音音色不一致），因此阻断并提示用户截取 10s-60s 的干净片段。
_MAX_REFERENCE_AUDIO_SIZE_MB: int = 50


@router.post(
    "/voxcpm_clone",
    summary="可控克隆",
    description="使用 VoxCPM2 引擎进行可控声音克隆，上传参考音频",
)
async def generate_voxcpm_clone(
    request: Request,
    text: str = Form(""),
    instruction: str = Form(""),
    ref_audio_path: str = Form(""),
    persona_name: str = Form(""),
    cfg: float = Form(2.0),
    norm: str = Form("true"),
    denoise: str = Form("true"),
    steps: int = Form(10),
    ref_audio_upload: UploadFile | None = File(None),
    lang: str = Form("Auto"),
    tempo_factor: float = Form(1.0),
    voice_enhancement: str = Form("false"),
    target_lufs: float = Form(-16.0),
) -> HTMLResponse:
    """VoxCPM2 可控语音克隆路由。

    支持三种参考音频输入路径（按优先级）：
    1. ref_audio_upload（用户本次上传的文件）
    2. ref_audio_path（服务器本地绝对/相对路径）
    3. persona_name（已注册音色名称，通过 resolve_persona_ref 查实际 wav）

    Args:
        request: FastAPI Request 对象。
        text: 待合成文本，必填，<= MAX_TEXT_LENGTH 字符。
        instruction: 风格/情感文本描述（如"温柔地说"、"愤怒地吼叫"）。
        ref_audio_path: 直接指定参考音频的本地路径。
        persona_name: 已注册音色（Persona）名称。
        cfg: CFG 强度，值越高与参考音频的一致性越强，推荐 [2, 7]。
        norm: 输出是否归一化（true/false 字符串）。
        denoise: 是否对参考音频做降噪预处理（true/false 字符串）。
        steps: 推理步数，越多质量越好但越慢，推荐 [10, 30]。
        ref_audio_upload: 用户上传的参考音频文件（wav/mp3/flac 等）。
        lang: 语言/方言标识，会合并到 instruction。
        tempo_factor: 后处理变速倍率，1.0 为原速。
        voice_enhancement: 是否启用人声增强。
        target_lufs: 响度归一化目标 (LUFS)，默认 -16.0。

    Returns:
        HTMLResponse: HTMX 格式 HTML 片段，携带 audio 元素。

    Raises:
        PersonaNotFoundError: 404，指定 persona_name 不存在或 wav 文件缺失。
        ValidationError: 400，文本为空 / 参考音频文件过大 / 格式不支持。
        InsufficientVRAMError: 503，推理时显存耗尽（由 OOM retry 捕获）。
    """
    # 1. 引擎就绪 + 文本非空/长度统一校验
    err: HTMLResponse | None = pre_validate(request, "voxcpm2", text, MAX_TEXT_LENGTH)
    if err is not None:
        return err

    # 2. 预处理：方言合并
    instruction = _merge_dialect(instruction, lang)

    # 3. 解析 actual_ref_path（三级回退优先级链）
    actual_ref_path: str | None = ref_audio_path if ref_audio_path else None

    # 3.1 UploadFile 上传文件（优先级最高，因为用户刚拖进来的最新）
    upload_path: str | None
    upload_path, err = await save_uploaded_audio(request, ref_audio_upload)
    if err is not None:
        return err
    if upload_path:
        actual_ref_path = upload_path

    # 3.2 Persona 名称回退（persona_manager 查嵌入缓存）
    if not actual_ref_path and persona_name:
        ref_path, err = await resolve_persona_ref(request, persona_name)
        if err is not None:
            return err
        if ref_path:
            actual_ref_path = ref_path
            logger.info(f"[VoxCPM克隆] 已加载音色 '{os.path.basename(persona_name)}' 的参考音频")

    # 4. 解析 bool 开关
    clone_norm: bool = _parse_bool_form(norm)
    clone_denoise: bool = _parse_bool_form(denoise)

    # 5. 构造生成闭包（同步，在 executor 线程中调用 GPU 推理）
    def _run():
        """正常参数下执行语音克隆生成（线程池GPU推理）"""
        engine = registry.get_current_engine()
        return engine.generate_voice_clone(
            text=text,
            reference_audio_path=actual_ref_path,
            instruction=instruction,
            cfg_value=cfg,
            inference_timesteps=steps,
            denoise=clone_denoise,
            normalize=clone_norm,
        )

    def _degraded_run():
        """OOM降级参数下执行语音克隆生成"""
        engine = registry.get_current_engine()
        degraded_steps: int = max(steps // 2, 4)
        return engine.generate_voice_clone(
            text=text,
            reference_audio_path=actual_ref_path,
            instruction=instruction,
            cfg_value=cfg,
            inference_timesteps=degraded_steps,
            denoise=False,
            normalize=clone_norm,
        )

    # 6. 统一执行器：串行信号量 + 硬超时 + OOM 降级 + 后处理 + 历史记录
    return await _execute_generation(
        request,
        text=text,
        run_fn=_run,
        endpoint_name="VoxCPM clone",
        voice_or_persona=instruction[:50],
        model_type="可控克隆",
        engine="voxcpm2",
        tempo_factor=tempo_factor,
        voice_enhancement=voice_enhancement,
        target_lufs=target_lufs,
        degraded_fn=_degraded_run,
    )


@router.post(
    "/voxcpm_ultimate",
    summary="极致克隆",
    description="使用 VoxCPM2 引擎进行极致声音克隆，支持多参考音频",
)
async def generate_voxcpm_ultimate(
    request: Request,
    text: str = Form(""),
    instruction: str = Form(""),
    ref_audio_path: str = Form(""),
    persona_name: str = Form(""),
    cfg: float = Form(2.0),
    norm: str = Form("true"),
    denoise: str = Form("true"),
    steps: int = Form(10),
    seed: int = Form(-1),
    lang: str = Form("Auto"),
    tempo_factor: float = Form(1.0),
    voice_enhancement: str = Form("false"),
    target_lufs: float = Form(-16.0),
) -> HTMLResponse:
    """VoxCPM2 极致克隆路由。

    相比"可控克隆"增加了 seed 确定性控制 + denoise 强度（0.0/1.0）开关，
    用于对生成结果可复现性和细节控制要求更高的场景。

    Args:
        request: FastAPI Request 对象。
        text: 待合成文本。
        instruction: 风格/情感文本描述。
        ref_audio_path: 参考音频本地路径。
        persona_name: 已注册音色名称。
        cfg: CFG 强度。
        norm: 输出是否归一化（true/false 字符串）。
        denoise: 参考音频降噪强度（true→1.0，false→0.0）。
        steps: 推理步数。
        seed: 随机种子，-1 表示随机；其他正值可复现结果。
        lang: 语言/方言标识。
        tempo_factor: 后处理变速倍率。
        voice_enhancement: 是否人声增强。
        target_lufs: 响度归一化目标。

    Returns:
        HTMLResponse: HTMX 格式 HTML 片段。
    """
    err: HTMLResponse | None = pre_validate(request, "voxcpm2", text, MAX_TEXT_LENGTH)
    if err is not None:
        return err

    instruction = _merge_dialect(instruction, lang)

    actual_ref_path: str | None = ref_audio_path if ref_audio_path else None

    if not actual_ref_path and persona_name:
        ref_path, err = await resolve_persona_ref(request, persona_name)
        if err is not None:
            return err
        if ref_path:
            actual_ref_path = ref_path
            logger.info(f"[VoxCPM极致克隆] 已加载音色 '{os.path.basename(persona_name)}' 的参考音频")

    advanced_norm: bool = _parse_bool_form(norm)
    advanced_denoise: float = 1.0 if _parse_bool_form(denoise) else 0.0

    def _run():
        """正常参数下执行极致克隆生成"""
        engine = registry.get_current_engine()
        return engine.generate_ultimate_clone(
            text,
            instruction,
            actual_ref_path if actual_ref_path else None,
            advanced_cfg=cfg,
            advanced_norm=advanced_norm,
            advanced_denoise=advanced_denoise,
            advanced_steps=steps,
            advanced_seed=seed,
        )

    def _degraded_run():
        """OOM降级参数下执行极致克隆生成"""
        engine = registry.get_current_engine()
        degraded_steps: int = max(steps // 2, 4)
        return engine.generate_ultimate_clone(
            text,
            instruction,
            actual_ref_path if actual_ref_path else None,
            advanced_cfg=cfg,
            advanced_norm=advanced_norm,
            advanced_denoise=0.0,
            advanced_steps=degraded_steps,
            advanced_seed=seed,
        )

    return await _execute_generation(
        request,
        text=text,
        run_fn=_run,
        endpoint_name="VoxCPM ultimate clone",
        voice_or_persona=instruction[:50],
        model_type="极致克隆",
        engine="voxcpm2",
        tempo_factor=tempo_factor,
        voice_enhancement=voice_enhancement,
        target_lufs=target_lufs,
        degraded_fn=_degraded_run,
    )


@router.post(
    "/voxcpm_prompt_continue",
    summary="Prompt 延续",
    description="使用 VoxCPM2 引擎基于 Prompt 音频延续生成",
)
async def generate_voxcpm_prompt_continue(
    request: Request,
    text: str = Form(""),
    prompt_wav: UploadFile | None = File(None),
    prompt_text: str = Form(""),
    lang: str = Form("Auto"),
    tempo_factor: float = Form(1.0),
    voice_enhancement: bool = Form(False),
    target_lufs: float | None = Form(None),
) -> HTMLResponse:
    """VoxCPM2 Prompt 延续生成路由。

    给定一段"音频（prompt_wav）+ 对应逐字稿（prompt_text）"，模型会先"念"
    一遍提示音频的说话风格（语速/停顿/情感），然后延续风格生成新的 text 内容。
    典型场景：有声书"接上次的语气继续往下念"、广播剧中"演员配了前 2 句，AI 续写"。

    Args:
        request: FastAPI Request 对象。
        text: 要续写的新文本。
        prompt_wav: 上传的 Prompt 音频文件（必须与 prompt_text 严格逐字对应）。
        prompt_text: Prompt 音频的逐字稿（必须与音频逐字对应，否则会风格漂移）。
        lang: 语言/方言标识（当前仅用于日志，模型内部自动识别）。
        tempo_factor: 后处理变速倍率，1.0 为原速。
        voice_enhancement: 是否启用人声增强后处理。
        target_lufs: 响度归一化目标 (LUFS)，None 时使用默认 -16.0。

    Returns:
        HTMLResponse: HTMX 格式 HTML 片段，成功/降级/错误。

    Raises:
        ValidationError: 400，prompt_wav 未上传 / prompt_text 为空 / 文本为空。
        InsufficientVRAMError: 503，CUDA OOM。
        ImportError: 底层依赖缺失（透传）。
    """
    err: HTMLResponse | None = pre_validate(request, "voxcpm2", text, MAX_TEXT_LENGTH)
    if err is not None:
        return err
    if not prompt_text.strip():
        return _error_html(request, "引导文本不能为空")

    # UploadFile 保存：Prompt 延续必须有音频 + 对应文本，缺一不可
    prompt_wav_path: str | None
    prompt_wav_path, err = await save_uploaded_audio(request, prompt_wav)
    if err is not None:
        return err

    if not prompt_wav_path:
        return _error_html(request, "请上传引导音频文件")

    import asyncio

    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

    def _run():
        """执行Prompt延续模式生成"""
        engine = registry.get_current_engine()
        return engine.generate_with_prompt(text, prompt_wav_path, prompt_text)

    start_time: float = time.monotonic()
    try:
        result: Any
        msg: str
        degraded_note: str | None
        # 直接调用 _run_with_oom_retry：Prompt 延续的后处理逻辑与常规生成略有差异
        # （单独判断了是否返回 tuple 以及对 prompt_text 记录日志），因此不走
        # _execute_generation 统一流程，保持原行为 100% 向后兼容。
        result, msg, degraded_note = await loop.run_in_executor(
            None,
            lambda: _run_with_oom_retry(_run, "VoxCPM prompt continue", degraded_fn=_run),
        )
        duration: float = time.monotonic() - start_time
        if result is None:
            _log_generation(
                "VoxCPM prompt continue",
                text,
                "voxcpm2",
                prompt_text[:50],
                False,
                duration,
                error_msg=msg,
            )
            return _error_html(request, msg)
        is_degraded: bool = degraded_note is not None
        _log_generation(
            "VoxCPM prompt continue",
            text,
            "voxcpm2",
            prompt_text[:50],
            True,
            duration,
            is_degraded=is_degraded,
        )
        _time_estimator.record(len(text), duration, "voxcpm2", segment_count=1)

        # 写入 history_db（S-R4: 使用统一单例 get_history_db()）
        from ....config import SAVE_DIR

        if isinstance(result, tuple) and len(result) >= 3:
            audio_path: str = os.path.join(SAVE_DIR, result[2]) if not os.path.isabs(result[2]) else result[2]
            await asyncio.to_thread(
                _record_to_history_db,
                filepath=audio_path,
                text=text,
                engine="voxcpm2",
                duration=duration,
                model_type="Prompt延续",
                output_format="wav",
                is_success=True,
            )
        monitor = get_health_monitor()
        monitor.record_generation(success=True)
        filename: str = result[2]
        pp_target_lufs: float = target_lufs if target_lufs is not None else -16.0
        filename = await asyncio.to_thread(
            _apply_post_processing_to_file,
            filename,
            tempo_factor,
            voice_enhancement,
            pp_target_lufs,
        )
        if degraded_note:
            return _partial_success_html(filename, msg, degraded_note)
        return _success_html(filename, msg)
    except Exception as e:
        duration = time.monotonic() - start_time
        logger.error(f"VoxCPM Prompt 延续生成失败: {e}")
        _log_generation(
            "VoxCPM prompt continue",
            text,
            "voxcpm2",
            prompt_text[:50],
            False,
            duration,
            error_msg=str(e),
        )
        # 未处理的异常：包装为安全消息返回；真异常栈由全局 error_handler 兜底
        return _error_html(request, _safe_error_msg(e))
