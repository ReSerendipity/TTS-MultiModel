# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""提示词扩展模块 — 丰富的 TTS 指令模板与智能扩展。

提供预置的音色描述模板、情感风格模板、场景模板和角色模板，
帮助用户快速生成高质量的 TTS 指令（instruction）文本。
同时支持基于关键词的智能提示词扩展和模板变量替换。

核心功能：
    1. 预置模板库 — 按类别组织的高质量指令模板
       - 音色设计模板（温柔女声、磁性男声、活泼少女等）
       - 情感风格模板（欢快、悲伤、严肃、亲切等）
       - 场景模板（新闻播报、有声书、广告、教育等）
       - 角色模板（ narrator、对话角色等）
    2. 模板变量替换 — 支持 {name}、{emotion} 等变量
    3. 智能扩展 — 根据关键词自动推荐和组合模板
    4. 多语言支持 — 中文/英文模板双语

典型使用::

    from .prompt_expander import PromptExpander, TemplateCategory

    expander = PromptExpander()

    # 获取预置模板
    templates = expander.get_templates(TemplateCategory.VOICE_DESIGN)

    # 使用模板
    instruction = expander.apply_template(
        "gentle_female",
        {"emotion": "温柔", "speed": "偏慢"},
    )
    # -> "温柔的女声，语速偏慢，亲切自然"

    # 智能扩展
    expanded = expander.expand("新闻播报")
    # -> "专业的新闻播音风格，语速适中，吐字清晰，..."
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("tts_multimodel")

# ---------------------------------------------------------------------------
# 枚举与常量
# ---------------------------------------------------------------------------


class TemplateCategory(str, Enum):
    """模板类别枚举。"""

    VOICE_DESIGN = "voice_design"
    EMOTION_STYLE = "emotion_style"
    SCENE = "scene"
    CHARACTER = "character"
    CUSTOM = "custom"


#: 模板变量正则（{variable_name} 格式）
_VAR_PATTERN = re.compile(r"\{(\w+)\}")


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class PromptTemplate:
    """提示词模板。

    Attributes:
        id: 模板唯一标识符。
        category: 模板类别。
        name_zh: 中文名称。
        name_en: 英文名称。
        template_zh: 中文模板文本（含 {变量}）。
        template_en: 英文模板文本。
        description: 模板描述。
        variables: 模板支持的变量名列表。
        tags: 标签列表（用于搜索）。
    """

    id: str
    category: TemplateCategory
    name_zh: str
    name_en: str
    template_zh: str
    template_en: str
    description: str = ""
    variables: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def render(self, params: dict[str, str] | None = None, lang: str = "zh") -> str:
        """渲染模板（替换变量）。

        Args:
            params: 变量参数字典。
            lang: 语言（"zh" 或 "en"）。

        Returns:
            渲染后的文本。
        """
        template = self.template_zh if lang == "zh" else self.template_en
        if not params:
            return template

        def _replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return str(params.get(var_name, match.group(0)))

        return _VAR_PATTERN.sub(_replace, template)


# ---------------------------------------------------------------------------
# 预置模板库
# ---------------------------------------------------------------------------

#: 预置提示词模板列表
_BUILTIN_TEMPLATES: list[PromptTemplate] = [
    # --- 音色设计模板 ---
    PromptTemplate(
        id="gentle_female",
        category=TemplateCategory.VOICE_DESIGN,
        name_zh="温柔女声",
        name_en="Gentle Female",
        template_zh="温柔的女声，语速{speed}，亲切自然，适合朗读和对话",
        template_en="Gentle female voice, {speed} pace, warm and natural, suitable for reading and conversation",
        description="温柔亲切的女性音色，适合有声书、客服、教育场景",
        variables=["speed"],
        tags=["女声", "温柔", "亲切", "female", "gentle"],
    ),
    PromptTemplate(
        id="magnetic_male",
        category=TemplateCategory.VOICE_DESIGN,
        name_zh="磁性男声",
        name_en="Magnetic Male",
        template_zh="磁性深沉的男声，语速{speed}，稳重有力，适合新闻播报和解说",
        template_en="Deep magnetic male voice, {speed} pace, authoritative and steady, suitable for news and narration",
        description="低沉磁性的男性音色，适合新闻、纪录片、广告",
        variables=["speed"],
        tags=["男声", "磁性", "稳重", "male", "deep"],
    ),
    PromptTemplate(
        id="lively_girl",
        category=TemplateCategory.VOICE_DESIGN,
        name_zh="活泼少女",
        name_en="Lively Girl",
        template_zh="活泼开朗的少女声，语速{speed}，充满活力，适合游戏和动画",
        template_en="Lively and cheerful young female voice, {speed} pace, energetic, suitable for games and animation",
        description="活泼可爱的少女音色，适合游戏、动画、短视频",
        variables=["speed"],
        tags=["女声", "活泼", "少女", "lively", "young"],
    ),
    PromptTemplate(
        id="professional_anchor",
        category=TemplateCategory.VOICE_DESIGN,
        name_zh="专业播音",
        name_en="Professional Anchor",
        template_zh="专业的播音员音色，吐字清晰，语速{speed}，字正腔圆",
        template_en="Professional broadcaster voice, clear articulation, {speed} pace, standard pronunciation",
        description="专业播音员音色，适合新闻、正式场合",
        variables=["speed"],
        tags=["播音", "专业", "清晰", "professional", "anchor"],
    ),
    PromptTemplate(
        id="warm_elder",
        category=TemplateCategory.VOICE_DESIGN,
        name_zh="温暖长者",
        name_en="Warm Elder",
        template_zh="温暖慈祥的长者声音，语速{speed}，富有阅历感，适合讲故事",
        template_en="Warm and kind elder voice, {speed} pace, rich in experience, suitable for storytelling",
        description="温暖的长者音色，适合故事、散文、回忆录",
        variables=["speed"],
        tags=["长者", "温暖", "慈祥", "elder", "warm"],
    ),
    # --- 情感风格模板 ---
    PromptTemplate(
        id="emotion_cheerful",
        category=TemplateCategory.EMOTION_STYLE,
        name_zh="欢快",
        name_en="Cheerful",
        template_zh="欢快愉悦的情感，{intensity}程度，语调上扬，充满活力",
        template_en="Cheerful and joyful emotion, {intensity} intensity, upbeat tone, full of energy",
        description="欢快愉悦的情感表达",
        variables=["intensity"],
        tags=["欢快", "愉悦", "cheerful", "happy"],
    ),
    PromptTemplate(
        id="emotion_sad",
        category=TemplateCategory.EMOTION_STYLE,
        name_zh="悲伤",
        name_en="Sad",
        template_zh="悲伤沉重的情感，{intensity}程度，语调低沉，带哭腔",
        template_en="Sad and heavy emotion, {intensity} intensity, low tone, with crying quality",
        description="悲伤沉重的情感表达",
        variables=["intensity"],
        tags=["悲伤", "沉重", "sad", "melancholy"],
    ),
    PromptTemplate(
        id="emotion_serious",
        category=TemplateCategory.EMOTION_STYLE,
        name_zh="严肃",
        name_en="Serious",
        template_zh="严肃庄重的情感，{intensity}程度，语调平稳，不苟言笑",
        template_en="Serious and solemn emotion, {intensity} intensity, steady tone, no laughter",
        description="严肃庄重的情感表达",
        variables=["intensity"],
        tags=["严肃", "庄重", "serious", "solemn"],
    ),
    PromptTemplate(
        id="emotion_intimate",
        category=TemplateCategory.EMOTION_STYLE,
        name_zh="亲切",
        name_en="Intimate",
        template_zh="亲切温和的情感，{intensity}程度，像在和朋友聊天",
        template_en="Intimate and gentle emotion, {intensity} intensity, like chatting with a friend",
        description="亲切温和的情感表达",
        variables=["intensity"],
        tags=["亲切", "温和", "intimate", "warm"],
    ),
    PromptTemplate(
        id="emotion_excited",
        category=TemplateCategory.EMOTION_STYLE,
        name_zh="兴奋",
        name_en="Excited",
        template_zh="兴奋激动的情感，{intensity}程度，语速加快，声调升高",
        template_en="Excited and thrilled emotion, {intensity} intensity, faster pace, higher pitch",
        description="兴奋激动的情感表达",
        variables=["intensity"],
        tags=["兴奋", "激动", "excited", "thrilled"],
    ),
    # --- 场景模板 ---
    PromptTemplate(
        id="scene_news",
        category=TemplateCategory.SCENE,
        name_zh="新闻播报",
        name_en="News Broadcast",
        template_zh="专业的新闻播音风格，语速适中，吐字清晰，字正腔圆，客观中立",
        template_en="Professional news broadcast style, moderate pace, clear articulation, objective and neutral",
        description="新闻播报场景模板",
        variables=[],
        tags=["新闻", "播报", "news", "broadcast"],
    ),
    PromptTemplate(
        id="scene_audiobook",
        category=TemplateCategory.SCENE,
        name_zh="有声书",
        name_en="Audiobook",
        template_zh="有声书朗读风格，富有感情，语速{speed}，角色区分明显，带有叙述感",
        template_en="Audiobook narration style, expressive, {speed} pace, distinct character voices, narrative quality",
        description="有声书朗读场景模板",
        variables=["speed"],
        tags=["有声书", "朗读", "audiobook", "narration"],
    ),
    PromptTemplate(
        id="scene_advertisement",
        category=TemplateCategory.SCENE,
        name_zh="广告",
        name_en="Advertisement",
        template_zh="广告播报风格，热情有感染力，语速{speed}，重点突出，吸引注意",
        template_en="Advertisement voice-over style, enthusiastic and engaging, {speed} pace, emphasis on key points",
        description="广告播报场景模板",
        variables=["speed"],
        tags=["广告", "播报", "advertisement", "commercial"],
    ),
    PromptTemplate(
        id="scene_education",
        category=TemplateCategory.SCENE,
        name_zh="教育",
        name_en="Education",
        template_zh="教育讲解风格，清晰耐心，语速{speed}，重点重复，易于理解",
        template_en="Educational lecture style, clear and patient, {speed} pace, key point repetition, easy to understand",
        description="教育讲解场景模板",
        variables=["speed"],
        tags=["教育", "讲解", "education", "lecture"],
    ),
    PromptTemplate(
        id="scene_podcast",
        category=TemplateCategory.SCENE,
        name_zh="播客",
        name_en="Podcast",
        template_zh="播客对话风格，轻松自然，语速{speed}，像在和朋友聊天",
        template_en="Podcast conversation style, relaxed and natural, {speed} pace, like chatting with friends",
        description="播客对话场景模板",
        variables=["speed"],
        tags=["播客", "对话", "podcast", "conversation"],
    ),
    PromptTemplate(
        id="scene_game",
        category=TemplateCategory.SCENE,
        name_zh="游戏",
        name_en="Game",
        template_zh="游戏角色配音风格，{character_type}角色，情感丰富，语速{speed}",
        template_en="Game character voice style, {character_type} character, rich emotion, {speed} pace",
        description="游戏配音场景模板",
        variables=["character_type", "speed"],
        tags=["游戏", "配音", "game", "voice_acting"],
    ),
    # --- 角色模板 ---
    PromptTemplate(
        id="char_narrator",
        category=TemplateCategory.CHARACTER,
        name_zh="旁白",
        name_en="Narrator",
        template_zh="旁白叙述者，{gender}声，沉稳大气，全知视角，语速{speed}",
        template_en="Narrator, {gender} voice, steady and grand, omniscient perspective, {speed} pace",
        description="旁白叙述者角色模板",
        variables=["gender", "speed"],
        tags=["旁白", "叙述", "narrator", "voiceover"],
    ),
    PromptTemplate(
        id="char_hero",
        category=TemplateCategory.CHARACTER,
        name_zh="主角",
        name_en="Hero",
        template_zh="故事主角，{gender}声，{age}岁，{personality}性格，语速{speed}",
        template_en="Story protagonist, {gender} voice, {age} years old, {personality} personality, {speed} pace",
        description="故事主角角色模板",
        variables=["gender", "age", "personality", "speed"],
        tags=["主角", "英雄", "hero", "protagonist"],
    ),
    PromptTemplate(
        id="char_villain",
        category=TemplateCategory.CHARACTER,
        name_zh="反派",
        name_en="Villain",
        template_zh="反派角色，{gender}声，阴险狡诈，语速{speed}，带冷笑",
        template_en="Villain character, {gender} voice, sinister and cunning, {speed} pace, with cold laughter",
        description="反派角色模板",
        variables=["gender", "speed"],
        tags=["反派", "坏人", "villain", "antagonist"],
    ),
    PromptTemplate(
        id="char_mentor",
        category=TemplateCategory.CHARACTER,
        name_zh="导师",
        name_en="Mentor",
        template_zh="智慧导师角色，{gender}声，语速{speed}，循循善诱，富有哲理",
        template_en="Wise mentor character, {gender} voice, {speed} pace, guiding and philosophical",
        description="智慧导师角色模板",
        variables=["gender", "speed"],
        tags=["导师", "智者", "mentor", "wise"],
    ),
]


# ---------------------------------------------------------------------------
# 关键词到模板的映射（用于智能扩展）
# ---------------------------------------------------------------------------

#: 关键词到模板 ID 的映射
_KEYWORD_MAP: dict[str, list[str]] = {
    # 音色关键词
    "温柔": ["gentle_female"],
    "磁性": ["magnetic_male"],
    "活泼": ["lively_girl"],
    "播音": ["professional_anchor"],
    "长者": ["warm_elder"],
    "gentle": ["gentle_female"],
    "magnetic": ["magnetic_male"],
    "lively": ["lively_girl"],
    # 情感关键词
    "欢快": ["emotion_cheerful"],
    "悲伤": ["emotion_sad"],
    "严肃": ["emotion_serious"],
    "亲切": ["emotion_intimate"],
    "兴奋": ["emotion_excited"],
    "cheerful": ["emotion_cheerful"],
    "sad": ["emotion_sad"],
    "serious": ["emotion_serious"],
    # 场景关键词
    "新闻": ["scene_news"],
    "有声书": ["scene_audiobook"],
    "广告": ["scene_advertisement"],
    "教育": ["scene_education"],
    "播客": ["scene_podcast"],
    "游戏": ["scene_game"],
    "news": ["scene_news"],
    "audiobook": ["scene_audiobook"],
    "advertisement": ["scene_advertisement"],
    # 角色关键词
    "旁白": ["char_narrator"],
    "主角": ["char_hero"],
    "反派": ["char_villain"],
    "导师": ["char_mentor"],
    "narrator": ["char_narrator"],
    "hero": ["char_hero"],
    "villain": ["char_villain"],
}


# ---------------------------------------------------------------------------
# PromptExpander
# ---------------------------------------------------------------------------


class PromptExpander:
    """提示词扩展器。

    管理预置模板库，提供模板检索、变量替换和智能扩展功能。

    核心能力：
        1. 按类别/标签/关键词检索模板
        2. 模板变量替换（render）
        3. 基于关键词的智能提示词扩展
        4. 自定义模板注册
    """

    def __init__(self) -> None:
        """初始化提示词扩展器，加载预置模板。"""
        self._templates: dict[str, PromptTemplate] = {}
        for template in _BUILTIN_TEMPLATES:
            self._templates[template.id] = template

        logger.info("PromptExpander 初始化完成，加载 %d 个预置模板", len(self._templates))

    def get_template(self, template_id: str) -> PromptTemplate | None:
        """根据 ID 获取模板。

        Args:
            template_id: 模板唯一标识符。

        Returns:
            PromptTemplate 或 None（不存在时）。
        """
        return self._templates.get(template_id)

    def get_templates(
        self,
        category: TemplateCategory | None = None,
    ) -> list[PromptTemplate]:
        """获取模板列表。

        Args:
            category: 模板类别，None 时返回全部。

        Returns:
            模板列表。
        """
        if category is None:
            return list(self._templates.values())
        return [t for t in self._templates.values() if t.category == category]

    def search_templates(self, query: str) -> list[PromptTemplate]:
        """按关键词搜索模板。

        在模板的 name、tags、description 中搜索匹配的关键词。

        Args:
            query: 搜索关键词。

        Returns:
            匹配的模板列表。
        """
        query_lower = query.lower()
        results: list[PromptTemplate] = []

        for template in self._templates.values():
            # 搜索名称
            if query_lower in template.name_zh.lower() or query_lower in template.name_en.lower():
                results.append(template)
                continue

            # 搜索标签
            if any(query_lower in tag.lower() for tag in template.tags):
                results.append(template)
                continue

            # 搜索描述
            if query_lower in template.description.lower():
                results.append(template)
                continue

        return results

    def apply_template(
        self,
        template_id: str,
        params: dict[str, str] | None = None,
        lang: str = "zh",
    ) -> str:
        """应用模板（渲染变量）。

        Args:
            template_id: 模板 ID。
            params: 变量参数字典。
            lang: 语言（"zh" 或 "en"）。

        Returns:
            渲染后的文本。

        Raises:
            KeyError: 模板 ID 不存在。
        """
        template = self._templates.get(template_id)
        if template is None:
            raise KeyError(f"模板不存在: {template_id}")

        return template.render(params, lang)

    def expand(
        self,
        text: str,
        lang: str = "zh",
        max_templates: int = 3,
    ) -> str:
        """智能扩展提示词。

        根据输入文本中的关键词，自动匹配并组合模板内容，
        生成更详细的提示词描述。

        Args:
            text: 用户输入的简短描述。
            lang: 输出语言。
            max_templates: 最多组合的模板数量。

        Returns:
            扩展后的详细提示词。
        """
        if not text or not text.strip():
            return text.strip() if text else ""

        matched_ids: list[str] = []
        for keyword, template_ids in _KEYWORD_MAP.items():
            if keyword in text.lower():
                for tid in template_ids:
                    if tid not in matched_ids:
                        matched_ids.append(tid)

        if not matched_ids:
            return text

        # 取前 max_templates 个模板
        matched_ids = matched_ids[:max_templates]

        # 组合模板内容
        parts: list[str] = [text.strip()]
        for tid in matched_ids:
            template = self._templates.get(tid)
            if template:
                rendered = template.render(None, lang)
                if rendered not in parts:  # 避免重复
                    parts.append(rendered)

        return "，".join(parts)

    def register_template(self, template: PromptTemplate) -> bool:
        """注册自定义模板。

        Args:
            template: 要注册的模板。

        Returns:
            注册成功返回 True，ID 冲突返回 False。
        """
        if template.id in self._templates:
            logger.warning("模板 ID 冲突: %s", template.id)
            return False

        self._templates[template.id] = template
        logger.info("注册自定义模板: %s (%s)", template.id, template.name_zh)
        return True

    def remove_template(self, template_id: str) -> bool:
        """移除模板。

        Args:
            template_id: 模板 ID。

        Returns:
            移除成功返回 True，不存在返回 False。
        """
        if template_id in self._templates:
            del self._templates[template_id]
            return True
        return False

    def list_categories(self) -> list[TemplateCategory]:
        """列出所有有模板的类别。

        Returns:
            类别列表。
        """
        return list({t.category for t in self._templates.values()})

    def get_template_count(self, category: TemplateCategory | None = None) -> int:
        """获取模板数量。

        Args:
            category: 类别，None 时返回总数。

        Returns:
            模板数量。
        """
        if category is None:
            return len(self._templates)
        return sum(1 for t in self._templates.values() if t.category == category)

    def list_template_ids(self) -> list[str]:
        """列出所有模板 ID。

        Returns:
            模板 ID 列表。
        """
        return list(self._templates.keys())


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

_expander_instance: PromptExpander | None = None


def get_prompt_expander() -> PromptExpander:
    """获取模块级 PromptExpander 单例。

    Returns:
        PromptExpander 实例。
    """
    global _expander_instance
    if _expander_instance is None:
        _expander_instance = PromptExpander()
    return _expander_instance


def expand_prompt(text: str, lang: str = "zh") -> str:
    """便捷函数：扩展提示词。

    Args:
        text: 用户输入的简短描述。
        lang: 输出语言。

    Returns:
        扩展后的详细提示词。
    """
    return get_prompt_expander().expand(text, lang)


def apply_template(
    template_id: str,
    params: dict[str, str] | None = None,
    lang: str = "zh",
) -> str:
    """便捷函数：应用模板。

    Args:
        template_id: 模板 ID。
        params: 变量参数。
        lang: 语言。

    Returns:
        渲染后的文本。
    """
    return get_prompt_expander().apply_template(template_id, params, lang)
