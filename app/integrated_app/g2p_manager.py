# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""G2P（Grapheme-to-Phoneme）音素转换管理器。

提供多语言 G2P 音素转换能力，支持中文多音字消歧、英文音素转换、
日文音素转换和韩文音素转换，内置 LRU 缓存以提升重复文本处理速度。

架构设计：
    G2PManager 是文本前端处理流水线中的 G2P 层，位于 TextNormalizer 之后、
    引擎推理之前。它接收规范化后的文本，输出音素序列或带注音的文本，
    供下游 TTS 引擎使用。

核心特性：
    1. 多语言 G2P 引擎管理：按语言加载对应 G2P 后端
       - zh: pypinyin（多音字消歧）+ jieba 分词
       - en: g2p_en（CMU 音素字典）
       - ja: pyopenjtalk（日文音素转换）
       - ko: g2pk2（韩文 G2P）
    2. LRU 缓存：对相同文本段避免重复 G2P 计算
    3. 优雅降级：G2P 后端不可用时回退到原文，不阻塞推理流程
    4. 性能监控：记录缓存命中率、处理时间

依赖策略：
    所有 G2P 后端依赖均为可选（try/except import），
    未安装时自动降级为"透传模式"（返回原文），
    确保服务在最小依赖下仍可启动。

典型使用::

    manager = G2PManager()
    phonemes = manager.convert("你好世界", lang="zh")
    # -> "nǐ hǎo shì jiè"

    # 批量处理
    results = manager.convert_batch(["你好", "世界"], lang="zh")
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("tts_multimodel")

# ---------------------------------------------------------------------------
# 常量定义
# ---------------------------------------------------------------------------

#: 支持的语言列表
SUPPORTED_LANGUAGES: tuple[str, ...] = ("zh", "en", "ja", "ko")

#: 默认语言（检测失败时回退）
DEFAULT_LANGUAGE = "zh"

#: 默认 LRU 缓存大小（条目数）
DEFAULT_CACHE_SIZE = 2048

#: 默认最大单次处理文本长度（字符数）
MAX_TEXT_LENGTH = 10000

# ---------------------------------------------------------------------------
# 可选 G2P 后端依赖加载
# ---------------------------------------------------------------------------

# --- 中文 G2P: pypinyin + jieba ---
try:
    from pypinyin import Style, lazy_pinyin, pinyin
    from pypinyin.contrib.tone_convert import to_normal

    _HAS_PYPINYIN: bool = True
except ImportError:
    pinyin = None  # type: ignore[assignment]
    lazy_pinyin = None  # type: ignore[assignment]
    to_normal = None  # type: ignore[assignment]
    Style = None  # type: ignore[assignment]
    _HAS_PYPINYIN = False

try:
    import jieba

    _HAS_JIEBA: bool = True
except ImportError:
    jieba = None  # type: ignore[assignment]
    _HAS_JIEBA = False

# --- 英文 G2P: g2p_en ---
try:
    from g2p_en import G2p as _EnG2p

    _HAS_G2P_EN: bool = True
except ImportError:
    _EnG2p = None  # type: ignore[assignment]
    _HAS_G2P_EN = False

# --- 日文 G2P: pyopenjtalk ---
try:
    import pyopenjtalk

    _HAS_OPENJTALK: bool = True
except ImportError:
    pyopenjtalk = None  # type: ignore[assignment]
    _HAS_OPENJTALK = False

# --- 韩文 G2P: g2pk2 ---
try:
    from g2pk2 import G2p as _KoG2p

    _HAS_G2PK2: bool = True
except ImportError:
    _KoG2p = None  # type: ignore[assignment]
    _HAS_G2PK2 = False


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class G2PResult:
    """G2P 转换结果。

    Attributes:
        text: 转换后的音素文本。
        language: 使用的语言代码。
        engine_name: 实际使用的 G2P 引擎名称（如 "pypinyin"、"g2p_en"、"passthrough"）。
        cached: 是否命中缓存。
        processing_time_ms: 处理耗时（毫秒）。
    """

    text: str
    language: str
    engine_name: str
    cached: bool = False
    processing_time_ms: float = 0.0


@dataclass
class G2PStats:
    """G2P 处理统计信息。

    Attributes:
        total_requests: 总处理请求数。
        cache_hits: 缓存命中次数。
        cache_misses: 缓存未命中次数。
        total_processing_time_ms: 总处理时间（毫秒）。
        language_usage: 按语言统计的请求数。
    """

    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    total_processing_time_ms: float = 0.0
    language_usage: dict[str, int] = field(default_factory=dict)

    @property
    def cache_hit_rate(self) -> float:
        """缓存命中率（0.0~1.0）。"""
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0.0

    @property
    def avg_processing_time_ms(self) -> float:
        """平均处理时间（毫秒）。"""
        return self.total_processing_time_ms / self.total_requests if self.total_requests > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "total_requests": self.total_requests,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            "total_processing_time_ms": round(self.total_processing_time_ms, 2),
            "avg_processing_time_ms": round(self.avg_processing_time_ms, 2),
            "language_usage": dict(self.language_usage),
        }


# ---------------------------------------------------------------------------
# LRU 缓存
# ---------------------------------------------------------------------------


class _LRUCache:
    """线程安全的 LRU 缓存。

    使用 OrderedDict 实现 LRU 淘汰策略，
    通过 threading.Lock 保证线程安全。
    """

    def __init__(self, maxsize: int = DEFAULT_CACHE_SIZE) -> None:
        """初始化 LRU 缓存。

        Args:
            maxsize: 缓存最大条目数。
        """
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        """获取缓存值。

        Args:
            key: 缓存键。

        Returns:
            缓存的值，未命中时返回 None。
        """
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return None

    def put(self, key: str, value: str) -> None:
        """写入缓存。

        Args:
            key: 缓存键。
            value: 缓存值。
        """
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = value
            else:
                self._cache[key] = value
                if len(self._cache) > self._maxsize:
                    self._cache.popitem(last=False)

    def clear(self) -> None:
        """清空缓存。"""
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        """当前缓存条目数。"""
        with self._lock:
            return len(self._cache)

    @property
    def maxsize(self) -> int:
        """缓存最大条目数。"""
        return self._maxsize


# ---------------------------------------------------------------------------
# G2PManager
# ---------------------------------------------------------------------------


class G2PManager:
    """多语言 G2P 音素转换管理器。

    管理多种语言的 G2P 后端引擎，提供统一的转换接口。
    内置 LRU 缓存提升重复文本处理速度，支持优雅降级。

    Attributes:
        stats: G2P 处理统计信息。
    """

    def __init__(self, cache_size: int = DEFAULT_CACHE_SIZE) -> None:
        """初始化 G2P 管理器。

        Args:
            cache_size: LRU 缓存大小（条目数）。
        """
        self._cache = _LRUCache(maxsize=cache_size)
        self.stats = G2PStats()
        self._lock = threading.Lock()

        # 懒初始化的 G2P 后端实例
        self._zh_engine: Any | None = None
        self._en_engine: Any | None = None
        self._ja_engine: Any | None = None
        self._ko_engine: Any | None = None
        self._initialized: set[str] = set()

        # 后端可用性标记
        self._backend_available: dict[str, bool] = {
            "zh": _HAS_PYPINYIN,
            "en": _HAS_G2P_EN,
            "ja": _HAS_OPENJTALK,
            "ko": _HAS_G2PK2,
        }

        logger.info(
            "G2PManager 初始化完成 | 后端可用性: zh=%s(pypinyin), en=%s(g2p_en), ja=%s(pyopenjtalk), ko=%s(g2pk2)",
            _HAS_PYPINYIN,
            _HAS_G2P_EN,
            _HAS_OPENJTALK,
            _HAS_G2PK2,
        )

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def convert(self, text: str, lang: str = DEFAULT_LANGUAGE) -> G2PResult:
        """对文本执行 G2P 音素转换。

        根据语言选择对应的 G2P 后端进行转换。
        如果后端不可用，回退为透传模式（返回原文）。
        结果会被缓存，相同文本+语言组合不会重复计算。

        Args:
            text: 待转换的文本（建议已过 TextNormalizer 规范化）。
            lang: 语言代码 (zh/en/ja/ko)。

        Returns:
            G2PResult 包含转换后的文本和元信息。

        Raises:
            ValueError: 文本超过最大长度限制。
        """
        start_time = time.perf_counter()

        # 参数校验：空文本或纯空白文本统一返回空结果
        if not text or not text.strip():
            return G2PResult(
                text=text.strip() if text else "",
                language=lang,
                engine_name="empty",
                cached=False,
                processing_time_ms=0.0,
            )

        if len(text) > MAX_TEXT_LENGTH:
            raise ValueError(f"文本长度 {len(text)} 超过最大限制 {MAX_TEXT_LENGTH}")

        if lang not in SUPPORTED_LANGUAGES:
            logger.warning("不支持的语言 '%s'，回退为 '%s'", lang, DEFAULT_LANGUAGE)
            lang = DEFAULT_LANGUAGE

        # 生成缓存键
        cache_key = self._make_cache_key(text, lang)

        # 检查缓存
        cached_result = self._cache.get(cache_key)
        if cached_result is not None:
            elapsed = (time.perf_counter() - start_time) * 1000
            with self._lock:
                self.stats.cache_hits += 1
                self.stats.total_requests += 1
                self.stats.total_processing_time_ms += elapsed
                self.stats.language_usage[lang] = self.stats.language_usage.get(lang, 0) + 1
            return G2PResult(
                text=cached_result,
                language=lang,
                engine_name="cached",
                cached=True,
                processing_time_ms=elapsed,
            )

        # 未命中缓存，执行 G2P 转换
        with self._lock:
            self.stats.cache_misses += 1

        converted, engine_name = self._do_convert(text, lang)

        # 写入缓存
        self._cache.put(cache_key, converted)

        elapsed = (time.perf_counter() - start_time) * 1000
        with self._lock:
            self.stats.total_requests += 1
            self.stats.total_processing_time_ms += elapsed
            self.stats.language_usage[lang] = self.stats.language_usage.get(lang, 0) + 1

        return G2PResult(
            text=converted,
            language=lang,
            engine_name=engine_name,
            cached=False,
            processing_time_ms=elapsed,
        )

    def convert_text(self, text: str, lang: str = DEFAULT_LANGUAGE) -> str:
        """便捷方法：仅返回转换后的文本字符串。

        Args:
            text: 待转换的文本。
            lang: 语言代码。

        Returns:
            转换后的音素文本。
        """
        result = self.convert(text, lang)
        return result.text

    def convert_batch(self, texts: list[str], lang: str = DEFAULT_LANGUAGE) -> list[G2PResult]:
        """批量 G2P 转换。

        Args:
            texts: 待转换的文本列表。
            lang: 语言代码。

        Returns:
            G2PResult 列表，与输入文本一一对应。
        """
        return [self.convert(text, lang) for text in texts]

    def is_available(self, lang: str) -> bool:
        """检查指定语言的 G2P 后端是否可用。

        Args:
            lang: 语言代码。

        Returns:
            后端可用返回 True，否则返回 False。
        """
        return self._backend_available.get(lang, False)

    def get_engine_name(self, lang: str) -> str:
        """获取指定语言实际使用的 G2P 引擎名称。

        Args:
            lang: 语言代码。

        Returns:
            引擎名称字符串（如 "pypinyin"、"g2p_en"、"passthrough"）。
        """
        if lang == "zh" and _HAS_PYPINYIN:
            return "pypinyin"
        elif lang == "en" and _HAS_G2P_EN:
            return "g2p_en"
        elif lang == "ja" and _HAS_OPENJTALK:
            return "pyopenjtalk"
        elif lang == "ko" and _HAS_G2PK2:
            return "g2pk2"
        else:
            return "passthrough"

    def clear_cache(self) -> None:
        """清空 G2P 缓存。"""
        self._cache.clear()
        logger.info("G2P 缓存已清空")

    def get_cache_stats(self) -> dict[str, Any]:
        """获取缓存统计信息。

        Returns:
            包含缓存大小、命中率等信息的字典。
        """
        return {
            "cache_size": self._cache.size,
            "cache_maxsize": self._cache.maxsize,
            **self.stats.to_dict(),
        }

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    @staticmethod
    def _make_cache_key(text: str, lang: str) -> str:
        """生成缓存键。

        使用 MD5 哈希文本内容 + 语言代码生成唯一键。

        Args:
            text: 文本内容。
            lang: 语言代码。

        Returns:
            缓存键字符串。
        """
        content = f"{lang}:{text}"
        return hashlib.md5(content.encode("utf-8"), usedforsecurity=False).hexdigest()

    def _do_convert(self, text: str, lang: str) -> tuple[str, str]:
        """执行实际的 G2P 转换。

        根据语言分派到对应的 G2P 后端。

        Args:
            text: 待转换文本。
            lang: 语言代码。

        Returns:
            二元组：(转换后的文本, 引擎名称)。
        """
        try:
            if lang == "zh":
                return self._convert_zh(text)
            elif lang == "en":
                return self._convert_en(text)
            elif lang == "ja":
                return self._convert_ja(text)
            elif lang == "ko":
                return self._convert_ko(text)
            else:
                return text, "passthrough"
        except Exception as e:
            logger.error("G2P 转换失败 (lang=%s): %s — 回退为透传", lang, e)
            return text, "passthrough_fallback"

    def _convert_zh(self, text: str) -> tuple[str, str]:
        """中文 G2P 转换。

        使用 pypinyin 进行拼音转换，结合 jieba 分词辅助多音字消歧。
        未安装依赖时回退为透传模式。

        Args:
            text: 中文文本。

        Returns:
            二元组：(拼音文本, 引擎名称)。
        """
        if not _HAS_PYPINYIN:
            logger.debug("pypinyin 未安装，中文 G2P 透传")
            return text, "passthrough"

        # 使用 jieba 分词辅助多音字消歧
        # 无 jieba 时按字符处理
        words = list(jieba.cut(text)) if _HAS_JIEBA else list(text)

        # 使用 pypinyin 转换为带声调的拼音
        pinyin_list: list[str] = []
        for word in words:
            if word.strip():
                # 对每个词获取拼音
                word_pinyin = pinyin(word, style=Style.TONE, heteronym=False)
                for p in word_pinyin:
                    if p and p[0]:
                        pinyin_list.append(p[0])

        result = " ".join(pinyin_list) if pinyin_list else text
        return result, "pypinyin"

    def _convert_en(self, text: str) -> tuple[str, str]:
        """英文 G2P 转换。

        使用 g2p_en 将英文文本转换为 CMU 音素。
        未安装依赖时回退为透传模式。

        Args:
            text: 英文文本。

        Returns:
            二元组：(音素文本, 引擎名称)。
        """
        if not _HAS_G2P_EN:
            logger.debug("g2p_en 未安装，英文 G2P 透传")
            return text, "passthrough"

        # 懒初始化 g2p_en 实例（首次调用时加载 CMU 字典）
        if self._en_engine is None:
            self._en_engine = _EnG2p()
            self._initialized.add("en")

        phonemes = self._en_engine(text)
        # g2p_en 返回音素列表，如 ['H', 'EH', 'L', 'OW', ' ', 'W', 'ER', 'L', 'D']
        result = " ".join(phonemes) if isinstance(phonemes, list) else str(phonemes)
        return result, "g2p_en"

    def _convert_ja(self, text: str) -> tuple[str, str]:
        """日文 G2P 转换。

        使用 pyopenjtalk 将日文文本转换为音素序列。
        未安装依赖时回退为透传模式。

        Args:
            text: 日文文本。

        Returns:
            二元组：(音素文本, 引擎名称)。
        """
        if not _HAS_OPENJTALK:
            logger.debug("pyopenjtalk 未安装，日文 G2P 透传")
            return text, "passthrough"

        # pyopenjtalk.g2p 返回音素字符串
        result = pyopenjtalk.g2p(text, kana=False)
        return result, "pyopenjtalk"

    def _convert_ko(self, text: str) -> tuple[str, str]:
        """韩文 G2P 转换。

        使用 g2pk2 将韩文文本转换为音素序列。
        未安装依赖时回退为透传模式。

        Args:
            text: 韩文文本。

        Returns:
            二元组：(音素文本, 引擎名称)。
        """
        if not _HAS_G2PK2:
            logger.debug("g2pk2 未安装，韩文 G2P 透传")
            return text, "passthrough"

        # 懒初始化 g2pk2 实例
        if self._ko_engine is None:
            self._ko_engine = _KoG2p()
            self._initialized.add("ko")

        result = self._ko_engine(text)
        return result, "g2pk2"


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

_manager_instance: G2PManager | None = None
_manager_lock = threading.Lock()


def get_g2p_manager() -> G2PManager:
    """获取模块级 G2PManager 单例。

    Returns:
        G2PManager 实例。
    """
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = G2PManager()
    return _manager_instance


def convert_text(text: str, lang: str = DEFAULT_LANGUAGE) -> str:
    """便捷函数：G2P 转换文本。

    Args:
        text: 待转换的文本。
        lang: 语言代码。

    Returns:
        转换后的音素文本。
    """
    return get_g2p_manager().convert_text(text, lang)


def convert(text: str, lang: str = DEFAULT_LANGUAGE) -> G2PResult:
    """便捷函数：G2P 转换文本（返回完整结果）。

    Args:
        text: 待转换的文本。
        lang: 语言代码。

    Returns:
        G2PResult 对象。
    """
    return get_g2p_manager().convert(text, lang)
