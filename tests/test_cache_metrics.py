"""缓存命中率可观测基线测试（后端服务设计评估 P1）。

覆盖：
- 进程级累计命中统计（LRU + GenerationResultCache）
- /metrics 文本导出 tts_cache_* 指标族与 tts_slow_queries_total
- 告警规则 min_cache_hit_rate（样本量下限 20）
"""

from integrated_app.cache import (
    LRUCache,
    get_global_cache_stats,
    reset_global_cache_stats,
)
from integrated_app.generation_cache import (
    GenerationResultCache,
    get_global_generation_cache_stats,
    reset_global_generation_cache_stats,
)
from integrated_app.observability.alerting import AlertSeverity, evaluate_rules
from integrated_app.observability.metrics import build_metrics_text, collect_metrics


class TestGlobalCacheStats:
    """进程级累计命中统计。"""

    def test_lru_global_stats_accumulate(self):
        reset_global_cache_stats()
        cache = LRUCache(maxsize=5)
        cache.put("a", 1)
        assert cache.get("a") == 1  # hit
        assert cache.get("b") is None  # miss
        stats = get_global_cache_stats()
        assert stats["hits"] >= 1
        assert stats["misses"] >= 1
        assert 0.0 <= stats["hit_rate_percent"] <= 100.0

    def test_generation_cache_global_stats(self):
        reset_global_generation_cache_stats()
        cache = GenerationResultCache(ttl_seconds=60.0)
        key = cache.make_cache_key("voxcpm2", "你好")
        cache.put(key, "audio-ref")
        assert cache.get(key) == "audio-ref"  # hit
        assert cache.get("no-such-key") is None  # miss
        stats = get_global_generation_cache_stats()
        assert stats["hits"] >= 1
        assert stats["misses"] >= 1

    def test_reset_stats_keeps_global(self):
        """实例 reset_stats() 不影响进程级累计统计（口径：进程生命周期）。"""
        reset_global_cache_stats()
        cache = LRUCache(maxsize=5)
        cache.put("a", 1)
        cache.get("a")
        cache.reset_stats()
        assert get_global_cache_stats()["hits"] >= 1


class TestMetricsExport:
    """/metrics 文本导出。"""

    def test_metrics_text_contains_cache_family(self):
        text = build_metrics_text()
        assert "# HELP tts_cache_requests_total" in text
        assert "# TYPE tts_cache_requests_total counter" in text
        assert 'tts_cache_requests_total{family="lru",result="hit"}' in text
        assert "# TYPE tts_cache_hit_rate_percent gauge" in text

    def test_metrics_text_contains_slow_query_total(self):
        assert "tts_slow_queries_total" in build_metrics_text()

    def test_collect_metrics_flat_keys(self):
        collected = collect_metrics()
        assert "tts_cache_hit_rate_percent" in collected
        assert "tts_cache_hit_rate_samples" in collected


class TestCacheHitRateAlertRule:
    """min_cache_hit_rate 告警规则（样本量下限 20）。"""

    def test_triggers_below_threshold(self):
        alerts = evaluate_rules(
            {"tts_cache_hit_rate_percent": 30.0, "tts_cache_hit_rate_samples": 50},
            {"min_cache_hit_rate": 50.0},
        )
        hits = [a for a in alerts if a.source == "cache_hit_rate_slo"]
        assert len(hits) == 1
        assert hits[0].severity == AlertSeverity.WARNING

    def test_no_trigger_above_threshold(self):
        alerts = evaluate_rules(
            {"tts_cache_hit_rate_percent": 80.0, "tts_cache_hit_rate_samples": 50},
            {"min_cache_hit_rate": 50.0},
        )
        assert not [a for a in alerts if a.source == "cache_hit_rate_slo"]

    def test_no_trigger_below_sample_floor(self):
        alerts = evaluate_rules(
            {"tts_cache_hit_rate_percent": 10.0, "tts_cache_hit_rate_samples": 5},
            {"min_cache_hit_rate": 50.0},
        )
        assert not [a for a in alerts if a.source == "cache_hit_rate_slo"]

    def test_no_trigger_when_metric_absent(self):
        assert not [a for a in evaluate_rules({}) if a.source == "cache_hit_rate_slo"]
