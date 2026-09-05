"""GPU 容量/MTTR 基线门禁（运维稳定性评估 P0-3）。

用法::

    python scripts/gpu_baseline_gate.py <cold-start-json> [--baseline baselines/gpu-mttr-baseline.json]

读入 ``perf/cold-start.py`` 产出的一份 cold-start JSON，与仓库基线文件比对：
    - 就绪性：``ping_status`` 与 ``readyz_status`` 必须都为 200（冷启动必须真就绪）；
    - 阈值：``engine_load_ms`` 与 ``total_cold_start_ms`` 不得超过
      ``基线值 × tolerance_ratio``，否则视为性能回归。

退出码：0=通过，1=未就绪或超阈值（阻断），2=输入/基线文件错误。

Why 独立脚本而非 CI 内联 heredoc：可单测、可在本地一键复现门禁判定，
符合 AGENTS.md「引用前跑一次检查、可执行路径必须真实存在」的证据绑定铁律。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 需要比对的耗时字段（毫秒）及其基线键（同名）
_GATE_KEYS: tuple[str, ...] = ("engine_load_ms", "total_cold_start_ms")


def load_json(path: Path) -> dict:
    """读取 JSON 文件为字典，失败抛异常由调用方处理。

    用 utf-8-sig：容忍 Windows 工具可能写入的 BOM；对无 BOM 文件同样安全。
    """
    with path.open(encoding="utf-8-sig") as f:
        return json.load(f)


def evaluate(latest: dict, base: dict) -> tuple[bool, list[str]]:
    """执行门禁判定。

    Args:
        latest: cold-start.py 产出的实测结果字典。
        base: 基线字典（含各键基线值与 tolerance_ratio）。

    Returns:
        (passed, report_lines): passed=True 表示全部通过。
    """
    lines: list[str] = []
    passed = True

    ping = latest.get("ping_status")
    readyz = latest.get("readyz_status")
    if ping != 200 or readyz != 200:
        passed = False
        lines.append(f"FAIL: 冷启动未就绪 ping_status={ping} readyz_status={readyz}（应均为 200）")
    else:
        lines.append(f"OK: 就绪性通过 ping={ping} readyz={readyz}")

    tol = float(base.get("tolerance_ratio", 1.5))
    for key in _GATE_KEYS:
        if key not in base or key not in latest:
            passed = False
            lines.append(f"FAIL: 缺少比对键 {key}（base={key in base} latest={key in latest}）")
            continue
        b = float(base[key])
        v = float(latest[key])
        thr = b * tol
        if v <= thr:
            lines.append(f"OK: {key} 实测 {v:.0f}ms ≤ 阈值 {thr:.0f}ms（基线 {b:.0f}ms × {tol}）")
        else:
            passed = False
            lines.append(f"FAIL: {key} 实测 {v:.0f}ms > 阈值 {thr:.0f}ms（基线 {b:.0f}ms × {tol}）性能回归")

    return passed, lines


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 参数列表（默认取 sys.argv[1:]）。

    Returns:
        int: 进程退出码（0 通过 / 1 阻断 / 2 用法错误）。
    """
    parser = argparse.ArgumentParser(description="GPU 冷启动基线门禁")
    parser.add_argument("latest", help="perf/cold-start.py 产出的 cold-start_*.json 路径")
    parser.add_argument(
        "--baseline",
        default="baselines/gpu-mttr-baseline.json",
        help="基线 JSON 路径（默认 baselines/gpu-mttr-baseline.json）",
    )
    args = parser.parse_args(argv)

    try:
        latest = load_json(Path(args.latest))
        base = load_json(Path(args.baseline))
    except (OSError, ValueError) as exc:
        print(f"ERROR: 读取输入/基线失败: {exc}", file=sys.stderr)
        return 2

    passed, lines = evaluate(latest, base)
    for line in lines:
        print(line)
    print("\n门禁结果:", "PASS ✅" if passed else "FAIL ❌")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
