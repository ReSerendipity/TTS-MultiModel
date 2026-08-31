"""告警抽象与通道（SRE 评估 P0-2 落地）。

设计目标：将「异常事件」与「通知渠道」解耦，使 MTTD 从「用户投诉」提升到
「分钟级主动发现」，且不依赖任何外部 SaaS。

通道（Channel）：
    - LogAlertChannel：默认启用，将告警写入应用日志（severity>=WARNING 可见）。
    - WebhookAlertChannel：可选，按 config 中的 ``observability.alerting.webhook_url``
      将告警 POST 到企业微信/飞书/Slack 兼容的 incoming webhook。

规则（Rules）：提供 ``evaluate_rules(metrics)`` 对 HealthMonitor 指标做阈值判定，
覆盖报告指出的关键盲区：熔断触发、显存泄漏、错误率过高、GPU 不可用。

降级（fail-open）：任何通道发送失败都只记日志、不影响主流程。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("tts_multimodel")


class AlertSeverity(str, Enum):
    """告警严重级别。"""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Alert:
    """一条告警。

    Attributes:
        severity: 严重程度。
        title: 简短标题。
        detail: 详细描述（可选，含诊断数值）。
        source: 触发来源（如 ``vram_circuit_breaker``）。
        labels: 附加标签（用于聚合/路由）。
    """

    severity: AlertSeverity
    title: str
    detail: str = ""
    source: str = "system"
    labels: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict。"""
        return {
            "severity": self.severity.value,
            "title": self.title,
            "detail": self.detail,
            "source": self.source,
            "labels": self.labels,
        }


class AlertChannel:
    """告警通道抽象基类。"""

    def send(self, alert: Alert) -> None:
        """发送告警（子类实现）。

        Args:
            alert: 待发送的告警。
        """
        raise NotImplementedError


class LogAlertChannel(AlertChannel):
    """将告警写入应用日志（默认通道，始终启用）。"""

    def send(self, alert: Alert) -> None:
        """按 severity 映射到 logging 级别输出。"""
        msg = f"[ALERT][{alert.severity.value}][{alert.source}] {alert.title}"
        if alert.detail:
            msg += f" :: {alert.detail}"
        if alert.severity == AlertSeverity.CRITICAL:
            logger.error(msg)
        elif alert.severity == AlertSeverity.WARNING:
            logger.warning(msg)
        else:
            logger.info(msg)


class WebhookAlertChannel(AlertChannel):
    """通过 incoming webhook 发送告警（可选通道）。

    兼容企业微信/飞书/Slack 的 text/markdown 形态：以 JSON ``{"text": ...}``
    投递，多数 webhook 网关可接收；若需自定义模板，可继承重写 ``_payload``。
    """

    def __init__(self, url: str, timeout: float = 5.0) -> None:
        """初始化 webhook 通道。

        Args:
            url: webhook 地址。
            timeout: 请求超时（秒）。
        """
        self.url = url
        self.timeout = timeout

    def _payload(self, alert: Alert) -> dict[str, Any]:
        """构造投递负载。"""
        text = f"[{alert.severity.value.upper()}] {alert.title}"
        if alert.detail:
            text += f"\n{alert.detail}"
        return {"text": text}

    def send(self, alert: Alert) -> None:
        """POST 告警到 webhook（失败静默降级）。"""
        try:
            import urllib.request

            data = json.dumps(self._payload(alert)).encode("utf-8")
            req = urllib.request.Request(
                self.url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - URL 来自受信配置
                if resp.status >= 400:
                    logger.warning("[alerting] webhook 返回 %s", resp.status)
        except Exception as exc:  # noqa: BLE001 — 通道失败绝不阻断主流程
            logger.debug("[alerting] webhook 发送失败（已忽略）: %s", exc)


class AlertManager:
    """告警管理器单例。

    负责：注册通道、去重（同一 source 短时间内不重复发）、计数（供 /metrics）。
    """

    def __init__(self) -> None:
        """初始化告警管理器，默认仅启用 Log 通道。"""
        self._channels: list[AlertChannel] = [LogAlertChannel()]
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
        # 去重：source -> 上次发送时间戳
        self._last_sent: dict[str, float] = {}
        self._dedup_window: float = 300.0  # 5 分钟内同 source 不重复发
        self._enabled = True

    def configure(self, cfg: dict[str, Any] | None = None) -> None:
        """根据配置启用可选通道（如 webhook）。

        Args:
            cfg: ``observability.alerting`` 配置段。
        """
        cfg = cfg or {}
        if not isinstance(cfg, dict):
            return
        if cfg.get("enabled") is False:
            self._enabled = False
        webhook = cfg.get("webhook_url") or ""
        if webhook:
            with self._lock:
                # 避免重复添加
                if not any(isinstance(c, WebhookAlertChannel) for c in self._channels):
                    self._channels.append(WebhookAlertChannel(webhook))

    def emit(self, alert: Alert, force: bool = False) -> bool:
        """发出一条告警。

        Args:
            alert: 告警对象。
            force: 跳过去重强制发送。

        Returns:
            bool: 是否实际发送（去重命中返回 False）。
        """
        if not self._enabled:
            return False
        now = time.time()
        if not force:
            last = self._last_sent.get(alert.source)
            if last is not None and (now - last) < self._dedup_window:
                return False
        with self._lock:
            self._last_sent[alert.source] = now
            self._counts[alert.severity.value] = self._counts.get(alert.severity.value, 0) + 1
            for ch in self._channels:
                try:
                    ch.send(alert)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[alerting] 通道发送异常: %s", exc)
        return True

    def alert_counts(self) -> dict[str, int]:
        """返回各 severity 累计告警数。"""
        with self._lock:
            return dict(self._counts)


# --- 单例 ---
_alert_manager: AlertManager | None = None


def get_alert_manager() -> AlertManager:
    """获取全局告警管理器单例，并按配置惰性初始化 webhook 通道。"""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
        try:
            from ..config import get_config

            _alert_manager.configure(get_config().observability_dict.get("alerting", {}))
        except Exception:  # noqa: BLE001
            pass
    return _alert_manager


def evaluate_rules(metrics: dict[str, Any], cfg: dict[str, Any] | None = None) -> list[Alert]:
    """根据阈值规则对指标做判定，返回应触发的告警列表（不发，仅判定）。

    Args:
        metrics: ``metrics.collect_metrics()`` 产出的结构化指标。
        cfg: ``observability.slo`` / ``alerting`` 配置段（可选，提供阈值）。

    Returns:
        list[Alert]: 需要发出的告警（调用方自行 ``emit`` 以走去重）。
    """
    cfg = cfg or {}
    alerts: list[Alert] = []

    # 1. 显存熔断累积
    trips = metrics.get("tts_circuit_breaker_trips_total", 0.0)
    if trips and trips > 0:
        alerts.append(
            Alert(
                severity=AlertSeverity.WARNING,
                title="显存熔断已触发",
                detail=f"累计熔断 {int(trips)} 次，GPU 显存曾触及 90% 阈值",
                source="vram_circuit_breaker",
                labels={"rule": "circuit_breaker_trips"},
            )
        )

    # 2. 错误率过高
    success_rate = metrics.get("tts_generation_success_rate", 100.0)
    threshold = float(cfg.get("min_success_rate", 95.0))
    total = metrics.get("tts_generations_total", 0.0)
    if total >= 10 and success_rate < threshold:
        alerts.append(
            Alert(
                severity=AlertSeverity.CRITICAL,
                title="生成成功率低于 SLO",
                detail=f"成功率 {success_rate:.1f}% < 阈值 {threshold:.1f}%（总次数 {int(total)}）",
                source="success_rate_slo",
                labels={"rule": "min_success_rate"},
            )
        )

    # 3. 显存占用过高（接近熔断）
    vram_pct = metrics.get("tts_vram_usage_percent", 0.0)
    vram_warn = float(cfg.get("vram_usage_warn_pct", 85.0))
    if vram_pct >= vram_warn:
        alerts.append(
            Alert(
                severity=AlertSeverity.WARNING,
                title="GPU 显存占用偏高",
                detail=f"显存占用 {vram_pct:.1f}% >= 预警 {vram_warn:.1f}%",
                source="vram_usage",
                labels={"rule": "vram_usage_warn"},
            )
        )

    # 4. 模型未加载（ readiness 维度）
    model_loaded = metrics.get("tts_model_loaded", 1.0)
    if model_loaded == 0.0:
        alerts.append(
            Alert(
                severity=AlertSeverity.WARNING,
                title="无可用推理引擎",
                detail="registry 当前无就绪引擎，/health/ready 将返回 degraded",
                source="model_not_loaded",
                labels={"rule": "model_loaded"},
            )
        )

    return alerts
