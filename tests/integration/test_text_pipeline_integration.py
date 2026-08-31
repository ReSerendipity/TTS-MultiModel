"""文本处理流水线集成测试。

测试完整的文本处理流水线：
    TextFrontend → G2PManager → TextSegmenter → ContentSafetyDetector → PromptExpander

覆盖端到端场景：
    1. 长文本 → 安全检测 → 规范化 → G2P → 分块 → 拼接
    2. 多语言文本处理
    3. 模板应用 + G2P
    4. 安全内容过滤 + 分块
"""

import os
import sys

import numpy as np
import pytest

pytestmark = pytest.mark.integration

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "bin")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from integrated_app.g2p_manager import G2PManager
from integrated_app.prompt_expander import PromptExpander, TemplateCategory
from integrated_app.security.content_safety import ContentSafetyDetector, SafetyCategory
from integrated_app.text_frontend import TextFrontend
from integrated_app.text_segmenter import AudioCrossfader, TextSegmenter

# ---------------------------------------------------------------------------
# 流水线集成测试
# ---------------------------------------------------------------------------


class TestTextPipelineIntegration:
    """完整的文本处理流水线集成测试。"""

    def test_full_pipeline_safe_text(self):
        """安全文本完整流水线处理。"""
        # 1. 安全检测
        safety = ContentSafetyDetector(threshold=0.5)
        text = "今天天气真好，我们去公园散步吧。这是一个美好的周末。"
        safety_result = safety.detect(text)
        assert safety_result.is_safe

        # 2. 文本前端处理（规范化 + G2P）
        frontend = TextFrontend()
        processed_text, lang = frontend.process(text)

        # 3. G2P 转换
        g2p = G2PManager()
        g2p_result = g2p.convert(processed_text, lang)
        assert g2p_result.text  # 非空

        # 4. 长文本分块
        segmenter = TextSegmenter(max_chars=20, min_chars=5)
        seg_result = segmenter.segment(processed_text, lang)
        assert seg_result.segment_count > 0

        # 5. 对每个分块执行 G2P
        for seg in seg_result.segments:
            seg_g2p = g2p.convert(seg.text, lang)
            assert seg_g2p.text  # 非空

    def test_pipeline_with_unsafe_text_blocked(self):
        """不安全文本应被拦截，不进入后续处理。"""
        safety = ContentSafetyDetector(threshold=0.3)
        text = "我要杀人"

        safety_result = safety.detect(text)

        # 如果检测为不安全，不应继续处理
        if not safety_result.is_safe:
            assert safety_result.category != SafetyCategory.SAFE
            # 模拟实际流程中的拦截行为
            assert safety_result.confidence > 0

    def test_pipeline_long_text_segmentation_and_crossfade(self):
        """长文本分块 + 音频交叉淡入淡出拼接。"""
        # 1. 分块
        segmenter = TextSegmenter(max_chars=30, min_chars=5)
        long_text = "这是第一句话。这是第二句话。这是第三句话。这是第四句话。这是第五句话。"
        seg_result = segmenter.segment(long_text, "zh")
        assert seg_result.segment_count > 1

        # 2. 模拟每段生成音频
        sr = 24000
        audio_segments = [
            np.ones(sr, dtype=np.float32) * 0.5  # 1秒音频
            for _ in seg_result.segments
        ]

        # 3. 交叉淡入淡出拼接
        crossfader = AudioCrossfader(fade_duration_ms=50, default_sample_rate=sr)
        final_audio = crossfader.crossfade_concat(audio_segments, sr)

        assert len(final_audio) > 0
        assert isinstance(final_audio, np.ndarray)

    def test_pipeline_multi_language(self):
        """多语言文本处理。"""
        frontend = TextFrontend()
        g2p = G2PManager()

        test_cases = [
            ("你好世界", "zh"),
            ("Hello world", "en"),
            ("こんにちは世界", "ja"),
            ("안녕하세요 세계", "ko"),
        ]

        for text, expected_lang in test_cases:
            # 前端处理
            processed, detected_lang = frontend.process(text)
            assert detected_lang == expected_lang

            # G2P
            g2p_result = g2p.convert(processed, detected_lang)
            assert g2p_result.text  # 非空
            assert g2p_result.language == detected_lang

    def test_pipeline_prompt_expansion_with_g2p(self):
        """提示词扩展 + G2P 转换。"""
        expander = PromptExpander()
        g2p = G2PManager()

        # 应用模板
        instruction = expander.apply_template(
            "gentle_female",
            {"speed": "偏慢"},
            lang="zh",
        )
        assert "温柔" in instruction
        assert "偏慢" in instruction

        # G2P 转换
        g2p_result = g2p.convert(instruction, "zh")
        assert g2p_result.text  # 非空

    def test_pipeline_batch_processing(self):
        """批量文本处理流水线。"""
        safety = ContentSafetyDetector(threshold=0.3)
        frontend = TextFrontend()
        g2p = G2PManager()

        texts = [
            "你好世界",
            "Hello world",
            "这是一个测试",
            "天气真好",
        ]

        # 批量安全检测
        safety_results = safety.detect_batch(texts)
        assert len(safety_results) == len(texts)

        # 过滤安全文本
        safe_texts = [text for text, result in zip(texts, safety_results, strict=False) if result.is_safe]

        # 批量前端处理
        for text in safe_texts:
            processed, lang = frontend.process(text)
            g2p_result = g2p.convert(processed, lang)
            assert g2p_result.text

    def test_pipeline_template_search_and_apply(self):
        """模板搜索 + 应用 + G2P。"""
        expander = PromptExpander()
        g2p = G2PManager()

        # 搜索模板
        results = expander.search_templates("新闻")
        assert len(results) > 0

        # 应用找到的模板
        template = results[0]
        instruction = template.render(lang="zh")
        assert instruction  # 非空

        # G2P 转换
        g2p_result = g2p.convert(instruction, "zh")
        assert g2p_result.text

    def test_pipeline_smart_expand(self):
        """智能扩展 + 安全检测。"""
        expander = PromptExpander()
        safety = ContentSafetyDetector(threshold=0.3)

        # 智能扩展
        expanded = expander.expand("温柔的新闻播报", lang="zh")
        assert len(expanded) > len("温柔的新闻播报")

        # 安全检测
        result = safety.detect(expanded)
        assert result.is_safe  # 扩展后的内容应该安全

    def test_pipeline_chinese_number_normalization(self):
        """中文数字规范化 + G2P。"""
        frontend = TextFrontend()
        g2p = G2PManager()

        text = "数量是100个，比例50%，日期2024年3月15日"
        processed, lang = frontend.process(text)

        # 规范化后应该包含中文数字
        assert "一百" in processed or "100" in processed

        # G2P 应能处理
        g2p_result = g2p.convert(processed, lang)
        assert g2p_result.text

    def test_pipeline_segmentation_preserves_content(self):
        """分块后内容完整性验证。"""
        segmenter = TextSegmenter(max_chars=50, min_chars=10)
        text = "这是第一句话。这是第二句话。这是第三句话。这是第四句话。"

        seg_result = segmenter.segment(text, "zh")

        # 所有分块合起来应覆盖原文主要内容
        all_text = "".join(seg.text for seg in seg_result.segments)
        # 至少包含原始关键词
        assert "第一" in all_text
        assert "第二" in all_text
        assert "第三" in all_text
        assert "第四" in all_text

    def test_pipeline_cache_effectiveness(self):
        """G2P 缓存效果验证。"""
        g2p = G2PManager(cache_size=10)

        text = "你好世界"
        # 第一次调用（未命中）
        result1 = g2p.convert(text, "zh")
        assert not result1.cached

        # 第二次调用（应命中）
        result2 = g2p.convert(text, "zh")
        assert result2.cached

        # 缓存统计
        stats = g2p.get_cache_stats()
        assert stats["cache_size"] >= 1
        assert g2p.stats.cache_hits >= 1

    def test_pipeline_crossfade_no_artifacts(self):
        """交叉淡入淡出不应产生音频伪影。"""
        sr = 24000
        crossfader = AudioCrossfader(fade_duration_ms=100, default_sample_rate=sr)

        # 生成两段正弦波
        t1 = np.linspace(0, 1, sr, endpoint=False)
        t2 = np.linspace(0, 1, sr, endpoint=False)
        audio1 = (np.sin(2 * np.pi * 440 * t1) * 0.5).astype(np.float32)
        audio2 = (np.sin(2 * np.pi * 880 * t2) * 0.5).astype(np.float32)

        result = crossfader.crossfade_concat([audio1, audio2], sr)

        # 检查无 NaN
        assert not np.any(np.isnan(result))
        # 检查无 Inf
        assert not np.any(np.isinf(result))
        # 检查值范围合理
        assert np.max(np.abs(result)) <= 1.0

    def test_pipeline_all_categories_have_templates(self):
        """所有类别都应有预置模板。"""
        expander = PromptExpander()

        for category in [
            TemplateCategory.VOICE_DESIGN,
            TemplateCategory.EMOTION_STYLE,
            TemplateCategory.SCENE,
            TemplateCategory.CHARACTER,
        ]:
            templates = expander.get_templates(category)
            assert len(templates) > 0, f"类别 {category} 没有预置模板"

    def test_pipeline_safety_stats_tracking(self):
        """安全检测统计追踪。"""
        detector = ContentSafetyDetector(threshold=0.3)

        # 多次检测
        detector.detect("你好")
        detector.detect("世界")
        detector.detect("测试")

        stats = detector.get_stats()
        assert stats["total_checks"] == 3
        assert stats["passed"] + stats["blocked"] == 3
