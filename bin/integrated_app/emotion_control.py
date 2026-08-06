"""情感/风格/韵律控制模块（第 7 章）

提供统一的情感向量、韵律标签解析、CFG 控制和自然语言指令解析能力，
供 VoxCPM2 和 IndexTTS2 引擎共享使用。

核心组件:
    - EmotionVector: 8 维情感向量数据类（与 IndexTTS2 EMOTION_DIMENSIONS 对齐）
    - ProsodyTagParser: ChatTTS / Chatterbox 风格韵律标签解析器
    - CFGController: Classifier-Free Guidance 强度控制器
    - InstructParser: CosyVoice 风格自然语言指令解析器
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("tts_multimodel")


# ---------------------------------------------------------------------------
# EmotionVector: 8 维情感向量
# ---------------------------------------------------------------------------

# 与 IndexTTS2Engine.EMOTION_DIMENSIONS 保持一致
EMOTION_DIMENSION_NAMES: tuple[str, ...] = (
    "happy",  # 开心
    "angry",  # 愤怒
    "sad",  # 悲伤
    "afraid",  # 害怕
    "disgusted",  # 厌恶
    "melancholic",  # 忧郁
    "surprised",  # 惊讶
    "calm",  # 平静
)

# 预设情感：名称 -> 情感维度字典
EMOTION_PRESETS: dict[str, dict[str, float]] = {
    "neutral": {
        "happy": 0.0,
        "angry": 0.0,
        "sad": 0.0,
        "afraid": 0.0,
        "disgusted": 0.0,
        "melancholic": 0.0,
        "surprised": 0.0,
        "calm": 0.5,
    },
    "happy": {
        "happy": 0.9,
        "angry": 0.0,
        "sad": 0.0,
        "afraid": 0.0,
        "disgusted": 0.0,
        "melancholic": 0.0,
        "surprised": 0.1,
        "calm": 0.1,
    },
    "sad": {
        "happy": 0.0,
        "angry": 0.0,
        "sad": 0.9,
        "afraid": 0.0,
        "disgusted": 0.0,
        "melancholic": 0.3,
        "surprised": 0.0,
        "calm": 0.1,
    },
    "angry": {
        "happy": 0.0,
        "angry": 0.9,
        "sad": 0.0,
        "afraid": 0.0,
        "disgusted": 0.2,
        "melancholic": 0.0,
        "surprised": 0.0,
        "calm": 0.0,
    },
    "calm": {
        "happy": 0.0,
        "angry": 0.0,
        "sad": 0.0,
        "afraid": 0.0,
        "disgusted": 0.0,
        "melancholic": 0.0,
        "surprised": 0.0,
        "calm": 0.9,
    },
}


def _clamp_01(value: float) -> float:
    """将浮点数限制在 [0.0, 1.0] 范围内。"""
    return max(0.0, min(1.0, value))


@dataclass
class EmotionVector:
    """8 维情感向量数据类。

    维度与 IndexTTS2Engine.EMOTION_DIMENSIONS 完全对齐，
    支持 tensor 转换、字典序列化和预设情感。

    Attributes:
        happy: 开心程度 (0.0-1.0)
        angry: 愤怒程度 (0.0-1.0)
        sad: 悲伤程度 (0.0-1.0)
        afraid: 害怕程度 (0.0-1.0)
        disgusted: 厌恶程度 (0.0-1.0)
        melancholic: 忧郁程度 (0.0-1.0)
        surprised: 惊讶程度 (0.0-1.0)
        calm: 平静程度 (0.0-1.0)
    """

    happy: float = 0.0
    angry: float = 0.0
    sad: float = 0.0
    afraid: float = 0.0
    disgusted: float = 0.0
    melancholic: float = 0.0
    surprised: float = 0.0
    calm: float = 0.0

    def __post_init__(self) -> None:
        """初始化后自动将所有维度限制在 [0.0, 1.0] 范围内。"""
        for dim in EMOTION_DIMENSION_NAMES:
            setattr(self, dim, _clamp_01(getattr(self, dim)))

    def to_tensor(self):
        """将情感向量转换为 torch.Tensor（形状 [8]）。

        使用延迟导入避免启动时加载 torch。

        Returns:
            torch.Tensor: 8 维情感向量张量

        Raises:
            ImportError: torch 未安装时抛出
        """
        import torch

        values = [getattr(self, dim) for dim in EMOTION_DIMENSION_NAMES]
        return torch.tensor(values, dtype=torch.float32)

    def to_list(self) -> list[float]:
        """将情感向量转换为浮点列表（按 EMOTION_DIMENSION_NAMES 顺序）。

        Returns:
            list[float]: 8 维情感值列表
        """
        return [getattr(self, dim) for dim in EMOTION_DIMENSION_NAMES]

    def to_dict(self) -> dict[str, float]:
        """将情感向量转换为字典。

        Returns:
            dict[str, float]: 维度名 -> 值的映射
        """
        return {dim: getattr(self, dim) for dim in EMOTION_DIMENSION_NAMES}

    @classmethod
    def from_dict(cls, data: dict[str, float]) -> EmotionVector:
        """从字典创建 EmotionVector 实例。

        Args:
            data: 维度名 -> 值的映射，缺失维度默认为 0.0，
                  多余字段被忽略。

        Returns:
            EmotionVector: 新实例
        """
        kwargs: dict[str, float] = {}
        for dim in EMOTION_DIMENSION_NAMES:
            if dim in data:
                kwargs[dim] = float(data[dim])
        return cls(**kwargs)

    @classmethod
    def from_list(cls, values: list[float] | tuple[float, ...]) -> EmotionVector:
        """从浮点列表创建 EmotionVector 实例（按 EMOTION_DIMENSION_NAMES 顺序）。

        Args:
            values: 8 个浮点数的列表/元组

        Returns:
            EmotionVector: 新实例

        Raises:
            ValueError: 列表长度不等于 8 时抛出
        """
        if len(values) != len(EMOTION_DIMENSION_NAMES):
            raise ValueError(f"情感向量需要 {len(EMOTION_DIMENSION_NAMES)} 个值，实际收到 {len(values)} 个")
        return cls(**dict(zip(EMOTION_DIMENSION_NAMES, values)))

    @classmethod
    def preset(cls, name: str) -> EmotionVector:
        """从预设名称创建情感向量。

        Args:
            name: 预设名称（neutral/happy/sad/angry/calm）

        Returns:
            EmotionVector: 预设情感向量

        Raises:
            ValueError: 预设名称不存在时抛出
        """
        name_lower = name.strip().lower()
        if name_lower not in EMOTION_PRESETS:
            available = ", ".join(sorted(EMOTION_PRESETS.keys()))
            raise ValueError(f"未知预设情感 '{name}'，可选: {available}")
        return cls.from_dict(EMOTION_PRESETS[name_lower])

    def is_neutral(self, threshold: float = 0.01) -> bool:
        """判断是否为中性情感（所有维度低于阈值）。

        Args:
            threshold: 判定阈值，默认 0.01

        Returns:
            bool: 所有维度低于阈值时返回 True
        """
        return all(getattr(self, dim) < threshold for dim in EMOTION_DIMENSION_NAMES)

    def dominant_emotion(self) -> str | None:
        """返回主导情感维度名称。

        Returns:
            str | None: 值最大的维度名，全部为 0 时返回 None
        """
        best_dim: str | None = None
        best_val: float = 0.0
        for dim in EMOTION_DIMENSION_NAMES:
            val = getattr(self, dim)
            if val > best_val:
                best_val = val
                best_dim = dim
        return best_dim

    def blend(self, other: EmotionVector, alpha: float = 0.5) -> EmotionVector:
        """线性混合两个情感向量。

        Args:
            other: 另一个情感向量
            alpha: 混合系数，0.0 = 完全使用 self，1.0 = 完全使用 other

        Returns:
            EmotionVector: 混合后的情感向量
        """
        alpha = _clamp_01(alpha)
        kwargs: dict[str, float] = {}
        for dim in EMOTION_DIMENSION_NAMES:
            self_val = getattr(self, dim)
            other_val = getattr(other, dim)
            kwargs[dim] = (1.0 - alpha) * self_val + alpha * other_val
        return EmotionVector(**kwargs)

    def __repr__(self) -> str:
        """返回情感向量的字符串表示（仅显示非零维度）。

        Returns:
            str: 如 "EmotionVector(happy=0.90, calm=0.10)" 或 "EmotionVector(neutral)"
        """
        parts = []
        for dim in EMOTION_DIMENSION_NAMES:
            val = getattr(self, dim)
            if val > 0.01:
                parts.append(f"{dim}={val:.2f}")
        if not parts:
            return "EmotionVector(neutral)"
        return f"EmotionVector({', '.join(parts)})"


# ---------------------------------------------------------------------------
# ProsodyTagParser: 韵律标签解析器
# ---------------------------------------------------------------------------

# ChatTTS 风格标签的正则模式
_CHAT_TTS_TAG_PATTERN = re.compile(r"\[(?P<tag>laugh|uv_break|oral_(?P<oral_idx>\d))\]")

# Chatterbox 风格 [paralinguistic] 标签的正则模式
_CHATTERBOX_TAG_PATTERN = re.compile(r"\[(?P<tag>[^\]]+)\]")

# 已知的特殊标签（不作为普通副语言标签处理）
_KNOWN_CHAT_TTS_TAGS = frozenset(
    {
        "laugh",
        "uv_break",
        *(f"oral_{i}" for i in range(10)),
    }
)


@dataclass
class ProsodyTag:
    """韵律标签结构。

    Attributes:
        position: 标签在原始文本中的字符偏移位置
        tag_type: 标签类型 (chatTTS / paralinguistic)
        tag_value: 标签值（如 "laugh", "uv_break", "oral_5" 或副语言描述文本）
    """

    position: int
    tag_type: str
    tag_value: str


class ProsodyTagParser:
    """韵律标签解析器。

    支持 ChatTTS 风格韵律标签和 Chatterbox 风格副语言标签的解析。
    可根据目标引擎后端对标签进行剥离或替换处理。

    支持的 ChatTTS 标签:
        - [laugh]: 笑声
        - [uv_break]: 停顿/间歇
        - [oral_0] ~ [oral_9]: 口语化程度

    支持的 Chatterbox 标签:
        - [任意副语言描述]: 如 [sigh], [gasp], [whisper] 等
        - 不与 ChatTTS 已知标签重叠的 [xxx] 均视为副语言标签
    """

    def parse(self, text: str) -> list[ProsodyTag]:
        """解析文本中的所有韵律标签。

        Args:
            text: 包含韵律标签的输入文本

        Returns:
            list[ProsodyTag]: 按位置排序的标签列表
        """
        if not text:
            return []

        tags: list[ProsodyTag] = []

        # 先解析 ChatTTS 风格标签
        for m in _CHAT_TTS_TAG_PATTERN.finditer(text):
            tag_value = m.group("tag")
            tags.append(
                ProsodyTag(
                    position=m.start(),
                    tag_type="chatTTS",
                    tag_value=tag_value,
                )
            )

        # 再解析 Chatterbox 风格副语言标签（排除已匹配的 ChatTTS 标签）
        chatTTS_spans = {m.start(): m.end() for m in _CHAT_TTS_TAG_PATTERN.finditer(text)}
        for m in _CHATTERBOX_TAG_PATTERN.finditer(text):
            tag_value = m.group("tag")
            # 跳过已知 ChatTTS 标签（已在上一步处理）
            if tag_value in _KNOWN_CHAT_TTS_TAGS:
                continue
            # 跳过与 ChatTTS 标签位置重叠的匹配
            if m.start() in chatTTS_spans:
                continue
            tags.append(
                ProsodyTag(
                    position=m.start(),
                    tag_type="paralinguistic",
                    tag_value=tag_value,
                )
            )

        # 按位置排序
        tags.sort(key=lambda t: t.position)
        return tags

    def strip_tags(self, text: str, engine: str = "voxcpm2") -> str:
        """从文本中剥离韵律标签，返回纯文本。

        不同引擎后端可能需要不同的处理策略：
        - VoxCPM2: 不支持标签，全部剥离
        - IndexTTS2: 保留标签作为情感参考（但目前也做剥离，由 emo_text 通道传递）

        Args:
            text: 包含韵律标签的输入文本
            engine: 目标引擎名称

        Returns:
            str: 剥离标签后的纯文本
        """
        if not text:
            return text

        result = _CHAT_TTS_TAG_PATTERN.sub("", text)
        # 清理残留的 Chatterbox 标签
        result = _CHATTERBOX_TAG_PATTERN.sub("", result)
        # 清理连续空格
        result = re.sub(r"\s{2,}", " ", result).strip()
        return result

    def replace_tags(
        self,
        text: str,
        engine: str = "voxcpm2",
        replacement: str = "",
    ) -> str:
        """用指定字符串替换韵律标签。

        Args:
            text: 包含韵律标签的输入文本
            engine: 目标引擎名称
            replacement: 替换字符串，默认为空字符串（等同于剥离）

        Returns:
            str: 替换标签后的文本
        """
        if not text:
            return text

        result = _CHAT_TTS_TAG_PATTERN.sub(replacement, text)
        result = _CHATTERBOX_TAG_PATTERN.sub(replacement, result)
        # 清理连续空格
        if replacement:
            result = re.sub(r"\s{2,}", " ", result).strip()
        return result

    def extract_paralinguistic_text(self, text: str) -> str:
        """从文本中提取副语言标签内容，组合为情感描述文本。

        用于将 Chatterbox 标签转换为 IndexTTS2 的 emo_text 输入。

        Args:
            text: 包含韵律标签的输入文本

        Returns:
            str: 副语言描述拼接文本（如 "sigh, gasp"）
        """
        tags = self.parse(text)
        paralinguistic = [t.tag_value for t in tags if t.tag_type == "paralinguistic"]
        return ", ".join(paralinguistic)


# ---------------------------------------------------------------------------
# CFGController: Classifier-Free Guidance 控制器
# ---------------------------------------------------------------------------

# 风格预设 -> CFG 值范围
_CFG_PRESETS: dict[str, tuple[float, float]] = {
    "natural": (1.0, 1.5),  # 自然风格：低 CFG，减少过度强化
    "expressive": (2.0, 3.0),  # 表现力风格：中等 CFG，增强风格表达
    "dramatic": (3.0, 5.0),  # 戏剧风格：高 CFG，最大化风格差异
}

# 风格预设 -> 推荐默认 CFG 值
_CFG_DEFAULTS: dict[str, float] = {
    "natural": 1.0,
    "expressive": 2.5,
    "dramatic": 3.5,
}

# 全局 CFG 合法范围
_CFG_MIN = 0.5
_CFG_MAX = 10.0


class CFGController:
    """Classifier-Free Guidance (CFG) 强度控制器。

    控制 CFG 值以调节风格表现力。CFG 值越高，输出越倾向于
    条件引导方向，风格越明显但可能出现过度强化（重复、不自然）。
    CFG 值越低，输出越接近无条件分布，自然但缺乏风格。

    预设风格:
        - natural (1.0-1.5): 自然语调
        - expressive (2.0-3.0): 表现力语调
        - dramatic (3.0-5.0): 戏剧化语调
    """

    def validate_cfg_range(self, cfg_value: float) -> float:
        """验证并钳制 CFG 值到合法范围。

        Args:
            cfg_value: 输入的 CFG 值

        Returns:
            float: 钳制后的 CFG 值，范围 [_CFG_MIN, _CFG_MAX]
        """
        clamped = max(_CFG_MIN, min(_CFG_MAX, float(cfg_value)))
        if clamped != cfg_value:
            logger.debug(
                f"[CFGController] CFG 值 {cfg_value} 超出合法范围 [{_CFG_MIN}, {_CFG_MAX}]，已钳制为 {clamped}"
            )
        return clamped

    def suggest_cfg_for_style(self, style: str) -> float:
        """根据风格名称推荐 CFG 值。

        Args:
            style: 风格名称 (natural/expressive/dramatic)

        Returns:
            float: 推荐的 CFG 值

        Raises:
            ValueError: 未知风格名称时抛出
        """
        style_lower = style.strip().lower()
        if style_lower not in _CFG_DEFAULTS:
            available = ", ".join(sorted(_CFG_DEFAULTS.keys()))
            raise ValueError(f"未知风格 '{style}'，可选: {available}")
        return _CFG_DEFAULTS[style_lower]

    def get_cfg_range_for_style(self, style: str) -> tuple[float, float]:
        """获取指定风格的 CFG 合法范围。

        Args:
            style: 风格名称

        Returns:
            tuple[float, float]: (min_cfg, max_cfg)

        Raises:
            ValueError: 未知风格名称时抛出
        """
        style_lower = style.strip().lower()
        if style_lower not in _CFG_PRESETS:
            available = ", ".join(sorted(_CFG_PRESETS.keys()))
            raise ValueError(f"未知风格 '{style}'，可选: {available}")
        return _CFG_PRESETS[style_lower]

    def list_styles(self) -> list[str]:
        """列出所有可用的风格预设名称。

        Returns:
            list[str]: 风格名称列表
        """
        return list(_CFG_DEFAULTS.keys())

    def validate_for_style(self, cfg_value: float, style: str) -> float:
        """验证 CFG 值是否适合指定风格，并钳制到风格范围。

        先将值钳制到全局合法范围，再检查是否在风格推荐范围内。
        如果超出风格范围，钳制到风格边界并记录警告。

        Args:
            cfg_value: 输入的 CFG 值
            style: 风格名称

        Returns:
            float: 适合该风格的 CFG 值
        """
        # 先钳制到全局范围
        value = self.validate_cfg_range(cfg_value)

        try:
            style_min, style_max = self.get_cfg_range_for_style(style)
        except ValueError:
            # 未知风格，仅使用全局范围
            return value

        if value < style_min or value > style_max:
            clamped = max(style_min, min(style_max, value))
            logger.info(
                f"[CFGController] CFG 值 {value} 超出风格 '{style}' "
                f"推荐范围 [{style_min}, {style_max}]，已钳制为 {clamped}"
            )
            return clamped
        return value


# ---------------------------------------------------------------------------
# InstructParser: CosyVoice 风格自然语言指令解析器
# ---------------------------------------------------------------------------

# 情感关键词映射（中文）
_EMOTION_KEYWORDS_ZH: dict[str, list[str]] = {
    "happy": ["开心", "高兴", "快乐", "兴奋", "喜悦", "欢快", "愉快", "欢乐"],
    "angry": ["愤怒", "生气", "恼火", "暴怒", "气愤", "发怒"],
    "sad": ["悲伤", "难过", "伤心", "哀伤", "悲痛", "忧伤", "凄凉"],
    "afraid": ["害怕", "恐惧", "惊恐", "担心", "焦虑", "畏惧"],
    "disgusted": ["厌恶", "恶心", "反感", "讨厌", "嫌弃"],
    "melancholic": ["忧郁", "惆怅", "消沉", "低落", "郁闷", "沉闷"],
    "surprised": ["惊讶", "吃惊", "意外", "震惊", "惊奇"],
    "calm": ["平静", "冷静", "沉着", "淡定", "从容", "安宁", "温和"],
}

# 情感关键词映射（英文）
_EMOTION_KEYWORDS_EN: dict[str, list[str]] = {
    "happy": ["happy", "joyful", "cheerful", "excited", "delighted", "glad"],
    "angry": ["angry", "furious", "mad", "outraged", "irritated"],
    "sad": ["sad", "sorrowful", "melancholy", "gloomy", "depressed", "down"],
    "afraid": ["afraid", "fearful", "scared", "anxious", "worried", "terrified"],
    "disgusted": ["disgusted", "repulsed", "revolted", "nauseated"],
    "melancholic": ["melancholic", "wistful", "pensive", "somber"],
    "surprised": ["surprised", "astonished", "shocked", "amazed", "startled"],
    "calm": ["calm", "peaceful", "serene", "tranquil", "composed", "gentle"],
}

# 语速关键词
_SPEED_KEYWORDS_ZH: dict[str, list[str]] = {
    "slow": ["慢", "缓慢", "慢慢", "迟缓"],
    "fast": ["快", "快速", "迅速", "急促"],
    "normal": ["正常", "中等", "适中"],
}

_SPEED_KEYWORDS_EN: dict[str, list[str]] = {
    "slow": ["slow", "slowly", "leisurely"],
    "fast": ["fast", "quickly", "rapidly", "swiftly"],
    "normal": ["normal", "moderate", "medium"],
}

# 语言/方言关键词
_LANGUAGE_HINTS_ZH: dict[str, list[str]] = {
    "zh": ["中文", "汉语", "普通话"],
    "en": ["英文", "英语"],
    "ja": ["日文", "日语"],
    "ko": ["韩文", "韩语"],
    "cantonese": ["粤语", "广东话", "白话"],
    "sichuan": ["四川话", "川话"],
    "northeast": ["东北话", "东北方言"],
}

_LANGUAGE_HINTS_EN: dict[str, list[str]] = {
    "zh": ["Chinese", "Mandarin"],
    "en": ["English"],
    "ja": ["Japanese"],
    "ko": ["Korean"],
    "cantonese": ["Cantonese"],
}

# 方言标识列表
_DIALECT_KEYS = frozenset({"cantonese", "sichuan", "northeast"})


@dataclass
class InstructResult:
    """自然语言指令解析结果。

    Attributes:
        emotion: 解析出的情感向量（可能为 None）
        emotion_name: 识别出的情感名称（如 "happy"）
        speed: 语速提示 ("slow" / "fast" / "normal" / None)
        language: 语言提示（如 "zh", "en"）
        dialect: 方言提示（如 "cantonese", "sichuan"）
        raw_instruction: 原始指令文本
    """

    emotion: EmotionVector | None = None
    emotion_name: str | None = None
    speed: str | None = None
    language: str | None = None
    dialect: str | None = None
    raw_instruction: str = ""


class InstructParser:
    """CosyVoice 风格自然语言指令解析器。

    解析自然语言中的情感、语速、语言、方言等提示，
    映射为引擎可用的结构化参数。

    支持的指令示例:
        - "用悲伤的语气说" -> emotion=sad
        - "speak slowly in a calm voice" -> speed=slow, emotion=calm
        - "用四川话说，开心点" -> dialect=sichuan, emotion=happy
        - "快速地、愤怒地朗读" -> speed=fast, emotion=angry
    """

    def parse(self, instruction: str) -> InstructResult:
        """解析自然语言指令。

        Args:
            instruction: 自然语言指令文本

        Returns:
            InstructResult: 解析结果
        """
        if not instruction or not instruction.strip():
            return InstructResult(raw_instruction=instruction)

        text = instruction.strip()

        # 1. 解析情感
        emotion_name = self._detect_emotion(text)
        emotion = None
        if emotion_name:
            emotion = EmotionVector.preset(emotion_name)

        # 2. 解析语速
        speed = self._detect_speed(text)

        # 3. 解析语言/方言
        language, dialect = self._detect_language(text)

        return InstructResult(
            emotion=emotion,
            emotion_name=emotion_name,
            speed=speed,
            language=language,
            dialect=dialect,
            raw_instruction=text,
        )

    def to_engine_params(self, instruction: str, engine: str = "voxcpm2") -> dict[str, Any]:
        """解析指令并转换为引擎特定参数。

        Args:
            instruction: 自然语言指令文本
            engine: 目标引擎名称

        Returns:
            dict[str, Any]: 引擎特定参数字典
        """
        result = self.parse(instruction)
        params: dict[str, Any] = {}

        if engine == "indextts2":
            # IndexTTS2: 情感通过 emo_vector 或 emo_text 传递
            if result.emotion is not None and not result.emotion.is_neutral():
                params["emo_vector"] = result.emotion.to_list()
                params["emo_alpha"] = 0.8
            elif result.emotion_name:
                # 如果只识别到情感名但向量为中性，使用文本模式
                params["emo_text"] = f"用{result.emotion_name}的语气"
                params["use_emo_text"] = True

            # 语速 -> tempo_factor 的粗略估算
            if result.speed == "slow":
                params["tempo_factor"] = 0.7
            elif result.speed == "fast":
                params["tempo_factor"] = 1.4
        else:
            # VoxCPM2: 情感通过 instruction 文本传递
            if result.emotion_name:
                params["instruction"] = instruction

            # CFG 根据情感强度调整
            if result.emotion_name and result.emotion_name != "neutral":
                params["cfg_value"] = 2.5  # 有明确情感倾向时使用表现力 CFG
            else:
                params["cfg_value"] = 1.0  # 自然风格

            # 语速
            if result.speed == "slow":
                params["speed"] = 0.7
            elif result.speed == "fast":
                params["speed"] = 1.4

        return params

    def _detect_emotion(self, text: str) -> str | None:
        """从文本中检测情感关键词。

        优先匹配中文关键词，其次匹配英文关键词。
        多个情感匹配时，选择出现位置最早的那个。

        Args:
            text: 输入文本

        Returns:
            str | None: 检测到的情感维度名称
        """
        best_pos = len(text) + 1
        best_emotion: str | None = None

        text_lower = text.lower()

        # 检查中文关键词
        for emotion_name, keywords in _EMOTION_KEYWORDS_ZH.items():
            for kw in keywords:
                pos = text.find(kw)
                if pos >= 0 and pos < best_pos:
                    best_pos = pos
                    best_emotion = emotion_name

        # 检查英文关键词
        for emotion_name, keywords in _EMOTION_KEYWORDS_EN.items():
            for kw in keywords:
                pos = text_lower.find(kw)
                if pos >= 0 and pos < best_pos:
                    best_pos = pos
                    best_emotion = emotion_name

        return best_emotion

    def _detect_speed(self, text: str) -> str | None:
        """从文本中检测语速关键词。

        Args:
            text: 输入文本

        Returns:
            str | None: "slow" / "fast" / "normal" / None
        """
        text_lower = text.lower()

        # 优先匹配中文
        for speed, keywords in _SPEED_KEYWORDS_ZH.items():
            for kw in keywords:
                if kw in text:
                    return speed

        # 其次匹配英文
        for speed, keywords in _SPEED_KEYWORDS_EN.items():
            for kw in keywords:
                if kw in text_lower:
                    return speed

        return None

    def _detect_language(self, text: str) -> tuple[str | None, str | None]:
        """从文本中检测语言/方言关键词。

        Args:
            text: 输入文本

        Returns:
            tuple[str | None, str | None]: (language, dialect)
                  方言优先于语言返回（如检测到"粤语"则 dialect="cantonese"）
        """
        text_lower = text.lower()

        # 先检测方言（方言隐含语言）
        for dialect_key, keywords in _LANGUAGE_HINTS_ZH.items():
            if dialect_key in _DIALECT_KEYS:
                for kw in keywords:
                    if kw in text:
                        return "zh", dialect_key

        for dialect_key, keywords in _LANGUAGE_HINTS_EN.items():
            if dialect_key in _DIALECT_KEYS:
                for kw in keywords:
                    if kw in text_lower:
                        return "zh", dialect_key

        # 再检测语言
        _LANG_KEYS = ("zh", "en", "ja", "ko")
        for lang, keywords in _LANGUAGE_HINTS_ZH.items():
            if lang in _LANG_KEYS:
                for kw in keywords:
                    if kw in text:
                        return lang, None

        for lang, keywords in _LANGUAGE_HINTS_EN.items():
            if lang in _LANG_KEYS:
                for kw in keywords:
                    if kw in text_lower:
                        return lang, None

        return None, None
