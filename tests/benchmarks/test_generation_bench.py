"""Performance benchmark tests for TTS MultiModel.

These benchmarks use pytest-benchmark's ``benchmark`` fixture to collect
statistical data (rounds, iterations, mean, median, stddev, min, max).

Run locally::

    pytest tests/benchmarks/ -v --benchmark-only \\
        --benchmark-columns=rounds,iterations,mean,median,stddev,min,max

CI (benchmark.yml) runs the same command on every push to ``main`` and on
weekly schedule.
"""

import numpy as np
import pytest
from unittest.mock import patch

try:
    import pytest_benchmark  # noqa: F401
    BENCHMARK_AVAILABLE = True
except ImportError:
    BENCHMARK_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not BENCHMARK_AVAILABLE,
    reason="pytest-benchmark not installed. Install with: pip install pytest-benchmark",
)


class TestGenerationBenchmarks:
    """Benchmark suite for core generation utilities."""

    @pytest.mark.benchmark
    def test_text_splitting_performance(self, benchmark):
        """Benchmark text splitting for TTS (100 iterations per round)."""
        from integrated_app.generation import split_text_for_tts

        long_text = "这是一段测试文本。" * 100

        def _run():
            for _ in range(100):
                split_text_for_tts(long_text, max_chars=200)

        result = benchmark(_run)
        # Sanity: the function should return a list of segments
        segments = split_text_for_tts(long_text, max_chars=200)
        assert isinstance(segments, list) and len(segments) > 0

    @pytest.mark.benchmark
    def test_audio_merge_performance(self, benchmark):
        """Benchmark audio segment merging (50 iterations per round)."""
        from integrated_app.generation import merge_audio_segments

        segments = [np.random.randn(24000).astype(np.float32) for _ in range(10)]

        def _run():
            for _ in range(50):
                merge_audio_segments(segments, 24000, silence_duration=0.3)

        benchmark(_run)

    @pytest.mark.benchmark
    def test_cache_operations_performance(self, benchmark):
        """Benchmark LRU cache put+get operations (1000 pairs per round)."""
        from integrated_app.cache import LRUCache

        def _run():
            cache = LRUCache(maxsize=100)
            for i in range(1000):
                cache.put(f"key_{i}", f"value_{i}")
                cache.get(f"key_{i}")

        benchmark(_run)

    @pytest.mark.benchmark
    def test_cache_hit_miss_latency(self, benchmark):
        """Benchmark cache hit vs miss latency (500 ops each per round)."""
        from integrated_app.cache import LRUCache

        cache = LRUCache(maxsize=1000)
        for i in range(500):
            cache.put(f"key_{i}", f"value_{i}")

        def _run():
            for i in range(500):
                cache.get(f"key_{i % 500}")
            for i in range(500):
                cache.get(f"missing_{i}")

        benchmark(_run)

    @pytest.mark.benchmark
    def test_adaptive_cache_capacity_adaptation(self, benchmark):
        """Benchmark adaptive cache capacity adjustment (10 adaptations per round)."""
        from integrated_app.cache import AdaptiveLRUCache

        cache = AdaptiveLRUCache(default_maxsize=15)

        with patch.object(AdaptiveLRUCache, "_get_gpu_memory_percent", return_value=0.0):
            def _run():
                for _ in range(10):
                    cache.adapt_capacity()

            benchmark(_run)
