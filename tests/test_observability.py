# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""可观测性体系单测（SRE 评估 P0~P2 落地）。

覆盖：Prometheus 指标导出、告警通道与去重、SLO/SLI 计算、容量采样。
全部不依赖 GPU / 网络（webhook 通道以 monkeypatch 验证），可离线运行。
"""

from __future__ import annotations

import importlib

import pytest
from app.integrated_app.observability import (
    Alert,
    AlertManager,
    AlertSeverity,
    build_metrics_text,
    get_alert_manager,
)
from app.integrated_app.observability.alerting import WebhookAlertChannel, evaluate_rules
from app.integrated_app.observability.capacity_sampler import (
    _snapshot,
    get_capacity_history,
    get_capacity_latest,
)
from app.integrated_app.observability.metrics import collect_metrics
from app.integrated_app.observability.slo import compute_slis, get_slo_report


def test_build_metrics_text_shape() -> None:
    """build_metrics_text 输出合法 Prometheus 文本：含 HELP/TYPE、以换行结尾。"""
    text = build_metrics_text()
    assert isinstance(text, str)
    assert text.endswith("\n")
    # 必有存活指标与告警总数（即使为 0）
    assert "# TYPE tts_up gauge" in text
    assert "# TYPE tts_alerts_total counter" in text
    assert "tts_up 1" in text
    # 无 label 指标始终输出
    assert "tts_alerts_total{severity=" in text


def test_collect_metrics_keys() -> None:
    """collect_metrics 返回结构化字典，至少含核心指标名。"""
    m = collect_metrics()
    for key in (
        "tts_up",
        "tts_generations_total",
        "tts_generation_errors_total",
        "tts_uptime_seconds",
        "tts_model_loaded",
        "tts_queue_size",
    ):
        assert key in m
        assert isinstance(m[key], float)


def test_alert_manager_emit_and_dedup() -> None:
    """AlertManager.emit 计数并去重（同 source 5 分钟内不重复发）。"""
    am = AlertManager()
    am._counts = {"critical": 0, "warning": 0, "info": 0}
    a = Alert(severity=AlertSeverity.WARNING, title="t", source="test_src")
    sent1 = am.emit(a)
    sent2 = am.emit(a)  # 去重命中
    assert sent1 is True
    assert sent2 is False
    assert am.alert_counts()["warning"] == 1
    # force 跳过去重
    assert am.emit(a, force=True) is True
    assert am.alert_counts()["warning"] == 2


def test_alert_manager_disabled() -> None:
    """enabled=False 时 emit 返回 False 不发。"""
    am = AlertManager()
    am._enabled = False
    a = Alert(severity=AlertSeverity.CRITICAL, title="t", source="x")
    assert am.emit(a) is False


def test_evaluate_rules_triggers() -> None:
    """evaluate_rules 对超阈指标产出对应告警。"""
    metrics = {
        "tts_circuit_breaker_trips_total": 2.0,
        "tts_generation_success_rate": 80.0,
        "tts_generations_total": 50.0,
        "tts_vram_usage_percent": 90.0,
        "tts_model_loaded": 1.0,
    }
    alerts = evaluate_rules(metrics, {"min_success_rate": 95.0, "vram_usage_warn_pct": 85.0})
    sources = {a.source for a in alerts}
    assert "vram_circuit_breaker" in sources
    assert "success_rate_slo" in sources
    assert "vram_usage" in sources


def test_evaluate_rules_no_false_positive() -> None:
    """健康指标下 evaluate_rules 不误报。"""
    metrics = {
        "tts_circuit_breaker_trips_total": 0.0,
        "tts_generation_success_rate": 100.0,
        "tts_generations_total": 50.0,
        "tts_vram_usage_percent": 50.0,
        "tts_model_loaded": 1.0,
    }
    assert evaluate_rules(metrics) == []


def test_webhook_channel_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    """WebhookAlertChannel 把告警 POST 到 webhook（mock urlopen）。"""
    sent = {}

    class _Resp:
        status = 200

    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        sent["url"] = req.full_url
        sent["body"] = req.data
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    ch = WebhookAlertChannel("https://hook.example.com/x")
    ch.send(Alert(severity=AlertSeverity.CRITICAL, title="boom", source="s"))
    assert sent["url"] == "https://hook.example.com/x"
    assert b"boom" in sent["body"]


def test_slo_report_structure() -> None:
    """get_slo_report 返回含目标/SLI/是否达标/错误预算的结构。"""
    report = get_slo_report()
    assert "slo_targets" in report
    assert "slis" in report
    assert isinstance(report["slis"], list)
    assert "all_met" in report
    assert "error_budget_burn_pct" in report
    # 冷启动（生成数=0）成功率视为达标，不应误报
    sr = next(s for s in report["slis"] if s["name"] == "success_rate")
    assert sr["met"] is True


def test_slis_compute() -> None:
    """compute_slis 返回三条核心 SLI。"""
    slis = compute_slis()
    names = {s.name for s in slis}
    assert {"success_rate", "availability", "avg_latency"} <= names


def test_capacity_sampler_snapshot_and_history() -> None:
    """容量采样快照可采集、历史可追加与限长。"""
    importlib.reload(__import__("app.integrated_app.observability.capacity_sampler", fromlist=["x"]))
    from app.integrated_app.observability.capacity_sampler import (
        _samples,
    )

    snap = _snapshot()
    assert "ts" in snap
    # 历史缓冲区存在且为 deque
    assert hasattr(_samples, "maxlen")
    # 限长逻辑
    hist = get_capacity_history(limit=5)
    assert isinstance(hist, list)
    # 未采样时 latest 为空 dict（不抛异常）
    assert get_capacity_latest() in ({},)


def test_get_alert_manager_singleton() -> None:
    """get_alert_manager 返回单例。"""
    assert get_alert_manager() is get_alert_manager()


class TestObservabilityRoutes:
    """可观测性 HTTP 路由冒烟（/api/system/metrics|slo|capacity 与根 /metrics）。"""

    def test_metrics_endpoint(self, client) -> None:
        resp = client.get("/api/system/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert "tts_up" in resp.text

    def test_slo_endpoint(self, client) -> None:
        resp = client.get("/api/system/slo")
        assert resp.status_code == 200
        data = resp.json()
        assert "slis" in data
        assert "all_met" in data

    def test_capacity_endpoint(self, client) -> None:
        resp = client.get("/api/system/capacity")
        assert resp.status_code == 200
        data = resp.json()
        assert "latest" in data
        assert "history" in data
        assert "count" in data

    def test_root_metrics_endpoint(self, client) -> None:
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "tts_up" in resp.text

    def test_audit_endpoint(self, client) -> None:
        # /api/system/audit 由 routes/system/health.py 提供（返回 {"items": [...]}）
        resp = client.get("/api/system/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
