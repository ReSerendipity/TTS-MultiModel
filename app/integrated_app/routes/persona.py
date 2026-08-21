"""Persona 音色管理路由模块。

架构说明：
    本模块提供 Persona 音色管理的 REST API 路由（前缀 /api/persona），
    对应 Persona Tab 的增删改查功能。主要职责包括：
    - 音色列表查询与关键词过滤（GET /table）
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

import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..exceptions import PersonaNotFoundError
from ..exceptions import ValidationError as TTSValidationError
from ..persona_manager import (
    delete_persona,
    get_persona_detail_table,
    get_total_persona_count,
)

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
