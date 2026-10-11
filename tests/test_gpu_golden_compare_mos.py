"""scripts/gpu_golden_compare.py 的 MOS 感知评分口径单测（NEXT 评估 P0-1）。

只测纯判定逻辑（main 的退出码与 junit 内容），不依赖 GPU/服务：
构造合成 measured/ref JSON + 临时 junit 路径即可。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

from gpu_golden_compare import main  # noqa: E402


class TestGpuGoldenCompareMos:
    """golden 对比的 MOS 可选口径。"""

    def _write(self, path: Path, payload: dict) -> Path:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def _ref(self, with_mos: bool = True) -> dict:
        ref = {
            "engine": "voxcpm2",
            "mean_rtf_baseline": 1.6,
            "rtf_regression_warn_pct": 30.0,
            "duration_drift_hard_pct": 20.0,
            "items": [
                {"idx": 1, "audio_sha256": "a" * 16, "audio_duration_sec": 3.0},
            ],
        }
        if with_mos:
            ref["mean_mos_baseline"] = 3.5
            ref["mos_regression_hard_pct"] = 10.0
            ref["items"][0]["mos_score"] = 3.5
        return ref

    def _meas(self, mean_mos: float | None = None, item_mos: float | None = None) -> dict:
        m = {
            "all_ok": True,
            "mean_rtf": 1.5,
            "results": [
                {"idx": 1, "status": 200, "audio_sha256": "a" * 16, "audio_duration_sec": 3.0},
            ],
        }
        if mean_mos is not None:
            m["mean_mos"] = mean_mos
        if item_mos is not None:
            m["results"][0]["mos_score"] = item_mos
        return m

    def _run(self, tmp_path: Path, ref: dict, meas: dict) -> tuple[int, str]:
        ref_p = self._write(tmp_path / "ref.json", ref)
        meas_p = self._write(tmp_path / "meas.json", meas)
        junit = tmp_path / "out.junit.xml"
        old = sys.argv
        try:
            sys.argv = [
                "gpu_golden_compare.py",
                "--measured",
                str(meas_p),
                "--ref",
                str(ref_p),
                "--junit",
                str(junit),
            ]
            rc = main()
        finally:
            sys.argv = old
        return rc, junit.read_text(encoding="utf-8") if junit.exists() else ""

    def test_mos_regression_is_red(self, tmp_path):
        """mean MOS 劣化超阈值 → 退出码 2（红）。"""
        rc, _ = self._run(tmp_path, self._ref(), self._meas(mean_mos=3.0, item_mos=3.5))
        assert rc == 2

    def test_mos_within_tolerance_green(self, tmp_path):
        """mean MOS 未劣化 → 退出码 0。"""
        rc, _ = self._run(tmp_path, self._ref(), self._meas(mean_mos=3.6, item_mos=3.5))
        assert rc == 0

    def test_mos_slight_drop_warns_not_red(self, tmp_path):
        """mean MOS 略降（未超阈值）→ 退出码 0 + junit 软警告。"""
        rc, junit = self._run(tmp_path, self._ref(), self._meas(mean_mos=3.4, item_mos=3.5))
        assert rc == 0
        assert "MOS 略降" in junit

    def test_mos_missing_in_measured_warns_not_red(self, tmp_path):
        """ref 声明 MOS 基线但 measured 无 mean_mos → 软警告、不红（不编造）。"""
        rc, junit = self._run(tmp_path, self._ref(), self._meas())
        assert rc == 0
        assert "无 mean_mos" in junit

    def test_no_mos_baseline_no_effect(self, tmp_path):
        """ref 未声明 MOS 基线（现状）→ 完全不影响判定，退出码 0。"""
        rc, _ = self._run(tmp_path, self._ref(with_mos=False), self._meas(mean_mos=3.0))
        assert rc == 0

    def test_per_item_mos_drop_is_soft_warning(self, tmp_path):
        """逐条 MOS 下降只软警告，不红（均值口径才硬）。"""
        rc, junit = self._run(tmp_path, self._ref(), self._meas(mean_mos=3.5, item_mos=2.8))
        assert rc == 0
        assert "MOS 下降" in junit
