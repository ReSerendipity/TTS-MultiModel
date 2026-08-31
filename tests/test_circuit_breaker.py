"""通用熔断器（app/integrated_app/circuit_breaker.py）单元测试。

覆盖：三态转移（CLOSED/OPEN/HALF_OPEN）、fail-fast、冷却超时、
白名单异常、同步/异步调用、手动重置与统计计数。
对应 BACKEND_DESIGN_ASSESSMENT §反模式 #6「无通用熔断器」的落地验证。
"""

import os
import sys
import time

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from integrated_app.circuit_breaker import (  # noqa: E402
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)


def _flaky_fn(failures: list[bool]):
    """按预设序列抛异常/成功，用于驱动熔断状态机。"""

    def _fn(x: int = 0) -> int:
        if failures and failures.pop(0):
            raise ConnectionError("downstream unavailable")
        return x

    return _fn


class TestInitialState:
    def test_starts_closed(self):
        cb = CircuitBreaker(name="svc", failure_threshold=3, reset_timeout=30.0)
        assert cb.state is CircuitState.CLOSED

    def test_open_error_carries_name_and_retry(self):
        cb = CircuitBreaker(name="svc", failure_threshold=1, reset_timeout=30.0)
        # 第一次调用抛的是目标函数自身的异常（并触发熔断打开）
        with pytest.raises(ConnectionError):
            cb.call(_flaky_fn([True]))
        # 熔断已 OPEN：第二次调用不再执行目标，直接抛 CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError) as exc_info:
            cb.call(_flaky_fn([False]))
        assert exc_info.value.name == "svc"
        assert "svc" in str(exc_info.value)

    def test_failure_threshold_is_clamped_to_one(self):
        cb = CircuitBreaker(name="svc", failure_threshold=-3)
        assert cb.failure_threshold == 1


class TestStateTransitions:
    def test_closed_opens_after_consecutive_failures(self):
        cb = CircuitBreaker(name="svc", failure_threshold=3, reset_timeout=30.0)
        for _ in range(3):
            with pytest.raises(ConnectionError):
                cb.call(_flaky_fn([True]))
        assert cb.state is CircuitState.OPEN
        assert cb.total_opens == 1

    def test_open_fails_fast_without_invoking_target(self):
        cb = CircuitBreaker(name="svc", failure_threshold=1, reset_timeout=30.0)
        with pytest.raises(ConnectionError):
            cb.call(_flaky_fn([True]))
        calls = []
        with pytest.raises(CircuitBreakerOpenError):
            cb.call(lambda: calls.append(1))
        assert calls == []  # OPEN 态直接拒绝，不再调用目标

    def test_open_to_half_open_after_reset_timeout(self, monkeypatch):
        cb = CircuitBreaker(name="svc", failure_threshold=1, reset_timeout=0.01)
        with pytest.raises(ConnectionError):
            cb.call(_flaky_fn([True]))
        assert cb.state is CircuitState.OPEN
        time.sleep(0.02)
        # 读取 state 触发超时检查，应转入 HALF_OPEN
        assert cb.state is CircuitState.HALF_OPEN

    def test_half_open_success_recovers_to_closed(self, monkeypatch):
        cb = CircuitBreaker(name="svc", failure_threshold=1, reset_timeout=0.01)
        with pytest.raises(ConnectionError):
            cb.call(_flaky_fn([True]))
        time.sleep(0.02)
        result = cb.call(_flaky_fn([False]))
        assert result == 0
        assert cb.state is CircuitState.CLOSED
        assert cb._consecutive_failures == 0

    def test_half_open_failure_reopens(self, monkeypatch):
        cb = CircuitBreaker(name="svc", failure_threshold=1, reset_timeout=0.01)
        with pytest.raises(ConnectionError):
            cb.call(_flaky_fn([True]))
        time.sleep(0.02)
        with pytest.raises(ConnectionError):
            cb.call(_flaky_fn([True]))
        assert cb.state is CircuitState.OPEN
        assert cb.total_opens == 2

    def test_success_resets_failure_counter(self):
        cb = CircuitBreaker(name="svc", failure_threshold=3, reset_timeout=30.0)
        with pytest.raises(ConnectionError):
            cb.call(_flaky_fn([True]))
        assert cb._consecutive_failures == 1
        cb.call(_flaky_fn([False]))
        assert cb._consecutive_failures == 0
        assert cb.state is CircuitState.CLOSED

    def test_manual_reset(self):
        cb = CircuitBreaker(name="svc", failure_threshold=1, reset_timeout=30.0)
        with pytest.raises(ConnectionError):
            cb.call(_flaky_fn([True]))
        assert cb.state is CircuitState.OPEN
        cb.reset()
        assert cb.state is CircuitState.CLOSED
        assert cb._consecutive_failures == 0


class TestExpectedExceptionsWhitelist:
    def test_non_whitelisted_exception_does_not_count(self):
        cb = CircuitBreaker(
            name="svc",
            failure_threshold=3,
            reset_timeout=30.0,
            expected_exceptions=(ConnectionError,),
        )
        with pytest.raises(ValueError):
            cb.call(_raise_value_error)
        # 非白名单异常不计入熔断失败
        assert cb._consecutive_failures == 0
        assert cb.state is CircuitState.CLOSED
        assert cb.total_failures == 1  # 但仍被记录

    def test_whitelisted_exception_counts_toward_open(self):
        cb = CircuitBreaker(
            name="svc",
            failure_threshold=2,
            reset_timeout=30.0,
            expected_exceptions=(ConnectionError,),
        )
        with pytest.raises(ConnectionError):
            cb.call(_flaky_fn([True]))
        with pytest.raises(ConnectionError):
            cb.call(_flaky_fn([True]))
        assert cb.state is CircuitState.OPEN

    def test_whitelist_matches_subclass(self):
        class _SubConnectionError(ConnectionError):
            pass

        cb = CircuitBreaker(
            name="svc",
            failure_threshold=1,
            reset_timeout=30.0,
            expected_exceptions=(ConnectionError,),
        )
        with pytest.raises(_SubConnectionError):
            cb.call(_raise_custom(_SubConnectionError()))
        assert cb.state is CircuitState.OPEN


def _raise_value_error():
    raise ValueError("bad request")


def _raise_custom(exc):
    def _fn():
        raise exc

    return _fn


class TestAsyncCalls:
    @pytest.mark.asyncio
    async def test_acall_success(self):
        cb = CircuitBreaker(name="svc", failure_threshold=3, reset_timeout=30.0)

        async def _ok(x: int) -> int:
            return x + 1

        assert await cb.acall(_ok, 1) == 2
        assert cb.total_calls == 1
        assert cb.state is CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_acall_failures_open_breaker(self):
        cb = CircuitBreaker(name="svc", failure_threshold=2, reset_timeout=30.0)

        async def _boom():
            raise ConnectionError("boom")

        with pytest.raises(ConnectionError):
            await cb.acall(_boom)
        with pytest.raises(ConnectionError):
            await cb.acall(_boom)
        assert cb.state is CircuitState.OPEN

        async def _probe():
            return "unreachable"

        with pytest.raises(CircuitBreakerOpenError):
            await cb.acall(_probe)


class TestStatistics:
    def test_counters_accumulate(self):
        cb = CircuitBreaker(name="svc", failure_threshold=2, reset_timeout=30.0)
        cb.call(_flaky_fn([False]))
        cb.call(_flaky_fn([False]))
        with pytest.raises(ConnectionError):
            cb.call(_flaky_fn([True]))
        with pytest.raises(ConnectionError):
            cb.call(_flaky_fn([True]))
        assert cb.total_calls == 4
        assert cb.total_failures == 2
        assert cb.total_opens == 1
