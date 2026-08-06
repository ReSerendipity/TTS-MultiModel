"""生成路由通用工具模块。

架构说明：
    本模块提供 generate 子路由的通用工具函数（不依赖具体引擎），
    被 VoxCPM2 / IndexTTS2 的生成路由共同调用，避免重复代码。

    主要功能分类：
    1. SSE 事件格式化与任务 ID 生成 (format_sse_event, new_task_id)
    2. 音频持久化 + 历史记录写入 (write_history_and_save_audio, save_uploaded_audio)
    3. 生成信号量与超时控制 (per-engine 并发限制 + 硬超时保护)
    4. OOM 降级重试机制 (_run_with_oom_retry)
    5. 音频后处理（响度归一化、语速调节、增强）
    6. 上传/文本输入校验
    7. 失败响应构建 (build_generation_error_response)
    8. S-R4: Legacy history.db 一次性迁移（两个 SQLite 路径合并）

注意：本模块同时暴露 `router = APIRouter(prefix="/api/generate")`，
    用于 app_server.py 顶层 pkgutil 扫描时挂载共享路由。
"""

import asyncio
import html
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import quote

import aiofiles
import numpy as np
from fastapi import APIRouter, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from ...audio_processing import enhance_audio
from ...config import MAX_UPLOAD_SIZE_BYTES, SAVE_DIR
from ...exceptions import EngineSwitchError, InsufficientVRAMError, TTSError
from ...gpu_utils import free_gpu_memory, is_oom_error
from ...history_db import get_history_db
from ...model_manager import _time_estimator
from ...monitor import get_health_monitor
from ..system import increment_generation, log_operation

router = APIRouter(prefix="/api/generate", tags=["generate"])

logger = logging.getLogger("tts_multimodel")

# ---------------------------------------------------------------------------
# S-R4: 统一 history_db 单例 — 移除模块级独立实例，改用全局 get_history_db()
# 修复 P0 数据一致性 Bug：生成路由写入 outputs/history.db，但读取页面
# 查询 data/history.db，导致用户看不到刚生成的历史记录。
# ---------------------------------------------------------------------------
_generation_semaphores: dict[str, asyncio.Semaphore] = {}
_generation_semaphore_lock = asyncio.Lock()
_generation_retry_counter: dict[str, int] = {"total": 0, "oom_retries": 0}

# REFACTOR: 集中常量，消除魔法数字
_MAX_CONCURRENT_GENERATIONS: int = max(1, int(os.environ.get("TTS_MAX_CONCURRENT_GENERATIONS", "1")))
# E6-1 SECURITY/ROBUSTNESS: 信号量获取超时 (秒) — 用户排队等待上限
_SEMAPHORE_ACQUIRE_TIMEOUT_S: float = float(os.environ.get("TTS_SEMAPHORE_TIMEOUT_S", "120.0"))
# E6-1 SECURITY/ROBUSTNESS: 单次生成硬超时 (秒) — 防止超长文本耗尽信号量池
# 默认 600s (10 分钟)，可按硬件调优。生成超时后释放信号量，返回友好错误。
_GENERATION_HARD_TIMEOUT_S: float = float(os.environ.get("TTS_GENERATION_TIMEOUT_S", "600.0"))

# S-R4: legacy history.db 迁移控制（幂等，只执行一次）
_legacy_history_migrated: bool = False
_legacy_migration_lock = threading.Lock()

# S-R4: legacy history.db 路径（原 create_history_db(SAVE_DIR) 使用的路径）
_LEGACY_HISTORY_DB_PATH: str = os.path.join(SAVE_DIR, "history.db")

ALLOWED_AUDIO_EXTENSIONS: set = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".wma", ".aac"}
_DIALECT_NAMES: set = {"四川话", "粤语", "吴语", "东北话", "河南话", "闽南语", "湖南话", "湖北话", "客家话"}


# ===========================================================================
# R9 新增公共工具函数（SSE / 任务 ID / 音频保存+历史 / 错误响应）
# ===========================================================================


def format_sse_event(
    event_type: str,
    data: dict[str, Any],
    event_id: str | None = None,
    retry: int | None = None,
) -> str:
    """构建 SSE (Server-Sent Events) 格式字符串。

    Why 注释 — retry 默认 3000ms：
        断线重连时 EventSource 默认 3 秒重试；前端若超过 15 秒没收到心跳
        会主动断开。此处显式声明 retry: 3000 让浏览器行为更可预期，
        便于前端统一心跳/重连策略调试。

    Args:
        event_type: 事件类型，如 "progress" / "complete" / "error" / "status" / "engine_switch"。
        data: 事件负载字典，需 JSON Serializable；非基本类型会通过 default=str 兜底。
        event_id: 可选事件 ID，断线重连时作为 Last-Event-ID 回传。
        retry: 可选断线重连间隔（毫秒），默认 3000。

    Returns:
        SSE 协议格式字符串：
            event: {event_type}\\n
            data: {...}\\n
            [id: {event_id}\\n]
            [retry: {retry}\\n]
            \\n
    """
    lines: list[str] = [f"event: {event_type}"]

    # data 序列化：非 JSON Serializable 对象先 default=str 兜底，再失败写 {} 保证流不中断
    try:
        data_str: str = json.dumps(data, ensure_ascii=False)
    except TypeError:
        try:
            data_str = json.dumps(data, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            logger.warning(f"format_sse_event: data 不可 JSON 序列化，已兜底 {{}} | event={event_type}")
            data_str = "{}"
    lines.append(f"data: {data_str}")

    if event_id is not None:
        lines.append(f"id: {event_id}")

    # Why: 显式 retry=3000，见函数级 Why 注释
    lines.append(f"retry: {retry if retry is not None else 3000}")

    return "\n".join(lines) + "\n\n"


def new_task_id() -> str:
    """生成新的任务 ID。

    使用 UUID4（无连字符的 32 位 hex 小写），避免 URL 转义问题，
    同时保证跨进程全局唯一性（不依赖自增 ID / 内存计数器）。

    Returns:
        32 字符小写 hex 字符串，如 "a1b2c3d4e5f6..."
    """
    return uuid.uuid4().hex


async def write_history_and_save_audio(
    audio_bytes: bytes,
    request: dict[str, Any],
    task_id: str,
    persona_id: str | None,
    engine: str,
) -> str:
    """保存生成音频到磁盘，并插入 history_db 历史记录。

    Why 顺序注释 — 先 save_audio 再 insert_history：
        history_db 记录中的 audio_url / filepath 依赖保存后生成的文件名。
        如果先 insert_history 后 save_audio 失败（如磁盘满、PermissionError），
        会产生 history 有记录但音频文件不存在的脏数据。必须先写文件成功，
        再写 DB；保存失败直接抛异常，DB 不会产生悬挂记录。

    Args:
        audio_bytes: 生成的原始音频二进制内容（.wav）。
        request: 请求上下文字典，至少含 "text" 字段用于历史预览。
        task_id: 任务 ID（new_task_id 生成），用作文件名前缀。
        persona_id: 关联音色 ID，无则 None。
        engine: 引擎标识，如 "voxcpm2" / "indextts2"。

    Returns:
        音频访问 URL 字符串，形如 "/api/audio/generated/{task_id}_{ts}.wav"

    Raises:
        PermissionError: 目标目录不可写时抛出，交由全局 error_handler 返回 500。
        OSError: 磁盘满等 IO 错误抛出。
    """
    timestamp: int = int(time.time() * 1000)
    filename: str = f"{task_id}_{timestamp}.wav"
    save_path: str = os.path.join(SAVE_DIR, filename)

    os.makedirs(SAVE_DIR, exist_ok=True)
    async with aiofiles.open(save_path, "wb") as f:
        await f.write(audio_bytes)

    text: str = request.get("text", "") if isinstance(request, dict) else ""
    duration_seconds: float = len(audio_bytes) / (44100 * 2 * 2)  # rough: 44.1kHz/16bit/stereo

    # S-R4: 首次调用时执行一次性 legacy 数据库迁移（幂等）
    _migrate_legacy_history_db_if_needed()

    db = get_history_db()
    db.insert(
        {
            "filename": filename,
            "filepath": save_path,
            "created_at": datetime.now().isoformat(),
            "file_size_bytes": len(audio_bytes),
            "duration_seconds": round(duration_seconds, 2),
            "text_preview": text[:100] if text else "",
            "engine": engine,
            "model_type": request.get("model_type") if isinstance(request, dict) else None,
            "model_size": request.get("model_size") if isinstance(request, dict) else None,
            "persona_name": persona_id,
            "output_format": "wav",
            "is_success": True,
            "error_msg": None,
        }
    )

    return f"/api/audio/generated/{filename}"


def build_generation_error_response(
    error: TTSError,
    task_id: str,
) -> JSONResponse:
    """构建统一的生成失败 JSON 响应结构。

    Args:
        error: 捕获到的 TTSError 派生异常（含 message / error_code）。
        task_id: 任务 ID，便于前端关联失败任务。

    Returns:
        JSONResponse: HTTP 500 / 4xx（根据异常类型动态取 status）
        {
            "status": "error",
            "task_id": str,
            "error": {
                "code": str,
                "message": str,
                "type": str,
            },
        }
    """
    error_type_name: str = type(error).__name__
    status_code: int = getattr(error, "status_code", 500)
    error_code: str = getattr(error, "error_code", error_type_name)
    message: str = str(error)

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "error",
            "task_id": task_id,
            "error": {
                "code": error_code,
                "message": message,
                "type": error_type_name,
            },
        },
    )


# ===========================================================================
# 信号量 / 并发控制
# ===========================================================================


async def _get_generation_semaphore(engine: str) -> asyncio.Semaphore:
    """Return the per-engine semaphore, creating it lazily if needed.

    Args:
        engine: 引擎名称（不区分大小写，默认 voxcpm2）。

    Returns:
        对应引擎的 asyncio.Semaphore 单例。
    """
    engine = (engine or "voxcpm2").lower()
    semaphore: asyncio.Semaphore | None = _generation_semaphores.get(engine)
    if semaphore is None:
        async with _generation_semaphore_lock:
            semaphore = _generation_semaphores.get(engine)
            if semaphore is None:
                semaphore = asyncio.Semaphore(_MAX_CONCURRENT_GENERATIONS)
                _generation_semaphores[engine] = semaphore
    return semaphore


# ===========================================================================
# S-R4: Legacy history.db 一次性迁移
# ===========================================================================


def _migrate_legacy_history_db_if_needed() -> None:
    """REFACTOR: [S-R4] 一次性迁移 legacy history.db 到统一位置。

    Root Cause:
        原 _get_history_db() 调用 create_history_db(SAVE_DIR) 创建独立实例，
        数据库路径为 {SAVE_DIR}/history.db (即 outputs/history.db)。
        而全局单例 get_history_db() 的路径为 {ROOT_DIR}/data/history.db。
        两个独立的 HistoryDatabase 实例操作不同的 SQLite 文件，导致：
        - 生成路由写入 outputs/history.db
        - 历史/音频页面读取 data/history.db
        - 用户看不到刚生成的历史记录（P0 数据一致性 Bug）

    Fix:
        统一使用 get_history_db() 全局单例。首次调用本函数时执行一次性迁移：
        1. 检查 legacy 路径是否存在 outputs/history.db
        2. 读取所有记录，用 insert_batch 合并到统一数据库（INSERT OR REPLACE 去重）
        3. 迁移成功后将 legacy 文件重命名为 .migrated_{timestamp}，防止再次迁移
        4. 幂等：已迁移则跳过；失败不阻塞应用启动（仅记录警告日志）

    Safety:
        - threading.Lock 保护，防止多线程并发触发
        - 双重检查 _legacy_history_migrated 标志位
        - 所有文件操作包裹 try/except，失败不影响主流程
    """
    global _legacy_history_migrated
    if _legacy_history_migrated:
        return

    with _legacy_migration_lock:
        if _legacy_history_migrated:
            return

        try:
            if not os.path.exists(_LEGACY_HISTORY_DB_PATH):
                _legacy_history_migrated = True
                return

            # 空文件直接重命名，避免后续无谓的读取
            try:
                if os.path.getsize(_LEGACY_HISTORY_DB_PATH) == 0:
                    try:
                        os.rename(_LEGACY_HISTORY_DB_PATH, f"{_LEGACY_HISTORY_DB_PATH}.empty")
                        logger.info("[S-R4] 检测到空的 legacy history.db，已重命名为 .empty")
                    except OSError as rename_err:
                        logger.debug(f"[S-R4] 重命名空 legacy 文件失败: {rename_err}")
                    _legacy_history_migrated = True
                    return
            except OSError as size_err:
                logger.debug(f"[S-R4] 获取 legacy 文件大小失败: {size_err}")
                _legacy_history_migrated = True
                return

            # 读取 legacy 数据库所有记录
            legacy_conn = sqlite3.connect(_LEGACY_HISTORY_DB_PATH)
            legacy_conn.row_factory = sqlite3.Row
            try:
                cursor = legacy_conn.execute("SELECT * FROM generation_history")
                rows = cursor.fetchall()
            except sqlite3.DatabaseError as read_err:
                logger.warning(f"[S-R4] 读取 legacy history.db 失败: {read_err}")
                _legacy_history_migrated = True
                return
            finally:
                with _suppress_os_errors():
                    legacy_conn.close()

            if not rows:
                # 空数据库（无记录），直接重命名
                try:
                    os.rename(_LEGACY_HISTORY_DB_PATH, f"{_LEGACY_HISTORY_DB_PATH}.migrated")
                    logger.info("[S-R4] legacy history.db 无记录，已重命名为 .migrated")
                except OSError as rename_err:
                    logger.debug(f"[S-R4] 重命名空 legacy 文件失败: {rename_err}")
                _legacy_history_migrated = True
                return

            # 转换为 dict 列表（sqlite3.Row 转 dict）
            records: list[dict[str, Any]] = [dict(row) for row in rows]

            # 写入目标数据库（get_history_db 全局单例）
            # insert_batch 内部使用 INSERT OR REPLACE，filepath UNIQUE 约束保证去重
            target_db = get_history_db()
            count: int = target_db.insert_batch(records)

            # 迁移成功，重命名旧文件（带时间戳防止冲突）
            migrated_path: str = f"{_LEGACY_HISTORY_DB_PATH}.migrated_{int(time.time())}"
            try:
                os.rename(_LEGACY_HISTORY_DB_PATH, migrated_path)
            except OSError as rename_err:
                logger.debug(f"[S-R4] 重命名 legacy 文件失败（迁移已完成）: {rename_err}")

            logger.info(
                f"[S-R4] 已迁移 {count} 条历史记录从 legacy history.db ({_LEGACY_HISTORY_DB_PATH}) 到统一数据库"
            )
        except Exception as migrate_err:  # noqa: BLE001
            # 任何异常都不阻塞应用启动
            logger.warning(f"[S-R4] 迁移 legacy history.db 失败: {migrate_err}（不影响应用启动）")
        finally:
            _legacy_history_migrated = True


class _suppress_os_errors:
    """E4: 上下文管理器，抑制 OSError（用于资源清理的 finally 块）。"""

    def __enter__(self) -> "_suppress_os_errors":
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


# ===========================================================================
# 引擎就绪校验 / 历史记录写入
# ===========================================================================


def _check_engine_ready(
    request: Any,
    engine_name: str | None = None,
) -> HTMLResponse | None:
    """检查当前引擎是否已加载，未就绪时返回 HTML 错误片段。

    Args:
        request: FastAPI 请求对象（用于访问 app.state.templates）。
        engine_name: 显式引擎名（None 则用 registry.current_engine）。

    Returns:
        未就绪时返回 HTMLResponse（400），就绪时返回 None。
    """
    from ...model_registry import registry

    if engine_name is None:
        engine_name = registry.current_engine
    if engine_name == "indextts2":
        if registry.indextts2_engine is None:
            return _error_html(
                request, "IndexTTS 2.0 模型未加载，请先加载模型", error_type="engine_not_ready", engine_id="indextts2"
            )
    else:
        if registry.voxcpm_model is None:
            return _error_html(
                request, "VoxCPM2 模型未加载，请先加载模型", error_type="engine_not_ready", engine_id="voxcpm2"
            )
    return None


def _record_to_history_db(
    filepath: str,
    text: str,
    engine: str,
    duration: float,
    model_type: str | None = None,
    model_size: str | None = None,
    persona_name: str | None = None,
    output_format: str = "wav",
    is_success: bool = True,
    error_msg: str | None = None,
) -> None:
    """将单次生成结果写入 history_db。

    Args:
        filepath: 音频绝对路径。
        text: 生成文本（前 100 字存入预览）。
        engine: 引擎标识。
        duration: 生成耗时（秒）。
        model_type: 模型类型（可选）。
        model_size: 模型大小标签（可选）。
        persona_name: 音色名称（可选）。
        output_format: 输出文件格式（默认 wav）。
        is_success: 本次是否生成成功。
        error_msg: 失败原因（仅 is_success=False 时使用）。
    """
    # S-R4: 首次调用时执行一次性 legacy 数据库迁移（幂等）
    _migrate_legacy_history_db_if_needed()

    try:
        # S-R4: 统一使用全局单例 get_history_db()，消除多实例路径不一致问题
        db = get_history_db()
        filename: str = os.path.basename(filepath) if filepath else ""
        file_size: int = os.path.getsize(filepath) if filepath and os.path.exists(filepath) else 0
        db.insert(
            {
                "filename": filename,
                "filepath": filepath or "",
                "created_at": datetime.now().isoformat(),
                "file_size_bytes": file_size,
                "duration_seconds": round(duration, 2),
                "text_preview": text[:100] if text else "",
                "engine": engine,
                "model_type": model_type,
                "model_size": model_size,
                "persona_name": persona_name,
                "output_format": output_format,
                "is_success": is_success,
                "error_msg": error_msg,
            }
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(f"历史记录数据库写入失败: {e}")


# ===========================================================================
# 错误/成功 HTML 片段 + 错误消息友好化
# ===========================================================================


def _safe_error_msg(exc: BaseException) -> str:
    """根据异常类型返回用户友好的错误消息。

    Args:
        exc: 已捕获的异常对象。

    Returns:
        用户可读的错误描述（中文，不超过 200 字符）。
    """
    if isinstance(exc, InsufficientVRAMError):
        return f"显存不足：{str(exc)}"
    if isinstance(exc, EngineSwitchError):
        return f"引擎切换失败：{str(exc)}"
    if isinstance(exc, TTSError):
        return str(exc)
    if isinstance(exc, RuntimeError):
        exc_str: str = str(exc)
        if "CUDA" in exc_str or "VRAM" in exc_str or "out of memory" in exc_str.lower():
            return "显存不足，请尝试缩短文本、关闭其他GPU程序，或在设置中切换到CPU模式"
        return f"运行时错误：{exc_str[:200]}"
    if isinstance(exc, ValueError):
        return f"参数错误：{str(exc)[:200]}"
    if isinstance(exc, FileNotFoundError):
        return "音频文件不存在或已被删除"
    if isinstance(exc, TimeoutError):
        return "请求超时，请稍后重试"
    if isinstance(exc, ConnectionError):
        return "网络连接异常，请检查网络"
    return "生成失败，请稍后重试"


def _partial_success_html(filename: str, message: str, degraded_note: str) -> HTMLResponse:
    """渲染"部分成功"HTML 片段（降级重试成功，但质量下降）。

    Args:
        filename: 保存后的音频文件名。
        message: 主成功提示。
        degraded_note: 降级说明（橙字提示）。

    Returns:
        HTMLResponse（200），含 audio 标签 + 状态消息。
    """
    safe_filename: str = quote(filename, safe="")
    return HTMLResponse(
        f'<div data-audio-filename="{html.escape(filename)}">'
        f'<audio class="tts-audio-hidden" src="/api/audio/{safe_filename}"></audio>'
        f'<div class="status-message success">{html.escape(message)}</div>'
        f'<div class="status-message warning" style="margin-top:8px;color:#f59e0b;">{html.escape(degraded_note)}</div>'
        f"</div>"
    )


def _log_generation(
    endpoint_name: str,
    text: str,
    engine: str,
    voice_or_persona: str,
    success: bool,
    duration: float,
    is_degraded: bool = False,
    error_msg: str | None = None,
) -> None:
    """记录生成操作日志（写入 health_monitor + operation_log）。

    Args:
        endpoint_name: 路由端点标识（如 clone/design/script）。
        text: 生成文本。
        engine: 引擎名称。
        voice_or_persona: 音色/角色名称。
        success: 是否成功。
        duration: 总耗时（秒）。
        is_degraded: 是否降级后成功（OOM 重试）。
        error_msg: 失败错误信息（仅 success=False）。
    """
    if success:
        increment_generation(success=True)
        details: dict[str, Any] = {
            "endpoint": endpoint_name,
            "engine": engine,
            "voice_persona": voice_or_persona,
            "text_length": len(text),
            "duration": round(duration, 2),
        }
        if is_degraded:
            details["degraded"] = True
        log_operation("generation", f"{endpoint_name} success ({duration:.1f}s)", details)
    else:
        increment_generation(success=False)
        details = {
            "endpoint": endpoint_name,
            "engine": engine,
            "voice_persona": voice_or_persona,
            "text_length": len(text),
            "duration": round(duration, 2),
        }
        if error_msg:
            details["error"] = str(error_msg)
        log_operation("generation", f"{endpoint_name} failed ({duration:.1f}s)", details)


# ===========================================================================
# 音频后处理（语速 / 增强 / 响度归一化）
# ===========================================================================


def _apply_post_processing_to_file(
    filename: str,
    tempo_factor: float,
    voice_enhancement: str | bool,
    target_lufs: float,
) -> str:
    """对已保存的音频文件应用后处理，输出为 *_pp.wav。

    Args:
        filename: 原始文件名（相对 SAVE_DIR，或绝对路径）。
        tempo_factor: 语速因子（1.0 不变，>1 更快，<1 更慢）。
        voice_enhancement: 是否启用语音增强（str "true"/"false" 或 bool）。
        target_lufs: 响度归一化目标 LUFS（默认 -16.0）。

    Returns:
        处理后的新文件名；无需处理则返回原文件名。
    """
    if tempo_factor == 1.0 and not voice_enhancement and target_lufs == -16.0:
        return filename

    from scipy.io import wavfile

    audio_path: str = filename if os.path.isabs(filename) else os.path.join(SAVE_DIR, filename)
    if not os.path.isfile(audio_path):
        logger.warning(f"后处理: 音频文件未找到: {audio_path}")
        return filename

    try:
        sr, data = wavfile.read(audio_path)
        if data.dtype == np.int16:
            audio: np.ndarray = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            audio = data.astype(np.float32) / 2147483648.0
        elif data.dtype == np.float32:
            audio = data.copy()
        else:
            audio = data.astype(np.float32)

        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        processed: np.ndarray = enhance_audio(
            audio,
            sr,
            normalize=True,
            tempo_factor=tempo_factor,
            voice_enhancement=voice_enhancement
            if isinstance(voice_enhancement, bool)
            else _parse_bool_form(voice_enhancement),
            target_lufs=target_lufs,
        )

        base, ext = os.path.splitext(filename)
        new_filename: str = f"{base}_pp{ext}"
        new_path: str = new_filename if os.path.isabs(new_filename) else os.path.join(SAVE_DIR, new_filename)

        output: np.ndarray = (processed * 32768.0).clip(-32768, 32767).astype(np.int16)
        wavfile.write(new_path, sr, output)

        logger.info(f"后处理已应用: {filename} -> {new_filename}")
        return new_filename
    except Exception as e:  # noqa: BLE001
        logger.error(f"后处理失败 {filename}: {e}")
        return filename


def _error_html(
    request: Any,
    error_message: str,
    error_type: str = "general",
    engine_id: str = "",
) -> HTMLResponse:
    """渲染 HTML 错误片段；优先使用 Jinja2 模板，模板不可用时降级返回安全字符串。

    Args:
        request: FastAPI 请求（用于访问 app.state.templates）。
        error_message: 错误提示文本。
        error_type: 错误分类（general / oom / validation / engine_not_ready）。
        engine_id: 引擎 ID（engine_not_ready 时渲染加载按钮）。

    Returns:
        HTMLResponse（400），携带 HX-Trigger toast 头。
    """
    try:
        templates = request.app.state.templates
        from ...i18n import get_lang

        return templates.TemplateResponse(
            request=request,
            name="partials/error_message.html",
            context={
                "lang": get_lang(request),
                "error_message": error_message,
                "error_type": error_type,
                "engine_id": engine_id,
            },
            status_code=400,
            headers={
                "HX-Trigger": json.dumps(
                    {"tts-toast": {"type": "error", "message": html.escape(error_message)}},
                    ensure_ascii=False,
                )
            },
        )
    except Exception:  # noqa: BLE001
        # 极端降级：仍保证 HTML 转义，防 XSS
        load_btn: str = ""
        if error_type == "engine_not_ready" and engine_id:
            load_btn = (
                f'<button type="button" onclick="window.switchModel(\'{html.escape(engine_id)}\')" '
                f'style="margin-top:8px;padding:4px 12px;border-radius:4px;background:var(--p500);'
                f'color:#fff;border:none;cursor:pointer;font-size:12px">加载模型</button>'
            )
        return HTMLResponse(
            f'<div class="tts-error-block" data-error-type="{html.escape(error_type)}">'
            f'<div class="error-title">生成失败</div>'
            f'<div class="error-message">{html.escape(error_message)}</div>'
            f"{load_btn}"
            f"</div>",
            status_code=400,
        )


# ===========================================================================
# 上传辅助 / 音色解析 / 输入校验
# ===========================================================================


async def save_uploaded_audio(
    request: Any,
    upload_file: UploadFile | None,
    upload_dir: str | None = None,
    max_size_mb: int = 25,
) -> tuple[str | None, HTMLResponse | None]:
    """保存上传的音频文件，返回 (path, None) 或 (None, error_html)。

    Args:
        request: FastAPI 请求（用于渲染错误 HTML）。
        upload_file: FastAPI UploadFile（可为 None）。
        upload_dir: 保存目录；默认 {SAVE_DIR}/uploads。
        max_size_mb: 最大文件大小 MB（仅用于错误消息显示；硬限制仍以 MAX_UPLOAD_SIZE_BYTES 为准）。

    Returns:
        成功: (绝对保存路径, None)
        失败: (None, HTMLResponse 400)
    """
    if not upload_file or not upload_file.filename:
        return None, None

    if upload_dir is None:
        upload_dir = os.path.join(SAVE_DIR, "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    safe_name: str = os.path.basename(upload_file.filename)
    _, ext = os.path.splitext(safe_name)
    if ext.lower() not in ALLOWED_AUDIO_EXTENSIONS:
        return None, _error_html(request, f"不支持的音频格式: {ext}")

    upload_path: str = os.path.join(upload_dir, f"{int(time.time())}_{safe_name}")
    content: bytes = await upload_file.read()
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        return None, _error_html(request, f"上传文件大小超过 {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)}MB 限制")

    async with aiofiles.open(upload_path, "wb") as f:
        await f.write(content)

    return upload_path, None


async def resolve_persona_ref(
    request: Any,
    persona_name: str | None,
) -> tuple[str | None, HTMLResponse | None]:
    """将 Persona 名称解析为参考音频路径。

    Args:
        request: FastAPI 请求（用于渲染错误 HTML）。
        persona_name: Persona 音色名称（basename）。

    Returns:
        成功: (wav_path, None)
        失败/不存在: (None, HTMLResponse 400)
    """
    if not persona_name:
        return None, None

    from ...persona_manager import load_persona_embedding

    safe_name: str = os.path.basename(persona_name)
    persona_data: Any | None = load_persona_embedding(safe_name)
    if persona_data is not None:
        wav_path, ref_text = persona_data
        if wav_path and os.path.isfile(wav_path):
            return wav_path, None
        else:
            return None, _error_html(request, f"音色文件不存在: {safe_name}")
    else:
        return None, _error_html(request, f"音色不存在: {safe_name}")


def pre_validate(
    request: Any,
    engine_name: str | None,
    text: str | None,
    max_length: int | None = None,
) -> HTMLResponse | None:
    """生成前预校验：引擎就绪 + 文本非空 + 长度限制。

    Args:
        request: FastAPI 请求。
        engine_name: 引擎名（None 用当前引擎）。
        text: 生成文本。
        max_length: 最大字符数（None 则不限制）。

    Returns:
        校验失败返回 HTMLResponse，成功返回 None。
    """
    model_not_ready: HTMLResponse | None = _check_engine_ready(request, engine_name)
    if model_not_ready:
        return model_not_ready
    if not text or not text.strip():
        return _error_html(request, "文本不能为空")
    if max_length and len(text) > max_length:
        return _error_html(request, f"文本长度超过限制（最大 {max_length} 字符）")
    return None


def _success_html(filename: str, status_message: str) -> HTMLResponse:
    """渲染成功 HTML 片段（audio + 成功状态）。

    Args:
        filename: 音频文件名。
        status_message: 成功提示文本。

    Returns:
        HTMLResponse（200）。
    """
    safe_filename: str = quote(filename, safe="")
    return HTMLResponse(
        f'<div data-audio-filename="{html.escape(filename)}">'
        f'<audio class="tts-audio-hidden" src="/api/audio/{safe_filename}"></audio>'
        f'<div class="status-message success">{html.escape(status_message)}</div>'
        f"</div>"
    )


# ===========================================================================
# OOM 降级重试
# ===========================================================================


def _run_with_oom_retry(
    run_fn: Any,
    endpoint_name: str,
    degraded_fn: Any | None = None,
    max_retries: int = 2,
) -> tuple[Any, str]:
    """执行生成函数；OOM 时自动清理显存并使用降级参数重试。

    Args:
        run_fn: 原始生成可调用，返回 (result, msg)。
        endpoint_name: 端点名称（用于日志）。
        degraded_fn: 降级生成可调用（相同签名）；None 则复用 run_fn。
        max_retries: 最大降级重试次数。

    Returns:
        (result, msg[, degraded_note]) 三元组（degraded_note 为 None 表示未降级）

    Raises:
        RuntimeError: 多次重试仍 OOM 时抛出友好中文错误。
        其他异常：非 OOM 异常原样抛出。
    """
    _generation_retry_counter["total"] += 1
    degraded_note: str | None = None
    retry_count: int = 0

    try:
        result, msg = run_fn()
        return result, msg, degraded_note  # type: ignore[return-value]
    except Exception as e:  # noqa: BLE001
        if not is_oom_error(e):
            logger.error(f"{endpoint_name} failed (non-OOM): {e}")
            raise

        logger.warning(f"{endpoint_name} hit OOM, attempting degraded retry...")
        _generation_retry_counter["oom_retries"] += 1
        free_gpu_memory()

        while retry_count < max_retries:
            retry_count += 1
            try:
                degraded_note = "由于显存限制，已自动降低生成质量参数以完成生成。"
                if degraded_fn:
                    result, msg = degraded_fn()
                else:
                    result, msg = run_fn()
                return result, msg, degraded_note  # type: ignore[return-value]
            except Exception as retry_e:  # noqa: BLE001
                if not is_oom_error(retry_e):
                    raise
                logger.warning(f"{endpoint_name} OOM retry {retry_count}/{max_retries} failed")
                free_gpu_memory()

        raise RuntimeError(
            "显存不足，已尝试降级重试但仍失败。请尝试缩短文本、关闭其他GPU程序，或在设置中切换到CPU模式"
        ) from None


def _parse_bool_form(value: Any) -> bool:
    """解析表单 bool 值（兼容 "true"/"1"/"yes" 字符串）。

    Args:
        value: 表单原始值（str / bool / int）。

    Returns:
        bool 解析结果。
    """
    return str(value).lower() in ("true", "1", "yes")


def _merge_dialect(instruction: str, dialect: str) -> str:
    """将方言标签合并到指令文本前。

    Args:
        instruction: 原指令文本。
        dialect: 方言名称（需在 _DIALECT_NAMES 白名单内）。

    Returns:
        合并后的指令；方言不在白名单内则原样返回 instruction。
    """
    if dialect and dialect in _DIALECT_NAMES:
        return (dialect + "，" + instruction) if instruction.strip() else dialect
    return instruction


# ===========================================================================
# 生成执行主流程（信号量 + 硬超时 + OOM 重试 + 历史记录）
# ===========================================================================


async def _execute_generation(
    request: Any,
    text: str,
    run_fn: Any,
    endpoint_name: str,
    voice_or_persona: str = "",
    model_type: str = "",
    engine: str = "voxcpm2",
    tempo_factor: float = 1.0,
    voice_enhancement: str = "false",
    target_lufs: float = -16.0,
    oom_retry: bool = True,
    degraded_fn: Any | None = None,
) -> HTMLResponse:
    """生成执行入口：获取 per-engine 信号量 → 加硬超时 → 调用实现函数。

    Args:
        request: FastAPI 请求对象。
        text: 生成文本。
        run_fn: 生成可调用（同步）。
        endpoint_name: 端点名称。
        voice_or_persona: 音色/角色名。
        model_type: 模型类型标签。
        engine: 引擎名。
        tempo_factor: 语速因子。
        voice_enhancement: 是否启用增强（"true"/"false" 字符串）。
        target_lufs: 目标响度 LUFS。
        oom_retry: 是否启用 OOM 降级重试。
        degraded_fn: 降级参数下的生成可调用。

    Returns:
        HTMLResponse（成功 200 / 失败 400）。
    """
    semaphore: asyncio.Semaphore = await _get_generation_semaphore(engine)
    try:
        await asyncio.wait_for(
            semaphore.acquire(),
            timeout=_SEMAPHORE_ACQUIRE_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return _error_html(request, "系统繁忙，请稍后再试（等待超时）")
    try:
        # E6-1 ROBUSTNESS: 为生成任务本身加硬超时，防止超长文本/死循环耗尽信号量池。
        # 注意：底层 torch 推理不响应 asyncio 取消，但 run_in_executor 的 Future
        # 可被 wait_for 取消（线程仍会跑完，但 HTTP 客户端会立即收到超时响应，
        # 信号量也会被释放，避免请求堆积）。
        return await asyncio.wait_for(
            _execute_generation_impl(
                request,
                text,
                run_fn,
                endpoint_name,
                voice_or_persona,
                model_type,
                engine,
                tempo_factor,
                voice_enhancement,
                target_lufs,
                oom_retry,
                degraded_fn,
            ),
            timeout=_GENERATION_HARD_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.error(f"{endpoint_name} 生成超时 (>{_GENERATION_HARD_TIMEOUT_S}s)，文本长度={len(text)}")
        _log_generation(
            endpoint_name,
            text,
            engine,
            voice_or_persona,
            False,
            _GENERATION_HARD_TIMEOUT_S,
            error_msg=f"generation timeout (>{_GENERATION_HARD_TIMEOUT_S}s)",
        )
        return _error_html(request, f"生成超时（超过 {_GENERATION_HARD_TIMEOUT_S:.0f} 秒），请尝试缩短文本或减少并发")
    finally:
        semaphore.release()


async def _execute_generation_impl(
    request: Any,
    text: str,
    run_fn: Any,
    endpoint_name: str,
    voice_or_persona: str = "",
    model_type: str = "",
    engine: str = "voxcpm2",
    tempo_factor: float = 1.0,
    voice_enhancement: str = "false",
    target_lufs: float = -16.0,
    oom_retry: bool = True,
    degraded_fn: Any | None = None,
) -> HTMLResponse:
    """生成核心实现：线程池执行同步 run_fn → 记录历史 → 后处理 → 返回 HTML。

    Args:
        同 _execute_generation。

    Returns:
        HTMLResponse（成功 / OOM 降级成功 / 失败）。
    """
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    start_time: float = time.monotonic()
    try:
        if oom_retry:
            result, msg, degraded_note = await loop.run_in_executor(
                None, lambda: _run_with_oom_retry(run_fn, endpoint_name, degraded_fn=degraded_fn)
            )
        else:
            result, msg = await loop.run_in_executor(None, run_fn)
            degraded_note = None
        duration: float = time.monotonic() - start_time
        if result is None:
            _log_generation(endpoint_name, text, engine, voice_or_persona, False, duration, error_msg=msg)
            return _error_html(request, msg)
        is_degraded: bool = degraded_note is not None
        _log_generation(endpoint_name, text, engine, voice_or_persona, True, duration, is_degraded=is_degraded)
        _time_estimator.record(len(text), duration, engine, segment_count=1)
        if isinstance(result, tuple) and len(result) >= 3:
            audio_path: str = result[2] if os.path.isabs(result[2]) else os.path.join(SAVE_DIR, result[2])
            await asyncio.to_thread(
                _record_to_history_db,
                filepath=audio_path,
                text=text,
                engine=engine,
                duration=duration,
                model_type=model_type,
                output_format="wav",
                is_success=True,
            )
        monitor = get_health_monitor()
        monitor.record_generation(success=True)
        filename: str = result[2]
        pp_voice_enhancement: bool = _parse_bool_form(voice_enhancement)
        filename = await asyncio.to_thread(
            _apply_post_processing_to_file, filename, tempo_factor, pp_voice_enhancement, target_lufs
        )
        if degraded_note:
            return _partial_success_html(filename, msg, degraded_note)
        return _success_html(filename, msg)
    except Exception as e:  # noqa: BLE001
        duration = time.monotonic() - start_time
        logger.error(f"{endpoint_name} generation failed: {e}")
        _log_generation(endpoint_name, text, engine, voice_or_persona, False, duration, error_msg=str(e))
        error_type: str = "general"
        if is_oom_error(e):
            error_type = "oom"
        elif isinstance(e, ValueError):
            error_type = "validation"
        return _error_html(request, _safe_error_msg(e), error_type=error_type)


# ===========================================================================
# 共享工具：音频上传验证 / 文本验证 / 参考音频加载
# ===========================================================================

# 支持的音频格式（与 ALLOWED_AUDIO_EXTENSIONS 保持一致）
SUPPORTED_AUDIO_FORMATS: set = ALLOWED_AUDIO_EXTENSIONS
MAX_AUDIO_SIZE_MB: int = 50  # 最大音频文件大小（MB）
MAX_TEXT_LENGTH_DEFAULT: int = 5000  # 默认最大文本长度


async def validate_audio_upload(
    file: UploadFile,
    max_size_mb: int = MAX_AUDIO_SIZE_MB,
    supported_formats: set = SUPPORTED_AUDIO_FORMATS,
) -> tuple[bool, str]:
    """验证上传的音频文件扩展名和大小。

    Args:
        file: FastAPI UploadFile。
        max_size_mb: 最大文件大小（MB）。
        supported_formats: 允许的扩展名集合。

    Returns:
        (is_valid, error_message)：error_message 为空串表示验证通过。
    """
    if not file or not file.filename:
        return False, "未选择音频文件"

    ext: str = os.path.splitext(file.filename)[1].lower()
    if ext not in supported_formats:
        return False, f"不支持的音频格式: {ext}，支持: {', '.join(sorted(supported_formats))}"

    try:
        content: bytes = await file.read()
        await file.seek(0)
        size_mb: float = len(content) / (1024 * 1024)
        if size_mb > max_size_mb:
            return False, f"音频文件过大: {size_mb:.1f}MB，最大支持: {max_size_mb}MB"
    except (OSError, ValueError) as read_err:
        logger.warning(f"读取音频文件失败: {read_err}")
        return False, f"读取音频文件失败: {read_err}"

    return True, ""


def validate_text_input(
    text: str,
    max_length: int = MAX_TEXT_LENGTH_DEFAULT,
    field_name: str = "文本",
) -> tuple[bool, str]:
    """验证文本输入非空且长度合法。

    Args:
        text: 原始文本。
        max_length: 最大字符数。
        field_name: 错误消息中显示的字段名（默认"文本"）。

    Returns:
        (is_valid, error_message)。
    """
    if not text or not text.strip():
        return False, f"请输入{field_name}"

    if len(text) > max_length:
        return False, f"{field_name}过长: {len(text)}字，最大支持: {max_length}字"

    return True, ""


async def load_reference_audio(
    request: Any,
    file: UploadFile,
    output_dir: str,
    prefix: str = "ref",
) -> tuple[str | None, str]:
    """校验并保存参考音频文件到指定目录。

    Args:
        request: FastAPI 请求（当前未直接使用，保留以对齐签名）。
        file: FastAPI UploadFile。
        output_dir: 目标输出目录。
        prefix: 文件名前缀（默认 "ref"）。

    Returns:
        成功: (绝对路径, "")
        失败: (None, 错误信息)
    """
    is_valid, error = await validate_audio_upload(file)
    if not is_valid:
        return None, error

    try:
        content: bytes = await file.read()
        filename: str = f"{prefix}_{file.filename}"
        filepath: str = os.path.join(output_dir, filename)

        os.makedirs(output_dir, exist_ok=True)
        async with aiofiles.open(filepath, "wb") as f:
            await f.write(content)

        return filepath, ""
    except (OSError, PermissionError) as save_err:
        logger.error(f"保存参考音频失败: {save_err}")
        return None, f"保存参考音频失败: {save_err}"
