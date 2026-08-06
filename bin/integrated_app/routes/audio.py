"""统一音频文件服务与历史记录管理 API 路由模块。

架构说明：
    本模块提供三类音频的安全静态文件服务 + 历史记录批量操作：

    ① 生成结果音频：GET ``/api/audio/{filename}`` → ``SAVE_DIR/*.wav``
       （历史记录列表页播放；向后兼容 100%，不改为 generated 子路径）
    ② Persona 参考音频：GET ``/api/persona/audio/{name}`` → ``PERSONA_DIR/*.wav``
       （音色列表预览与克隆 Tab 参考）
    ③ 说话人样本音频：GET ``/api/speaker/sample/{key}`` → ``samples/parity/*.wav``
       （嵌入计算参考用的预置样本）

    此外还包含音频上传、历史记录的查询/批量删除/隐藏/显示/导出 ZIP/同步等端点。

路径前缀：
    由 ``APIRouter(prefix="/api", tags=["audio"])`` 注册，因此实际路径
    形如 ``/api/audio/...``、``/api/history/...``（为保持向后兼容 100%，
    **不修改** 现有路径结构，即使规范文档中提到的 ``/api/audio/generated``
    与 ``/api/audio/persona`` 也保持为历史路径）。

三重路径安全（硬约束）：
    所有基于用户输入拼接文件路径的场景均执行：
    1. 正则白名单 ``^[A-Za-z0-9._-]+$`` 拒绝 ``..`` ``/`` ``\\`` ``:`` 等非法字符
    2. ``os.path.realpath`` 校验最终路径必须在 ``root_dir`` 前缀下（防 symlink 攻击）
    3. 使用 ``Path(root_dir) / filename`` **强制拼接**，忽略用户输入的任何目录段

权限 / CSRF：
    所有 GET 只读端点无需认证；所有 POST state-changing 端点（上传、批量
    删除/隐藏/显示/清空/同步/导出）由 ``CSRFMiddleware`` 统一校验
    ``X-CSRF-Token`` 头，本路由层不二次校验。
"""

import glob
import io
import logging
import os
import re
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from ..config import MAX_UPLOAD_SIZE_BYTES as MAX_UPLOAD_SIZE
from ..config import PERSONA_DIR, PROJECT_ROOT, SAVE_DIR
from ..history_db import get_history_db
from .generate.utils import ALLOWED_AUDIO_EXTENSIONS

router = APIRouter(prefix="/api", tags=["audio"])

logger = logging.getLogger("tts_multimodel.audio_routes")

# ---- 常量 ----------------------------------------------------------------
_CHUNK_SIZE = 1024 * 1024  # 1 MB 流式读写块
_CACHE_AUDIO_HEADER = "public, max-age=3600"  # 用户生成音频: 1 小时
_CACHE_STATIC_HEADER = "public, max-age=86400, immutable"  # 静态样本: 1 天
_AUDIO_ACCEPT_RANGES = "bytes"

_MAX_BATCH_EXPORT_COUNT = 100
_MAX_BATCH_OPERATION_COUNT = 500
_KEYWORD_MAX_LENGTH = 100

# 非法字符正则：只允许字母数字、点、下划线、短横线
_SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_SPEAKER_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

_AUDIO_MAGIC_BYTES = {
    b"RIFF": ".wav",
    b"ID3": ".mp3",
    b"\xff\xfb": ".mp3",
    b"\xff\xf3": ".mp3",
    b"\xff\xf2": ".mp3",
    b"fLaC": ".flac",
    b"OggS": ".ogg",
    b"\x00\x00\x00": ".m4a",
}


# ---------------------------------------------------------------------------
# 路径安全：三重校验
# ---------------------------------------------------------------------------


def _build_content_disposition(filename: str, disposition: str = "attachment") -> str:
    """构建 RFC 5987 兼容的 Content-Disposition 响应头。

    支持中文/Unicode 文件名，兼容主流浏览器（Chrome/Firefox/Edge/Safari）。

    策略（双编码回退）：
      1. filename 参数使用 ASCII 回退（用下划线替换非 ASCII 字符或使用 URL 编码）
      2. filename* 参数使用 RFC 5987 编码（UTF-8 + percent-encoding）
      3. 现代浏览器优先使用 filename*，旧浏览器回退到 filename

    参考：
      - RFC 5987: https://tools.ietf.org/html/rfc5987
      - RFC 6266: https://tools.ietf.org/html/rfc6266

    Args:
        filename: 文件名（可包含中文/Unicode 字符）
        disposition: "attachment" 或 "inline"

    Returns:
        完整的 Content-Disposition 头值
    """
    # 检查文件名是否包含非 ASCII 字符
    ascii_safe = all(ord(c) < 128 for c in filename)

    if ascii_safe:
        # 纯 ASCII 文件名，直接使用简单格式
        return f'{disposition}; filename="{filename}"'

    # 非 ASCII 文件名：使用 RFC 5987 编码
    # 1. ASCII 回退：替换非 ASCII 字符为下划线
    ascii_fallback = "".join(c if ord(c) < 128 else "_" for c in filename)
    # 确保回退文件名有扩展名
    if "." in filename and "." not in ascii_fallback:
        ext = filename.rsplit(".", 1)[-1]
        ascii_fallback = f"audio.{ext}"

    # 2. RFC 5987 编码：UTF-8 + percent-encoding
    # 注意：RFC 5987 要求使用 RFC 3986 的 percent-encoding
    encoded_name = urllib.parse.quote(filename, safe="!#$&+-.^_`|~")
    rfc5987_name = f"UTF-8''{encoded_name}"

    # 返回双编码格式，浏览器会优先选择支持的编码
    return f'{disposition}; filename="{ascii_fallback}"; filename*={rfc5987_name}'


def _safe_file_path(root_dir: Path, user_input: str) -> Path:
    """对用户输入的文件名执行三重安全校验，返回安全的绝对路径。

    Security：
        ① 正则白名单字符（最外层，快速拦截）
        ② realpath 前缀校验（防 symlink 指向根目录外）
        ③ 强制 root_dir / filename 拼接（无论输入怎么写，都必须在 root 下）

    Args:
        root_dir:   只允许访问的根目录（绝对路径）。
        user_input: 用户输入的文件名或片段（不允许包含路径分隔符）。

    Returns:
        安全拼接后的 ``Path`` 对象（绝对路径）。

    Raises:
        HTTPException 403: 任一步校验失败时抛出，附带"非法路径"消息。
    """
    if not isinstance(user_input, str) or not user_input:
        raise HTTPException(status_code=403, detail="非法路径：空文件名")

    # ① 正则白名单（先 cheap 地拦截绝大多数攻击向量）
    if not _SAFE_NAME_PATTERN.match(user_input):
        logger.warning("_safe_file_path 正则校验失败 (安全审计): input=%r", user_input[:120])
        raise HTTPException(status_code=403, detail="非法路径")

    # ③ 强制拼接（不管输入，必须在 root_dir 下；即使通过了①②仍强制走此路径）
    candidate = Path(root_dir) / user_input
    candidate_abs = candidate.resolve()

    # ② realpath 前缀检查（防御 symlink 攻击）
    root_abs = Path(root_dir).resolve()
    try:
        candidate_abs.relative_to(root_abs)
    except ValueError:
        logger.warning(
            "_safe_file_path realpath 越界 (安全审计): root=%s candidate=%s",
            root_abs,
            candidate_abs,
        )
        raise HTTPException(status_code=403, detail="非法路径") from None

    return candidate_abs


# ---------------------------------------------------------------------------
# 上传 / 校验辅助
# ---------------------------------------------------------------------------


def _sync_history_incremental() -> None:
    """后台任务：仅同步比上次水位线新的文件（增量）。"""
    try:
        history_manager = get_history_db()
        before = history_manager.last_sync_mtime
        history_manager.sync_from_filesystem(since_mtime=before)
        logger.info("[历史同步] 增量同步完成，水位线: %.3f -> %.3f", before, history_manager.last_sync_mtime)
    except Exception as exc:  # noqa: BLE001
        logger.error("[历史同步] 后台增量同步失败: %s", exc, exc_info=True)


def _validate_audio_content(content: bytes, claimed_ext: str) -> bool:
    """根据魔数签名验证音频文件内容是否匹配声明的扩展名。

    lenient fallback：无法判定格式时返回 True；仅在确定检测到的格式与声
    明扩展名不匹配时返回 False。

    Args:
        content:     文件首 16 字节以上内容。
        claimed_ext: 声明扩展名（含前导点，如 ``.wav``）。

    Returns:
        True 表示允许上传，False 表示拒绝。
    """
    header = content[:16]
    if len(header) < 4:
        logger.warning("音频文件过短，无法验证魔数签名，允许上传")
        return True

    if header[:3] == b"\x00\x00\x00" and len(header) >= 8 and header[4:8] == b"ftyp":
        return claimed_ext in {".m4a", ".mp4"}

    detected_ext: str | None = None
    for magic, ext in _AUDIO_MAGIC_BYTES.items():
        if magic == b"\x00\x00\x00":
            continue
        if header[: len(magic)] == magic:
            detected_ext = ext
            break

    if detected_ext is None:
        logger.warning("无法通过魔数签名确定音频格式（声明扩展名 '%s'），允许上传", claimed_ext)
        return True

    return detected_ext == claimed_ext


async def _stream_upload_to_disk(file: UploadFile, dest_path: str) -> tuple[bool, str, bytes]:
    """流式分块读取上传文件，实时校验并累加字节数。

    Returns:
        (success, error_message, header_bytes) — header_bytes 用于后续魔数校验。
    """
    total = 0
    header_buffer = bytearray()
    header_collected = False
    try:
        async with aiofiles.open(dest_path, "wb") as f:
            while True:
                chunk = await file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_SIZE:
                    await f.close()
                    try:
                        os.remove(dest_path)
                    except OSError as exc:
                        logger.debug("清理超限上传文件失败: %s", exc)
                    return False, f"File too large. Maximum size is {MAX_UPLOAD_SIZE // (1024 * 1024)}MB", b""
                await f.write(chunk)
                if not header_collected:
                    header_buffer.extend(chunk)
                    if len(header_buffer) >= 16:
                        header_collected = True
        return True, "", bytes(header_buffer[:16])
    except OSError as exc:
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except OSError as rm_exc:
            logger.debug("清理写入失败文件失败: %s", rm_exc)
        return False, f"写入文件失败: {exc}", b""


def _validate_ids(ids: Any, max_count: int = _MAX_BATCH_OPERATION_COUNT) -> tuple[list, str | None]:
    """统一校验批量操作端点的 ``ids`` 参数。

    规则：list 类型、非空、长度 ≤ max_count、元素均为 int。

    Returns:
        (valid_ids, error_message) — 成功时 error_message 为 None。
    """
    if not isinstance(ids, list):
        return [], "ids 必须是数组"
    if not ids:
        return [], "未选择记录"
    if len(ids) > max_count:
        return [], f"单次操作最多 {max_count} 条记录"
    if not all(isinstance(i, int) for i in ids):
        return [], "ids 必须是整数数组"
    return ids, None


# ---------------------------------------------------------------------------
# Why FileResponse 而非简单 StaticFiles 挂载：
#   StaticFiles 中间件是纯静态匹配，无法执行 per-request 的鉴权、
#   三重路径安全校验、history_db 下载次数统计、以及 HTTP Range 字节
#   支持（Audio 播放器 seek 依赖 Accept-Ranges + Range 请求）。改用
#   显式动态路由可以串联上述所有能力，同时利用 FileResponse 的
#   零拷贝 sendfile 机制保持性能。
# ---------------------------------------------------------------------------


def _serve_safe_file(
    fs_path: Path, media_type: str, download_name: str, cache_header: str, disposition: str = "inline"
) -> Response:
    """通用安全文件服务：处理 FileNotFoundError / PermissionError 统一响应。

    Args:
        fs_path:       经 _safe_file_path 校验后的绝对路径。
        media_type:    Content-Type，如 ``audio/wav``。
        download_name: Content-Disposition 使用的文件名（支持中文，自动 RFC 5987 编码）。
        cache_header:  Cache-Control 响应头值。
        disposition:   "inline"（浏览器内播放）或 "attachment"（强制下载）。

    Returns:
        FileResponse / JSONResponse (404, 500)。
    """
    try:
        if not fs_path.is_file():
            return JSONResponse(
                {"status": "error", "message": "音频文件不存在或已被清理"},
                status_code=404,
            )
        headers = {
            "Cache-Control": cache_header,
            "Accept-Ranges": _AUDIO_ACCEPT_RANGES,
            "Content-Disposition": _build_content_disposition(download_name, disposition),
        }
        return FileResponse(str(fs_path), media_type=media_type, headers=headers)
    except FileNotFoundError:
        logger.info("音频文件不存在: %s", fs_path)
        return JSONResponse(
            {"status": "error", "message": "音频文件不存在或已被清理"},
            status_code=404,
        )
    except PermissionError as exc:
        logger.error("音频文件无读取权限: %s (%s)", fs_path, exc)
        return JSONResponse(
            {"status": "error", "message": "音频文件无读取权限，请联系管理员"},
            status_code=500,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("读取音频文件异常: %s", exc, exc_info=True)
        return JSONResponse(
            {"status": "error", "message": "音频读取失败"},
            status_code=500,
        )


@router.get("/audio/{filename}", summary="获取音频", description="获取生成的音频文件（历史记录播放）")
async def get_generated_audio(filename: str) -> Response:
    """获取生成结果音频（GET ``/api/audio/{filename}``）。

    Args:
        filename: 音频文件名（通常形如 ``gen_xxx.wav``），由
            ``_safe_file_path`` 做三重校验，防路径遍历。

    Returns:
        ``FileResponse`` → ``SAVE_DIR/{filename}``；
        403 非法路径 / 404 不存在 / 500 权限错误。
    """
    try:
        safe_path = _safe_file_path(SAVE_DIR, filename)
    except HTTPException:
        raise

    ext = os.path.splitext(filename)[1].lower()
    media_type = "audio/mpeg" if ext == ".mp3" else "audio/wav"
    return _serve_safe_file(safe_path, media_type, filename, _CACHE_AUDIO_HEADER)


@router.get("/persona/audio/{name}", summary="音色音频", description="获取指定音色的参考音频")
async def get_persona_audio(name: str) -> Response:
    """获取 Persona 参考音频（GET ``/api/persona/audio/{name}``）。

    注：规范中的 ``/api/audio/persona/{persona_id}/{filename}`` 与当前
    实现不一致，为向后兼容 100% **保持现有路径**。

    Args:
        name: Persona 名称（无扩展名）；自动拼 ``{name}.wav``。

    Returns:
        ``FileResponse`` → ``PERSONA_DIR/{name}.wav``；
        403 / 404 / 500。
    """
    # 先对 name 本身做字符白名单；再拼 .wav 传给 _safe_file_path
    if not isinstance(name, str) or not _SPEAKER_KEY_PATTERN.match(name):
        logger.warning("get_persona_audio name 非法字符: %r", name[:120])
        return JSONResponse({"status": "error", "message": "Invalid persona name"}, status_code=403)

    try:
        safe_path = _safe_file_path(PERSONA_DIR, f"{name}.wav")
    except HTTPException:
        raise
    return _serve_safe_file(safe_path, "audio/wav", f"{name}.wav", _CACHE_AUDIO_HEADER)


@router.get("/speaker/sample/{key}", summary="说话人样本", description="获取预置说话人的样本音频")
async def get_speaker_sample(key: str) -> Response:
    """获取预置说话人样本音频（GET ``/api/speaker/sample/{key}``）。

    注：规范中的 ``/api/audio/sample/{name}`` 与当前实现不一致，
    为向后兼容 100% **保持现有路径**。

    Args:
        key: 说话人样本 key（仅字母数字 + _ + -）。

    Returns:
        ``FileResponse`` → ``samples/parity/*{key}*.wav`` 首个匹配。
    """
    if not _SPEAKER_KEY_PATTERN.match(key):
        logger.warning("speaker_sample key 非法: %r", key[:120])
        return JSONResponse({"status": "error", "message": "Invalid speaker key"}, status_code=400)

    samples_dir = os.path.join(PROJECT_ROOT, "samples", "parity")
    if not os.path.isdir(samples_dir):
        return JSONResponse({"status": "error", "message": "Samples directory not found"}, status_code=404)

    pattern = os.path.join(samples_dir, f"*{key.lower()}*.wav")
    matches = glob.glob(pattern)
    if not matches:
        return JSONResponse(
            {"status": "error", "message": f"No sample found for speaker: {key}"},
            status_code=404,
        )
    match_path = Path(matches[0])
    # 确保匹配结果仍在 samples_dir 内（防 glob 注入）
    try:
        match_path.resolve().relative_to(Path(samples_dir).resolve())
    except ValueError:
        logger.warning("speaker_sample glob 越界: %s", match_path)
        return JSONResponse({"status": "error", "message": "Invalid speaker key"}, status_code=400)

    return _serve_safe_file(match_path, "audio/wav", match_path.name, _CACHE_STATIC_HEADER)


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------


@router.post("/upload/audio", summary="上传音频", description="上传音频文件到服务器（用于克隆参考）")
async def upload_audio(file: UploadFile = File(...)) -> Response:
    """上传音频文件（CSRF 由中间件统一校验）。

    流式分块读取并实时校验大小（防 DoS）；扩展名保留原始小写化结果，
    仅允许 ``ALLOWED_AUDIO_EXTENSIONS``；首 16 字节做魔数签名校验。

    Args:
        file: FastAPI ``UploadFile``。

    Returns:
        JSON 200: ``{"status": "ok", "path": ..., "filename": ...}``
        JSON 400 / 413 / 500
    """
    try:
        original_name = os.path.basename(file.filename or "")
        _, ext = os.path.splitext(original_name)
        ext_lower = ext.lower()
        if ext_lower not in ALLOWED_AUDIO_EXTENSIONS:
            return JSONResponse(
                {
                    "status": "error",
                    "message": (
                        f"Unsupported file type: {ext_lower}. Allowed: {', '.join(sorted(ALLOWED_AUDIO_EXTENSIONS))}"
                    ),
                },
                status_code=400,
            )

        timestamp = int(time.time() * 1000)
        import secrets as _secrets

        suffix = _secrets.token_hex(4)
        filename = f"temp_upload_{timestamp}_{suffix}{ext_lower}"
        file_path = os.path.join(SAVE_DIR, filename)

        ok, err_msg, header_bytes = await _stream_upload_to_disk(file, file_path)
        if not ok:
            return JSONResponse({"status": "error", "message": err_msg}, status_code=413)

        if not _validate_audio_content(header_bytes, ext_lower):
            try:
                os.remove(file_path)
            except OSError as exc:
                logger.debug("清理格式不匹配文件失败: %s", exc)
            return JSONResponse(
                {
                    "status": "error",
                    "message": (
                        "File content does not match the claimed audio format. "
                        "The file may be corrupted or not a valid audio file."
                    ),
                },
                status_code=400,
            )

        return JSONResponse({"status": "ok", "path": file_path, "filename": filename})
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("音频上传失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": "上传失败，请检查文件后重试"}, status_code=500)


# ---------------------------------------------------------------------------
# 历史记录查询与批量操作
# ---------------------------------------------------------------------------


@router.get("/history/table", summary="历史记录", description="获取生成历史记录分页表格")
async def history_table(request: Request) -> Response:
    """获取历史记录表格（GET ``/api/history/table``）。

    Query 参数（全部可选）：
        keyword / time_filter / duration_filter / include_hidden / limit / offset

    Args:
        request: FastAPI Request，读取 query params。

    Returns:
        JSON：``{"status": "ok", "records": [...], "total": ..., "hasMore":..., "loaded":...}``
    """
    keyword = request.query_params.get("keyword", "")
    if len(keyword) > _KEYWORD_MAX_LENGTH:
        keyword = keyword[:_KEYWORD_MAX_LENGTH]
    time_filter = request.query_params.get("time_filter", "all")
    duration_filter = request.query_params.get("duration_filter", "all")
    include_hidden = request.query_params.get("include_hidden", "false").lower() == "true"

    try:
        limit = int(request.query_params.get("limit", 20))
    except (ValueError, TypeError):
        limit = 20
    try:
        offset = int(request.query_params.get("offset", 0))
    except (ValueError, TypeError):
        offset = 0
    if offset < 0:
        offset = 0
    if limit > 100:
        limit = 100
    elif limit <= 0:
        limit = 20

    try:
        history_manager = get_history_db()
        result = history_manager.get_paginated_records(
            limit=limit,
            offset=offset,
            search_keyword=keyword,
            time_filter=time_filter,
            duration_filter=duration_filter,
            include_hidden=include_hidden,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("历史记录查询失败: %s", exc, exc_info=True)
        return JSONResponse(
            {"status": "error", "message": "查询历史记录失败"},
            status_code=500,
        )

    records = []
    for rec in result["items"]:
        file_size = rec.get("file_size_bytes", 0) or 0
        size_mb = file_size / (1024 * 1024) if file_size > 0 else 0
        size_str = f"{size_mb:.1f} MB"
        duration = rec.get("duration_seconds", 0) or 0
        try:
            duration = float(str(duration).rstrip("s"))
        except (ValueError, TypeError):
            duration = 0
        duration_str = f"{duration:.1f}s" if duration > 0 else "<1s"
        records.append([rec.get("filename", ""), rec.get("created_at", ""), duration_str, size_str])

    return JSONResponse(
        {
            "status": "ok",
            "records": records,
            "total": result["total"],
            "hasMore": result["hasMore"],
            "loaded": result["loaded"],
        }
    )


@router.post("/batch_export_history", summary="批量导出", description="批量导出历史记录音频为 ZIP")
async def batch_export_history(request: Request) -> Response:
    """批量导出历史音频为 ZIP（CSRF 由中间件校验）。

    Body JSON：``{"ids": [int, ...]}``

    Security：
        ZIP 内仅使用 ``os.path.basename(filename)`` 防 ZIP 路径遍历；
        错误消息不泄露内部路径细节。
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "无效的 JSON 请求体"}, status_code=400)

    ids = payload.get("ids", [])
    valid_ids, err_msg = _validate_ids(ids, max_count=_MAX_BATCH_EXPORT_COUNT)
    if err_msg:
        return JSONResponse({"status": "error", "message": err_msg}, status_code=400)

    history_manager = get_history_db()
    try:
        records = history_manager.get_records_by_ids(valid_ids)
    except Exception as exc:  # noqa: BLE001
        logger.error("批量导出查询失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": "查询历史记录失败"}, status_code=500)

    if not records:
        return JSONResponse({"status": "error", "message": "未找到有效记录"}, status_code=404)

    zip_buffer = io.BytesIO()
    found_count = 0
    missing_count = 0
    try:
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for rec in records:
                filename = rec["filename"]
                filepath = rec["filepath"]
                if filepath and os.path.isfile(filepath):
                    safe_name = os.path.basename(filename)  # [D4] 防 ZIP 路径遍历
                    zf.write(filepath, safe_name)
                    found_count += 1
                else:
                    missing_count += 1
    except Exception as exc:  # noqa: BLE001
        logger.error("批量导出打包 ZIP 失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": "打包导出文件失败"}, status_code=500)

    if found_count == 0:
        return JSONResponse(
            {"status": "error", "message": "所有音频文件均不存在或已被删除"},
            status_code=404,
        )

    zip_buffer.seek(0)
    timestamp = int(time.time())
    zip_filename = f"history_export_{timestamp}.zip"
    logger.info("[批量导出] %d 个文件, %d 个缺失", found_count, missing_count)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": _build_content_disposition(zip_filename, "attachment")},
    )


@router.post("/batch_delete_history", summary="批量删除", description="批量删除（隐藏）历史记录")
async def batch_delete_history(request: Request) -> Response:
    """批量隐藏 / 物理删除历史记录（CSRF 由中间件校验）。

    Body JSON：``{"ids": [...], "delete_files": false}``
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "无效的 JSON 请求体"}, status_code=400)

    ids = payload.get("ids", [])
    delete_files = payload.get("delete_files", False)

    valid_ids, err_msg = _validate_ids(ids)
    if err_msg:
        return JSONResponse({"status": "error", "message": err_msg}, status_code=400)

    if not isinstance(delete_files, bool):
        delete_files = bool(delete_files)

    history_manager = get_history_db()
    try:
        if delete_files:
            deleted_count, failed_files = history_manager.delete_multiple_records_by_ids(valid_ids, delete_file=True)
            count = deleted_count
            action = "deleted"
        else:
            count = history_manager.hide_multiple_records_by_ids(valid_ids)
            action = "hidden"
    except Exception as exc:  # noqa: BLE001
        logger.error("批量删除失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": "操作失败，请稍后重试"}, status_code=500)

    return JSONResponse({"status": "ok", "count": count, "action": action})


@router.post("/history/hide", summary="隐藏记录", description="隐藏指定的历史记录")
async def hide_history_records(request: Request) -> Response:
    """批量隐藏记录（CSRF 由中间件校验）。

    Body JSON：``{"ids": [int,...]}``
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "无效的 JSON 请求体"}, status_code=400)

    ids = payload.get("ids", [])
    valid_ids, err_msg = _validate_ids(ids)
    if err_msg:
        return JSONResponse({"status": "error", "message": err_msg}, status_code=400)

    history_manager = get_history_db()
    try:
        count = history_manager.hide_multiple_records_by_ids(valid_ids)
    except Exception as exc:  # noqa: BLE001
        logger.error("批量隐藏失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": "操作失败，请稍后重试"}, status_code=500)
    return JSONResponse({"status": "ok", "count": count})


@router.post("/history/clear_all", summary="清空记录", description="清空所有历史记录（默认只隐藏）")
async def clear_all_history(request: Request) -> Response:
    """清空所有历史记录（CSRF 由中间件校验）。

    Body JSON：``{"hide_only": true}``（默认 true，仅隐藏；false 物理删除）
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "无效的 JSON 请求体"}, status_code=400)

    hide_only = payload.get("hide_only", True)
    if not isinstance(hide_only, bool):
        hide_only = bool(hide_only)

    history_manager = get_history_db()
    try:
        count = history_manager.clear_all_records(hide_only=hide_only)
    except Exception as exc:  # noqa: BLE001
        logger.error("清空历史记录失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": "操作失败，请稍后重试"}, status_code=500)

    action = "hidden" if hide_only else "cleared"
    return JSONResponse({"status": "ok", "count": count, "action": action})


@router.post("/history/show", summary="显示记录", description="恢复显示被隐藏的历史记录")
async def show_history_records(request: Request) -> Response:
    """批量显示被隐藏的记录（CSRF 由中间件校验）。"""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "无效的 JSON 请求体"}, status_code=400)

    ids = payload.get("ids", [])
    valid_ids, err_msg = _validate_ids(ids)
    if err_msg:
        return JSONResponse({"status": "error", "message": err_msg}, status_code=400)

    history_manager = get_history_db()
    try:
        count = history_manager.show_multiple_records_by_ids(valid_ids)
    except Exception as exc:  # noqa: BLE001
        logger.error("批量显示失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": "操作失败，请稍后重试"}, status_code=500)
    return JSONResponse({"status": "ok", "count": count})


@router.post("/history/show_all", summary="显示全部", description="恢复显示所有隐藏记录")
async def show_all_history(request: Request) -> Response:
    """显示全部被隐藏的历史记录（CSRF 由中间件校验）。"""
    history_manager = get_history_db()
    try:
        count = history_manager.show_all_records()
    except Exception as exc:  # noqa: BLE001
        logger.error("显示全部历史记录失败: %s", exc, exc_info=True)
        return JSONResponse({"status": "error", "message": "操作失败，请稍后重试"}, status_code=500)
    return JSONResponse({"status": "ok", "count": count})


@router.post("/history/sync", summary="同步记录", description="触发后台增量同步文件系统与数据库")
async def sync_history(background_tasks: BackgroundTasks) -> Response:
    """触发后台增量同步历史记录（CSRF 由中间件校验）。

    立即返回 HTTP 200，同步在后台 ``BackgroundTasks`` 中执行，不阻塞响应。
    """
    background_tasks.add_task(_sync_history_incremental)
    return JSONResponse({"status": "ok", "message": "增量同步已触发"})
