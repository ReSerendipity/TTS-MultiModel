# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""监控指标 Prometheus 端点（MLOps 治理 P3-2）。

路径前缀：``/api/system``
接口清单：
  * ``GET /api/system/metrics`` — Prometheus 文本格式（exposition format）指标，
    供 Alertmanager / Grafana / Prometheus 抓取。

注意：``GET /api/system/audit`` 由 ``routes/system/health.py`` 提供；
``GET /api/system/slo`` / ``/capacity`` 由 ``routes/system/observability.py`` 提供。
本模块仅负责 ``/metrics`` 一个端点，避免 ``_discover_routes`` 自动发现时产生
重复路由歧义。

设计约束（AGENTS.md 硬约束 #5 离线工作）：
  - **零第三方依赖**：不引入 prometheus-client，直接输出 Prometheus 文本格式。
  - 指标聚合统一下沉到 ``observability.metrics.build_metrics_text``（结构化优先、
    覆盖 HealthMonitor / 历史库 / 告警计数等多数据源），本端点只负责 HTTP 封装，
    避免「聚合逻辑散落两处」的反模式。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from ...observability.metrics import build_metrics_text

logger = logging.getLogger("tts_multimodel")

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/metrics")
async def metrics() -> PlainTextResponse:
    """Prometheus 文本格式指标端点（零依赖，可被抓取）。

    Returns:
        ``text/plain; version=0.0.4`` 的 exposition format 文本。
    """
    try:
        text = build_metrics_text()
    except Exception as exc:  # noqa: BLE001 — 指标端点绝不能因聚合异常而 500
        logger.warning("metrics 聚合失败（返回最小可用内容）: %s", exc)
        text = "# HELP tts_up 服务是否存活（1=是）\n# TYPE tts_up gauge\ntts_up 1\n"
    return PlainTextResponse(text, media_type="text/plain; version=0.0.4")
