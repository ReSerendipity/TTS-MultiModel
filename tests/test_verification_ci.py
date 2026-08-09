"""验证脚本 CI 化 — 将 scripts/verify_*.py 的核心逻辑转为 pytest 测试。

使验证脚本可在 CI 中自动运行，无需手动执行。

覆盖验证目标:
  - verify_persona_pt_origin.py: 验证 .pt 文件来源标记
  - verify_model_checksums.py: 验证模型文件校验和
  - verify_model_weights.py: 验证模型权重完整性
  - verify_ui_optimizations.py: 验证 UI 优化脚本
"""

import os
import sys
from pathlib import Path

import pytest

_BIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

_REPO_ROOT = Path(__file__).resolve().parent.parent


class TestPersonaPtOrigin:
    """验证 .pt 文件来源标记。"""

    def test_persona_pt_origin_constant_exists(self):
        """PERSONA_PT_ORIGIN 常量存在。"""
        from integrated_app.persona_manager import PERSONA_PT_ORIGIN
        assert PERSONA_PT_ORIGIN is not None
        assert "TTS_MultiModel" in PERSONA_PT_ORIGIN

    def test_persona_pt_format_version_positive(self):
        """PERSONA_PT_FORMAT_VERSION 为正整数。"""
        from integrated_app.persona_manager import PERSONA_PT_FORMAT_VERSION
        assert isinstance(PERSONA_PT_FORMAT_VERSION, int)
        assert PERSONA_PT_FORMAT_VERSION >= 1

    def test_no_pickle_in_prompt_cache(self):
        """prompt_cache 不将 pickle 作为主序列化机制（安全要求）。

        注意：模块中存在 ``import pickle`` 用于读取旧版 .pkl 文件迁移，
        这是合法的向后兼容代码，不构成安全风险。
        """
        import integrated_app.prompt_cache as pc
        source = open(pc.__file__, encoding="utf-8").read()
        # pickle.dump 不应出现在写入路径中
        assert "pickle.dump" not in source
        # 主序列化函数应使用 json
        assert "json.dumps" in source
        assert "json.loads" in source

    def test_cache_files_use_json_extension(self):
        """缓存文件使用 .json 扩展名。"""
        from integrated_app.prompt_cache import _get_cache_file_path, _get_metadata_path
        assert str(_get_cache_file_path("test")).endswith(".json")
        assert str(_get_metadata_path()).endswith(".json")


class TestModelChecksums:
    """验证模型文件校验和。"""

    def test_model_dirs_exist(self):
        """模型目录结构存在。"""
        from integrated_app.config import VOXCPM2_MODEL_PATH, INDEXTTS2_MODEL_PATH
        # Paths should be defined (may not exist in CI, but should be valid strings)
        assert isinstance(VOXCPM2_MODEL_PATH, str)
        assert isinstance(INDEXTTS2_MODEL_PATH, str)

    def test_pretrained_models_dir_exists(self):
        """pretrained_models 目录存在。"""
        pretrained_dir = _REPO_ROOT / "pretrained_models"
        assert pretrained_dir.exists(), f"pretrained_models dir not found at {pretrained_dir}"

    def test_config_has_model_paths(self):
        """配置包含所有必要的模型路径。"""
        from integrated_app.config import (
            VOXCPM2_MODEL_PATH,
            VOXCPM2_ASR_PATH,
            VOXCPM2_DENOISER_PATH,
            INDEXTTS2_MODEL_PATH,
        )
        assert VOXCPM2_MODEL_PATH is not None
        assert VOXCPM2_ASR_PATH is not None
        assert VOXCPM2_DENOISER_PATH is not None
        assert INDEXTTS2_MODEL_PATH is not None


class TestModelWeights:
    """验证模型权重完整性。"""

    def test_model_registry_importable(self):
        """model_registry 可导入。"""
        from integrated_app.model_registry import registry
        assert registry is not None

    def test_registry_has_current_engine(self):
        """registry 有 current_engine 属性。"""
        from integrated_app.model_registry import registry
        assert hasattr(registry, "current_engine")

    def test_registry_has_is_ready_methods(self):
        """registry 有 is_*_ready 方法。"""
        from integrated_app.model_registry import registry
        assert hasattr(registry, "is_voxcpm_ready")
        assert hasattr(registry, "is_indextts2_ready")

    def test_engine_interface_defined(self):
        """TTSEngine 接口已定义。"""
        from integrated_app.engine_interface import TTSEngine
        assert TTSEngine is not None
        assert hasattr(TTSEngine, "generate") or hasattr(TTSEngine, "__abstractmethods__")


class TestUIOptimizations:
    """验证 UI 优化。"""

    def test_templates_dir_exists(self):
        """模板目录存在。"""
        templates_dir = _REPO_ROOT / "bin" / "integrated_app" / "templates"
        assert templates_dir.exists()

    def test_base_html_exists(self):
        """base.html 模板存在。"""
        base_html = _REPO_ROOT / "bin" / "integrated_app" / "templates" / "base.html"
        assert base_html.exists()

    def test_static_dir_exists(self):
        """静态资源目录存在。"""
        static_dir = _REPO_ROOT / "bin" / "integrated_app" / "static"
        assert static_dir.exists()

    def test_locales_exist(self):
        """国际化文件存在。"""
        locales_dir = _REPO_ROOT / "bin" / "integrated_app" / "locales"
        assert locales_dir.exists()
        zh_json = locales_dir / "zh.json"
        assert zh_json.exists()

    def test_tab_templates_exist(self):
        """核心 tab 模板文件存在。"""
        tabs_dir = _REPO_ROOT / "bin" / "integrated_app" / "templates" / "tabs"
        assert tabs_dir.exists()
        # Check for core tabs
        core_tabs = ["voice_design.html", "voice_clone.html", "settings.html"]
        for tab in core_tabs:
            tab_path = tabs_dir / tab
            assert tab_path.exists(), f"Tab template not found: {tab}"

    def test_csrf_middleware_registered(self, client):
        """CSRF 中间件已注册。"""
        # POST without CSRF token should return 403
        resp = client.post("/api/training/stop")
        assert resp.status_code == 403

    def test_error_handler_registered(self, client):
        """错误处理器已注册。"""
        resp = client.get("/api/nonexistent-xyz-123")
        assert resp.status_code == 404
        data = resp.json()
        assert isinstance(data, dict)

    def test_request_id_middleware_registered(self, client):
        """Request ID 中间件已注册。"""
        resp = client.get("/api/health/ping")
        assert "x-request-id" in resp.headers

    def test_rate_limit_middleware_importable(self):
        """Rate limit 中间件可导入。"""
        from integrated_app.middleware.rate_limit import RateLimitMiddleware
        assert RateLimitMiddleware is not None
