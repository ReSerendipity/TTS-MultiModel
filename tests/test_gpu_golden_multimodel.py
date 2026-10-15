"""multimodel 扩展集参与判定入口测试（LLM 应用工程评估 P1-2）。

覆盖：
- gpu_golden_baseline.load_cases 的扩展集合并语义（idx+100，不与主集冲突）
- gpu_golden_compare 的 --ref-multimodel 判定入口 / 缺省 measure-only 语义
- 403（音色未授权声明）按 env-skip 软警告处理，不冒充质量退化
"""

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import gpu_golden_compare  # noqa: E402
from gpu_golden_baseline import load_cases  # noqa: E402


def _json_write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class TestLoadCasesMerge:
    def test_file_plus_extra_reindexed(self, tmp_path):
        main_file = tmp_path / "main.json"
        _json_write(
            main_file,
            {"cases": [{"idx": 1, "text": "主集", "voice": "alloy", "model": "tts-1", "engine": "voxcpm2"}]},
        )
        extra = tmp_path / "extra.json"
        _json_write(
            extra,
            {
                "cases": [
                    {"idx": 3, "text": "扩展甲", "voice": "韩立", "model": "tts-1", "engine": "voxcpm2"},
                    {"idx": 5, "text": "扩展乙", "voice": "alloy", "model": "tts-1", "engine": "voxcpm2"},
                ]
            },
        )
        cases = load_cases(str(main_file), "tts-1", "", extra_path=str(extra))
        assert [c["idx"] for c in cases] == [1, 103, 105]
        assert cases[1]["voice"] == "韩立"

    def test_default_plus_extra(self, tmp_path):
        extra = tmp_path / "extra.json"
        _json_write(extra, {"cases": [{"idx": 1, "text": "扩展", "model": "tts-1"}]})
        cases = load_cases(None, "tts-1", "", extra_path=str(extra))
        assert len(cases) == 11
        assert cases[-1]["idx"] == 101
        assert cases[-1]["voice"] == "alloy"


def _run_compare(
    monkeypatch,
    tmp_path,
    measured: dict | None = None,
    ref_mm: dict | None = None,
    ref_main: dict | None = None,
):
    m = tmp_path / "measured.json"
    r = tmp_path / "ref.json"
    j = tmp_path / "golden.junit.xml"
    _json_write(
        m,
        measured
        or {
            "all_ok": True,
            "mean_rtf": 0.5,
            "results": [
                {"idx": 1, "status": 200, "audio_duration_sec": 2.0, "audio_sha256": "aaa"},
                {"idx": 101, "status": 403, "audio_duration_sec": 0.0, "audio_sha256": "zzz"},
            ],
        },
    )
    _json_write(
        r,
        ref_main
        or {
            "duration_drift_hard_pct": 20.0,
            "mean_rtf_baseline": 0.5,
            "items": [{"idx": 1, "audio_duration_sec": 2.0, "audio_sha256": "aaa"}],
        },
    )
    argv = ["gpu_golden_compare.py", "--measured", str(m), "--ref", str(r), "--junit", str(j)]
    if ref_mm is not None:
        rf = tmp_path / "ref_mm.json"
        _json_write(rf, ref_mm)
        argv += ["--ref-multimodel", str(rf)]
    monkeypatch.setattr(sys, "argv", argv)
    return gpu_golden_compare.main(), j, tmp_path


class TestCompareMultimodelGate:
    def test_ref_multimodel_403_env_skip_not_red(self, tmp_path, monkeypatch, capsys):
        code, junit, _tmp = _run_compare(
            monkeypatch, tmp_path, ref_mm={"items": [{"idx": 101, "audio_duration_sec": 2.0, "audio_sha256": "zzz"}]}
        )
        assert code == 0
        assert "multimodel ref items=1" in capsys.readouterr().out
        assert junit.exists()

    def test_ref_multimodel_hard_fail_on_bad_status(self, tmp_path, monkeypatch):
        measured = {
            "all_ok": True,
            "mean_rtf": 0.5,
            "results": [
                {"idx": 1, "status": 200, "audio_duration_sec": 2.0, "audio_sha256": "aaa"},
                {"idx": 101, "status": 500, "audio_duration_sec": 0.0, "audio_sha256": "zzz"},
            ],
        }
        code, _j, _tmp = _run_compare(
            monkeypatch,
            tmp_path,
            measured=measured,
            ref_mm={"items": [{"idx": 101, "audio_duration_sec": 2.0, "audio_sha256": "zzz"}]},
        )
        assert code == 2

    def test_measure_only_notice_without_ref(self, tmp_path, monkeypatch, capsys):
        code, _j, _tmp = _run_compare(monkeypatch, tmp_path, ref_mm=None)
        assert code == 0
        assert "measure-only" in capsys.readouterr().out
