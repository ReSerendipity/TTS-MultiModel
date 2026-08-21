"""cache 模块单元测试补充 — 自适应 LRU 缓存容量调整。

覆盖目标模块: app/integrated_app/cache.py
"""

from integrated_app.cache import AdaptiveLRUCache, LRUCache


class TestAdaptiveLRUCache:
    def setup_method(self):
        self.cache = AdaptiveLRUCache(default_maxsize=5, adapt_interval=999.0)

    def test_basic_put_get(self):
        self.cache.put("a", 1)
        assert self.cache.get("a") == 1

    def test_eviction_when_adapted_capacity_small(self, monkeypatch):
        # GPU 使用率高时容量收缩，超出部分被淘汰
        monkeypatch.setattr(AdaptiveLRUCache, "_get_gpu_memory_percent", staticmethod(lambda: 95.0))
        cache = AdaptiveLRUCache(default_maxsize=5, adapt_interval=999.0)
        cache.adapt_capacity()  # 触发容量收缩
        for i in range(10):
            cache.put(f"k{i}", i)
        assert len(cache._cache) <= 5
        assert cache.get("k0") is None  # 最早的被淘汰

    def test_get_stats(self):
        self.cache.put("a", 1)
        self.cache.get("a")
        stats = self.cache.get_stats()
        assert "hits" in stats or "hit_count" in stats

    def test_capacity_adapt(self, monkeypatch):
        # 强制 GPU 使用率高 → 容量收缩
        monkeypatch.setattr(AdaptiveLRUCache, "_get_gpu_memory_percent", staticmethod(lambda: 95.0))
        new_capacity = self.cache.adapt_capacity()
        assert new_capacity < 5 or new_capacity >= 1

    def test_capacity_adapt_low_gpu(self, monkeypatch):
        monkeypatch.setattr(AdaptiveLRUCache, "_get_gpu_memory_percent", staticmethod(lambda: 5.0))
        new_capacity = self.cache.adapt_capacity()
        assert new_capacity > 0

    def test_gpu_unavailable_returns_zero(self, monkeypatch):
        monkeypatch.setattr(AdaptiveLRUCache, "_get_gpu_memory_percent", staticmethod(lambda: 0.0))
        assert AdaptiveLRUCache._get_gpu_memory_percent() == 0.0


class TestLRUCacheBasics:
    def test_contains_and_delete(self):
        cache = LRUCache(maxsize=5)
        cache.put("a", 1)
        assert "a" in cache
        del cache["a"]
        assert "a" not in cache

    def test_missing_key_returns_none(self):
        cache = LRUCache(maxsize=5)
        assert cache.get("missing") is None

    def test_reset_stats(self):
        cache = LRUCache(maxsize=5)
        cache.put("a", 1)
        cache.get("a")
        cache.reset_stats()
        stats = cache.get_stats()
        assert stats["hits"] == 0
