"""覆盖率预算检查脚本。

读取 coverage.xml，按 configs/coverage_budget.toml 中的预算逐模块校验。
低于预算的模块输出错误并以非零退出码退出（CI 硬门禁）。

用法:
    python scripts/check_coverage_budget.py [coverage.xml] [--config configs/coverage_budget.toml]

示例:
    python scripts/check_coverage_budget.py
    python scripts/check_coverage_budget.py coverage.xml --config configs/coverage_budget.toml
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # Python < 3.11 回退


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COVERAGE = REPO_ROOT / "coverage.xml"
DEFAULT_CONFIG = REPO_ROOT / "configs" / "coverage_budget.toml"


def load_budgets(config_path: Path) -> tuple[dict[str, float], dict]:
    """加载覆盖率预算配置。"""
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    budgets = data.get("budgets", {})
    settings = data.get("settings", {})
    return budgets, settings


def parse_coverage(coverage_path: Path) -> dict[str, tuple[int, int]]:
    """解析 coverage.xml，返回 {module_filename: (hit_lines, total_lines)}。

    使用 filename 属性（含相对路径）而非 class name（仅文件名），
    避免 utils.py / load.py 等通用文件名跨模块歧义。
    """
    tree = ET.parse(coverage_path)
    root = tree.getroot()
    modules: dict[str, tuple[int, int]] = {}

    for cls in root.findall(".//class"):
        filename = cls.get("filename", cls.get("name", ""))
        lines_elem = cls.find("lines")
        if lines_elem is None:
            continue
        all_lines = lines_elem.findall("line")
        total = len(all_lines)
        hit = sum(1 for line in all_lines if line.get("hits", "0") != "0")
        if total > 0:
            # 同一文件可能有多个 class 元素（如 __init__.py），合并计数
            if filename in modules:
                old_hit, old_total = modules[filename]
                modules[filename] = (old_hit + hit, old_total + total)
            else:
                modules[filename] = (hit, total)

    return modules


def match_budget(module_name: str, budgets: dict[str, float]) -> float | None:
    """为模块名匹配最严格的预算（多个 pattern 匹配时取最大值）。"""
    best: float | None = None
    for pattern, threshold in budgets.items():
        if pattern in module_name and (best is None or threshold > best):
            best = threshold
    return best


def check_coverage(
    coverage_path: Path,
    config_path: Path,
) -> tuple[list[str], list[str], list[str]]:
    """执行覆盖率预算检查。

    Returns:
        (errors, warnings, info) — 错误列表、警告列表、信息列表。
    """
    budgets, settings = load_budgets(config_path)
    tolerance = float(settings.get("tolerance", 1.0))
    modules = parse_coverage(coverage_path)

    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    checked = 0
    for module_name, (hit, total) in sorted(modules.items()):
        budget = match_budget(module_name, budgets)
        if budget is None:
            continue
        checked += 1
        rate = hit / total * 100
        if rate < budget - tolerance:
            errors.append(f"FAIL  {rate:5.1f}% < {budget:5.1f}%  ({hit:4d}/{total:4d})  {module_name}")
        elif rate < budget:
            warnings.append(f"WARN  {rate:5.1f}% ≈ {budget:5.1f}%  ({hit:4d}/{total:4d})  {module_name} (容差内)")
        else:
            info.append(f"PASS  {rate:5.1f}% >= {budget:5.1f}%  ({hit:4d}/{total:4d})  {module_name}")

    info.append(f"\n已检查 {checked} 个模块，{len(errors)} 个不达标，{len(warnings)} 个容差内。")
    return errors, warnings, info


def main():
    parser = argparse.ArgumentParser(description="覆盖率预算检查")
    parser.add_argument(
        "coverage", nargs="?", type=Path, default=DEFAULT_COVERAGE, help="coverage.xml 路径（默认: coverage.xml）"
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG, help="预算配置路径（默认: configs/coverage_budget.toml）"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="显示全部通过的模块")
    args = parser.parse_args()

    if not args.coverage.is_file():
        print(f"ERROR: coverage 文件不存在: {args.coverage}", file=sys.stderr)
        print("请先运行: pytest --cov=integrated_app --cov-report=xml", file=sys.stderr)
        sys.exit(2)

    if not args.config.is_file():
        print(f"ERROR: 预算配置不存在: {args.config}", file=sys.stderr)
        sys.exit(2)

    errors, warnings, info = check_coverage(args.coverage, args.config)

    print("=" * 70)
    print("覆盖率预算检查 (Coverage Budget Check)")
    print("=" * 70)

    if args.verbose:
        for line in info:
            print(line)

    if warnings:
        print("\n--- 容差内警告 ---")
        for line in warnings:
            print(line)

    if errors:
        print("\n--- 不达标模块 ---")
        for line in errors:
            print(line)
        print(f"\n❌ {len(errors)} 个模块低于覆盖率预算，CI 门禁失败。")
        sys.exit(1)
    else:
        print(f"\n✅ 全部受检模块达标（{len(warnings)} 个在容差内）。")
        sys.exit(0)


if __name__ == "__main__":
    main()
