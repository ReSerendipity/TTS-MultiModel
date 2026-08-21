"""VoxCPM2 引擎生成路由聚合模块。

**路由前缀**：
    所有 VoxCPM2 生成端点统一挂载在 ``/api/generate/voxcpm2`` 前缀下。

**支持的生成模式**：
    - 语音设计 (design)：POST /voxcpm_design — 纯文本描述塑造全新音色
    - 可控克隆 (clone)：POST /voxcpm_clone — 零样本克隆，支持 cfg/denoise/norm
    - 极致克隆 (ultimate)：POST /voxcpm_ultimate — 完整参数控制，支持 seed 复现
    - Prompt 延续 (prompt_continue)：POST /voxcpm_prompt_continue — 音频+文本风格续写
    - 剧本工坊 (script)：POST /voxcpm_script — 多角色对话批量生成
    - SSE 流式生成 (streaming_sse)：POST /streaming_sse — 长文本逐段推送
    - 流式一次性返回 (streaming)：POST /streaming — 流式推理后一次性返回
    - 流式自动播放 (streaming_audio)：POST /streaming_audio — 分段生成+自动播放
    - 后处理 (post-process)：POST /post-process — 已生成音频的变速/增强/归一化
    - 取消生成 (cancel)：POST /cancel — 取消正在进行的生成任务

**异步任务队列与并发控制**：
    - 使用 per-engine asyncio.Semaphore（默认容量 1，单 Worker 串行执行）
    - 信号量获取超时：120 秒（环境变量 ``TTS_SEMAPHORE_TIMEOUT_S`` 可配置）
    - 单次生成硬超时：600 秒（环境变量 ``TTS_GENERATION_TIMEOUT_S`` 可配置）
    - 所有生成任务在 ``asyncio.run_in_executor`` 线程池中执行，避免阻塞事件循环
    - OOM 降级重试机制：显存不足时自动减半 timesteps、关闭 denoise 重试（最多 2 次）

**SSE 事件说明（流式端点）**：
    ``/streaming_sse`` 端点推送的事件序列：
    1. ``event: meta`` — 首包，携带 total_segments/sample_rate/channels/bits
    2. ``event: progress`` — 每段开始生成时推送（segment/total/status）
    3. ``event: audio`` — 每段生成完成后推送 base64 编码的 PCM 数据
    4. ``event: done`` — 全部完成，携带 filename/duration
    5. ``event: error`` — 段级异常时推送错误消息（不终止连接）

    统一进度事件流端点：所有生成任务（含非流式）的进度通过
    ``/api/sse/events`` 端点推送 progress/complete/status/cancelled/time_estimate 事件。

**模块导入说明**：
    本模块通过导入子模块（clone/design/script/streaming）触发其路由注册到
    父包 utils.router（prefix="/api/generate"）上。app_server.py 的 pkgutil
    扫描机制自动发现并挂载本路由。
"""

from ..utils import router
from . import clone, design, script, streaming

__all__ = ["router", "clone", "design", "script", "streaming"]
