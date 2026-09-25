"""通用引擎语音克隆路由。

**端点**：POST ``/api/generate/generic/clone``

**适用引擎**：任何实现 ``TTSEngine.generate_voice_clone`` 的当前激活引擎，


**表单参数（multipart/form-data）**：
    - text (str, 必填)：待合成文本。
    - engine (str, 可选)：目标引擎名，仅用于前置就绪校验；默认使用当前引擎。
    - prompt_text (str, 可选)：参考音频对应转写文本（传给 instruction）。
    - persona_name (str, 可选)：已注册 Persona 名称，作为参考音频来源。
    - has_consent (bool, 可选)：上传参考音频时必须勾选的授权声明
      （P0-1 补口，与 voxcpm2/clone.py 同款门禁）。
    - ref_audio (File, 可选)：直接上传的参考音频文件。
    - tempo_factor / voice_enhancement / target_lufs：通用后处理参数。

**引擎专属高级参数（折叠区，用户可视场景选择是否调整）**：

    ``TTSEngine.generate_voice_clone`` 的协议签名是 ``**kwargs``，但**协议收下不等于
    底层消化得了**：VoxCPM2 的实现把 kwargs 原样转发给封闭签名的
    ``fn_voxcpm_clone(text, instruction, ref_audio_path, cfg_value,
    inference_timesteps, denoise, normalize)``，IndexTTS 的实现转给 ``infer()``
    且上游会拒绝未知 model_kwargs。所以本路由按当前引擎做一次词汇翻译
    （见 :func:`_clone_kwargs_for_engine`），不再原名透传。

**参考音频优先级**：ref_audio 上传 > persona_name。二者均缺失时报错
    （通用克隆需要参考音频）。

**返回**：HTMX HTML 片段（含 <audio> + 状态消息）。
"""

import logging

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from ....model_registry import registry
from ....persona_manager import get_persona_consent_state
from ..utils import (
    _error_html,
    _execute_generation,
    pre_validate,
    resolve_persona_ref,
    router,
    save_uploaded_audio,
)

logger = logging.getLogger("tts_multimodel")


def _clone_kwargs_for_engine(
    engine_name: str,
    *,
    seed: int,
    num_steps: int,
    guidance_scale: float,
    language: str,
) -> dict[str, object]:
    """把通用路由的表单词汇翻译成当前引擎真正接受的参数名。

    参数名的出处：
        - ``fn_voxcpm_clone``（engines/voxcpm2/clone.py）只收
          ``cfg_value`` / ``inference_timesteps`` / ``denoise`` / ``normalize``，
          **没有** ``seed`` 与 ``language``；喂错名字直接 TypeError。
        - ``IndexTTS2.infer`` 收 ``lang``；``seed`` 仅在 > 0 时下传
          （与 indextts2/synthesize.py 的同款判断保持一致，上游会拒绝未知 model_kwargs）。

    Args:
        engine_name: 当前激活引擎名（``registry.current_engine``）。
        seed: 已解析好的随机种子（-1 表示随机）。
        num_steps: 扩散/采样步数。
        guidance_scale: CFG / guidance 强度。
        language: 语言偏好。

    Returns:
        可直接 ``**`` 展开进 ``generate_voice_clone`` 的字典。
    """
    if engine_name == "voxcpm2":
        if seed != -1:
            logger.warning("[通用克隆] voxcpm2 可控克隆不接受 seed，本次已忽略（复现请用极致克隆的 advanced_seed）")
        return {"cfg_value": guidance_scale, "inference_timesteps": num_steps}

    if engine_name.startswith("indextts"):
        extras: dict[str, object] = {}
        if language:
            extras["lang"] = language
        if seed > 0:
            extras["seed"] = seed
        return extras

    logger.warning(f"[通用克隆] 引擎 {engine_name!r} 的参数词汇未知，不下传任何引擎专属参数")
    return {}


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
    has_consent: bool = Form(False),
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
    # 0. P0-1 声音克隆授权 v1 补口：与 voxcpm2/clone.py 同款门禁（fail-safe，
    #    先于 pre_validate 执行，避免被 EngineNotReady 短路而形同虚设）。
    #    上传来源必须显式勾选；persona 来源默认放行但审计（unverified 可经
    #    security.clone_unverified_allow=False 收紧为拒绝）。
    wants_clone = (ref_audio is not None and bool(ref_audio.filename)) or bool(persona_name)
    if wants_clone:
        from ....security.audit import log_audit

        if not persona_name:
            if not has_consent:
                return _error_html(
                    request,
                    "请先勾选「我已确认拥有该参考声音的使用权或已获得其授权」再生成",
                )
        else:
            state = get_persona_consent_state(persona_name)
            if state == "unverified":
                from ....config import get_config

                if not get_config().pydantic_config.security.clone_unverified_allow:
                    return _error_html(
                        request,
                        f"音色 [{persona_name}] 缺少声音使用授权声明（unverified），且当前配置禁止放行未声明音色",
                    )
            log_audit(
                "voice_clone",
                detail=f"persona={persona_name} consent_state={state} endpoint=generic/clone",
                severity="warning" if state == "unverified" else "info",
                outcome="success",
                request_id=getattr(request.state, "request_id", None),
            )

    # 1. 前置校验：引擎就绪 + 文本非空 + 长度限制
    invalid = pre_validate(request, engine or None, text)
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

    effective_seed = -1 if (random_seed or "").lower() == "true" else seed

    if not ref_path:
        return _error_html(request, "通用克隆需要参考音频（上传文件或选择音色）")

    # 3. 构造生成闭包（在 executor 线程中执行 GPU 推理）
    def _run():
        """调用当前引擎的 generate_voice_clone（参数名按引擎翻译，见 _clone_kwargs_for_engine）。"""
        current = registry.get_current_engine()
        if current is None:
            raise RuntimeError("当前无已加载引擎")
        return current.generate_voice_clone(
            text,
            reference_audio_path=ref_path,
            instruction=prompt_text,
            **_clone_kwargs_for_engine(
                registry.current_engine or "",
                seed=effective_seed,
                num_steps=num_steps,
                guidance_scale=guidance_scale,
                language=language,
            ),
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
