"""生成结果缓存（app/integrated_app/generation_cache.py）单元测试。

覆盖：确定性缓存键、命中/未命中、TTL 过期、LRU 淘汰、统计与清理。
对应评估整改 T9「相同生成请求结果级复用」的落地验证。
"""

import os
import sys

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from integrated_app.generation_cache import GenerationResultCache  # noqa: E402


class TestCacheKey:
    def test_same_input_same_key(self):
        k1 = GenerationResultCache.make_cache_key("voxcpm2", "你好世界", cfg_value=2.0, seed=42)
        k2 = GenerationResultCache.make_cache_key("voxcpm2", "你好世界", cfg_value=2.0, seed=42)
        assert k1 == k2
        assert len(k1) == 64  # SHA-256 hex

    def test_key_is_order_independent(self):
        k1 = GenerationResultCache.make_cache_key("voxcpm2", "hi", a=1, b=2)
        k2 = GenerationResultCache.make_cache_key("voxcpm2", "hi", b=2, a=1)
        assert k1 == k2

    def test_different_input_different_key(self):
        k1 = GenerationResultCache.make_cache_key("voxcpm2", "text a")
        k2 = GenerationResultCache.make_cache_key("voxcpm2", "text b")
        k3 = GenerationResultCache.make_cache_key("indextts2", "text a")
        assert k1 != k2
        assert k1 != k3

    def test_text_normalization(self):
        k1 = GenerationResultCache.make_cache_key("voxcpm2", "你好\n世界")
        k2 = GenerationResultCache.make_cache_key("voxcpm2", "你好 世界")
        assert k1 == k2  # 换行/回车被归一化为空格并 strip


class TestGetPut:
    def test_miss_returns_none(self):
        cache = GenerationResultCache(ttl_seconds=300, max_entries=8)
        assert cache.get("nope") is None

    def test_put_then_get_hits(self):
        cache = GenerationResultCache(ttl_seconds=300, max_entries=8)
        key = GenerationResultCache.make_cache_key("voxcpm2", "hello")
        cache.put(key, b"audio-bytes")
        assert cache.get(key) == b"audio-bytes"

    def test_expired_entry_returns_none(self):
        cache = GenerationResultCache(ttl_seconds=300, ttl_jitter_ratio=0.0, max_entries=8)
        key = GenerationResultCache.make_cache_key("voxcpm2", "hello")
        cache.put(key, b"audio-bytes")
        # 直接把存储里的过期时间拨回过去，模拟 TTL 已到期（避免等待真实时间）
        expire_at, _ = cache._store[key]
        cache._store[key] = (expire_at - 1000.0, b"audio-bytes")
        assert cache.get(key) is None
        assert key not in cache._store  # 过期条目已清理

    def test_lru_eviction(self):
        cache = GenerationResultCache(ttl_seconds=300, max_entries=2)
        k1 = GenerationResultCache.make_cache_key("voxcpm2", "one")
        k2 = GenerationResultCache.make_cache_key("voxcpm2", "two")
        k3 = GenerationResultCache.make_cache_key("voxcpm2", "three")
        cache.put(k1, 1)
        cache.put(k2, 2)
        cache.get(k1)  # 访问 k1 → k2 变为最久未使用
        cache.put(k3, 3)
        assert cache.get(k2) is None  # k2 被淘汰
        assert cache.get(k1) == 1
        assert cache.get(k3) == 3

    def test_get_moves_entry_to_most_recent(self):
        cache = GenerationResultCache(ttl_seconds=300, max_entries=2)
        k1 = GenerationResultCache.make_cache_key("voxcpm2", "one")
        k2 = GenerationResultCache.make_cache_key("voxcpm2", "two")
        k3 = GenerationResultCache.make_cache_key("voxcpm2", "three")
        cache.put(k1, 1)
        cache.put(k2, 2)
        cache.get(k2)  # k2 变最新
        cache.put(k3, 3)
        assert cache.get(k1) is None  # k1 被淘汰
        assert cache.get(k2) == 2

    def test_clear_empties_store(self):
        cache = GenerationResultCache(ttl_seconds=300, max_entries=8)
        key = GenerationResultCache.make_cache_key("voxcpm2", "hello")
        cache.put(key, b"audio")
        cache.clear()
        assert cache.get(key) is None
        assert cache.get_stats()["hits"] == 0


class TestStats:
    def test_hit_rate(self):
        cache = GenerationResultCache(ttl_seconds=300, max_entries=8)
        key = GenerationResultCache.make_cache_key("voxcpm2", "hello")
        cache.get(key)  # miss
        cache.put(key, b"audio")
        cache.get(key)  # hit
        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1  # 仅第一次 get 未命中
        assert stats["hit_rate"] == 50.0
        assert stats["size"] == 1
        assert stats["max_entries"] == 8

    def test_zero_sample_hit_rate_is_zero(self):
        cache = GenerationResultCache()
        assert cache.get_stats()["hit_rate"] == 0.0

    def test_constructor_clamps(self):
        cache = GenerationResultCache(ttl_seconds=-5, max_entries=0, ttl_jitter_ratio=5)
        assert cache._ttl_seconds == 0.0
        assert cache._max_entries == 1
        assert cache._ttl_jitter_ratio == 1.0

    def test_jitter_never_shrinks_below_one_second(self):
        cache = GenerationResultCache(ttl_seconds=0.5, max_entries=8)
        key = GenerationResultCache.make_cache_key("voxcpm2", "hello")
        cache.put(key, b"audio")
        # 最差情况 TTL 也不能小于 1s，保证极短 TTL 不产生立即过期抖动
        _, value = cache._store[key]
        assert value == b"audio"
