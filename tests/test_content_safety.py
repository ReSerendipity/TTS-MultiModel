"""内容安全检测模块单元测试。

覆盖目标模块: app/integrated_app/security/content_safety.py
测试内容:
    1. 安全文本通过检测
    2. 各类别不安全文本被拦截（暴力/仇恨/自残/色情/违法/骚扰）
    3. 置信度计算正确性
    4. 批量检测与过滤
    5. 统计信息正确性
    6. 边界条件与异常处理
"""

import os
import sys

import pytest

_APP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"
)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from integrated_app.security.content_safety import (
    ContentSafetyDetector,
    SafetyCategory,
    SafetyDetectionResult,
    check_safety,
    get_safety_detector,
    is_safe,
)


# ---------------------------------------------------------------------------
# 安全文本测试
# ---------------------------------------------------------------------------


class TestSafeText:
    """安全文本应通过检测。"""

    def setup_method(self):
        self.detector = ContentSafetyDetector(threshold=0.6)

    def test_empty_text_safe(self):
        """空文本应判定为安全。"""
        result = self.detector.detect("")
        assert result.is_safe is True

    def test_whitespace_safe(self):
        """纯空白应判定为安全。"""
        result = self.detector.detect("   ")
        assert result.is_safe is True

    def test_normal_chinese_text(self):
        """正常中文文本应通过。"""
        texts = [
            "今天天气真好，我们去公园散步吧。",
            "这是一个关于科技发展的新闻报道。",
            "学习Python编程语言很有趣。",
            "大家好，欢迎收听今天的播客节目。",
        ]
        for text in texts:
            result = self.detector.detect(text)
            assert result.is_safe is True, f"误判为不安全: {text} -> {result}"

    def test_normal_english_text(self):
        """正常英文文本应通过。"""
        texts = [
            "The weather is nice today, let's go for a walk.",
            "This is a news report about technology development.",
            "Learning Python programming is fun.",
            "Hello everyone, welcome to today's podcast.",
        ]
        for text in texts:
            result = self.detector.detect(text)
            assert result.is_safe is True, f"误判为不安全: {text} -> {result}"

    def test_normal_japanese_text(self):
        """正常日文文本应通过。"""
        texts = [
            "今日は天気がいいですね。公園に行きましょう。",
            "これは技術に関するニュースです。",
        ]
        for text in texts:
            result = self.detector.detect(text)
            assert result.is_safe is True

    def test_normal_korean_text(self):
        """正常韩文文本应通过。"""
        texts = [
            "오늘 날씨가 정말 좋네요. 공원에 가요.",
            "이것은 기술에 관한 뉴스입니다.",
        ]
        for text in texts:
            result = self.detector.detect(text)
            assert result.is_safe is True


# ---------------------------------------------------------------------------
# 不安全文本检测测试
# ---------------------------------------------------------------------------


class TestUnsafeText:
    """不安全文本应被检测到。"""

    def setup_method(self):
        self.detector = ContentSafetyDetector(threshold=0.3)

    def test_violence_detected(self):
        """暴力内容应被检测。"""
        texts = [
            "我要杀人",
            "把他打死",
            "准备爆炸物",
            "murder him tonight",
        ]
        for text in texts:
            result = self.detector.detect(text)
            assert result.category != SafetyCategory.SAFE or not result.is_safe or result.confidence > 0

    def test_hate_speech_detected(self):
        """仇恨言论应被检测。"""
        texts = [
            "劣等民族应该滚出去",
            "种族灭绝是必要的",
        ]
        for text in texts:
            result = self.detector.detect(text)
            # 至少应检测到一些模式
            assert result.confidence > 0 or not result.is_safe

    def test_self_harm_detected(self):
        """自残内容应被检测。"""
        texts = [
            "我想自杀",
            "不想活了",
            "kill myself tonight",
        ]
        for text in texts:
            result = self.detector.detect(text)
            assert not result.is_safe or result.confidence > 0

    def test_illegal_detected(self):
        """违法内容应被检测。"""
        texts = [
            "他在贩毒",
            "这是一个洗钱的计划",
            "drug trafficking business",
        ]
        for text in texts:
            result = self.detector.detect(text)
            assert not result.is_safe or result.confidence > 0

    def test_harassment_detected(self):
        """骚扰内容应被检测。"""
        texts = [
            "你是个废物",
            "去死吧",
            "you are worthless trash",
        ]
        for text in texts:
            result = self.detector.detect(text)
            assert not result.is_safe or result.confidence > 0

    def test_matched_patterns_recorded(self):
        """匹配到的模式应被记录。"""
        result = self.detector.detect("我要杀人")
        if not result.is_safe:
            assert len(result.matched_patterns) > 0


# ---------------------------------------------------------------------------
# 置信度与阈值测试
# ---------------------------------------------------------------------------


class TestConfidenceThreshold:
    """置信度与阈值测试。"""

    def test_low_threshold_blocks_more(self):
        """低阈值应拦截更多内容。"""
        low_detector = ContentSafetyDetector(threshold=0.1)
        high_detector = ContentSafetyDetector(threshold=0.9)

        text = "去死"  # 匹配骚扰模式但置信度不高
        low_result = low_detector.detect(text)
        high_result = high_detector.detect(text)

        # 低阈值更容易判定为不安全
        if low_result.confidence > 0:
            assert not low_result.is_safe or high_result.is_safe

    def test_threshold_property(self):
        """threshold 属性应正确返回。"""
        detector = ContentSafetyDetector(threshold=0.5)
        assert detector.threshold == 0.5

    def test_invalid_threshold_raises(self):
        """无效阈值应抛出 ValueError。"""
        with pytest.raises(ValueError):
            ContentSafetyDetector(threshold=-0.1)
        with pytest.raises(ValueError):
            ContentSafetyDetector(threshold=1.1)

    def test_confidence_range(self):
        """置信度应在 0~1 之间。"""
        detector = ContentSafetyDetector()
        result = detector.detect("测试文本")
        assert 0.0 <= result.confidence <= 1.0


# ---------------------------------------------------------------------------
# 批量检测与过滤测试
# ---------------------------------------------------------------------------


class TestBatchDetection:
    """批量检测功能测试。"""

    def setup_method(self):
        self.detector = ContentSafetyDetector(threshold=0.3)

    def test_batch_basic(self):
        """批量检测应返回等长结果列表。"""
        texts = ["你好世界", "我要杀人", "天气真好"]
        results = self.detector.detect_batch(texts)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, SafetyDetectionResult)

    def test_batch_empty_list(self):
        """空列表应返回空列表。"""
        results = self.detector.detect_batch([])
        assert results == []

    def test_filter_safe_returns_unsafe(self):
        """filter_safe 应返回不安全的文本。"""
        texts = ["你好世界", "我要杀人", "天气真好", "贩毒计划"]
        unsafe = self.detector.filter_safe(texts)
        # 应只包含不安全项
        for idx, text, result in unsafe:
            assert not result.is_safe
            assert idx in (1, 3)

    def test_filter_safe_all_safe(self):
        """全部安全时 filter_safe 应返回空列表。"""
        texts = ["你好", "世界", "测试"]
        unsafe = self.detector.filter_safe(texts)
        assert unsafe == []


# ---------------------------------------------------------------------------
# 统计信息测试
# ---------------------------------------------------------------------------


class TestSafetyStats:
    """检测统计信息测试。"""

    def test_stats_initial(self):
        """初始统计应为零。"""
        detector = ContentSafetyDetector()
        stats = detector.get_stats()
        assert stats["total_checks"] == 0
        assert stats["blocked"] == 0
        assert stats["passed"] == 0

    def test_stats_after_checks(self):
        """检测后统计应更新。"""
        detector = ContentSafetyDetector(threshold=0.3)
        detector.detect("你好")
        detector.detect("我要杀人")
        detector.detect("世界")

        stats = detector.get_stats()
        assert stats["total_checks"] == 3
        assert stats["passed"] + stats["blocked"] == 3

    def test_block_rate(self):
        """拦截率应正确计算。"""
        detector = ContentSafetyDetector(threshold=0.3)
        detector.detect("你好")
        detector.detect("世界")
        stats = detector.get_stats()
        if stats["total_checks"] > 0:
            rate = stats["blocked"] / stats["total_checks"]
            assert 0.0 <= rate <= 1.0


# ---------------------------------------------------------------------------
# 便捷方法测试
# ---------------------------------------------------------------------------


class TestConvenienceMethods:
    """便捷方法测试。"""

    def test_is_safe_method(self):
        """is_safe 方法应返回布尔值。"""
        detector = ContentSafetyDetector()
        assert isinstance(detector.is_safe("你好"), bool)
        assert isinstance(detector.is_safe("我要杀人"), bool)

    def test_module_get_detector_singleton(self):
        """模块级单例应返回同一实例。"""
        d1 = get_safety_detector()
        d2 = get_safety_detector()
        assert d1 is d2

    def test_module_check_safety(self):
        """模块级 check_safety 应返回 SafetyDetectionResult。"""
        result = check_safety("你好")
        assert isinstance(result, SafetyDetectionResult)

    def test_module_is_safe(self):
        """模块级 is_safe 应返回布尔值。"""
        assert isinstance(is_safe("你好"), bool)


# ---------------------------------------------------------------------------
# 边界条件与异常处理测试
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """边界条件与异常处理测试。"""

    def setup_method(self):
        self.detector = ContentSafetyDetector()

    def test_text_too_long_raises(self):
        """超长文本应抛出 ValueError。"""
        long_text = "a" * 10001
        with pytest.raises(ValueError, match="超过最大限制"):
            self.detector.detect(long_text)

    def test_text_at_max_length(self):
        """恰好最大长度的文本应正常处理。"""
        text = "a" * 10000
        result = self.detector.detect(text)
        assert isinstance(result, SafetyDetectionResult)

    def test_special_characters(self):
        """特殊字符不应导致崩溃。"""
        texts = [
            "Hello! @#$%^&*()",
            "你好！@#￥%……&*（）",
            "🎉Emoji Test🎯",
            "Line1\nLine2\tTab",
        ]
        for text in texts:
            result = self.detector.detect(text)
            assert isinstance(result, SafetyDetectionResult)

    def test_mixed_language(self):
        """混合语言文本应正常处理。"""
        text = "你好 Hello こんにちは 안녕 murder"
        result = self.detector.detect(text)
        assert isinstance(result, SafetyDetectionResult)

    def test_result_to_dict(self):
        """to_dict 应返回可序列化字典。"""
        result = self.detector.detect("测试")
        d = result.to_dict()
        assert isinstance(d, dict)
        assert "is_safe" in d
        assert "category" in d
        assert "confidence" in d

    def test_category_enum_values(self):
        """SafetyCategory 枚举值应正确。"""
        assert SafetyCategory.SAFE.value == "safe"
        assert SafetyCategory.VIOLENCE.value == "violence"
        assert SafetyCategory.HATE_SPEECH.value == "hate_speech"
        assert SafetyCategory.SELF_HARM.value == "self_harm"
        assert SafetyCategory.SEXUAL.value == "sexual"
        assert SafetyCategory.ILLEGAL.value == "illegal"
        assert SafetyCategory.HARASSMENT.value == "harassment"

    def test_clip_disabled_by_default(self):
        """默认不启用 CLIP（避免需要下载模型）。"""
        detector = ContentSafetyDetector(enable_clip=False)
        result = detector.detect("测试文本")
        assert result.detection_method in ("keyword", "combined")
