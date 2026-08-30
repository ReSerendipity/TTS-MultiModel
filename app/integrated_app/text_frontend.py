"""多语言文本前端处理模块

提供语言检测、文本规范化、G2P 桩实现及统一门面接口，
支持 zh/en/ja/ko 四种主要语言。

类层次:
    LanguageDetector  -- 基于 CJK Unicode 范围和拉丁字符启发式语言检测
    TextNormalizer    -- 数字/日期/符号/缩写展开（按语言规则）
    G2PProcessor      -- Grapheme-to-Phoneme 桩（预留未来 G2P 集成）
    TextFrontend      -- 门面，组合上述三个组件
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .config import to_lang_code
from .exceptions import ContentSafetyError
from .security.content_safety import check_safety

logger = logging.getLogger("tts_multimodel")

# ---------------------------------------------------------------------------
# 常量定义
# ---------------------------------------------------------------------------

# 支持的语言代码
SUPPORTED_LANGUAGES = ("zh", "en", "ja", "ko")

# 默认语言（检测失败时回退）
DEFAULT_LANGUAGE = "zh"

# ---------------------------------------------------------------------------
# Unicode 范围常量（用于语言检测）
# ---------------------------------------------------------------------------

# CJK 统一汉字（中日韩共用）
_CJK_RANGES = [
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x20000, 0x2A6DF),  # CJK Extension B
    (0x2A700, 0x2B73F),  # CJK Extension C
    (0x2B740, 0x2B81F),  # CJK Extension D
    (0x2B820, 0x2CEAF),  # CJK Extension E
    (0x2CEB0, 0x2EBEF),  # CJK Extension F
    (0x30000, 0x3134A),  # CJK Extension G
    (0x31350, 0x323AF),  # CJK Extension H
]

# 日文平假名
_HIRAGANA_RANGE = (0x3040, 0x309F)

# 日文片假名
_KATAKANA_RANGE = (0x30A0, 0x30FF)

# 日文假名扩展
_KATAKANA_EXT_RANGE = (0x31F0, 0x31FF)

# 韩文 Hangul 音节
_HANGUL_SYLLABLES_RANGE = (0xAC00, 0xD7AF)

# 韩文 Jamo
_HANGUL_JAMO_RANGE = (0x1100, 0x11FF)
_HANGUL_COMPAT_JAMO_RANGE = (0x3130, 0x318F)

# 拉丁字母
_LATIN_RANGES = [
    (0x0041, 0x005A),  # 大写 A-Z
    (0x0061, 0x007A),  # 小写 a-z
    (0x00C0, 0x024F),  # 拉丁扩展
]

# 中文标点范围（用于判断中文语境）
_ZH_PUNCTUATION = set("，。！？、；：''【】《》（）—…·")

# 日文标点
_JA_PUNCTUATION = set("。「」、・『』【】")

# 韩文标点
_KO_PUNCTUATION = set("，。！？、；：")

# ---------------------------------------------------------------------------
# 中文数字映射
# ---------------------------------------------------------------------------

_ZH_DIGITS = {
    "0": "零",
    "1": "一",
    "2": "二",
    "3": "三",
    "4": "四",
    "5": "五",
    "6": "六",
    "7": "七",
    "8": "八",
    "9": "九",
}

_ZH_UNITS = [
    ("", ""),
    ("十", "十"),
    ("百", "百"),
    ("千", "千"),
    ("万", "万"),
]

# 大单位映射
_ZH_BIG_UNITS = {2: "万", 4: "亿", 6: "兆"}

# ---------------------------------------------------------------------------
# 英文缩写展开
# ---------------------------------------------------------------------------

_EN_ABBREVIATIONS = {
    "Mr.": "Mister",
    "Mrs.": "Misses",
    "Ms.": "Miss",
    "Dr.": "Doctor",
    "Prof.": "Professor",
    "Sr.": "Senior",
    "Jr.": "Junior",
    "St.": "Street",
    "Ave.": "Avenue",
    "Blvd.": "Boulevard",
    "U.S.A.": "United States of America",
    "U.S.": "United States",
    "U.K.": "United Kingdom",
    "E.U.": "European Union",
    "Inc.": "Incorporated",
    "Ltd.": "Limited",
    "Corp.": "Corporation",
    "Co.": "Company",
    "Dept.": "Department",
    "Assn.": "Association",
    "No.": "Number",
    "Vol.": "Volume",
    "vs.": "versus",
    "etc.": "et cetera",
    "i.e.": "that is",
    "e.g.": "for example",
    "approx.": "approximately",
    "apt.": "apartment",
    "dept.": "department",
    "est.": "established",
    "govt.": "government",
    "mgt.": "management",
    "min.": "minimum",
    "max.": "maximum",
    "temp.": "temperature",
    "wk.": "week",
    "yr.": "year",
    "hr.": "hour",
    "sec.": "second",
    "min": "minute",
    "oz.": "ounce",
    "lb.": "pound",
    "ft.": "feet",
    "in.": "inches",
    "km": "kilometer",
    "cm": "centimeter",
    "mm": "millimeter",
    "kg": "kilogram",
    "mg": "milligram",
}

# 英文符号展开
_EN_SYMBOL_MAP = {
    "&": "and",
    "@": "at",
    "%": "percent",
    "#": "number",
    "$": "dollar",
    "+": "plus",
    "=": "equals",
    "<": "less than",
    ">": "greater than",
    "~": "approximately",
    "*": "star",
    "/": "slash",
}

# 中文符号展开
_ZH_SYMBOL_MAP = {
    "&": "和",
    "%": "百分之",
    "@": "艾特",
    "#": "号",
    "+": "加",
    "=": "等于",
    "<": "小于",
    ">": "大于",
    "*": "星",
    "/": "斜杠",
}

# TTS 控制标签（需要保留，不被清理）
_TTS_CONTROL_TAGS = {
    "[uv_break]",
    "[laugh]",
    "[break]",
    "[breath]",
    "[pause]",
    "[uv_break_0.2]",
    "[uv_break_0.3]",
    "[uv_break_0.5]",
    "[emphasis]",
    "[whisper]",
    "[speed_up]",
    "[speed_down]",
    "[volume_up]",
    "[volume_down]",
    "[pitch_up]",
    "[pitch_down]",
}

# Emoji Unicode 范围（覆盖主要表情符号区域）
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001f600-\U0001f64f"  # 表情符号
    "\U0001f300-\U0001f5ff"  # 符号与象形文字
    "\U0001f680-\U0001f6ff"  # 交通与地图符号
    "\U0001f1e0-\U0001f1ff"  # 国旗
    "\U00002500-\U00002bef"  # 杂项符号
    "\U00002700-\U000027bf"  # 装饰符号
    "\U0001f900-\U0001f9ff"  # 补充符号与象形文字
    "\U0001fa00-\U0001fa6f"  # 棋类符号
    "\U0001fa70-\U0001faff"  # 符号与象形文字扩展-A
    "\U00002600-\U000026ff"  # 杂项符号
    "\U0000fe00-\U0000fe0f"  # 变体选择符
    "\U0000200d"  # 零宽连接符
    "\U00002300-\U000023ff"  # 杂项技术
    "\U00002b50"  # 星星
    "]+",
    flags=re.UNICODE,
)


# ---------------------------------------------------------------------------
# LanguageDetector
# ---------------------------------------------------------------------------


@dataclass
class LanguageDetectionResult:
    """语言检测结果

    Attributes:
        language: 检测到的语言代码 (zh/en/ja/ko)
        confidence: 置信度 (0.0~1.0)
        char_counts: 各语言字符计数
    """

    language: str
    confidence: float
    char_counts: dict[str, int] = field(default_factory=dict)


class LanguageDetector:
    """基于字符启发式的语言检测器

    通过统计 CJK Unicode 范围、假名、韩文字符和拉丁字符的占比来判断文本语言。
    对于中日文共用的 CJK 汉字，根据假名比例进一步区分。
    支持混合语言文本，返回主要语言。

    性能优化：使用预编译正则表达式在 C 层面批量统计字符，避免 Python 级逐字符循环。
    """

    def __init__(self) -> None:
        # 预编译完整正则（覆盖所有 CJK 范围，C 引擎批量匹配比 Python 循环快 10-50 倍）
        cjk_pattern_parts = [
            r"\u4e00-\u9fff",
            r"\u3400-\u4dbf",
            r"\U00020000-\U0002a6df",
            r"\U0002a700-\U0002b73f",
            r"\U0002b740-\U0002b81f",
            r"\U0002b820-\U0002ceaf",
            r"\U0002ceb0-\U0002ebef",
            r"\U00030000-\U0003134a",
            r"\U00031350-\U000323af",
        ]
        self._re_cjk = re.compile(f"[{''.join(cjk_pattern_parts)}]")
        self._re_hiragana = re.compile(r"[\u3040-\u309f]")
        self._re_katakana = re.compile(r"[\u30a0-\u30ff\u31f0-\u31ff]")
        self._re_kana = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u31f0-\u31ff]")
        self._re_hangul = re.compile(r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")
        self._re_latin = re.compile(r"[A-Za-z\u00c0-\u024f]")
        self._re_digit = re.compile(r"[0-9]")
        self._re_non_space = re.compile(r"\S")
        # 预编译标点正则（同样使用 C 引擎批量计数）
        zh_punct_str = "".join(re.escape(c) for c in _ZH_PUNCTUATION)
        ja_punct_str = "".join(re.escape(c) for c in _JA_PUNCTUATION)
        ko_punct_str = "".join(re.escape(c) for c in _KO_PUNCTUATION)
        self._re_zh_punct = re.compile(f"[{zh_punct_str}]")
        self._re_ja_punct = re.compile(f"[{ja_punct_str}]")
        self._re_ko_punct = re.compile(f"[{ko_punct_str}]")

    def detect(self, text: str) -> LanguageDetectionResult:
        """检测输入文本的主要语言

        使用正则 findall 在 C 层面批量统计字符数，相比 Python 逐字符循环提升 10-50x。

        Args:
            text: 输入文本

        Returns:
            LanguageDetectionResult 包含语言代码、置信度和字符计数
        """
        if not text or not text.strip():
            return LanguageDetectionResult(language=DEFAULT_LANGUAGE, confidence=0.0)

        # 使用 C 级正则批量统计（findall 返回匹配列表，len() 即为计数）
        kana_count = len(self._re_kana.findall(text))
        ko_count = len(self._re_hangul.findall(text))
        cjk_count = len(self._re_cjk.findall(text))
        latin_count = len(self._re_latin.findall(text))
        total_non_space = len(self._re_non_space.findall(text))

        if total_non_space == 0:
            return LanguageDetectionResult(language=DEFAULT_LANGUAGE, confidence=0.0)

        # 将 CJK 汉字在中日之间分配
        ja_cjk = 0
        zh_cjk = cjk_count
        if kana_count > 0 and cjk_count > 0:
            ja_ratio = kana_count / (kana_count + cjk_count)
            ja_cjk = int(cjk_count * ja_ratio)
            zh_cjk = cjk_count - ja_cjk

        lang_counts = {
            "zh": zh_cjk,
            "ja": kana_count + ja_cjk,
            "ko": ko_count,
            "en": latin_count,
        }

        max_lang = max(lang_counts, key=lambda k: lang_counts[k])
        max_count = lang_counts[max_lang]
        non_other_total = sum(lang_counts.values())

        confidence = 0.0 if non_other_total == 0 else max_count / non_other_total

        # 检查标点辅助判断（仅用于置信度低时的增强）
        if confidence < 0.5:
            zh_punct_count = len(self._re_zh_punct.findall(text))
            ja_punct_count = len(self._re_ja_punct.findall(text))
            ko_punct_count = len(self._re_ko_punct.findall(text))

            if ja_punct_count > zh_punct_count and ja_punct_count > ko_punct_count and max_lang == "zh":
                max_lang = "ja"
                confidence = max(confidence, 0.5)

        result = LanguageDetectionResult(
            language=max_lang,
            confidence=round(confidence, 4),
            char_counts=lang_counts,
        )

        logger.debug(
            "语言检测结果: lang=%s, confidence=%.2f, counts=%s",
            result.language,
            result.confidence,
            result.char_counts,
        )
        return result

    def detect_language(self, text: str) -> str:
        """便捷方法：返回检测到的语言代码

        Args:
            text: 输入文本

        Returns:
            语言代码字符串 (zh/en/ja/ko)
        """
        result = self.detect(text)
        return result.language

    # -- 私有辅助方法 --

    @staticmethod
    def _is_cjk(cp: int) -> bool:
        """判断码点是否在 CJK 统一汉字范围内"""
        return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)

    @staticmethod
    def _is_hiragana(cp: int) -> bool:
        """判断 Unicode 码点是否为日文平假名

        Args:
            cp: Unicode 码点值

        Returns:
            如果是平假名返回 True，否则返回 False
        """
        lo, hi = _HIRAGANA_RANGE
        return lo <= cp <= hi

    @staticmethod
    def _is_katakana(cp: int) -> bool:
        """判断 Unicode 码点是否为日文片假名（含扩展）

        Args:
            cp: Unicode 码点值

        Returns:
            如果是片假名或片假名扩展返回 True，否则返回 False
        """
        lo1, hi1 = _KATAKANA_RANGE
        lo2, hi2 = _KATAKANA_EXT_RANGE
        return (lo1 <= cp <= hi1) or (lo2 <= cp <= hi2)

    @staticmethod
    def _is_hangul(cp: int) -> bool:
        """判断 Unicode 码点是否为韩文（音节、Jamo、兼容 Jamo）

        Args:
            cp: Unicode 码点值

        Returns:
            如果是韩文字符返回 True，否则返回 False
        """
        return (
            _HANGUL_SYLLABLES_RANGE[0] <= cp <= _HANGUL_SYLLABLES_RANGE[1]
            or _HANGUL_JAMO_RANGE[0] <= cp <= _HANGUL_JAMO_RANGE[1]
            or _HANGUL_COMPAT_JAMO_RANGE[0] <= cp <= _HANGUL_COMPAT_JAMO_RANGE[1]
        )

    @staticmethod
    def _is_latin(cp: int) -> bool:
        """判断 Unicode 码点是否为拉丁字母（含扩展）

        Args:
            cp: Unicode 码点值

        Returns:
            如果是拉丁字母返回 True，否则返回 False
        """
        return any(lo <= cp <= hi for lo, hi in _LATIN_RANGES)


# ---------------------------------------------------------------------------
# TextNormalizer
# ---------------------------------------------------------------------------


class TextNormalizer:
    """多语言文本规范化器

    支持：
      - 数字展开（123 -> "一百二十三" / "one hundred twenty three"）
      - 日期/时间展开（2024年3月15日 -> "二零二四年三月十五日"）
      - 符号展开（&, %, @ -> 全词等价）
      - 缩写处理（U.S.A., Dr. 等）
      - 每语言独立规范化规则
    """

    def __init__(self) -> None:
        self._re_zh_date = re.compile(
            r"(\d{4}|\d{2})年((0?[1-9]|1[0-2])月)?"
            r"(((0?[1-9])|((1|2)[0-9])|30|31)([日号]))?"
        )
        self._re_zh_time = re.compile(r"([0-1]?[0-9]|2[0-3]):([0-5][0-9])(:([0-5][0-9]))?")
        self._re_zh_time_range = re.compile(
            r"([0-1]?[0-9]|2[0-3]):([0-5][0-9])(:([0-5][0-9]))?"
            r"[-~]([0-1]?[0-9]|2[0-3]):([0-5][0-9])(:([0-5][0-9]))?"
        )
        self._re_zh_percentage = re.compile(r"(-?)(\d+(\.\d+)?)%")
        self._re_zh_number = re.compile(r"(-?)((\d+)(\.\d+)?)|(\.(\d+))")
        self._re_zh_positive_quantifier = re.compile(
            # 整数部分必须连带可选小数（\.\d+）一起吞掉。
            # 此前只写 (\d+)，遇到「3.5元」时量词分支只匹配到「5元」，
            # 展开成「五元」后把「3.」留在原地 -> 输出「3.五元」，
            # 小数点被读成静音、数值语义被破坏。
            r"(\d+(?:\.\d+)?)([多余几])?"
            r"(处|台|架|枚|趟|幅|平|方|堵|间|床|株|批|项|例|列|篇|栋|注|亩|封|艘|把|目|套|段|人|所|朵|匹|张|座|回|场|尾|条|个|首|阙|阵|网|炮|顶|丘|棵|只|支|袭|辆|挑|担|颗|壳|曲|墙|群|腔|砣|客|贯|扎|捆|刀|令|打|手|罗|坡|山|岭|江|溪|钟|队|单|双|对|出|口|头|脚|板|跳|枝|件|贴|针|线|管|名|位|身|堂|课|本|页|家|户|层|丝|毫|厘|分|钱|两|斤|担|铢|石|钧|锱|忽|克|米|升|斗|年|月|日|季|刻|时|周|天|秒|分|小时|旬|纪|岁|世|更|夜|春|夏|秋|冬|代|伏|辈|丸|泡|粒|颗|幢|堆|条|根|支|道|面|片|张|颗|块|元|吨|角|毛|度|倍|次|步|枪|弹|篇|章|节|册|卷|期|届|轮|组|班|排|连|营|团|军|师|旅|舰|机|部|省|市|县|区|镇|村|路|街|楼|号|线|站|区|期|段|级|类|种|款|项|名|位|套|间|栋|层|户)"
        )
        self._re_zh_frac = re.compile(r"(-?)(\d+)/(\d+)")
        self._re_zh_version = re.compile(r"((\d+)(\.\d+)(\.\d+)?(\.\d+)+)")
        self._re_zh_decimal = re.compile(r"(-?)((\d+)(\.\d+))")

        # 英文正则
        self._re_en_time = re.compile(r"\b([01]?[0-9]|2[0-3]):([0-5][0-9])\b")
        self._re_en_comma_number = re.compile(r"([0-9][0-9,]+[0-9])")
        self._re_en_decimal = re.compile(r"([0-9]+\.\s*[0-9]+)")
        self._re_en_ordinal = re.compile(r"[0-9]+(st|nd|rd|th)")
        self._re_en_number = re.compile(r"\b\d+\b")
        self._re_en_year = re.compile(r"\b(\d{4})\b")

        # 全角转半角
        self._re_fullwidth_digit = re.compile(r"[\uff10-\uff19]")
        self._re_fullwidth_alpha = re.compile(r"[\uff21-\uff3a\uff41-\uff5a]")

        # Markdown 清理正则（按优先级排序）
        self._re_md_code_block = re.compile(r"```[\s\S]*?```", re.MULTILINE)
        self._re_md_inline_code = re.compile(r"`[^`\n]+`")
        self._re_md_image = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
        self._re_md_link = re.compile(r"\[([^\]]+)\]\([^)]+\)")
        self._re_md_html_tag = re.compile(r"<[^>]+>")
        self._re_md_heading = re.compile(r"^#{1,6}\s+", re.MULTILINE)
        self._re_md_bold = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
        self._re_md_italic = re.compile(r"\*([^*]+)\*|_([^_]+)_")
        self._re_md_strikethrough = re.compile(r"~~([^~]+)~~")
        self._re_md_horizontal_rule = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)
        self._re_md_blockquote = re.compile(r"^>\s?", re.MULTILINE)
        self._re_md_list = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)
        self._re_md_ordered_list = re.compile(r"^[\s]*\d+\.\s+", re.MULTILINE)
        self._re_md_table_sep = re.compile(r"\|?\s*[-:]+\s*\|", re.MULTILINE)
        self._re_md_table_pipe = re.compile(r"\s*\|\s*")
        self._re_tts_tag = re.compile(
            r"\[(uv_break|laugh|break|breath|pause|emphasis|whisper|"
            r"speed_up|speed_down|volume_up|volume_down|pitch_up|pitch_down)"
            r"(_\d+\.?\d*)?\]"
        )

    def clean_markdown_emoji(self, text: str) -> str:
        """清理 Markdown 格式和 Emoji 字符（保留 TTS 控制标签）

        参考 Fish Speech 和 VoiceBox 的文本预处理设计：
        - 移除代码块、图片、HTML 标签
        - 保留链接文字内容
        - 移除 Markdown 格式符号（粗体、斜体、标题等）
        - 移除 Emoji 表情
        - 保留 TTS 控制标签（[uv_break], [laugh] 等）

        Args:
            text: 输入文本

        Returns:
            清理后的纯文本
        """
        if not text:
            return text

        # 步骤 0：保护 TTS 控制标签（替换为占位符）
        tts_tag_placeholders: dict[str, str] = {}
        placeholder_idx = 0

        def _protect_tts_tag(match: re.Match) -> str:
            nonlocal placeholder_idx
            tag = match.group(0)
            placeholder = f"__TTS_TAG_{placeholder_idx}__"
            tts_tag_placeholders[placeholder] = tag
            placeholder_idx += 1
            return placeholder

        text = self._re_tts_tag.sub(_protect_tts_tag, text)

        # 步骤 1：移除代码块（```...```）
        text = self._re_md_code_block.sub(" ", text)

        # 步骤 2：移除行内代码（`...`），保留内容
        text = self._re_md_inline_code.sub(lambda m: " " + m.group(0)[1:-1] + " ", text)

        # 步骤 3：处理图片 - 保留 alt 文本
        text = self._re_md_image.sub(lambda m: " " + (m.group(1) or "") + " ", text)

        # 步骤 4：处理链接 - 保留链接文字
        text = self._re_md_link.sub(lambda m: " " + m.group(1) + " ", text)

        # 步骤 5：移除 HTML 标签
        text = self._re_md_html_tag.sub(" ", text)

        # 步骤 6：移除标题标记
        text = self._re_md_heading.sub("", text)

        # 步骤 7：移除粗体/斜体标记，保留内容
        text = self._re_md_bold.sub(lambda m: m.group(1) or m.group(2) or "", text)
        text = self._re_md_italic.sub(lambda m: m.group(1) or m.group(2) or "", text)

        # 步骤 8：移除删除线标记，保留内容
        text = self._re_md_strikethrough.sub(lambda m: m.group(1), text)

        # 步骤 9：移除水平分割线
        text = self._re_md_horizontal_rule.sub(" ", text)

        # 步骤 10：移除引用标记
        text = self._re_md_blockquote.sub("", text)

        # 步骤 11：移除列表标记
        text = self._re_md_list.sub("", text)
        text = self._re_md_ordered_list.sub("", text)

        # 步骤 12：处理表格 - 移除分隔符和管道符
        text = self._re_md_table_sep.sub(" ", text)
        text = self._re_md_table_pipe.sub(" ", text)

        # 步骤 13：移除 Emoji 字符
        text = _EMOJI_PATTERN.sub(" ", text)

        # 步骤 14：清理剩余的 Markdown 符号残留
        text = re.sub(r"[*_~`#>|]", " ", text)

        # 步骤 15：恢复 TTS 控制标签
        for placeholder, tag in tts_tag_placeholders.items():
            text = text.replace(placeholder, tag)

        # 步骤 16：清理多余空白（保留换行）
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        return text

    def normalize_punctuation(self, text: str, lang: str) -> str:
        """规范化标点符号

        统一中英文标点，处理重复标点，确保 TTS 正确停顿。

        Args:
            text: 输入文本
            lang: 语言代码 (zh/en/ja/ko)

        Returns:
            标点规范化后的文本
        """
        if not text:
            return text

        if lang == "zh":
            # 中文语境：将英文标点转为中文标点（特殊情况除外）
            punct_map = {
                ",": "，",
                ".": "。",
                "?": "？",
                "!": "！",
                ":": "：",
                ";": "；",
                "(": "（",
                ")": "）",
            }
            # 但保留数字中的小数点和时间中的冒号
            result = []
            i = 0
            while i < len(text):
                ch = text[i]
                if ch in punct_map:
                    # 检查是否是数字中的小数点或时间冒号
                    if ch == "." and i > 0 and i < len(text) - 1:
                        prev_char = text[i - 1]
                        next_char = text[i + 1]
                        if prev_char.isdigit() and next_char.isdigit():
                            result.append(ch)
                            i += 1
                            continue
                    if ch == ":" and i > 0 and i < len(text) - 1:
                        prev_char = text[i - 1]
                        next_char = text[i + 1]
                        if prev_char.isdigit() and next_char.isdigit():
                            result.append(ch)
                            i += 1
                            continue
                    result.append(punct_map[ch])
                else:
                    result.append(ch)
                i += 1
            text = "".join(result)

        # 处理重复标点：多个连续相同标点只保留一个
        text = re.sub(r"([。！？，、；：])\1+", r"\1", text)
        # 句末标点规范化
        text = re.sub(r"[。.！!？?]+$", lambda m: m.group(0)[-1], text)

        return text

    def _normalize_zh_homophones(self, text: str) -> str:
        """中文同音字/易错读字词替换

        替换 TTS 容易读错的字词，提升发音准确性。
        """
        # 常见易错读字词映射
        homophone_map = {
            "嗯": "恩",
            "呐": "那",
            "诶": "哎",
            "喔": "哦",
            "嘘": "虚",
            "呗": "吧",
            "哒": "达",
            "噻": "塞",
            "嘞": "了",
            "咯": "了",
            "咋": "怎么",
            "啥": "什么",
            "咋个": "怎么",
            "为啥": "为什么",
        }
        for wrong, right in homophone_map.items():
            text = text.replace(wrong, right)
        return text

    def normalize(self, text: str, lang: str) -> str:
        """按指定语言规则规范化文本

        处理流程：
          1. 清理 Markdown/Emoji
          2. 全角转半角
          3. 标点规范化
          4. 语言特定规范化（数字、日期等）
          5. 中文同音字替换

        Args:
            text: 输入文本
            lang: 语言代码 (zh/en/ja/ko)

        Returns:
            规范化后的文本
        """
        if not text:
            return text

        # UI 语言下拉提交的是中文显示名（见 config._LANGS），必须先归一成 ISO 代码，
        # 否则下面的分支永远匹配不上，整段语言特定规范化被静默跳过。
        lang = to_lang_code(lang)

        # 步骤 1：清理 Markdown 和 Emoji
        text = self.clean_markdown_emoji(text)

        # 步骤 2：通用预处理：全角转半角
        text = self._fullwidth_to_halfwidth(text)

        # 步骤 3：标点规范化
        text = self.normalize_punctuation(text, lang)

        # 步骤 4：语言特定规范化
        if lang == "zh":
            text = self._normalize_zh(text)
            # 步骤 5：中文同音字替换
            text = self._normalize_zh_homophones(text)
            return text
        elif lang == "en":
            return self._normalize_en(text)
        elif lang == "ja":
            return self._normalize_ja(text)
        elif lang == "ko":
            return self._normalize_ko(text)
        elif lang == "auto":
            # 「自动检测」= 不强制按某一语言做数字/日期等改写，交给模型自身判定。
            # 用 debug 而非 warning：这是合法选项，不是异常输入。
            logger.debug("语言为 auto，跳过语言特定规范化")
            return text
        else:
            # de / fr / ru / pt / es / it 等暂无专用规范化实现，属已知能力缺口，
            # 不是调用方传错，因此不得按 warning 级别刷屏。
            logger.debug("语言 '%s' 暂无专用规范化实现，按原样返回", lang)
            return text

    # -- 中文规范化 --

    def _normalize_zh(self, text: str) -> str:
        """中文文本规范化

        处理顺序：日期 -> 时间 -> 百分比 -> 分数 -> 版本号 -> 数字+量词 -> 小数 -> 整数 -> 符号
        """
        # 日期展开
        text = self._re_zh_date.sub(self._replace_zh_date, text)

        # 时间范围
        text = self._re_zh_time_range.sub(self._replace_zh_time_range, text)
        # 时间展开
        text = self._re_zh_time.sub(self._replace_zh_time, text)

        # 百分比
        text = self._re_zh_percentage.sub(self._replace_zh_percentage, text)

        # 分数
        text = self._re_zh_frac.sub(self._replace_zh_frac, text)

        # 版本号（如 1.2.3.4）
        text = self._re_zh_version.sub(self._replace_zh_version, text)

        # 数字+量词（必须在纯数字之前处理）
        text = self._re_zh_positive_quantifier.sub(self._replace_zh_quantifier, text)

        # 小数
        text = self._re_zh_decimal.sub(self._replace_zh_decimal, text)

        # 纯整数（需要避免误处理已展开的中文数字）
        text = re.compile(r"(?<![\u4e00-\u9fff])(\d+)(?![\u4e00-\u9fff\d.])").sub(self._replace_zh_number, text)

        # 符号展开
        text = self._expand_zh_symbols(text)

        return text

    def _replace_zh_date(self, match: re.Match) -> str:
        """日期展开：2024年3月15日 -> 二零二四年三月十五日"""
        year = match.group(1) or ""
        month = match.group(3) or ""
        day = match.group(5) or ""
        day_suffix = match.group(9) or "日"

        result = ""
        if year:
            result += self._verbalize_digit(year) + "年"
        if month:
            result += self._verbalize_cardinal(month) + "月"
        if day:
            result += self._verbalize_cardinal(day) + day_suffix
        return result

    def _replace_zh_time(self, match: re.Match) -> str:
        """时间展开：14:30 -> 十四点三十分钟"""
        hour = match.group(1)
        minute = match.group(2)
        second = match.group(4)

        result = self._verbalize_cardinal(hour) + "点"
        if minute and int(minute) != 0:
            if int(minute) == 30:
                result += "半"
            else:
                result += self._time_num2str(minute) + "分"
        if second and int(second) != 0:
            result += self._time_num2str(second) + "秒"
        return result

    def _replace_zh_time_range(self, match: re.Match) -> str:
        """时间范围展开：8:30-12:30 -> 八点三十分至十二点三十分"""
        h1, m1 = match.group(1), match.group(2)
        s1 = match.group(4)
        h2, m2 = match.group(6), match.group(7)
        s2 = match.group(9)

        result = self._verbalize_cardinal(h1) + "点"
        if m1 and int(m1) != 0:
            if int(m1) == 30:
                result += "半"
            else:
                result += self._time_num2str(m1) + "分"
        if s1 and int(s1) != 0:
            result += self._time_num2str(s1) + "秒"

        result += "至"

        result += self._verbalize_cardinal(h2) + "点"
        if m2 and int(m2) != 0:
            if int(m2) == 30:
                result += "半"
            else:
                result += self._time_num2str(m2) + "分"
        if s2 and int(s2) != 0:
            result += self._time_num2str(s2) + "秒"

        return result

    def _replace_zh_percentage(self, match: re.Match) -> str:
        """百分比展开：50% -> 百分之五十"""
        sign = "负" if match.group(1) else ""
        percent = match.group(2)
        return f"{sign}百分之{self._verbalize_cardinal(percent)}"

    def _replace_zh_frac(self, match: re.Match) -> str:
        """分数展开：3/4 -> 四分之三"""
        sign = "负" if match.group(1) else ""
        nominator = self._verbalize_cardinal(match.group(2))
        denominator = self._verbalize_cardinal(match.group(3))
        return f"{sign}{denominator}分之{nominator}"

    def _replace_zh_version(self, match: re.Match) -> str:
        """版本号展开：1.2.3 -> 一点二点三"""
        version_str = match.group(1)
        result = ""
        for ch in version_str:
            if ch == ".":
                result += "点"
            else:
                result += _ZH_DIGITS.get(ch, ch)
        return result

    def _replace_zh_quantifier(self, match: re.Match) -> str:
        """数字+量词展开：3个人 -> 三个人，3.5元 -> 三点五元"""
        number = match.group(1)
        modifier = match.group(2) or ""
        quantifier = match.group(3)
        num_str = self._verbalize_maybe_decimal(number)
        # "二" 在量词前通常读 "两"
        if num_str == "二":
            num_str = "两"
        return f"{num_str}{modifier}{quantifier}"

    def _replace_zh_decimal(self, match: re.Match) -> str:
        """小数展开：3.14 -> 三点一四"""
        sign = "负" if match.group(1) else ""
        integer_part = match.group(3)
        decimal_part = match.group(4)
        result = sign + self._verbalize_cardinal(integer_part)
        for ch in decimal_part:
            if ch == ".":
                result += "点"
            else:
                result += _ZH_DIGITS.get(ch, ch)
        return result

    def _replace_zh_number(self, match: re.Match) -> str:
        """纯数字展开"""
        number = match.group(0)
        return self._verbalize_cardinal(number)

    def _expand_zh_symbols(self, text: str) -> str:
        """展开中文语境中的符号"""
        for symbol, replacement in _ZH_SYMBOL_MAP.items():
            text = text.replace(symbol, replacement)
        return text

    # -- 中文数字核心方法 --

    @staticmethod
    def _verbalize_digit(num_str: str) -> str:
        """逐位读出数字（如年份、电话号码等）

        Args:
            num_str: 数字字符串

        Returns:
            中文逐位读法（"2024" -> "二零二四"）
        """
        return "".join(_ZH_DIGITS.get(ch, ch) for ch in num_str)

    @staticmethod
    def _verbalize_maybe_decimal(num_str: str) -> str:
        """按需展开小数：``"3"`` -> ``"三"``，``"3.5"`` -> ``"三点五"``。

        WHY 不直接复用 ``_verbalize_cardinal``：它对无法 ``int()`` 的输入退化成
        ``_verbalize_digit``，而后者对 ``"."`` 没有映射（``_ZH_DIGITS.get(ch, ch)``
        原样返回点号），于是 ``"3.5"`` 会得到 ``"三.五"`` 而不是 ``"三点五"``。

        Args:
            num_str: 十进制数字字符串，可含一个小数点。

        Returns:
            str: 中文读法。
        """
        if "." not in num_str:
            return TextNormalizer._verbalize_cardinal(num_str)
        int_part: str
        frac_part: str
        int_part, _, frac_part = num_str.partition(".")
        head: str = TextNormalizer._verbalize_cardinal(int_part) if int_part else ""
        return f"{head}点{TextNormalizer._verbalize_digit(frac_part)}"

    @staticmethod
    def _verbalize_cardinal(num_str: str) -> str:
        """将数字字符串转为中文基数词

        Args:
            num_str: 数字字符串（如 "123"）

        Returns:
            中文基数词（如 "一百二十三"）
        """
        try:
            num = int(num_str)
        except (ValueError, TypeError):
            # 如果无法转为整数，逐位读取
            return TextNormalizer._verbalize_digit(num_str)

        if num == 0:
            return "零"

        is_negative = num < 0
        num = abs(num)

        result = ""
        # 处理亿级别
        if num >= 100000000:
            yi_part = num // 100000000
            result += TextNormalizer._verbalize_cardinal(str(yi_part)) + "亿"
            num %= 100000000

        # 处理万级别
        if num >= 10000:
            wan_part = num // 10000
            result += TextNormalizer._verbalize_cardinal(str(wan_part)) + "万"
            num %= 10000

        # 处理千级别
        if num >= 1000:
            qian_part = num // 1000
            result += _ZH_DIGITS[str(qian_part)] + "千"
            num %= 1000
        elif result and num > 0:
            # 万级之后不足千需要补零
            result += "零"

        # 处理百级别
        if num >= 100:
            bai_part = num // 100
            result += _ZH_DIGITS[str(bai_part)] + "百"
            num %= 100
        elif result and num > 0:
            result += "零"

        # 处理十级别
        if num >= 10:
            shi_part = num // 10
            # 如果是 10~19 且没有更高级别，"一十" 简化为 "十"
            if shi_part == 1 and not result:
                result += "十"
            else:
                result += _ZH_DIGITS[str(shi_part)] + "十"
            num %= 10
        elif result and num > 0:
            # 百级之后不足十需要补零（但避免连续零）
            if not result.endswith("零"):
                result += "零"

        # 处理个位
        if num > 0:
            result += _ZH_DIGITS[str(num)]

        if is_negative:
            result = "负" + result

        return result

    @staticmethod
    def _time_num2str(num_str: str) -> str:
        """时间中的数字转中文

        "05" -> "零五", "30" -> "三十"
        """
        num_str = num_str.lstrip("0")
        if not num_str:
            return "零"
        # 保留前导零的情况
        if len(num_str) == 1 and len(num_str) < len(num_str):
            return "零" + TextNormalizer._verbalize_cardinal(num_str)
        return TextNormalizer._verbalize_cardinal(num_str)

    # -- 英文规范化 --

    def _normalize_en(self, text: str) -> str:
        """英文文本规范化

        处理顺序：缩写 -> 符号 -> 时间 -> 序数词 -> 数字
        """
        # 缩写展开（先处理长的，避免子串匹配）
        text = self._expand_en_abbreviations(text)

        # 符号展开
        text = self._expand_en_symbols(text)

        # 时间展开
        text = self._re_en_time.sub(self._replace_en_time, text)

        # 序数词展开
        text = self._re_en_ordinal.sub(self._replace_en_ordinal, text)

        # 逗号分隔的大数字（去掉逗号，后续由数字展开处理）
        text = self._re_en_comma_number.sub(lambda m: m.group(1).replace(",", ""), text)

        # 小数展开
        text = self._re_en_decimal.sub(self._replace_en_decimal, text)

        # 纯数字展开
        text = self._re_en_number.sub(self._replace_en_number, text)

        return text

    def _expand_en_abbreviations(self, text: str) -> str:
        """展开英文缩写（如 Mr. -> Mister, U.S.A. -> United States of America）

        Args:
            text: 输入文本

        Returns:
            缩写展开后的文本
        """
        # 按长度降序排列以优先匹配较长的缩写
        for abbr in sorted(_EN_ABBREVIATIONS, key=len, reverse=True):
            # 大小写不敏感匹配，但保留边界检查
            pattern = re.compile(re.escape(abbr), re.IGNORECASE)
            text = pattern.sub(_EN_ABBREVIATIONS[abbr], text)
        return text

    def _expand_en_symbols(self, text: str) -> str:
        """展开英文语境中的符号（如 & -> and, % -> percent）

        Args:
            text: 输入文本

        Returns:
            符号展开后的文本
        """
        for symbol, replacement in _EN_SYMBOL_MAP.items():
            text = text.replace(symbol, f" {replacement} ")
        return text

    def _replace_en_time(self, match: re.Match) -> str:
        """时间展开：14:30 -> two thirty p.m."""
        hours = int(match.group(1))
        minutes = int(match.group(2))

        period = "a.m." if hours < 12 else "p.m."
        if hours > 12:
            hours -= 12
        elif hours == 0:
            hours = 12

        hour_word = self._number_to_en_word(hours)
        if minutes == 0:
            return f"{hour_word} o'clock {period}"
        elif minutes < 10:
            minute_word = f"oh {self._number_to_en_word(minutes)}"
            return f"{hour_word} {minute_word} {period}"
        else:
            minute_word = self._number_to_en_word(minutes)
            return f"{hour_word} {minute_word} {period}"

    def _replace_en_ordinal(self, match: re.Match) -> str:
        """序数词展开：1st -> first, 2nd -> second"""
        num_str = match.group(0)
        for s in ("st", "nd", "rd", "th"):
            if num_str.lower().endswith(s):
                num_str = num_str[: -len(s)]
                break

        try:
            num = int(num_str)
        except ValueError:
            return match.group(0)

        # 基本序数词映射
        ordinals = {
            1: "first",
            2: "second",
            3: "third",
            4: "fourth",
            5: "fifth",
            6: "sixth",
            7: "seventh",
            8: "eighth",
            9: "ninth",
            10: "tenth",
            11: "eleventh",
            12: "twelfth",
            13: "thirteenth",
        }

        if num in ordinals:
            return ordinals[num]

        # 规则生成
        if num % 100 in (11, 12, 13):
            return f"{self._number_to_en_word(num)}th"

        last_digit = num % 10
        if last_digit == 1:
            suffix_word = "first"
        elif last_digit == 2:
            suffix_word = "second"
        elif last_digit == 3:
            suffix_word = "third"
        else:
            suffix_word = "th"

        if num < 100:
            return f"{self._number_to_en_word(num)}{suffix_word}"
        else:
            return f"{self._number_to_en_word(num)} {suffix_word}"

    def _replace_en_decimal(self, match: re.Match) -> str:
        """小数展开：3.14 -> three point one four"""
        decimal_str = match.group(0).replace(" ", "")
        parts = decimal_str.split(".")
        if len(parts) != 2:
            return decimal_str

        integer_part = self._number_to_en_word(int(parts[0]))
        decimal_digits = " ".join(self._digit_to_en_word(ch) for ch in parts[1] if ch.isdigit())
        return f"{integer_part} point {decimal_digits}"

    def _replace_en_number(self, match: re.Match) -> str:
        """纯数字展开"""
        num_str = match.group(0)
        try:
            num = int(num_str)
        except ValueError:
            return num_str

        # 年份特殊处理（1000-2999 之间读法）
        if 1000 <= num <= 2999 and len(num_str) == 4:
            return self._year_to_en(num)

        return self._number_to_en_word(num)

    @staticmethod
    def _digit_to_en_word(digit: str) -> str:
        """单个数字字符转英文单词

        Args:
            digit: 单个数字字符 (0-9)

        Returns:
            对应的英文单词（如 "0" -> "zero"）
        """
        digit_words = {
            "0": "zero",
            "1": "one",
            "2": "two",
            "3": "three",
            "4": "four",
            "5": "five",
            "6": "six",
            "7": "seven",
            "8": "eight",
            "9": "nine",
        }
        return digit_words.get(digit, digit)

    @staticmethod
    def _number_to_en_word(num: int) -> str:
        """整数转英文单词

        支持 0 ~ 999,999,999,999 范围
        """
        if num == 0:
            return "zero"

        is_negative = num < 0
        num = abs(num)

        ones = [
            "",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        ]
        tens = [
            "",
            "",
            "twenty",
            "thirty",
            "forty",
            "fifty",
            "sixty",
            "seventy",
            "eighty",
            "ninety",
        ]

        def _convert(n: int) -> str:
            if n < 20:
                return ones[n]
            elif n < 100:
                t = tens[n // 10]
                r = n % 10
                return f"{t} {ones[r]}" if r else t
            elif n < 1000:
                h = ones[n // 100]
                r = n % 100
                return f"{h} hundred {_convert(r)}" if r else f"{h} hundred"
            elif n < 1_000_000:
                t = _convert(n // 1000)
                r = n % 1000
                return f"{t} thousand {_convert(r)}" if r else f"{t} thousand"
            elif n < 1_000_000_000:
                t = _convert(n // 1_000_000)
                r = n % 1_000_000
                return f"{t} million {_convert(r)}" if r else f"{t} million"
            else:
                t = _convert(n // 1_000_000_000)
                r = n % 1_000_000_000
                return f"{t} billion {_convert(r)}" if r else f"{t} billion"

        result = _convert(num).strip()
        # 清理多余空格
        result = re.sub(r"\s+", " ", result)

        if is_negative:
            result = f"minus {result}"
        return result

    @staticmethod
    def _year_to_en(year: int) -> str:
        """年份读法

        2000 -> "two thousand"
        2024 -> "twenty twenty four"
        1999 -> "nineteen ninety nine"
        """
        if year % 1000 == 0:
            return TextNormalizer._number_to_en_word(year)

        if 2000 <= year <= 2099:
            if year % 100 == 0:
                return "two thousand"
            elif year % 100 < 10:
                return f"two thousand and {TextNormalizer._number_to_en_word(year % 100)}"
            else:
                return f"twenty {TextNormalizer._number_to_en_word(year % 100)}"
        else:
            # 两位两位读
            upper = year // 100
            lower = year % 100
            upper_str = TextNormalizer._number_to_en_word(upper)
            if lower == 0:
                return upper_str
            lower_str = TextNormalizer._number_to_en_word(lower)
            return f"{upper_str} {lower_str}"

    # -- 日文规范化 --

    def _normalize_ja(self, text: str) -> str:
        """日文文本规范化

        基本处理：全角转半角、数字逐位读取（日文数字读法依赖上下文，此处做基础处理）
        """
        # 符号展开（日文语境使用中文类似的符号映射）
        text = self._expand_zh_symbols(text)

        # 日文语境中的数字：逐位展开（日文数字读法依赖语境，此处使用基础音读）
        text = re.compile(r"\d+").sub(self._replace_ja_number, text)

        # 日期格式（日文使用年/月/日标记）
        text = self._re_zh_date.sub(self._replace_ja_date, text)

        return text

    def _replace_ja_number(self, match: re.Match) -> str:
        """日文数字逐位展开

        简化处理：将数字逐位转为日文音读数字。
        注意：日文数字读法高度依赖上下文（如日期、计数等），
        此处仅做基础逐位展开，精确读法需依赖未来 G2P 引擎。

        Args:
            match: 正则匹配对象，group(0) 为数字字符串

        Returns:
            逐位展开后的日语音读数字字符串
        """
        num_str = match.group(0)
        # 日文音读数字映射
        ja_digits = {
            "0": "ゼロ",
            "1": "いち",
            "2": "に",
            "3": "さん",
            "4": "よん",
            "5": "ご",
            "6": "ろく",
            "7": "なな",
            "8": "はち",
            "9": "きゅう",
        }
        return "".join(ja_digits.get(ch, ch) for ch in num_str)

    def _replace_ja_date(self, match: re.Match) -> str:
        """日文日期展开

        日文日期中数字通常使用和音读法，此处做简化逐位展开。
        精确读法（如月份的特殊读法）留待 G2P 引擎处理。

        Args:
            match: 正则匹配对象，包含年/月/日分组

        Returns:
            逐位展开后的日文日期字符串
        """
        year = match.group(1) or ""
        month = match.group(3) or ""
        day = match.group(5) or ""

        ja_digits = {
            "0": "ゼロ",
            "1": "いち",
            "2": "に",
            "3": "さん",
            "4": "よん",
            "5": "ご",
            "6": "ろく",
            "7": "なな",
            "8": "はち",
            "9": "きゅう",
        }

        result = ""
        if year:
            result += "".join(ja_digits.get(ch, ch) for ch in year) + "年"
        if month:
            result += "".join(ja_digits.get(ch, ch) for ch in month) + "月"
        if day:
            result += "".join(ja_digits.get(ch, ch) for ch in day) + "日"
        return result

    # -- 韩文规范化 --

    def _normalize_ko(self, text: str) -> str:
        """韩文文本规范化

        基本处理：数字转韩文读法
        """
        # 符号展开
        text = self._expand_ko_symbols(text)

        # 数字展开
        text = re.compile(r"\d+").sub(self._replace_ko_number, text)

        return text

    def _replace_ko_number(self, match: re.Match) -> str:
        """韩文数字展开

        使用韩文汉字音读数字系统（일, 이, 삼...），
        对于 1-99 使用韩文固有数字（하나, 둘, 셋...），
        对于 100 以上使用汉字音读系统。

        注意：韩文数字读法依赖语境，此处为简化规则处理。

        Args:
            match: 正则匹配对象，group(0) 为数字字符串

        Returns:
            韩文读法的数字字符串
        """
        num_str = match.group(0)
        try:
            num = int(num_str)
        except ValueError:
            return num_str

        if num == 0:
            return "영"

        # 韩文固有数字（1-99）
        ko_native_units = {
            1: "한",
            2: "두",
            3: "세",
            4: "네",
            5: "다섯",
            6: "여섯",
            7: "일곱",
            8: "여덟",
            9: "아홉",
            10: "열",
            20: "스물",
            30: "서른",
            40: "마흔",
            50: "쉰",
            60: "예순",
            70: "일흔",
            80: "여든",
            90: "아흔",
        }

        # 1-99 使用固有数字
        if 1 <= num <= 99:
            tens = (num // 10) * 10
            ones = num % 10
            if tens == 0:
                return ko_native_units.get(num, str(num))
            result = ko_native_units.get(tens, "")
            if ones > 0:
                result += ko_native_units.get(ones, str(ones))
            return result

        # 100+ 使用汉字音读系统
        ko_digits = {
            0: "영",
            1: "일",
            2: "이",
            3: "삼",
            4: "사",
            5: "오",
            6: "육",
            7: "칠",
            8: "팔",
            9: "구",
        }
        ko_units_small = ["", "십", "백", "천"]
        ko_units_big = ["", "만", "억", "조"]

        result = ""
        big_index = 0
        remaining = num
        while remaining > 0:
            segment = remaining % 10000
            if segment > 0:
                segment_str = ""
                for i, digit in enumerate(str(segment).zfill(4)):
                    d = int(digit)
                    if d > 0:
                        # 十位上的 1 可以省略（십 而不是 일십）
                        if d == 1 and (3 - i) == 1 and segment < 100:
                            segment_str += ko_units_small[3 - i]
                        else:
                            segment_str += ko_digits[d] + ko_units_small[3 - i]
                if big_index > 0:
                    segment_str += ko_units_big[big_index]
                result = segment_str + result
            remaining //= 10000
            big_index += 1

        return result if result else ko_digits[0]

    def _expand_ko_symbols(self, text: str) -> str:
        """展开韩文语境中的符号（如 & -> 그리고, % -> 퍼센트）

        Args:
            text: 输入文本

        Returns:
            符号展开后的文本
        """
        ko_symbol_map = {
            "&": "그리고",
            "%": "퍼센트",
            "@": "엣",
            "#": "번호",
            "+": "더하기",
            "=": "같다",
        }
        for symbol, replacement in ko_symbol_map.items():
            text = text.replace(symbol, replacement)
        return text

    # -- 通用工具方法 --

    @staticmethod
    def _fullwidth_to_halfwidth(text: str) -> str:
        """全角字符转半角字符

        全角数字和英文字母转为半角，便于后续正则匹配。
        """
        result = []
        for ch in text:
            cp = ord(ch)
            # 全角数字 ０-９ (FF10-FF19) -> 0-9
            if 0xFF10 <= cp <= 0xFF19:
                result.append(chr(cp - 0xFF10 + 0x30))
            # 全角大写 Ａ-Ｚ (FF21-FF3A) -> A-Z
            elif 0xFF21 <= cp <= 0xFF3A:
                result.append(chr(cp - 0xFF21 + 0x41))
            # 全角小写 ａ-ｚ (FF41-FF5A) -> a-z
            elif 0xFF41 <= cp <= 0xFF5A:
                result.append(chr(cp - 0xFF41 + 0x61))
            # 全角空格
            elif cp == 0x3000:
                result.append(" ")
            else:
                result.append(ch)
        return "".join(result)


# ---------------------------------------------------------------------------
# G2PProcessor
# ---------------------------------------------------------------------------


class G2PProcessor:
    """Grapheme-to-Phoneme 处理器

    提供统一的 G2P 处理接口，通过 G2PManager 委托给具体 G2P 后端。
    支持 pypinyin（中文多音字消歧）、g2p_en（英文）、pyopenjtalk（日文）、
    g2pk2（韩文）。所有后端均为可选依赖，未安装时优雅降级为透传模式。

    设计参考主流 TTS 文本前端：
      - chinese.py  使用 pypinyin + jieba + cn2an
      - english.py  使用 g2p_en + CMU dict
      - japanese.py 使用 pyopenjtalk
      - korean.py   使用 g2pk2 + jamo

    内置 LRU 缓存提升重复文本处理速度，缓存命中率通过 stats 属性可查。
    """

    def __init__(self) -> None:
        self._initialized: dict[str, bool] = {}
        # 懒导入 G2PManager，避免循环依赖
        self._manager: Any | None = None

    def _get_manager(self) -> Any:
        """懒获取 G2PManager 实例。

        Returns:
            G2PManager 实例。
        """
        if self._manager is None:
            try:
                from .g2p_manager import get_g2p_manager

                self._manager = get_g2p_manager()
            except ImportError as e:
                logger.warning("G2PManager 导入失败，回退为透传模式: %s", e)
                self._manager = False  # type: ignore[assignment]
        return self._manager if self._manager is not False else None

    def process(self, text: str, lang: str) -> str:
        """对文本执行 G2P 处理

        通过 G2PManager 委托给具体 G2P 后端。
        后端不可用时优雅降级为透传模式（返回原文）。

        Args:
            text: 规范化后的文本
            lang: 语言代码 (zh/en/ja/ko)

        Returns:
            G2P 处理后的文本（音素序列或带注音文本）
        """
        if not text:
            return text

        manager = self._get_manager()
        if manager is None:
            logger.debug("G2P 透传（manager 不可用）: lang=%s, text='%s'", lang, text[:50])
            return text

        try:
            result = manager.convert_text(text, lang)
            logger.debug(
                "G2P 处理: lang=%s, engine=%s, text='%s' -> '%s'",
                lang,
                manager.get_engine_name(lang),
                text[:50],
                result[:50],
            )
            return result
        except Exception as e:
            logger.error("G2P 处理失败 (lang=%s): %s — 透传原文", lang, e)
            return text

    def is_available(self, lang: str) -> bool:
        """检查指定语言的 G2P 引擎是否可用
        Args:
            lang: 语言代码
        Returns:
            处理能力可用返回 True，否则返回 False。
            受支持语言因透传模式而始终可用（后端缺失时降级为原文）。
        """
        return lang in SUPPORTED_LANGUAGES

    def initialize_engine(self, lang: str) -> bool:
        """初始化指定语言的 G2P 引擎

        G2PManager 采用懒初始化策略，此方法仅记录初始化状态。
        实际引擎在首次 convert 调用时按需加载。

        Args:
            lang: 语言代码

        Returns:
            是否初始化成功
        """
        if lang not in SUPPORTED_LANGUAGES:
            logger.warning("不支持的语言: %s", lang)
            return False

        self._initialized[lang] = True
        logger.debug("G2P 引擎初始化: lang=%s", lang)
        return True


# ---------------------------------------------------------------------------
# TextFrontend（门面类）
# ---------------------------------------------------------------------------


class TextFrontend:
    """多语言文本前端处理门面

    组合 LanguageDetector + TextNormalizer + G2PProcessor，
    提供统一的文本预处理入口。

    使用方式::

        frontend = TextFrontend()
        processed_text, lang = frontend.process("2024年3月15日天气25度")
        # processed_text = "二零二四年三月十五日天气二十五度"
        # lang = "zh"

        # 指定语言
        processed_text, lang = frontend.process("Hello, 123 people", lang="en")
        # processed_text = "Hello, one hundred twenty three people"
        # lang = "en"
    """

    def __init__(self) -> None:
        self._detector = LanguageDetector()
        self._normalizer = TextNormalizer()
        self._g2p = G2PProcessor()

    def process(self, text: str, lang: str | None = None) -> tuple[str, str]:
        """处理输入文本

        流程：
          1. 如果 lang 未指定，自动检测语言
          2. 按语言应用文本规范化
          3. 按语言执行 G2P 处理（当前为桩）

        Args:
            text: 输入文本
            lang: 指定语言代码，如果为 None 则自动检测

        Returns:
            (processed_text, detected_or_specified_lang) 元组
        """
        if not text or not text.strip():
            return (text or "", lang or DEFAULT_LANGUAGE)

        # 步骤 1：语言检测
        if lang is None:
            detection = self._detector.detect(text)
            lang = detection.language
            logger.info(
                "自动检测语言: %s (置信度: %.2f)",
                lang,
                detection.confidence,
            )
        else:
            if lang not in SUPPORTED_LANGUAGES:
                logger.warning(
                    "不支持的语言 '%s'，回退为 '%s'",
                    lang,
                    DEFAULT_LANGUAGE,
                )
                lang = DEFAULT_LANGUAGE

        # 步骤 2：文本规范化
        try:
            normalized_text = self._normalizer.normalize(text, lang)
        except Exception as e:
            logger.error("文本规范化失败 (lang=%s): %s", lang, e)
            normalized_text = text

        # 步骤 3：G2P 处理（桩）
        try:
            processed_text = self._g2p.process(normalized_text, lang)
        except Exception as e:
            logger.error("G2P 处理失败 (lang=%s): %s", lang, e)
            processed_text = normalized_text

        return (processed_text, lang)

    def detect_language(self, text: str) -> LanguageDetectionResult:
        """检测文本语言

        Args:
            text: 输入文本

        Returns:
            LanguageDetectionResult
        """
        return self._detector.detect(text)

    def normalize(self, text: str, lang: str) -> str:
        """仅执行文本规范化（不包含 G2P）

        Args:
            text: 输入文本
            lang: 语言代码

        Returns:
            规范化后的文本
        """
        return self._normalizer.normalize(text, lang)

    @property
    def detector(self) -> LanguageDetector:
        """获取语言检测器实例"""
        return self._detector

    @property
    def normalizer(self) -> TextNormalizer:
        """获取文本规范化器实例"""
        return self._normalizer

    @property
    def g2p_processor(self) -> G2PProcessor:
        """获取 G2P 处理器实例"""
        return self._g2p


# ---------------------------------------------------------------------------
# 模块级便捷函数
# ---------------------------------------------------------------------------

# 模块级单例（懒初始化）
_frontend_instance: TextFrontend | None = None


def get_frontend() -> TextFrontend:
    """获取模块级 TextFrontend 单例

    Returns:
        TextFrontend 实例
    """
    global _frontend_instance
    if _frontend_instance is None:
        _frontend_instance = TextFrontend()
    return _frontend_instance


def process_text(text: str, lang: str | None = None) -> tuple[str, str]:
    """便捷函数：处理文本

    Args:
        text: 输入文本
        lang: 可选语言代码

    Returns:
        (processed_text, lang) 元组
    """
    return get_frontend().process(text, lang)


def detect_language(text: str) -> str:
    """便捷函数：检测语言

    Args:
        text: 输入文本

    Returns:
        语言代码字符串
    """
    return get_frontend().detect_language(text).language


def normalize_text(text: str, lang: str) -> str:
    """便捷函数：规范化文本

    内容安全门禁：IndexTTS2 / VoxCPM2 两族引擎在推理前都会调用本函数，
    未通过安全检测的文本在此抛出 :class:`ContentSafetyError`（HTTP 400 语义），
    由引擎层向上传播，禁止进入合成管线。

    Args:
        text: 输入文本
        lang: 语言代码

    Returns:
        规范化后的文本

    Raises:
        ContentSafetyError: 文本未通过内容安全检测。
    """
    _check_content_safety(text)
    return get_frontend().normalize(text, lang)


def _check_content_safety(text: str) -> None:
    """内容安全门禁：不安全文本抛出 ContentSafetyError。

    检测由 ``security.content_safety`` 模块完成（六类中英关键词正则
    + 可选 CLIP 语义检测），阈值来自 config.yaml
    ``security.content_safety_threshold``（默认 0.3，单条强关键词命中即拦截）。
    """
    if not text or not text.strip():
        return
    result = check_safety(text)
    if not result.is_safe:
        logger.warning(
            "内容安全拦截: category=%s, confidence=%.4f, patterns=%s",
            result.category.value,
            result.confidence,
            result.matched_patterns,
        )
        raise ContentSafetyError(
            f"文本未通过内容安全检测（{result.category.value}，置信度 {result.confidence:.2f}），已拒绝合成。",
            category=result.category.value,
        )
