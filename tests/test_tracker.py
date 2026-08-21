"""tracker 模块单元测试 — 生成任务队列追踪。

覆盖目标模块: app/integrated_app/tracker.py
"""

import pytest

from integrated_app.tracker import GenerationTracker


class TestGenerationTracker:
    def setup_method(self):
        self.tracker = GenerationTracker()

    def test_initial_state(self):
        assert self.tracker.queue_depth == 0
        assert self.tracker.avg_gen_time == pytest.approx(15.0)
        assert self.tracker.phase == "空闲"

    def test_start_generation_increments(self):
        depth = self.tracker.start_generation()
        assert depth == 1
        assert self.tracker.queue_depth == 1

    def test_end_generation_ema_update(self):
        self.tracker.start_generation()
        self.tracker.end_generation(elapsed=5.0)
        # EMA: 0.8*15 + 0.2*5 = 13.0
        assert self.tracker.avg_gen_time == pytest.approx(13.0)
        assert self.tracker.queue_depth == 0

    def test_end_generation_never_negative(self):
        self.tracker.end_generation(1.0)
        assert self.tracker.queue_depth == 0

    def test_estimate_wait(self):
        self.tracker.start_generation()
        self.tracker.start_generation()
        wait = self.tracker.estimate_wait()
        assert wait == pytest.approx(15.0 * 2)

    def test_status_text_idle(self):
        assert self.tracker.status_text() == "空闲"

    def test_status_text_queued(self):
        self.tracker.start_generation()
        assert "队列: 1" in self.tracker.status_text()

    def test_get_info(self):
        info = self.tracker.get_info()
        assert info["queue_depth"] == 0
        assert info["avg_gen_time"] == pytest.approx(15.0)
        assert "status_text" in info

    def test_update_phase(self):
        self.tracker.update_phase("生成中")
        assert self.tracker.phase == "生成中"

    def test_reset(self):
        self.tracker.start_generation()
        self.tracker.end_generation(3.0)
        self.tracker.update_phase("生成中")
        self.tracker.reset()
        assert self.tracker.queue_depth == 0
        assert self.tracker.avg_gen_time == pytest.approx(15.0)
        assert self.tracker.phase == "空闲"

    def test_concurrent_start(self):
        import threading

        threads = [threading.Thread(target=self.tracker.start_generation) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert self.tracker.queue_depth == 10
