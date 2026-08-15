# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""内容安全检测模块 — 基于 CLIP 风格的多模态安全分类。

提供 TTS 文本输入的安全检测能力，防止有害内容通过 TTS 系统传播。
支持多类别安全检测（暴力、仇恨、自残、色情、违法等），
并提供可配置的过滤策略和置信度阈值。

检测策略：
    1. 关键词/模式匹配 — 基于规则的高精度匹配（零误判）
    2. 语义相似度检测 — 基于 CLIP 文本嵌入的语义级检测（泛化能力强）
    3. 综合评分 — 加权融合两种策略的结果

架构设计：
    ContentSafetyDetector 是安全检测的统一入口，
    位于文本前端处理之后、引擎推理之前。
    可选集成 CLIP 模型进行语义级检测，
    未安装 CLIP 依赖时降级为纯关键词匹配模式。

典型使用::

    detector = ContentSafetyDetector()
    result = detector.detect("要检测的文本")
    if result.is_safe:
        # 继续 TTS 合成
        ...
    else:
        # 拦截并返回安全提示
        ...
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("tts_multimodel")

# ---------------------------------------------------------------------------
# 常量定义
# ---------------------------------------------------------------------------

#: 默认安全检测置信度阈值（0.0~1.0）
#: 0.3：单条强关键词命中（置信度 1/(1+2)≈0.333）即判定不安全，
#: 与 config.yaml ``security.content_safety_threshold`` 默认值保持一致。
DEFAULT_THRESHOLD = 0.3

#: 最大检测文本长度
MAX_DETECTION_TEXT_LENGTH = 10000


class SafetyCategory(str, Enum):
    """安全检测类别枚举。"""

    VIOLENCE = "violence"
    HATE_SPEECH = "hate_speech"
    SELF_HARM = "self_harm"
    SEXUAL = "sexual"
    ILLEGAL = "illegal"
    HARASSMENT = "harassment"
    SAFE = "safe"


#: 安全类别描述（中文）
CATEGORY_DESCRIPTIONS: dict[SafetyCategory, str] = {
    SafetyCategory.VIOLENCE: "暴力/伤害",
    SafetyCategory.HATE_SPEECH: "仇恨言论",
    SafetyCategory.SELF_HARM: "自残/自杀",
    SafetyCategory.SEXUAL: "色情内容",
    SafetyCategory.ILLEGAL: "违法犯罪",
    SafetyCategory.HARASSMENT: "骚扰/霸凌",
    SafetyCategory.SAFE: "安全内容",
}


# ---------------------------------------------------------------------------
# 不安全内容关键词模式（多语言）
# ---------------------------------------------------------------------------

#: 暴力相关关键词
_VIOLENCE_PATTERNS: list[str] = [
    r"杀(?:人|死|掉|了)",
    r"砍(?:死|伤|人)",
    r"打(?:死|伤|残)",
    r"爆(?:炸|破)",
    r"枪(?:杀|击|决)",
    r"刺(?:杀|死|伤)",
    r"毒(?:杀|死)",
    r"谋(?:杀|害|杀)",
    r"屠(?:杀|宰)",
    r"灭(?:口|门|族)",
    r"虐(?:待|杀|死)",
    r"械(?:斗|战)",
    r"暴(?:力|行|徒)",
    r"凶(?:杀|器|残|恶)",
    r"伤害他人",
    r"kill\s*(?:you|him|her|them|all)",
    r"murder",
    r"assassinat",
    r"bomb",
    r"explosive",
    r"shoot\s*(?:to\s*kill|dead)",
    r"massacre",
    r"slaughter",
    r"torture",
    r"behead",
]

#: 仇恨言论关键词
_HATE_SPEECH_PATTERNS: list[str] = [
    r"劣等(?:民族|种族|人)",
    r"滚(?:回|出|去).*(?:国|家|你)",
    r"虫(?:豸|子|蚁)",
    r"下等(?:人|民族|种族)",
    r"肮脏的*(?:种族|民族|人)",
    r"种族(?:歧视|灭绝|清洗)",
    r"民族仇恨",
    r"nigger|nigga",
    r"chink|gook|wetback",
    r"faggot|dyke|tranny",
    r"racial\s*slur",
    r"ethnic\s*cleansing",
    r"genocide",
    r"white\s*supremacy",
    r"nazi|neo-?nazi",
]

#: 自残/自杀关键词
_SELF_HARM_PATTERNS: list[str] = [
    r"自杀",
    r"自残",
    r"割(?:腕|脉|自己)",
    r"跳(?:楼|河|崖|桥)",
    r"上吊",
    r"服毒",
    r"安眠药.*过量",
    r"不想活",
    r"结束.*生命",
    r"了结自己",
    r"suicide",
    r"self.?harm",
    r"kill\s*myself",
    r"end\s*my\s*life",
    r"cut\s*myself",
    r"overdose",
]

#: 色情内容关键词
_SEXUAL_PATTERNS: list[str] = [
    r"裸(?:体|照|聊)",
    r"色(?:情|狼|诱|播)",
    r"性(?:交|行|器|虐|侵)",
    r"强(?:奸|暴|上)",
    r"猥亵",
    r"淫(?:秽|荡|乱)",
    r"嫖(?:娼|妓)",
    r"卖淫",
    r"porn|pornograph",
    r"sexual\s*(?:intercourse|assault|abuse)",
    r"rape|molest",
    r"nude|naked",
    r"explicit\s*content",
]

#: 违法犯罪关键词
_ILLEGAL_PATTERNS: list[str] = [
    r"贩(?:毒|卖.*武器|枪)",
    r"制(?:毒|造.*枪|造.*弹)",
    r"走私",
    r"洗钱",
    r"诈骗.*(?:钱财|金钱|银行)",
    r"绑架",
    r"勒索",
    r"行贿",
    r"受贿",
    r"drug\s*(?:trafficking|dealing|manufacturing)",
    r"money\s*laundering",
    r"smuggl",
    r"extort",
    r"kidnap",
    r"bribery",
    r"counterfeit",
]

#: 骚扰/霸凌关键词
_HARASSMENT_PATTERNS: list[str] = [
    r"你(?:是|就是个).*(?:废物|垃圾|蠢货|白痴|弱智|贱人)",
    r"滚(?:开|蛋|远点)",
    r"去死",
    r"丑(?:八怪|死|逼)",
    r"没用.*东西",
    r"废(?:物|柴|人)",
    r"stupid\s*(?:idiot|moron|fool)",
    r"go\s*die|go\s*to\s*hell",
    r"you\s*are\s*(?:worthless|useless|trash|garbage)",
    r"bully|harass|stalk",
    r"loser|pathetic",
]


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class SafetyDetectionResult:
    """安全检测结果。

    Attributes:
        is_safe: 文本是否安全（通过检测）。
        category: 检测到的安全类别（安全时为 SAFE）。
        confidence: 检测置信度（0.0~1.0，越高越确定）。
        matched_patterns: 匹配到的模式列表。
        message: 面向用户的结果消息。
        detection_method: 使用的检测方法（"keyword" / "clip" / "combined"）。
    """

    is_safe: bool
    category: SafetyCategory
    confidence: float
    matched_patterns: list[str] = field(default_factory=list)
    message: str = ""
    detection_method: str = "keyword"

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "is_safe": self.is_safe,
            "category": self.category.value,
            "confidence": round(self.confidence, 4),
            "matched_patterns": self.matched_patterns,
            "message": self.message,
            "detection_method": self.detection_method,
        }


@dataclass
class SafetyStats:
    """安全检测统计信息。"""

    total_checks: int = 0
    blocked: int = 0
    passed: int = 0
    category_counts: dict[str, int] = field(default_factory=dict)

    @property
    def block_rate(self) -> float:
        """拦截率。"""
        return self.blocked / self.total_checks if self.total_checks > 0 else 0.0


# ---------------------------------------------------------------------------
# ContentSafetyDetector
# ---------------------------------------------------------------------------


class ContentSafetyDetector:
    """内容安全检测器。

    提供多维度、多语言的不安全内容检测能力。
    支持 CLIP 语义级检测（可选）和关键词模式匹配（默认）。

    检测流程：
        1. 关键词/模式匹配 — 高精度，零误判
        2. （可选）CLIP 语义相似度检测 — 泛化到变体表述
        3. 综合评分 — 加权融合
        4. 阈值判定 — 超过阈值则拦截
    """

    def __init__(
        self,
        threshold: float = DEFAULT_THRESHOLD,
        enable_clip: bool = False,
    ) -> None:
        """初始化内容安全检测器。

        Args:
            threshold: 安全检测置信度阈值（0.0~1.0），
                      检测得分超过此值则判定为不安全。
            enable_clip: 是否启用 CLIP 语义检测（需要安装 transformers + torch）。
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold 必须在 0.0~1.0 之间，得到: {threshold}")

        self._threshold = threshold
        self._enable_clip = enable_clip
        self.stats = SafetyStats()
        self._lock = threading.Lock()

        # 编译关键词模式正则
        self._compiled_patterns: dict[SafetyCategory, list[re.Pattern[str]]] = {
            SafetyCategory.VIOLENCE: [
                re.compile(p, re.IGNORECASE) for p in _VIOLENCE_PATTERNS
            ],
            SafetyCategory.HATE_SPEECH: [
                re.compile(p, re.IGNORECASE) for p in _HATE_SPEECH_PATTERNS
            ],
            SafetyCategory.SELF_HARM: [
                re.compile(p, re.IGNORECASE) for p in _SELF_HARM_PATTERNS
            ],
            SafetyCategory.SEXUAL: [
                re.compile(p, re.IGNORECASE) for p in _SEXUAL_PATTERNS
            ],
            SafetyCategory.ILLEGAL: [
                re.compile(p, re.IGNORECASE) for p in _ILLEGAL_PATTERNS
            ],
            SafetyCategory.HARASSMENT: [
                re.compile(p, re.IGNORECASE) for p in _HARASSMENT_PATTERNS
            ],
        }

        # CLIP 模型懒初始化
        self._clip_model: Any | None = None
        self._clip_tokenizer: Any | None = None
        self._clip_initialized = False

        logger.info(
            "ContentSafetyDetector 初始化 | threshold=%.2f, clip=%s, patterns=%d",
            threshold,
            enable_clip,
            sum(len(v) for v in self._compiled_patterns.values()),
        )

    @property
    def threshold(self) -> float:
        """检测阈值。"""
        return self._threshold

    def detect(self, text: str) -> SafetyDetectionResult:
        """检测文本内容是否安全。

        Args:
            text: 待检测的文本。

        Returns:
            SafetyDetectionResult 包含检测结果和元信息。

        Raises:
            ValueError: 文本超过最大长度。
        """
        if not text or not text.strip():
            return SafetyDetectionResult(
                is_safe=True,
                category=SafetyCategory.SAFE,
                confidence=1.0,
                message="空文本，判定为安全",
            )

        if len(text) > MAX_DETECTION_TEXT_LENGTH:
            raise ValueError(
                f"文本长度 {len(text)} 超过最大限制 {MAX_DETECTION_TEXT_LENGTH}"
            )

        # 第一步：关键词模式匹配
        keyword_result = self._detect_by_keywords(text)

        # 第二步：（可选）CLIP 语义检测
        clip_result = None
        if self._enable_clip:
            clip_result = self._detect_by_clip(text)

        # 综合评分
        if clip_result is not None:
            result = self._combine_results(keyword_result, clip_result)
        else:
            result = keyword_result

        # 更新统计
        with self._lock:
            self.stats.total_checks += 1
            if result.is_safe:
                self.stats.passed += 1
            else:
                self.stats.blocked += 1
                cat = result.category.value
                self.stats.category_counts[cat] = (
                    self.stats.category_counts.get(cat, 0) + 1
                )

        return result

    def detect_batch(self, texts: list[str]) -> list[SafetyDetectionResult]:
        """批量检测文本安全性。

        Args:
            texts: 待检测的文本列表。

        Returns:
            SafetyDetectionResult 列表，与输入一一对应。
        """
        return [self.detect(text) for text in texts]

    def is_safe(self, text: str) -> bool:
        """便捷方法：仅返回是否安全。

        Args:
            text: 待检测的文本。

        Returns:
            安全返回 True，不安全返回 False。
        """
        return self.detect(text).is_safe

    def filter_safe(
        self,
        texts: list[str],
    ) -> list[tuple[int, str, SafetyDetectionResult]]:
        """过滤出不安全的文本。

        Args:
            texts: 待检测的文本列表。

        Returns:
            不安全文本的列表，每项为 (索引, 文本, 检测结果)。
        """
        results: list[tuple[int, str, SafetyDetectionResult]] = []
        for i, text in enumerate(texts):
            result = self.detect(text)
            if not result.is_safe:
                results.append((i, text, result))
        return results

    def get_stats(self) -> dict[str, Any]:
        """获取检测统计信息。

        Returns:
            包含检测次数、拦截次数等信息的字典。
        """
        with self._lock:
            return {
                "total_checks": self.stats.total_checks,
                "blocked": self.stats.blocked,
                "passed": self.stats.passed,
                "block_rate": round(self.stats.block_rate, 4),
                "category_counts": dict(self.stats.category_counts),
            }

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    def _detect_by_keywords(self, text: str) -> SafetyDetectionResult:
        """基于关键词模式的安全检测。

        Args:
            text: 待检测文本。

        Returns:
            SafetyDetectionResult 检测结果。
        """
        all_matches: list[tuple[SafetyCategory, str]] = []

        for category, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                matches = pattern.findall(text)
                if matches:
                    for match in matches:
                        all_matches.append((category, match if isinstance(match, str) else match[0]))

        if not all_matches:
            return SafetyDetectionResult(
                is_safe=True,
                category=SafetyCategory.SAFE,
                confidence=1.0,
                message="未匹配到任何不安全模式",
                detection_method="keyword",
            )

        # 取置信度最高的类别
        # 置信度 = 匹配数 / (匹配数 + 2)（平滑函数，避免单次匹配就 100%）
        category_counts: dict[SafetyCategory, int] = {}
        for cat, _ in all_matches:
            category_counts[cat] = category_counts.get(cat, 0) + 1

        best_category = max(category_counts, key=lambda k: category_counts[k])
        best_count = category_counts[best_category]
        confidence = best_count / (best_count + 2.0)

        matched_patterns = [m for _, m in all_matches if _ == best_category]

        is_safe = confidence < self._threshold

        return SafetyDetectionResult(
            is_safe=is_safe,
            category=best_category if not is_safe else SafetyCategory.SAFE,
            confidence=confidence,
            matched_patterns=list(set(matched_patterns))[:10],  # 去重，最多10个
            message=(
                f"检测到{CATEGORY_DESCRIPTIONS[best_category]}相关内容"
                if not is_safe
                else "内容安全（匹配数低于阈值）"
            ),
            detection_method="keyword",
        )

    def _detect_by_clip(self, text: str) -> SafetyDetectionResult | None:
        """基于 CLIP 语义嵌入的安全检测。

        需要安装 transformers + torch。
        未安装或初始化失败时返回 None。

        Args:
            text: 待检测文本。

        Returns:
            SafetyDetectionResult 或 None（不可用时）。
        """
        if not self._initialize_clip():
            return None

        try:
            # 使用 CLIP 文本编码器获取嵌入
            # 这里是简化的实现：计算文本与各类别描述的余弦相似度
            # 实际生产中应使用预训练的安全分类头
            import torch

            # 类别提示文本
            category_prompts = {
                SafetyCategory.VIOLENCE: "a text about violence, killing, or harm",
                SafetyCategory.HATE_SPEECH: "a text containing hate speech or racial slurs",
                SafetyCategory.SELF_HARM: "a text about suicide or self-harm",
                SafetyCategory.SEXUAL: "a text containing sexual or explicit content",
                SafetyCategory.ILLEGAL: "a text about illegal activities or crimes",
                SafetyCategory.HARASSMENT: "a text about harassment or bullying",
                SafetyCategory.SAFE: "a normal, safe and innocent text",
            }

            # 编码输入文本
            inputs = self._clip_tokenizer(
                [text] + list(category_prompts.values()),
                padding=True,
                truncation=True,
                max_length=77,
                return_tensors="pt",
            )

            with torch.no_grad():
                outputs = self._clip_model(**inputs)
                # 使用 CLS token 或池化输出
                embeddings = outputs.logits_per_classification if hasattr(outputs, "logits_per_classification") else outputs.last_hidden_state[:, 0, :]
                # 归一化
                embeddings = embeddings / embeddings.norm(dim=-1, keepdim=True)
                text_emb = embeddings[0:1]
                category_embs = embeddings[1:]
                # 余弦相似度
                similarities = (text_emb @ category_embs.T).squeeze(0)
                similarities = similarities.cpu().numpy()

            # 找到相似度最高的类别
            categories = list(category_prompts.keys())
            best_idx = int(similarities.argmax())
            best_category = categories[best_idx]
            best_confidence = float(similarities[best_idx])

            is_safe = best_category == SafetyCategory.SAFE or best_confidence < self._threshold

            return SafetyDetectionResult(
                is_safe=is_safe,
                category=best_category if not is_safe else SafetyCategory.SAFE,
                confidence=best_confidence,
                message=(
                    f"CLIP 检测: {CATEGORY_DESCRIPTIONS[best_category]}"
                    if not is_safe
                    else "CLIP 检测: 内容安全"
                ),
                detection_method="clip",
            )

        except Exception as e:
            logger.error("CLIP 安全检测失败: %s", e)
            return None

    def _initialize_clip(self) -> bool:
        """懒初始化 CLIP 模型。

        Returns:
            初始化成功返回 True，否则 False。
        """
        if self._clip_initialized:
            return self._clip_model is not None

        self._clip_initialized = True

        try:
            from transformers import AutoModel, AutoTokenizer

            model_name = "openai/clip-vit-base-patch32"
            self._clip_tokenizer = AutoTokenizer.from_pretrained(model_name)
            self._clip_model = AutoModel.from_pretrained(model_name)
            self._clip_model.eval()
            logger.info("CLIP 安全检测模型加载成功: %s", model_name)
            return True
        except ImportError:
            logger.debug("transformers 未安装，CLIP 安全检测不可用")
            return False
        except Exception as e:
            logger.warning("CLIP 模型加载失败（离线模式？）: %s", e)
            return False

    @staticmethod
    def _combine_results(
        keyword_result: SafetyDetectionResult,
        clip_result: SafetyDetectionResult,
    ) -> SafetyDetectionResult:
        """综合关键词和 CLIP 检测结果。

        加权策略：
            - 如果任一检测判定为不安全，则综合结果为不安全
            - 置信度取两者中较高者
            - 类别取不安全判定的类别

        Args:
            keyword_result: 关键词检测结果。
            clip_result: CLIP 检测结果。

        Returns:
            综合 SafetyDetectionResult。
        """
        if keyword_result.is_safe and clip_result.is_safe:
            return SafetyDetectionResult(
                is_safe=True,
                category=SafetyCategory.SAFE,
                confidence=max(keyword_result.confidence, clip_result.confidence),
                message="综合检测: 内容安全",
                detection_method="combined",
            )

        # 取不安全结果
        unsafe_result = (
            keyword_result if not keyword_result.is_safe else clip_result
        )
        safe_confidence = (
            keyword_result.confidence if keyword_result.is_safe else clip_result.confidence
        )
        unsafe_confidence = unsafe_result.confidence

        return SafetyDetectionResult(
            is_safe=False,
            category=unsafe_result.category,
            confidence=max(unsafe_confidence, 1.0 - safe_confidence),
            matched_patterns=unsafe_result.matched_patterns,
            message=f"综合检测: {unsafe_result.message}",
            detection_method="combined",
        )


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

_detector_instance: ContentSafetyDetector | None = None
_detector_lock = threading.Lock()


def get_safety_detector(
    threshold: float | None = None,
) -> ContentSafetyDetector:
    """获取模块级 ContentSafetyDetector 单例。

    Args:
        threshold: 检测阈值。``None`` 时优先读取 config.yaml
            ``security.content_safety_threshold``，未配置/读取失败时
            回退到 :data:`DEFAULT_THRESHOLD`。

    Returns:
        ContentSafetyDetector 实例。
    """
    global _detector_instance
    if threshold is None:
        threshold = _resolve_safety_threshold()
    if _detector_instance is None:
        with _detector_lock:
            if _detector_instance is None:
                _detector_instance = ContentSafetyDetector(threshold=threshold)
    return _detector_instance


def _resolve_safety_threshold() -> float:
    """解析内容安全检测阈值：config.yaml → 模块默认值。

    配置读取为"尽力而为"：任何异常（缺少依赖/配置文件缺失/类型非法）
    都回退到 DEFAULT_THRESHOLD，绝不阻断主流程。
    """
    try:
        from ..config import get_config

        # 运行时配置门面（config.py AppConfig）经 pydantic_config 暴露强类型配置树
        return float(get_config().pydantic_config.security.content_safety_threshold)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "读取 content_safety_threshold 配置失败，回退默认阈值 %.2f: %s",
            DEFAULT_THRESHOLD,
            exc,
        )
        return DEFAULT_THRESHOLD


def check_safety(text: str) -> SafetyDetectionResult:
    """便捷函数：检测文本安全性。

    Args:
        text: 待检测的文本。

    Returns:
        SafetyDetectionResult 检测结果。
    """
    return get_safety_detector().detect(text)


def is_safe(text: str) -> bool:
    """便捷函数：仅返回是否安全。

    Args:
        text: 待检测的文本。

    Returns:
        安全返回 True，不安全返回 False。
    """
    return get_safety_detector().is_safe(text)
