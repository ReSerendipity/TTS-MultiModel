# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""TTS_MultiModel FastAPI 应用核心入口模块。

架构说明：
    本模块负责创建 FastAPI 应用实例、管理应用生命周期（lifespan）、
    注册中间件（RequestID/CORS/CSRF/APIAuth/error_handler）、
    通过 pkgutil 自动发现并挂载路由、配置静态文件服务和模板引擎。

启动链路：
    start.bat → app/clean_launch.py → app/integrated_app/app_server.py
    → uvicorn.run(create_app()) → lifespan(startup) → 路由自动注册 → 服务监听

硬约束（AGENTS.md §6）：
    - workers=1：GPU 单 Worker 串行，避免并发显存爆炸
    - server.auto_load_model=true：lifespan startup 阶段自动预加载模型
    - 模型加载失败不阻止应用启动：用户可手动在 Settings 页加载模型
"""

import asyncio
import contextlib
import importlib
import logging
import os
import pkgutil
import threading
import time
from collections.abc import AsyncGenerator
from logging.handlers import RotatingFileHandler
from typing import Any

import uvicorn
from fastapi import FastAPI, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth import APIAuthMiddleware
from .exceptions import TTSError, ValidationError
from .middleware.csrf import CSRFMiddleware
from .middleware.error_handler import (
    generic_error_handler,
    tts_error_handler,
    validation_error_handler,
)
from .middleware.rate_limit import RateLimitMiddleware
from .middleware.request_id import RequestIDLogFilter, RequestIDMiddleware
from .model_registry import EngineName

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_BASE_DIR))
logger = logging.getLogger("tts_multimodel")

_event_loop: asyncio.AbstractEventLoop | None = None


def _set_event_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Store the running event loop for cross-thread state updates.

    Args:
        loop: 当前运行的 asyncio 事件循环，None 表示清除引用。
    """
    global _event_loop
    _event_loop = loop


# --- Cache-aware StaticFiles (P1-1: 差异化缓存策略，来源：Seedvr2) ---

# CSS/JS：开发时经常改，不缓存
_NO_CACHE_EXTENSIONS: set[str] = {
    ".html",
    ".json",
    ".css",
    ".js",
    ".map",
}

# 字体：几乎不变，缓存 30 天 (2592000 秒)
_FONT_EXTENSIONS: set[str] = {
    ".woff2",
    ".woff",
    ".ttf",
    ".eot",
    ".otf",
}

# 图片：缓存 1 天 (86400 秒)
_IMAGE_EXTENSIONS: set[str] = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".webp",
}


class CachedStaticFiles(StaticFiles):
    """StaticFiles subclass with differentiated Cache-Control headers (P1-1).

    Strategy (来源：Seedvr2 VersionedStaticFiles):
    - CSS / JS / HTML / JSON: ``no-cache, must-revalidate`` (开发时经常改)
    - Fonts (.woff2 / .woff / .ttf / .eot / .otf): ``public, max-age=2592000`` (缓存 30 天)
    - Images (.png / .jpg / .jpeg / .gif / .svg / .ico / .webp): ``public, max-age=86400`` (缓存 1 天)
    - Other: 保持默认（不加 Cache-Control 头）
    """

    async def get_response(self, path: str, scope: dict[str, Any]) -> Response:
        """重写父类 StaticFiles.get_response，根据文件类型添加差异化 Cache-Control 头。

        Args:
            path: 请求的静态文件路径。
            scope: ASGI scope 字典。

        Returns:
            Response: 添加了差异化 Cache-Control 头的 HTTP 响应。
        """
        response = await super().get_response(path, scope)
        if hasattr(response, "headers") and response.status_code == 200:
            ext = os.path.splitext(path)[1].lower()
            if ext in _NO_CACHE_EXTENSIONS:
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
                response.headers["Pragma"] = "no-cache"
            elif ext in _FONT_EXTENSIONS:
                response.headers["Cache-Control"] = "public, max-age=2592000"
            elif ext in _IMAGE_EXTENSIONS:
                response.headers["Cache-Control"] = "public, max-age=86400"
        return response


def setup_logging() -> None:
    """配置日志：控制台 + 按大小轮转的文件（单文件 10MB，保留 3 个备份）。

    日志级别与路径支持环境变量覆盖（优先级高于默认值）：
    - ``LOG_LEVEL``：DEBUG / INFO / WARNING / ERROR
    - ``LOG_PATH``：日志文件路径，默认 ``<项目根>/logs/app.log``

    统一格式：时间戳 + 级别 + 进程/线程 + 模块位置 + 请求ID + 消息，
    便于生产环境按 request_id 做链路追踪、按 filename:lineno 快速定位。
    """
    root_logger = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers):
        return

    # 日志路径：环境变量 LOG_PATH 优先，否则使用项目根 logs/app.log
    log_path = os.environ.get("LOG_PATH", os.path.join(_PROJECT_ROOT, "logs", "app.log"))
    log_dir = os.path.dirname(log_path)
    os.makedirs(log_dir, exist_ok=True)

    # 日志级别：环境变量 LOG_LEVEL 优先，默认 INFO
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [PID:%(process)d TID:%(thread)d] "
        "[%(name)s:%(filename)s:%(lineno)d] [req=%(request_id)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出（开发/调试时可见）
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(RequestIDLogFilter())
    root_logger.addHandler(stream_handler)

    # 文件输出（按大小轮转）
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(RequestIDLogFilter())
    root_logger.addHandler(file_handler)

    # 无条件设置根 logger 级别（默认 root 为 WARNING，会过滤 INFO 日志）
    root_logger.setLevel(log_level)


def _discover_routes(package_name: str = ".routes") -> list[str]:
    """使用 pkgutil 递归发现路由模块名列表。

    通过 ``pkgutil.walk_packages`` 遍历指定包下的所有模块和子包，
    返回完整的相对模块名列表（不含 router 实例提取）。
    实际的模块导入和 router 提取由调用方在 try/except 中执行。

    Why 传 package=__package__:
        相对导入路径（如 ``.routes.api.persona``）需要包上下文才能正确解析。
        不传 ``package`` 参数会导致 ``importlib.import_module`` 抛出
        ``ImportError: attempted relative import with no known parent package``。

    Args:
        package_name: 要扫描的包路径，默认 ``.routes`` 相对当前包。

    Returns:
        已发现的模块相对路径列表，例如 ``["routes.pages", "routes.api.model"]``。
    """
    discovered: list[str] = []
    try:
        base_pkg = importlib.import_module(package_name, package=__package__)
    except ImportError as e:
        logger.warning(f"[路由发现] 导入基础包 {package_name} 失败: {e}")
        return discovered

    if not hasattr(base_pkg, "__path__"):
        return discovered

    for _importer, modname, _ispkg in pkgutil.walk_packages(
        base_pkg.__path__,
        prefix=base_pkg.__name__ + ".",
    ):
        try:
            discovered.append(modname)
        except Exception as e:
            logger.warning(f"[路由发现] 记录模块名 {modname} 失败: {e}")
            continue

    return discovered


def _auto_discover_routers(routes_package: Any, prefix: str = "") -> list[Any]:
    """Recursively discover and collect routers from routes package.

    Handles both top-level modules (e.g., pages.py) and sub-packages
    (e.g., system/health.py, generate/voxcpm2/design.py).

    Args:
        routes_package: The routes package to scan (must have __path__ attribute).
        prefix: Current import prefix for nested packages.

    Returns:
        List of FastAPI APIRouter instances.
    """
    routers: list[Any] = []
    if not hasattr(routes_package, "__path__"):
        return routers
    for _importer, modname, ispkg in pkgutil.iter_modules(routes_package.__path__):
        full_name = f"{prefix}{modname}" if prefix else modname
        try:
            mod = importlib.import_module(f".routes.{full_name}", package="integrated_app")
            if hasattr(mod, "router"):
                routers.append(mod.router)
        except Exception as e:
            logger.warning(f"[路由发现] 导入 {full_name} 失败: {e}")

        if ispkg:
            try:
                subpkg = importlib.import_module(f".routes.{full_name}", package="integrated_app")
                routers.extend(_auto_discover_routers(subpkg, f"{full_name}."))
            except Exception as e:
                logger.warning(f"[路由发现] 递归扫描 {full_name} 失败: {e}")

    return routers


async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI 生命周期上下文管理器（async contextmanager 风格）。

    Startup 阶段（进入 ``yield`` 前）：
        1. 捕获当前事件循环引用，供后台线程安全调度协程
        2. 初始化 app.state 模型加载状态
        3. 同步历史记录数据库（history_db.sync_from_filesystem）
        4. 加载声明式引擎规格（load_engine_specs_from_config）
        5. 预热 Persona 缓存（如可用）
        6. 若 auto_load_model=true，后台线程预加载模型
        7. 启动 HealthMonitor 健康监控线程

    Shutdown 阶段（离开 ``yield`` 后）：
        1. 先 unload 所有模型（模型卸载会触发 history 写入）
        2. 再关闭 history_db 连接池（确保写入完成后再关 DB）
        3. 清理临时文件目录

    Why 先 unload 模型再 close_all DB：
        模型卸载流程（``model_manager.unload_all``）会将最终推理状态、
        显存释放记录等写入 history 数据库。如果先关闭 DB 连接池，
        这些写入会抛异常并丢失审计日志。

    Args:
        app: FastAPI 应用实例。

    Yields:
        None: 应用运行期间挂起，直到接收到 shutdown 信号。
    """
    _set_event_loop(asyncio.get_running_loop())

    app.state.models_ok = False
    app.state.model_loading = False
    app.state.model_load_progress = "等待手动加载模型"

    # P0-3: 核心模块完整性自校验 (CWE-912 防御，来源：Seedvr2)
    # 在 HistoryDB 初始化之前、config 加载之后执行
    # 自检失败默认只告警不阻塞启动（避免误伤）；可按 runtime.integrity.block_startup_on_failure 阻断
    from .config import get_config

    try:
        from .security.integrity_selfcheck import run_startup_selfcheck

        selfcheck = run_startup_selfcheck()
        if selfcheck["failed"] > 0:
            logger.error(
                "=" * 60 + "\n"
                "[SECURITY] ⚠️  启动时核心模块完整性自检失败！\n"
                f"    失败文件: {', '.join(selfcheck['failed_files'])}\n"
                "    请检查代码是否被篡改或重新生成清单。\n" + "=" * 60
            )
    except Exception as e:
        logger.debug(f"核心模块完整性自检跳过: {e}")
        selfcheck = {"failed": 0, "failed_files": []}

    # M1 整改：按配置可在自检失败时阻断启动（默认关闭）
    if selfcheck["failed"] > 0 and get_config().pydantic_config.runtime.integrity.block_startup_on_failure:
        raise RuntimeError(
            f"核心模块完整性自检失败 ({selfcheck['failed']} 项)，"
            "按 runtime.integrity.block_startup_on_failure 配置阻断启动"
        )

    # C1 整改：模型权重哈希校验（加载链路，默认关闭；清单缺失时不阻塞）
    try:
        from .model_manager import verify_model_integrity

        mi = verify_model_integrity()
        if mi.get("enabled"):
            if mi.get("mismatched") or mi.get("missing"):
                logger.warning(
                    "[SECURITY] 模型权重哈希校验：不匹配 %s，缺失 %s",
                    mi.get("mismatched"),
                    mi.get("missing"),
                )
                if get_config().pydantic_config.runtime.integrity.block_on_model_mismatch:
                    raise RuntimeError(
                        f"模型权重哈希校验失败（不匹配 {mi.get('mismatched')}），"
                        "按 runtime.integrity.block_on_model_mismatch 配置阻断启动"
                    )
            else:
                logger.info("[SECURITY] 模型权重哈希校验通过（已检 %d 项）", mi.get("checked", 0))
    except RuntimeError:
        raise
    except Exception as e:
        logger.debug(f"模型权重哈希校验跳过: {e}")

    # H3 整改：PII 留存清理（启动时执行一次；默认保留 90 天）
    try:
        from .history_db import get_history_db

        retention = get_config().pydantic_config.security.pii_retention_days
        if retention > 0:
            deleted = get_history_db().purge_expired(retention)
            if deleted:
                logger.info("[lifespan] PII 留存清理删除 %d 条过期记录", deleted)
    except Exception as e:
        logger.debug(f"PII 留存清理跳过: {e}")

    try:
        from .history_db import get_history_db

        history_manager = get_history_db()
        await run_in_threadpool(history_manager.sync_from_filesystem)
        logger.info("[lifespan] 历史记录全量同步完成")
    except Exception as e:
        logger.exception(f"[lifespan] 历史记录全量同步失败: {e}")

    try:
        from .model_registry import load_engine_specs_from_config

        load_engine_specs_from_config()
        logger.info("[lifespan] 引擎规格加载完成")
    except Exception as e:
        logger.debug(f"[lifespan] 引擎规格加载失败（使用默认值）: {e}")

    try:
        # 此前这里写的是 `from .persona_manager import get_persona_manager`
        # + `pm.warmup_cache()`，但 persona_manager 是纯函数模块、根本没有这两个
        # 名字（旧设计遗留）。真实实现在 model_manager.warmup_persona_cache。
        # 后果：启动预热从未执行过，而失败只记 debug 日志、完全不可见。
        from .model_manager import warmup_persona_cache

        status = await run_in_threadpool(warmup_persona_cache)
        logger.info(f"[lifespan] Persona 缓存预热完成: {status}")
    except Exception as e:
        # 用 warning 而非 debug：预热失败意味着启动路径有问题（例如又出现幻影引用），
        # 埋在 debug 级别会让它在生产环境永久不可见。
        logger.warning(f"[lifespan] Persona 缓存预热跳过: {e}")

    try:
        from .monitor import get_health_monitor

        hm = get_health_monitor()
        hm.start_background(delay_seconds=30)
        logger.info("[lifespan] HealthMonitor 后台线程已启动")
    except Exception as e:
        logger.debug(f"[lifespan] HealthMonitor 启动跳过: {e}")

    # 初始化异步生成任务队列（参考 VoiceBox 串行队列设计）
    try:
        from .task_queue import init_queue

        await init_queue()
        logger.info("[lifespan] 异步生成任务队列已初始化")
    except Exception as e:
        logger.debug(f"[lifespan] 任务队列初始化失败（将使用信号量机制）: {e}")

    # P1-2: 断点续跑 — 启动时扫描未完成的 checkpoint 并尝试恢复（来源：Image_MultiModel）
    # 设计：
    #   - list_resumable() 顺带清理已完成/无效的 checkpoint 文件
    #   - 批量端点/批量脚本可通过 app.state.checkpoint_resume_handler 注册
    #     续跑处理器：callable(cp_dict) -> bool（是否成功恢复），支持同步或异步
    #   - 仅当 config.yaml runtime.task.auto_recover=true 时才真正续跑，
    #     否则只扫描记录（保持既有单任务行为不变）
    try:
        from .batch_inference import make_checkpoint_resume_handler
        from .checkpoint import TaskCheckpoint
        from .config import ROOT_DIR, get_config

        runtime_task = get_config().pydantic_config.runtime.task
        checkpoint_dir = os.path.join(ROOT_DIR, runtime_task.checkpoint_dir)
        checkpoint_mgr = TaskCheckpoint(checkpoint_dir=checkpoint_dir)
        pending = checkpoint_mgr.list_resumable()
        if pending:
            logger.info(f"[lifespan] 发现 {len(pending)} 个未完成的 checkpoint 可恢复")
            for cp in pending:
                logger.info(
                    f"  - task_id={cp.get('task_id')}, "
                    f"completed={cp.get('completed', 0)}/{cp.get('total', 0)}, "
                    f"remaining={len(cp.get('remaining', []))}, "
                    f"engine={cp.get('engine', '')}"
                )
        else:
            logger.debug("[lifespan] 无未完成的 checkpoint")
        app.state.checkpoint_mgr = checkpoint_mgr
        # 默认续跑处理器：批量端点/脚本通过 batch_inference.register_resume_inference_fn
        # 注册引擎推理函数后，auto_recover=true 时可自动续跑未完成 checkpoint。
        app.state.checkpoint_resume_handler = make_checkpoint_resume_handler(checkpoint_mgr)

        if runtime_task.auto_recover:
            handler = getattr(app.state, "checkpoint_resume_handler", None)
            if handler is None:
                logger.warning(
                    "[lifespan] auto_recover 已开启但未注册续跑处理器"
                    "（checkpoint_resume_handler），未完成任务将保留 checkpoint 待恢复"
                )
            else:
                resumed = 0
                for cp in pending:
                    try:
                        result = handler(cp)
                        if asyncio.iscoroutine(result):
                            result = await result
                        if result:
                            resumed += 1
                    except Exception as resume_err:
                        logger.warning(
                            "[lifespan] 恢复 checkpoint %s 失败: %s",
                            cp.get("task_id"),
                            resume_err,
                        )
                if resumed:
                    logger.info(f"[lifespan] 已从 checkpoint 恢复 {resumed} 个未完成任务")
    except Exception as e:
        logger.debug(f"[lifespan] Checkpoint 扫描跳过: {e}")

    auto_load = os.environ.get("TTS_AUTO_LOAD_MODEL", "0") == "1"
    if auto_load:
        auto_engine = os.environ.get("TTS_AUTO_LOAD_ENGINE", "voxcpm2")
        app.state.model_loading = True
        app.state.model_load_progress = "正在初始化..."

        def _load_in_background() -> None:
            from .middleware.request_id import set_request_id

            set_request_id(f"bg-{threading.current_thread().name}")

            async def _update_state(**kwargs: Any) -> None:
                for k, v in kwargs.items():
                    setattr(app.state, k, v)

            def _schedule_state_update(**kwargs: Any) -> None:
                loop = _event_loop
                if loop is not None and not loop.is_closed():
                    asyncio.run_coroutine_threadsafe(_update_state(**kwargs), loop)
                else:
                    for k, v in kwargs.items():
                        setattr(app.state, k, v)

            try:
                if auto_engine == EngineName.INDEXTTS2.value:
                    from .model_manager import load_indextts2

                    logger.info("[lifespan] 后台加载 IndexTTS 2.5 模型中...")
                    gen = load_indextts2()
                else:
                    from .model_manager import load_voxcpm2

                    logger.info("[lifespan] 后台加载 VoxCPM2 模型中...")
                    gen = load_voxcpm2()
                last_status = ""
                for status_text, _, _, _ in gen:
                    last_status = status_text
                    _schedule_state_update(model_load_progress=status_text)
                    logger.info(f"[lifespan] {status_text}")
                if "失败" in last_status or "error" in last_status.lower():
                    _schedule_state_update(
                        models_ok=False,
                        model_loading=False,
                        model_load_progress=last_status,
                    )
                    logger.error(f"[lifespan] {auto_engine} 加载失败: {last_status}")
                else:
                    _schedule_state_update(
                        models_ok=True,
                        model_loading=False,
                        model_load_progress="模型已就绪",
                    )
                    logger.info(f"[lifespan] {auto_engine} 模型已就绪，服务完全启动")
            except Exception:
                # Why try/except Exception + logger.exception 不抛出:
                # 模型加载失败（OOM / 文件损坏 / 权限不足）不应阻止整个应用启动，
                # 用户可以进入 Settings 页面手动重新加载模型。
                logger.exception(f"[lifespan] {auto_engine} 模型后台加载异常")
                _schedule_state_update(
                    models_ok=False,
                    model_loading=False,
                    model_load_progress="加载失败，请查看日志",
                )
                logger.info("[lifespan] 用户可通过界面手动加载模型")

        load_thread = threading.Thread(
            target=_load_in_background,
            daemon=True,
            name="model-startup-load",
        )
        load_thread.start()
        logger.info("[lifespan] 服务已启动，模型正在后台加载...")
    else:
        logger.info("[lifespan] 自动加载已禁用，请通过界面手动加载模型")

    # RB-2: 启动临时文件定期清理后台任务（每30分钟清理一次）
    _temp_cleanup_stop = asyncio.Event()

    async def _periodic_temp_cleanup() -> None:
        """定期清理过期临时文件，避免长时间运行后临时目录堆积。"""
        from .utils import cleanup_temp_files

        while not _temp_cleanup_stop.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(_temp_cleanup_stop.wait(), timeout=1800)  # 30分钟
            if _temp_cleanup_stop.is_set():
                break
            try:
                removed = await run_in_threadpool(cleanup_temp_files)
                if removed > 0:
                    logger.info(f"[periodic-cleanup] 定期清理完成，删除 {removed} 个过期临时文件")
            except Exception:
                logger.debug("[periodic-cleanup] 定期清理异常（忽略）", exc_info=True)

    _cleanup_task = asyncio.create_task(_periodic_temp_cleanup())
    logger.info("[lifespan] 临时文件定期清理任务已启动（30分钟间隔）")

    yield

    logger.info("[lifespan] Shutdown 阶段开始")

    # 停止定期清理任务
    _temp_cleanup_stop.set()
    try:
        await asyncio.wait_for(_cleanup_task, timeout=5)
    except (asyncio.TimeoutError, Exception):
        _cleanup_task.cancel()

    try:
        from .model_manager import unload_all_models

        logger.info("[lifespan] 正在卸载所有模型...")
        unload_all_models()
        logger.info("[lifespan] 所有模型已卸载")
    except Exception:
        logger.exception("[lifespan] 模型卸载过程出现异常")

    try:
        from .history_db import get_history_db

        history_manager = get_history_db()
        history_manager.close_all()
        logger.info("[lifespan] 历史记录数据库连接池已关闭")
    except Exception:
        logger.exception("[lifespan] 历史记录数据库关闭异常")

    try:
        from .utils import cleanup_temp_files

        removed = cleanup_temp_files()
        logger.info(f"[lifespan] 临时文件清理完成，删除 {removed} 个文件")
    except Exception:
        logger.exception("[lifespan] 临时文件清理异常")

    # 关闭异步生成任务队列
    try:
        from .task_queue import shutdown_queue

        await shutdown_queue()
        logger.info("[lifespan] 异步生成任务队列已关闭")
    except Exception:
        logger.debug("[lifespan] 任务队列关闭异常")

    _set_event_loop(None)
    logger.info("[lifespan] Shutdown 阶段完成")


def create_app() -> FastAPI:
    """创建并返回配置完整的 FastAPI 应用实例。

    中间件注册顺序（按 ASGI 调用栈从外到内，请求先经过先注册的）：
        1. RequestIDMiddleware：最早注入 request_id，后续所有 logger/异常处理
           都能读取到 request_id（Why：CSRF/APIAuth/error_handler 日志中都要
           带 request_id 做链路追踪，所以必须在最外层）
        2. CORSMiddleware：处理跨域预检 OPTIONS，放行后才进入业务中间件
        3. CSRFMiddleware：在 CORS 之后（Why：CORS 预检 OPTIONS 请求不携带
           CSRF Token，如果 CSRF 在 CORS 之前会拦截 OPTIONS 导致跨域失败）
        4. APIAuthMiddleware：在 CSRF 之后，对程序化 Bearer Token 调用做认证
        5. error_handler（通过 add_exception_handler 注册）：最内层捕获异常

    路由挂载：
        - ``/static``：CachedStaticFiles 静态资源（带 Cache-Control）
        - ``/api/persona``、``/api/model``、``/api/generate/*`` 等：
          通过 ``_auto_discover_routers`` 自动发现并 include_router

    Raises:
        无：模板目录不存在时会回退到最小模板，不会抛出异常。

    Returns:
        配置完成的 FastAPI 实例。
    """
    from .config import get_config

    app = FastAPI(
        title="TTS MultiModel Voice Studio",
        description="多模型语音合成平台，支持 VoxCPM2 和 IndexTTS2 引擎",
        version=get_config().version,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # --- 异常处理器：注册顺序不敏感，按 Exception 子类匹配 ---
    app.add_exception_handler(TTSError, tts_error_handler)
    app.add_exception_handler(ValidationError, validation_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)

    # --- 中间件注册顺序（重要！先注册的先处理请求）---
    # Why RequestIDMiddleware 必须是第一个：
    #   后续 CSRF、APIAuth、路由 handler、异常 handler 中的所有 logger
    #   都依赖 RequestIDLogFilter 读取 request_id。如果 RequestID 注入晚了，
    #   前面中间件的日志会丢失 request_id，链路追踪断链。
    app.add_middleware(RequestIDMiddleware)

    # CORS 在 CSRF 之前：CORS 预检 OPTIONS 不携带 CSRF Token，
    # 必须先由 CORSMiddleware 放行 OPTIONS，否则 CSRF 会拦截预检请求。
    cors_origins_str = os.environ.get("TTS_CORS_ORIGINS", "")
    if cors_origins_str:
        cors_origins = [o.strip() for o in cors_origins_str.split(",") if o.strip()]
    else:
        cors_origins = [
            "http://127.0.0.1",
            "http://localhost",
            "http://127.0.0.1:7869",
            "http://localhost:7869",
            # 安全评估报告 L1：已移除无效/误导性 origin ``http://0.0.0.0:7869``。
            # 0.0.0.0 不是合法浏览器来源，且 `allow_credentials=True` 下保留它
            # 会误导运维以为「监听 0.0.0.0 也能被 CORS 放行」，与「禁止 0.0.0.0
            # 监听」红线（security-assertions CI）精神冲突。
            "http://host.docker.internal:7869",
        ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-CSRF-Token", "HX-Request", "HX-Target", "HX-Trigger"],
    )

    # P2 安全修复：CSRF Cookie HMAC 签名 — 首次启动自动生成持久化密钥
    # 启用后 CSRF token cookie 将携带 HMAC-SHA256 签名，防止 XSS 注入伪造
    import secrets as _secrets

    csrf_secret_path = os.path.join(_PROJECT_ROOT, "data", ".csrf_secret")
    csrf_secret = ""  # nosec B105 - 占位初始化，随后立即被 secrets.token_urlsafe(48) 覆盖为强随机值
    try:
        os.makedirs(os.path.dirname(csrf_secret_path), exist_ok=True)
        if os.path.exists(csrf_secret_path):
            with open(csrf_secret_path, encoding="utf-8") as f:
                csrf_secret = f.read().strip()
        if not csrf_secret:
            csrf_secret = _secrets.token_urlsafe(48)
            with open(csrf_secret_path, "w", encoding="utf-8") as f:
                f.write(csrf_secret)
            logger.info("[create_app] 已生成新的 CSRF HMAC 密钥: %s", csrf_secret_path)
    except OSError as csrf_err:
        logger.warning("[create_app] CSRF 密钥初始化失败，回退到无签名模式: %s", csrf_err)

    app.add_middleware(CSRFMiddleware, secret_key=csrf_secret)

    api_auth = get_config().api_auth_dict
    app.add_middleware(
        APIAuthMiddleware,
        enabled=api_auth.get("enabled", False),
        token=api_auth.get("token", ""),
    )

    # P2 安全修复：API 速率限制 — 防止单 IP 狂发生成请求打爆 GPU（M2：配置外置 + 可信代理 XFF）
    rl_cfg = get_config().pydantic_config.rate_limit
    app.add_middleware(
        RateLimitMiddleware,
        enabled=rl_cfg.enabled,
        requests_per_minute=rl_cfg.requests_per_minute,
        burst=rl_cfg.burst,
        trusted_proxies=rl_cfg.trusted_proxies,
    )

    # --- 静态文件挂载：/static ---
    static_dir = os.path.join(_BASE_DIR, "static")
    os.makedirs(static_dir, exist_ok=True)
    app.mount("/static", CachedStaticFiles(directory=static_dir), name="static")

    setup_logging()

    # --- Jinja2 模板初始化，模板缺失时回退到最小模板 ---
    templates_dir = os.path.join(_BASE_DIR, "templates")
    os.makedirs(templates_dir, exist_ok=True)
    templates: Jinja2Templates | None = None
    try:
        templates = Jinja2Templates(directory=templates_dir)
        debug_mode = os.environ.get("TTS_DEBUG", "0") == "1"
        templates.env.auto_reload = debug_mode
        from .i18n import register_i18n_filters

        register_i18n_filters(templates.env)
    except Exception as e:
        logger.exception(f"[create_app] 模板环境初始化失败: {e}")
        templates = None

    if templates is None:
        try:
            from jinja2 import DictLoader, Environment

            minimal_templates = {
                "download_guide.html": """
<html><body><h1>TTS_MultiModel 下载引导</h1>
<p>缺失模型文件，请参照文档放置模型。</p></body></html>
""",
            }
            env = Environment(loader=DictLoader(minimal_templates), autoescape=True)
            templates = Jinja2Templates(env=env)
            templates.env.globals["app_version"] = "fallback"
            logger.error("[create_app] 模板目录不可用，已回退到最小内置模板")
        except Exception:
            logger.exception("[create_app] 最小模板回退也失败，应用将无模板支持")

    if templates is not None:
        app_version = getattr(app.state, "version", None) or os.environ.get("TTS_APP_VERSION")
        if not app_version:
            app_version = str(int(time.time()))
        templates.env.globals["app_version"] = app_version
        app.state.templates = templates

    # --- 轻量级健康检查端点 ---
    @app.get("/api/health/ping")
    async def health_ping() -> dict[str, Any]:
        """Quick liveness probe -- returns 200 if the server is running.

        Returns:
            包含 status / timestamp / version 的健康心跳响应。
        """
        return {
            "status": "ok",
            "timestamp": time.time(),
            "version": getattr(app.state, "version", "unknown"),
            "attribution": "TTS_MultiModel © ReSerendipity, Apache 2.0",
        }

    @app.get("/api/health/ready")
    async def health_ready() -> dict[str, Any]:
        """Readiness 探针 —— 统一委托给 routes/system/health.ready（深度检查，避免双探针语义分歧）。"""
        from .routes.system.health import ready as system_ready

        return await system_ready()

    @app.get("/readyz")
    async def readyz() -> dict[str, Any]:
        """k8s 风格 readiness 探针别名（与 /api/health/ready 同语义）。"""
        from .routes.system.health import ready as system_ready

        return await system_ready()

    # --- 自动发现并挂载路由，单个模块失败不影响其他路由 ---
    from . import routes

    route_modules = _discover_routes(".routes")
    for mod_name in route_modules:
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, "router"):
                app.include_router(mod.router)
        except Exception as e:
            logger.warning(f"[create_app] 路由模块 {mod_name} 导入失败: {e}")
            continue

    # 兼容旧的 _auto_discover_routers 逻辑（兜底）
    legacy_routers = _auto_discover_routers(routes)
    for r in legacy_routers:
        already_mounted = False
        for existing in app.routes:
            if getattr(existing, "path_prefix", None) == getattr(r, "prefix", None):
                already_mounted = True
                break
        if not already_mounted:
            app.include_router(r)

    # OpenAI 兼容 API 路由挂载（/v1/* 端点）
    try:
        from .openai_api import openai_router

        app.include_router(openai_router.router)
        logger.info("[create_app] OpenAI 兼容 API 路由已挂载 (/v1/*)")
    except Exception as e:
        logger.warning(f"[create_app] OpenAI 兼容 API 路由挂载失败: {e}")

    return app


def run_server(ip: str = "127.0.0.1", port: int = 7869) -> None:
    """启动 uvicorn 服务器。

    Args:
        ip: 监听地址，默认 ``127.0.0.1``。
        port: 监听端口，默认 ``7869``。
    """
    from .config import check_models_available, force_load_config, get_config

    force_load_config()

    # P1 安全修复：非本地绑定且未启用 API Auth 时拒绝启动（安全网）
    # 防止用户在 0.0.0.0 等对外地址上无认证暴露所有 /api/* 接口
    _LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
    if ip not in _LOCAL_HOSTS:
        auth_cfg = get_config().api_auth_dict
        if not auth_cfg.get("enabled") or not auth_cfg.get("token"):
            logger.error(
                "[run_server] 安全网拦截：监听地址 %s 为非本地绑定，"
                "但 API 认证未启用（api_auth.enabled=false 或 token 为空）。"
                "请在 config.yaml 中设置 api_auth.enabled=true 和一个安全的 token，"
                "或仅使用 127.0.0.1 本地绑定。拒绝启动以防止未授权访问。",
                ip,
            )
            raise SystemExit(1)

    app = create_app()
    models_ok, missing = check_models_available()
    app.state.models_ok = models_ok
    app.state.missing_models = missing
    version = get_config().version
    app.state.version = version

    # 即使模型文件缺失，也正常启动应用，让用户可以通过界面加载模型
    # 前端会通过 /api/model/status 检查模型状态并显示相应提示
    if not models_ok:
        logger.warning(f"[run_server] 模型文件不完整: {missing}")
        logger.warning("[run_server] 应用正常启动，用户可通过界面加载模型")

    # P1: 启动时输出归属信息（增加品牌化剥离成本）
    logger.info(
        "[run_server] TTS_MultiModel v%s © ReSerendipity, Apache 2.0 | "
        "Official: https://github.com/ReSerendipity/TTS_MultiModel",
        version,
    )

    # H2 整改：SSL 接线 —— 当 config 配置且证书文件存在时启用 HTTPS（uvicorn ssl 上下文）
    ssl_kwargs: dict[str, str] = {}
    _ssl = get_config().pydantic_config.server.ssl
    if _ssl.certfile and _ssl.keyfile and os.path.exists(_ssl.certfile) and os.path.exists(_ssl.keyfile):
        ssl_kwargs = {"ssl_certfile": _ssl.certfile, "ssl_keyfile": _ssl.keyfile}
        logger.info("[run_server] 已启用 HTTPS (SSL): cert=%s", _ssl.certfile)
    else:
        logger.info("[run_server] 以 HTTP 模式运行（对外暴露请通过反向代理终止 TLS）")
    uvicorn.run(app, host=ip, port=int(port), **ssl_kwargs)


if __name__ == "__main__":
    """python -m integrated_app.app_server --host 127.0.0.1 --port 7869"""
    import argparse

    parser = argparse.ArgumentParser(description="TTS MultiModel server")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=7869, help="监听端口")
    args = parser.parse_args()
    run_server(ip=args.host, port=args.port)
