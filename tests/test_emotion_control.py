"""emotion_control 模块单元测试 — 情感向量、韵律标签、CFG 控制与指令解析。

覆盖目标模块: bin/integrated_app/emotion_control.py
"""

import pytest

from integrated_app.emotion_control import (
    EMOTION_DIMENSION_NAMES,
    CFGController,
    EmotionVector,
    InstructParser,
    ProsodyTagParser,
)


class TestEmotionVector:
    def test_default_neutral(self):
        vec = EmotionVector()
        assert vec.is_neutral()

    def test_clamping(self):
        vec = EmotionVector(happy=2.0, angry=-0.5)
        assert vec.happy == 1.0
        assert vec.angry == 0.0

    def test_to_list_and_dict(self):
        vec = EmotionVector(happy=0.5, sad=0.2)
        lst = vec.to_list()
        assert len(lst) == 8
        assert lst[0] == pytest.approx(0.5)
        d = vec.to_dict()
        assert d["happy"] == pytest.approx(0.5)
        assert d["sad"] == pytest.approx(0.2)

    def test_to_tensor(self):

        vec = EmotionVector(happy=0.5)
        tensor = vec.to_tensor()
        assert tensor.shape == (8,)
        assert tensor[0].item() == pytest.approx(0.5)

    def test_from_dict_ignores_unknown_keys(self):
        vec = EmotionVector.from_dict({"happy": 0.3, "unknown": 9.0})
        assert vec.happy == pytest.approx(0.3)
        with pytest.raises(AttributeError):
            _ = vec.unknown

    def test_from_list_wrong_length(self):
        with pytest.raises(ValueError):
            EmotionVector.from_list([0.0, 0.0])

    def test_preset(self):
        vec = EmotionVector.preset("HAPPY")
        assert vec.happy == pytest.approx(0.9)

    def test_preset_unknown(self):
        with pytest.raises(ValueError):
            EmotionVector.preset("nonexistent")

    def test_dominant_emotion(self):
        vec = EmotionVector(happy=0.1, sad=0.9)
        assert vec.dominant_emotion() == "sad"
        assert EmotionVector().dominant_emotion() is None

    def test_blend(self):
        a = EmotionVector(happy=1.0)
        b = EmotionVector(sad=1.0)
        mixed = a.blend(b, alpha=0.5)
        assert mixed.happy == pytest.approx(0.5)
        assert mixed.sad == pytest.approx(0.5)

    def test_repr(self):
        assert "neutral" in repr(EmotionVector())
        assert "happy=0.90" in repr(EmotionVector(happy=0.9))

    def test_dimension_names_length(self):
        assert len(EMOTION_DIMENSION_NAMES) == 8


class TestProsodyTagParser:
    def setup_method(self):
        self.parser = ProsodyTagParser()

    def test_parse_chattts_tags(self):
        tags = self.parser.parse("你好[laugh]世界[uv_break]")
        types = [(t.tag_type, t.tag_value) for t in tags]
        assert ("chatTTS", "laugh") in types
        assert ("chatTTS", "uv_break") in types

    def test_parse_paralinguistic(self):
        tags = self.parser.parse("[sigh]你好")
        assert tags[0].tag_type == "paralinguistic"
        assert tags[0].tag_value == "sigh"

    def test_parse_empty(self):
        assert self.parser.parse("") == []
        assert self.parser.parse(None) == []

    def test_strip_tags(self):
        cleaned = self.parser.strip_tags("你好[laugh]世界 [sigh]")
        assert "[laugh]" not in cleaned
        assert "[sigh]" not in cleaned

    def test_replace_tags(self):
        replaced = self.parser.replace_tags("你好[laugh]世界", replacement="!")
        assert "[laugh]" not in replaced

    def test_extract_paralinguistic_text(self):
        text = self.parser.extract_paralinguistic_text("[sigh]你好[gasp]")
        assert "sigh" in text
        assert "gasp" in text


class TestCFGController:
    def setup_method(self):
        self.cfg = CFGController()

    def test_validate_cfg_range(self):
        assert self.cfg.validate_cfg_range(5.0) == pytest.approx(5.0)
        assert self.cfg.validate_cfg_range(0.0) == pytest.approx(0.5)
        assert self.cfg.validate_cfg_range(20.0) == pytest.approx(10.0)

    def test_suggest_cfg_for_style(self):
        assert self.cfg.suggest_cfg_for_style("natural") == pytest.approx(1.0)
        with pytest.raises(ValueError):
            self.cfg.suggest_cfg_for_style("unknown")

    def test_get_cfg_range_for_style(self):
        lo, hi = self.cfg.get_cfg_range_for_style("dramatic")
        assert lo == pytest.approx(3.0)
        assert hi == pytest.approx(5.0)
        with pytest.raises(ValueError):
            self.cfg.get_cfg_range_for_style("nope")

    def test_list_styles(self):
        assert "natural" in self.cfg.list_styles()

    def test_validate_for_style(self):
        assert self.cfg.validate_for_style(1.2, "natural") == pytest.approx(1.2)
        clamped = self.cfg.validate_for_style(9.0, "natural")
        assert clamped == pytest.approx(1.5)
        # 未知风格时仅做全局钳制
        assert self.cfg.validate_for_style(9.0, "unknown") == pytest.approx(9.0)


class TestInstructParser:
    def setup_method(self):
        self.parser = InstructParser()

    def test_parse_empty(self):
        result = self.parser.parse("")
        assert result.emotion is None
        assert result.raw_instruction == ""

    def test_parse_zh_emotion(self):
        result = self.parser.parse("用悲伤的语气说")
        assert result.emotion_name == "sad"
        assert result.emotion is not None

    def test_parse_en_emotion_and_speed(self):
        result = self.parser.parse("speak slowly in a calm voice")
        assert result.emotion_name == "calm"
        assert result.speed == "slow"

    def test_parse_dialect(self):
        result = self.parser.parse("用四川话说，开心点")
        assert result.dialect == "sichuan"
        assert result.language == "zh"

    def test_parse_fast_speed(self):
        result = self.parser.parse("快速地朗读")
        assert result.speed == "fast"

    def test_to_engine_params_indextts2(self):
        params = self.parser.to_engine_params("用开心的语气说", engine="indextts2")
        assert "emo_vector" in params or "emo_text" in params

    def test_to_engine_params_voxcpm2(self):
        params = self.parser.to_engine_params("用悲伤的语气说", engine="voxcpm2")
        assert params["cfg_value"] == pytest.approx(2.5)
        assert "instruction" in params

    def test_to_engine_params_voxcpm2_neutral(self):
        params = self.parser.to_engine_params("没有情感的普通文本", engine="voxcpm2")
        assert params["cfg_value"] == pytest.approx(1.0)
