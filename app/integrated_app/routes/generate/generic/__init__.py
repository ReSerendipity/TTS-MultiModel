"""通用引擎生成路由聚合模块。

**路由前缀**：``/api/generate/generic``

**支持端点**：
    - POST /generic/clone — 通用语音克隆：适配任何实现 ``generate_voice_clone``
      的当前引擎（如 generic_tts_engine）。

**设计说明**：
    VoxCPM2 / IndexTTS2 拥有各自专属的生成路由（参数丰富、引擎特定）。
    对于通过 config.yaml + engine_registry 声明式接入的通用新式引擎，
    本模块提供一个**引擎无关**的克隆端点：直接调用
    ``registry.get_current_engine().generate_voice_clone(...)``，
    复用统一生成执行器（信号量串行、硬超时、OOM 降级、历史入库、SSE 进度）。
"""

import logging

from . import clone  # noqa: F401 — 导入以触发路由注册

logger = logging.getLogger("tts_multimodel")

__all__ = ["clone"]
