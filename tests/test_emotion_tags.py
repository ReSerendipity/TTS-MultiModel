"""emotion_tags 模块单元测试 — 情感标签解析与转换。

覆盖目标模块: bin/integrated_app/emotion_tags.py
"""

import pytest

from integrated_app.emotion_tags import (
    EMOTION_REGISTRY,
    EmotionTag,
    get_emotion_library,
    parse_tags,
    strip_all_tags,
    tags_to_control_instruction,
    validate_tags,
)


class TestEmotionTag:
    def test_post_init_normalizes_name_and_intensity(self):
        tag = EmotionTag(name="HAPPY", intensity=2.5, raw_text="[HAPPY:2.5]")
        assert tag.name == "happy"
        assert tag.intensity == 1.0

        low = EmotionTag(name="sad", intensity=-1.0)
        assert low.intensity == 0.0


class TestParseTags:
    def test_parse_english_tag(self):
        tags, cleaned = parse_tags("[whisper]Hello world")
        assert [t.name for t in tags] == ["whisper"]
        assert cleaned == "Hello world"

    def test_parse_tag_with_intensity(self):
        tags, cleaned = parse_tags("Hello [excited:0.8]world")
        assert tags[0].name == "excited"
        assert tags[0].intensity == pytest.approx(0.8)
        assert tags[0].raw_text == "[excited:0.8]"
        assert cleaned == "Hello world"

    def test_parse_chinese_tag(self):
        tags, cleaned = parse_tags("你好[温柔]世界")
        assert tags[0].name == "gentle"
        assert cleaned == "你好世界"

    def test_parse_unknown_tag_warns_but_keeps(self):
        tags, cleaned = parse_tags("[weirdtag]text")
        assert tags[0].name == "weirdtag"
        assert cleaned == "text"

    def test_parse_empty_text(self):
        tags, cleaned = parse_tags("")
        assert tags == []
        assert cleaned == ""

    def test_intensity_clamped(self):
        tags, _ = parse_tags("[sad:3.0]")
        assert tags[0].intensity == 1.0
        # 正则不匹配负强度；直接构造 EmotionTag 验证钳制
        tag = EmotionTag(name="sad", intensity=-0.5)
        assert tag.intensity == 0.0


class TestTagsToControlInstruction:
    def test_single_emotion_tag(self):
        vec, cfg = tags_to_control_instruction([EmotionTag(name="happy")])
        assert vec["happy"] == pytest.approx(1.0)
        assert "cheerful" in cfg

    def test_intensity_affects_weight_and_cfg(self):
        vec, cfg = tags_to_control_instruction([EmotionTag(name="happy", intensity=0.5)])
        assert vec["happy"] == pytest.approx(0.5)
        assert "intensity: 0.5" in cfg

    def test_multiple_tags_averaged(self):
        tags = [EmotionTag(name="happy"), EmotionTag(name="calm")]
        vec, cfg = tags_to_control_instruction(tags)
        assert vec["happy"] == pytest.approx(0.5)
        # happy 预设 calm=0.3，calm 预设 calm=1.0，平均为 0.65
        assert vec["calm"] == pytest.approx(0.65)
        assert cfg is not None

    def test_unknown_tag_skipped(self):
        vec, cfg = tags_to_control_instruction([EmotionTag(name="notexists")])
        assert vec is None
        assert cfg is None

    def test_prosody_tag_cfg_only(self):
        vec, cfg = tags_to_control_instruction([EmotionTag(name="whisper")])
        assert vec is None
        assert "whispering" in cfg


class TestStripAndValidate:
    def test_strip_all_tags(self):
        assert strip_all_tags("[whisper]你好 [sad]世界") == "你好 世界"

    def test_get_emotion_library(self):
        lib = get_emotion_library()
        names = {d["name"] for d in lib}
        assert "happy" in names
        assert "whisper" in names
        assert all("display_name_zh" in d for d in lib)

    def test_validate_tags_known(self):
        assert validate_tags([EmotionTag(name="happy")]) == []

    def test_validate_tags_unknown(self):
        warnings = validate_tags([EmotionTag(name="zzz")])
        assert len(warnings) == 1
        assert "zzz" in warnings[0]

    def test_emotion_registry_populated(self):
        assert len(EMOTION_REGISTRY) >= 15
        assert "sad" in EMOTION_REGISTRY
        assert "shout" in EMOTION_REGISTRY
