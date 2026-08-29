"""Smoke tests - minimal set to verify core functionality works.

These tests should:
1. Run quickly (<30s total)
2. Not require GPU or model loading
3. Cover critical paths that would indicate a broken build

Usage:
    pytest -m smoke  # Run only smoke tests
"""

import os
import sys

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")


pytestmark = pytest.mark.smoke


class TestConfigLoading:
    """Verify configuration loads without errors."""

    def test_config_loads_successfully(self):
        """Config should load from config.yaml without exceptions."""
        from integrated_app.config import get_config

        config = get_config()
        assert config is not None

    def test_config_has_api_auth_section(self):
        """Config should have api_auth section."""
        from integrated_app.config import get_config

        config = get_config()
        assert hasattr(config, "api_auth")


class TestCoreUtils:
    """Verify core utility functions work."""

    def test_progress_manager_create(self):
        """ProgressManager should instantiate and track progress."""
        from integrated_app.progress import ProgressManager

        pm = ProgressManager()
        pm.start(total_segments=5, phase="测试")
        state = pm.get_state()
        assert state["phase"] == "测试"
        assert state["total_segments"] == 5
        pm.complete()
        assert pm.get_state()["is_complete"] is True

    def test_text_segmenter_can_be_imported(self):
        """Text segmenter module should be importable."""
        from integrated_app.text_segmenter import TextSegmenter

        # Create with valid params (min_chars cannot exceed max_chars)
        segmenter = TextSegmenter(max_chars=100, min_chars=50)
        assert segmenter is not None


class TestModels:
    """Test config models."""

    def test_generation_config_defaults(self):
        """GenerationConfig should have sensible defaults."""
        from integrated_app.config_models import GenerationConfig

        config = GenerationConfig()
        assert config.max_chars_per_segment == 200
        assert config.default_sample_rate == 24000


class TestRegistryPattern:
    """Verify registry pattern exists."""

    def test_engine_registry_class_exists(self):
        """EngineRegistry class should be importable."""
        from integrated_app.engine_interface import EngineRegistry

        assert EngineRegistry is not None


class TestImportSanity:
    """Basic import sanity checks for critical modules."""

    def test_task_queue_module(self):
        """Task queue module should be importable."""
        from integrated_app.task_queue import init_queue, shutdown_queue

        assert init_queue is not None
        assert shutdown_queue is not None

    def test_cache_utils(self):
        """Cache utilities should be importable."""
        from integrated_app.cache import LRUCache

        cache = LRUCache(maxsize=10)
        cache.put("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_audio_processing_module(self):
        """Audio processing module should be importable."""
        import integrated_app.audio_processing as ap

        assert ap is not None
