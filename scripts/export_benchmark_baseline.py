"""导出 pytest-benchmark storage 中的最新 run 为独立基线 JSON 文件。

用法:
    python scripts/export_benchmark_baseline.py <storage_dir> <output_json> [--name baseline-name]

示例:
    python scripts/export_benchmark_baseline.py output/benchmarks benchmarks/baseline.json --name v2.2.1
"""

import argparse
import json
import sys
from pathlib import Path


def find_latest_run(storage_dir: Path) -> Path | None:
    """在 storage 目录中找到编号最大的 pytest-benchmark run JSON 文件。"""
    runs = sorted(storage_dir.glob("*.json"))
    if not runs:
        return None
    # pytest-benchmark 命名: 0001_<name>.json，按编号排序取最大
    return runs[-1]


def export_baseline(storage_dir: Path, output_path: Path, name: str | None = None) -> dict:
    """读取最新 run，导出为独立基线 JSON。

    Returns:
        导出的基线 dict（含 benchmark 元信息与 benchmarks 列表）。
    """
    latest = find_latest_run(storage_dir)
    if latest is None:
        print(f"ERROR: 未在 {storage_dir} 中找到任何 benchmark run JSON", file=sys.stderr)
        sys.exit(1)

    with open(latest, encoding="utf-8") as f:
        data = json.load(f)

    # pytest-benchmark storage 格式: {"benchmark": {...}} 或直接 {...}
    benchmark = data.get("benchmark", data)

    # 覆盖基线名称
    if name:
        benchmark["name"] = name

    # 精简 machine_info（避免泄露 CI runner 细节）
    if "machine_info" in benchmark:
        mi = benchmark["machine_info"]
        benchmark["machine_info"] = {
            "node": mi.get("node", ""),
            "processor": mi.get("processor", ""),
            "machine": mi.get("machine", ""),
            "python_implementation": mi.get("python_implementation", ""),
            "python_version": mi.get("python_version", ""),
            "system": mi.get("system", ""),
            "release": mi.get("release", ""),
            "cpu_count_logical": mi.get("cpu_count_logical", 0),
            "cpu_count_physical": mi.get("cpu_count_physical", 0),
        }

    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"benchmark": benchmark}, f, indent=2, ensure_ascii=False)

    count = len(benchmark.get("benchmarks", []))
    print(f"OK: 已导出 {count} 个 benchmark 用例到 {output_path}")
    print(f"    来源: {latest.name}")
    print(f"    基线名: {benchmark.get('name', 'N/A')}")
    return {"benchmark": benchmark}


def main():
    parser = argparse.ArgumentParser(description="导出 pytest-benchmark 最新 run 为独立基线 JSON")
    parser.add_argument("storage_dir", type=Path, help="pytest-benchmark storage 目录")
    parser.add_argument("output", type=Path, help="输出基线 JSON 路径")
    parser.add_argument("--name", type=str, default=None, help="基线名称（如 v2.2.1）")
    args = parser.parse_args()

    if not args.storage_dir.is_dir():
        print(f"ERROR: storage 目录不存在: {args.storage_dir}", file=sys.stderr)
        sys.exit(1)

    export_baseline(args.storage_dir, args.output, args.name)


if __name__ == "__main__":
    main()
