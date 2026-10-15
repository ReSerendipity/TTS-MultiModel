"""历史库慢查询可度量基线测试（后端服务设计评估 P1）。

覆盖 _TimedConnection 计时挂钩：超阈值记录 WARNING 并累计进程内计数；
阈值 <=0 时完全关闭（纯透传，零行为差异）。
"""

from integrated_app.history_db import HistoryDatabase


def _make_db(tmp_path):
    return HistoryDatabase(str(tmp_path / "history.db"))


class TestSlowQueryBaseline:
    def test_slow_query_recorded(self, tmp_path, caplog):
        db = _make_db(tmp_path)
        db._slow_query_log_ms = 0.001  # 任意真实查询都会超阈值
        db.close_all()  # 重建线程本地连接，使新阈值在 _TimedConnection 上生效
        with caplog.at_level("WARNING", logger="tts_multimodel"):
            db._execute("SELECT 1")
        assert db.slow_query_count >= 1
        assert any("慢查询" in rec.getMessage() for rec in caplog.records)
        db.close_all()

    def test_disabled_when_threshold_zero(self, tmp_path):
        db = _make_db(tmp_path)
        db._slow_query_log_ms = 0
        db.close_all()  # 重建线程本地连接，使关闭态在 _TimedConnection 上生效
        db._execute("SELECT 1")
        assert db.slow_query_count == 0
        db.close_all()

    def test_default_threshold_from_config(self, tmp_path):
        """默认阈值取自 config.yaml history.slow_query_log_ms（本仓=200）。"""
        db = _make_db(tmp_path)
        assert db._slow_query_log_ms == 200
        db.close_all()
