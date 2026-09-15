# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""通用引擎发现 / 能力查询端点（通用接入口径的补强）。

本模块提供**只读、不触发模型加载**的通用端点，让前端 / 第三方工具在
接入任意新引擎后，立刻能通过统一口径看到其注册元数据与能力集合
（是否实现 ControllableTTSEngine、支持哪些特性标签等）。

设计要点：
    - 路由前缀 ``/api/engines``，独立于现役 per-engine 路由
      （``/api/generate/voxcpm2``、``/api/generate/indextts2`` 等），
      也独立于 ``/api/generate/generic`` 包里的通用克隆端点，互不冲突。
    - 由 ``app_server._discover_routes`` 自动发现（模块级 ``router`` 变量），
      无需在中心路由表登记。
    - 完全只读：只查 ``engine_registry`` 的元数据，不实例化引擎、不加载 GPU。
    - 生成类请求仍走各引擎专属路由（或其薄路由模块）；本端点只负责"发现与能力"。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ...engine_interface import (
    engine_implements_controllable,
    engine_registry,
)

logger = logging.getLogger("tts_multimodel")

router = APIRouter(prefix="/api/engines", tags=["engine-discovery"])


def _engine_capabilities(cls: type | None, name: str) -> dict[str, bool]:
    """探测引擎类对 TTSEngine / ControllableTTSEngine 两个协议的满足情况。

    WHY 用 ``hasattr`` 而非 ``issubclass``：
        ``ControllableTTSEngine`` 含非方法成员（``lora_enabled`` 属性），
        ``runtime_checkable`` Protocol 对含非方法成员的协议禁止 ``issubclass``。
        这里改为鸭子类型成员探测（与 Protocol 的判定语义一致）。

    Args:
        cls: 引擎类（可能为 None，表示未注册 / 懒导入失败）。
        name: 引擎注册名（用于统一能力探测，避免重复实现成员检查逻辑）。

    Returns:
        dict: 含 ``tts_engine`` / ``controllable`` 两个布尔能力标志。
    """
    if cls is None:
        return {"tts_engine": False, "controllable": False}
    _tts_methods = (
        "is_ready",
        "load",
        "unload",
        "generate_voice_design",
        "generate_voice_clone",
        "generate_script",
        "generate_streaming",
    )
    is_tts = all(hasattr(cls, attr) for attr in _tts_methods)
    return {
        "tts_engine": is_tts,
        "controllable": engine_implements_controllable(name) if name else False,
    }


@router.get("/list")
def list_engines() -> dict:
    """列出所有已注册引擎及其元数据与能力。

    Returns:
        dict: ``{"engines": [...]}``，每个元素含
        ``name`` / ``display_name`` / ``metadata`` / ``capabilities``。
    """
    engines: list[dict] = []
    for name in engine_registry.list_engines():
        meta = engine_registry.get_metadata(name)
        cls = engine_registry.get(name)
        engines.append(
            {
                "name": name,
                "display_name": meta.get("display_name", name),
                "metadata": meta,
                "capabilities": _engine_capabilities(cls, name),
            }
        )
    return {"engines": engines}


@router.get("/{engine_id}/info", response_model=None)
def engine_info(engine_id: str) -> dict[str, Any] | JSONResponse:
    """查询单个引擎的元数据与能力；未注册返回 404。

    Args:
        engine_id: 引擎注册名。

    Returns:
        dict[str, Any] | JSONResponse: 含 ``name`` / ``display_name`` /
            ``metadata`` / ``capabilities``；未注册时返回 404 JSON。

    WHY 返回标注是联合类型 + 装饰器带 ``response_model=None``：
        本函数有两条 return 路径，404 分支返回的是 ``JSONResponse`` 而非 dict。
        ① 此前只写 ``-> dict``，mypy 报
        ``Incompatible return value type (got "JSONResponse", expected "dict[Any, Any]")``
        ——即 2026-09-14 提交 024572e 引入、让 mypy 棘轮从 103 涨到 104 的那个错误。
        ② 但只把标注改成联合类型还不够：FastAPI 会拿返回标注去推断响应模型，
        ``dict[str, Any] | JSONResponse`` 不是合法 Pydantic 字段类型，模块导入期就抛
        ``FastAPIError: Invalid args for response field``（整个 routes 发现链会连带失败）。
        故按 FastAPI 官方提示加 ``response_model=None`` 关闭推断，两条 return 路径
        的类型标注与实际返回就此一致。
    """
    if not engine_registry.is_registered(engine_id):
        return JSONResponse(
            status_code=404,
            content={"error": f"未注册引擎: {engine_id}（可用: {engine_registry.list_engines()}）"},
        )
    meta = engine_registry.get_metadata(engine_id)
    cls = engine_registry.get(engine_id)
    return {
        "name": engine_id,
        "display_name": meta.get("display_name", engine_id),
        "metadata": meta,
        "capabilities": _engine_capabilities(cls, engine_id),
        "controllable_features": meta.get("supported_features", []),
    }
