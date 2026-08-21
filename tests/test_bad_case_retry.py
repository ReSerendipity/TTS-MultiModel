"""bad_case_retry 模块单元测试 — 坏案例检测与参数重试。

覆盖目标模块: app/integrated_app/bad_case_retry.py
"""

import numpy as np

from integrated_app.bad_case_retry import (
    FailureType,
    RetryConfig,
    RetryResult,
    RetryState,
    RetryStrategy,
    adjust_params_for_retry,
    detect_failure_type,
    retry_with_bad_case_detection,
)


def _sine(duration=1.0, sr=24000, freq=440.0):
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    return (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class TestDetectFailureType:
    def test_empty_audio(self):
        ok, ftype, reason = detect_failure_type(np.array([]), 24000)
        assert ok is True
        assert ftype == FailureType.SILENCE

    def test_none_audio(self):
        ok, ftype, _ = detect_failure_type(None, 24000)
        assert ok is True
        assert ftype == FailureType.SILENCE

    def test_normal_audio_ok(self):
        ok, ftype, reason = detect_failure_type(_sine(), 24000)
        assert ok is False
        assert reason == "OK"

    def test_too_short(self):
        wav = _sine(duration=0.05)
        ok, ftype, reason = detect_failure_type(wav, 24000, expected_duration=1.0)
        assert ok is True
        assert ftype == FailureType.TOO_SHORT

    def test_silence_low_energy(self):
        wav = np.zeros(24000, dtype=np.float32)  # 1 秒，避免触发 TOO_SHORT
        ok, ftype, _ = detect_failure_type(wav, 24000)
        assert ok is True
        assert ftype == FailureType.SILENCE

    def test_clipping(self):
        wav = np.ones(24000, dtype=np.float32)  # 1 秒全 1.0 → 削波
        ok, ftype, _ = detect_failure_type(wav, 24000)
        assert ok is True
        assert ftype == FailureType.CLIPPING


class TestAdjustParamsForRetry:
    def test_silence_strategy(self):
        params = {"cfg_value": 2.0, "temperature": 0.8}
        new = adjust_params_for_retry(params, FailureType.SILENCE, attempt=1)
        assert new["cfg_value"] < 2.0
        assert new["temperature"] > 0.8

    def test_clipping_strategy(self):
        params = {"cfg_value": 2.0, "temperature": 0.8}
        new = adjust_params_for_retry(params, FailureType.CLIPPING, attempt=1)
        assert new["cfg_value"] < 2.0
        assert new["temperature"] < 0.8

    def test_repetition_strategy(self):
        params = {"cfg_value": 2.0, "temperature": 0.8, "top_p": 0.9}
        new = adjust_params_for_retry(params, FailureType.REPETITION, attempt=1)
        assert new["temperature"] > 0.8
        assert new["top_p"] > 0.9

    def test_too_long_strategy(self):
        params = {"cfg_value": 2.0, "temperature": 0.8}
        new = adjust_params_for_retry(params, FailureType.TOO_LONG, attempt=1)
        assert new["cfg_value"] > 2.0
        assert new["temperature"] < 0.8

    def test_input_unchanged(self):
        params = {"cfg_value": 2.0}
        adjust_params_for_retry(params, FailureType.SILENCE, attempt=1)
        assert params == {"cfg_value": 2.0}  # 原字典不被修改


class TestRetryStateAndResult:
    def test_retry_state_defaults(self):
        state = RetryState()
        assert state.attempt == 0
        assert state.failure_type == FailureType.UNKNOWN

    def test_retry_result_fields(self):
        result = RetryResult(success=True, attempts=2)
        assert result.success is True
        assert result.attempts == 2


class TestRetryWithBadCaseDetection:
    def test_success_first_try(self):
        def gen(**kwargs):
            return _sine()

        result = retry_with_bad_case_detection(gen, {})
        assert result.success is True
        assert result.attempts == 1

    def test_eventual_success(self):
        attempts = {"n": 0}

        def gen(**kwargs):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return np.zeros(24000, dtype=np.float32)  # 静音 → 失败
            return _sine()

        result = retry_with_bad_case_detection(gen, {})
        assert result.success is True
        assert result.attempts >= 2

    def test_retries_exhausted(self):
        def gen(**kwargs):
            return np.zeros(24000, dtype=np.float32)

        cfg = RetryConfig(max_retries=2)
        result = retry_with_bad_case_detection(gen, {}, config=cfg)
        # 实现优雅降级：重试耗尽后接受当前输出（success=True）
        assert result.success is True
        assert result.attempts <= cfg.max_retries + 1
        assert "重试耗尽" in result.failure_reason


class TestEnums:
    def test_strategies(self):
        assert RetryStrategy.CFG_INCREASE.value == "cfg_increase"
        assert RetryStrategy.COMBINED.value == "combined"

    def test_failure_types(self):
        assert FailureType.INTERNAL_SILENCE.value == "internal_silence"
        assert FailureType.UNKNOWN.value == "unknown"
