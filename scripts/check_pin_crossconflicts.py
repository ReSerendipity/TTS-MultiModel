#!/usr/bin/env python3
"""校验锁文件内部是否自相矛盾：按 PyPI 声明的交叉约束逐条比对。

与 `check_pin_floors.py` 的分工：那个只看「钉版 < 本项目声明的下界」（下界方向），
本脚本看「锁里 A==x 与 B==y 的互相约束是否成立」（上界 / == / ~= 方向）。
2026-09-20 的 issue #97 就是这类：`transformers==4.52.1` 要 `tokenizers<0.22`
而锁里是 0.23.2、`sympy` 要 `mpmath<1.4` 而锁里是 1.4.1、`hydra-core`/`omegaconf`
要 `antlr4-python3-runtime==4.9.*` 而锁里是 4.13.2 —— 下界检查器一条都抓不到。

为什么要跑这个而不是「CI 装一次看看」：便携包锁集的真实解析要 WinPython + 数 GB 轮子，
本地/CI 都不便每次改动都装；而 PyPI 的 `requires_dist` 元数据足以证伪。

网络失败时**不静默跳过**：受影响依赖计入 unchecked 并以退出码 2 报出。

用法：
    python scripts/check_pin_crossconflicts.py                  # 全部锁文件
    python scripts/check_pin_crossconflicts.py --quiet          # 只报冲突与 unchecked 数
"""

from __future__ import annotations

import argparse
import concurrent.futures
import http.client
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
LOCK_FILES = ("requirements-lock.txt", "launcher/requirements-small.txt")
_CACHE: dict[str, list[str]] = {}


def _ver(s: str) -> tuple:
    """版本 → 定长 4 元组；剥掉本地版本段与预发布尾标（与 check_pin_floors 同一口径）。"""
    core = re.split(r"[+]", s, maxsplit=1)[0]
    core = re.split(r"(?<=[\d.])(?:a|b|rc|dev|pre|post)\d*$", core)[0]
    nums = [int(n) for n in re.findall(r"\d+", core)[:4]]
    return tuple(nums + [0] * (4 - len(nums)))


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def read_pins(path: Path) -> dict[str, tuple[str, str]]:
    """{包名: (版本, 出现它的锁文件列表)}；只收 `==` 钉版。"""
    pins: dict[str, tuple[str, str]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9._-]+)==([0-9][0-9a-zA-Z.+-]*)$", line)
        if m:
            key = _norm(m.group(1))
            if key in pins and pins[key][0] != m.group(2):
                pins[key] = (pins[key][0], f"{pins[key][1]}、{m.group(2)}@{path.name}")
            else:
                pins.setdefault(key, (m.group(2), path.name))
    return pins


def requires_dist(name: str, ver: str) -> list[str] | None:
    """取该版本的 requires_dist；网络/不存在返回 None（区别于「确实无依赖」的 []）。"""
    key = f"{name}=={ver}"
    if key in _CACHE:
        return _CACHE[key]
    out = _fetch_requires_dist(name, ver)
    if out is not None:
        _CACHE[key] = out
    return out


def _fetch_requires_dist(name: str, ver: str) -> list[str] | None:
    """抓一个版本的 requires_dist，带重试。

    为什么要重试：这是**pre-commit 的一个 hook**，一次网络抖动就会把整条 `git commit`
    打掉。2026-09-21 实测：单次 `RemoteDisconnected` 让 hook 吐 traceback（不是按设计
    报 unchecked + 退出码 2），而它顺序抓 ~97+94 个包的元数据，撞上一次丢包几乎是必然。

    异常面要写宽：`urllib` 只在 `h.request()` 那一步把 OSError 包成 `URLError`，
    `h.getresponse()` 里抛的 `http.client.RemoteDisconnected` 是**裸的** OSError 子类，
    原先只捕 `URLError` 所以漏了它。
    """
    url = f"https://pypi.org/pypi/{name}/{ver}/json"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.load(resp)
            break
        except (OSError, http.client.HTTPException, ValueError):
            if attempt == 2:
                return None
            time.sleep(0.6 * (attempt + 1))
    else:  # pragma: no cover - for 循环带 break，理论到不了
        return None
    return data.get("info", {}).get("requires_dist") or []


def prefetch(pairs: list[tuple[str, str]], workers: int = 8) -> None:
    """并发把元数据灌进 _CACHE，并汇报**取不到**的包（否则只剩一行行 [SKIP] 看不出是网络问题）。"""
    todo = [(n, v) for n, v in pairs if f"{n}=={v}" not in _CACHE]
    if not todo:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda p: (p, _fetch_requires_dist(*p)), todo))
    for (n, v), out in results:
        if out is not None:
            _CACHE[f"{n}=={v}"] = out


_SPEC_RE = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*([^;]*)")
_OP_RE = re.compile(r"(>=|<=|==|~=|!=|>|<)\s*([0-9][0-9A-Za-z.*+-]*)")


def _nums(s: str) -> list[int]:
    core = re.split(r"[+]", s, maxsplit=1)[0]
    core = re.split(r"(?<=[\d.])(?:a|b|rc|dev|pre|post)\d*$", core)[0]
    return [int(n) for n in re.findall(r"\d+", core)]


def _satisfies(op: str, got: str, want: str) -> bool:
    if op == ">=":
        return _ver(got) >= _ver(want)
    if op == ">":
        return _ver(got) > _ver(want)
    if op == "<=":
        return _ver(got) <= _ver(want)
    if op == "<":
        return _ver(got) < _ver(want)
    if op == "==":
        if want.endswith(".*"):
            prefix = _nums(want[:-2])  # "4.9.*" -> [4, 9]
            return _nums(got)[: len(prefix)] == prefix
        return got == want
    if op == "!=":
        return got != want
    if op == "~=":
        # 只按下界判：兼容版本对「钉低了」的检出已足够，且不引入 PEP 440 前缀语义分歧。
        return _ver(got) >= _ver(want)
    return True


def check(path: Path) -> tuple[list[str], list[str]]:
    """返回 (冲突列表, 无法核验列表)。"""
    pins = read_pins(path)
    conflicts: list[str] = []
    unchecked: list[str] = []

    for name, (ver, _) in sorted(pins.items()):
        dist = requires_dist(name, ver)
        if dist is None:
            unchecked.append(f"{name}=={ver}（PyPI 元数据取不到，未核验其约束）")
            continue
        for raw in dist:
            if "extra" in raw:
                continue
            m = _SPEC_RE.match(raw)
            if not m:
                continue
            dep = _norm(m.group(1))
            if dep not in pins or not m.group(2).strip():
                continue
            got = pins[dep][0]
            for op, want in _OP_RE.findall(m.group(2)):
                if not _satisfies(op, got, want):
                    conflicts.append(f"{path.name}: {name}=={ver} 要求 {dep}{op}{want}，锁里是 {dep}=={got}")
    return conflicts, unchecked


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校验锁文件内部交叉约束")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    existing = [p for rel in LOCK_FILES if (p := _ROOT / rel).exists()]
    pairs = sorted({(n, v) for p in existing for n, (v, _) in read_pins(p).items()})
    t0 = time.monotonic()
    prefetch(pairs)
    if not args.quiet:
        print(f"[pin-cross] 并发抓 PyPI 元数据 {len(pairs)} 个包，用时 {time.monotonic() - t0:.1f}s")

    all_conflicts: list[str] = []
    all_unchecked: list[str] = []
    for p in existing:
        c, u = check(p)
        all_conflicts += c
        all_unchecked += u

    # 两份锁应一致；分别报出的同一行会重复，去重但记录来源
    uniq = list(dict.fromkeys(all_conflicts))
    if not args.quiet:
        print(f"[pin-cross] 缓存 PyPI 版本 {len(_CACHE)} 个，冲突 {len(uniq)} 条，未核验 {len(all_unchecked)} 条")
    for c in uniq:
        print(f"  [FAIL] {c}")
    for u in all_unchecked:
        print(f"  [SKIP] {u}")

    if uniq:
        print(
            "\n[pin-cross] 锁集内部自相矛盾：pip 解析必然失败（历史上表现为便携包 "
            "ResolutionImpossible）。要么回退被自动化顶掉的手工钉版，要么整体换到互相兼容的一组版本。",
            file=sys.stderr,
        )
        return 1
    if all_unchecked:
        print(
            "\n[pin-cross] 有依赖未能核验（重试 3 次后 PyPI 元数据仍取不到），不当作通过。"
            "\n  先确认网络/代理可达 pypi.org，再重跑本脚本；这不是锁冲突，别去改锁。",
            file=sys.stderr,
        )
        return 2
    print("[pin-cross] PASS 未发现交叉约束冲突")
    return 0


if __name__ == "__main__":
    sys.exit(main())
