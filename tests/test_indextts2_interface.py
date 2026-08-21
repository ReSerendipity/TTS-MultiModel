"""IndexTTS2 引擎接口级单元测试（不加载模型）。

覆盖目标模块: app/integrated_app/engines/indextts2_engine.py
"""

import inspect

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


class TestVersion:
    """IndexTTS 2.5 升级相关断言。

    注意：``version`` 是 @property，通过实例访问会触发模型加载（__init__ 中
    即加载模型），因此这里只做类层面（描述符）断言，不实例化。
    """

    def test_version_is_property(self):
        # 类层面访问 @property 得到描述符对象，验证 version 是 property
        assert isinstance(IndexTTS2Engine.__dict__["version"], property)

    def test_version_is_2_5(self):
        # 通过 property 的 fget 获取返回值，避免实例化触发模型加载
        fget = IndexTTS2Engine.__dict__["version"].fget
        assert fget is not None
        assert fget(IndexTTS2Engine) == "IndexTTS 2.5"


class TestIndexTTS2LangCapability:
    """IndexTTS 2.5 新增 lang 语言能力。

    通过 inspect.signature 检查方法签名（不实例化、不触发模型加载）。
    """

    def test_infer_has_lang_param(self):
        params = inspect.signature(IndexTTS2Engine.infer).parameters
        assert "lang" in params

    def test_synthesize_has_lang_param(self):
        params = inspect.signature(IndexTTS2Engine.synthesize).parameters
        assert "lang" in params


class TestIndexTTS2InitSignature:
    """IndexTTS 2.5 __init__ 参数检查（不实例化）。"""

    def test_init_uses_bf16_not_fp16(self):
        params = inspect.signature(IndexTTS2Engine.__init__).parameters
        assert "use_bf16" in params
        assert "use_fp16" not in params

    def test_init_has_lang_param(self):
        assert "lang" in inspect.signature(IndexTTS2Engine.__init__).parameters

    def test_init_has_use_qwen_emo_param(self):
        assert "use_qwen_emo" in inspect.signature(IndexTTS2Engine.__init__).parameters
