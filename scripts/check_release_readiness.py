#!/usr/bin/env python3
"""发版前条件检查：这条 release PR 现在合下去，main 会不会立刻变红？

为什么要有它（2026-09-22 实测的机制）：**release-please 自己开的 PR 拿不到任何 CI 检查** ——
GitHub 固定行为，由 `GITHUB_TOKEN` 产生的提交不再级联触发 workflow。于是"手工同步的 5 类版本位
会在 release PR 上红"这个假设是错的：那些闸只会红在**合并之后的 main** 上，而那时 tag 与 Release
已经发出去了。这个脚本把同一套判据提前到合并之前跑，结果以 commit status 的形式回写到
release 分支的 head 上（`release-please.yml` 的 release-gate 步骤负责调它并回写）。

判据本身**不在这里重写**：脚本用 importlib 载入 `tests/test_version_consistency.py`，
把它里面的 `test_*` 逐个跑一遍。这样"CI 里那条"与"发版前这条"永远同源，不会各漂一半。

用法：
    python scripts/check_release_readiness.py                # 检当前仓库根
    python scripts/check_release_readiness.py --root <目录>   # 检别处检出（CI 用它检 PR head）

退出码：0 = 可以合；1 = 不能合（会红在 main 上），并逐条打印差在哪。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path

# 与 release-please 的 extra-files 无关、必须由人补的版本位（说明见 docs/release-governance.md §1）
HAND_SYNCED = (
    "desktop/src-tauri/Cargo.lock",
    "config.yaml",
    "deploy/kubernetes/deployment.yaml",
    "scripts/installer/setup.nsi",
    "version.json 的 changelog / release_date",
)


def _load_checker(root: Path):
    """把 tests/test_version_consistency.py 当模块载入（它只 import 标准库，无需装依赖）。"""
    src = root / "tests" / "test_version_consistency.py"
    if not src.is_file():
        raise SystemExit(f"找不到判据来源：{src}")
    spec = importlib.util.spec_from_file_location("_release_readiness_checker", src)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent), type=Path)
    args = ap.parse_args()
    root: Path = args.root.resolve()

    mod = _load_checker(root)
    problems: list[str] = []

    try:
        sites = mod._site_versions()
    except Exception as exc:  # 读取器本身坏了 = 判据不可信，同样不能放行
        print(f"::error::读版本位失败：{type(exc).__name__}: {exc}")
        return 1

    print("版本位：")
    for key in sorted(sites):
        print(f"  {sites[key]:9} {key}")
    try:
        target = sites["pyproject.toml"]
    except KeyError:
        print("::error::读不到 pyproject.toml 的版本，无法确定目标版本")
        return 1

    lag = {k: v for k, v in sites.items() if v != target}
    if lag:
        print(f"\n落后于 {target} 的版本位（这些必须人补，release-please 不会碰它们）：")
        for k, v in sorted(lag.items()):
            print(f"  {v:9} {k}")
        problems.append(f"{len(lag)} 处版本位与 {target} 不一致")

    must_cover = [h for h in HAND_SYNCED if any(h.split()[0] in k for k in sites)]
    print(
        f"\n人工同步位清单（{len(HAND_SYNCED)} 类，其中本仓读取器覆盖 {len(must_cover)} 类）：" + "、".join(HAND_SYNCED)
    )

    names = [n for n in dir(mod) if n.startswith("test_")]
    print(f"\n复用 tests/test_version_consistency.py 的 {len(names)} 条判据：")
    for name in sorted(names):
        fn = getattr(mod, name)
        try:
            fn()
            print(f"  PASS {name}")
        except AssertionError as exc:
            first = str(exc).strip().splitlines()[0] if str(exc).strip() else "（无消息）"
            print(f"  FAIL {name} —— {first}")
            problems.append(f"{name}: {first}")
        except Exception:  # 崩了比断言失败更糟：判据没跑成
            print(f"  ERROR {name}\n{traceback.format_exc()}")
            problems.append(f"{name} 抛异常，判据未能执行")

    manifest = root / ".release-please-manifest.json"
    if manifest.is_file():
        got = json.loads(manifest.read_text(encoding="utf-8")).get(".")
        if str(got) != target:
            problems.append(f".release-please-manifest.json={got} 与目标版本 {target} 不一致")

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8", errors="ignore")
    if f"## [{target}]" not in changelog:
        problems.append(f"CHANGELOG.md 里没有 ## [{target}] 段")

    print()
    if problems:
        print("::error::发版条件不满足，合下去会让 main 立刻变红：")
        for p in problems:
            print("  - " + p)
        return 1
    print(f"发版条件满足：全部版本位 = {target}，判据全过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
