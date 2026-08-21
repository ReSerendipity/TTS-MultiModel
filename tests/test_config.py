# -*- coding: utf-8 -*-
"""配置加载和校验测试"""
import os
import sys
import pytest
import tempfile
import yaml

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")


class TestConfigLoading:
    """测试配置加载"""

    def test_load_default_config(self):
        """测试默认配置加载"""
        from integrated_app.config_models import AppConfig
        config = AppConfig()
        assert config is not None

    def test_sse_config_defaults(self):
        """测试 SSE 配置默认值"""
        from integrated_app.config_models import SSEConfig
        config = SSEConfig()
        assert config.active_interval == 0.3
        assert config.idle_base_interval == 1.0
        assert config.idle_max_interval == 3.0
        assert config.idle_step == 0.5
        assert config.heartbeat_interval == 30.0

    def test_audio_player_config_defaults(self):
        """测试音频播放器配置默认值"""
        from integrated_app.config_models import AudioPlayerConfig
        config = AudioPlayerConfig()
        assert config.waveform_steps == 300
        assert config.default_sample_rate == 44100
        assert config.progress_update_ms == 100

    def test_ui_config_defaults(self):
        """测试 UI 配置默认值"""
        from integrated_app.config_models import UIConfig
        config = UIConfig()
        assert config.sidebar_width == 240
        assert config.sidebar_collapsed_width == 52

    def test_config_yaml_exists(self):
        """测试 config.yaml 文件存在"""
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config.yaml"
        )
        assert os.path.exists(config_path), f"config.yaml not found at {config_path}"

    def test_config_yaml_parseable(self):
        """测试 config.yaml 可解析"""
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config.yaml"
        )
        with open(config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict)


class TestConfigValidation:
    """测试配置校验"""

    def test_generation_config_invalid_chars_per_segment_raises(self):
        """测试生成配置无效字符数抛出异常"""
        from pydantic import ValidationError
        from integrated_app.config_models import GenerationConfig
        with pytest.raises(ValidationError):
            GenerationConfig(max_chars_per_segment=10)

    def test_generation_defaults_invalid_timesteps_raises(self):
        """测试生成默认配置无效步数抛出异常"""
        from pydantic import ValidationError
        from integrated_app.config_models import GenerationDefaultsConfig
        with pytest.raises(ValidationError):
            GenerationDefaultsConfig(inference_timesteps=0)

    def test_server_config_invalid_port_raises(self):
        """测试服务器配置无效端口抛出异常"""
        from pydantic import ValidationError
        from integrated_app.config_models import ServerConfig
        with pytest.raises(ValidationError):
            ServerConfig(port=0)

    def test_memory_config_invalid_cache_size_raises(self):
        """测试内存配置无效缓存大小抛出异常"""
        from pydantic import ValidationError
        from integrated_app.config_models import MemoryConfig
        with pytest.raises(ValidationError):
            MemoryConfig(max_cache_size=0)

    def test_server_config_workers_gt1_raises(self):
        """测试服务器配置 workers > 1 抛出异常"""
        from pydantic import ValidationError
        from integrated_app.config_models import AppConfig, ServerConfig
        with pytest.raises(ValidationError):
            AppConfig(server=ServerConfig(workers=2))
