"""text_frontend 模块单元测试 — 语言检测与文本规范化。

覆盖目标模块: app/integrated_app/text_frontend.py
"""

from integrated_app.text_frontend import (
    LanguageDetector,
    TextFrontend,
    TextNormalizer,
    detect_language,
    get_frontend,
    normalize_text,
    process_text,
)


class TestLanguageDetector:
    def setup_method(self):
        self.detector = LanguageDetector()

    def test_empty_text(self):
        result = self.detector.detect("")
        assert result.language == "zh"
        assert result.confidence == 0.0

    def test_chinese(self):
        result = self.detector.detect("你好世界，这是测试文本。")
        assert result.language == "zh"

    def test_english(self):
        result = self.detector.detect("Hello world, this is a test.")
        assert result.language == "en"

    def test_japanese(self):
        result = self.detector.detect("こんにちは、これはテストです。")
        assert result.language == "ja"

    def test_korean(self):
        result = self.detector.detect("안녕하세요, 이것은 테스트입니다.")
        assert result.language == "ko"

    def test_detect_language_convenience(self):
        assert self.detector.detect_language("hello") == "en"
        assert self.detector.detect_language("你好") == "zh"


class TestTextNormalizer:
    def setup_method(self):
        self.normalizer = TextNormalizer()

    def test_normalize_zh(self):
        out = self.normalizer.normalize("今天天气不错，我们去公园吧！", "zh")
        assert out
        assert isinstance(out, str)

    def test_normalize_zh_numbers(self):
        out = self.normalizer.normalize("数量是100个，比例50%", "zh")
        assert "一百" in out or "100" in out

    def test_normalize_en(self):
        out = self.normalizer.normalize("Hello world, it's a test.", "en")
        assert "hello" in out.lower()

    def test_normalize_en_numbers(self):
        out = self.normalizer.normalize("There are 100 items.", "en")
        assert "hundred" in out.lower() or "100" in out

    def test_normalize_ja(self):
        out = self.normalizer.normalize("これはテストです。100円です。", "ja")
        assert out

    def test_normalize_ko(self):
        out = self.normalizer.normalize("이것은 테스트입니다. 100개입니다.", "ko")
        assert out

    def test_normalize_punctuation(self):
        out = self.normalizer.normalize("你好！！！？？", "zh")
        assert "？" in out or "!" in out

    def test_protect_tts_tags(self):
        out = self.normalizer.normalize("你好[温柔]世界", "zh")
        assert "温柔" in out or "[温柔]" in out


class TestModuleFunctions:
    def test_detect_language_module(self):
        assert detect_language("Hello") == "en"
        assert detect_language("你好") == "zh"

    def test_normalize_text_module(self):
        out = normalize_text("你好世界", "zh")
        assert isinstance(out, str)

    def test_process_text_module(self):
        text, lang = process_text("Hello world")
        assert isinstance(text, str)
        assert lang == "en"

    def test_get_frontend_singleton(self):
        assert get_frontend() is get_frontend()


class TestTextFrontend:
    def test_instance(self):
        frontend = TextFrontend()
        assert frontend is not None
        result = frontend.process("你好")
        assert isinstance(result, tuple)
        assert len(result) == 2


class TestLangAliasEndToEnd:
    """UI 语言下拉提交中文显示名，规范化必须与 ISO 代码等价。

    WHY：修复前 normalize() 按 zh/en/ja/ko 分支，收到 "中文" 走 else 分支
    打 warning 并原样返回 —— 语种选择端到端零效果，且没有任何测试能发现。
    """

    SAMPLE = "价格涨了50%，比昨天多3.5元"

    def test_display_name_actually_normalizes(self):
        """证明规范化真的发生了，而不是两边都原样返回导致断言空转。"""
        assert normalize_text(self.SAMPLE, "中文") != self.SAMPLE

    def test_display_name_matches_iso_code_result(self):
        assert normalize_text(self.SAMPLE, "中文") == normalize_text(self.SAMPLE, "zh")

    def test_auto_alias_matches_auto_code(self):
        assert normalize_text(self.SAMPLE, "自动检测") == normalize_text(self.SAMPLE, "auto")

    def test_language_without_normalizer_returns_text_unchanged(self):
        """无专用实现的语言（德语等）应原样返回而非抛错。"""
        assert normalize_text("Hello world", "德语") == "Hello world"
