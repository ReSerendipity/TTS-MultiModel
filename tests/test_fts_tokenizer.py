"""fts_tokenizer 模块单元测试 — FTS5 中文分词预处理。

覆盖目标模块: bin/integrated_app/fts_tokenizer.py
"""

import pytest

from integrated_app.fts_tokenizer import (
    _check_jieba,
    build_segmented_fts_query,
    tokenize_chinese,
)


class TestTokenizeChinese:
    def test_empty_text(self):
        assert tokenize_chinese("") == []
        assert tokenize_chinese(None) == []

    def test_ascii_fallback(self):
        result = tokenize_chinese("hello")
        assert result == ["hello"]

    def test_chinese_text(self):
        if not _check_jieba():
            pytest.skip("jieba 不可用")
        words = tokenize_chinese("语音合成")
        assert len(words) >= 1
        assert all(len(w) >= 2 for w in words)


class TestBuildSegmentedFtsQuery:
    def test_empty_keyword(self):
        assert build_segmented_fts_query("") == '""'

    def test_ascii_uses_trigram(self):
        assert build_segmented_fts_query("hello") == '"hello"'

    def test_quotes_escaped(self):
        assert build_segmented_fts_query('say "hi"') == '"say ""hi"""'

    def test_chinese_jieba_or_query(self):
        if not _check_jieba():
            pytest.skip("jieba 不可用")
        query = build_segmented_fts_query("语音合成技术")
        assert " OR " in query
        assert query.startswith('"')
