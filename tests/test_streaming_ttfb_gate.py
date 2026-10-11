"""scripts/streaming_ttfb_gate.py 门禁判定单测（NEXT 评估 P0-2）。

只测纯判定逻辑（evaluate），不依赖 GPU/服务：构造合成 JSON 输入即可。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

from streaming_ttfb_gate import evaluate  # noqa: E402


class TestStreamingTtfbGate:
    """流式首包延迟门禁判定。"""

    def _base(self) -> dict:
        return {
            "tolerance_ratio": 1.5,
            "ttfb_ms": {"p50": 1000.0, "p95": 2000.0, "mean": 1500.0},
        }

    def test_pass_when_within_tolerance(self):
        latest = {
            "runs_ok": 5,
            "runs_failed": 0,
            "runs": [],
            "ttfb_ms": {"p50": 800.0, "p95": 1800.0, "mean": 1400.0},
        }
        passed, lines = evaluate(latest, self._base())
        assert passed
        assert not any(line.startswith("FAIL") for line in lines)

    def test_fail_when_p95_exceeds_tolerance(self):
        latest = {
            "runs_ok": 5,
            "runs_failed": 0,
            "runs": [],
            "ttfb_ms": {"p50": 800.0, "p95": 4000.0, "mean": 1400.0},
        }
        passed, lines = evaluate(latest, self._base())
        assert not passed
        assert any("p95" in line and line.startswith("FAIL") for line in lines)

    def test_fail_when_any_percentile_exceeds(self):
        """任一百分位超阈值即红（不止 p95）。"""
        latest = {
            "runs_ok": 5,
            "runs_failed": 0,
            "runs": [],
            "ttfb_ms": {"p50": 9000.0, "p95": 1800.0, "mean": 1400.0},
        }
        passed, lines = evaluate(latest, self._base())
        assert not passed
        assert any("p50" in line and line.startswith("FAIL") for line in lines)

    def test_fail_when_measurement_invalid(self):
        """存在失败轮次 = 测量无效，阻断。"""
        latest = {
            "runs_ok": 4,
            "runs_failed": 1,
            "runs": [{"error": "timeout"}],
            "ttfb_ms": {"p50": 800.0, "p95": 1800.0, "mean": 1400.0},
        }
        passed, lines = evaluate(latest, self._base())
        assert not passed
        assert any("失败轮次" in line for line in lines)

    def test_fail_when_key_missing(self):
        """基线或实测缺键 = 判定失败（防静默漏比）。"""
        latest = {
            "runs_ok": 5,
            "runs_failed": 0,
            "runs": [],
            "ttfb_ms": {"p50": 800.0, "p95": 1800.0},
        }
        passed, lines = evaluate(latest, self._base())
        assert not passed
        assert any("缺少比对键" in line for line in lines)
