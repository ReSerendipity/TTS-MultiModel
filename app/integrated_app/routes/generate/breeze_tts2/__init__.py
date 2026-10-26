# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Breeze TTS 2 生成路由子包。

前缀 ``/api/generate``（共享 router），端点：
    - POST /breeze_design     — Voice Design（自然语言描述音色，无参考音频）
    - POST /breeze_clone      — Voice Clone（参考音频 + 准确转写）
    - POST /breeze_direction  — Voice Direction（克隆 + 指令控制语气/情感/语速）

正文内可直接嵌入 Vocal Events 标记（英文 ``(...)`` / 中文 ``[...]``，如
``(laugh)`` / ``[笑]``），随文本透传给模型。
"""

from . import clone, design, direction  # noqa: F401 — 导入以触发路由注册

__all__ = ["clone", "design", "direction"]
