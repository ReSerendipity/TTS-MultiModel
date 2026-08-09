"""text_frontend G2P 处理器单元测试。

覆盖目标模块: bin/integrated_app/text_frontend.py (G2PProcessor / TextFrontend)
"""

from integrated_app.text_frontend import G2PProcessor, TextFrontend


class TestG2PProcessor:
    def setup_method(self):
        self.g2p = G2PProcessor()

    def test_process_empty(self):
        assert self.g2p.process("", "zh") == ""

    def test_process_returns_text(self):
        assert self.g2p.process("你好世界", "zh") == "你好世界"
        assert self.g2p.process("Hello", "en") == "Hello"

    def test_is_available(self):
        assert self.g2p.is_available("zh") is True
        assert self.g2p.is_available("en") is True

    def test_initialize_engine(self):
        assert self.g2p.initialize_engine("zh") is True
        assert "zh" in self.g2p._initialized

    def test_initialize_unsupported_lang(self):
        assert self.g2p.initialize_engine("xx") is False


class TestTextFrontendG2P:
    def test_frontend_process(self):
        frontend = TextFrontend()
        result = frontend.process("你好世界")
        assert isinstance(result, tuple)
        assert len(result) == 2
