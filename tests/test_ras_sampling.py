"""ras_sampling 模块单元测试 — RAS 重复感知采样。

覆盖目标模块: bin/integrated_app/ras_sampling.py
"""

import pytest

from integrated_app.ras_sampling import (
    RASConfig,
    RepetitionDetector,
    adjust_sampling_params,
)


class TestRASConfig:
    def test_defaults(self):
        cfg = RASConfig()
        assert cfg.window_size > 0
        assert cfg.ngram_n >= 2
        assert cfg.repetition_threshold >= 1


class TestRepetitionDetector:
    def setup_method(self):
        self.cfg = RASConfig(window_size=64, ngram_n=3, repetition_threshold=3)
        self.detector = RepetitionDetector(self.cfg)

    def test_initial_state(self):
        assert self.detector.repetition_count == 0
        assert self.detector.total_detections == 0
        assert self.detector.is_repetitive() is False

    def test_append_normal_tokens_no_repeat(self):
        for i in range(20):
            self.detector.append(i)
        assert self.detector.is_repetitive() is False

    def test_append_repeating_tokens_detects(self):
        # 连续追加相同 token 触发 n-gram 重复
        for _ in range(30):
            self.detector.append(42)
        assert self.detector.total_detections > 0
        assert self.detector.repetition_count > 0

    def test_append_batch(self):
        self.detector.append_batch([1, 2, 3, 4, 5])
        assert self.detector.repetition_count == 0

    def test_get_repetition_level(self):
        assert self.detector.get_repetition_level() == 0
        for _ in range(30):
            self.detector.append(7)
        assert self.detector.get_repetition_level() >= 0

    def test_token_count_tracking(self):
        self.detector.append(5)
        self.detector.append(5)
        assert self.detector.get_token_count(5) >= 2

    def test_reset(self):
        for _ in range(30):
            self.detector.append(9)
        self.detector.reset()
        assert self.detector.repetition_count == 0
        assert self.detector.total_detections == 0


class TestAdjustSamplingParams:
    def test_adjust_repetition(self):
        cfg = RASConfig(window_size=64, ngram_n=3, repetition_threshold=2)
        detector = RepetitionDetector(cfg)
        # 触发重复：连续追加相同 token
        for _ in range(30):
            detector.append(42)
        assert detector.is_repetitive()
        temp, top_p = adjust_sampling_params(0.8, 0.9, detector)
        assert temp > 0.8
        assert top_p > 0.9

    def test_adjust_no_repetition_unchanged(self):
        detector = RepetitionDetector(RASConfig())
        detector.append_batch([1, 2, 3, 4, 5])
        temp, top_p = adjust_sampling_params(0.8, 0.9, detector)
        assert temp == pytest.approx(0.8)
        assert top_p == pytest.approx(0.9)
