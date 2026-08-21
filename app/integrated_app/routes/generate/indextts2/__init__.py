"""IndexTTS2 情感控制引擎生成路由聚合模块。

**路由前缀**：
    所有 IndexTTS2 生成端点统一挂载在 ``/api/generate/indextts2`` 前缀下。

**支持的生成模式**：
    - 情感合成 (synthesize)：POST /indextts2 — 零样本克隆 + 8 维情感向量控制 + 时长控制

**IndexTTS2 核心能力**：
    - 零样本语音克隆：参考音频 + 文本 → 同音色新语音合成
    - 8 维情感向量精细控制：happy/angry/sad/afraid/disgusted/melancholic/surprised/calm
      每维取值范围 [0, 1]，总和超过 1 时自动归一化
    - 三种情感注入模式（互斥，优先级从高到低）：
      1. 情感文本描述 (emo_text)：自然语言描述情感，适合非专业用户
      2. 情感参考音频 (emo_audio)：上传带目标情感的音频，迁移情感风格
      3. 8 维情感向量 (emo_*)：滑杆精确控制，适合专业场景复现
    - 时长控制：speed 范围 [0.5x, 2.0x]，支持 target_duration 精确目标时长
    - emo_alpha 情感强度混合比：[0, 1]，1 为完全情感化，0 为完全中性

**异步任务队列与并发控制**：
    - 使用 per-engine asyncio.Semaphore（默认容量 1，单 Worker 串行执行）
    - 信号量获取超时：120 秒（环境变量 ``TTS_SEMAPHORE_TIMEOUT_S`` 可配置）
    - 单次生成硬超时：600 秒（环境变量 ``TTS_GENERATION_TIMEOUT_S`` 可配置）
    - 所有生成任务在 ``asyncio.run_in_executor`` 线程池中执行，避免阻塞事件循环
    - OOM 降级重试机制：显存不足时自动释放缓存并重试（最多 2 次）

**SSE 事件说明**：
    IndexTTS2 当前未提供独立 SSE 流式端点，所有生成任务的进度通过统一
    SSE 端点 ``/api/sse/events`` 推送：
    - ``progress``：生成进度百分比
    - ``complete``：生成完成，携带音频 URL
    - ``status``：状态更新（如模型加载中、引擎切换）
    - ``cancelled``：任务被取消
    - ``time_estimate``：预计剩余时间

**返回格式**：
    所有端点返回 HTMX 格式 HTML 片段（``text/html``），包含：
    - ``<audio>`` 隐藏元素，src 指向 ``/api/audio/{filename}``
    - ``data-audio-filename`` 属性标记生成的文件名
    - 成功/警告/错误状态消息 div
    - 降级成功时额外显示橙色警告提示（degraded_note）

**模块导入说明**：
    本模块通过导入子模块 synthesize 触发其路由注册到父包 utils.router
    （prefix="/api/generate"）上。app_server.py 的 pkgutil 扫描机制自动发现
    并挂载本路由。
"""

from ..utils import router
from . import synthesize

__all__ = ["router", "synthesize"]
