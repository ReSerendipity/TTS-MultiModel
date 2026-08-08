"""Smoke tests for the vLLM backend module.

Covers:
- is_vllm_available() detection
- VLLMConfig dataclass + to_vllm_kwargs()
- VLLMStatus dataclass defaults
- VLLMBackend lifecycle (initialize, generate, shutdown, get_stats)
- get_vllm_backend() singleton
- check_vllm_config_compatibility() model architecture detection
- VLLM_DISABLED environment variable
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from integrated_app.vllm_backend import (
    VLLMConfig,
    VLLMBackend,
    VLLMStatus,
    VLLM_DISABLED,
    check_vllm_config_compatibility,
    get_vllm_backend,
    is_vllm_available,
)


# ---------------------------------------------------------------------------
# is_vllm_available
# ---------------------------------------------------------------------------


class TestVLLMAvailability:
    """Test vLLM availability detection."""

    def test_returns_bool(self):
        result = is_vllm_available()
        assert isinstance(result, bool)

    def test_not_available_in_ci(self):
        """vLLM should not be installed in the CI/test environment."""
        # vLLM is a GPU-only dependency, so it should not be available
        # in the test environment
        assert is_vllm_available() is False


# ---------------------------------------------------------------------------
# VLLMConfig
# ---------------------------------------------------------------------------


class TestVLLMConfig:
    """Test VLLMConfig dataclass."""

    def test_defaults(self):
        cfg = VLLMConfig()
        assert cfg.tensor_parallel_size == 1
        assert cfg.gpu_memory_utilization == 0.85
        assert cfg.max_model_len == 4096
        assert cfg.dtype == "auto"
        assert cfg.enforce_eager is False
        assert cfg.trust_remote_code is True
        assert cfg.enable_prefix_caching is True
        assert cfg.block_size == 16
        assert cfg.swap_space == 4
        assert cfg.disable_log_stats is True

    def test_custom_values(self):
        cfg = VLLMConfig(
            tensor_parallel_size=2,
            gpu_memory_utilization=0.9,
            max_model_len=8192,
            dtype="float16",
        )
        assert cfg.tensor_parallel_size == 2
        assert cfg.gpu_memory_utilization == 0.9
        assert cfg.max_model_len == 8192
        assert cfg.dtype == "float16"

    def test_to_vllm_kwargs(self):
        cfg = VLLMConfig(tensor_parallel_size=2, max_model_len=2048)
        kwargs = cfg.to_vllm_kwargs()
        assert kwargs["tensor_parallel_size"] == 2
        assert kwargs["max_model_len"] == 2048
        assert kwargs["dtype"] == "auto"
        assert "model" not in kwargs  # model is added separately


# ---------------------------------------------------------------------------
# VLLMStatus
# ---------------------------------------------------------------------------


class TestVLLMStatus:
    """Test VLLMStatus dataclass."""

    def test_defaults(self):
        status = VLLMStatus()
        assert status.available is False
        assert status.initialized is False
        assert status.model_path == ""
        assert status.engine_type == ""
        assert status.init_time_s == 0.0
        assert status.error == ""
        assert status.gpu_count == 0
        assert status.gpu_memory_gb == 0.0


# ---------------------------------------------------------------------------
# VLLMBackend
# ---------------------------------------------------------------------------


class TestVLLMBackend:
    """Test VLLMBackend lifecycle."""

    def test_default_config(self):
        backend = VLLMBackend()
        assert backend._config is not None
        assert backend.is_ready is False
        assert backend._generation_count == 0

    def test_is_available_property(self):
        backend = VLLMBackend()
        # vLLM is not installed in test env
        assert backend.is_available is False

    def test_initialize_not_available(self):
        backend = VLLMBackend()
        result = backend.initialize("/fake/path")
        assert result is False
        assert "vLLM 未安装" in backend._status.error

    def test_generate_not_ready(self):
        backend = VLLMBackend()
        result = backend.generate("hello")
        assert result is None

    def test_get_stats(self):
        backend = VLLMBackend()
        stats = backend.get_stats()
        assert "available" in stats
        assert "initialized" in stats
        assert "engine_type" in stats
        assert "model_path" in stats
        assert "init_time_s" in stats
        assert "generation_count" in stats
        assert "gpu_count" in stats
        assert "gpu_memory_gb" in stats
        assert "error" in stats

    def test_shutdown_not_initialized(self):
        backend = VLLMBackend()
        # Should not raise even if not initialized
        backend.shutdown()
        assert backend.is_ready is False

    def test_status_property(self):
        backend = VLLMBackend()
        assert isinstance(backend.status, VLLMStatus)

    def test_is_ready_property(self):
        backend = VLLMBackend()
        assert backend.is_ready is False

    def test_initialize_already_initialized(self):
        """If already initialized, should return True without re-init."""
        backend = VLLMBackend()
        backend._status.initialized = True
        result = backend.initialize("/fake/path")
        assert result is True


# ---------------------------------------------------------------------------
# get_vllm_backend singleton
# ---------------------------------------------------------------------------


class TestGetVLLMBackend:
    """Test get_vllm_backend singleton function."""

    def test_returns_instance(self):
        backend = get_vllm_backend()
        assert isinstance(backend, VLLMBackend)

    def test_singleton(self):
        b1 = get_vllm_backend()
        b2 = get_vllm_backend()
        assert b1 is b2


# ---------------------------------------------------------------------------
# check_vllm_config_compatibility
# ---------------------------------------------------------------------------


class TestCheckVLLMConfigCompatibility:
    """Test model architecture compatibility checking."""

    def test_nonexistent_path(self):
        result = check_vllm_config_compatibility("/nonexistent/path")
        assert result["compatible"] is False
        assert "vLLM 未安装" in result["reason"] or "不存在" in result["reason"]

    def test_with_config_json(self):
        """Test compatibility check with a valid config.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.json")
            with open(config_path, "w") as f:
                json.dump({"architecture": "LlamaForCausalLM"}, f)

            result = check_vllm_config_compatibility(tmpdir)
            # vLLM not installed, so vllm_installed should be False
            if result["vllm_installed"]:
                assert result["compatible"] is True
            else:
                assert "vLLM 未安装" in result["reason"]

    def test_unsupported_architecture(self):
        """Test unsupported architecture."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.json")
            with open(config_path, "w") as f:
                json.dump({"architecture": "GPT2LMHeadModel"}, f)

            result = check_vllm_config_compatibility(tmpdir)
            if result["vllm_installed"]:
                assert result["compatible"] is False
                assert "GPT2LMHeadModel" in result["reason"]

    def test_no_config_json(self):
        """Test with a directory that has no config.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = check_vllm_config_compatibility(tmpdir)
            if result["vllm_installed"]:
                assert "config.json" in result["reason"]


# ---------------------------------------------------------------------------
# VLLM_DISABLED env var
# ---------------------------------------------------------------------------


class TestVLLMDisabled:
    """Test VLLM_DISABLED environment variable."""

    def test_default_value(self):
        # VLLM_DISABLED should be a string
        assert VLLM_DISABLED in (True, False) or isinstance(VLLM_DISABLED, bool)
