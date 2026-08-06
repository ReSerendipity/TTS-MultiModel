"""
情感标签系统 - 为情感控制和 RAG 风格的情感指令提供内联情感标签解析。

支持 [happy]、[sad:0.8]、[whisper] 等英文标签，以及中文标签如 [温柔]、[悲伤] 等。
标签可通过 EmotionControlManager 转换为情感向量或 CFG 控制指令。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from utils import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 正则模式
# ---------------------------------------------------------------------------

# 匹配 [tagname] 或 [tagname:intensity] 格式的标签
# 强度为 0.0-1.0 之间的小数
_TAG_PATTERN = re.compile(
    r"\[([a-zA-Z\u4e00-\u9fff]+)(?::([0-9]*\.?[0-9]+))?\]",
)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class EmotionTag:
    """从文本中解析出的单个情感标签。

    Attributes:
        name: 标准化情感名称（英文，小写）。
        intensity: 情感强度 0.0-1.0，默认 1.0。
        raw_text: 标签在原始文本中的字符串（如 "[sad:0.8]"）。
    """

    name: str
    intensity: float = 1.0
    raw_text: str = ""

    def __post_init__(self) -> None:
        self.name = self.name.lower().strip()
        self.intensity = max(0.0, min(1.0, self.intensity))


@dataclass
class EmotionDefinition:
    """情感标签的定义，包含映射信息。

    Attributes:
        name: 标准英文名称（小写）。
        display_name_zh: 中文显示名称。
        emotion_vector: 映射到 8 维情感向量的值（happy, angry, sad, afraid,
            disgusted, melancholic, surprised, calm），每个值 0-1。
        cfg_instruction: 用于 CFG 的情感控制提示词。
        aliases: 替代名称列表（中英文均可）。
        is_prosody: 是否为韵律标签（如 whisper、shout），这类标签
            映射到 cfg_instruction 而非情感向量。
    """

    name: str
    display_name_zh: str
    emotion_vector: dict[str, float] | None = None
    cfg_instruction: str | None = None
    aliases: list[str] = field(default_factory=list)
    is_prosody: bool = False

    def matches(self, tag_name: str) -> bool:
        """检查标签名称是否匹配此情感定义（包括别名）。

        Args:
            tag_name: 要检查的标签名称（不区分大小写）。

        Returns:
            如果匹配则返回 True。
        """
        lower = tag_name.lower().strip()
        if lower == self.name:
            return True
        return lower in [a.lower() for a in self.aliases]


# ---------------------------------------------------------------------------
# 情感标签库
# ---------------------------------------------------------------------------

EMOTION_REGISTRY: dict[str, EmotionDefinition] = {}


def _register(defn: EmotionDefinition) -> None:
    """向全局注册表中注册情感定义。

    Args:
        defn: 要注册的 EmotionDefinition。
    """
    EMOTION_REGISTRY[defn.name] = defn


# --- 基础情感 ---

_register(
    EmotionDefinition(
        name="happy",
        display_name_zh="开心",
        emotion_vector={"happy": 1.0, "calm": 0.3},
        cfg_instruction="cheerful and happy tone, smiling voice, upbeat",
        aliases=["joy", "joyful", "cheerful", "开心", "高兴", "快乐", "喜悦"],
    )
)

_register(
    EmotionDefinition(
        name="sad",
        display_name_zh="悲伤",
        emotion_vector={"sad": 1.0, "melancholic": 0.7},
        cfg_instruction="sad and sorrowful tone, crying voice, melancholic",
        aliases=["sorrow", "sorrowful", "unhappy", "悲伤", "难过", "伤心", "哀伤"],
    )
)

_register(
    EmotionDefinition(
        name="angry",
        display_name_zh="愤怒",
        emotion_vector={"angry": 1.0},
        cfg_instruction="angry and furious tone, raised voice, aggressive",
        aliases=["anger", "furious", "mad", "愤怒", "生气", "恼怒"],
    )
)

_register(
    EmotionDefinition(
        name="afraid",
        display_name_zh="恐惧",
        emotion_vector={"afraid": 1.0, "sad": 0.2},
        cfg_instruction="scared and fearful tone, trembling voice, anxious",
        aliases=["fear", "fearful", "scared", "fearful", "恐惧", "害怕", "惊恐"],
    )
)

_register(
    EmotionDefinition(
        name="surprised",
        display_name_zh="惊讶",
        emotion_vector={"surprised": 1.0},
        cfg_instruction="surprised and astonished tone, excited exclamation",
        aliases=["surprise", "shocked", "astonished", "惊讶", "吃惊", "惊奇"],
    )
)

_register(
    EmotionDefinition(
        name="calm",
        display_name_zh="平静",
        emotion_vector={"calm": 1.0},
        cfg_instruction="calm and neutral tone, peaceful, steady voice",
        aliases=["neutral", "peaceful", "steady", "平静", "冷静", "平和", "淡定"],
    )
)

_register(
    EmotionDefinition(
        name="disgusted",
        display_name_zh="厌恶",
        emotion_vector={"disgusted": 1.0, "angry": 0.3},
        cfg_instruction="disgusted and repulsed tone, contemptuous voice",
        aliases=["disgust", "repulsed", "contempt", "厌恶", "反感", "憎恶"],
    )
)

_register(
    EmotionDefinition(
        name="melancholic",
        display_name_zh="忧郁",
        emotion_vector={"melancholic": 1.0, "sad": 0.5, "calm": 0.2},
        cfg_instruction="melancholic and wistful tone, nostalgic, gentle sadness",
        aliases=["melancholy", "wistful", "nostalgic", "忧郁", "惆怅", "忧伤"],
    )
)


# --- 复杂情感 ---

_register(
    EmotionDefinition(
        name="excited",
        display_name_zh="兴奋",
        emotion_vector={"happy": 0.9, "surprised": 0.6},
        cfg_instruction="excited and enthusiastic tone, energetic, animated",
        aliases=["excitement", "enthusiastic", "energetic", "兴奋", "激动", "热情"],
    )
)

_register(
    EmotionDefinition(
        name="gentle",
        display_name_zh="温柔",
        emotion_vector={"calm": 0.7, "happy": 0.2, "melancholic": 0.1},
        cfg_instruction="gentle and soft tone, warm, tender voice, caring",
        aliases=["soft", "tender", "warm", "kind", "温柔", "柔和", "温和", "亲切"],
    )
)

_register(
    EmotionDefinition(
        name="serious",
        display_name_zh="严肃",
        emotion_vector={"calm": 0.6, "melancholic": 0.2},
        cfg_instruction="serious and solemn tone, formal, grave voice",
        aliases=["solemn", "grave", "formal", "严肃", "庄重", "郑重"],
    )
)

_register(
    EmotionDefinition(
        name="nervous",
        display_name_zh="紧张",
        emotion_vector={"afraid": 0.6, "surprised": 0.3},
        cfg_instruction="nervous and anxious tone, hesitant, shaky voice",
        aliases=["anxious", "tense", "worried", "紧张", "焦虑", "不安"],
    )
)

_register(
    EmotionDefinition(
        name="proud",
        display_name_zh="自豪",
        emotion_vector={"happy": 0.7, "calm": 0.3},
        cfg_instruction="proud and confident tone, dignified, self-assured",
        aliases=["confident", "dignified", "自豪", "骄傲", "自信"],
    )
)

_register(
    EmotionDefinition(
        name="whisper",
        display_name_zh="耳语",
        cfg_instruction="whispering, very quiet voice, breathy, hushed tone, secretive",
        aliases=["whispering", "whispered", "hushed", "耳语", "低声", "悄悄话", "小声"],
        is_prosody=True,
    )
)

_register(
    EmotionDefinition(
        name="shout",
        display_name_zh="大喊",
        cfg_instruction="shouting, loud voice, yelling, raised volume, forceful",
        aliases=["shouting", "yell", "yelling", "loud", "大喊", "大叫", "喊叫", "大声"],
        is_prosody=True,
    )
)

_register(
    EmotionDefinition(
        name="laughing",
        display_name_zh="大笑",
        emotion_vector={"happy": 1.0, "surprised": 0.3},
        cfg_instruction="laughing while speaking, giggling, cheerful laughter in voice",
        aliases=["laugh", "laughter", "giggle", "giggling", "大笑", "笑", "笑着说"],
    )
)

_register(
    EmotionDefinition(
        name="crying",
        display_name_zh="哭泣",
        emotion_vector={"sad": 1.0, "melancholic": 0.5},
        cfg_instruction="crying while speaking, sobbing, tearful voice, choked up",
        aliases=["cry", "sobbing", "tearful", "weep", "哭泣", "哭", "哭着说", "抽泣"],
    )
)

# 中文到英文的反向映射（用于快速查找）
_CHINESE_TO_ENGLISH: dict[str, str] = {}
for _defn in EMOTION_REGISTRY.values():
    for _alias in _defn.aliases:
        if any("\u4e00" <= c <= "\u9fff" for c in _alias):
            _CHINESE_TO_ENGLISH[_alias] = _defn.name


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def parse_tags(text: str) -> tuple[list[EmotionTag], str]:
    """从文本中解析情感标签，返回 (标签列表, 清理后文本)。

    Args:
        text: 可能包含情感标签的输入文本，如
              "[whisper]Hello world" 或 "你好[温柔]世界"

    Returns:
        (解析后的 EmotionTag 列表, 移除标签后的文本) 元组。

    Examples:
        >>> tags, clean = parse_tags("[whisper]Hello [excited:0.8]world")
        >>> [t.name for t in tags]
        ['whisper', 'excited']
        >>> clean
        'Hello world'
    """
    tags = []
    cleaned = text

    for match in _TAG_PATTERN.finditer(text):
        raw_name = match.group(1)
        intensity_str = match.group(2)

        # 中文转英文
        name = _CHINESE_TO_ENGLISH.get(raw_name, raw_name.lower())
        intensity = float(intensity_str) if intensity_str else 1.0
        intensity = max(0.0, min(1.0, intensity))

        tag = EmotionTag(name=name, intensity=intensity, raw_text=match.group(0))
        tags.append(tag)

        if name not in EMOTION_REGISTRY:
            logger.warning(f"未知情感标签: '{raw_name}' -> '{name}'")

    # 从文本中移除标签
    cleaned = _TAG_PATTERN.sub("", text).strip()
    # 合并多个空格
    cleaned = re.sub(r"\s+", " ", cleaned)

    return tags, cleaned


def tags_to_control_instruction(
    tags: list[EmotionTag],
) -> tuple[dict[str, float] | None, str | None]:
    """将情感标签转换为 (情感向量, CFG 控制指令)。

    当多个情感标签存在时，情感向量按强度加权取平均；
    CFG 指令则合并所有标签的指令文本。

    Args:
        tags: parse_tags() 返回的 EmotionTag 列表。

    Returns:
        (emotion_vector, cfg_instruction) 元组。如果无对应映射则值为 None。
        emotion_vector 是 8 维情感字典，cfg_instruction 是 CFG 提示文本。
    """
    emotion_vecs: list[dict[str, float]] = []
    cfg_parts: list[str] = []

    for tag in tags:
        defn = EMOTION_REGISTRY.get(tag.name)
        if defn is None:
            continue

        if defn.emotion_vector:
            weighted = {k: v * tag.intensity for k, v in defn.emotion_vector.items()}
            emotion_vecs.append(weighted)

        if defn.cfg_instruction:
            if tag.intensity < 1.0:
                cfg_parts.append(f"{defn.cfg_instruction} (intensity: {tag.intensity:.1f})")
            else:
                cfg_parts.append(defn.cfg_instruction)

    # 合并情感向量
    merged_vec: dict[str, float] | None = None
    if emotion_vecs:
        all_keys: set[str] = set()
        for v in emotion_vecs:
            all_keys.update(v.keys())
        merged_vec = {}
        for key in all_keys:
            values = [v.get(key, 0.0) for v in emotion_vecs]
            merged_vec[key] = min(1.0, sum(values) / len(emotion_vecs))

    cfg_instruction = ", ".join(cfg_parts) if cfg_parts else None

    return merged_vec, cfg_instruction


def strip_all_tags(text: str) -> str:
    """从文本中移除所有情感标签，返回清理后的文本。

    Args:
        text: 输入文本。

    Returns:
        移除标签后的纯文本。
    """
    return _TAG_PATTERN.sub("", text).strip()


def get_emotion_library() -> list[dict]:
    """获取所有已注册情感标签的列表（用于 UI 展示）。

    Returns:
        情感字典列表，每个字典包含 name、display_name_zh、aliases、is_prosody 字段。
    """
    result = []
    for defn in EMOTION_REGISTRY.values():
        result.append(
            {
                "name": defn.name,
                "display_name_zh": defn.display_name_zh,
                "aliases": defn.aliases,
                "is_prosody": defn.is_prosody,
            }
        )
    return result


def validate_tags(tags: list[EmotionTag]) -> list[str]:
    """验证情感标签，返回未知标签的警告信息列表。

    Args:
        tags: 要验证的 EmotionTag 列表。

    Returns:
        警告信息字符串列表。如果所有标签均已知则返回空列表。
    """
    warnings = []
    for tag in tags:
        if tag.name not in EMOTION_REGISTRY:
            suggestions = [name for name in EMOTION_REGISTRY if name.startswith(tag.name[:2])]
            msg = f"未知情感标签: '{tag.name}'"
            if suggestions:
                msg += f"，您是否想要: {', '.join(suggestions[:3])}?"
            warnings.append(msg)
    return warnings
