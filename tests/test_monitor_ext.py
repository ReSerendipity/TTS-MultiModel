"""monitor 模块单元测试 — 健康监控与显存熔断。

覆盖目标模块: app/integrated_app/monitor.py
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


class TestLatencyHistogramAndErrorTypes:
    """运维稳定性评估 P1 新增：失败分类计数、延迟直方图与 p95 分位数。"""

    def setup_method(self):
        self.monitor = HealthMonitor()

    def test_error_type_counts_bounded(self):
        """未知类型归入 other，cardinality 有界。"""
        self.monitor.record_generation_error_type("timeout")
        self.monitor.record_generation_error_type("oom")
        self.monitor.record_generation_error_type("bogus")
        self.monitor.record_generation_error_type("also-bogus")
        counts = self.monitor.error_type_counts()
        assert counts["timeout"] == 1
        assert counts["oom"] == 1
        assert counts["other"] == 2  # 两个未知值归 other
        assert set(counts) == {"timeout", "oom", "param", "safety", "other"}

    def test_latency_cumulative_buckets(self):
        """直方图为累计（cumulative）语义：样本计入所有 ≥ 其值的桶。"""
        self.monitor.record_latency(0.3)  # 落入所有桶
        self.monitor.record_latency(40.0)  # 只落入 >40 的桶
        buckets, total, count = self.monitor.latency_observations()
        assert count == 2
        assert buckets["0.5"] == 1  # 仅 0.3s 样本
        assert buckets["30"] == 1  # 仅 0.3s（40 > 30）
        assert buckets["60"] == 2  # 两样本都 ≤ 60
        assert abs(total - 40.3) < 1e-6

    def test_latency_quantile_p95(self):
        """p95 分位数：全 ≤30s 时应在合理范围。"""
        for _ in range(20):
            self.monitor.record_latency(5.0)
        p95 = self.monitor.latency_quantile(0.95)
        assert 0.0 < p95 <= 30.0

    def test_latency_quantile_no_samples(self):
        """无样本时 p95 返回 0。"""
        assert self.monitor.latency_quantile(0.95) == 0.0

    def test_latency_over_max_bucket_counts_inf(self):
        """超最大桶（600s）样本计入 count 但不进任何有限桶。"""
        self.monitor.record_latency(700.0)
        buckets, _total, count = self.monitor.latency_observations()
        assert count == 1
        assert all(v == 0 for v in buckets.values())  # 无有限桶命中

    def test_oom_auto_recovery_counter(self):
        """OOM 受控自动重载成功计数可导出。"""
        self.monitor.record_oom_auto_recovery()
        metrics = self.monitor.get_metrics()
        assert metrics["total_oom_auto_recoveries"] >= 1

    def test_get_metrics_exposes_new_fields(self):
        """get_metrics 暴露 error_type_counts / latency_p95_seconds 等新增键。"""
        self.monitor.record_latency(2.0)
        self.monitor.record_generation_error_type("safety")
        metrics = self.monitor.get_metrics()
        assert "error_type_counts" in metrics
        assert "latency_buckets" in metrics
        assert "latency_p95_seconds" in metrics
        assert "latency_p50_seconds" in metrics
