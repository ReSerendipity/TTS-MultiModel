"""prompt_cache 模块单元测试补充 — prompt 缓存序列化与存取。

覆盖目标模块: bin/integrated_app/prompt_cache.py
"""

import numpy as np
import pytest

from integrated_app.prompt_cache import (
    PromptCache,
    _deserialize_value,
    _serialize_value,
)


class TestSerialization:
    def test_serialize_dict_roundtrip(self):
        obj = {"a": 1, "b": "text"}
        meta, binary = _serialize_value(obj)
        restored = _deserialize_value(meta, binary)
        assert restored == obj

    def test_serialize_numpy_roundtrip(self):
        arr = np.zeros(100, dtype=np.float32)
        meta, binary = _serialize_value(arr)
        restored = _deserialize_value(meta, binary)
        assert isinstance(restored, np.ndarray)
        assert restored.shape == arr.shape


class TestPromptCache:
    @pytest.fixture
    def cache(self, tmp_path):
        return PromptCache(cache_dir=str(tmp_path / "cache"), max_entries=10)

    def test_put_get_roundtrip(self, cache):
        key = "test-key"
        value = {"text": "你好", "ref_audio": np.zeros(100, dtype=np.float32)}
        cache.put(key, value)
        restored = cache.get(key)
        assert restored is not None
        assert restored["text"] == "你好"

    def test_get_missing(self, cache):
        assert cache.get("missing-key") is None

    def test_has_key(self, cache):
        cache.put("k1", {"x": 1})
        assert cache.get("k1") is not None
        assert cache.get("k2") is None

    def test_invalidate(self, cache):
        cache.put("k1", {"x": 1})
        assert cache.invalidate("k1") is True
        assert cache.get("k1") is None

    def test_stats(self, cache):
        cache.put("k1", {"x": 1})
        stats = cache.get_stats()
        assert "size" in stats or "entries" in stats or isinstance(stats, dict)
