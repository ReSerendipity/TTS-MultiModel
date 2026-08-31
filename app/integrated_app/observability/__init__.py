"""可观测性子包（SRE 评估 P0~P2 落地）。

集中收纳指标导出、告警、SLO、容量采样等运维能力，统一从 ``app.integrated_app.monitor``
的 ``HealthMonitor`` 单例读取事实源。设计原则（AGENTS.md 硬约束 #5 离线优先）：
- 不引入任何外部服务强依赖（Prometheus 文本格式手写，无需 ``prometheus_client``）。
- 所有能力本地可运行；外部 exporter / webhook 仅作为可选增强通道。

子模块：
    metrics.py          生成 Prometheus 文本格式指标（零依赖）
    alerting.py         告警抽象（log / webhook 通道）+ 阈值规则
    slo.py              SLO/SLI 定义与计算
    capacity_sampler.py 持续容量采样后台任务
"""

from .alerting import (
    Alert,
    AlertManager,
    AlertSeverity,
    get_alert_manager,
)
from .metrics import build_metrics_text

__all__ = [
    "Alert",
    "AlertManager",
    "AlertSeverity",
    "get_alert_manager",
    "build_metrics_text",
]
