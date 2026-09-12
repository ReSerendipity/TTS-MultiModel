"""Voicebox 语音转换路由子包。

暴露 convert 路由模块，触发 /api/generate/voicebox/convert 端点注册。
"""

from . import convert  # noqa: F401 — 导入以触发路由注册

__all__ = ["convert"]
