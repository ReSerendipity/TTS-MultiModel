"""FTS5 中文分词改进模块。

使用 jieba 对中文关键词进行分词预处理，提升 FTS5 全文搜索的召回率。

当前 history_db 使用 trigram 分词器，对 >=3 字符的子串匹配良好，
但对中文短语分词不友好（"语音合成" 会匹配 "语音合" 和 "音合成"
而非语义化的 "语音" + "合成"）。

本模块提供预处理函数，将中文关键词分词后构建更精确的 FTS5 查询。

使用方式（在 history_db.py 的 _build_fts_query 中调用）::

    from .fts_tokenizer import tokenize_search_keyword
    fts_query = tokenize_search_keyword(keyword)
"""

from __future__ import annotations

import logging

logger = logging.getLogger("tts_multimodel")

# 延迟导入 jieba（可选依赖）
_jieba_available: bool | None = None


def _check_jieba() -> bool:
    """检查 jieba 是否可用（缓存结果）。"""
    global _jieba_available
    if _jieba_available is None:
        try:
            import jieba  # noqa: F401

            _jieba_available = True
        except ImportError:
            _jieba_available = False
            logger.debug("[FTS分词] jieba 不可用，中文搜索使用 trigram 回退")
    return _jieba_available


def tokenize_chinese(text: str) -> list[str]:
    """对中文文本进行 jieba 分词。

    Args:
        text: 待分词的中文文本。

    Returns:
        分词后的词语列表。jieba 不可用时返回原始文本的单元素列表。
    """
    if not text or not _check_jieba():
        return [text] if text else []

    try:
        import jieba

        # 精确模式分词
        words = list(jieba.cut(text, cut_all=False))
        # 过滤空白和单字符（trigram 已处理）
        words = [w.strip() for w in words if w.strip() and len(w.strip()) >= 2]
        return words
    except Exception as e:
        logger.debug("[FTS分词] jieba 分词失败，回退原始文本: %s", e)
        return [text]


def build_segmented_fts_query(keyword: str) -> str:
    """构建基于分词的 FTS5 查询字符串。

    对中文关键词进行 jieba 分词后，将每个词作为独立 phrase 用 OR 连接，
    提升搜索召回率。非中文或 jieba 不可用时回退到原始 trigram phrase。

    Args:
        keyword: 用户原始搜索关键词。

    Returns:
        FTS5 MATCH 查询字符串。
    """
    if not keyword:
        return '""'

    # 检测是否包含中文字符
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in keyword)

    if not has_cjk or not _check_jieba():
        # 非中文或无 jieba：使用原始 trigram phrase
        return '"' + keyword.replace('"', '""') + '"'

    # 中文关键词：分词后构建 OR 查询
    words = tokenize_chinese(keyword)
    if not words:
        return '"' + keyword.replace('"', '""') + '"'

    # 每个分词作为 phrase，用 OR 连接
    phrases = ['"' + w.replace('"', '""') + '"' for w in words]

    # 如果分词后只有一个词，直接返回
    if len(phrases) == 1:
        return phrases[0]

    # 多词用 OR 连接
    return " OR ".join(phrases)
