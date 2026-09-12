"""Voicebox 语音转换（Voice Conversion）路由模块。

**端点**：POST ``/api/generate/voicebox/convert``

**功能**：
    将源说话人音频的音色转换为目标参考音频的音色，保留源音频的
    语音内容和韵律。基于 OpenVoice ToneColorConverter 实现零样本音色转换。

**与 TTS 路由的区别**：
    - TTS 路由：文本 → 语音（text-to-speech）
    - 本路由：音频 → 音频（voice conversion）
    不需要输入文本，只需要源音频和目标音色参考音频。

**表单参数（multipart/form-data）**：
    - source_audio (File, 必填)：源说话人音频文件（要被转换音色的语音）
    - target_audio (File, 必填)：目标音色参考音频文件（提供目标音色，建议 3-30 秒）
    - tau (float, 可选)：音色转换强度 0.0-1.0，默认 0.3
    - output_format (str, 可选)：输出格式，目前仅支持 wav

**返回格式**：
    - 成功：HTMX HTML 片段（text/html），含 <audio> 标签 + 状态消息
    - 失败：HTMX HTML 错误片段

**典型用法**：
    1. 用户上传一段自己的语音（source_audio）
    2. 用户上传一段目标音色的参考音频（target_audio，如某配音演员的声音）
    3. 点击转换，输出用目标音色重说源内容的音频
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

# 全局 VoiceboxEngine 实例（懒加载，单例）
_voicebox_engine = None
_voicebox_loading = False


def _get_voicebox_engine(request: Request):
    """获取或创建 VoiceboxEngine 单例。

    懒加载模式：首次调用时创建并加载模型，后续调用复用实例。

    Args:
        request: FastAPI 请求对象（用于获取应用状态和配置）。

    Returns:
        tuple[VoiceboxEngine | None, str | None]: (引擎实例, 错误消息)
    """
    global _voicebox_engine, _voicebox_loading

    if _voicebox_engine is not None and _voicebox_engine.is_ready():
        return _voicebox_engine, None

    if _voicebox_loading:
        return None, "Voicebox 引擎正在加载中，请稍候..."

    _voicebox_loading = True
    try:
        from ....engines.voicebox_engine import VoiceboxEngine

        config = get_config()
        engines_config = config.get("models", {}).get("engines", {})
        voicebox_config = engines_config.get("voicebox", {})

        # 解析模型路径
        model_source_mode = config.get("models", {}).get("model_source_mode", "portable")
        model_dir = voicebox_config.get("model_dir", "OpenVoice")
        if model_source_mode == "shared":
            shared_root = config.get("models", {}).get("shared_models_root", "")
            model_path = os.path.join(shared_root, model_dir) if shared_root else model_dir
        else:
            model_path = os.path.join("model", model_dir)

        base_se_path = voicebox_config.get("base_se_path", "")
        device = None  # 自动检测

        engine = VoiceboxEngine(
            model_path=model_path,
            device=device,
            base_se_path=base_se_path if base_se_path else None,
        )
        engine.load()
        _voicebox_engine = engine
        logger.info("[Voicebox Route] 引擎加载成功: %s", model_path)
        return engine, None

    except Exception as e:
        logger.error("[Voicebox Route] 引擎加载失败: %s", e, exc_info=True)
        return None, f"Voicebox 引擎加载失败: {e}"
    finally:
        _voicebox_loading = False


@router.post(
    "/voicebox/convert",
    summary="语音转换（音色迁移）",
    description="将源音频的音色转换为目标参考音频的音色（基于 OpenVoice ToneColorConverter）",
)
async def voicebox_convert_endpoint(
    request: Request,
    source_audio: UploadFile = File(...),
    target_audio: UploadFile = File(...),
    tau: float = Form(0.3),
    output_format: str = Form("wav"),
) -> HTMLResponse:
    """Voicebox 语音转换端点。

    Args:
        request: FastAPI 请求对象。
        source_audio: 源说话人音频文件（要被转换音色的语音）。
        target_audio: 目标音色参考音频文件。
        tau: 音色转换强度（0.0-1.0），默认 0.3。
        output_format: 输出格式，目前仅支持 wav。

    Returns:
        HTMLResponse: 成功/失败的 HTMX 片段。
    """
    start_time = time.time()

    # 1. 校验参数
    if tau < 0.0 or tau > 1.0:
        return _error_html(request, "tau 参数必须在 0.0-1.0 之间", status_code=400)

    if not source_audio.filename:
        return _error_html(request, "源音频文件不能为空", status_code=400)

    if not target_audio.filename:
        return _error_html(request, "目标参考音频文件不能为空", status_code=400)

    # 2. 保存上传的音频文件
    source_path, source_err = await save_uploaded_audio(request, source_audio)
    if source_err:
        return source_err

    target_path, target_err = await save_uploaded_audio(request, target_audio)
    if target_err:
        return target_err

    # 3. 获取引擎（懒加载）
    engine, load_err = _get_voicebox_engine(request)
    if load_err:
        return _error_html(request, load_err, status_code=503)

    # 4. 生成输出路径
    output_dir = Path("outputs")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(output_dir / f"voicebox_convert_{int(time.time())}.{output_format}")

    # 5. 执行转换（在线程池中执行，避免阻塞事件循环）
    try:
        import asyncio

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: engine.voice_conversion(
                source_audio=source_path,
                target_audio=target_path,
                output_path=output_path,
                tau=tau,
            ),
        )
    except Exception as e:
        logger.error("[Voicebox Route] 转换执行异常: %s", e, exc_info=True)
        return _error_html(request, f"转换执行失败: {e}", status_code=500)

    # 6. 返回结果
    elapsed = time.time() - start_time

    if not result.success:
        return _error_html(request, result.message, status_code=500)

    # 构建 HTMX 成功响应
    audio_filename = os.path.basename(result.audio_path)
    success_html = f"""
    <div class="voicebox-result" data-audio-filename="{audio_filename}">
        <div class="result-success">
            <p>✅ 音色转换完成（耗时 {elapsed:.1f}s）</p>
            <p>源音频: {os.path.basename(source_path)}</p>
            <p>目标音色: {os.path.basename(target_path)}</p>
            <p>转换强度 (tau): {tau}</p>
            <p>输出时长: {result.duration:.2f}s</p>
        </div>
        <audio controls preload="metadata">
            <source src="/api/audio/{audio_filename}" type="audio/wav">
            您的浏览器不支持音频播放。
        </audio>
        <a href="/api/audio/{audio_filename}" download="{audio_filename}">
            下载转换后的音频
        </a>
    </div>
    """
    return HTMLResponse(content=success_html)
