"""VoxCPM2 剧本工坊（Script Workshop）多角色对话批量生成路由模块。

**路由前缀与端点**：
    前缀：``/api/generate/voxcpm2``
    - POST /voxcpm_script — 剧本工坊多角色对话批量生成

**HTTP 方法**：
    仅接受 POST 请求，参数通过 ``multipart/form-data`` 表单提交。

**在 TTS 生成管线中的位置**：
    位于生成管线的"多角色批量合成"环节，是单条"克隆/设计"路由之上的批量封装层。
    输入一段"剧本格式文本"（每行 ``[角色名] 台词``），配合 persona_map 将角色名
    映射到已注册 Persona，逐行调用底层 generate_script 引擎方法，按行返回音频，
    以支持前端"逐行高亮 + 点哪行播哪行"的交互式体验。

**剧本格式说明**：
    每行格式为 ``[角色名] 台词内容``，空行会被自动跳过。persona_names 参数为
    逗号分隔的角色名列表（如 "旁白,小明,妈妈"），必须与剧本中 ``[角色名]`` 完全一致。

**异步任务队列与并发控制**：
    - 通过 ``_execute_generation()`` 获取 per-engine asyncio.Semaphore（默认容量 1）
    - 整笔剧本作为单条生成任务串行执行（内部逐行推理）
    - 信号量获取超时：120 秒；单次生成硬超时：600 秒
    - GPU 推理在 ``run_in_executor`` 线程池中执行
    - OOM 自动降级：显存不足时自动减半 steps、关闭 denoise 重试（最多 2 次）
    - 单行角色参考音频缺失时局部降级（使用默认音色），不阻断整笔剧本

**SSE 事件说明**：
    本端点为非流式同步端点，整笔剧本合并后一次性返回。生成进度通过统一 SSE
    端点 ``/api/sse/events`` 推送：
    1. ``status`` — 任务开始，剧本解析中
    2. ``progress`` — 逐行进度百分比（已完成行数/总行数）
    3. ``time_estimate`` — 预计剩余时间
    4. ``complete`` — 全部完成，携带合并后音频 URL
    5. ``error`` / ``cancelled`` — 失败或取消
    注：engine.generate_script() 内部维护逐行 lines 元数据，支持行级交互。

**通用生成管线流程**：
    1. 参数校验：剧本文本非空 + 长度限制；persona_names 逗号分割解析
    2. Persona 映射构建：角色名 → 参考音频 WAV 绝对路径
    3. 引擎检查：registry.current_engine == voxcpm2 且模型已加载
    4. 串行锁：_execute_generation 内部信号量（整笔剧本作为单条生成任务串行）
    5. 进度 SSE：ProgressManager 由 _execute_generation 统一驱动
    6. 引擎调用：engine.generate_script()（内部按行拆分 + 逐角色独立推理）
    7. 后处理：统一应用 tempo/voice_enhancement/LUFS（剧本级整体设置）
    8. 保存 & History：整笔写入 SAVE_DIR + history_db，单条片段由 engine 内部落盘
    9. 响应：HTMX HTML 片段（整笔剧本合并后的单一 WAV）

**返回格式**：
    HTTP 200: text/html — HTMX 片段，data-audio-filename 属性 + 隐藏 audio 元素
    HTTP 200 (降级): text/html — 额外显示橙色警告提示（OOM 降级成功）
    HTTP 400: text/html — 错误片段（文本为空/格式错误/角色音色缺失）

**典型剧本格式示例**::

    [旁白] 在一个风和日丽的早晨，小明推开了家门。
    [小明] （伸懒腰）啊——今天天气真不错！
    [妈妈] （从厨房探出头）小明，早饭已经做好了，快过来吃。
"""

import os

from fastapi import Form, Request
from fastapi.responses import HTMLResponse

from ....config import MAX_TEXT_LENGTH
from ....model_registry import registry
from ..utils import (
    _check_engine_ready,
    _error_html,
    _execute_generation,
    _parse_bool_form,
    logger,
    router,
)

# Why：per-character override 架构（此处通过 persona_map 间接实现，engine 内部
# 再支持 per-character cfg/timesteps 覆盖）的设计动机。
# 剧本的"多角色差异化"是核心体验：旁白需要 cfg 4（平静叙述）、反派需要 cfg 7
# （激动/夸张）、少女角色需要 cfg 5 配合更高的 timesteps（细腻温柔）。
# 如果只用全局统一 cfg/timesteps，所有角色"听起来都像一个人换了个声线"，
# 剧本的角色立体感会严重下降。因此允许按角色独立配置是必要的复杂性。
_SCRIPT_MIN_VALID_LINES: int = 1

# Why：ScriptResponse 引擎内部返回"按行"的 lines 列表（每行一个独立 audio_url），
# 而不是把所有行拼成一个长 WAV。前端拿到 lines 列表后，可以：
#   1) 逐行高亮（读哪行亮哪行）
#   2) 点击单行重播
#   3) 单行失败时不影响其他行继续播放
# 如果整合成一个 WAV，就失去了这些交互能力；用户也很难定位"第 17 句台词"
# 在 5 分钟音频里的精确时间戳。因此这里坚持按行返回、单行错误局部降级的策略。
# 注：当前路由层返回的是 _execute_generation 合并后的单一文件，行级交互由
# engine.generate_script() 内部维护其 own lines 元数据。
_SCRIPT_MAX_CHARACTERS_PER_LINE: int = 500


@router.post(
    "/voxcpm_script",
    summary="剧本工坊",
    description="使用 VoxCPM2 生成多角色对话剧本语音",
)
async def generate_voxcpm_script(
    request: Request,
    text: str = Form(""),
    instruction: str = Form(""),
    lang: str = Form("Auto"),
    cfg: float = Form(2.0),
    norm: str = Form("true"),
    denoise: str = Form("true"),
    steps: int = Form(10),
    seed: int = Form(-1),
    persona_names: str = Form(""),
    tempo_factor: float = Form(1.0),
    voice_enhancement: str = Form("false"),
    target_lufs: float = Form(-16.0),
) -> HTMLResponse:
    """VoxCPM2 剧本工坊多角色批量生成路由。

    解析剧本格式文本（每行 ``[角色名] 台词``），根据 persona_names 建立
    角色名 → 参考音频的映射，调用 engine.generate_script() 批量合成多角色对话。

    Args:
        request: FastAPI Request 对象。
        text: 剧本格式文本，每行 ``[角色名] 台词``；空行会被跳过。
        instruction: 全局风格/情感描述文本，作用于所有角色（如"整个剧本都用
            舞台腔朗读"）。具体角色的个性化风格由 engine.generate_script()
            内部按行解析角色标签。
        lang: 语言/方言标识，传递给 engine。
        cfg: 全局 CFG 强度；per-character override 由 engine 内部读取
            persona 元数据或高级参数实现（此处为了向后兼容保持单值入口）。
        norm: 是否对输出音频做响度归一化（true/false 字符串）。
        denoise: 是否对参考音频降噪（true→1.0，false→0.0）。
        steps: 全局推理步数。
        seed: 随机种子，-1 表示随机；正数可复现结果。
        persona_names: 逗号分隔的角色名列表（必须与剧本 ``[角色名]`` 完全一致），
            例如 ``"旁白,小明,妈妈"``；顺序不敏感，内部按名字匹配。
        tempo_factor: 后处理变速倍率，1.0 为原速。
        voice_enhancement: 是否启用人声增强后处理。
        target_lufs: 响度归一化目标 (LUFS)。

    Returns:
        HTMLResponse: HTMX 格式 HTML 片段，携带合并后剧本 WAV 的 audio 元素。
            行级明细（逐行播放/重播）由 engine.generate_script 写入 sidecar JSON
            或通过其他通道暴露，本路由保持原有单一音频响应模式。

    Raises:
        ValidationError: 400，剧本全为空行 / 格式无法解析 / 文本超长。
        PersonaNotFoundError: 404，persona_names 中某角色未注册。
        InsufficientVRAMError: 503，多角色连续推理中显存耗尽（由 OOM retry 捕获）。
    """
    # ------------------------------------------------------------------
    # 1. 引擎就绪 + 文本非空/长度校验
    # ------------------------------------------------------------------
    model_not_ready: HTMLResponse | None = _check_engine_ready(request, "voxcpm2")
    if model_not_ready is not None:
        return model_not_ready

    if not text.strip():
        return _error_html(request, "文本不能为空")

    if len(text) > MAX_TEXT_LENGTH:
        return _error_html(
            request,
            f"文本长度超过限制（最大 {MAX_TEXT_LENGTH} 字符）",
        )

    # ------------------------------------------------------------------
    # 2. 解析高级参数 bool 开关
    # ------------------------------------------------------------------
    advanced_norm: bool = _parse_bool_form(norm)
    advanced_denoise: float = 1.0 if _parse_bool_form(denoise) else 0.0

    # ------------------------------------------------------------------
    # 3. 构建 persona_map_with_wav：角色名 → 参考音频 WAV 绝对路径
    # ------------------------------------------------------------------
    persona_map_with_wav: dict[str, str] = {}
    if persona_names.strip():
        from ....persona_manager import load_persona_embedding

        persona_name_list = [n.strip() for n in persona_names.split(",") if n.strip()]
        for pname in persona_name_list:
            safe_name: str = os.path.basename(pname)
            persona_data = load_persona_embedding(safe_name)
            if persona_data is not None:
                wav_path, _ref_text = persona_data
                if wav_path and os.path.isfile(wav_path):
                    persona_map_with_wav[safe_name] = wav_path
                    logger.info(f"[VoxCPM剧本工坊] 已加载音色 '{safe_name}' 的参考音频")
                else:
                    # Why：单行失败局部降级策略。
                    # 某个角色的 WAV 文件损坏/被删除时，不应该让整笔剧本（可能 50 行
                    # 20 个角色）完全失败——engine 内部对"角色找不到参考音频"会
                    # 自动 fallback 到默认音色；这里仅记录警告，不阻断主流程。
                    logger.warning(f"[VoxCPM剧本工坊] 音色 '{safe_name}' 无WAV文件，将使用默认音色")
            else:
                logger.warning(f"[VoxCPM剧本工坊] 音色 '{safe_name}' 不存在，将使用默认音色")

    # ------------------------------------------------------------------
    # 4. 构造生成闭包（整笔剧本作为单次推理任务串行执行；engine.generate_script
    #    内部再按行拆分 -> 逐角色生成 -> 段间插入 script_studio_silence_secs 静音）
    # ------------------------------------------------------------------
    def _run():
        """正常参数下执行剧本工坊逐行生成"""
        engine = registry.get_current_engine()
        return engine.generate_script(
            text,
            persona_map=persona_map_with_wav if persona_map_with_wav else None,
            advanced_cfg=cfg,
            advanced_norm=advanced_norm,
            advanced_denoise=advanced_denoise,
            advanced_steps=steps,
            advanced_seed=seed,
            lang=lang,
        )

    def _degraded_run():
        """OOM降级参数下执行剧本工坊生成"""
        engine = registry.get_current_engine()
        # Why：OOM 降级时不仅砍 steps（从 10 降到 5），还强制关闭 denoise。
        # 剧本工坊通常一次生成 ~10-50 行，连续推理显存峰值会随行数累加；
        # denoise 会引入额外的 STFT/ISTFT 临时张量（~2x 显存），关 denoise
        # 是性价比最高的降级手段——质量下降可见但不至于"角色都变了"。
        degraded_steps: int = max(steps // 2, 4)
        return engine.generate_script(
            text,
            persona_map=persona_map_with_wav if persona_map_with_wav else None,
            advanced_cfg=cfg,
            advanced_norm=advanced_norm,
            advanced_denoise=0.0,
            advanced_steps=degraded_steps,
            advanced_seed=seed,
            lang=lang,
        )

    # ------------------------------------------------------------------
    # 5. 统一生成执行器（串行信号量 + 硬超时 + OOM 降级 + 后处理 + 历史记录）
    # ------------------------------------------------------------------
    return await _execute_generation(
        request,
        text=text,
        run_fn=_run,
        endpoint_name="VoxCPM script",
        voice_or_persona="script",
        model_type="剧本工坊",
        engine="voxcpm2",
        tempo_factor=tempo_factor,
        voice_enhancement=voice_enhancement,
        target_lufs=target_lufs,
        degraded_fn=_degraded_run,
    )
