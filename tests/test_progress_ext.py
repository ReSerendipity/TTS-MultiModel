"""progress 模块单元测试 — 生成进度管理。

覆盖目标模块: app/integrated_app/progress.py
"""

from integrated_app.progress import ProgressManager


class TestProgressManager:
    def setup_method(self):
        self.pm = ProgressManager()

    def test_initial_state(self):
        state = self.pm.get_state()
        assert state["is_active"] is False
        assert state["is_cancelled"] is False

    def test_start_and_percentage(self):
        self.pm.start(total_segments=4)
        assert self.pm.get_state()["is_active"] is True
        assert self.pm.get_percentage() == 0.0

    def test_advance_segment(self):
        self.pm.start(total_segments=4)
        self.pm.advance_segment()
        assert self.pm.get_percentage() > 0.0

    def test_complete(self):
        self.pm.start(total_segments=1)
        self.pm.advance_segment()
        self.pm.complete()
        state = self.pm.get_state()
        assert state["is_complete"] is True

    def test_cancel(self):
        self.pm.start(total_segments=3)
        self.pm.cancel()
        state = self.pm.get_state()
        assert state["is_cancelled"] is True

    def test_mark_error(self):
        self.pm.start(total_segments=1)
        self.pm.mark_error("出错了")
        state = self.pm.get_state()
        assert state["is_error"] is True

    def test_update_phase(self):
        self.pm.start(total_segments=1)
        self.pm.update_phase("推理中")
        assert self.pm.get_state()["phase"] == "推理中"

    def test_render_html_progress_bar(self):
        self.pm.start(total_segments=2)
        html = self.pm.render_html_progress_bar()
        assert isinstance(html, str)
        assert "progress" in html.lower()

    def test_get_progress_html(self):
        self.pm.start(total_segments=1)
        html = self.pm.get_progress_html()
        assert isinstance(html, str)

    def test_get_status(self):
        self.pm.start(total_segments=1)
        status = self.pm.get_status()
        assert isinstance(status, dict)

    def test_reset(self):
        self.pm.start(total_segments=2)
        self.pm.complete()
        self.pm.reset()
        state = self.pm.get_state()
        assert state["is_active"] is False

    def test_eta_seconds(self):
        self.pm.start(total_segments=1)
        eta = self.pm.get_eta_seconds()
        assert eta is None or isinstance(eta, (int, float))
