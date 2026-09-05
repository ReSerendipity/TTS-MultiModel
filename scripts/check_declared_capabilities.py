#!/usr/bin/env python3
"""能力声明 → 能力验证 门禁（综合评估 P0-1 主线 / 必答问题①落点）。

项目曾在 4 处「声明能力但缺验证证据」：
    1. 引擎声明 3 实现 2（已由 scripts/check_engine_specs.py 三向校验闭环）
    2. 有 training/ 模块 —— 本脚本核对「训练产物是否存在」
    3. 声明 SLO 99.5/99/30s —— 本脚本核对「性能基线产物是否存在」
    4. 测试 1761 用例 —— 本脚本核对「覆盖率与 fail_under 缓冲」

判定口径（延续总纲 §4 ①：已验证 / 未验证 / 已证伪）：
    - WARN 不阻断：产物缺失代表「未验证」而非故障（本地单人开发常无训练产物
      与基准），阻断会让门禁天天红、最终被 --no-verify 绕过。
    - --strict 时任一 WARN 升级为 FAIL（退出码 1），供 CI 的「发布前」job 使用。
    - 引擎三向一致性不在本脚本重复（见 check_engine_specs.py）。

用法：
    python scripts/check_declared_capabilities.py
    python scripts/check_declared_capabilities.py --strict
    python scripts/check_declared_capabilities.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


def _reexec_with_venv_python() -> None:
    venv_python = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
        os.execv(str(venv_python), [str(venv_python), __file__, *sys.argv[1:]])


_reexec_with_venv_python()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: 训练产物识别（LoRA/微调权重落盘格式）
_TRAINING_ARTIFACT_EXTS = {".safetensors", ".bin", ".ckpt", ".pt", ".pth"}

#: SLO 基线产物来源目录（CI 落点 output/benchmarks（benchmark.yml 持久化）；
#: 本地落点 perf/results（cold-start/generation-benchmark）；.benchmarks 为历史占位）
_BASELINE_DIRS = ("perf/results", "output/benchmarks", ".benchmarks")


class CheckResult:
    def __init__(self, name: str, status: str, detail: str = "") -> None:
        self.name = name
        self.status = status  # OK / WARN / FAIL
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        d = {"name": self.name, "status": self.status}
        if self.detail:
            d["detail"] = self.detail
        return d


def _load_yaml_text() -> str:
    return (_PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")


def _check_version_consistency(results: list[CheckResult]) -> None:
    """pyproject / config.yaml / CHANGELOG 顶号版本三向一致。"""
    try:
        import tomllib
    except ImportError:  # pragma: no cover - py3.11+ 恒有 tomllib
        tomllib = None  # type: ignore[assignment]
    try:
        if tomllib is not None:
            pyproject = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
            pp_ver = (pyproject.get("project") or {}).get("version", "")
        else:  # pragma: no cover - 老解释器兜底
            pp_ver = re.search(
                r'^version\s*=\s*"([^"]+)"',
                (_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
                re.M,
            ).group(1)
    except Exception as e:  # noqa: BLE001
        results.append(CheckResult("version.consistency", "WARN", f"pyproject 解析失败: {e}"))
        return

    cfg_ver = (re.search(r'^version:\s*"([^"]+)"', _load_yaml_text(), re.M) or [None, ""])[1]
    changelog = (_PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8", errors="replace")
    cl_match = re.search(r"^## \[([^\]]+)\]\(", changelog, re.M)
    cl_ver = cl_match.group(1) if cl_match else ""

    vers = {"pyproject": pp_ver, "config.yaml": cfg_ver, "CHANGELOG": cl_ver}
    uniq = {v for v in vers.values() if v}
    if len(uniq) == 1 and "" not in uniq:
        results.append(CheckResult("version.consistency", "OK", f"三方一致 v{pp_ver}"))
    else:
        results.append(
            CheckResult(
                "version.consistency",
                "WARN",
                f"版本声明不一致: {vers}（release-please 只同步 pyproject/CHANGELOG，"
                "config.yaml 需人工补齐，见 GOTCHAS #9）",
            )
        )


def _check_training_artifacts(results: list[CheckResult]) -> None:
    """训练模块声明 vs 产物证据（未验证 = 能力声明缺最后一步验证）。"""
    training_route = _PROJECT_ROOT / "app" / "integrated_app" / "routes" / "training.py"
    lora_dir = _PROJECT_ROOT / "lora"
    declared = training_route.exists()

    artifacts: list[Path] = []
    if lora_dir.exists():
        artifacts = [p for p in lora_dir.rglob("*") if p.is_file() and p.suffix.lower() in _TRAINING_ARTIFACT_EXTS]

    if not declared:
        results.append(
            CheckResult("training.artifacts", "OK", "无训练入口（routes/training.py 不存在），不涉及产物校验")
        )
        return
    if artifacts:
        detail = f"lora/ 发现 {len(artifacts)} 个训练产物（如 {artifacts[0].name}）"
        results.append(CheckResult("training.artifacts", "OK", detail))
    else:
        results.append(
            CheckResult(
                "training.artifacts",
                "WARN",
                "存在训练入口 routes/training.py 但 lora/ 无任何训练产物（.safetensors/.pt 等）"
                "——训练能力声明「未验证」，需实际跑通一次 LoRA 微调左移验证",
            )
        )


def _check_slo_baseline(results: list[CheckResult]) -> None:
    """SLO 声明 vs 性能基线产物（未验证 = 无历史基线佐证 99.5/99/30s 目标）。"""
    declared = "observability" in _load_yaml_text() and "slo" in _load_yaml_text()

    baseline_files: list[Path] = []
    for d in _BASELINE_DIRS:
        p = _PROJECT_ROOT / d
        if p.exists():
            baseline_files.extend(
                [
                    f
                    for f in p.rglob("*")
                    if f.is_file()
                    and f.name != ".gitkeep"
                    and f.suffix.lower() in {".json", ".csv", ".md", ".txt", ".html"}
                ]
            )

    if not declared:
        results.append(CheckResult("slo.baseline", "OK", "observability.slo 未声明，跳过"))
        return
    if baseline_files:
        results.append(
            CheckResult(
                "slo.baseline",
                "OK",
                f"性能基线产物 {len(baseline_files)} 个（如 perf/results/{baseline_files[0].name}）",
            )
        )
    else:
        results.append(
            CheckResult(
                "slo.baseline",
                "WARN",
                "observability.slo 声明了 99.5%/99%/30s 目标，但 perf/results 与 .benchmarks "
                "均无基线产物——SLO 声明「未验证」，建议接 perf/generation-benchmark.py 落库",
            )
        )


def _check_coverage_buffer(results: list[CheckResult]) -> None:
    """覆盖率门禁缓冲（fail_under vs 实测 line-rate，<5pp 提示收紧风险）。"""
    try:
        cfg_text = (_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        fail_under = re.search(r"fail_under\s*=\s*([0-9.]+)", cfg_text)
        if fail_under is None:
            return
        gate = float(fail_under.group(1))
    except Exception:  # noqa: BLE001
        return

    cov_file = _PROJECT_ROOT / "coverage.xml"
    if not cov_file.exists():
        results.append(CheckResult("coverage.buffer", "WARN", f"coverage.xml 不存在，无法校验 fail_under={gate} 缓冲"))
        return
    try:
        import xml.etree.ElementTree as ET

        root = ET.parse(cov_file).getroot()
        klass = [c for p in root.iter("package") for c in p.iter("class")]
        tl = 0
        cl = 0
        for c in klass:
            lines = c.find("lines")
            nl = len(list(lines)) if lines is not None else 0
            tl += nl
            cl += nl * float(c.get("line-rate", "0"))
        line_rate = 100.0 * cl / tl if tl else 0.0
        buffer_pp = line_rate - gate
        if buffer_pp >= 5.0:
            results.append(
                CheckResult("coverage.buffer", "OK", f"line={line_rate:.2f}% gate={gate}% 缓冲 {buffer_pp:.2f}pp")
            )
        else:
            results.append(
                CheckResult(
                    "coverage.buffer",
                    "WARN",
                    f"line={line_rate:.2f}% gate={gate}% 缓冲仅 {buffer_pp:.2f}pp（<5pp）"
                    "——任何一次路径收缩都可能红，建议设覆盖率棘轮只增不减",
                )
            )
    except Exception as e:  # noqa: BLE001
        results.append(CheckResult("coverage.buffer", "WARN", f"coverage.xml 解析失败: {e}"))


def run_all_checks() -> list[CheckResult]:
    results: list[CheckResult] = []
    _check_version_consistency(results)
    _check_training_artifacts(results)
    _check_slo_baseline(results)
    _check_coverage_buffer(results)
    return results


def format_text(results: list[CheckResult]) -> str:
    lines = ["=== TTS_MultiModel 能力声明→验证 门禁 ===", ""]
    for r in results:
        tag = f"[{r.status}]"
        lines.append(f"{tag} {r.name.ljust(32)} {r.detail}")
    lines.append("---")
    ok = sum(1 for r in results if r.status == "OK")
    warn = sum(1 for r in results if r.status == "WARN")
    fail = sum(1 for r in results if r.status == "FAIL")
    lines.append(f"总计: {len(results)} 项 | OK={ok} WARN={warn} FAIL={fail}")
    lines.append("口径: WARN=未验证（不阻断）; --strict 时 WARN 升级为 FAIL")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="能力声明→验证 门禁（WARN 不阻断）")
    parser.add_argument("--strict", action="store_true", help="把 WARN 升级为 FAIL（供 CI 发布前使用）")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    results = run_all_checks()
    if args.json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
    else:
        print(format_text(results))

    if args.strict:
        return 1 if any(r.status in ("WARN", "FAIL") for r in results) else 0
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
