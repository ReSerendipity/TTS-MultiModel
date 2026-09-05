"""mypy 棘轮检查脚本。

运行 mypy 并与基线（configs/mypy_baseline.json）对比：
- 错误数增加 → 失败（阻断，防止类型退化）
- 错误数减少 → 自动更新基线（ratchet down）并通过
- 错误数不变 → 通过

用法:
    python scripts/check_mypy_ratchet.py [--target app/integrated_app/] [--baseline configs/mypy_baseline.json]

示例:
    python scripts/check_mypy_ratchet.py
    python scripts/check_mypy_ratchet.py --target app/ --baseline configs/mypy_baseline.json
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGET = "app/integrated_app/"
DEFAULT_BASELINE = REPO_ROOT / "configs" / "mypy_baseline.json"

# mypy 摘要行格式: "Found N errors in M files (checked K source files)"
SUMMARY_RE = re.compile(r"Found (\d+) errors? in (\d+) files?.*?(?:checked (\d+) source files?)?")


def run_mypy(target: str) -> tuple[int, int, int, list[str]]:
    """运行 mypy，返回 (total_errors, files_with_errors, files_checked, error_lines)。"""
    cmd = [sys.executable, "-m", "mypy", target, "--no-error-summary"]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)

    lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
    error_lines = [line for line in lines if line.strip()]

    # 再跑一次带摘要的获取统计
    cmd_summary = [sys.executable, "-m", "mypy", target]
    result_summary = subprocess.run(cmd_summary, capture_output=True, text=True, cwd=REPO_ROOT)
    summary_text = result_summary.stdout + result_summary.stderr

    total = 0
    files_err = 0
    files_checked = 0
    for line in summary_text.split("\n"):
        m = SUMMARY_RE.search(line)
        if m:
            total = int(m.group(1))
            files_err = int(m.group(2))
            files_checked = int(m.group(3)) if m.group(3) else 0
            break

    # 如果摘要解析失败，用错误行数估算
    if total == 0 and error_lines:
        total = len(error_lines)
        files_err = len(set(line.split(":")[0] for line in error_lines if ":" in line))

    return total, files_err, files_checked, error_lines


def load_baseline(baseline_path: Path) -> dict:
    """加载基线文件。"""
    if not baseline_path.is_file():
        return {"total_errors": 0, "files_with_errors": 0, "files_checked": 0, "per_file_errors": {}}
    with open(baseline_path, encoding="utf-8") as f:
        return json.load(f)


def save_baseline(baseline_path: Path, data: dict) -> None:
    """保存基线文件。"""
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def check_mypy_ratchet(target: str, baseline_path: Path) -> int:
    """执行 mypy 棘轮检查。

    Returns:
        0 = 通过（错误数未增加），1 = 失败（错误数增加）。
    """
    print("=" * 70)
    print("mypy 棘轮检查 (mypy ratchet check)")
    print("=" * 70)

    baseline = load_baseline(baseline_path)
    baseline_total = baseline.get("total_errors", 0)

    print(f"\n基线错误数: {baseline_total}")
    print(f"运行 mypy 检查: {target} ...")

    total, files_err, files_checked, error_lines = run_mypy(target)

    print(f"当前错误数: {total}（{files_err} 个文件，检查 {files_checked} 个源文件）")

    if total > baseline_total:
        delta = total - baseline_total
        print(f"\n❌ 失败：错误数增加 {delta} 个（{baseline_total} → {total}）")
        print("   请修复新增的类型错误后再提交。最近的错误：")
        for line in error_lines[-15:]:
            print(f"   {line}")
        if len(error_lines) > 15:
            print(f"   ... 还有 {len(error_lines) - 15} 个错误")
        return 1

    if total < baseline_total:
        delta = baseline_total - total
        print(f"\n✅ 错误数减少 {delta} 个（{baseline_total} → {total}），自动收紧基线（ratchet down）。")
        new_baseline = {
            "total_errors": total,
            "files_with_errors": files_err,
            "files_checked": files_checked,
            "per_file_errors": {},
            "last_updated": __import__("datetime").date.today().isoformat(),
            "note": "mypy 棘轮基线。错误数增加则 CI 阻断；减少则自动更新本文件（ratchet down）。",
        }
        save_baseline(baseline_path, new_baseline)
        print(f"   基线已更新: {baseline_path}")
        return 0

    print(f"\n✅ 错误数不变（{total}），通过棘轮检查。")
    return 0


def main():
    parser = argparse.ArgumentParser(description="mypy 棘轮检查")
    parser.add_argument("--target", type=str, default=DEFAULT_TARGET, help="mypy 检查目标（默认: app/integrated_app/）")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE, help="基线文件路径")
    args = parser.parse_args()

    sys.exit(check_mypy_ratchet(args.target, args.baseline))


if __name__ == "__main__":
    main()
