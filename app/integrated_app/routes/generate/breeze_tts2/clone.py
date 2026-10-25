# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Breeze TTS 2 Voice Clone 路由。

POST /api/generate/breeze_clone —— 参考音频 + **准确转写** → 音色克隆。
（Breeze 模板要求转写与音频严格对齐；授权门禁同 generic/clone。）
"""

from __future__ import annotations

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from ....config import get_engine_text_limit
from ....model_registry import registry
from ....persona_manager import get_persona_consent_state
from ..utils import _error_html, _execute_generation, pre_validate, router
from ._ref import breeze_text_limit_error, parse_seed, resolve_breeze_ref


@router.post(
    "/breeze_clone",
    summary="Breeze 声音克隆",
    description="Breeze TTS 2：参考音频 + 准确转写克隆音色（中英双语）",
)
async def generate_breeze_clone(
    request: Request,
    text: str = Form(""),
    expected_engine: str = Form(""),
    engine: str = Form("breeze-tts2"),
    ref_text: str = Form(""),
    persona_name: str = Form(""),
    has_consent: bool = Form(False),
    ref_audio: UploadFile | None = File(None),
    seed: int = Form(-1),
    random_seed: str = Form("true"),
    tempo_factor: float = Form(1.0),
    voice_enhancement: str = Form("false"),
    target_lufs: float = Form(-16.0),
) -> HTMLResponse:
    """Voice Clone 生成端点（HTMX 片段返回）。"""
    # 服务端引擎门禁（与模板 JS 守卫同口径：页面引擎与激活引擎分家时阻断）
    if expected_engine and registry.current_engine and registry.current_engine != expected_engine:
        return _error_html(
            request,
            f"当前激活引擎为 {registry.current_engine}；本页属 Breeze TTS 2，请先到模型页切换引擎后再生成",
        )

    # 0. 声音克隆授权门禁（fail-safe，先于引擎就绪校验——同 generic/clone）
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
                detail=f"persona={persona_name} consent_state={state} endpoint=breeze/clone",
                severity="warning" if state == "unverified" else "info",
                outcome="success",
                request_id=getattr(request.state, "request_id", None),
            )

    # 1. 引擎就绪 + 文本校验
    invalid = pre_validate(request, engine or None, text)
    if invalid is not None:
        return invalid

    limit: int = get_engine_text_limit("breeze-tts2")
    over_limit = breeze_text_limit_error(request, text, limit)
    if over_limit is not None:
        return over_limit

    # 2. 参考音频 + 转写解析（上传优先，其次 Persona）
    ref_path, resolved_ref_text, ref_err = await resolve_breeze_ref(request, ref_audio, persona_name, ref_text)
    if ref_err is not None:
        return ref_err

    effective_seed = parse_seed(seed, random_seed)

    # 3. 生成闭包（executor 线程；Breeze 纯克隆模板无负分支，不暴露 CFG）
    def _run():
        current = registry.get_current_engine()
        if current is None:
            raise RuntimeError("当前无已加载引擎")
        return current.generate_voice_clone(
            text,
            reference_audio_path=ref_path,
            ref_text=resolved_ref_text,
            seed=effective_seed,
        )

    return await _execute_generation(
        request,
        text=text,
        run_fn=_run,
        endpoint_name="Breeze clone",
        voice_or_persona=persona_name or "upload",
        model_type="声音克隆",
        engine="breeze-tts2",
        tempo_factor=tempo_factor,
        voice_enhancement=voice_enhancement,
        target_lufs=target_lufs,
        oom_retry=False,
    )
