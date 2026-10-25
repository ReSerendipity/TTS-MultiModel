# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Breeze TTS 2 Voice Direction 路由。

POST /api/generate/breeze_direction —— 克隆音色 + 自然语言指令控制
语气/情感/语速/表达（映射 ControllableTTSEngine.generate_ultimate_clone 槽位）。
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
    "/breeze_direction",
    summary="Breeze 声音导演",
    description="Breeze TTS 2：克隆参考音色并按指令控制语气/情感/语速（中英双语）",
)
async def generate_breeze_direction(
    request: Request,
    text: str = Form(""),
    expected_engine: str = Form(""),
    engine: str = Form("breeze-tts2"),
    instruction: str = Form(""),
    ref_text: str = Form(""),
    persona_name: str = Form(""),
    has_consent: bool = Form(False),
    ref_audio: UploadFile | None = File(None),
    cfg_scale: float = Form(4.0, ge=0.1, le=10),
    seed: int = Form(-1),
    random_seed: str = Form("true"),
    tempo_factor: float = Form(1.0),
    voice_enhancement: str = Form("false"),
    target_lufs: float = Form(-16.0),
) -> HTMLResponse:
    """Voice Direction 生成端点（HTMX 片段返回）。"""
    # 服务端引擎门禁（与模板 JS 守卫同口径：页面引擎与激活引擎分家时阻断）
    if expected_engine and registry.current_engine and registry.current_engine != expected_engine:
        return _error_html(
            request,
            f"当前激活引擎为 {registry.current_engine}；本页属 Breeze TTS 2，请先到模型页切换引擎后再生成",
        )

    # 0. 授权门禁（同 breeze_clone / generic-clone 口径）
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
                detail=f"persona={persona_name} consent_state={state} endpoint=breeze/direction",
                severity="warning" if state == "unverified" else "info",
                outcome="success",
                request_id=getattr(request.state, "request_id", None),
            )

    if not (instruction and instruction.strip()):
        return _error_html(request, "Voice Direction 需要指令（如“说得慢一些，语气克制而严肃”）")

    invalid = pre_validate(request, engine or None, text)
    if invalid is not None:
        return invalid

    limit: int = get_engine_text_limit("breeze-tts2")
    over_limit = breeze_text_limit_error(request, text, limit)
    if over_limit is not None:
        return over_limit

    ref_path, resolved_ref_text, ref_err = await resolve_breeze_ref(request, ref_audio, persona_name, ref_text)
    if ref_err is not None:
        return ref_err

    effective_seed = parse_seed(seed, random_seed)

    def _run():
        current = registry.get_current_engine()
        if current is None:
            raise RuntimeError("当前无已加载引擎")
        return current.generate_ultimate_clone(
            text,
            instruction=instruction,
            ref_audio_path=ref_path,
            advanced_cfg=cfg_scale,
            advanced_seed=effective_seed,
            ref_text=resolved_ref_text,
        )

    def _degraded_run():
        engine = registry.get_current_engine()
        return engine.generate_ultimate_clone(
            text,
            instruction=instruction,
            ref_audio_path=ref_path,
            advanced_cfg=max(cfg_scale / 2, 1.0),
            advanced_seed=effective_seed,
            ref_text=resolved_ref_text,
        )

    return await _execute_generation(
        request,
        text=text,
        run_fn=_run,
        endpoint_name="Breeze direction",
        voice_or_persona=persona_name or "upload",
        model_type="声音导演",
        engine="breeze-tts2",
        tempo_factor=tempo_factor,
        voice_enhancement=voice_enhancement,
        target_lufs=target_lufs,
        degraded_fn=_degraded_run,
    )
