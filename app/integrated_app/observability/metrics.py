# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Prometheus 文本格式指标导出（零外部依赖手写实现）。

对应报告缺口：
    - SRE 评估 §1.1「无 Prometheus / OpenTelemetry exporter，指标仅能主动 HTTP pull」
    - SRE 评估 §2-5「监控盲区：指标随进程重启清零、请求级延迟未导出」
    - 容器化评估 P2-8「引入 prometheus_client 暴露 /metrics」

Why 手写而非依赖 prometheus_client：
    - AGENTS.md 硬约束 #5 要求一切外部资源可离线工作；引入新依赖会增加
      安装失败面（CI 多 OS 矩阵）。
    - Prometheus 的「文本 exposition 格式」本身是简单稳定的规范，手写
      完全满足 scrape 需求，且 100% 可单测、无网络依赖。
    - 若未来需要真正 exporter，可在本文件替换 ``build_metrics_text`` 内部
      实现，对外 API 不变。

数据流（结构化优先）：
    ``collect_metrics()`` 产出 ``dict[str, float]``（唯一事实源，供 SLO / 告警 / 单测复用），
    ``build_metrics_text()`` 仅负责把它渲染成 Prometheus 文本。
    ⚠️ 早期实现是「先渲染文本再反解析」，反解析要求行内含 ``=``，会静默丢掉所有
    无 label 的样本（见 AGENTS.md 陷阱记录）。改为结构化优先后从根上消除这类问题。

指标事实源：
    - ``monitor.HealthMonitor`` 单例（进程内运行时统计 + GPU）
    - ``history_db`` 的 ``generation_history`` 聚合（跨重启累计，补 SRE §1.1 清零缺口）
    - ``model_registry`` / ``task_queue``（就绪状态与排队深度）
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tts_multimodel")

# 指标帮助文本（HELP）
_HELP: dict[str, str] = {
    "tts_up": "服务是否存活（1=是）",
    "tts_generations_total": "累计生成尝试次数（进程内）",
    "tts_generation_errors_total": "累计生成失败次数（进程内）",
    "tts_generation_success_rate": "生成成功率（0-100）",
    "tts_oom_retries_total": "累计 OOM 后自动重试次数",
    "tts_circuit_breaker_trips_total": "累计显存熔断触发次数",
    "tts_uptime_seconds": "进程运行时间（秒）",
    "tts_vram_used_mb": "GPU 已分配显存（MB）",
    "tts_vram_total_mb": "GPU 显存总量（MB）",
    "tts_vram_usage_percent": "GPU 显存占用百分比",
    "tts_queue_size": "当前生成队列排队数",
    "tts_model_loaded": "模型是否已加载（1=已加载 0=未加载）",
    "tts_gen_total": "历史库累计生成记录数（跨重启）",
    "tts_degraded_total": "历史库累计降级生成次数（跨重启）",
    "tts_rtf_avg": "历史库平均实时率 = 生成耗时/音频时长（<1 为实时）",
    "tts_avg_gen_time_ms": "平均生成耗时（毫秒，由 rtf × 音频时长推导）",
    "tts_alerts_total": "累计发出的告警次数（按 severity 分维）",
}

# 指标类型
_TYPE: dict[str, str] = {
    "tts_up": "gauge",
    "tts_generations_total": "counter",
    "tts_generation_errors_total": "counter",
    "tts_generation_success_rate": "gauge",
    "tts_oom_retries_total": "counter",
    "tts_circuit_breaker_trips_total": "counter",
    "tts_uptime_seconds": "gauge",
    "tts_vram_used_mb": "gauge",
    "tts_vram_total_mb": "gauge",
    "tts_vram_usage_percent": "gauge",
    "tts_queue_size": "gauge",
    "tts_model_loaded": "gauge",
    "tts_gen_total": "counter",
    "tts_degraded_total": "counter",
    "tts_rtf_avg": "gauge",
    "tts_avg_gen_time_ms": "gauge",
    "tts_alerts_total": "counter",
}

# 无 label 指标的渲染顺序（保证 scrape 输出稳定，利于 diff 与单测）
_ORDER: tuple[str, ...] = tuple(_HELP)


def _safe_float(value: Any, default: float = 0.0) -> float:
    """把任意值安全转换为 float，失败返回默认值。

    Args:
        value: 待转换值。
        default: 转换失败时的返回值。

    Returns:
        float: 转换结果或默认值。
    """
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    # NaN / Inf 会污染 Prometheus 抓取（PromQL 聚合结果不可预测），统一归零
    if result != result or result in (float("inf"), float("-inf")):
        return default
    return result


def _history_aggregate() -> dict[str, float]:
    """从历史库聚合跨重启指标（补「进程重启即清零」缺口）。

    Returns:
        dict: 含 ``tts_gen_total`` / ``tts_degraded_total`` / ``tts_rtf_avg`` /
        ``tts_avg_gen_time_ms``；DB 不可用时返回零值（fail-open）。
    """
    out = {
        "tts_gen_total": 0.0,
        "tts_degraded_total": 0.0,
        "tts_rtf_avg": 0.0,
        "tts_avg_gen_time_ms": 0.0,
    }
    try:
        from ..history_db import get_history_db

        db = get_history_db()
        with db.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(is_degraded), 0), "
                "COALESCE(AVG(rtf), 0.0), COALESCE(AVG(duration_seconds), 0.0) "
                "FROM generation_history"
            ).fetchone()
        if row:
            out["tts_gen_total"] = _safe_float(row[0])
            out["tts_degraded_total"] = _safe_float(row[1])
            avg_rtf = _safe_float(row[2])
            avg_duration = _safe_float(row[3])
            out["tts_rtf_avg"] = round(avg_rtf, 4)
            # 生成耗时 = rtf × 音频时长；rtf 缺省（老记录为 NULL）时该值自然为 0
            out["tts_avg_gen_time_ms"] = round(avg_rtf * avg_duration * 1000.0, 3)
    except Exception as exc:  # noqa: BLE001 — 指标采集失败绝不阻断 /metrics
        logger.debug("[metrics] 历史库聚合失败（返回零值）: %s", exc)
    return out


def collect_metrics() -> dict[str, float]:
    """采集全部指标，返回「指标名 -> 数值」的结构化字典（唯一事实源）。

    任何子系统采集失败都只降级为零值并在 debug 级记日志，
    绝不向上抛异常——/metrics 是运维生命线，必须始终可抓取。

    Returns:
        dict[str, float]: 扁平指标字典（不含按 severity 分维的告警计数）。
    """
    collected: dict[str, float] = {"tts_up": 1.0}

    # --- 1. HealthMonitor：进程内运行时统计 + GPU ---
    try:
        from ..monitor import get_health_monitor

        report = get_health_monitor().get_metrics()
        collected["tts_generations_total"] = _safe_float(report.get("total_generations"))
        collected["tts_generation_errors_total"] = _safe_float(report.get("total_errors"))
        collected["tts_oom_retries_total"] = _safe_float(report.get("total_oom_retries"))
        collected["tts_circuit_breaker_trips_total"] = _safe_float(report.get("circuit_breaker_trips"))
        collected["tts_uptime_seconds"] = _safe_float(report.get("uptime_seconds"))
        collected["tts_generation_success_rate"] = _safe_float(report.get("success_rate_pct"))

        gpu = report.get("gpu")
        # CPU 模式下省略 GPU 指标：避免向 Prometheus 暴露恒为 0 的假时间序列
        if isinstance(gpu, dict) and gpu.get("mode") != "cpu" and "vram_used_mb" in gpu:
            collected["tts_vram_used_mb"] = _safe_float(gpu.get("vram_used_mb"))
            collected["tts_vram_total_mb"] = _safe_float(gpu.get("vram_total_mb"))
            collected["tts_vram_usage_percent"] = _safe_float(gpu.get("vram_usage_pct"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[metrics] HealthMonitor 指标采集失败: %s", exc)

    # --- 2. 模型就绪状态 ---
    try:
        from ..model_registry import registry

        collected["tts_model_loaded"] = 1.0 if registry.is_engine_ready() else 0.0
    except Exception:  # noqa: BLE001
        collected["tts_model_loaded"] = 0.0

    # --- 3. 队列深度 ---
    try:
        from ..task_queue import get_queue_status

        collected["tts_queue_size"] = _safe_float(get_queue_status().get("queue_size"))
    except Exception:  # noqa: BLE001
        collected["tts_queue_size"] = 0.0

    # --- 4. 历史库聚合（跨重启维度） ---
    collected.update(_history_aggregate())
    return collected


def collect_alert_counts() -> dict[str, int]:
    """按 severity 采集累计告警数（带 label 维度，需单独渲染）。

    Returns:
        dict[str, int]: ``severity -> 累计次数``；采集失败返回空字典。
    """
    try:
        from .alerting import get_alert_manager

        return get_alert_manager().alert_counts()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[metrics] 告警计数采集失败: %s", exc)
        return {}


def _emit(name: str, value: float, labels: dict[str, str] | None = None) -> str:
    """格式化单个指标样本为 Prometheus 文本行。

    Args:
        name: 指标名。
        value: 数值。
        labels: 可选标签字典。

    Returns:
        str: 单行 exposition 文本。
    """
    # Prometheus 文本格式规定 label value 内的 \ " 必须转义
    if labels:
        parts = []
        for k, v in labels.items():
            escaped = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            parts.append(f'{k}="{escaped}"')
        return f"{name}{{{','.join(parts)}}} {value}"
    return f"{name} {value}"


def _fmt(value: float) -> str:
    """数值格式化：整数不带小数点，浮点保留合理精度。

    Args:
        value: 待格式化数值。

    Returns:
        str: 可直接写入文本格式的数值字符串。
    """
    if value == int(value):
        return str(int(value))
    return f"{value:.6g}"


def build_metrics_text() -> str:
    """渲染 Prometheus 文本 exposition 格式（供 /metrics 抓取）。

    Returns:
        str: 含 HELP/TYPE 头、以 ``\\n`` 结尾的指标文本。
    """
    collected = collect_metrics()
    lines: list[str] = []

    # 固定顺序渲染无 label 指标（保证输出稳定）
    for name in _ORDER:
        if name == "tts_alerts_total" or name not in collected:
            continue
        lines.append(f"# HELP {name} {_HELP[name]}")
        lines.append(f"# TYPE {name} {_TYPE[name]}")
        lines.append(_emit(name, _safe_float(collected[name])))

    # 告警计数按 severity 分维渲染
    counts = collect_alert_counts()
    lines.append(f"# HELP tts_alerts_total {_HELP['tts_alerts_total']}")
    lines.append(f"# TYPE tts_alerts_total {_TYPE['tts_alerts_total']}")
    if counts:
        for severity in sorted(counts):
            lines.append(_emit("tts_alerts_total", float(counts[severity]), {"severity": severity}))
    else:
        # 始终输出至少一个样本：缺失的时间序列会让 `absent()` 类告警误报
        lines.append(_emit("tts_alerts_total", 0.0, {"severity": "info"}))

    return "\n".join(lines) + "\n"
