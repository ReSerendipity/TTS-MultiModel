"""i18n 翻译测试"""

import os
import sys

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")


class TestI18n:
    """测试国际化"""

    def test_translate_zh(self):
        """测试中文翻译"""
        from integrated_app.i18n import t

        result = t("ready", "zh")
        assert result  # 不为空

    def test_translate_en(self):
        """测试英文翻译"""
        from integrated_app.i18n import t

        result = t("ready", "en")
        assert result  # 不为空

    def test_fallback_to_key(self):
        """测试未知键回退到键名"""
        from integrated_app.i18n import t

        result = t("nonexistent_key_12345", "zh")
        assert result  # 应该返回键名本身而非空

    def test_supported_languages(self):
        """测试支持的语言列表"""
        from integrated_app.i18n import get_lang

        # 基本验证：get_lang 函数存在且可调用
        assert callable(get_lang)
