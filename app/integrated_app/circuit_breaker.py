# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""通用熔断器（Circuit Breaker）工具 —— 与 VRAM 专用熔断解耦的可复用组件。

评估整改背景（BACKEND_DESIGN_ASSESSMENT §反模式 #6「无通用熔断器」）：
    现有代码仅有 VRAM 占用率驱动的专用熔断（service_layer._VRAM_CIRCUIT_BREAKER_THRESHOLD
    与 cache.AdaptiveLRUCache._CAPACITY_MAP），缺少面向**外部依赖 / 可选能力**的通用
    故障隔离机制。当某个下游调用（如可选的水印服务、远程校验、第三方 embedding）持续
    失败时，通用熔断器可快速失败（fail-fast）避免雪崩，并在冷却后自动探测恢复。

设计要点：
    - 三态：CLOSED（正常放行）/ OPEN（直接拒绝，fail-fast）/ HALF_OPEN（试探性放行少量请求）。
    - 线程安全：内部以 ``threading.Lock`` 保护状态转移，可在多 worker / 线程池场景下复用。
    - 失败计数基于「连续失败」或「滑动窗口失败率」可选；本实现采用连续失败阈值
      （简单、可预测、与 TTS 推理重试语义一致），并支持 ``expected_exceptions`` 白名单
      —— 只有命中白名单的异常才计为失败，业务校验类异常（如 ValidationError）不应触发熔断。
    - 与 VRAM 熔断的关系：本模块是**正交**的通用故障隔离层；VRAM 熔断继续由
      service_layer / cache 负责。二者可组合使用（例如 VRAM 熔断关闭某能力，
      通用熔断器再隔离该能力对外部依赖的调用）。

典型用法::

    cb = CircuitBreaker(name="watermark_svc", failure_threshold=3, reset_timeout=30.0)
    try:
        result = cb.call(watermark_embed, audio_bytes)
    except CircuitBreakerOpenError:
        # 快速失败：熔断器已打开，走降级路径（如跳过水印）
        result = audio_bytes
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar

logger = logging.getLogger("tts_multimodel")

T = TypeVar("T")


class CircuitState(str, Enum):
    """熔断器三态。

    Attributes:
        CLOSED: 正常放行，统计失败次数。
        OPEN: 已熔断，对调用直接抛 ``CircuitBreakerOpenError``（fail-fast）。
        HALF_OPEN: 冷却结束后的试探态，允许有限次调用探测依赖是否恢复。
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(RuntimeError):
    """熔断器处于 OPEN 态时，对受保护调用的快速失败异常。

    Attributes:
        name: 熔断器名称。
        retry_after: 建议的重试等待秒数（距离 OPEN->HALF_OPEN 重置还剩多久）。
    """

    def __init__(self, name: str, retry_after: float = 0.0) -> None:
        self.name = name
        self.retry_after = retry_after
        super().__init__(f"Circuit breaker '{name}' is OPEN (retry after {retry_after:.1f}s)")


class CircuitBreaker:
    """通用熔断器实现（线程安全，支持同步 / 异步调用）。

    Args:
        name: 熔断器标识（用于日志与监控）。
        failure_threshold: 进入 OPEN 态所需的连续失败次数（>=1）。
        reset_timeout: OPEN 态持续时间（秒），到期自动转入 HALF_OPEN。
        half_open_max_calls: HALF_OPEN 态允许探测的调用次数（默认 1）。
        expected_exceptions: 仅这些异常类型（及其子类）才计入失败；
            为 None 表示所有 Exception 均计入。
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        reset_timeout: float = 30.0,
        half_open_max_calls: int = 1,
        expected_exceptions: tuple[type[Exception], ...] | None = None,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, int(failure_threshold))
        self.reset_timeout = max(0.0, float(reset_timeout))
        self.half_open_max_calls = max(1, int(half_open_max_calls))
        self.expected_exceptions = expected_exceptions

        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._half_open_calls: int = 0
        self._opened_at: float = 0.0
        self._lock = threading.Lock()
        # 监控指标（供 /api/system/health 或日志读取）
        self.total_calls: int = 0
        self.total_failures: int = 0
        self.total_opens: int = 0

    # -- 状态查询 ----------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        """当前状态（线程安全快照）。

        Returns:
            CircuitState 枚举实例。
        """
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    def _maybe_transition_to_half_open(self) -> None:
        """在持锁前提下，若 OPEN 已超冷却时间则转入 HALF_OPEN。

        调用方必须已持有 ``self._lock``。
        """
        if self._state is CircuitState.OPEN and time.monotonic() - self._opened_at >= self.reset_timeout:
            self._state = CircuitState.HALF_OPEN
            self._half_open_calls = 0
            logger.info("[CircuitBreaker] '%s' OPEN -> HALF_OPEN（冷却结束，开始探测）", self.name)

    # -- 调用入口 ----------------------------------------------------------

    def _should_count(self, exc: Exception) -> bool:
        """判断异常是否应计入熔断失败。

        Args:
            exc: 被捕获的异常。

        Returns:
            True 表示计入失败（命中 expected_exceptions 白名单，或白名单为 None）。
        """
        if self.expected_exceptions is None:
            return True
        return isinstance(exc, self.expected_exceptions)

    def _on_success(self) -> None:
        with self._lock:
            self.total_calls += 1
            if self._state is CircuitState.HALF_OPEN:
                self._half_open_calls += 1
                if self._half_open_calls >= self.half_open_max_calls:
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    logger.info("[CircuitBreaker] '%s' HALF_OPEN -> CLOSED（探测成功，恢复）", self.name)
            else:
                self._consecutive_failures = 0

    def _on_failure(self, exc: Exception) -> None:
        with self._lock:
            self.total_calls += 1
            self.total_failures += 1
            if not self._should_count(exc):
                # 不在白名单内的异常（如业务校验错误）不计入熔断，但仍记录
                logger.debug("[CircuitBreaker] '%s' 忽略非白名单异常: %s", self.name, type(exc).__name__)
                return
            if self._state is CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                self.total_opens += 1
                logger.warning("[CircuitBreaker] '%s' HALF_OPEN 探测失败，重新 OPEN", self.name)
                return
            if self._state is CircuitState.CLOSED:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._opened_at = time.monotonic()
                    self.total_opens += 1
                    logger.warning(
                        "[CircuitBreaker] '%s' CLOSED -> OPEN（连续失败 %d 次）",
                        self.name,
                        self._consecutive_failures,
                    )

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """同步调用受保护函数，自动应用熔断逻辑。

        Args:
            func: 受保护的同步可调用对象。
            *args, **kwargs: 透传给 func 的位置 / 关键字参数。

        Returns:
            func 的返回值。

        Raises:
            CircuitBreakerOpenError: 熔断器处于 OPEN 态时直接抛出（fail-fast）。
            其他异常：func 自身抛出的异常原样透传（并计入熔断统计）。
        """
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state is CircuitState.OPEN:
                retry_after = max(0.0, self.reset_timeout - (time.monotonic() - self._opened_at))
                raise CircuitBreakerOpenError(self.name, retry_after)
        try:
            result = func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self._on_failure(exc)
            raise
        self._on_success()
        return result

    async def acall(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """异步调用受保护协程，自动应用熔断逻辑。

        注意：状态锁为同步锁，仅保护状态转移；协程等待期间不持锁，
        避免阻塞事件循环。对异步依赖（如 aiohttp 调用）同样适用。

        Args:
            func: 受保护的异步可调用对象（协程函数）。
            *args, **kwargs: 透传参数。

        Returns:
            协程的返回值。

        Raises:
            CircuitBreakerOpenError: OPEN 态直接抛出。
            其他异常：原样透传并计入统计。
        """
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state is CircuitState.OPEN:
                retry_after = max(0.0, self.reset_timeout - (time.monotonic() - self._opened_at))
                raise CircuitBreakerOpenError(self.name, retry_after)
        try:
            result = await func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self._on_failure(exc)
            raise
        self._on_success()
        return result

    def reset(self) -> None:
        """手动将熔断器重置回 CLOSED 态（运维干预 / 测试用）。"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._half_open_calls = 0
            self._opened_at = 0.0
            logger.info("[CircuitBreaker] '%s' 已手动重置为 CLOSED", self.name)


# ---------------------------------------------------------------------------
# 单例注册表：按名字复用熔断器，便于跨模块共享同一状态（如 waterark 服务）。
# ---------------------------------------------------------------------------
_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKERS_LOCK = threading.Lock()


def get_circuit_breaker(
    name: str,
    failure_threshold: int = 3,
    reset_timeout: float = 30.0,
    half_open_max_calls: int = 1,
    expected_exceptions: tuple[type[Exception], ...] | None = None,
) -> CircuitBreaker:
    """获取（或惰性创建）按名字注册的全局熔断器单例。

    Args:
        name: 熔断器名称（如 ``watermark_svc``）。
        failure_threshold: 首次创建时使用的连续失败阈值。
        reset_timeout: 首次创建时使用的 OPEN→HALF_OPEN 冷却时间（秒）。
        half_open_max_calls: 首次创建时使用的探测调用数。
        expected_exceptions: 首次创建时使用的失败白名单。

    Returns:
        CircuitBreaker 单例（同名复用，后续创建忽略参数沿用既有实例）。
    """
    with _BREAKERS_LOCK:
        existing = _BREAKERS.get(name)
        if existing is not None:
            return existing
        cb = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            reset_timeout=reset_timeout,
            half_open_max_calls=half_open_max_calls,
            expected_exceptions=expected_exceptions,
        )
        _BREAKERS[name] = cb
        return cb
