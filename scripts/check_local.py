#!/usr/bin/env python3
"""本地提交前检查（跨平台，Python 3.9+）。

快检（默认，秒级）：ruff / black / compileall / 严格 UTF-8 扫描
完整检查（--full）：在快检基础上加 pytest 全量（含 --timeout 防挂死）

用法:
    python scripts/check_local.py            # 快检
    python scripts/check_local.py --full     # 快检 + 全量 pytest
    python scripts/check_local.py --mypy     # 快检 + mypy（SeedVR2 仓库需要）

说明:
    - CI 是唯一权威门禁，本脚本是提交前辅助，防低级错误（lint/格式/编码/语法）
    - 覆盖率门槛不进本地（数值跨平台有差异，以 CI 为准）
    - 编码扫描对 git ls-files 中所有文本文件做严格 UTF-8 解码，
      可抓"乱码提交"（compileall 只能抓 SyntaxError，抓不到字符串/JSON 里的乱码）
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

TEXT_EXTS = {
    ".py", ".json", ".yaml", ".yml", ".toml", ".md", ".txt",
    ".html", ".css", ".js", ".mjs", ".ts", ".cfg", ".ini",
    ".sh", ".ps1", ".bat", ".svg", ".xml",
}


def run(cmd: list[str], desc: str) -> None:
    print(f"== {desc} ==", flush=True)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"FAIL: {desc} (exit {result.returncode})", flush=True)
        sys.exit(result.returncode)


def check_dir(path: str) -> bool:
    return os.path.isdir(path)


def utf8_scan() -> None:
    """严格 UTF-8 扫描所有被 git 跟踪的文本文件。"""
    print("== UTF-8 编码扫描（git ls-files 文本文件）==", flush=True)
    listed = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True
    )
    if listed.returncode != 0:
        print("  (不是 git 仓库，跳过)", flush=True)
        return
    bad: list[str] = []
    for f in listed.stdout.splitlines():
        ext = os.path.splitext(f)[1].lower()
        if ext not in TEXT_EXTS:
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                fh.read()
        except UnicodeDecodeError as e:
            bad.append(f"{f}: {e}")
        except OSError:
            continue
    if bad:
        print("FAIL: 以下文件不是有效 UTF-8（可能是编码损坏，提交前必须修复）:", flush=True)
        for b in bad:
            print(f"  {b}", flush=True)
        sys.exit(1)
    print("  OK", flush=True)


def compile_all() -> None:
    """compileall 抓语法错误（乱码导致 SyntaxError 的最直接检测）。"""
    dirs = [d for d in ("bin", "tests", "examples", "scripts") if check_dir(d)]
    if not dirs:
        print("== compileall：无可检查目录 ==", flush=True)
        return
    run([sys.executable, "-m", "compileall", "-q", "-j", "4", *dirs], "compileall 语法检查")


def main() -> None:
    ap = argparse.ArgumentParser(description="本地提交前检查")
    ap.add_argument("--full", action="store_true", help="快检 + 全量 pytest（慢）")
    ap.add_argument("--mypy", action="store_true", help="额外跑 mypy（SeedVR2 等仓库）")
    args = ap.parse_args()

    # ---- 快检层（秒级）----
    run([sys.executable, "-m", "ruff", "check", "."], "ruff check")
    # 格式化检查：优先 ruff format（若仓库用 black 则自动回退）
    fmt = [sys.executable, "-m", "ruff", "format", "--check", "."]
    if subprocess.run(fmt, capture_output=True).returncode not in (0, 2):
        run([sys.executable, "-m", "black", "--check", "."], "black --check")
    else:
        run(fmt, "ruff format --check")
    compile_all()
    utf8_scan()

    if args.mypy:
        mypy_dirs = [d for d in ("bin/integrated_app",) if check_dir(d)]
        if mypy_dirs:
            run([sys.executable, "-m", "mypy", *mypy_dirs], "mypy 类型检查")

    if args.full:
        # 全量 pytest（--timeout 防挂死；--full 时才跑，耗时较长）
        run(
            [sys.executable, "-m", "pytest", "-q", "--timeout=180",
             "-m", "not integration and not gpu and not cuda and not vram"],
            "pytest 全量（含超时保护）",
        )

    print("\n✅ 全部检查通过，可以提交。", flush=True)


if __name__ == "__main__":
    main()
