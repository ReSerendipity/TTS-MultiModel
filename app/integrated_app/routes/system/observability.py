"""可观测性 HTTP 路由（SRE 评估 P0~P2 落地）。

路径前缀：``/api/system``
接口清单：
    * ``GET /api/system/slo``      — SLO/SLI 报告（目标/实际值/错误预算）
    * ``GET /api/system/capacity`` — 最近容量采样（GPU/CPU/内存时序）

Why 单独成文件而非塞进 health.py：
    可观测性是一个独立关注点，且本文件依赖 observability 子包；与
    health.py 的健康语义解耦，便于独立演进与测试。
注意：Prometheus 指标端点 ``GET /api/system/metrics`` 由 ``routes/system/metrics.py``
    统一提供（它复用 ``observability.metrics.build_metrics_text``），避免重复注册。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger("tts_multimodel")

router = APIRouter(prefix="/api/system", tags=["system", "observability"])


@router.get("/slo", summary="SLO/SLI 报告", description="返回 SLO 目标、SLI 实际值与错误预算消耗")
def slo_endpoint() -> dict[str, Any]:
    """返回 SLO 报告。"""
    from ...observability.slo import get_slo_report

    return get_slo_report()


@router.get("/capacity", summary="容量采样", description="返回最近 GPU/CPU/内存容量采样时序")
def capacity_endpoint(limit: int = 60) -> dict[str, Any]:
    """返回容量采样历史。

    Args:
        limit: 返回最近 N 条采样，默认 60。

    Returns:
        dict: 含 ``latest`` 与 ``history``（受 limit 限制）。
    """
    from ...observability.capacity_sampler import get_capacity_history, get_capacity_latest

    return {
        "latest": get_capacity_latest(),
        "history": get_capacity_history(limit=limit),
        "count": len(get_capacity_history()),
    }
