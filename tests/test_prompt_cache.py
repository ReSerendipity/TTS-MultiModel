"""Tests for prompt cache with safe serialization."""

import os
import sys

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")


class TestPromptCacheSerialization:
    """Test prompt cache uses safe JSON serialization."""

    def test_cache_file_extension(self):
        """Cache files use .json extension."""
        from integrated_app.prompt_cache import _get_cache_file_path

        path = _get_cache_file_path("test_key")
        assert str(path).endswith(".json")

    def test_metadata_extension(self):
        """Metadata file uses .json extension."""
        from integrated_app.prompt_cache import _get_metadata_path

        path = _get_metadata_path()
        assert str(path).endswith(".json")

    def test_no_pickle_in_module(self):
        """Module does not expose pickle as a public attribute."""
        import integrated_app.prompt_cache as pc

        # pickle should not be in the module's public namespace
        public_attrs = [a for a in dir(pc) if not a.startswith("_")]
        assert "pickle" not in public_attrs

    def test_serialize_dict(self):
        """Can serialize a simple dict."""
        from integrated_app.prompt_cache import _deserialize_prompt_cache, _serialize_prompt_cache

        data = {"key": "value", "number": 42}
        serialized = _serialize_prompt_cache(data)
        deserialized = _deserialize_prompt_cache(serialized)
        assert deserialized["key"] == "value"
        assert deserialized["number"] == 42

    def test_serialize_nested_dict(self):
        """Can serialize nested dicts."""
        from integrated_app.prompt_cache import _deserialize_prompt_cache, _serialize_prompt_cache

        data = {"outer": {"inner": [1, 2, 3]}}
        serialized = _serialize_prompt_cache(data)
        deserialized = _deserialize_prompt_cache(serialized)
        assert deserialized["outer"]["inner"] == [1, 2, 3]

    def test_serialize_list(self):
        """Can serialize a list."""
        from integrated_app.prompt_cache import _deserialize_prompt_cache, _serialize_prompt_cache

        data = [1, "two", 3.0, None]
        serialized = _serialize_prompt_cache(data)
        deserialized = _deserialize_prompt_cache(serialized)
        assert deserialized == [1, "two", 3.0, None]


class TestPromptCacheOperations:
    """Test prompt cache CRUD operations."""

    @pytest.fixture
    def cache_dir(self, tmp_path):
        """Create a temporary cache directory."""
        return str(tmp_path / "prompt_cache")

    def _set_cache_dir(self, cache_dir):
        """Monkeypatch the global cache directory for isolated tests."""
        from pathlib import Path

        import integrated_app.prompt_cache as pc

        os.makedirs(cache_dir, exist_ok=True)
        pc._PROMPT_CACHE_DIR = Path(cache_dir)

    def test_cache_directory_creation(self, cache_dir):
        """Cache directory is created on first use."""
        from integrated_app.prompt_cache import _ensure_cache_dir

        self._set_cache_dir(cache_dir)
        _ensure_cache_dir()
        assert os.path.isdir(cache_dir)

    def test_save_and_load(self, cache_dir):
        """Can save and load a cache entry."""
        from integrated_app.prompt_cache import load_cached_prompt, save_prompt_cache

        self._set_cache_dir(cache_dir)
        audio_path = os.path.join(cache_dir, "test_audio.wav")
        # Create a dummy file so cache key can be computed from content
        with open(audio_path, "wb") as f:
            f.write(b"test_audio_data")
        save_prompt_cache(audio_path, {"data": "test_value"})
        result = load_cached_prompt(audio_path)
        assert result is not None
        assert result["data"] == "test_value"

    def test_cache_stats(self, cache_dir):
        """Can get cache statistics."""
        from integrated_app.prompt_cache import _ensure_cache_dir, get_cache_stats, save_prompt_cache

        self._set_cache_dir(cache_dir)
        _ensure_cache_dir()
        audio_path = os.path.join(cache_dir, "stats_audio.wav")
        with open(audio_path, "wb") as f:
            f.write(b"stats_audio_data")
        save_prompt_cache(audio_path, {"data": "test_value"})
        stats = get_cache_stats()
        assert isinstance(stats, dict)
        assert "entries" in stats
        assert stats["entries"] >= 1

    def test_clear_cache(self, cache_dir):
        """Can clear the cache."""
        from integrated_app.prompt_cache import (
            _ensure_cache_dir,
            clear_prompt_cache,
            get_cache_stats,
            load_cached_prompt,
            save_prompt_cache,
        )

        self._set_cache_dir(cache_dir)
        _ensure_cache_dir()
        audio_path = os.path.join(cache_dir, "clear_audio.wav")
        with open(audio_path, "wb") as f:
            f.write(b"clear_audio_data")
        save_prompt_cache(audio_path, {"data": "value"})
        assert load_cached_prompt(audio_path) is not None
        clear_prompt_cache()
        stats = get_cache_stats()
        assert stats["entries"] == 0
