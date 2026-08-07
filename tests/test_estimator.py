"""estimator 模块单元测试 — 生成时间估算器（线性回归）。

覆盖目标模块: bin/integrated_app/estimator.py
"""

import json

import pytest

from integrated_app.estimator import GenerationTimeEstimator


class TestGenerationTimeEstimator:
    def test_cold_start_default(self, tmp_path):
        est = GenerationTimeEstimator(str(tmp_path / "times.json"))
        assert est.estimate(100) == pytest.approx(100 / 15.0 + 0.5)
        est2, conf = est.estimate_with_confidence(100)
        assert conf == 0.0

    def test_estimate_zero_chars(self, tmp_path):
        est = GenerationTimeEstimator(str(tmp_path / "t.json"))
        assert est.estimate(0) == 0.0
        assert est.estimate(-5) == 0.0

    def test_estimate_with_segments(self, tmp_path):
        est = GenerationTimeEstimator(str(tmp_path / "t.json"))
        base = est.estimate(100)
        segmented = est.estimate(100, segment_count=3)
        assert segmented == pytest.approx(base + 0.6)

    def test_record_sample(self, tmp_path):
        est = GenerationTimeEstimator(str(tmp_path / "t.json"))
        est.record_sample(10, 1.0)
        est.record_sample(20, 2.0)
        est.record_sample(30, 3.0)
        assert est._count == 3
        assert est.estimate(15) > 0

    def test_record_ignores_invalid(self, tmp_path):
        est = GenerationTimeEstimator(str(tmp_path / "t.json"))
        est.record_sample(0, 1.0)
        est.record_sample(10, 0)
        assert est._count == 0

    def test_record_alias(self, tmp_path):
        est = GenerationTimeEstimator(str(tmp_path / "t.json"))
        est.record(10, 1.0, engine="voxcpm2", segment_count=1)
        assert est._count == 1

    def test_sliding_window(self, tmp_path):
        est = GenerationTimeEstimator(str(tmp_path / "t.json"), max_entries=5)
        for i in range(10):
            est.record_sample(i + 1, (i + 1) * 0.1)
        assert len(est._samples) == 5

    def test_persistence_roundtrip(self, tmp_path):
        data_file = str(tmp_path / "times.json")
        est = GenerationTimeEstimator(data_file)
        est.record_sample(100, 6.0)
        est.flush()
        est2 = GenerationTimeEstimator(data_file)
        assert est2._count >= 1
        assert len(est2._samples) == 1

    def test_load_old_format(self, tmp_path):
        data_file = str(tmp_path / "times.json")
        with open(data_file, "w", encoding="utf-8") as f:
            json.dump({"samples": [{"char_count": 50, "duration": 3.0}], "count": 1}, f)
        est = GenerationTimeEstimator(data_file)
        assert len(est._samples) == 1
        assert est._samples[0] == (50, 3.0)

    def test_load_corrupt_file(self, tmp_path):
        data_file = str(tmp_path / "times.json")
        with open(data_file, "w", encoding="utf-8") as f:
            f.write("{broken json")
        est = GenerationTimeEstimator(data_file)
        assert len(est._samples) == 0
        assert est._count == 0

    def test_get_stats_empty(self, tmp_path):
        est = GenerationTimeEstimator(str(tmp_path / "t.json"))
        stats = est.get_stats()
        assert stats["sample_count"] == 0
        assert stats["model"] == "default (no data)"

    def test_get_stats_with_data(self, tmp_path):
        est = GenerationTimeEstimator(str(tmp_path / "t.json"))
        est.record_sample(10, 1.0)
        est.record_sample(20, 2.0)
        stats = est.get_stats()
        assert stats["sample_count"] == 2
        assert stats["avg_duration"] == pytest.approx(1.5)
        assert len(stats["recent_samples"]) == 2

    def test_reset(self, tmp_path):
        data_file = str(tmp_path / "t.json")
        est = GenerationTimeEstimator(data_file)
        est.record_sample(10, 1.0)
        est.reset()
        assert est._count == 0
        assert len(est._samples) == 0

    def test_estimate_with_char_count_kwarg(self, tmp_path):
        est = GenerationTimeEstimator(str(tmp_path / "t.json"))
        assert est.estimate(None, char_count=100) == pytest.approx(100 / 15.0 + 0.5)

    def test_confidence_levels(self, tmp_path):
        est = GenerationTimeEstimator(str(tmp_path / "t.json"))
        _, conf0 = est.estimate_with_confidence(10)
        assert conf0 == 0.0
        for i in range(3):
            est.record_sample(i + 1, 0.5)
        _, conf3 = est.estimate_with_confidence(10)
        assert conf3 == pytest.approx(0.3)
