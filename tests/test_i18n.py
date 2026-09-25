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


class TestVocabConsistency:
    """5 份词表 key 集合必须一致。

    ``docs/agents/CODE_STYLE.md`` §8.3 第 3 步原先写"本仓暂无自动校验脚本，
    需人工比对"，而 AGENTS.md 自检清单又要求"新增 key 已完成 5 种语言同步"——
    即一条没有机器判据的铁律。本类就是补上的那条判据。
    """

    _FILES = ("zh.json", "zh-tw.json", "en.json", "ja.json", "ko.json")

    def _load_all(self) -> dict[str, set[str]]:
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "app" / "integrated_app" / "locales"
        return {f: set(json.loads((root / f).read_text(encoding="utf-8"))) for f in self._FILES}

    def test_all_five_vocab_have_identical_key_sets(self) -> None:
        """任一语言缺 key，界面就会露出英文原串（两层回退的最后一层）。"""
        import pytest

        sets = self._load_all()
        assert all(s for s in sets.values()), "有词表是空的，本断言会空转"
        reference = sets["en.json"]
        diffs = {
            f: {"missing": sorted(reference - s), "extra": sorted(s - reference)}
            for f, s in sets.items()
            if s != reference
        }
        if diffs:
            pytest.fail(f"词表 key 集合不一致：{diffs}")

    def test_no_empty_translation_values(self) -> None:
        """有 key 但值是空串，比缺 key 更糟：两层回退都不会触发。"""
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "app" / "integrated_app" / "locales"
        blanks: dict[str, list[str]] = {}
        for name in self._FILES:
            data = json.loads((root / name).read_text(encoding="utf-8"))
            bad = sorted(k for k, v in data.items() if isinstance(v, str) and not v.strip())
            if bad:
                blanks[name] = bad
        assert not blanks, f"存在空翻译值：{blanks}"
