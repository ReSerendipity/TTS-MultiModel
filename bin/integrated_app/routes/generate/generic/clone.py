"""通用引擎语音克隆路由。

**端点**：POST ``/api/generate/generic/clone``

**适用引擎**：任何实现 ``TTSEngine.generate_voice_clone`` 的当前激活引擎，
    

**表单参数（multipart/form-data）**：
    - text (str, 必填)：待合成文本。
    - engine (str, 可选)：目标引擎名，仅用于前置就绪校验；默认使用当前引擎。
    - prompt_text (str, 可选)：参考音频对应转写文本（传给 instruction）。
    - persona_name (str, 可选)：已注册 Persona 名称，作为参考音频来源。
    - ref_audio (File, 可选)：直接上传的参考音频文件。
    - tempo_factor / voice_enhancement / target_lufs：通用后处理参数。

**引擎专属高级参数（折叠区，用户可视场景选择是否调整）**：
    
    由于 ``TTSEngine.generate_voice_clone`` 协议定义为 ``**kwargs``，
    路由将这些参数透传给当前激活引擎，未匹配字段会被引擎忽略，
    因此一条端点可同时服务多个引擎。

**参考音频优先级**：ref_audio 上传 > persona_name。二者均缺失时报错
    （通用克隆需要参考音频）。

**返回**：HTMX HTML 片段（含 <audio> + 状态消息）。
"""

import logging

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from ....config import MAX_TEXT_LENGTH
from ....model_registry import registry
from ..utils import (
    _error_html,
    _execute_generation,
    pre_validate,
    resolve_persona_ref,
    router,
    save_uploaded_audio,
)

logger = logging.getLogger("tts_multimodel")


@router.post(
    "/generic/clone",
    summary="通用语音克隆",
    description="调用当前激活引擎的 generate_voice_clone(通用引擎)",
)
async def generic_clone_endpoint(
    request: Request,
    text: str = Form(...),
    engine: str = Form(""),
    prompt_text: str = Form(""),
    persona_name: str = Form(""),
    ref_audio: UploadFile | None = File(None),
    tempo_factor: float = Form(1.0),
    voice_enhancement: str = Form("false"),
    target_lufs: float = Form(-16.0),
        num_steps: int = Form(10),
    guidance_scale: float = Form(1.2),
    seed: int = Form(42),
    random_seed: str = Form("true"),
    language: str = Form("auto"),
) -> HTMLResponse:
    """通用引擎零样本语音克隆端点。

    Args:
        request: FastAPI 请求对象。
        text: 待合成文本。
        engine: 目标引擎名（仅前置校验用，默认当前引擎）。
        prompt_text: 参考音频转写文本。
        persona_name: Persona 名称（参考音频来源之一）。
        ref_audio: 上传的参考音频文件（优先于 persona_name）。
        tempo_factor: 语速因子。
        voice_enhancement: 是否人声增强（"true"/"false"）。
        target_lufs: 目标响度 LUFS。
        num_steps/guidance_scale/seed/random_seed/language:
            某些引擎可能支持额外的超参数配置。

    Returns:
        HTMLResponse: 成功/失败的 HTMX 片段。
    """
    # 1. 前置校验：引擎就绪 + 文本非空 + 长度限制
    invalid = pre_validate(request, engine or None, text, MAX_TEXT_LENGTH)
    if invalid:
        return invalid

    # 2. 解析参考音频（上传优先，其次 Persona）
    ref_path: str | None = None
    if ref_audio is not None and ref_audio.filename:
        ref_path, err = await save_uploaded_audio(request, ref_audio)
        if err:
            return err
    if ref_path is None and persona_name:
        ref_path, err = await resolve_persona_ref(request, persona_name)
        if err:
            return err

    if not ref_path:
        return _error_html(request, "通用克隆需要参考音频（上传文件或选择音色）")

        effective_seed = -1 if (random_seed or "").lower() == "true" else seed

    # 3. 构造生成闭包（在 executor 线程中执行 GPU 推理）
    def _run():
        """调用当前引擎的 generate_voice_clone。"""
        current = registry.get_current_engine()
        if current is None:
            raise RuntimeError("当前无已加载引擎")
        return current.generate_voice_clone(
            text,
            reference_audio_path=ref_path,
            instruction=prompt_text,
            # 自定义参数透传给引擎            num_steps=num_steps,
            guidance_scale=guidance_scale,
            seed=effective_seed,
            language=language,
        )

    # 4. 统一生成执行器：串行信号量 + 硬超时 + 后处理 + 历史入库 + SSE
    return await _execute_generation(
        request,
        text=text,
        run_fn=_run,
        endpoint_name="Generic clone",
        voice_or_persona=persona_name or "upload",
        model_type="通用克隆",
        engine=registry.current_engine or engine or "generic",
        tempo_factor=tempo_factor,
        voice_enhancement=voice_enhancement,
        target_lufs=target_lufs,
        oom_retry=False,
    )
