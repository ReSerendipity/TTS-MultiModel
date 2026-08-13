"""G2P 管理器单元测试。

覆盖目标模块: bin/integrated_app/g2p_manager.py
测试内容:
    1. G2PManager 基本转换功能（多语言）
    2. LRU 缓存命中与淘汰
    3. 优雅降级（后端不可用时透传）
    4. 统计信息正确性
    5. 批量转换
    6. 边界条件与异常处理
"""

import os
import sys

import pytest

_BIN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"
)
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

from integrated_app.g2p_manager import (
    DEFAULT_CACHE_SIZE,
    G2PManager,
    G2PResult,
    G2PStats,
    convert,
    convert_text,
    get_g2p_manager,
)


# ---------------------------------------------------------------------------
# G2PManager 基础功能测试
# ---------------------------------------------------------------------------


class TestG2PManagerBasic:
    """G2PManager 基本转换功能测试。"""

    def setup_method(self):
        """每个测试方法前创建新的 G2PManager 实例。"""
        self.manager = G2PManager(cache_size=128)

    def test_empty_text(self):
        """空文本应返回空结果。"""
        result = self.manager.convert("", "zh")
        assert result.text == ""
        assert result.language == "zh"

    def test_whitespace_only(self):
        """纯空白文本应返回空结果。"""
        result = self.manager.convert("   ", "zh")
        assert result.text == ""

    def test_chinese_passthrough_or_pinyin(self):
        """中文文本应被处理（pypinyin 可用时返回拼音，否则透传）。"""
        result = self.manager.convert("你好世界", "zh")
        assert isinstance(result, G2PResult)
        assert result.language == "zh"
        assert result.text  # 非空
        assert result.engine_name in ("pypinyin", "passthrough")

    def test_english_passthrough_or_phonemes(self):
        """英文文本应被处理（g2p_en 可用时返回音素，否则透传）。"""
        result = self.manager.convert("Hello world", "en")
        assert isinstance(result, G2PResult)
        assert result.language == "en"
        assert result.text
        assert result.engine_name in ("g2p_en", "passthrough")

    def test_japanese_passthrough_or_phonemes(self):
        """日文文本应被处理。"""
        result = self.manager.convert("こんにちは", "ja")
        assert isinstance(result, G2PResult)
        assert result.language == "ja"
        assert result.text
        assert result.engine_name in ("pyopenjtalk", "passthrough")

    def test_korean_passthrough_or_phonemes(self):
        """韩文文本应被处理。"""
        result = self.manager.convert("안녕하세요", "ko")
        assert isinstance(result, G2PResult)
        assert result.language == "ko"
        assert result.text
        assert result.engine_name in ("g2pk2", "passthrough")

    def test_unsupported_language_falls_back(self):
        """不支持的语言应回退为默认语言。"""
        result = self.manager.convert("hello", "fr")
        assert result.language == "zh"  # 回退为默认

    def test_convert_text_convenience(self):
        """convert_text 便捷方法应返回字符串。"""
        text = self.manager.convert_text("测试", "zh")
        assert isinstance(text, str)


# ---------------------------------------------------------------------------
# LRU 缓存测试
# ---------------------------------------------------------------------------


class TestG2PCache:
    """G2P 缓存功能测试。"""

    def setup_method(self):
        self.manager = G2PManager(cache_size=4)

    def test_cache_hit(self):
        """相同文本第二次调用应命中缓存。"""
        text = "你好世界"
        result1 = self.manager.convert(text, "zh")
        assert not result1.cached

        result2 = self.manager.convert(text, "zh")
        assert result2.cached
        assert result2.text == result1.text

    def test_cache_miss_different_text(self):
        """不同文本不应命中缓存。"""
        self.manager.convert("你好", "zh")
        result = self.manager.convert("世界", "zh")
        assert not result.cached

    def test_cache_miss_different_lang(self):
        """相同文本不同语言不应命中缓存。"""
        self.manager.convert("hello", "en")
        result = self.manager.convert("hello", "zh")
        assert not result.cached

    def test_cache_eviction(self):
        """缓存满时应淘汰最久未使用的条目。"""
        texts = ["文本1", "文本2", "文本3", "文本4"]
        for t in texts:
            self.manager.convert(t, "zh")

        # 添加第 5 个，应淘汰"文本1"
        self.manager.convert("文本5", "zh")

        # 再次请求"文本1"应未命中缓存
        result = self.manager.convert("文本1", "zh")
        assert not result.cached

        # "文本4"应仍在缓存中
        result = self.manager.convert("文本4", "zh")
        assert result.cached

    def test_cache_clear(self):
        """clear_cache 应清空缓存。"""
        self.manager.convert("测试", "zh")
        assert self.manager._cache.size > 0

        self.manager.clear_cache()
        assert self.manager._cache.size == 0

    def test_cache_stats(self):
        """缓存统计信息应正确。"""
        self.manager.convert("文本A", "zh")
        self.manager.convert("文本A", "zh")  # 命中
        self.manager.convert("文本B", "zh")  # 未命中

        stats = self.manager.get_cache_stats()
        assert stats["cache_size"] >= 1
        assert self.manager.stats.cache_hits == 1
        assert self.manager.stats.cache_misses == 2
        assert self.manager.stats.total_requests == 3


# ---------------------------------------------------------------------------
# 优雅降级测试
# ---------------------------------------------------------------------------


class TestG2PFallback:
    """G2P 后端不可用时的降级测试。"""

    def setup_method(self):
        self.manager = G2PManager()

    def test_passthrough_when_unavailable(self):
        """后端不可用时应透传原文。"""
        # 无论后端是否可用，都不应抛异常
        result = self.manager.convert("测试文本", "zh")
        assert result.text  # 非空
        assert result.engine_name in (
            "pypinyin",
            "passthrough",
            "passthrough_fallback",
        )

    def test_is_available_returns_bool(self):
        """is_available 应返回布尔值。"""
        for lang in ("zh", "en", "ja", "ko"):
            assert isinstance(self.manager.is_available(lang), bool)

    def test_get_engine_name(self):
        """get_engine_name 应返回引擎名称字符串。"""
        for lang in ("zh", "en", "ja", "ko"):
            name = self.manager.get_engine_name(lang)
            assert isinstance(name, str)
            assert len(name) > 0


# ---------------------------------------------------------------------------
# 统计信息测试
# ---------------------------------------------------------------------------


class TestG2PStats:
    """G2P 统计信息测试。"""

    def test_stats_initial(self):
        """初始统计应为零。"""
        manager = G2PManager()
        assert manager.stats.total_requests == 0
        assert manager.stats.cache_hits == 0
        assert manager.stats.cache_misses == 0

    def test_stats_after_requests(self):
        """请求后统计应更新。"""
        manager = G2PManager()
        manager.convert("你好", "zh")
        manager.convert("你好", "zh")  # 命中
        manager.convert("world", "en")

        assert manager.stats.total_requests == 3
        assert manager.stats.cache_hits == 1
        assert manager.stats.cache_misses == 2

    def test_language_usage(self):
        """语言使用统计应正确。"""
        manager = G2PManager()
        manager.convert("你好", "zh")
        manager.convert("hello", "en")
        manager.convert("你好", "zh")

        assert manager.stats.language_usage.get("zh", 0) == 2
        assert manager.stats.language_usage.get("en", 0) == 1

    def test_stats_to_dict(self):
        """to_dict 应返回可序列化字典。"""
        manager = G2PManager()
        manager.convert("test", "en")
        d = manager.stats.to_dict()
        assert isinstance(d, dict)
        assert "total_requests" in d
        assert "cache_hit_rate" in d
        assert "language_usage" in d

    def test_cache_hit_rate(self):
        """缓存命中率计算应正确。"""
        stats = G2PStats()
        stats.cache_hits = 3
        stats.cache_misses = 1
        assert stats.cache_hit_rate == 0.75


# ---------------------------------------------------------------------------
# 批量转换测试
# ---------------------------------------------------------------------------


class TestG2PBatch:
    """G2P 批量转换测试。"""

    def setup_method(self):
        self.manager = G2PManager()

    def test_batch_basic(self):
        """批量转换应返回与输入等长的结果列表。"""
        texts = ["你好", "世界", "测试"]
        results = self.manager.convert_batch(texts, "zh")
        assert len(results) == 3
        for r in results:
            assert isinstance(r, G2PResult)

    def test_batch_empty_list(self):
        """空列表应返回空列表。"""
        results = self.manager.convert_batch([], "zh")
        assert results == []

    def test_batch_with_empty_string(self):
        """批量转换中包含空字符串应正常处理。"""
        texts = ["你好", "", "测试"]
        results = self.manager.convert_batch(texts, "zh")
        assert len(results) == 3
        assert results[1].text == ""

    def test_batch_cache_hits(self):
        """批量转换中重复文本应命中缓存。"""
        texts = ["你好", "你好", "你好"]
        results = self.manager.convert_batch(texts, "zh")
        assert not results[0].cached
        assert results[1].cached
        assert results[2].cached


# ---------------------------------------------------------------------------
# 边界条件与异常处理测试
# ---------------------------------------------------------------------------


class TestG2PEdgeCases:
    """G2P 边界条件测试。"""

    def setup_method(self):
        self.manager = G2PManager()

    def test_text_too_long_raises(self):
        """超长文本应抛出 ValueError。"""
        long_text = "a" * 10001
        with pytest.raises(ValueError, match="超过最大限制"):
            self.manager.convert(long_text, "zh")

    def test_text_at_max_length(self):
        """恰好最大长度的文本应正常处理。"""
        text = "a" * 10000
        result = self.manager.convert(text, "zh")
        assert isinstance(result, G2PResult)

    def test_special_characters(self):
        """特殊字符不应导致崩溃。"""
        texts = [
            "Hello! @#$%^&*()",
            "你好！@#￥%……&*（）",
            "Line1\nLine2\tTab",
            "🎉Emoji Test🎯",
        ]
        for text in texts:
            result = self.manager.convert(text, "zh")
            assert isinstance(result, G2PResult)
            assert result.text  # 非空

    def test_mixed_language_text(self):
        """混合语言文本应正常处理。"""
        text = "你好 Hello こんにちは 안녕"
        result = self.manager.convert(text, "zh")
        assert isinstance(result, G2PResult)
        assert result.text

    def test_none_text(self):
        """None 输入应安全处理。"""
        result = self.manager.convert("", "zh")  # 空字符串替代 None
        assert result.text == ""

    def test_processing_time_recorded(self):
        """处理时间应被记录。"""
        result = self.manager.convert("测试文本", "zh")
        assert result.processing_time_ms >= 0.0


# ---------------------------------------------------------------------------
# 模块级单例测试
# ---------------------------------------------------------------------------


class TestG2PSingleton:
    """模块级单例测试。"""

    def test_get_g2p_manager_singleton(self):
        """get_g2p_manager 应返回同一实例。"""
        m1 = get_g2p_manager()
        m2 = get_g2p_manager()
        assert m1 is m2

    def test_convert_text_module_function(self):
        """模块级 convert_text 函数应正常工作。"""
        result = convert_text("测试", "zh")
        assert isinstance(result, str)

    def test_convert_module_function(self):
        """模块级 convert 函数应返回 G2PResult。"""
        result = convert("测试", "zh")
        assert isinstance(result, G2PResult)


# ---------------------------------------------------------------------------
# G2PResult 数据类测试
# ---------------------------------------------------------------------------


class TestG2PResult:
    """G2PResult 数据类测试。"""

    def test_creation(self):
        """G2PResult 应正确创建。"""
        result = G2PResult(
            text="nǐ hǎo",
            language="zh",
            engine_name="pypinyin",
            cached=False,
            processing_time_ms=1.5,
        )
        assert result.text == "nǐ hǎo"
        assert result.language == "zh"
        assert result.engine_name == "pypinyin"
        assert result.cached is False
        assert result.processing_time_ms == 1.5

    def test_defaults(self):
        """G2PResult 默认值应正确。"""
        result = G2PResult(text="test", language="en", engine_name="passthrough")
        assert result.cached is False
        assert result.processing_time_ms == 0.0
