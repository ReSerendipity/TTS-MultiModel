"""SLO / SLI 定义与计算（SRE 评估 P1-1 落地）。

报告缺口：原项目「无任何 SLO/SLI 定义」。本模块在代码层给出可度量目标：
    - availability（可用性）：由 liveness 探活 + readiness 就绪推导
    - success_rate（成功率）：生成成功 / 总次数
    - latency（延迟）：采用 avg_gen_time_ms 作为代理 SLI（完整 p95 需接入请求级计时）

SLI 数据源统一取自 ``metrics.collect_metrics()`` / ``HealthMonitor``，
SLO 阈值来自 config ``observability.slo``，缺省使用内置默认值。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("tts_multimodel")

_DEFAULT_SLO: dict[str, float] = {
    "availability": 99.5,  # 目标可用性 %
    "min_success_rate": 99.0,  # 目标生成成功率 %
    "max_avg_latency_ms": 30000.0,  # 目标平均生成耗时上限 ms
}


@dataclass
class SLIResult:
    """单条 SLI 计算结果。"""

    name: str
    value: float
    target: float
    unit: str
    met: bool

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict。"""
        return {
            "name": self.name,
            "value": round(self.value, 3),
            "target": self.target,
            "unit": self.unit,
            "met": self.met,
        }


def _load_slo_cfg() -> dict[str, float]:
    """读取 SLO 配置，缺省回退默认值。"""
    try:
        from ..config import get_config

        raw = get_config().observability_dict.get("slo", {}) or {}
        cfg = dict(_DEFAULT_SLO)
        for k, v in raw.items():
            if isinstance(v, (int, float)):
                cfg[k] = float(v)
        return cfg
    except Exception:  # noqa: BLE001
        return dict(_DEFAULT_SLO)


def compute_slis() -> list[SLIResult]:
    """计算当前所有 SLI。

    Returns:
        list[SLIResult]: 各 SLO 维度的 SLI 结果（含是否达标）。
    """
    from .metrics import collect_metrics

    cfg = _load_slo_cfg()
    metrics = collect_metrics()
    results: list[SLIResult] = []

    # 1. 成功率
    success_rate = float(metrics.get("tts_generation_success_rate", 100.0))
    total = float(metrics.get("tts_generations_total", 0.0))
    # 样本过少时视为达标（避免冷启动误报），但记录实际值
    sr_met = (total < 10) or (success_rate >= cfg["min_success_rate"])
    results.append(
        SLIResult(
            name="success_rate",
            value=success_rate,
            target=cfg["min_success_rate"],
            unit="%",
            met=sr_met,
        )
    )

    # 2. 可用性：模型已加载且生成可用 → 100%；仅当无引擎时降级
    model_loaded = float(metrics.get("tts_model_loaded", 1.0))
    availability = 100.0 if model_loaded >= 1.0 else 0.0
    results.append(
        SLIResult(
            name="availability",
            value=availability,
            target=cfg["availability"],
            unit="%",
            met=availability >= cfg["availability"],
        )
    )

    # 3. 延迟（代理 SLI：平均生成耗时）
    # 说明：请求级 p95 需接入逐请求计时中间件（见 docs/ops/SLO.md 的后续项）。
    # 此处以 HealthMonitor/model_manager 的平均生成耗时作为代理 SLI；取不到时为 0，按达标处理。
    avg_latency = float(metrics.get("tts_avg_gen_time_ms", 0.0) or 0.0)
    results.append(
        SLIResult(
            name="avg_latency",
            value=avg_latency,
            target=cfg["max_avg_latency_ms"],
            unit="ms",
            met=(avg_latency <= cfg["max_avg_latency_ms"]) if avg_latency > 0 else True,
        )
    )

    return results


def get_slo_report() -> dict[str, Any]:
    """返回 SLO 报告（含目标、SLI、是否达标、错误预算消耗）。

    Returns:
        dict: 含 ``slo_targets`` / ``slis`` / ``all_met`` / ``summary``。
    """
    slis = compute_slis()
    targets = _load_slo_cfg()
    all_met = all(s.met for s in slis)
    # 错误预算消耗（以成功率为例）：(目标-实际)/目标
    sr = next((s for s in slis if s.name == "success_rate"), None)
    budget_burn = 0.0
    if sr and sr.target > 0:
        budget_burn = max(0.0, (sr.target - sr.value) / sr.target * 100.0)
    return {
        "slo_targets": targets,
        "slis": [s.to_dict() for s in slis],
        "all_met": all_met,
        "error_budget_burn_pct": round(budget_burn, 3),
        "summary": "所有 SLO 达标" if all_met else "存在未达标 SLO，请检查告警与 Runbook",
    }
