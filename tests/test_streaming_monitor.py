"""streaming_monitor 模块单元测试 — 流式音频质量监测。

覆盖目标模块: app/integrated_app/streaming_monitor.py
"""

import numpy as np
import pytest

from integrated_app.streaming_monitor import ChunkQualityReport, StreamingQualityMonitor


class TestChunkQualityReport:
    def test_summary_normal(self):
        report = ChunkQualityReport(rms=0.1)
        assert report.summary == "正常"

    def test_summary_low_volume(self):
        report = ChunkQualityReport(rms=0.0)
        assert "极低音量" in report.summary

    def test_summary_clipping(self):
        report = ChunkQualityReport(has_clipping=True, peak_amplitude=1.0)
        assert "削波" in report.summary

    def test_summary_silence(self):
        report = ChunkQualityReport(silence_ratio=0.9)
        assert "高静音比" in report.summary


class TestStreamingQualityMonitor:
    def setup_method(self):
        self.monitor = StreamingQualityMonitor(expected_sr=24000)

    def test_normal_chunk(self):
        chunk = (0.1 * np.sin(np.linspace(0, 100, 1000))).astype(np.float32)
        report = self.monitor.analyze_chunk(chunk)
        assert report.has_issue is False
        assert report.peak_amplitude > 0.05
        assert report.rms > 0.0

    def test_empty_chunk(self):
        report = self.monitor.analyze_chunk(np.array([], dtype=np.float32))
        assert report.has_issue is True
        assert "空" in report.issue_description

    def test_silent_chunk(self):
        chunk = np.zeros(500, dtype=np.float32)
        report = self.monitor.analyze_chunk(chunk)
        assert report.silence_ratio == 1.0
        assert report.has_issue is True

    def test_clipping_chunk(self):
        chunk = np.ones(500, dtype=np.float32)
        report = self.monitor.analyze_chunk(chunk)
        assert report.has_clipping is True
        assert report.has_issue is True

    def test_bytes_input(self):
        chunk = b"\x00" * 400
        report = self.monitor.analyze_chunk(chunk)
        assert report.has_issue is True  # 全零 → 静音

    def test_multichannel_flatten(self):
        chunk = np.stack([np.zeros(300), np.ones(300)], axis=-1)
        report = self.monitor.analyze_chunk(chunk)
        assert report.peak_amplitude == 1.0

    def test_get_summary(self):
        self.monitor.analyze_chunk(np.zeros(100, dtype=np.float32))
        self.monitor.analyze_chunk((0.5 * np.ones(100)).astype(np.float32))
        summary = self.monitor.get_summary()
        assert summary["total_chunks"] == 2
        assert summary["total_samples"] == 200
        assert summary["total_duration_s"] == pytest.approx(200 / 24000)
        assert summary["clipping_chunks"] >= 0
        assert summary["avg_rms"] > 0
