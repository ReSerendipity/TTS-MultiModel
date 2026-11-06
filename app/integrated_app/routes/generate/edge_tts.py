# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Edge-TTS 生成薄路由（阶段 4 D-2：降级链落点）。

背景（`docs/runbooks/2026-11-01-o3-failover-drill.md` §2b.4 断点①）：
    edge-tts 注册于引擎表但无任何 REST 生成路由——降级切换成功后合成请求
    仍无落点。本模块补上直连生成端点；模块级 ``router`` 由
    ``app_server._discover_routes`` 自动发现，无需中心路由表登记。

设计要点：
    - 无权重引擎（``requires_gpu=False`` 且 ``vram_gb=0``）：不走
      ``_check_engine_ready`` 的实例闸（该口径已放宽，见 ``utils.py``），
      真实可用性由合成过程验证；
    - 失败语义：依赖缺失 / 网络失败 / 零音频块 → ``error_type=fallback_failed``
      （与主引擎故障 ``engine_not_ready`` 可区分，不假成功）；
    - 产物落 ``outputs/``（SAVE_DIR），与其他生成路由一致，受生命周期管理。
"""

from __future__ import annotations

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/generate/edge-tts", tags=["edge-tts"])


@router.post(
    "/tts",
    summary="Edge-TTS 文本合成（CPU 兜底引擎直连）",
    description=(
        "用 Edge-TTS（联网在线服务）将文本合成为 24kHz WAV。"
        "无权重引擎：无需加载模型；依赖缺失或网络失败时返回 "
        "error_type=fallback_failed（503），不假成功。"
    ),
    responses={
        503: {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string", "enum": ["error"]},
                            "error_type": {"type": "string", "enum": ["fallback_failed"]},
                            "detail": {"type": "string"},
                        },
                    }
                }
            },
            "description": "edge-tts 不可用（依赖缺失/网络失败/零音频块）",
        },
    },
)
async def edge_tts_tts(text: str = Form(""), lang: str = Form("zh")) -> JSONResponse:
    """Edge-TTS 直连文本合成。

    Args:
        text: 待合成文本（非空）。
        lang: 语言提示（目前仅透传给音色选择，默认中文晓晓）。

    Returns:
        JSONResponse: 成功 200（status/filename/size_bytes/engine）；
        失败 503（status/error_type=fallback_failed/detail）。
    """
    if not text.strip():
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error_type": "validation", "detail": "text 不能为空"},
        )
    # 延迟导入（函数体查找模块属性）：测试 monkeypatch seam 时无需改导入绑定
    from . import utils as _gen_utils

    try:
        filename, size_bytes = await _gen_utils._synthesize_edge_tts_wav(text, lang)
    except _gen_utils.EdgeTTSFallbackUnavailableError as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "error_type": "fallback_failed", "detail": str(exc)},
        )
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "filename": filename,
            "size_bytes": size_bytes,
            "engine": "edge-tts",
        },
    )
