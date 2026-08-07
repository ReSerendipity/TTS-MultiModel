"""IndexTTS2 引擎接口级单元测试（不加载模型）。

覆盖目标模块: bin/integrated_app/engines/indextts2_engine.py
"""

import pytest

from integrated_app.engines.indextts2_engine import IndexTTS2Engine


class TestEmotionConstants:
    def test_emotion_dimensions(self):
        dims = IndexTTS2Engine.EMOTION_DIMENSIONS
        assert isinstance(dims, list)
        assert "happy" in dims
        assert "sad" in dims
        # 与 emotion_control 的 8 维对齐
        from integrated_app.emotion_control import EMOTION_DIMENSION_NAMES

        assert set(dims) == set(EMOTION_DIMENSION_NAMES)


class TestBuildEmotionVector:
    def test_preset_vector(self):
        vec = IndexTTS2Engine.build_emotion_vector(happy=0.8)
        assert len(vec) == 8
        assert all(isinstance(v, (int, float)) for v in vec)
        assert vec[0] == pytest.approx(0.8)  # happy 是第一个维度

    def test_values_clamped(self):
        vec = IndexTTS2Engine.build_emotion_vector(happy=2.0)
        assert all(0.0 <= v <= 1.0 for v in vec)


class TestGetPresetEmotions:
    def test_returns_dict(self):
        presets = IndexTTS2Engine.get_preset_emotions()
        assert isinstance(presets, dict)
        assert "happy" in presets
        assert "neutral" in presets
        assert len(presets["happy"]) == 8


class TestIndexTTS2EngineInterface:
    def test_class_implements_protocol(self):
        from integrated_app.engine_interface import TTSEngine

        assert issubclass(IndexTTS2Engine, TTSEngine)

    def test_static_properties(self):
        # 类层面访问 @property 得到描述符对象，验证属性存在且类型正确
        assert isinstance(IndexTTS2Engine.__dict__["version"], property)
        assert isinstance(IndexTTS2Engine.__dict__["min_vram_gb"], property)
        assert isinstance(IndexTTS2Engine.__dict__["min_ram_gb"], property)

    def test_init_without_model_dir(self):
        # 初始化时即校验模型文件，缺失则抛 EngineLoadError（引导用户下载模型）
        from integrated_app.exceptions import EngineLoadError

        with pytest.raises(EngineLoadError):
            IndexTTS2Engine(model_dir="/nonexistent/path")
