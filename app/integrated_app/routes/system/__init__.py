"""系统管理 API 路由包。

本模块聚合所有系统级管理接口，路径前缀为 ``/api/system``，主要供 WebUI
Settings 面板、k8s 健康探针、运维监控使用。

包含子模块：
    - :mod:`health` — 健康检查（liveness/readiness）、生成统计、GPU 泄漏检测、队列状态、优雅关闭
    - :mod:`gpu` — GPU 实时状态查询、显存历史曲线、手动显存清理
    - :mod:`logs` — 操作日志查询与清理（内存环形缓冲区 + SQLite 持久化双通道）
    - :mod:`settings` — 运行时配置读写（config.yaml 原子持久化 + 热更新）

对外暴露：
    - ``router`` — 预聚合的 FastAPI APIRouter 实例，自动包含上述四个子路由
    - ``increment_generation()`` / ``get_generation_stats()`` — 生成计数辅助
    - ``log_operation()`` / ``get_operation_log()`` — 操作日志写入与读取入口

设计说明：
    各子路由均使用相同前缀 ``/api/system``，在本文件统一 ``include_router``。
    这样上层 ``app_server`` 只需挂载本包的 ``router`` 即可完成全部系统接口注册，
    无需逐一导入子模块。
"""

from fastapi import APIRouter

from .gpu import router as gpu_router
from .health import get_generation_stats as get_generation_stats
from .health import increment_generation as increment_generation
from .health import router as health_router
from .logs import get_operation_log as get_operation_log
from .logs import log_operation as log_operation
from .logs import router as logs_router
from .settings import router as settings_router

router = APIRouter(prefix="/api/system", tags=["system"])
router.include_router(health_router)
router.include_router(gpu_router)
router.include_router(logs_router)
router.include_router(settings_router)
