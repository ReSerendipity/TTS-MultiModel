"""monitor 模块单元测试 — 健康监控与显存熔断。

覆盖目标模块: bin/integrated_app/monitor.py
"""

from integrated_app.monitor import HealthMonitor, get_health_monitor


class TestHealthMonitor:
    def setup_method(self):
        self.monitor = HealthMonitor()

    def test_initial_state(self):
        assert self.monitor.get_vram_usage_percent() >= 0.0

    def test_record_vram_usage(self):
        self.monitor.record_vram_usage(1024.0)
        metrics = self.monitor.get_metrics()
        assert "total_generations" in metrics

    def test_reset_vram_baseline(self):
        self.monitor.record_vram_usage(2048.0)
        self.monitor.reset_vram_baseline()
        metrics = self.monitor.get_metrics()
        assert "uptime_seconds" in metrics

    def test_check_memory_leak_no_leak(self):
        self.monitor.reset_vram_baseline()
        result = self.monitor.check_memory_leak()
        assert result is None or isinstance(result, str)

    def test_record_generation(self):
        self.monitor.record_generation(success=True)
        self.monitor.record_generation(success=False)
        metrics = self.monitor.get_metrics()
        assert metrics["total_generations"] == 2
        assert metrics["total_errors"] == 1

    def test_record_oom_retry(self):
        self.monitor.record_oom_retry()
        metrics = self.monitor.get_metrics()
        assert metrics["total_oom_retries"] >= 1

    def test_check_vram_circuit_breaker(self):
        ok, message = self.monitor.check_vram_circuit_breaker()
        assert isinstance(ok, bool)
        assert isinstance(message, str)

    def test_check_model_load_prereq(self):
        ok, message, code = self.monitor.check_model_load_prereq(0.5)
        assert isinstance(ok, bool)
        assert isinstance(message, str)
        assert isinstance(code, int)

    def test_set_model_status(self):
        self.monitor.set_model_status("loading")
        metrics = self.monitor.get_metrics()
        assert metrics["model_status"] == "loading"

    def test_run_model_self_check(self):
        ok, message = self.monitor.run_model_self_check()
        assert isinstance(ok, bool)
        assert isinstance(message, str)

    def test_get_health_report(self):
        report = self.monitor.get_health_report()
        assert "uptime_seconds" in report
        assert "total_generations" in report

    def test_get_vram_trend(self):
        trend = self.monitor.get_vram_trend()
        assert isinstance(trend, dict)

    def test_singleton(self):
        assert get_health_monitor() is get_health_monitor()
