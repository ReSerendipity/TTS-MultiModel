#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""下载管理路由：运行时与模型权重的就绪检测、安装、取消、卸载。

瘦包首次启动时后端跑在**引导解释器**上（只有 Web 依赖、没有 torch），本模块与
``download_service.py`` 是「下载管理」页面的全部依赖，**两者都禁止顶层
import torch**——否则页面打不开，用户就陷入「装了软件却装不上环境」的死局。

刻意不用 ``Depends`` 注入配置：本仓没有 ``dependencies.py``，但同类仓的那份顶层
import 了 ModelManager → 推理引擎 → torch，一旦照抄就会把本路由连带拖挂。配置一律
从 ``request.app.state.config`` 取（由 ``app_server.create_app`` 写入）。

组件 ID 约定：环境为 ``runtime``，权重为模型清单里的 key（如 ``voxcpm2``）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..download_service import (
    build_status,
    catalog_view,
    get_download_manager,
    runtime_catalog,
    scan_models,
    uninstall_model,
    uninstall_runtime,
)

logger = logging.getLogger("tts_multimodel")

router = APIRouter(prefix="/api/downloads", tags=["downloads"])

#: 与 ``routes/system/shutdown.py`` 同一套回环白名单：下载/删除是重操作，只认本机
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})


def _ok(data: object) -> dict:
    """统一的成功响应包装（前端一律按 ``resp.success`` 判断）。"""
    return {"success": True, "data": data}


def _err(code: str, message: str, status: int) -> JSONResponse:
    """统一的错误响应包装。"""
    return JSONResponse(
        status_code=status,
        content={"success": False, "error": {"code": code, "message": message}},
    )


def _deny_remote(request: Request) -> JSONResponse | None:
    """非本机来源一律拒绝（下载与删除都不该被局域网/公网触发）。"""
    client_host = (request.client.host if request.client else "") or ""
    if client_host not in LOOPBACK_HOSTS:
        logger.warning("[downloads] 拒绝非本机下载管理请求，来源: %s", client_host or "<unknown>")
        return _err("FORBIDDEN", "仅允许本机调用", 403)
    return None


def _config(request: Request) -> dict:
    """从应用状态取配置（缺失时给空 dict，不影响端点可用性）。"""
    config = getattr(request.app.state, "config", None)
    if config is None:
        return {}
    if isinstance(config, dict):
        return config
    raw = getattr(config, "_yaml_config", None)
    return raw if isinstance(raw, dict) else {}


class StartRequest(BaseModel):
    """启动下载任务的请求体。"""

    kind: str = Field(description="runtime | model")
    target: str = Field(default="", description="kind=model 时模型清单里的 key")


class ComponentRequest(BaseModel):
    """取消/卸载的请求体。"""

    kind: str = Field(description="runtime | model")
    target: str = Field(default="", description="kind=model 时模型清单里的 key")


def _component(kind: str, target: str) -> str:
    """组件 ID：runtime 或 model:<key>。"""
    return "runtime" if kind == "runtime" else f"model:{target}"


@router.get("/status")
async def status(request: Request) -> dict:
    """整体状态：当前运行模式、运行时是否已装环境、各模型是否就位。"""
    data = build_status()
    data["config_present"] = bool(_config(request))
    return _ok(data)


@router.get("/catalog")
async def catalog(request: Request) -> dict:
    """可下载清单：环境包（分卷与校验和）+ 各权重变体。"""
    return _ok(catalog_view())


@router.post("/start")
async def start(request: Request, payload: StartRequest):
    """启动下载任务。同一组件重复调用返回既有任务，不会并行下两遍。"""
    denied = _deny_remote(request)
    if denied:
        return denied
    kind = payload.kind.strip()
    target = payload.target.strip()
    if kind not in ("runtime", "model"):
        return _err("BAD_REQUEST", "kind 必须是 runtime 或 model", 400)
    if kind == "runtime":
        if not runtime_catalog():
            return _err(
                "NO_CATALOG",
                "未找到环境包清单 downloads_catalog.json，无法确定下载地址",
                503,
            )
    else:
        if not target:
            return _err("BAD_REQUEST", "kind=model 必须指定 target", 400)
        if target not in {str(m["name"]) for m in scan_models()}:
            return _err("BAD_REQUEST", f"未知模型: {target}", 400)
    task = get_download_manager().start(kind, target)
    logger.info("[downloads] 任务启动: %s", task.component)
    return _ok(task.to_dict())


@router.get("/progress")
async def progress() -> dict:
    """全部下载任务的进度快照（前端 1s 轮询）。"""
    return _ok({"tasks": get_download_manager().progress()})


@router.post("/cancel")
async def cancel(request: Request, payload: ComponentRequest):
    """请求取消：置取消标志，下载循环在下一次读块时退出。"""
    denied = _deny_remote(request)
    if denied:
        return denied
    component = _component(payload.kind.strip(), payload.target.strip())
    cancelled = get_download_manager().cancel(component)
    logger.info("[downloads] 取消请求: %s -> %s", component, cancelled)
    return _ok({"cancelled": cancelled})


@router.post("/uninstall")
async def uninstall(request: Request, payload: ComponentRequest):
    """卸载：引导态只摘 torch 家族与 RUNTIME.json，完整运行时态删整个目录。"""
    denied = _deny_remote(request)
    if denied:
        return denied
    kind = payload.kind.strip()
    target = payload.target.strip()
    if kind == "runtime":
        uninstall_runtime()
        logger.info("[downloads] 已卸载运行环境")
        return _ok({"uninstalled": "runtime"})
    if kind == "model" and target:
        try:
            uninstall_model(target)
        except KeyError as exc:
            return _err("BAD_REQUEST", str(exc), 400)
        except ValueError as exc:
            return _err("BAD_REQUEST", str(exc), 400)
        logger.info("[downloads] 已卸载模型: %s", target)
        return _ok({"uninstalled": f"model:{target}"})
    return _err("BAD_REQUEST", "kind 必须是 runtime 或 model+target", 400)
