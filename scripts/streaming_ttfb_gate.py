#!/usr/bin/env python3
"""流式首包延迟（TTFB）基线门禁（NEXT 评估 P0-2 落地）。

用法::

    python scripts/streaming_ttfb_gate.py <streaming-baseline-json> \
        [--baseline baselines/streaming-ttfb-baseline.json]

读入 ``perf/streaming-baseline.py`` 产出的一份流式基线 JSON，与仓库基线文件比对：
    - 首包延迟（``ttfb_ms`` 的 p50/p95/mean）不得超过 ``基线值 × tolerance_ratio``；
    - 任意一轮失败（``runs_failed > 0`` 或 ``error`` 非空）视为测量无效。

退出码：0=通过，1=超阈值或测量无效（阻断），2=输入/基线文件错误。

Why 独立脚本而非 CI 内联：与 gpu_baseline_gate.py 同口径——可单测、可本地一键复现
门禁判定，符合 AGENTS.md（本地维护、不随仓库分发）「引用前跑一次检查、可执行路径必须真实存在」的铁律。

注意：``baselines/streaming-ttfb-baseline.json`` 为分片修复前的实测基线
（2026-09-23，P95≈91.6s，200 字单段）。分片修复后须在真机重测并更新基线
（GPU 真跑按既有 skip 台账约定记受阻，不强制在本机执行）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 需要比对的百分位键（毫秒），键名与 streaming-baseline.py 输出一致
_GATE_KEYS: tuple[str, ...] = ("p50", "p95", "mean")


def load_json(path: Path) -> dict:
    """读取 JSON 文件为字典，失败抛异常由调用方处理。

    用 utf-8-sig：容忍 Windows 工具可能写入的 BOM；对无 BOM 文件同样安全。
    """
    with path.open(encoding="utf-8-sig") as f:
        return json.load(f)


def evaluate(latest: dict, base: dict) -> tuple[bool, list[str]]:
    """执行门禁判定。

    Args:
        latest: streaming-baseline.py 产出的实测结果字典。
        base: 基线字典（含 ttfb_ms 各键基线与 tolerance_ratio）。

    Returns:
        (passed, report_lines): passed=True 表示全部通过。
    """
    lines: list[str] = []
    passed = True

    runs_failed = int(latest.get("runs_failed", 0))
    error_runs = [r for r in latest.get("runs", []) if r.get("error")]
    if runs_failed > 0 or error_runs:
        passed = False
        lines.append(f"FAIL: 存在失败轮次 runs_failed={runs_failed} error_runs={len(error_runs)}（测量无效）")
    else:
        runs_ok = int(latest.get("runs_ok", 0))
        lines.append(f"OK: 测量有效 runs_ok={runs_ok}")

    latest_ttfb = latest.get("ttfb_ms") or {}
    base_ttfb = base.get("ttfb_ms") or {}
    tol = float(base.get("tolerance_ratio", 1.5))
    for key in _GATE_KEYS:
        if key not in base_ttfb or key not in latest_ttfb:
            passed = False
            lines.append(f"FAIL: 缺少比对键 ttfb_ms.{key}（base={key in base_ttfb} latest={key in latest_ttfb}）")
            continue
        b = float(base_ttfb[key])
        v = float(latest_ttfb[key])
        thr = b * tol
        if v <= thr:
            lines.append(f"OK: TTFB p{key} 实测 {v:.0f}ms ≤ 阈值 {thr:.0f}ms（基线 {b:.0f}ms × {tol}）")
        else:
            passed = False
            lines.append(f"FAIL: TTFB p{key} 实测 {v:.0f}ms > 阈值 {thr:.0f}ms（基线 {b:.0f}ms × {tol}）首包延迟回归")

    return passed, lines


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Returns:
        int: 进程退出码（0 通过 / 1 阻断 / 2 用法错误）。
    """
    parser = argparse.ArgumentParser(description="流式首包延迟基线门禁")
    parser.add_argument("latest", help="perf/streaming-baseline.py 产出的 streaming-baseline_*.json 路径")
    parser.add_argument(
        "--baseline",
        default="baselines/streaming-ttfb-baseline.json",
        help="基线 JSON 路径（默认 baselines/streaming-ttfb-baseline.json）",
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
