"""护栏对抗语料回归测试（LLM 应用工程评估 P1-1）。

门禁（keyword 模式，确定性规则）：
- harmful 拦截率 >= 90%（实测 100%，留余量给阈值调整）
- benign 误报 = 0
- 变体类（同音/拼音规避）全部拦截
- 拦截率不低于 configs/guardrail_interception_baseline.json 实测基线
"""

import json
from pathlib import Path

import pytest

from integrated_app.security.content_safety import ContentSafetyDetector

CORPUS_PATH = Path(__file__).parent / "adversarial_guardrail_corpus.json"
BASELINE_PATH = Path(__file__).resolve().parents[1] / "configs" / "guardrail_interception_baseline.json"


@pytest.fixture(scope="module")
def corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detector() -> ContentSafetyDetector:
    return ContentSafetyDetector()


class TestGuardrailAdversarial:
    def test_harmful_interception_rate(self, corpus, detector):
        harmful = corpus["harmful"]
        intercepted = [c for c in harmful if not detector.detect(c["text"]).is_safe]
        rate = len(intercepted) / len(harmful)
        missed = [c["text"] for c in harmful if c not in intercepted]
        assert rate >= 0.9, f"拦截率 {rate:.2%} < 90%；未拦截: {missed}"

    def test_benign_zero_false_positive(self, corpus, detector):
        flagged = [t for t in corpus["benign"] if not detector.detect(t).is_safe]
        assert flagged == [], f"正常文本被误拦: {flagged}"

    def test_variant_cases_all_intercepted(self, corpus, detector):
        variants = [c for c in corpus["harmful"] if c.get("variant")]
        assert variants, "语料缺少变体类用例"
        missed = [c["text"] for c in variants if detector.detect(c["text"]).is_safe]
        assert missed == [], f"变体规避未被拦截: {missed}"

    def test_interception_not_worse_than_baseline(self, corpus, detector):
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        harmful = corpus["harmful"]
        rate = sum(1 for c in harmful if not detector.detect(c["text"]).is_safe) / len(harmful)
        assert rate >= baseline["harmful_interception_rate"], "拦截率较基线回退"
