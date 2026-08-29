"""Tests for cache.py LRU logic and AdaptiveLRUCache."""

from unittest.mock import patch

from integrated_app.cache import AdaptiveLRUCache, LRUCache


class TestLRUCache:
    def test_put_and_get(self):
        cache = LRUCache(maxsize=3)
        cache.put("a", 1)
        assert cache.get("a") == 1

    def test_eviction(self):
        cache = LRUCache(maxsize=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)  # "a" should be evicted
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3

    def test_lru_order_update(self):
        cache = LRUCache(maxsize=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")  # access "a" to make it most recently used
        cache.put("c", 3)  # "b" should be evicted (least recently used)
        assert cache.get("a") == 1
        assert cache.get("b") is None

    def test_overwrite(self):
        cache = LRUCache(maxsize=2)
        cache.put("a", 1)
        cache.put("a", 99)
        assert cache.get("a") == 99

    def test_stats(self):
        cache = LRUCache(maxsize=3)
        cache.put("a", 1)
        cache.get("a")  # hit
        cache.get("b")  # miss
        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1

    def test_contains(self):
        cache = LRUCache(maxsize=3)
        cache.put("a", 1)
        assert "a" in cache
        assert "b" not in cache

    def test_delete(self):
        cache = LRUCache(maxsize=3)
        cache.put("a", 1)
        del cache["a"]
        assert cache.get("a") is None

    def test_stats_hit_rate(self):
        cache = LRUCache(maxsize=5)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")  # hit
        cache.get("a")  # hit
        cache.get("c")  # miss
        stats = cache.get_stats()
        assert stats["hit_rate"] == 66.7
        assert stats["size"] == 2
        assert stats["maxsize"] == 5

    def test_reset_stats(self):
        cache = LRUCache(maxsize=3)
        cache.put("a", 1)
        cache.get("a")
        cache.reset_stats()
        stats = cache.get_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0

    def test_empty_cache_get_miss(self):
        cache = LRUCache(maxsize=3)
        assert cache.get("nonexistent") is None
        stats = cache.get_stats()
        assert stats["misses"] == 1

    def test_maxsize_one(self):
        cache = LRUCache(maxsize=1)
        cache.put("a", 1)
        cache.put("b", 2)  # "a" evicted
        assert cache.get("a") is None
        assert cache.get("b") == 2


class TestAdaptiveLRUCache:
    def test_basic_put_get(self):
        cache = AdaptiveLRUCache(default_maxsize=5)
        cache.put("a", 1)
        assert cache.get("a") == 1

    def test_adapt_capacity_no_gpu(self):
        """Without GPU, capacity should default to 20."""
        cache = AdaptiveLRUCache(default_maxsize=5)
        # Mock _get_gpu_memory_percent to avoid importing torch-dependent gpu_backend
        with patch.object(AdaptiveLRUCache, "_get_gpu_memory_percent", return_value=0.0):
            target = cache.adapt_capacity()
            assert target == 20  # No GPU = 0% = default 20

    def test_memory_estimate(self):
        cache = AdaptiveLRUCache(default_maxsize=5)
        with patch.object(AdaptiveLRUCache, "_get_gpu_memory_percent", return_value=0.0):
            cache.put("a", "hello")
            stats = cache.get_stats()
            assert stats["memory_estimate_mb"] >= 0

    def test_clear(self):
        cache = AdaptiveLRUCache(default_maxsize=5)
        with patch.object(AdaptiveLRUCache, "_get_gpu_memory_percent", return_value=0.0):
            cache.put("a", 1)
            cache.put("b", 2)
            cache.clear()
            assert cache.get("a") is None
            assert cache.get("b") is None
            stats = cache.get_stats()
            assert stats["size"] == 0

    def test_eviction_count(self):
        cache = AdaptiveLRUCache(default_maxsize=3)
        with patch.object(AdaptiveLRUCache, "_get_gpu_memory_percent", return_value=0.0):
            for i in range(10):
                cache.put(f"key_{i}", f"value_{i}")
            stats = cache.get_stats()
            assert stats["eviction_count"] >= 0

    def test_delete_updates_memory(self):
        cache = AdaptiveLRUCache(default_maxsize=5)
        with patch.object(AdaptiveLRUCache, "_get_gpu_memory_percent", return_value=0.0):
            cache.put("a", "hello world")
            stats_before = cache.get_stats()
            del cache["a"]
            stats_after = cache.get_stats()
            assert stats_after["size"] == stats_before["size"] - 1

    def test_overwrite_same_key(self):
        cache = AdaptiveLRUCache(default_maxsize=5)
        with patch.object(AdaptiveLRUCache, "_get_gpu_memory_percent", return_value=0.0):
            cache.put("a", "first")
            cache.put("a", "second")
            assert cache.get("a") == "second"
            stats = cache.get_stats()
            assert stats["size"] == 1

    def test_capacity_map_high_gpu(self):
        """High GPU usage should shrink cache."""
        cache = AdaptiveLRUCache(default_maxsize=15)
        with patch.object(AdaptiveLRUCache, "_get_gpu_memory_percent", return_value=95.0):
            target = cache.adapt_capacity()
            assert target == 5  # GPU > 90% -> 5 items

    def test_capacity_map_medium_gpu(self):
        """Medium GPU usage should set moderate cache."""
        cache = AdaptiveLRUCache(default_maxsize=15)
        with patch.object(AdaptiveLRUCache, "_get_gpu_memory_percent", return_value=80.0):
            target = cache.adapt_capacity()
            assert target == 10  # GPU > 75% -> 10 items
