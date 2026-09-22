"""`Performance Benchmark` 门禁的判据测试（issue #118）。

测的是 **workflow 里那段脚本本身** —— 用 yaml 解析出 `run` 内容、剥掉 heredoc 再执行，
不是抄一份逻辑到测试里（抄的会漂，真实那段改了测试却还绿，是最坏的一种假安全）。

钉住的四条行为，逐条对应 issue #118 里实测到的毛病：
- 同一份代码在 runner 之间 median 摆 30% 不能判红（旧阈值 20% 会随机红）；
- 真回退要红，并且**点名是哪一项、从多少到多少**（旧逻辑把输出全重定向走，只留 exit 1）；
- 变快不能算失败（pytest-benchmark 的 compare-fail 是双向的）；
- 拿不到 main 基线时只记录、不炸；但**本轮结果自己没存下来要硬失败**（那是流程问题，不能静默）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_WF = _ROOT / ".github" / "workflows" / "benchmark.yml"

_MINE = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
_BASE = "f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3b2a10987"

_BASE_MED = {"test_alpha": 1.0, "test_beta": 2.0}


def _gate_script() -> str:
    """抽出 regression gate 步骤里 heredoc 中的 python 原文。"""
    doc = yaml.safe_load(_WF.read_text(encoding="utf-8"))
    steps = doc["jobs"]["benchmark"]["steps"]
    step = next((s for s in steps if "regression gate" in (s.get("name") or "")), None)
    assert step, "benchmark.yml 里找不到 regression gate 步骤（被删了？那 issue #118 还开着）"
    body = step["run"]
    head = "python - <<'PY'\n"
    assert body.startswith(head) and body.rstrip().endswith("PY"), "heredoc 形状变了，抽取逻辑要跟着改"
    script = body[len(head) :].rstrip()[: -len("PY")].rstrip()
    for marker in ("median", "变快不判失败", "拿不到任何基线", "L3 仓内基线"):
        assert marker in script, f"抽出来的脚本缺标记 {marker!r} —— 抽到的是旧逻辑？"
    return script


def _repo_baseline(tmp_path: Path, meds: dict[str, float]) -> Path:
    """写一份 L3 仓内基线：形状必须是 export_benchmark_baseline.py 的产物
    （benchmarks 嵌在 {"benchmark": {...}} 里，而不是 storage JSON 的顶层）。
    这个差别就是"读到 0 条 → 假装通过"与真对比的分界。"""
    d = tmp_path / "benchmarks"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "benchmark": {
            "name": "main-test",
            "benchmarks": [
                {"name": k, "stats": {"median": v, "mean": v, "stddev": 0.0, "min": v, "max": v}}
                for k, v in meds.items()
            ],
        }
    }
    (d / "baseline.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def _storage(tmp_path: Path, cur: dict[str, float], *, with_baseline: bool = True, with_current: bool = True) -> Path:
    d = tmp_path / "output" / "benchmarks" / "Linux-CPython-3.12-64bit"
    d.mkdir(parents=True, exist_ok=True)

    def dump(name: str, meds: dict[str, float]) -> None:
        payload = {
            "benchmarks": [
                {"name": k, "stats": {"median": v, "mean": v, "stddev": 0.0, "min": v, "max": v}}
                for k, v in meds.items()
            ]
        }
        (d / name).write_text(json.dumps(payload), encoding="utf-8")

    if with_baseline:
        dump(f"0001_ci-{_BASE}.json", _BASE_MED)
    if with_current:
        dump(f"0002_ci-{_MINE}.json", cur)
    return tmp_path


def _run(tmp_path: Path, *, gate: str = "50") -> subprocess.CompletedProcess[str]:
    # PYTHONIOENCODING 不是冗余：Windows 上子进程的 stdout 默认走 GBK，
    # 父进程按 utf-8 解码就把门禁打印的中文全变成 \ufffd —— 4 条断言只在本地假红。
    env = dict(os.environ, SHA=_MINE, GATE_PCT=gate, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run(
        [sys.executable, "-c", _gate_script()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )


def test_runner_noise_does_not_redden_the_gate(tmp_path: Path) -> None:
    """±30% 的抖动是这台机器的地板（实测 17%–80%），必须放行。"""
    res = _run(_storage(tmp_path, {"test_alpha": 1.3, "test_beta": 1.4}))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "没有项变慢超过 50%" in res.stdout


def test_real_regression_reddens_and_names_the_benchmark(tmp_path: Path) -> None:
    res = _run(_storage(tmp_path, {"test_alpha": 3.0, "test_beta": 2.0}))
    out = res.stdout + res.stderr
    assert res.returncode == 1, out
    assert "test_alpha" in out and "+200.0%" in out, f"红得不具体：{out[:300]}"
    assert "::error::" in out


def test_getting_faster_is_not_a_failure(tmp_path: Path) -> None:
    res = _run(_storage(tmp_path, {"test_alpha": 0.05, "test_beta": 0.1}))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "变快不判失败" in res.stdout


def test_missing_main_baseline_records_without_failing(tmp_path: Path) -> None:
    res = _run(_storage(tmp_path, {"test_alpha": 9.9, "test_beta": 9.9}, with_baseline=False))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "拿不到任何基线" in res.stdout


def test_l3_repo_baseline_catches_regression_when_cache_is_gone(tmp_path: Path) -> None:
    """L1 缓存不可靠是有账的：仓库缓存配额 10 GB 已用 10.23 GB，main 存的基线两小时后就被驱逐，
    PR 侧 restore 直接 "Cache not found"。所以缓存缺席时必须退到仓内基线并**真的对比**。"""
    wd = _storage(tmp_path, {"test_alpha": 3.0, "test_beta": 2.0}, with_baseline=False)
    _repo_baseline(wd, _BASE_MED)
    res = _run(wd)
    out = res.stdout + res.stderr
    assert res.returncode == 1, f"缓存缺席 + 有仓内基线，却放过了 +200% 的回退：{out[:400]}"
    assert "基线来源：L3 仓内基线" in out, out[:400]
    assert "test_alpha" in out and "+200.0%" in out


def test_l3_repo_baseline_passes_within_noise(tmp_path: Path) -> None:
    wd = _storage(tmp_path, {"test_alpha": 1.3, "test_beta": 2.4}, with_baseline=False)
    _repo_baseline(wd, _BASE_MED)
    res = _run(wd)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "基线来源：L3 仓内基线" in res.stdout


def test_placeholder_baseline_is_called_out_not_treated_as_clean(tmp_path: Path) -> None:
    """`benchmarks/baseline.json` 长期是个 0 条的占位文件。占位 ≠ 通过：要单独喊出来，
    否则"三级存储"看起来都在，实际一条都没比过。"""
    wd = _storage(tmp_path, {"test_alpha": 9.9, "test_beta": 9.9}, with_baseline=False)
    _repo_baseline(wd, {})
    res = _run(wd)
    out = res.stdout + res.stderr
    assert res.returncode == 0, out
    assert "::warning::" in out and "占位文件" in out, f"空基线没有被指出来：{out[:300]}"
    assert "拿不到任何基线" in out


def test_missing_current_run_is_a_hard_failure(tmp_path: Path) -> None:
    """本轮自己没存下来 = 流程坏了，不能像旧版那样静默当成"无事发生"。"""
    res = _run(_storage(tmp_path, {}, with_current=False))
    assert res.returncode == 1, res.stdout + res.stderr
    assert "本轮结果没有存下来" in res.stdout + res.stderr


def test_gate_threshold_is_not_tuned_back_to_noise_level() -> None:
    """把阈值写回 20% 就等于把 issue #118 请回来：这条守卫要求它 ≥ 40。"""
    doc = yaml.safe_load(_WF.read_text(encoding="utf-8"))
    step = next(s for s in doc["jobs"]["benchmark"]["steps"] if "regression gate" in (s.get("name") or ""))
    gate = float(step["env"]["GATE_PCT"])
    assert gate >= 40, f"GATE_PCT={gate}：同一份代码的 runner 间摆动实测到 80%，这个阈值只会随机红"
    # 裸前缀 restore-key 会让 PR 命中任意分支的缓存（自己和自己比）—— 不许回来
    assert "            benchmark-storage-\n" not in _WF.read_text(encoding="utf-8"), (
        "restore-keys 里又出现了裸前缀 `benchmark-storage-`，会命中任意分支缓存"
    )


@pytest.mark.parametrize("marker", ["median", "GATE_PCT"])
def test_gate_still_reads_median_and_is_configurable(marker: str) -> None:
    assert marker in _WF.read_text(encoding="utf-8"), f"门禁不再使用 {marker}，本测试的假设要更新"


def test_repo_baseline_file_is_not_a_placeholder() -> None:
    """`benchmarks/baseline.json` 从 2026-09-22 起是门禁的兜底基线，就不再允许是占位文件。
    这条钉三件事：至少有 1 条、median 全为正、形状仍是 export 脚本的 `{"benchmark": {...}}`
    （门禁的读取器认两种形状，但仓内这份必须是其中一种，否则等于没基线）。"""
    path = _ROOT / "benchmarks" / "baseline.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    body = data.get("benchmark") or data
    items = body.get("benchmarks") or []
    assert items, (
        "benchmarks/baseline.json 又是 0 条 —— 那是占位。用 scripts/export_benchmark_baseline.py "
        "从一次 main 的 benchmark 结果填它（见 benchmarks/README.md），别让它假装成三级存储。"
    )
    assert body.get("name"), "基线没有 name，回看时不知道它是哪次 run 锚的"
    bad = {b["name"]: b["stats"]["median"] for b in items if not b["stats"]["median"] > 0}
    assert not bad, f"基线里有非正 median：{bad}"
    missing = [b["name"] for b in items if "median" not in b.get("stats", {})]
    assert not missing, f"基线条目缺 stats.median：{missing}"
