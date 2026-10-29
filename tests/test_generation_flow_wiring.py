# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""生成流接线单测（BACKEND_DESIGN_ASSESSMENT 中期/长期项）。

覆盖：
    - 生成缓存键确定性（GenerationResultCache.make_cache_key 经 utils 封装）
    - 幂等键缓存的写入/查询/过期/TTL（Idempotency-Key 去重）
    - OOM 降级重试的指数退避 + 抖动（避免立即重试加剧显存抖动）
不依赖 GPU：OOM 判定与显存释放均以 monkeypatch 隔离。
"""

from __future__ import annotations

import time

import pytest
from app.integrated_app.routes.generate import utils as u


def test_build_generation_cache_key_deterministic() -> None:
    """相同入参必得相同键，不同后处理参数产生不同键。"""
    k1 = u._build_generation_cache_key("voxcpm2", "你好", "p1", 1.0, "false", -16.0)
    k2 = u._build_generation_cache_key("voxcpm2", "你好", "p1", 1.0, "false", -16.0)
    assert k1 == k2
    k3 = u._build_generation_cache_key("voxcpm2", "你好", "p1", 1.2, "false", -16.0)
    assert k3 != k1


def test_idempotency_store_and_lookup(tmp_path) -> None:
    """写入后未过期且文件存在可命中；文件缺失或过期则视为未命中。"""
    f = tmp_path / "a.wav"
    f.write_bytes(b"data")
    key = "idem-1"
    u._idempotency_store(key, str(f))
    assert u._idempotency_lookup(key) == str(f)
    # 文件被删 -> 命中失败
    f.unlink()
    assert u._idempotency_lookup(key) is None


def test_idempotency_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    """超过 TTL 视为未命中。"""
    f = __import__("tempfile").NamedTemporaryFile(suffix=".wav", delete=False)
    f.write(b"x")
    f.close()
    key = "idem-exp"
    u._IDEMPOTENCY_TTL_S = 0.01
    try:
        u._idempotency_store(key, f.name)
        assert u._idempotency_lookup(key) == f.name
        time.sleep(0.03)
        assert u._idempotency_lookup(key) is None
    finally:
        u._IDEMPOTENCY_TTL_S = 300.0
        import os

        os.unlink(f.name)


def test_oom_retry_exponential_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """连续 OOM 时，重试前退避时长应随次数指数增长（含抖动但不为负）。"""

    class _FakeOOM(RuntimeError):
        pass

    sleeps: list[float] = []
    monkeypatch.setattr(u, "is_oom_error", lambda e: True)
    monkeypatch.setattr(u, "free_gpu_memory", lambda: None)
    monkeypatch.setattr(u.time, "sleep", lambda s: sleeps.append(s))

    call_count = {"n": 0}

    def run_fn():
        call_count["n"] += 1
        raise _FakeOOM("oom")

    with pytest.raises(RuntimeError):
        u._run_with_oom_retry(run_fn, "test", max_retries=3)

    # max_retries=3 -> 首次失败 + 3 次重试，重试前各有一次退避（3 次）
    assert len(sleeps) == 3
    # 退避应为正且大致递增：第1次≈1s，第2次≈2s，第3次≈4s（带 ±20% 抖动）
    assert sleeps[0] <= sleeps[1] <= sleeps[2]
    assert all(s >= 0 for s in sleeps)
    assert sleeps[2] <= 8.0 + 1e-6  # 上限 cap


def test_oom_retry_non_oom_reraised(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 OOM 异常原样抛出、不重试、不触发退避。"""

    class _ValErr(ValueError):
        pass

    sleeps: list[float] = []
    monkeypatch.setattr(u, "is_oom_error", lambda e: False)
    monkeypatch.setattr(u.time, "sleep", lambda s: sleeps.append(s))

    def run_fn():
        raise _ValErr("bad")

    with pytest.raises(_ValErr):
        u._run_with_oom_retry(run_fn, "test", max_retries=3)
    assert sleeps == []  # 非 OOM 不进入退避逻辑


def test_get_generation_cache_disabled_by_default() -> None:
    """默认配置（cache_enabled=false）下 _get_generation_cache 返回 None。"""
    # 重置惰性单例，确保读到默认配置
    u._GENERATION_CACHE = None
    assert u._get_generation_cache() is None


# ---------------------------------------------------------------------------
# Better Harness 2026-10-29T1419 修复回检：
#   generation-timeout-fallback-calls-itself / content-safety-gate-fails-open-silently
#   idempotency-key-not-bound-to-request / failure-logs-lose-request-and-task-identity
#   oom-auto-recovery-leg-has-no-check
# 全部不依赖 GPU：配置与告警通道均以替身隔离。
# ---------------------------------------------------------------------------
import types  # noqa: E402


def _fake_cfg(**gen_kwargs) -> types.SimpleNamespace:
    """造一个只到 generation 层的配置替身。"""
    return types.SimpleNamespace(pydantic_config=types.SimpleNamespace(generation=types.SimpleNamespace(**gen_kwargs)))


def _config_broken() -> None:
    raise RuntimeError("config singleton not initialised")


def test_gen_timeout_getters_fall_back_to_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置读不到时回退到模块常量，而不是回退到函数自己（那会递归爆栈）。"""
    monkeypatch.delenv("TTS_SEMAPHORE_TIMEOUT_S", raising=False)
    monkeypatch.delenv("TTS_GENERATION_TIMEOUT_S", raising=False)
    monkeypatch.setattr(u, "get_config", _config_broken)
    assert u._gen_semaphore_timeout() == u._SEMAPHORE_ACQUIRE_TIMEOUT_S
    assert u._gen_hard_timeout() == u._GENERATION_HARD_TIMEOUT_S


def test_ensure_content_safe_logs_when_switch_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    """开关读取失败必须留一条「本轮未审计」的审计事件，不能静默放行。"""
    events: list[tuple[str, dict]] = []
    # 函数内是 `from ...security.audit import log_audit`，所以要打桩审计模块本身
    monkeypatch.setattr(u, "get_config", _config_broken)
    monkeypatch.setattr(
        "app.integrated_app.security.audit.log_audit",
        lambda event, **kw: events.append((event, kw)),
    )
    assert u._ensure_content_safe(object(), "hello") is None  # 仍放行，但可见
    assert events and events[0][0] == "content_safety_skipped"
    assert events[0][1]["outcome"] == "not-audited"
    assert "actor" in events[0][1] and "detail" in events[0][1]


def test_idempotency_hit_requires_matching_fingerprint(tmp_path) -> None:
    """同键不同内容不算重试：不返回上一次音频；同键同内容才复用。"""
    f = tmp_path / "fp.wav"
    f.write_bytes(b"data")
    key = "idem-fp-1"
    u._idempotency_store(key, str(f), "fp-A")
    assert u._idempotency_lookup(key, "fp-A") == str(f)
    assert u._idempotency_lookup(key, "fp-B") is None
    # 条目登记时没有指纹 -> 维持旧行为（不误伤历史调用方）
    u._idempotency_store(key, str(f))
    assert u._idempotency_lookup(key, "fp-B") == str(f)


def test_store_generation_result_skips_idempotency_without_fingerprint(tmp_path) -> None:
    """算不出内容指纹时不登记幂等键，避免退回「只认字符串」的老口子。"""
    f = tmp_path / "nofp.wav"
    f.write_bytes(b"data")
    u._store_generation_result(str(f), "idem-nofp", None, None)
    assert "idem-nofp" not in u._IDEMPOTENCY_CACHE
    u._store_generation_result(str(f), "idem-withfp", None, "fp-A")
    assert "idem-withfp" in u._IDEMPOTENCY_CACHE
    u._IDEMPOTENCY_CACHE.pop("idem-withfp", None)


def test_bind_request_id_publishes_id_inside_callable() -> None:
    """随线程池任务带进去的标识，能在被调用的那一段里读到。"""
    from app.integrated_app.middleware.request_id import get_request_id

    seen: dict[str, str] = {}

    def probe() -> int:
        seen["rid"] = get_request_id()
        return 42

    try:
        assert u._bind_request_id("req-bind-check", probe)() == 42
        assert seen["rid"] == "req-bind-check"
    finally:
        u.set_request_id("-")


def test_oom_auto_recovery_disabled_by_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """generation.oom_auto_reload=false -> 退回旧的「只靠容器重启」，不调度重载。"""
    scheduled: list[object] = []

    def _spy(coro):
        scheduled.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(u, "get_config", lambda: _fake_cfg(oom_auto_reload=False, oom_auto_reload_cooldown_s=300.0))
    monkeypatch.setattr(u, "_last_oom_auto_reload_ts", 0.0)
    monkeypatch.setattr(u.asyncio, "create_task", _spy)
    u._schedule_oom_auto_recovery("ep-disabled")
    assert scheduled == []


def test_oom_auto_recovery_skips_inside_cooldown_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """冷却窗口内再次 OOM 不重复调度，防止坏权重引发重载风暴。"""
    scheduled: list[object] = []

    def _spy(coro):
        scheduled.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(u, "get_config", lambda: _fake_cfg(oom_auto_reload=True, oom_auto_reload_cooldown_s=300.0))
    monkeypatch.setattr(u, "_last_oom_auto_reload_ts", time.time())
    monkeypatch.setattr(u.asyncio, "create_task", _spy)
    u._schedule_oom_auto_recovery("ep-cooldown")
    assert scheduled == []


def test_oom_auto_recovery_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """本函数承诺不抛异常：配置坏 + 无运行循环时也只记日志，不影响正在返回的 OOM 响应。"""

    def _boom(_coro):
        _coro.close()  # 调度失败时协程未被消费，显式关闭避免 ResourceWarning
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(u, "get_config", _config_broken)
    monkeypatch.setattr(u, "_last_oom_auto_reload_ts", 0.0)
    monkeypatch.setattr(u.asyncio, "create_task", _boom)
    u._schedule_oom_auto_recovery("ep-raise")  # 不得向外抛
