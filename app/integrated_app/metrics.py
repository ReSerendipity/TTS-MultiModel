"""Prometheus HTTP 层仪表包装（prometheus_client 可选依赖）。

app_server 的请求计数中间件与告警评估循环依赖本模块提供的
``ENABLED`` / ``INFLIGHT`` / ``REQUEST_COUNT`` / ``set_queue_depth`` /
``set_models_ok``。

Why 独立成模块：
    ``observability/metrics.py`` 是「手写 Prometheus 文本 exposition」实现
    （零第三方依赖，面向 /metrics 抓取端点，负责序列化输出）；本模块是
    「HTTP 中间件在途/计数仪表」的可选包装，仅在 ``prometheus_client`` 可用时
    启用（``ENABLED=True``），否则全部 no-op，**绝不阻断主流程**。
    二者职责互补：前者管「输出什么」，后者管「进程内实时计数」。

设计约束：
    - AGENTS.md 硬约束 #5 离线优先：``prometheus_client`` 未安装时应用照常运行，
      所有仪表调用静默降级为 no-op。
    - 所有指标名统一以 ``tts_`` 前缀，与 observability/metrics.py 保持一致，
      便于 Prometheus 端统一查询与告警规则复用。
"""

from __future__ import annotations

try:
    from prometheus_client import Counter, Gauge

    _PROMETHEUS_CLIENT_AVAILABLE = True
except ImportError:  # pragma: no cover - 可选依赖缺失时的降级路径
    _PROMETHEUS_CLIENT_AVAILABLE = False

# 是否启用 HTTP 层仪表（prometheus_client 可导入才启用）
ENABLED = _PROMETHEUS_CLIENT_AVAILABLE


if _PROMETHEUS_CLIENT_AVAILABLE:
    INFLIGHT = Gauge("tts_http_requests_inflight", "当前处理中的 HTTP 请求数")
    REQUEST_COUNT = Counter(
        "tts_http_requests_total",
        "HTTP 请求总数（按 method / path / status 分维）",
        ["method", "path", "status"],
    )
    _QUEUE_DEPTH = Gauge("tts_queue_depth", "当前生成队列排队数")
    _MODELS_OK = Gauge("tts_models_ok", "推理引擎是否就绪（1=就绪 0=未就绪）")

    def set_queue_depth(value: int) -> None:
        """写入当前队列深度仪表。"""
        _QUEUE_DEPTH.set(value)

    def set_models_ok(value: bool) -> None:
        """写入引擎就绪状态仪表。"""
        _MODELS_OK.set(1 if value else 0)

else:

    class _NoopGauge:
        """无操作 Gauge 替身（prometheus_client 缺失时静默降级）。"""

        def inc(self, *args: object, **kwargs: object) -> None:
            pass

        def dec(self, *args: object, **kwargs: object) -> None:
            pass

        def set(self, *args: object, **kwargs: object) -> None:
            pass

    class _NoopCounter:
        """无操作 Counter 替身（含 labels() 链式调用）。"""

        def inc(self, *args: object, **kwargs: object) -> None:
            pass

        def labels(self, *args: object, **kwargs: object) -> _NoopCounter:
            return _NoopCounter()

    INFLIGHT = _NoopGauge()
    REQUEST_COUNT = _NoopCounter()

    def set_queue_depth(value: int) -> None:
        """prometheus_client 未安装时的降级 no-op。"""

    def set_models_ok(value: bool) -> None:
        """prometheus_client 未安装时的降级 no-op。"""
