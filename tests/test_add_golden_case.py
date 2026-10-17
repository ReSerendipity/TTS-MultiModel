"""坏案例→金标准回流工具测试（LLM 应用工程评估 P1-2）。"""

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import add_golden_case  # noqa: E402


def _write(path: Path, cases: list[dict]) -> None:
    path.write_text(json.dumps({"cases": cases}, ensure_ascii=False, indent=2), encoding="utf-8")


class TestAppendGoldenCase:
    def test_append_assigns_next_idx(self, tmp_path):
        f = tmp_path / "cases.json"
        _write(f, [{"idx": 3, "text": "旧", "voice": "alloy", "model": "tts-1", "engine": "voxcpm2"}])
        ok, msg = add_golden_case.append_case(
            f, {"text": "坏案例", "voice": "韩立", "model": "tts-1", "engine": "voxcpm2"}
        )
        assert ok, msg
        data = json.loads(f.read_text(encoding="utf-8"))
        assert len(data["cases"]) == 2
        assert data["cases"][-1]["idx"] == 4
        assert data["cases"][-1]["text"] == "坏案例"

    def test_duplicate_rejected(self, tmp_path):
        f = tmp_path / "cases.json"
        case = {"idx": 1, "text": "重复", "voice": "alloy", "model": "tts-1", "engine": "voxcpm2"}
        _write(f, [case])
        before = f.read_text(encoding="utf-8")
        ok, _msg = add_golden_case.append_case(
            f, {"text": "重复", "voice": "alloy", "model": "tts-1", "engine": "voxcpm2"}
        )
        assert not ok
        assert f.read_text(encoding="utf-8") == before

    def test_dry_run_no_write(self, tmp_path):
        f = tmp_path / "cases.json"
        _write(f, [])
        before = f.read_text(encoding="utf-8")
        ok, _msg = add_golden_case.append_case(
            f, {"text": "dry", "voice": "alloy", "model": "tts-1", "engine": "voxcpm2"}, dry_run=True
        )
        assert ok
        assert f.read_text(encoding="utf-8") == before

    def test_empty_text_rejected(self, tmp_path):
        f = tmp_path / "cases.json"
        _write(f, [])
        ok, _msg = add_golden_case.append_case(
            f, {"text": "  ", "voice": "alloy", "model": "tts-1", "engine": "voxcpm2"}
        )
        assert not ok

    def test_main_writes_note(self, tmp_path, monkeypatch, capsys):
        f = tmp_path / "cases.json"
        _write(f, [])
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "add_golden_case.py",
                "--text",
                "回流案例",
                "--voice",
                "御姐",
                "--engine",
                "voxcpm2",
                "--file",
                str(f),
                "--note",
                "线上退化",
            ],
        )
        assert add_golden_case.main() == 0
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["cases"][0]["note"] == "线上退化"
        assert "idx=1" in capsys.readouterr().out

    def test_main_rejects_duplicate(self, tmp_path, monkeypatch):
        f = tmp_path / "cases.json"
        _write(f, [{"idx": 1, "text": "回流案例", "voice": "御姐", "model": "tts-1", "engine": "voxcpm2"}])
        monkeypatch.setattr(
            sys,
            "argv",
            ["add_golden_case.py", "--text", "回流案例", "--voice", "御姐", "--file", str(f)],
        )
        with pytest.raises(SystemExit) as exc:
            add_golden_case.main()
        assert exc.value.code == 1
