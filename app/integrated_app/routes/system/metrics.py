# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""监控指标 Prometheus 端点（MLOps 治理 P3-2）。

路径前缀：``/api/system``
接口清单：
  * ``GET /api/system/metrics`` — Prometheus 文本格式（exposition format）指标，
    供 Alertmanager / Grafana / Prometheus 抓取。

设计约束（AGENTS.md 硬约束 #5 离线工作）：
  - **零第三方依赖**：不引入 prometheus-client，直接输出 Prometheus 文本格式
    （纯字符串拼接，符合 exposition format 规范）。
  - 实时健康/资源指标由 ``monitor.py`` / ``tracker.py`` 计算；本端点负责聚合与暴露。
  - 与评估报告第 6 节伪代码对应：
        if vram_usage > 95% for > 10min  -> tts_vram_used_mb（monitor.py 采集，此处暴露）
        if request_queue_depth > 100     -> tts_queue_depth（tracker.py 采集，此处暴露）
        if audio_quality_score < 0.8     -> 待 P2-7 质量分上线后补充

注意：当前暴露的是「累计/聚合」类指标（生成次数、降级数、平均 RTF）。
「时间维度」类告警（持续 N 分钟）由 Alertmanager 在抓取后基于 rate() 实现，
本端点只负责产出可被抓取的时间序列。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from ..security.audit import get_recent_audit

logger = logging.getLogger("tts_multimodel")

router = APIRouter(prefix="/api/system", tags=["system"])


def _build_metrics_text() -> str:
    """聚合监控指标，输出 Prometheus 文本格式。

    延迟导入 history_db 以避免应用启动期的循环依赖与重路径初始化。

    Returns:
        Prometheus exposition format 文本（以 ``\\n`` 结尾）。
    """
    from ..history_db import get_history_db

    lines: list[str] = []
    # 进程级存活（liveness）
    lines.append("# HELP tts_up 服务是否存活（1=是）")
    lines.append("# TYPE tts_up gauge")
    lines.append("tts_up 1")

    # 历史库聚合指标（生成质量 / 退化趋势）
    total = degraded = 0
    avg_rtf = 0.0
    try:
        db = get_history_db()
        with db.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(is_degraded), 0), COALESCE(AVG(rtf), 0.0) FROM generation_history"
            ).fetchone()
        total = int(row[0] or 0)
        degraded = int(row[1] or 0)
        avg_rtf = float(row[2] or 0.0)
    except Exception as e:  # noqa: BLE001
        logger.warning("metrics 聚合失败（返回零值）: %s", e)

    lines.append("# HELP tts_gen_total 累计生成次数")
    lines.append("# TYPE tts_gen_total counter")
    lines.append(f"tts_gen_total {total}")

    lines.append("# HELP tts_degraded_total 降级生成次数（P2-7 质量趋势监控）")
    lines.append("# TYPE tts_degraded_total counter")
    lines.append(f"tts_degraded_total {degraded}")

    lines.append("# HELP tts_rtf_avg 平均实时率 = 生成耗时/音频时长（<1 为实时）")
    lines.append("# TYPE tts_rtf_avg gauge")
    lines.append(f"tts_rtf_avg {avg_rtf:.4f}")

    return "\n".join(lines) + "\n"


@router.get("/metrics")
async def metrics() -> PlainTextResponse:
    """Prometheus 文本格式指标端点（零依赖，可被抓取）。

    Returns:
        ``text/plain; version=0.0.4`` 的 exposition format 文本。
    """
    return PlainTextResponse(_build_metrics_text(), media_type="text/plain; version=0.0.4")


@router.get("/audit")
async def audit_events(limit: int = 100) -> dict:
    """M7：返回最近的结构化安全审计事件（内存环，最多 500 条）。

    端点位于 /api/system 下，与其他 /api/* 一致受 APIAuth Bearer Token 保护。
    ``audit_enabled=False`` 时不落盘，但内存环仍可用，便于实时回溯。

    Args:
        limit: 返回条数上限（1~500）。

    Returns:
        ``{"status": "ok", "events": [...]}``。
    """
    limit = max(1, min(int(limit), 500))
    return {"status": "ok", "events": get_recent_audit(limit)}
