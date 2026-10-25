# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Breeze TTS 2 路由共享工具：参考音频（上传 / Persona 双来源）+ 转写解析。

Breeze 的克隆/导演模板**必须**携带参考音频的准确转写（ref_text），与
VoxCPM 系「可选 instruction」的语义不同，因此不能复用 generic/clone 的
instruction 直通做法，在这里统一解析。
"""

from __future__ import annotations

import os

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse

from ....path_guard import is_bare_filename
from ....persona_manager import PERSONA_DIR, load_persona_embedding
from ..utils import _error_html, save_uploaded_audio


async def resolve_breeze_ref(
    request: Request,
    ref_audio: UploadFile | None,
    persona_name: str,
    ref_text_form: str,
) -> tuple[str | None, str | None, HTMLResponse | None]:
    """解析 Breeze 参考音频与转写。

    优先级：上传文件 > Persona。转写优先取表单，Persona 新格式缓存自带
    (wav, ref_text) 二元组时允许缺省表单转写。

    Returns:
        (wav_path, ref_text, None) 成功；任一为 None 时第三个元素为错误片段。
    """
    wav_path: str | None = None
    ref_text: str = (ref_text_form or "").strip()

    if ref_audio is not None and ref_audio.filename:
        wav_path, err = await save_uploaded_audio(request, ref_audio)
        if err is not None:
            return None, None, err
    elif persona_name:
        # 音色名必须是单段裸名（与 voxcpm2/design.py 同款防目录穿越口径）
        if not is_bare_filename(persona_name):
            return None, None, _error_html(request, f"音色名格式不合法: {persona_name}")
        persona_data = load_persona_embedding(persona_name)
        if persona_data is None:
            return None, None, _error_html(request, f"音色不存在: {persona_name}")
        if isinstance(persona_data, tuple) and len(persona_data) == 2:
            wav_path = str(persona_data[0])
            ref_text = ref_text or str(persona_data[1] or "").strip()
        elif isinstance(persona_data, (str, os.PathLike)):
            wav_path = str(persona_data)
        else:
            wav_path = os.path.join(PERSONA_DIR, f"{persona_name}.wav")
        if not os.path.isfile(wav_path):
            return None, None, _error_html(request, f"音色音频文件缺失: {persona_name}")

    if wav_path is None:
        return None, None, _error_html(request, "请上传参考音频或选择已有音色")

    if not ref_text:
        return (
            None,
            None,
            _error_html(
                request,
                "Breeze TTS 2 需要「参考音频的准确转写」（ref_text）——转写与音频内容"
                "严格对齐才能保证克隆质量；Persona 未携带转写时请在表单中补填。",
            ),
        )
    return wav_path, ref_text, None


def breeze_text_limit_error(request: Request, text: str, limit: int) -> HTMLResponse | None:
    """文本超限错误片段（未超限返回 None）。"""
    if len(text) > limit:
        return _error_html(
            request,
            f"文本长度超过限制（Breeze TTS 2 单次最大 {limit} 字符，当前 {len(text)} 字符，"
            "受模型 2048 token 序列上限约束）；请分段生成。",
        )
    return None


def parse_seed(seed: int, random_seed: str) -> int:
    """表单 seed/random_seed 解析为引擎 seed（random_seed=true → -1 随机）。"""
    return -1 if (random_seed or "").lower() == "true" else int(seed)


__all__ = [
    "breeze_text_limit_error",
    "parse_seed",
    "resolve_breeze_ref",
]
