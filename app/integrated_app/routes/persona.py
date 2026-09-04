"""Persona 音色管理路由模块。

架构说明：
    本模块提供 Persona 音色管理的 REST API 路由（前缀 /api/persona），
    对应 Persona Tab 的增删改查功能。主要职责包括：
    - 音色列表查询与关键词过滤（GET /table）
    - 音色固化保存（POST /save，voice_design / voice_clone / ultimate_clone
      三页的「保存音色」按钮；重名时通过 X-Persona-Confirm 响应头驱动前端二次点击覆盖）
    - 音色删除（DELETE /{name}，同步清理关联的 wav/txt/pt/json 文件）
    - 音色参考音频下发由 routes/audio.py 的 /api/persona/audio/{name} 处理

Persona 数据结构（由 persona_manager 维护）：
    - id / name: 音色唯一标识名称
    - description: 音色描述文本
    - tags: 标签列表
    - reference_audio_path: 参考音频 .wav 路径
    - embedding_path: 预计算嵌入 .pt 路径
    - created_at / updated_at: 创建/更新时间戳
"""

import contextlib
import html
import logging
import os
import re

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import SAVE_DIR
from ..exceptions import PersonaNotFoundError
from ..exceptions import ValidationError as TTSValidationError
from ..persona_manager import (
    delete_persona,
    fn_save_persona,
    get_persona_detail_table,
    get_persona_list,
    get_total_persona_count,
)
from .generate.utils import ALLOWED_AUDIO_EXTENSIONS, save_uploaded_audio

router = APIRouter(prefix="/api/persona", tags=["persona"])
logger = logging.getLogger("tts_multimodel")

_KEYWORD_MAX_LENGTH = 100

# R6: Why 非法 ID 字符集定义 —— Persona 名称最终会映射到文件系统路径，
# 必须剔除路径遍历符号（/ \\ ..）和 shell 元字符，防止任意文件读写。
_INVALID_ID_PATTERN = re.compile(r"[\\/\x00-\x1f<>|:\"?*\x7f]|\.\.")


def _validate_persona_id(name: str) -> None:
    """校验 Persona ID 是否包含非法字符。

    Args:
        name: 待校验的 Persona 名称。

    Raises:
        TTSValidationError: 名称为空或包含非法字符时抛出，HTTP 403。
    """
    if not name or not name.strip():
        raise TTSValidationError("非法 Persona ID：名称不能为空")
    if _INVALID_ID_PATTERN.search(name):
        raise TTSValidationError("非法 Persona ID")


#: 触发前端把表单里的 overwrite 翻成 true 的响应头（见 static/js/app_init.js）
_PERSONA_CONFIRM_HEADER: str = "X-Persona-Confirm"


def _persona_status_html(message: str, tone: str) -> HTMLResponse:
    """渲染保存结果的 HTMX 片段（直接 innerHTML 进各页的 status 容器）。

    Args:
        message: 面向用户的提示文本（会被 HTML 转义）。
        tone: ``success`` / ``warning`` / ``error``，决定配色语义。

    Returns:
        HTMLResponse: 带 ``status-message`` 类的片段。
    """
    color = {"success": "#16a34a", "warning": "#f59e0b", "error": "#dc2626"}[tone]
    return HTMLResponse(
        f'<div class="status-message {tone}" style="font-size:12px;color:{color}">{html.escape(message)}</div>'
    )


def _resolve_generated_audio(value: str) -> str | None:
    """把前端回传的「最近一次生成结果」文件名解析为 SAVE_DIR 内的绝对路径。

    WHY 只接受裸文件名：该字段来自客户端，若允许路径分隔符就直接构成任意文件
    读取——音色固化会把音频复制进 personas/，因此必须钉死在 outputs/ 目录内。

    Args:
        value: 前端 ``result_audio`` 字段（来自结果片段的 data-audio-filename）。

    Returns:
        命中且合法时返回绝对路径，否则 None。
    """
    if not value or os.path.basename(value) != value or value in {".", ".."}:
        return None
    if os.path.splitext(value)[1].lower() not in ALLOWED_AUDIO_EXTENSIONS:
        return None
    save_root = os.path.realpath(SAVE_DIR)
    candidate = os.path.realpath(os.path.join(SAVE_DIR, value))
    if candidate != save_root and not candidate.startswith(save_root + os.sep):
        return None
    return candidate if os.path.isfile(candidate) else None


@router.post(
    "/save",
    summary="保存音色",
    description="把上传的参考音频或最近一次生成结果固化到音色库；重名时返回覆盖确认",
)
async def persona_save(
    request: Request,
    save_name: str = Form(""),
    ref_audio: UploadFile | None = File(None),
    ref_audio_upload: UploadFile | None = File(None),
    result_audio: str = Form(""),
    ref_text: str = Form(""),
    instruction: str = Form(""),
    overwrite: bool = Form(False),
) -> HTMLResponse:
    """保存音色（voice_design / voice_clone / ultimate_clone 三页共用）。

    音频来源二选一，优先级：上传的 ``ref_audio`` > ``ref_audio_upload``（克隆/极致
    克隆页生成表单的文件控件名，保存按钮随该表单提交，故此处两个名字都要收）>
    生成结果 ``result_audio``。克隆/极致克隆页表单自带参考音频，走前者；语音设计页
    无参考音频，固化的是「最近一次生成的结果音频」，由前端把结果片段上的
    ``data-audio-filename`` 回填进隐藏字段。

    Args:
        request: FastAPI Request（CSRF 由中间件统一校验，此处无需二次处理）。
        save_name: 目标音色名，交由 ``fn_save_persona`` 做白名单与越界校验。
        ref_audio: 用户上传的参考音频（设计页/历史表单使用的字段名）。
        ref_audio_upload: 用户上传的参考音频（克隆/极致克隆页生成表单的字段名别名）。
        result_audio: outputs/ 目录内的生成结果文件名。
        ref_text: 音色参考文本（克隆页）。
        instruction: 音色风格描述（设计页，作为 ref_text 的替代来源）。
        overwrite: 是否覆盖同名音色。

    Returns:
        HTMLResponse: 状态片段；重名且 overwrite=False 时额外带
        ``X-Persona-Confirm`` 头，前端据此把表单的 overwrite 置为 true，
        用户再点一次即覆盖。
    """
    name: str = save_name.strip()
    if not name:
        return _persona_status_html("请先填写音色名称", "error")

    from ..security.audit import log_audit

    log_audit("persona_save", detail=f"name={name}", outcome="attempt")

    staged_path: str | None = None
    audio_input: str | None = None

    # 克隆/极致克隆页的「保存音色」按钮随生成表单提交，文件控件名是
    # ref_audio_upload；设计页/历史表单用 ref_audio。两个名字都收，前者优先。
    effective_upload = ref_audio if (ref_audio is not None and ref_audio.filename) else ref_audio_upload

    if effective_upload is not None and effective_upload.filename:
        # 复用 routes/generate/utils.save_uploaded_audio：它已实现扩展名白名单、
        # 体积上限与安全文件名处理，落盘后返回绝对路径。
        # WHY 不把 bytes 直接喂给 fn_save_persona：其 docstring 声称支持 bytes，
        # 但底层 preprocess_and_save_temp 实际只接受 文件路径 / UploadFile /
        # np.ndarray，传 bytes 会得到「不支持的音频输入类型: <class 'bytes'>」——
        # 文档与实现不符，以实现为准。
        staged_path, err = await save_uploaded_audio(request, effective_upload, title_key="op_failed")
        if err is not None:
            # 校验失败时 _error_html 返回 HTTP 400。但 HTMX 默认只把 2xx 响应换进
            # 目标容器，而 app_init.js 的 htmx:responseError 监听器只做 console.warn
            # + 复位生成按钮状态、不渲染响应体。结果是用户点了「保存音色」却看不到
            # 任何内联提示。因此这里保留原提示片段、以 200 返回给同一个 status 容器
            # 渲染，与本端点其它校验分支形态一致；同时转发 HX-Trigger，让全仓统一的
            # toast 通道也照常收到（_error_html 的该头此前因 ensure_ascii=False 抛
            # UnicodeEncodeError 而一直静默丢失，见 routes/generate/utils.py）。
            headers: dict[str, str] = {}
            trigger = err.headers.get("HX-Trigger")
            if trigger:
                headers["HX-Trigger"] = trigger
            return HTMLResponse(content=err.body, status_code=200, headers=headers)
        audio_input = staged_path
    else:
        audio_input = _resolve_generated_audio(result_audio.strip())
        if audio_input is None and result_audio.strip():
            return _persona_status_html("生成结果音频无效或已被清理，请重新生成后再保存", "error")

    if audio_input is None:
        return _persona_status_html("缺少音频：请上传参考音频，或先生成一段语音再保存", "error")

    try:
        message, needs_confirm = fn_save_persona(
            name,
            audio_input,
            (ref_text or instruction).strip(),
            bool(overwrite),
        )
    except Exception as exc:  # noqa: BLE001 - 固化失败不得泄漏内部异常细节
        logger.exception("保存音色失败 (name=%s): %s", name, exc)
        return _persona_status_html("保存失败，请稍后重试", "error")
    finally:
        # 临时上传文件用完即删，避免 outputs/uploads 堆积
        if staged_path:
            with contextlib.suppress(OSError):
                if os.path.isfile(staged_path):
                    os.remove(staged_path)

    if needs_confirm:
        response = _persona_status_html(message, "warning")
        response.headers[_PERSONA_CONFIRM_HEADER] = "1"
        return response

    return _persona_status_html(message, "success" if message.startswith("✅") else "error")


@router.get(
    "/list",
    summary="音色名下拉片段",
    description="返回音色名 <option> 片段，供克隆/设计页「刷新列表」按钮局部刷新下拉框",
)
async def persona_list_options(request: Request) -> HTMLResponse:
    """返回音色下拉框的 ``<option>`` HTML 片段（HTMX innerHTML swap 专用）。

    WHY：voice_clone / ultimate_clone 两页的「刷新列表」按钮历史上指向
    ``hx-get="/api/persona/list"``、voice_design 页指向 ``/api/persona/options``，
    而后端从未注册这两个 GET 端点（实际命中 405，htmx 对 4xx 不 swap、全局
    responseError 只 console.warn → 按钮静默失效）。列表本体由 tab 渲染时的
    ``ctx.persona_list`` 服务端注入；本端点与 tab 渲染共用 ``get_persona_list()``
    数据源，避免两处漂移。

    Args:
        request: FastAPI 请求对象（未用 query 参数；保留签名与同文件风格一致）。

    Returns:
        HTMLResponse: ``<option>`` 片段；音色库为空时返回占位 option（与
        模板空态文案一致，路由层片段沿用 persona_save 的中文文案约定）。
    """
    names: list[str] = get_persona_list()
    options = "".join(f'<option value="{html.escape(n, quote=True)}">{html.escape(n)}</option>' for n in names)
    if not options:
        options = '<option value="" disabled selected>暂无已保存音色，请先上传或生成</option>'
    return HTMLResponse(options)


@router.get("/table", summary="音色表格", description="获取音色库表格数据，支持关键词过滤")
async def persona_table(request: Request) -> JSONResponse:
    """获取音色库表格数据，支持关键词过滤与分页。

    Args:
        request: FastAPI 请求对象，用于读取 query_params。
            - keyword (str): 搜索关键词，可选，最长 100 字符，超长自动截断。

    Returns:
        JSONResponse: 标准响应结构
            {
                "status": "ok",
                "records": List[List[str]],  音色详情表格行
                "total": int                音色总数量
            }

    Raises:
        无显式异常，persona_manager 内部错误会被全局 error_handler 捕获。
    """
    keyword: str = request.query_params.get("keyword", "")
    if len(keyword) > _KEYWORD_MAX_LENGTH:
        keyword = keyword[:_KEYWORD_MAX_LENGTH]

    records: list[list[str]] = get_persona_detail_table(search_keyword=keyword)

    return JSONResponse(
        {
            "status": "ok",
            "records": records,
            "total": get_total_persona_count(),
        }
    )


@router.delete("/{name}", summary="删除音色", description="删除指定音色及其关联文件（wav/txt/pt/json）")
async def persona_delete(name: str) -> JSONResponse:
    """删除指定 Persona 音色及其关联的全部文件。

    删除策略（Why 注释）：
        四文件（wav/txt/pt/json）逐个 try/except 独立删除，单个失败不中断整体流程，
        也不回滚已删除的文件。原因：wav 删除了但 txt 没删除的"半删除"状态
        比"四文件全存在"更好——下次 list 时找不到 .wav 会自动标记"损坏音色"
        让用户手动清理，比 throw 400 整笔回滚（wav 恢复回来）更鲁棒。

    Args:
        name: Persona 音色名称（唯一 ID）。

    Returns:
        JSONResponse: 删除结果
            成功: {"status": "ok", "message": str} HTTP 200
            失败: {"status": "error", "message": str} HTTP 400

    Raises:
        TTSValidationError: name 包含非法字符时抛出，HTTP 403。
        PersonaNotFoundError: 目标音色不存在时抛出，HTTP 404。
    """
    _validate_persona_id(name)

    try:
        success: bool
        message: str
        success, message = delete_persona(name)
    except PersonaNotFoundError:
        raise
    except OSError as fs_err:
        logger.error(f"删除 Persona 底层文件失败 name={name}: {fs_err}")
        return JSONResponse(
            {"status": "error", "message": f"删除文件失败: {fs_err}"},
            status_code=400,
        )

    if success:
        return JSONResponse({"status": "ok", "message": message})
    return JSONResponse({"status": "error", "message": message}, status_code=400)
