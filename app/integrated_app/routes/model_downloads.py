# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""模型下载管理路由（framework-only 分发口径的用户侧入口）。

**路由前缀与端点**：
    前缀：``/api/models/download``
    - GET  /catalog — 下载目录（tier/安装状态/体积提示，按 required→optional 排序）
    - POST /start   — 启动下载任务（Form: key；同一时刻至多一个活动任务）
    - GET  /status  — 当前任务状态（阶段 + 日志尾部；顺带收割子进程退出态）
    - POST /cancel  — 取消当前任务（terminate → kill 兜底）

**架构位置**：
    薄路由层，全部逻辑在 :mod:`integrated_app.model_download_manager`；
    下载实现复用 ``scripts/download_models.py``（子进程），避免两套下载逻辑漂移。

**安全**：
    - ``key`` 必须在 MODEL_REGISTRY 白名单内（未知 KEY 400），不接受任意路径；
    - 子进程命令为固定脚本 + 白名单参数，无 shell 注入面；
    - CSRF 由全局 Double-Submit Cookie 中间件覆盖（POST 端点）。

**SSE 说明**：
    进度采用 2s 轮询 ``/status``（前端 fetch），不引入 SSE——下载是分钟级任务，
    日志尾部已足够表达进度，轮询实现与桌面壳/CLI 的复用成本最低。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse

from ..model_download_manager import model_download_manager

logger = logging.getLogger("tts_multimodel")

router = APIRouter(prefix="/api/models/download", tags=["model-downloads"])


@router.get("/catalog")
def download_catalog() -> dict:
    """下载目录：全部条目 + tier + 安装状态（只读，不触发任何下载）。"""
    return {"items": model_download_manager.catalog()}


@router.post("/start")
async def download_start(key: str = Form(...)) -> JSONResponse:
    """启动一个下载任务；已有任务运行中时返回 409。"""
    job, err = model_download_manager.start(key.strip())
    if err or job is None:
        status_code = 404 if "未知模型 KEY" in err else 409
        return JSONResponse({"status": "error", "message": err}, status_code=status_code)
    logger.info("[model-download] API 启动任务: %s", key)
    return JSONResponse({"status": "ok", "job": job.to_dict()})


@router.get("/status")
def download_status() -> dict:
    """当前任务状态（含日志尾部；空闲时 job 为 null）。"""
    return model_download_manager.status()


@router.post("/cancel")
async def download_cancel() -> JSONResponse:
    """取消当前下载任务（尽力 terminate；子进程可能需数秒退出）。"""
    ok, err = model_download_manager.cancel()
    if not ok:
        return JSONResponse({"status": "error", "message": err}, status_code=409)
    return JSONResponse({"status": "ok"})
