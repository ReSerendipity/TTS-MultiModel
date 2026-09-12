"""Step-Audio-EditX 音频编辑路由子包。

暴露 edit 路由模块，触发 /api/generate/step-audio-editx/edit 端点注册。
"""

from . import edit  # noqa: F401 — 导入以触发路由注册

__all__ = ["edit"]
