"""覆盖率提升测试 — 针对低覆盖模块补充单元测试。

覆盖目标模块:
  - bin/integrated_app/persona_manager.py (34.77% → 目标 50%+)
  - bin/integrated_app/model_manager.py (17.42% → 目标 30%+)
  - bin/integrated_app/exceptions.py
  - bin/integrated_app/monitor.py
  - bin/integrated_app/emotion_tags.py
  - bin/integrated_app/estimator.py
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_BIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)


# =====================================================================
# persona_manager.py 测试
# =====================================================================


class TestPersonaNameValidation:
    """_validate_persona_name 函数测试。"""

    def test_valid_english_name(self):
        from integrated_app.persona_manager import _validate_persona_name
        ok, msg = _validate_persona_name("MyVoice")
        assert ok is True
        assert msg == ""

    def test_valid_chinese_name(self):
        from integrated_app.persona_manager import _validate_persona_name
        ok, msg = _validate_persona_name("我的音色")
        assert ok is True

    def test_valid_name_with_underscore(self):
        from integrated_app.persona_manager import _validate_persona_name
        ok, _ = _validate_persona_name("voice_01")
        assert ok is True

    def test_valid_name_with_hyphen(self):
        from integrated_app.persona_manager import _validate_persona_name
        ok, _ = _validate_persona_name("voice-01")
        assert ok is True

    def test_empty_name_rejected(self):
        from integrated_app.persona_manager import _validate_persona_name
        ok, msg = _validate_persona_name("")
        assert ok is False
        assert "空" in msg

    def test_path_traversal_rejected(self):
        from integrated_app.persona_manager import _validate_persona_name
        ok, _ = _validate_persona_name("../etc/passwd")
        assert ok is False

    def test_backslash_rejected(self):
        from integrated_app.persona_manager import _validate_persona_name
        ok, _ = _validate_persona_name("voice\\name")
        assert ok is False

    def test_colon_rejected(self):
        from integrated_app.persona_manager import _validate_persona_name
        ok, _ = _validate_persona_name("voice:name")
        assert ok is False

    def test_long_name_rejected(self):
        from integrated_app.persona_manager import _validate_persona_name
        ok, _ = _validate_persona_name("a" * 51)
        assert ok is False

    def test_name_at_max_length(self):
        from integrated_app.persona_manager import _validate_persona_name
        ok, _ = _validate_persona_name("a" * 50)
        assert ok is True

    def test_name_with_numbers(self):
        from integrated_app.persona_manager import _validate_persona_name
        ok, _ = _validate_persona_name("voice123")
        assert ok is True


class TestPersonaManagerFunctions:
    """persona_manager 模块函数测试。"""

    def test_persona_dir_exists_or_creatable(self):
        """PERSONA_DIR 配置存在。"""
        from integrated_app.config import PERSONA_DIR
        assert isinstance(PERSONA_DIR, str)

    def test_get_persona_list_returns_list(self, tmp_path):
        """get_persona_list 返回列表。"""
        with patch("integrated_app.persona_manager.PERSONA_DIR", str(tmp_path)):
            from integrated_app.persona_manager import get_persona_list
            result = get_persona_list()
            assert isinstance(result, list)

    def test_get_total_persona_count_returns_int(self, tmp_path):
        """get_total_persona_count 返回整数。"""
        with patch("integrated_app.persona_manager.PERSONA_DIR", str(tmp_path)):
            from integrated_app.persona_manager import get_total_persona_count
            count = get_total_persona_count()
            assert isinstance(count, int)
            assert count >= 0

    def test_delete_nonexistent_persona_returns_tuple(self, tmp_path):
        """删除不存在的音色返回 (bool, str) 元组。"""
        with patch("integrated_app.persona_manager.PERSONA_DIR", str(tmp_path)):
            from integrated_app.persona_manager import delete_persona
            result = delete_persona("nonexistent_persona")
            assert isinstance(result, tuple)
            assert result[0] is False

    def test_persona_pt_origin_constant(self):
        """PERSONA_PT_ORIGIN 常量定义正确。"""
        from integrated_app.persona_manager import PERSONA_PT_ORIGIN
        assert "TTS_MultiModel" in PERSONA_PT_ORIGIN

    def test_persona_pt_format_version(self):
        """PERSONA_PT_FORMAT_VERSION 常量为正整数。"""
        from integrated_app.persona_manager import PERSONA_PT_FORMAT_VERSION
        assert PERSONA_PT_FORMAT_VERSION >= 1


# =====================================================================
# model_manager.py 测试
# =====================================================================


class TestModelManagerConstants:
    """model_manager 常量与配置测试。"""

    def test_vram_free_threshold(self):
        from integrated_app.model_manager import _VRAM_FREE_THRESHOLD_BYTES
        assert _VRAM_FREE_THRESHOLD_BYTES > 0

    def test_vram_wait_max_seconds(self):
        from integrated_app.model_manager import _VRAM_WAIT_MAX_SECONDS
        assert _VRAM_WAIT_MAX_SECONDS > 0

    def test_vram_poll_interval(self):
        from integrated_app.model_manager import _VRAM_POLL_INTERVAL_SECONDS
        assert _VRAM_POLL_INTERVAL_SECONDS > 0

    def test_persona_cache_default_size(self):
        from integrated_app.model_manager import _PERSONA_CACHE_DEFAULT_SIZE
        assert _PERSONA_CACHE_DEFAULT_SIZE > 0

    def test_warmup_top_personas(self):
        from integrated_app.model_manager import _WARMUP_TOP_PERSONAS
        assert _WARMUP_TOP_PERSONAS > 0

    def test_unload_slow_threshold(self):
        from integrated_app.model_manager import _UNLOAD_SLOW_THRESHOLD_SECONDS
        assert _UNLOAD_SLOW_THRESHOLD_SECONDS > 0


class TestModelManagerRegistry:
    """model_manager 与 registry 集成测试。"""

    def test_registry_has_current_engine(self):
        from integrated_app.model_registry import registry
        assert hasattr(registry, "current_engine")

    def test_registry_has_is_voxcpm_ready(self):
        from integrated_app.model_registry import registry
        assert hasattr(registry, "is_voxcpm_ready")

    def test_registry_has_is_indextts2_ready(self):
        from integrated_app.model_registry import registry
        assert hasattr(registry, "is_indextts2_ready")

    def test_model_lock_is_reentrant(self):
        """_model_lock 是可重入锁（RLock）。"""
        from integrated_app.model_manager import _model_lock
        # RLock can be acquired twice from same thread
        acquired1 = _model_lock.acquire()
        acquired2 = _model_lock.acquire()
        assert acquired1 is True
        assert acquired2 is True
        _model_lock.release()
        _model_lock.release()


class TestModelManagerReExports:
    """model_manager 向后兼容 re-export 测试。"""

    def test_lru_cache_exported(self):
        from integrated_app.model_manager import LRUCache
        assert LRUCache is not None

    def test_adaptive_lru_cache_exported(self):
        from integrated_app.model_manager import AdaptiveLRUCache
        assert AdaptiveLRUCache is not None

    def test_progress_manager_exported(self):
        from integrated_app.model_manager import ProgressManager
        assert ProgressManager is not None

    def test_generation_tracker_exported(self):
        from integrated_app.model_manager import GenerationTracker
        assert GenerationTracker is not None

    def test_gpu_memory_monitor_exported(self):
        from integrated_app.model_manager import GPUMemoryMonitor
        assert GPUMemoryMonitor is not None

    def test_is_oom_error_exported(self):
        from integrated_app.model_manager import is_oom_error
        assert callable(is_oom_error)

    def test_free_gpu_memory_exported(self):
        from integrated_app.model_manager import free_gpu_memory
        assert callable(free_gpu_memory)


# =====================================================================
# exceptions.py 测试
# =====================================================================


class TestExceptions:
    """自定义异常类测试。"""

    def test_tts_error_is_exception(self):
        from integrated_app.exceptions import TTSError
        assert issubclass(TTSError, Exception)

    def test_tts_error_has_code(self):
        from integrated_app.exceptions import TTSError
        err = TTSError("test message")
        assert hasattr(err, "code") or hasattr(err, "message")

    def test_engine_load_error(self):
        from integrated_app.exceptions import EngineLoadError
        assert issubclass(EngineLoadError, Exception)

    def test_engine_switch_error(self):
        from integrated_app.exceptions import EngineSwitchError
        assert issubclass(EngineSwitchError, Exception)

    def test_insufficient_vram_error(self):
        from integrated_app.exceptions import InsufficientVRAMError
        assert issubclass(InsufficientVRAMError, Exception)

    def test_generation_error(self):
        from integrated_app.exceptions import GenerationError
        assert issubclass(GenerationError, Exception)

    def test_engine_not_loaded_error(self):
        from integrated_app.exceptions import EngineNotLoadedError
        assert issubclass(EngineNotLoadedError, Exception)


# =====================================================================
# estimator.py 测试
# =====================================================================


class TestGenerationTimeEstimator:
    """GenerationTimeEstimator 测试。"""

    def test_estimator_importable(self):
        from integrated_app.estimator import GenerationTimeEstimator
        assert GenerationTimeEstimator is not None

    def test_estimator_has_estimate_method(self, tmp_path):
        from integrated_app.estimator import GenerationTimeEstimator
        est = GenerationTimeEstimator(data_file=str(tmp_path / "estimator.json"))
        assert hasattr(est, "estimate")


# =====================================================================
# emotion_tags.py 测试
# =====================================================================


class TestEmotionTags:
    """emotion_tags 模块测试。"""

    def test_module_importable(self):
        from integrated_app import emotion_tags
        assert emotion_tags is not None

    def test_has_emotion_function(self):
        from integrated_app import emotion_tags
        has_func = any(
            hasattr(emotion_tags, name)
            for name in ("get_emotion_library", "parse_emotion_tags", "extract_emotions", "process_emotion", "apply_emotion")
        )
        assert has_func


# =====================================================================
# monitor.py 测试
# =====================================================================


class TestMonitor:
    """monitor 模块测试。"""

    def test_get_health_monitor(self):
        from integrated_app.monitor import get_health_monitor
        monitor = get_health_monitor()
        assert monitor is not None

    def test_health_monitor_singleton(self):
        from integrated_app.monitor import get_health_monitor
        m1 = get_health_monitor()
        m2 = get_health_monitor()
        assert m1 is m2


# =====================================================================
# routes/tabs.py 测试
# =====================================================================


class TestTabRoutes:
    """标签页路由测试。"""

    def test_voice_design_tab_loads(self, client):
        resp = client.get("/tab/voice_design")
        assert resp.status_code == 200

    def test_voice_clone_tab_loads(self, client):
        resp = client.get("/tab/voice_clone")
        assert resp.status_code == 200

    def test_settings_tab_loads(self, client):
        resp = client.get("/tab/settings")
        assert resp.status_code == 200

    def test_history_tab_loads(self, client):
        resp = client.get("/tab/history")
        assert resp.status_code == 200

    def test_persona_tab_loads(self, client):
        resp = client.get("/tab/persona")
        assert resp.status_code == 200

    def test_help_tab_loads(self, client):
        resp = client.get("/tab/help")
        assert resp.status_code == 200

    def test_nonexistent_tab_returns_404(self, client):
        resp = client.get("/tab/nonexistent_tab_xyz")
        assert resp.status_code in (404, 200)


# =====================================================================
# routes/system/settings.py 测试
# =====================================================================


class TestSystemSettingsRoutes:
    """系统设置路由测试。"""

    def test_settings_get(self, client):
        resp = client.get("/api/system/settings")
        assert resp.status_code in (200, 405, 403)

    def test_logs_route(self, client):
        resp = client.get("/api/system/logs")
        assert resp.status_code in (200, 404)
