"""配置加载和校验测试"""

import os
import sys

import pytest
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
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
        assert os.path.exists(config_path), f"config.yaml not found at {config_path}"

    def test_config_yaml_parseable(self):
        """测试 config.yaml 可解析"""
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")
        with open(config_path, encoding="utf-8") as f:
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


class TestToLangCode:
    """语言标识归一（修复 UI 中文显示名与 ISO 代码词表不通的缺陷）。"""

    def test_ui_display_names_map_to_iso_codes(self):
        from integrated_app.config import to_lang_code

        assert to_lang_code("中文") == "zh"
        assert to_lang_code("英语") == "en"
        assert to_lang_code("日语") == "ja"
        assert to_lang_code("韩语") == "ko"
        assert to_lang_code("自动检测") == "auto"

    def test_case_and_region_variants(self):
        from integrated_app.config import to_lang_code

        assert to_lang_code("ZH") == "zh"
        assert to_lang_code("zh-CN") == "zh"
        assert to_lang_code("zh-Hans") == "zh"
        assert to_lang_code("pt-BR") == "pt"
        assert to_lang_code(" en ") == "en"

    def test_empty_and_unknown_fall_back_to_auto(self):
        from integrated_app.config import to_lang_code

        assert to_lang_code("") == "auto"
        assert to_lang_code(None) == "auto"
        assert to_lang_code("火星语") == "auto"

    def test_every_ui_lang_option_is_mappable(self):
        """_LANGS 新增语种时必须同步 _LANG_ALIASES，否则该语种在前端被静默降级为 auto。"""
        from integrated_app.config import _LANGS, to_lang_code

        unmapped = [name for name in _LANGS if to_lang_code(name) == "auto" and name != "自动检测"]
        assert unmapped == [], f"_LANGS 中这些显示名没有语言码映射：{unmapped}"
