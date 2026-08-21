"""提示词扩展模块单元测试。

覆盖目标模块: app/integrated_app/prompt_expander.py
测试内容:
    1. 模板加载与检索
    2. 模板渲染（变量替换）
    3. 智能扩展
    4. 自定义模板注册与移除
    5. 搜索功能
    6. 模块级便捷函数
    7. 边界条件与异常处理
"""

import os
import sys

import pytest

_APP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"
)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from integrated_app.prompt_expander import (
    PromptExpander,
    PromptTemplate,
    TemplateCategory,
    apply_template,
    expand_prompt,
    get_prompt_expander,
)


# ---------------------------------------------------------------------------
# 模板加载与检索测试
# ---------------------------------------------------------------------------


class TestTemplateLoading:
    """模板加载与检索测试。"""

    def setup_method(self):
        self.expander = PromptExpander()

    def test_builtin_templates_loaded(self):
        """预置模板应被加载。"""
        assert self.expander.get_template_count() > 0

    def test_get_template_by_id(self):
        """根据 ID 获取模板。"""
        template = self.expander.get_template("gentle_female")
        assert template is not None
        assert template.id == "gentle_female"
        assert template.category == TemplateCategory.VOICE_DESIGN

    def test_get_nonexistent_template(self):
        """不存在的模板 ID 应返回 None。"""
        assert self.expander.get_template("nonexistent") is None

    def test_get_templates_by_category(self):
        """按类别获取模板列表。"""
        voice_design = self.expander.get_templates(TemplateCategory.VOICE_DESIGN)
        assert len(voice_design) > 0
        for t in voice_design:
            assert t.category == TemplateCategory.VOICE_DESIGN

    def test_get_all_templates(self):
        """获取全部模板（category=None）。"""
        all_templates = self.expander.get_templates(None)
        assert len(all_templates) == self.expander.get_template_count()

    def test_list_categories(self):
        """列出所有类别。"""
        categories = self.expander.list_categories()
        assert TemplateCategory.VOICE_DESIGN in categories
        assert TemplateCategory.EMOTION_STYLE in categories
        assert TemplateCategory.SCENE in categories
        assert TemplateCategory.CHARACTER in categories

    def test_list_template_ids(self):
        """列出所有模板 ID。"""
        ids = self.expander.list_template_ids()
        assert len(ids) > 0
        assert "gentle_female" in ids

    def test_get_template_count_by_category(self):
        """按类别获取模板数量。"""
        count = self.expander.get_template_count(TemplateCategory.VOICE_DESIGN)
        assert count > 0


# ---------------------------------------------------------------------------
# 模板渲染测试
# ---------------------------------------------------------------------------


class TestTemplateRender:
    """模板渲染（变量替换）测试。"""

    def setup_method(self):
        self.expander = PromptExpander()

    def test_render_with_params(self):
        """带参数的模板渲染。"""
        result = self.expander.apply_template(
            "gentle_female",
            {"speed": "偏慢"},
            lang="zh",
        )
        assert "偏慢" in result
        assert "温柔" in result

    def test_render_without_params(self):
        """无参数的模板渲染（变量保留原样）。"""
        result = self.expander.apply_template("gentle_female", None, lang="zh")
        assert "{speed}" in result  # 变量未被替换

    def test_render_english(self):
        """英文模板渲染。"""
        result = self.expander.apply_template(
            "gentle_female",
            {"speed": "slow"},
            lang="en",
        )
        assert "slow" in result
        assert "Gentle" in result

    def test_render_no_variables(self):
        """无变量模板渲染。"""
        result = self.expander.apply_template("scene_news", None, lang="zh")
        assert "新闻" in result
        assert "{" not in result  # 无未替换变量

    def test_render_partial_params(self):
        """部分参数替换（未提供的变量保留）。"""
        template = self.expander.get_template("char_hero")
        result = template.render({"gender": "女"}, lang="zh")
        assert "女" in result
        assert "{age}" in result  # 未提供 age

    def test_apply_nonexistent_template_raises(self):
        """不存在的模板应抛出 KeyError。"""
        with pytest.raises(KeyError, match="模板不存在"):
            self.expander.apply_template("nonexistent")


# ---------------------------------------------------------------------------
# 智能扩展测试
# ---------------------------------------------------------------------------


class TestSmartExpand:
    """智能扩展功能测试。"""

    def setup_method(self):
        self.expander = PromptExpander()

    def test_expand_empty_text(self):
        """空文本扩展应返回空。"""
        assert self.expander.expand("") == ""
        assert self.expander.expand("   ") == ""

    def test_expand_no_match(self):
        """无匹配关键词时应返回原文。"""
        text = "这是一个没有匹配关键词的文本"
        result = self.expander.expand(text)
        assert result == text

    def test_expand_with_keyword(self):
        """包含关键词时应扩展。"""
        result = self.expander.expand("新闻播报", lang="zh")
        assert len(result) > len("新闻播报")

    def test_expand_multiple_keywords(self):
        """多个关键词应组合多个模板。"""
        result = self.expander.expand("温柔的新闻播报", lang="zh")
        assert len(result) > len("温柔的新闻播报")

    def test_expand_english(self):
        """英文关键词扩展。"""
        result = self.expander.expand("cheerful news", lang="en")
        assert len(result) > len("cheerful news")

    def test_expand_max_templates(self):
        """max_templates 限制组合数量。"""
        result_all = self.expander.expand("温柔 活泼 播音 新闻 有声书", max_templates=10)
        result_limited = self.expander.expand("温柔 活泼 播音 新闻 有声书", max_templates=2)
        assert len(result_limited) <= len(result_all)


# ---------------------------------------------------------------------------
# 自定义模板注册与移除测试
# ---------------------------------------------------------------------------


class TestCustomTemplate:
    """自定义模板注册与移除测试。"""

    def setup_method(self):
        self.expander = PromptExpander()

    def test_register_custom_template(self):
        """注册自定义模板。"""
        template = PromptTemplate(
            id="custom_test",
            category=TemplateCategory.CUSTOM,
            name_zh="测试模板",
            name_en="Test Template",
            template_zh="这是一个{type}测试模板",
            template_en="This is a {type} test template",
            variables=["type"],
        )
        assert self.expander.register_template(template) is True
        assert self.expander.get_template("custom_test") is not None

    def test_register_duplicate_id_fails(self):
        """重复 ID 注册应失败。"""
        template = PromptTemplate(
            id="gentle_female",  # 已存在的 ID
            category=TemplateCategory.CUSTOM,
            name_zh="重复",
            name_en="Duplicate",
            template_zh="重复",
            template_en="Duplicate",
        )
        assert self.expander.register_template(template) is False

    def test_remove_template(self):
        """移除模板。"""
        template = PromptTemplate(
            id="removable",
            category=TemplateCategory.CUSTOM,
            name_zh="可移除",
            name_en="Removable",
            template_zh="可移除",
            template_en="Removable",
        )
        self.expander.register_template(template)
        assert self.expander.remove_template("removable") is True
        assert self.expander.get_template("removable") is None

    def test_remove_nonexistent(self):
        """移除不存在的模板应返回 False。"""
        assert self.expander.remove_template("nonexistent") is False


# ---------------------------------------------------------------------------
# 搜索功能测试
# ---------------------------------------------------------------------------


class TestSearch:
    """模板搜索功能测试。"""

    def setup_method(self):
        self.expander = PromptExpander()

    def test_search_by_name_zh(self):
        """按中文名搜索。"""
        results = self.expander.search_templates("温柔")
        assert len(results) > 0
        assert any("温柔" in t.name_zh for t in results)

    def test_search_by_name_en(self):
        """按英文名搜索。"""
        results = self.expander.search_templates("Gentle")
        assert len(results) > 0

    def test_search_by_tag(self):
        """按标签搜索。"""
        results = self.expander.search_templates("女声")
        assert len(results) > 0

    def test_search_no_match(self):
        """无匹配时返回空列表。"""
        results = self.expander.search_templates("完全不存在的关键词xyz123")
        assert results == []

    def test_search_case_insensitive(self):
        """搜索应大小写不敏感。"""
        results_lower = self.expander.search_templates("gentle")
        results_upper = self.expander.search_templates("GENTLE")
        assert len(results_lower) == len(results_upper)


# ---------------------------------------------------------------------------
# 模块级便捷函数测试
# ---------------------------------------------------------------------------


class TestModuleFunctions:
    """模块级便捷函数测试。"""

    def test_get_prompt_expander_singleton(self):
        """get_prompt_expander 应返回同一实例。"""
        e1 = get_prompt_expander()
        e2 = get_prompt_expander()
        assert e1 is e2

    def test_expand_prompt_function(self):
        """expand_prompt 便捷函数。"""
        result = expand_prompt("新闻播报")
        assert isinstance(result, str)

    def test_apply_template_function(self):
        """apply_template 便捷函数。"""
        result = apply_template("scene_news", lang="zh")
        assert isinstance(result, str)
        assert "新闻" in result


# ---------------------------------------------------------------------------
# PromptTemplate 数据类测试
# ---------------------------------------------------------------------------


class TestPromptTemplate:
    """PromptTemplate 数据类测试。"""

    def test_creation(self):
        """PromptTemplate 应正确创建。"""
        template = PromptTemplate(
            id="test",
            category=TemplateCategory.CUSTOM,
            name_zh="测试",
            name_en="Test",
            template_zh="你好{name}",
            template_en="Hello {name}",
            variables=["name"],
        )
        assert template.id == "test"
        assert template.category == TemplateCategory.CUSTOM

    def test_render_zh(self):
        """中文渲染。"""
        template = PromptTemplate(
            id="test",
            category=TemplateCategory.CUSTOM,
            name_zh="测试",
            name_en="Test",
            template_zh="你好{name}",
            template_en="Hello {name}",
        )
        result = template.render({"name": "世界"}, lang="zh")
        assert result == "你好世界"

    def test_render_en(self):
        """英文渲染。"""
        template = PromptTemplate(
            id="test",
            category=TemplateCategory.CUSTOM,
            name_zh="测试",
            name_en="Test",
            template_zh="你好{name}",
            template_en="Hello {name}",
        )
        result = template.render({"name": "World"}, lang="en")
        assert result == "Hello World"

    def test_render_no_params(self):
        """无参数渲染（变量保留）。"""
        template = PromptTemplate(
            id="test",
            category=TemplateCategory.CUSTOM,
            name_zh="测试",
            name_en="Test",
            template_zh="你好{name}",
            template_en="Hello {name}",
        )
        result = template.render(None, lang="zh")
        assert "{name}" in result


# ---------------------------------------------------------------------------
# 边界条件测试
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """边界条件测试。"""

    def setup_method(self):
        self.expander = PromptExpander()

    def test_template_category_enum_values(self):
        """TemplateCategory 枚举值应正确。"""
        assert TemplateCategory.VOICE_DESIGN.value == "voice_design"
        assert TemplateCategory.EMOTION_STYLE.value == "emotion_style"
        assert TemplateCategory.SCENE.value == "scene"
        assert TemplateCategory.CHARACTER.value == "character"
        assert TemplateCategory.CUSTOM.value == "custom"

    def test_all_builtin_have_unique_ids(self):
        """所有预置模板 ID 应唯一。"""
        ids = self.expander.list_template_ids()
        assert len(ids) == len(set(ids))

    def test_all_builtin_have_nonempty_templates(self):
        """所有预置模板文本不应为空。"""
        for template in self.expander.get_templates():
            assert template.template_zh, f"模板 {template.id} 中文文本为空"
            assert template.template_en, f"模板 {template.id} 英文文本为空"

    def test_expand_with_special_chars(self):
        """特殊字符扩展。"""
        result = self.expander.expand("新闻!@#播报")
        assert isinstance(result, str)
