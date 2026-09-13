"""Step-Audio-EditX 音频编辑路由模块。

**端点**：POST ``/api/generate/step-audio-editx/edit``

**功能**：
    对已有参考音频进行情绪、风格、副语言、语速等精细编辑，保留原说话人
    身份和音色。基于 StepFun AI 开源的 Step-Audio-EditX 模型（vLLM + CosyVoice）。

**与 TTS 路由的区别**：
    - TTS 路由：文本 → 语音（text-to-speech）
    - 本路由：参考音频 + 编辑指令 → 编辑后音频（audio editing）
    需要提供参考音频及其精确转写文本。

**编辑类型（edit_type）**：
    - emotion：情绪编辑（happy/angry/sad/...，共 16 种）
    - style：风格编辑（serious/arrogant/child/older/...，共 35 种）
    - paralinguistic：副语言编辑（[Laughter]/[Sigh] 标签嵌入目标文本）
    - speed：语速编辑（faster/slower/more faster/more slower）
    - vad：语音活动检测（辅助）
    - denoise：降噪（辅助）

**表单参数（multipart/form-data）**：
    - source_audio (File, 必填)：源参考音频文件（提供音色和语音内容）
    - source_text (str, 必填)：源参考音频的精确转写文本
    - edit_type (str, 必填)：编辑类型（emotion/style/paralinguistic/speed/vad/denoise）
    - edit_info (str, 可选)：编辑子类型/标签（如 "happy"、"older"）
    - target_text (str, 可选)：目标文本（可包含副语言标签，默认使用 source_text）
    - output_format (str, 可选)：输出格式，目前仅支持 wav

**返回格式**：
    - 成功：HTMX HTML 片段（text/html），含 <audio> 标签 + 状态消息
    - 失败：HTMX HTML 错误片段
"""

import logging
import os
import time
from pathlib import Path

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from ....config import get_config
from ..utils import _error_html, router, save_uploaded_audio

logger = logging.getLogger("tts_multimodel")

# 全局 StepAudioEditXEngine 实例（懒加载，单例）
_editx_engine = None
_editx_loading = False


def _get_editx_engine(request: Request):
    """获取或创建 StepAudioEditXEngine 单例。

    懒加载模式：首次调用时创建并加载模型，后续调用复用实例。

    Args:
        request: FastAPI 请求对象。

    Returns:
        tuple[StepAudioEditXEngine | None, str | None]: (引擎实例, 错误消息)
    """
    global _editx_engine, _editx_loading

    if _editx_engine is not None and _editx_engine.is_ready():
        return _editx_engine, None

    if _editx_loading:
        return None, "Step-Audio-EditX 引擎正在加载中，请稍候..."

    _editx_loading = True
    try:
        from ....engines.step_audio_editx_engine import StepAudioEditXEngine

        config = get_config()
        models_cfg = config.pydantic_config.models
        editx_spec = models_cfg.get_engine_spec("step-audio-editx")
        model_dir = editx_spec.model_dir if editx_spec else "Step-Audio-EditX"

        # 解析模型路径（shared / portable 双模式，对齐 config.py get_pretrained_dir）
        if models_cfg.model_source_mode == "shared" and models_cfg.shared_models_root:
            model_path = os.path.join(models_cfg.shared_models_root, model_dir)
        elif models_cfg.model_source_mode == "shared":
            # shared 模式但 shared_models_root 为空：与旧 raw-config 缺省一致，回退到相对 model_dir
            model_path = model_dir
        else:
            model_path = os.path.join("model", model_dir)

        repo_path = editx_spec.repo_path if editx_spec else ""
        tokenizer_path = editx_spec.tokenizer_path if editx_spec else ""
        gpu_memory_utilization = editx_spec.gpu_memory_utilization if editx_spec else 0.5
        max_model_len = editx_spec.max_model_len if editx_spec else 3072
        dtype = editx_spec.dtype if editx_spec else "bfloat16"

        engine = StepAudioEditXEngine(
            model_path=model_path,
            tokenizer_path=tokenizer_path if tokenizer_path else None,
            repo_path=repo_path if repo_path else None,
            device=None,  # 自动检测
            gpu_memory_utilization=gpu_memory_utilization,
            max_model_len=max_model_len,
            dtype=dtype,
        )
        engine.load()
        _editx_engine = engine
        logger.info("[StepAudioEditX Route] 引擎加载成功: %s", model_path)
        return engine, None

    except Exception as e:
        logger.error("[StepAudioEditX Route] 引擎加载失败: %s", e, exc_info=True)
        return None, f"Step-Audio-EditX 引擎加载失败: {e}"
    finally:
        _editx_loading = False


@router.post(
    "/step-audio-editx/edit",
    summary="音频编辑（情绪/风格/副语言/语速）",
    description="对参考音频进行情绪/风格/副语言/语速编辑（基于 Step-Audio-EditX）",
)
async def step_audio_editx_edit_endpoint(
    request: Request,
    source_audio: UploadFile = File(...),
    source_text: str = Form(...),
    edit_type: str = Form(...),
    edit_info: str = Form(""),
    target_text: str = Form(""),
    output_format: str = Form("wav"),
) -> HTMLResponse:
    """Step-Audio-EditX 音频编辑端点。

    Args:
        request: FastAPI 请求对象。
        source_audio: 源参考音频文件。
        source_text: 源参考音频的精确转写文本。
        edit_type: 编辑类型（emotion/style/paralinguistic/speed/vad/denoise）。
        edit_info: 编辑子类型/标签。
        target_text: 目标文本（可包含副语言标签，空则使用 source_text）。
        output_format: 输出格式（目前仅支持 wav）。

    Returns:
        HTMLResponse: 成功/失败的 HTMX 片段。
    """
    start_time = time.time()

    # 1. 校验参数
    if not source_text or not source_text.strip():
        return _error_html(request, "source_text 不能为空（必须提供参考音频的精确转写文本）", status_code=400)

    if not source_audio.filename:
        return _error_html(request, "源参考音频文件不能为空", status_code=400)

    # 2. 保存上传的音频
    source_path, source_err = await save_uploaded_audio(request, source_audio)
    if source_err:
        return source_err

    # 3. 获取引擎（懒加载）
    engine, load_err = _get_editx_engine(request)
    if load_err:
        return _error_html(request, load_err, status_code=503)

    # 4. 生成输出路径
    output_dir = Path("outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(output_dir / f"audio_edit_{edit_type}_{int(time.time())}.{output_format}")

    # 5. 执行编辑（在线程池中执行，避免阻塞事件循环）
    try:
        import asyncio

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: engine.edit_audio(
                source_audio=source_path,
                source_text=source_text,
                edit_type=edit_type,
                edit_info=edit_info,
                target_text=target_text if target_text.strip() else None,
                output_path=output_path,
            ),
        )
    except Exception as e:
        logger.error("[StepAudioEditX Route] 编辑执行异常: %s", e, exc_info=True)
        return _error_html(request, f"编辑执行失败: {e}", status_code=500)

    # 6. 返回结果
    elapsed = time.time() - start_time

    if not result.success:
        return _error_html(request, result.message, status_code=500)

    audio_filename = os.path.basename(result.audio_path)
    success_html = f"""
    <div class="step-audio-editx-result" data-audio-filename="{audio_filename}">
        <div class="result-success">
            <p>✅ 音频编辑完成（耗时 {elapsed:.1f}s）</p>
            <p>编辑类型: {edit_type}</p>
            <p>编辑标签: {edit_info or "(无)"}</p>
            <p>参考音频: {os.path.basename(source_path or "")}</p>
            <p>输出时长: {result.duration:.2f}s</p>
        </div>
        <audio controls preload="metadata">
            <source src="/api/audio/{audio_filename}" type="audio/wav">
            您的浏览器不支持音频播放。
        </audio>
        <a href="/api/audio/{audio_filename}" download="{audio_filename}">
            下载编辑后的音频
        </a>
    </div>
    """
    return HTMLResponse(content=success_html)
