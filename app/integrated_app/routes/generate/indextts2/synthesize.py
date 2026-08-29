"""IndexTTS2 情感控制（Emotion-Controllable）语音合成路由模块。

**路由前缀与端点**：
    前缀：``/api/generate/indextts2``
    - POST /indextts2 — IndexTTS2 情感控制语音合成

**HTTP 方法**：
    仅接受 POST 请求，参数通过 ``multipart/form-data`` 表单提交，
    支持双文件上传（参考音频 ref_audio + 情感参考音频 emo_audio）。

**在 TTS 生成管线中的位置**：
    位于生成管线的"情感维度控制"环节，是 IndexTTS2 引擎区别于 VoxCPM2 的核心
    能力入口。VoxCPM2 通过 instruction 文本描述间接控制情感，IndexTTS2 则
    直接提供 **8 维情感向量**（happy/angry/sad/afraid/disgusted/melancholic/
    surprised/calm）的精细旋钮，并支持 **时长控制 (speed 0.5x-2.0x)**，
    适合需要精确控制"情绪强度+语速节奏"的场景（如游戏配音、有声书角色塑造）。

**核心能力**：
    - 零样本克隆（参考音频 + 文本 -> 新文本同音色合成）
    - 8 维情感向量精细控制（每维 ∈ [0, 1]，总和超过 1 时自动归一化）
    - 时长控制：speed ∈ [0.5x, 2.0x]，支持 target_duration 精确目标时长
    - 三种情感注入模式互斥（优先级从高到低）：
      1. 情感文本 (emo_text)：自然语言描述情感，适合非专业用户
      2. 情感音频 (emo_audio)：上传带目标情感的参考音频，迁移情感风格
      3. 8 维情感向量 (emo_*)：8 个滑杆精确控制，适合专业场景复现
    - emo_alpha 情感强度混合比：[0, 1]，1 为完全情感化，0 为完全中性

**异步任务队列与并发控制**：
    - 通过 ``_execute_generation()`` 获取 per-engine asyncio.Semaphore（默认容量 1）
    - 信号量获取超时：120 秒；单次生成硬超时：600 秒
    - GPU 推理在 ``run_in_executor`` 线程池中执行，内部按 segment 拆分逐段推理
    - OOM 自动降级：显存不足时自动释放缓存并重试（最多 2 次）
    - 临时 WAV 文件用完即删，避免 outputs/ 目录堆积

**SSE 事件说明**：
    本端点为非流式同步端点，所有 segment 推理完成后合并为单一 WAV 返回。
    生成进度通过统一 SSE 端点 ``/api/sse/events`` 推送：
    1. ``status`` — 任务开始，引擎初始化中
    2. ``progress`` — 逐段进度百分比（已完成段数/总段数）
    3. ``time_estimate`` — 预计剩余时间
    4. ``complete`` — 全部完成，携带合并后音频 URL
    5. ``engine_switch`` — 引擎切换通知（如从 VoxCPM2 切换到 IndexTTS2）
    6. ``error`` / ``cancelled`` — 失败或取消

**情感向量校验规则**：
    - 白名单：仅支持 happy/angry/sad/afraid/disgusted/melancholic/surprised/calm 8 维
    - 缺键补 0：未传的情感维度默认为 0（中性）
    - 多键拒绝：传入未知情感标签直接返回 400 错误
    - 总和归一化：8 维总和 > 1 时自动按比例缩放并打印 warning

**通用生成管线流程（IndexTTS2 版）**：
    1. 参数校验：pre_validate（引擎就绪 + 文本非空/长度）
    2. 音频上传：保存 ref_audio（音色参考）和 emo_audio（情感参考，可选）
    3. 情感模式判定：三选一（emo_text > emo_audio > 8 维向量）+ 向量归一化
    4. 时长参数处理：target_duration > 0 时启用精确时长控制
    5. 引擎检查：registry.current_engine == indextts2 且 indextts2_engine 非 None
    6. 串行锁：_execute_generation 内部信号量（per-engine）
    7. 进度 SSE：ProgressManager 由 _execute_generation_impl 统一驱动
    8. 引擎调用：engine.infer() — 内部按 segment 拆分逐段推理 → 临时 WAV → 合并
    9. 后处理：tempo_factor（变速）/ voice_enhancement（人声增强）/ target_lufs（响度归一化）
    10. 保存 & History：写入 SAVE_DIR + history_db（_execute_generation 内部）
    11. 清理：删除临时 segment WAV 文件
    12. 响应：HTMX HTML 片段

**返回格式**：
    HTTP 200: text/html — HTMX 片段，data-audio-filename 属性 + 隐藏 audio 元素
    HTTP 200 (降级): text/html — 额外显示橙色警告提示（OOM 降级成功）
    HTTP 400: text/html — 错误片段（参数错误/情感标签未知/参考音频问题）
    HTTP 503: text/html — 引擎未加载/显存不足
"""

import contextlib
import os
import time
from typing import Any

import numpy as np
from fastapi import File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from ....config import MAX_TEXT_LENGTH, SAVE_DIR
from ..utils import (
    _error_html,
    _execute_generation,
    pre_validate,
    router,
    save_uploaded_audio,
)

# IndexTTS2 情感 8 维白名单（模型训练时的 softmax 输出类目，缺一补 0，多一直接拒绝）
# 顺序必须与模型权重的情感分类头输出顺序一致。
_EMOTION_LABELS_WHITELIST = (
    "happy",
    "angry",
    "sad",
    "afraid",
    "disgusted",
    "melancholic",
    "surprised",
    "calm",
)

# Why：8 维情感向量总和强制 ≤ 1（并在 > 1 时归一化到 1）。
# 模型训练时情感分类头输出的是 softmax 概率分布（总和 = 1），推理时情感向量会
# 作为 condition 注入 decoder。如果用户传入 0.9 happy + 0.9 angry = 1.8，
# 模型内部会做一次压缩归一化，但实际情感强度是不可预测的（可能"既不高兴也不生气
# 反而变成平静"）。为保证可控性，这里显式归一化并打印 warning，
# 让用户知道"我输入的 1.8 被缩成了 1"的换算关系。
_EMOTION_VECTOR_SUM_UPPER_BOUND: float = 1.0

# Why：speed 硬限制在 [0.5, 2.0] 范围内的设计决策。
# IndexTTS2 的时长控制模块（duration predictor + length regulator）训练时的
# 时长伸缩范围基于"原始时长 × 0.5 ~ 原始时长 × 2.0"构建。超出此范围：
#   - > 2.0x（如 3.0x）：相邻 phoneme 的 duration 被压到 0，导致音节吞音
#   - < 0.5x（如 0.3x）：duration 被拉长到重复填充，出现"口吃"式重复音节
# 强行让用户输入合法范围比"允许越界但质量下降"的体验更好（后者会让用户觉得
# "模型不行"，而前者会让用户"知道我应该调什么范围"）。
_SPEED_MIN: float = 0.5
_SPEED_MAX: float = 2.0

# 情感注入模式枚举（三选一，优先级按代码顺序）
_EMOTION_MODE_TEXT: str = "text"
_EMOTION_MODE_AUDIO: str = "audio"
_EMOTION_MODE_VECTOR: str = "vector"


@router.post(
    "/indextts2",
    summary="IndexTTS2 合成",
    description="使用 IndexTTS2 引擎进行语音合成",
)
async def generate_indextts2(
    request: Request,
    text: str = Form(""),
    lang: str = Form("Auto"),
    ref_audio: UploadFile | None = File(None),
    ref_text: str = Form(""),
    seed: int = Form(0),
    emo_text: str = Form(""),
    emo_audio: UploadFile | None = File(None),
    emo_happy: float = Form(0.0),
    emo_angry: float = Form(0.0),
    emo_sad: float = Form(0.0),
    emo_afraid: float = Form(0.0),
    emo_disgusted: float = Form(0.0),
    emo_melancholic: float = Form(0.0),
    emo_surprised: float = Form(0.0),
    emo_calm: float = Form(0.0),
    emo_alpha: float = Form(0.8),
    emo_alpha_text: float = Form(0.8),
    emo_alpha_audio: float = Form(0.8),
    target_duration: float = Form(0.0),
    tempo_factor: float = Form(1.0),
    voice_enhancement: str = Form("false"),
    target_lufs: float = Form(-16.0),
) -> HTMLResponse:
    """IndexTTS2 情感控制语音合成路由。

    支持三种互斥的情感注入模式（优先级：emo_text > emo_audio > 8 维情感向量）：
    1. **emo_text**：用自然语言描述情感（如"非常开心，激动地说"），模型内部
       编码为情感嵌入，适合"凭感觉调"的非专业用户。
    2. **emo_audio**：上传一段带目标情感的参考音频（如一段哭泣的录音），
       模型提取该音频的情感风格迁移到新语音上。
    3. **8 维情感向量**：emo_happy/angry/.../calm 8 个滑杆（每维 0~1），
       适合需要精确复现情感组合的专业场景。注意总和 > 1 时会自动归一化。

    同时支持 **speed 控制**（通过 target_duration 与文本长度隐式推导，或直接
    使用全局 tempo_factor 后处理变速）。

    Args:
        request: FastAPI Request 对象。
        text: 待合成的朗读文本，必填，<= MAX_TEXT_LENGTH。
        lang: 语言标识（当前仅日志用，IndexTTS2 内部自动识别语种）。
        ref_audio: 克隆音色用的参考音频文件（可选；不传则用默认说话人）。
        ref_text: 参考音频的逐字稿（可选；提供后 speaker embedding 质量更高）。
        seed: 随机种子，0 表示随机；正数可复现生成结果。
        emo_text: 情感文本描述（优先级最高）。
        emo_audio: 情感参考音频（优先级次高）。
        emo_happy: 8 维情感 — 开心，范围 [0, 1]。
        emo_angry: 8 维情感 — 愤怒，范围 [0, 1]。
        emo_sad: 8 维情感 — 悲伤，范围 [0, 1]。
        emo_afraid: 8 维情感 — 恐惧，范围 [0, 1]。
        emo_disgusted: 8 维情感 — 厌恶，范围 [0, 1]。
        emo_melancholic: 8 维情感 — 忧郁，范围 [0, 1]。
        emo_surprised: 8 维情感 — 惊讶，范围 [0, 1]。
        emo_calm: 8 维情感 — 平静，范围 [0, 1]。
        emo_alpha: 默认情感强度混合比 [0, 1]（1 = 完全用情感条件，0 = 完全中性）。
        emo_alpha_text: 当使用 emo_text 模式时的 alpha（覆盖默认 emo_alpha）。
        emo_alpha_audio: 当使用 emo_audio 模式时的 alpha（覆盖默认 emo_alpha）。
        target_duration: 目标总时长（秒），> 0 时启用精确时长控制（覆盖 speed）。
        tempo_factor: 后处理变速倍率（1.0 原速），推荐配合 target_duration 一起用。
        voice_enhancement: 是否启用人声增强后处理（"true"/"false"）。
        target_lufs: 响度归一化目标 (LUFS)，默认 -16.0。

    Returns:
        HTMLResponse: HTMX 格式 HTML 片段，携带合成后的 WAV audio 元素。

    Raises:
        ValidationError: 400，情感向量未知键 / speed 越界 / 参考音频过大 等。
        EngineNotLoadedError: 503，IndexTTS2 引擎未加载（引导用户去 Settings 加载）。
        InsufficientVRAMError: 503，CUDA OOM（由 _run_with_oom_retry 捕获降级）。
    """
    # ------------------------------------------------------------------
    # 1. 引擎就绪 + 文本长度统一校验（pre_validate 内部也会判断 current_engine）
    # ------------------------------------------------------------------
    err: HTMLResponse | None = pre_validate(request, "indextts2", text, MAX_TEXT_LENGTH)
    if err is not None:
        return err

    # 延迟导入，避免模块加载时对 torch 等大依赖的硬耦合
    from ....generation import _save_wav_compatible, split_text_for_tts
    from ....model_registry import registry

    # 再次显式取 engine 引用：pre_validate 已检查过非空，这里再取一次用于 infer()
    engine = registry.get_current_engine()

    # 2.0 与 2.5 复用本端点与同一引擎槽位，完成文案按当前激活引擎显示
    model_label: str = "IndexTTS 2.0" if registry.current_engine == "indextts20" else "IndexTTS 2.5"

    # ------------------------------------------------------------------
    # 2. 处理参考音频（ref_audio）与情感参考音频（emo_audio）
    # ------------------------------------------------------------------
    ref_audio_path: str | None = None
    emo_audio_path: str | None = None

    ref_audio_path, err = await save_uploaded_audio(request, ref_audio)
    if err is not None:
        return err

    emo_audio_path, err = await save_uploaded_audio(request, emo_audio)
    if err is not None:
        return err

    # ------------------------------------------------------------------
    # 3. 情感模式判定：三选一，优先级 emo_text > emo_audio > 8 维向量
    # ------------------------------------------------------------------
    emotion_mode: str | None = None
    emotion_data: Any = None
    emotion_alpha: float = emo_alpha

    if emo_text and emo_text.strip():
        emotion_mode = _EMOTION_MODE_TEXT
        emotion_data = emo_text.strip()
        emotion_alpha = emo_alpha_text
    elif emo_audio_path:
        emotion_mode = _EMOTION_MODE_AUDIO
        emotion_data = emo_audio_path
        emotion_alpha = emo_alpha_audio
    else:
        # 8 维情感向量（构造 dict，方便后续做"缺 0 / 多报错"校验）
        emotion_dict: dict[str, float] = {
            "happy": emo_happy,
            "angry": emo_angry,
            "sad": emo_sad,
            "afraid": emo_afraid,
            "disgusted": emo_disgusted,
            "melancholic": emo_melancholic,
            "surprised": emo_surprised,
            "calm": emo_calm,
        }
        # Why：8 维情感向量的"缺键补 0，多键报错"策略。
        # 缺键（例如前端迭代时忘了加 calm 滑杆）不应该让整条请求挂掉——训练时
        # 没填的维度就是 0，语义等价。但多键（如用户通过 JSON API 传了 "excited"）
        # 是真正的非法输入——模型根本没有第 9 个情感头，强行塞进去会导致
        # tensor shape mismatch 或者被静默丢弃。为避免后者这种"静默出错"，
        # 显式返回 400 并列出支持的标签列表。
        # 注：此处 emotion_dict 由代码硬编码 8 键构造，"多键"场景只在未来
        # 修改代码时才可能出现，此处保留防御性校验以对未来修改负责。
        unknown_keys = [k for k in emotion_dict if k not in _EMOTION_LABELS_WHITELIST]
        if unknown_keys:
            return _error_html(
                request,
                f"未知情感标签：{', '.join(unknown_keys)}，支持 8 维：{', '.join(_EMOTION_LABELS_WHITELIST)}",
            )
        # 缺键补 0（当前硬编码 8 键必然全有，但防御性保证未来的变动）
        for label in _EMOTION_LABELS_WHITELIST:
            emotion_dict.setdefault(label, 0.0)
        # 所有维度 <= 0（用户没拖任何滑杆）时跳过向量模式，退回纯中性合成
        if any(v > 0 for v in emotion_dict.values()):
            # 归一化：sum > 1 时按比例缩放，sum <= 1 时保持原样（允许有"不确定"余量）
            emo_sum = sum(emotion_dict.values())
            if emo_sum > _EMOTION_VECTOR_SUM_UPPER_BOUND:
                # Why：sum > 1 时不直接报错而是"归一化 + warning"。
                # 前端滑杆交互下"用户同时把 happy 拉到 1.0，angry 也拉到 1.0"
                # 是非常自然的操作——用户就是想表达"又喜又怒"。直接 400 拒绝
                # 体验太差；自动换算到 happy=0.5/angry=0.5 则符合直觉（各占一半）。
                from ..utils import logger as _logger

                _logger.warning(
                    "[IndexTTS2] 8 维情感向量总和 %.2f > 上限 %.2f，已自动归一化。原始值：%s",
                    emo_sum,
                    _EMOTION_VECTOR_SUM_UPPER_BOUND,
                    emotion_dict,
                )
                emotion_dict = {k: v / emo_sum for k, v in emotion_dict.items()}
            emotion_mode = _EMOTION_MODE_VECTOR
            emotion_data = emotion_dict

    # ------------------------------------------------------------------
    # 4. 时长控制处理
    # target_duration（精确总时长秒）优先级高于 speed；> 0 时启用。
    # 注意：speed 的 0.5x-2.0x 限制在 IndexTTS2 engine 内部按 target_duration /
    # 文本自然时长 隐式校验，如果超出范围 engine 会在内部 clamp 并打日志。
    # 此处不做双重校验以避免与 engine 内部逻辑漂移。
    # ------------------------------------------------------------------
    target_dur: float | None = target_duration if target_duration > 0 else None

    # ------------------------------------------------------------------
    # 5. 构造生成闭包（按 segment 拆分 -> 每段 infer -> 合并 -> 写盘）
    # ------------------------------------------------------------------
    def _run():
        """IndexTTS2分段情感合成闭包（含临时文件清理、segment逐段推理逻辑）"""
        segments = split_text_for_tts(text)
        all_audio: list[np.ndarray] = []
        # IndexTTS 2.5 / 2.0 原生输出 22050Hz；以引擎实际返回的 sample_rate 为准，
        # 不再硬编码 44100（那会让浏览器按 2 倍速播放，音调与时长全错）。
        out_sample_rate: int = 22050
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            infer_kwargs: dict[str, Any] = {
                "text": seg,
                "spk_audio_prompt": ref_audio_path or "",
                "output_path": None,
                "verbose": False,
                "lang": lang,
            }
            if emotion_data is not None:
                if emotion_mode == _EMOTION_MODE_TEXT:
                    infer_kwargs["emo_text"] = emotion_data
                    infer_kwargs["use_emo_text"] = True
                elif emotion_mode == _EMOTION_MODE_AUDIO:
                    infer_kwargs["emo_audio_prompt"] = emotion_data
                elif emotion_mode == _EMOTION_MODE_VECTOR:
                    # 必须按白名单固定顺序传 list 给 engine（内部按 index 对齐）
                    ordered_values = [float(emotion_data[label]) for label in _EMOTION_LABELS_WHITELIST]
                    infer_kwargs["emo_vector"] = ordered_values
            infer_kwargs["emo_alpha"] = emotion_alpha
            if target_dur is not None:
                infer_kwargs["target_duration"] = target_dur
            if seed > 0:
                infer_kwargs["seed"] = seed

            # engine.infer() 的对外契约是 (sample_rate, wav, output_path) 三元组
            # （见 engines/indextts2_engine.py 的 Returns 说明与内部调用点）。
            # 历史上本行把返回值当 str 用并对 tuple 调 os.path.exists()，
            # 导致 IndexTTS 每次都「推理已完成、wav 已落盘」却在收尾抛
            # `_path_exists: path should be ... not tuple` → 整条请求 400、前端拿不到音频。
            seg_result = engine.infer(**infer_kwargs)
            if not seg_result:
                continue
            seg_sr, seg_wav, seg_path = seg_result
            data = np.asarray(seg_wav)
            if data.size == 0:
                continue
            if data.dtype != np.int16:
                data = (data.astype(np.float32) * 32768.0).clip(-32768, 32767).astype(np.int16)
            all_audio.append(data)
            if seg_sr:
                out_sample_rate = int(seg_sr)
            # 引擎已写盘的临时 WAV 用完即删：避免 SAVE_DIR/tmp 堆积
            if seg_path:
                with contextlib.suppress(OSError):
                    if os.path.isfile(seg_path):
                        os.remove(seg_path)

        if not all_audio:
            return None, "生成失败：未产生任何音频数据"

        combined: np.ndarray = np.concatenate(all_audio) if len(all_audio) > 1 else all_audio[0]
        timestamp: int = int(time.time())
        filename: str = f"indextts2_{timestamp}.wav"
        output_path: str = os.path.join(SAVE_DIR, filename)
        _save_wav_compatible(combined, output_path, sample_rate=out_sample_rate)
        return (
            out_sample_rate,
            "wav",
            filename,
        ), f"{model_label} 生成完成"

    # ------------------------------------------------------------------
    # 6. 统一生成执行器：
    #    - 串行信号量
    #    - 硬超时保护
    #    - OOM 降级重试（OOM retry 内部会调用 free_gpu_memory 清理缓存）
    #    - 后处理（tempo/voice_enhancement/LUFS）
    #    - 写入 history_db
    #    - 返回 HTMX HTML 片段
    # ------------------------------------------------------------------
    return await _execute_generation(
        request,
        text=text,
        run_fn=_run,
        endpoint_name="IndexTTS2",
        voice_or_persona="",
        model_type=model_label,
        engine="indextts2",
        tempo_factor=tempo_factor,
        voice_enhancement=voice_enhancement,
        target_lufs=target_lufs,
    )
