"""VoxCPM2 语音设计（Text-to-Voice Design）路由模块。

**路由前缀与端点**：
    前缀：``/api/generate/voxcpm2``
    - POST /voxcpm_design — 语音设计生成

**HTTP 方法**：
    所有端点仅接受 POST 请求，参数通过 ``multipart/form-data`` 表单提交。

**在 TTS 生成管线中的位置**：
    位于生成管线的"语音设计"环节，是 VoxCPM2 引擎从纯文本描述直接合成
    指定音色风格语音的入口。区别于"克隆"（需要参考音频）和"剧本"（多角色），
    本路由仅需 voice_description 文本描述即可塑造全新音色，常用于：
    - 从零开始设计一个新声音（如"温柔女声，25岁，播音腔"）
    - 基于已有 persona 的参考音频 + 文本描述微调音色
    - 快速原型迭代（调整 cfg / timesteps / seed 组合试听效果）

**异步任务队列与并发控制**：
    - 通过 ``_execute_generation()`` 获取 per-engine asyncio.Semaphore（默认容量 1）
    - 信号量获取超时：120 秒（排队等待上限）
    - 单次生成硬超时：600 秒（防止超长文本阻塞队列）
    - GPU 推理在 ``run_in_executor`` 线程池中执行，不阻塞 asyncio 事件循环
    - OOM 自动降级：显存不足时自动减半 steps、关闭 denoise 重试（最多 2 次）

**SSE 事件说明**：
    本端点为非流式同步端点，不直接返回 SSE 流。生成进度通过统一 SSE 端点
    ``/api/sse/events`` 推送，事件序列：
    1. ``status`` — 任务开始，状态更新为"生成中"
    2. ``progress`` — 进度百分比更新（0-100）
    3. ``time_estimate`` — 预计剩余时间
    4. ``complete`` — 生成完成，携带音频 URL 和文件名
    5. ``error`` / ``cancelled`` — 生成失败或被取消

**通用生成管线流程（与其他 VoxCPM2 非流式路由一致）**：
    1. 参数校验：文本非空 + 长度限制（MAX_TEXT_LENGTH）+ instruction 软限制提示
    2. 引擎检查：registry.current_engine == voxcpm2 且模型已加载
    3. 串行锁：通过 _execute_generation 内部的信号量串行化
    4. 进度 SSE：由 _execute_generation_impl 统一驱动 ProgressManager
    5. 引擎调用：engine.generate_voice_design()（在 executor 线程中执行）
    6. 后处理：响度归一化（target_lufs）/ 变速（tempo_factor）/ 人声增强（voice_enhancement）
    7. 保存 & History：写入 SAVE_DIR + history_db 记录（S-R4 统一单例）
    8. 响应：HTMX HTML 片段（含隐藏 audio 标签 + 成功/降级/错误文案）

**返回格式**：
    HTTP 200: text/html — HTMX 片段，包含：
    - ``<div data-audio-filename="{filename}">`` 容器
    - ``<audio class="tts-audio-hidden" src="/api/audio/{filename}">`` 隐藏音频元素
    - ``<div class="status-message success/warning/error">`` 状态消息
    HTTP 400: text/html — 错误片段（含 HX-Trigger toast 头）
"""

import os

from fastapi import Form, Request
from fastapi.responses import HTMLResponse

from ....config import MAX_TEXT_LENGTH
from ....model_registry import registry
from ..utils import (
    _check_engine_ready,
    _error_html,
    _execute_generation,
    _merge_dialect,
    _parse_bool_form,
    logger,
    router,
)

# Why：voice_description（即本路由中的 instruction 字段）的有效 token 窗口受限
# VoxCPM2 底层模型的 CrossAttention 文本描述层使用了与 CLIP 类似的 77 token 窗口，
# 超过部分会被静默截断。中文字符与 token 的比例大致为 2.5~3:1，因此将 200 中文字符
# 作为软上限白名单提示（实际 hard limit 由 MAX_TEXT_LENGTH 控制），避免用户以为
# 超长描述会生效而产生"白写了"的挫败感。
_VOICE_DESCRIPTION_SOFT_LIMIT_CHARS: int = 200

# Why：seed 默认为 -1 表示"随机"。生成完成后响应中会携带实际使用的 seed_used，
# 用户可以把该数值回填到下次请求中，100% 复现相同的音色 + 节奏 + 停顿细节，
# 满足"刚才那个声音很好听，再给我来一段一样的"的可复现需求。
_RANDOM_SEED_SENTINEL: int = -1


@router.post(
    "/voxcpm_design",
    summary="声音设计",
    description="使用 VoxCPM2 根据文本描述生成指定风格的语音",
)
async def generate_voxcpm_design(
    request: Request,
    text: str = Form(""),
    instruction: str = Form(""),
    persona_name: str = Form(""),
    lang: str = Form("Auto"),
    cfg: float = Form(2.0),
    steps: int = Form(10),
    denoise: str = Form("true"),
    tempo_factor: float = Form(1.0),
    voice_enhancement: str = Form("false"),
    target_lufs: float = Form(-16.0),
) -> HTMLResponse:
    """VoxCPM2 语音设计生成路由。

    根据文本描述（instruction）生成指定风格的语音；可选传入 persona_name
    加载已有音色的参考音频作为风格锚点。

    Args:
        request: FastAPI Request 对象，用于模板渲染与上下文传递。
        text: 待合成的朗读文本，必填，<= MAX_TEXT_LENGTH 字符。
        instruction: 音色风格文本描述（如"温柔女声，年轻，25岁"）。
        persona_name: 可选已有音色名称；传入后加载该音色的参考音频
            作为生成锚点，使结果风格贴近。
        lang: 语言 / 方言标识（Auto / zh / en / 四川话 / 粤语 等），
            会合并到 instruction 中。
        cfg: Classifier-Free Guidance 强度，推荐范围 [2.0, 10.0]，
            值越高风格越鲜明但可能失真。
        steps: 推理步数 (timesteps)，范围 [4, 50]，越多质量越高但越慢。
        denoise: 是否对参考音频降噪（"true"/"false" 字符串）。
        tempo_factor: 后处理变速倍率，1.0 为原速。
        voice_enhancement: 是否启用人声增强后处理。
        target_lufs: 响度归一化目标值 (LUFS)，默认 -16.0。

    Returns:
        HTMLResponse: HTMX 格式的 HTML 片段，包含：
            - data-audio-filename 属性（生成音频文件名）
            - <audio> 隐藏元素（src=/api/audio/{filename}）
            - 成功/降级/错误状态消息

    Raises:
        （以下异常本路由内优先转为 _error_html 返回，未捕获者抛给全局 error_handler）
        ValidationError: 400，文本为空 / 超长 / 描述为空等。
        InsufficientVRAMError: 503，CUDA OOM，由 _run_with_oom_retry 内部捕获并降级重试。
        ImportError: 底层 torch / diffusers 依赖缺失（透传 error_handler）。
    """
    # ------------------------------------------------------------------
    # 1. 参数校验（Pydantic Form 级别 + 业务级双重校验）
    # ------------------------------------------------------------------
    model_not_ready: HTMLResponse | None = _check_engine_ready(request, "voxcpm2")
    if model_not_ready is not None:
        return model_not_ready

    if not text.strip():
        return _error_html(request, "文本不能为空")

    if len(text) > MAX_TEXT_LENGTH:
        return _error_html(
            request,
            f"文本长度超过限制（最大 {MAX_TEXT_LENGTH} 字符）",
        )

    # Why：如模块级常量注释所述，这里对 instruction 做软限制提示——
    # 超过 200 字时只警告不阻断，但明确告知用户"超出部分可能不生效"，
    # 兼顾灵活性（用户可能确实想写长一点撞运气）与预期管理。
    if instruction and len(instruction) > _VOICE_DESCRIPTION_SOFT_LIMIT_CHARS:
        logger.warning(
            "[VoxCPM声音设计] voice_description 长度 %d 超过软上限 %d，超出部分可能因模型 token 窗口限制被截断而不生效",
            len(instruction),
            _VOICE_DESCRIPTION_SOFT_LIMIT_CHARS,
        )

    # ------------------------------------------------------------------
    # 2. 预处理：方言合并 + 表单 bool 解析
    # ------------------------------------------------------------------
    instruction = _merge_dialect(instruction, lang)
    advanced_denoise: bool = _parse_bool_form(denoise)

    # ------------------------------------------------------------------
    # 3. Persona 参考音频加载（可选锚点）
    # ------------------------------------------------------------------
    actual_ref_path: str | None = None
    if persona_name:
        from ....persona_manager import load_persona_embedding

        safe_name: str = os.path.basename(persona_name)
        persona_data = load_persona_embedding(safe_name)
        if persona_data is not None:
            wav_path, _ref_text = persona_data
            if wav_path and os.path.isfile(wav_path):
                actual_ref_path = wav_path
                logger.info(f"[VoxCPM声音设计] 已加载音色 '{safe_name}' 的参考音频")
            else:
                return _error_html(request, f"音色文件不存在: {safe_name}")
        else:
            logger.warning(f"[VoxCPM声音设计] 音色 '{safe_name}' 不存在，将使用默认音色")

    # ------------------------------------------------------------------
    # 4. 构造生成闭包（在 executor 线程中以 CPU/GPU 绑定方式运行）
    #    CUDA OOM 由 _execute_generation -> _run_with_oom_retry 捕获并自动
    #    降级重试 + 调用 free_gpu_memory 清理显存。
    # ------------------------------------------------------------------
    def _run():
        """正常参数下执行语音设计生成（线程池GPU推理）"""
        engine = registry.get_current_engine()
        return engine.generate_voice_design(
            text,
            instruction,
            cfg_value=cfg,
            inference_timesteps=steps,
            denoise=advanced_denoise,
            ref_audio_path=actual_ref_path,
        )

    def _degraded_run():
        """OOM降级参数下执行语音设计生成（steps减半、denoise关闭）"""
        engine = registry.get_current_engine()
        degraded_steps: int = max(steps // 2, 4)
        return engine.generate_voice_design(
            text,
            instruction,
            cfg_value=cfg,
            inference_timesteps=degraded_steps,
            denoise=False,
            ref_audio_path=actual_ref_path,
        )

    # ------------------------------------------------------------------
    # 5. 调用统一生成执行器：
    #    - 获取/释放每引擎串行信号量
    #    - 硬超时保护
    #    - OOM 降级重试
    #    - 后处理（tempo / voice_enhancement / LUFS）
    #    - 写入 history_db
    #    - 返回 HTMX HTML 片段
    # ------------------------------------------------------------------
    return await _execute_generation(
        request,
        text=text,
        run_fn=_run,
        endpoint_name="VoxCPM design",
        voice_or_persona=instruction[:50],
        model_type="声音设计",
        engine="voxcpm2",
        tempo_factor=tempo_factor,
        voice_enhancement=voice_enhancement,
        target_lufs=target_lufs,
        degraded_fn=_degraded_run,
    )
