"""scripts/gpu_golden_baseline.py 用例清单解析单测（NEXT 评估 P1-1）。

只测纯解析逻辑（load_cases），不依赖 GPU/服务。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

from gpu_golden_baseline import GOLDEN_TEXTS, load_cases  # noqa: E402


class TestGoldenCasesLoading:
    """golden 用例解析。"""

    def test_default_cases_when_no_file(self):
        """无 --cases 时回退内置 10 条，voice 默认 alloy、engine=voxcpm2。"""
        cases = load_cases(None, "tts-1", "")
        assert len(cases) == len(GOLDEN_TEXTS) == 10
        assert all(c["voice"] == "alloy" for c in cases)
        assert all(c["engine"] == "voxcpm2" for c in cases)
        assert all(c["model"] == "tts-1" for c in cases)

    def test_voice_override_applies_to_default(self):
        """--voice 覆盖默认集的音色。"""
        cases = load_cases(None, "tts-1", "韩立")
        assert all(c["voice"] == "韩立" for c in cases)

    def test_cases_file_parsed(self, tmp_path):
        payload = {
            "cases": [
                {"idx": 7, "text": "中文文本", "voice": "韩立", "model": "tts-1", "engine": "voxcpm2"},
                {"text": "English text", "voice": "alloy", "engine": "voxcpm2"},
            ]
        }
        p = tmp_path / "cases.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        cases = load_cases(str(p), "tts-1", "")
        assert cases[0]["idx"] == 7
        assert cases[0]["voice"] == "韩立"
        assert cases[1]["idx"] == 2  # 未显式 idx 时按顺序
        assert cases[1]["model"] == "tts-1"  # 未显式 model 时用默认

    def test_voice_override_beats_case_value(self, tmp_path):
        payload = {"cases": [{"text": "x", "voice": "alloy"}]}
        p = tmp_path / "cases.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        cases = load_cases(str(p), "tts-1", "御姐")
        assert cases[0]["voice"] == "御姐"

    def test_missing_cases_file_raises(self, tmp_path):
        import pytest

        with pytest.raises(FileNotFoundError):
            load_cases(str(tmp_path / "nope.json"), "tts-1", "")
