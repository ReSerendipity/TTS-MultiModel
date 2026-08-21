"""生成子路由聚合模块。

**路由前缀**：
    所有生成相关 API 端点统一挂载在 ``/api/generate`` 前缀下（定义于 utils.py 的 router）。

**支持的引擎与生成模式**：

    VoxCPM2 引擎（前缀 ``/api/generate/voxcpm2``）：
        - POST /voxcpm_design          — 语音设计：纯文本描述塑造全新音色
        - POST /voxcpm_clone           — 可控克隆：零样本克隆，支持 cfg/denoise/norm
        - POST /voxcpm_ultimate        — 极致克隆：完整参数控制，支持 seed 复现
        - POST /voxcpm_prompt_continue — Prompt 延续：音频+文本风格续写
        - POST /voxcpm_script          — 剧本工坊：多角色对话批量生成
        - POST /streaming_sse          — SSE 流式生成：长文本逐段推送音频片段
        - POST /streaming              — 流式生成（一次性返回）：流式推理后返回合并 WAV
        - POST /streaming_audio        — 流式自动播放：分段生成 + 自动播放 HTML
        - POST /post-process           — 后处理：已生成音频的变速/增强/归一化
        - POST /cancel                 — 取消生成：终止正在进行的生成任务

    IndexTTS2 引擎（前缀 ``/api/generate/indextts2``）：
        - POST /indextts2              — 情感合成：零样本克隆 + 8 维情感向量 + 时长控制

**架构说明**：
    本模块作为 generate 子包的路由聚合入口，负责：
    1. 导入 generate/voxcpm2 和 generate/indextts2 两个引擎子模块，触发其路由注册
    2. 从 utils.py 暴露共享 router（供 app_server.py 的 pkgutil 扫描）

**路由注册机制**：
    utils.py 定义主 router (prefix="/api/generate")，各引擎子模块（design.py、
    clone.py、synthesize.py 等）直接在导入时将路由注册到该共享 router 上。
    通过导入 voxcpm2 和 indextts2 子包来确保所有路由端点被正确注册。

**异步任务队列与并发控制**：
    - 所有生成任务通过 per-engine asyncio.Semaphore 串行化（默认容量 1）
    - 信号量获取超时：120 秒（环境变量 ``TTS_SEMAPHORE_TIMEOUT_S``）
    - 单次生成硬超时：600 秒（环境变量 ``TTS_GENERATION_TIMEOUT_S``）
    - GPU 推理在 ``asyncio.run_in_executor`` 线程池中执行，不阻塞事件循环
    - OOM 自动降级重试：显存不足时自动降低参数重试（最多 2 次）

**SSE 事件流说明**：
    - 独立流式端点：``/api/generate/voxcpm2/streaming_sse`` 返回
      ``text/event-stream``，推送 meta/progress/audio/done/error 事件
    - 统一进度端点：``/api/sse/events`` 推送所有生成任务的进度事件
      （progress/complete/status/engine_switch/cancelled/time_estimate）
    - 非流式端点通过 HTMX HTML 片段直接返回结果，不使用 SSE

**返回格式**：
    - 非流式端点：HTMX HTML 片段（text/html），含 <audio> 标签 + 状态消息
    - SSE 流式端点：text/event-stream，逐段推送 base64 PCM 音频数据
    - 错误响应：统一 JSON 格式 ``{"status":"error","task_id":"...","error":{...}}``
"""

import logging

from . import generic, indextts2, voxcpm2  # noqa: F401 — 导入以触发路由注册
from .utils import router

logger = logging.getLogger("tts_multimodel")

__all__ = ["router", "indextts2", "voxcpm2", "generic"]
