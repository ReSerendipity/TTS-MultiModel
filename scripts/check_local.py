#!/usr/bin/env python3
"""本地提交前检查（跨平台，Python 3.9+）。

快检（默认，秒级）：ruff / black / compileall / 严格 UTF-8 扫描
完整检查（--full）：在快检基础上加 pytest 全量（含 --timeout 防挂死）

用法:
    python scripts/check_local.py            # 快检
    python scripts/check_local.py --full     # 快检 + 全量 pytest
    python scripts/check_local.py --mypy     # 快检 + mypy（SeedVR2 仓库需要）
    python scripts/check_local.py --e2e --e2e-specs specs/navigation.spec.ts,specs/settings.spec.ts
                                            # 快检 + E2E 前置检查 + 跑指定 Playwright spec

说明:
    - CI 是唯一权威门禁，本脚本是提交前辅助，防低级错误（lint/格式/编码/语法）
    - 覆盖率门槛不进本地（数值跨平台有差异，以 CI 为准）
    - 编码扫描对 git ls-files 中所有文本文件做严格 UTF-8 解码，
      可抓"乱码提交"（compileall 只能抓 SyntaxError，抓不到字符串/JSON 里的乱码）
    - --e2e 会先做环境预检（fastapi / playwright 可导入、tests 目录存在），
      再跑指定 Playwright spec。改 UI/测试后提交前务必本地跑一遍受影响 spec
      （E2E 全量 231 个约 6 分钟，可一次传多个 spec 用逗号分隔）。
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


def e2e_preflight() -> None:
    """E2E 前置检查：本地服务器依赖与 Playwright 可用性。

    2026-08 事故：本地 PATH 里的 python 没有 fastapi，Playwright webServer
    启动 app_server.py 直接 ModuleNotFoundError，误判为测试失败。
    因此提交前先确认当前解释器能 import fastapi（如果 webServer 用系统
    python 起不来，用正确解释器先手动起服务器再跑测试）。
    """
    print("== E2E 环境预检 ==", flush=True)
    checks = [
        ("fastapi", "app_server.py 依赖（本地服务器）"),
        ("playwright.sync_api", "Playwright Python 绑定"),
    ]
    for mod, desc in checks:
        try:
            __import__(mod)
            print(f"  OK: {mod} ({desc})", flush=True)
        except ImportError as e:
            print(
                f"FAIL: {mod} ({desc}) 不可导入: {e}\n"
                f"  提示：若 PATH 里的 python 缺 fastapi，用装了依赖的解释器"
                f"  （如 C:\\Python312\\python.exe）手动启动 bin/integrated_app/app_server.py，"
                f"  测试会复用已启动的服务器（playwright.config.ts 的 reuseExistingServer）。",
                flush=True,
            )
            sys.exit(1)
    if not os.path.isdir("tests"):
        print("FAIL: 找不到 tests/ 目录（本仓库没有 Playwright E2E？）", flush=True)
        sys.exit(1)
    print("  OK: E2E 前置依赖齐备", flush=True)


def run_e2e(specs: str) -> None:
    """跑指定 Playwright spec（tests 目录，chromium-desktop 项目）。"""
    print(f"== Playwright E2E: {specs} ==", flush=True)
    spec_list = [s.strip() for s in specs.split(",") if s.strip()]
    if not spec_list:
        print("FAIL: --e2e-specs 不能为空", flush=True)
        sys.exit(1)
    # Windows 用 playwright.cmd；类 Unix 直接 npx。
    if os.name == "nt":
        cmd = [r"node_modules\.bin\playwright.cmd"]
    else:
        cmd = ["npx", "playwright"]
    cmd += ["test", *spec_list, "--project=chromium-desktop", "--reporter=line"]
    run(cmd, f"Playwright E2E {len(spec_list)} 个 spec")


def main() -> None:
    ap = argparse.ArgumentParser(description="本地提交前检查")
    ap.add_argument("--full", action="store_true", help="快检 + 全量 pytest（慢）")
    ap.add_argument("--mypy", action="store_true", help="额外跑 mypy（SeedVR2 等仓库）")
    ap.add_argument("--e2e", action="store_true", help="快检 + E2E 前置检查 + 跑指定 Playwright spec")
    ap.add_argument("--e2e-specs", default="", help="逗号分隔的 spec 路径（配合 --e2e）")
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

    if args.e2e:
        e2e_preflight()
        run_e2e(args.e2e_specs)

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
