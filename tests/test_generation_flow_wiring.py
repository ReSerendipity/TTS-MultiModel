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
