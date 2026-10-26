# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Breeze TTS 2 Voice Design 路由。

POST /api/generate/breeze_design —— 自然语言描述音色（无参考音频）。
instruction 为空时退化为默认音色朗读。官方推荐 cfg_scale=4 强化指令遵循。
"""

from __future__ import annotations

from fastapi import Form, Request
from fastapi.responses import HTMLResponse

from ....config import get_engine_text_limit
from ....model_registry import registry
from ..utils import _check_engine_ready, _error_html, _execute_generation, router
from ._ref import breeze_text_limit_error, parse_seed


@router.post(
    "/breeze_design",
    summary="Breeze 声音设计",
    description="Breeze TTS 2：根据自然语言描述塑造全新音色（中英双语）",
)
async def generate_breeze_design(
    request: Request,
    text: str = Form(""),
    expected_engine: str = Form(""),
    instruction: str = Form(""),
    cfg_scale: float = Form(4.0, ge=0.1, le=10),
    seed: int = Form(-1),
    random_seed: str = Form("true"),
    tempo_factor: float = Form(1.0),
    voice_enhancement: str = Form("false"),
    target_lufs: float = Form(-16.0),
) -> HTMLResponse:
    """Voice Design 生成端点（HTMX 片段返回，契约同 voxcpm_design）。"""
    model_not_ready = _check_engine_ready(request, "breeze-tts2")
    if model_not_ready is not None:
        return model_not_ready

    # 服务端引擎门禁（与模板 JS 守卫同口径：页面引擎与激活引擎分家时阻断）
    if expected_engine and registry.current_engine and registry.current_engine != expected_engine:
        return _error_html(
            request,
            f"当前激活引擎为 {registry.current_engine}；本页属 Breeze TTS 2，请先到模型页切换引擎后再生成",
        )

    if not text.strip():
        return _error_html(request, "文本不能为空")

    limit: int = get_engine_text_limit("breeze-tts2")
    over_limit = breeze_text_limit_error(request, text, limit)
    if over_limit is not None:
        return over_limit

    effective_seed = parse_seed(seed, random_seed)

    def _run():
        engine = registry.get_current_engine()
        return engine.generate_voice_design(
            text,
            instruction,
            cfg_scale=cfg_scale,
            seed=effective_seed,
        )

    def _degraded_run():
        # OOM 降级：降低 CFG（弱化双分支引导，batch 减半）并保持文本不变
        engine = registry.get_current_engine()
        return engine.generate_voice_design(
            text,
            instruction,
            cfg_scale=max(cfg_scale / 2, 1.0),
            seed=effective_seed,
        )

    return await _execute_generation(
        request,
        text=text,
        run_fn=_run,
        endpoint_name="Breeze design",
        voice_or_persona=instruction[:50],
        model_type="声音设计",
        engine="breeze-tts2",
        tempo_factor=tempo_factor,
        voice_enhancement=voice_enhancement,
        target_lufs=target_lufs,
        degraded_fn=_degraded_run,
    )
