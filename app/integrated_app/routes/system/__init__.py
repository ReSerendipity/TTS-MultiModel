"""系统管理 API 路由包。

本模块聚合所有系统级管理接口，路径前缀为 ``/api/system``，主要供 WebUI
Settings 面板、k8s 健康探针、运维监控使用。

包含子模块：
    - :mod:`health` — 健康检查（liveness/readiness）、生成统计、GPU 泄漏检测、队列状态、优雅关闭
    - :mod:`gpu` — GPU 实时状态查询、显存历史曲线、手动显存清理
    - :mod:`logs` — 操作日志查询与清理（内存环形缓冲区 + SQLite 持久化双通道）
    - :mod:`settings` — 运行时配置读写（config.yaml 原子持久化 + 热更新）

对外暴露：
    - ``router`` — 空的命名空间 APIRouter（子路由由 app_server 自动发现逐个挂载）
    - ``increment_generation()`` / ``get_generation_stats()`` — 生成计数辅助
    - ``log_operation()`` / ``get_operation_log()`` — 操作日志写入与读取入口

设计说明：
    各子路由（health / gpu / logs / settings）各自带 ``prefix="/api/system"``，
    由上层 ``app_server._auto_discover_routers`` 递归遍历本包并**逐个**挂载，
    因此本文件的聚合 ``router`` 不再 ``include_router`` 子路由（否则会产生
    ``/api/system/api/system/*`` 双前缀重复路由），仅作为兼容显式导入的空命名空间。
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

# 说明：本包的 router 故意保持为空，不再 include 子路由。
# 原因：app_server._auto_discover_routers 会递归遍历本包并逐个挂载
# health / gpu / logs / settings 各自的 router（它们已带 prefix="/api/system"）。
# 若这里再 include 一次，就会叠加出 /api/system/api/system/* 的双前缀重复路由。
# 保留 router 属性仅为兼容显式导入；实际注册以自动发现挂载的子路由为准。
router = APIRouter(prefix="/api/system", tags=["system"])

# 子路由与辅助函数在此包内再导出，供显式 ``from .system import xxx`` 使用
# （登记进 __all__ 表明为有意再导出，避免 ruff F401 误判未使用）。
__all__ = [
    "router",
    "health_router",
    "gpu_router",
    "logs_router",
    "settings_router",
    "increment_generation",
    "get_generation_stats",
    "log_operation",
    "get_operation_log",
]
