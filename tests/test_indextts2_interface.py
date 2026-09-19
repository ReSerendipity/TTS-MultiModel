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


class TestDurationControlGuard:
    """时长控制参数守卫：底层库只认 duration_factor，绝对秒数必须显式拒绝。

    ``indextts.infer_v2_5.infer()`` 的形参表里没有 ``target_duration``，透传会落进
    ``**generation_kwargs`` 并被 ``model.generate`` 以 "model_kwargs not used" 拒收
    （400），与 GOTCHAS #90 记录的 seed 同型；2.0 走 ``infer_v2``，连 duration_factor
    都没有，旧行为是 debug 一句然后静默忽略——用户拿到与请求无关的音频却以为生效。
    """

    @staticmethod
    def _bare_engine(version: str, supports_duration: bool):
        from unittest.mock import MagicMock

        eng = object.__new__(IndexTTS2Engine)
        eng.tts = MagicMock()
        eng.tts.infer.return_value = (22050, MagicMock())
        eng.lang = "Auto"
        eng.version_str = version  # version 是只读 property，由 version_str 推导
        eng.supports_duration = supports_duration
        eng._engine_name = "indextts2" if version == "2.5" else "indextts20"
        eng.supported_langs = {"Auto", "ZH", "EN", "JA", "ES", "AR"}
        eng.device = "cpu"
        eng.use_bf16 = False
        return eng

    @staticmethod
    def _ref_audio(tmp_path):
        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"RIFF" + b"\x00" * 200)
        return str(ref)

    def test_target_duration_rejected_on_2_5(self, tmp_path):
        from integrated_app.exceptions import ValidationError

        eng = self._bare_engine("2.5", supports_duration=True)
        with pytest.raises(ValidationError, match="不支持按绝对秒数") as exc:
            eng.infer(
                text="你好",
                spk_audio_prompt=self._ref_audio(tmp_path),
                output_path=str(tmp_path / "out.wav"),
                target_duration=8.0,
            )
        assert exc.value.field == "target_duration"
        assert exc.value.status_code == 400
        # self.version 已经带 "IndexTTS " 前缀，文案里再拼一次会变成
        # "IndexTTS IndexTTS 2.5 …"（真机点击时看到过）
        assert str(exc.value).startswith("IndexTTS 2.5 ")
        eng.tts.infer.assert_not_called()

    def test_duration_factor_rejected_on_2_0(self, tmp_path):
        from integrated_app.exceptions import ValidationError

        eng = self._bare_engine("2.0", supports_duration=False)
        with pytest.raises(ValidationError, match="不支持显式时长控制") as exc:
            eng.infer(
                text="你好",
                spk_audio_prompt=self._ref_audio(tmp_path),
                output_path=str(tmp_path / "out.wav"),
                duration_factor=1.25,
            )
        assert exc.value.field == "duration_scale"
        assert str(exc.value).startswith("IndexTTS 2.0 ")
        eng.tts.infer.assert_not_called()
