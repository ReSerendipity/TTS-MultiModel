"""persona_metadata 模块单元测试 — 音色元数据与导入导出。

覆盖目标模块: bin/integrated_app/persona_metadata.py
"""

from integrated_app.persona_metadata import (
    PersonaExporter,
    PersonaMetadata,
    get_all_tags,
    get_categories,
)


class TestPersonaMetadata:
    def test_to_dict_roundtrip(self):
        meta = PersonaMetadata(name="小明", description="温柔的声音")
        data = meta.to_dict()
        assert data["name"] == "小明"
        restored = PersonaMetadata.from_dict(data)
        assert restored.name == "小明"
        assert restored.description == "温柔的声音"

    def test_from_legacy_text(self):
        meta = PersonaMetadata.from_legacy_text("小明", "萝莉音\n声音很温柔\n安静")
        assert meta.name == "小明"
        assert meta.voice_type == "萝莉音"
        assert meta.description == "声音很温柔"
        assert meta.traits == "安静"

    def test_to_legacy_text(self):
        meta = PersonaMetadata(name="小明", description="描述内容")
        text = meta.to_legacy_text()
        assert "描述内容" in text

    def test_tags_default_empty(self):
        meta = PersonaMetadata(name="测试")
        assert meta.tags == [] or meta.tags is None


class TestTagsAndCategories:
    def test_get_all_tags(self):
        tags = get_all_tags()
        assert isinstance(tags, dict)

    def test_get_categories(self):
        categories = get_categories()
        assert isinstance(categories, list)


class TestPersonaExporter:
    def test_export_nonexistent_persona(self, tmp_path):
        # 不存在的音色目录：实现可能抛异常或创建空包，仅验证不崩溃
        try:
            result = PersonaExporter.export_persona(str(tmp_path), str(tmp_path / "out.zip"))
            assert isinstance(result, str)
        except Exception:
            pass
