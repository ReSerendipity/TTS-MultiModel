#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""仓库唯一的 Python 解释器解析入口（F7）。

背景：文档曾把 ``.venv/Scripts/python.exe`` 写死为命令入口。2026-11-03 实测该解释器
不可达——``.venv`` 是指向 ``D:\\_venvs\\tts`` 的目录联接，目标目录不存在——文档里所有
以它为入口的命令随之全部失效；而同机的系统 ``python`` 本身完全可跑。故把「venv 优先、
系统解释器兜底」这一既有回退模式（与 ``.githooks/pre-commit:20-27`` 同源）收敛到本文件：
文档命令一律经本入口调用，不再把本机符号链接的绝对路径当契约。

解析顺序（前五项逐个**实际执行**探测，不只看文件是否存在）：

    0. 环境变量 ``TTS_PYTHON``（显式覆盖：设置即只认它，探测失败直接报错，不静默回退）
    1. 项目 ``.venv/Scripts/python.exe``（Windows venv；目标失效时自动跳过）
    2. 项目 ``.venv/bin/python``（POSIX venv）
    3. PATH 上的 ``python``
    4. PATH 上的 ``python3``
    5. Windows ``py -3`` 启动器
    6. 当前进程的解释器（``sys.executable``，最后的安全网）

全部探测失败：退出码 ``127`` 并打印修复指引——不把「没解析到」写成「已通过」。

用法：

    python scripts/run_python.py --print                 # 只打印解析结果（可被脚本消费）
    python scripts/run_python.py --explain               # 打印解析结果 + 命中来源
    python scripts/run_python.py --list                  # 打印全部候选与探测结果
    python scripts/run_python.py scripts/backup_history_db.py --keep 14
    python scripts/run_python.py -m pytest tests/ -q --ignore=tests/e2e

说明：

- 目标脚本的相对路径先按当前目录解析，不存在时按仓库根解析——因此在仓库外也能调用；
- 解析成功后启动目标程序并**原样回传退出码**：POSIX 用 ``os.execv`` 替换当前进程；
  Windows 用子进程回传（实测 Windows 的 ``os.execv`` 经 ``_P_OVERLAY`` 会丢失退出码）；
- 判定回放：``tests/test_run_python_entry.py``。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: 支持的最低 Python 版本（与 pyproject.toml 的 requires-python 同源）
MIN_PYTHON = (3, 10)

#: 探测脚本：能跑起来且版本达标才算「可用解释器」
_PROBE_CODE = "import sys; raise SystemExit(0 if sys.version_info >= ({}, {}) else 1)".format(*MIN_PYTHON)

#: 显式覆盖用的环境变量（CI / 特殊环境钉解释器时使用）
ENV_OVERRIDE = "TTS_PYTHON"

#: 仓库 venv 的解释器相对路径（Windows / POSIX）
_VENV_RELATIVE = (Path("Scripts") / "python.exe", Path("bin") / "python")

Which = Callable[[str], "str | None"]
Probe = Callable[["Interpreter"], bool]


@dataclass(frozen=True)
class Interpreter:
    """一个可启动的解释器：可执行文件 + 前缀参数（如 ``py -3``）+ 命中来源。"""

    executable: str
    prefix_args: tuple[str, ...] = ()
    source: str = ""

    @property
    def argv(self) -> list[str]:
        return [self.executable, *self.prefix_args]

    def describe(self) -> str:
        return " ".join(self.argv)


class InterpreterNotFoundError(RuntimeError):
    """没有任何候选解释器可用。"""


def _is_executable_file(path: Path) -> bool:
    """文件存在且可执行。Windows 上 ``X_OK`` 约等于「存在」，与 ``.githooks/pre-commit`` 的 ``-x`` 同义。"""
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:  # 失效的符号链接 / 目录联接在部分平台上会抛 OSError
        return False


def venv_python(project_root: Path | str | None = None) -> Path | None:
    """项目 venv 的解释器路径；不存在或不可执行（含符号链接目标失效）时返回 ``None``。"""
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    for relative in _VENV_RELATIVE:
        candidate = root / ".venv" / relative
        if _is_executable_file(candidate):
            return candidate
    return None


def iter_candidates(
    project_root: Path | str | None = None,
    environ: dict[str, str] | None = None,
    which: Which = shutil.which,
) -> Iterator[Interpreter]:
    """按约定顺序产出候选解释器（不做探测；探测由 :func:`resolve` 负责）。"""
    env = os.environ if environ is None else environ

    override = env.get(ENV_OVERRIDE)
    if override:
        # 显式覆盖是唯一候选：不静默回退，否则「我钉了 TTS_PYTHON」会变成一句空话
        yield Interpreter(override, (), f"env:{ENV_OVERRIDE}")
        return

    venv = venv_python(project_root)
    if venv is not None:
        yield Interpreter(str(venv), (), "project-venv")

    for name in ("python", "python3"):
        found = which(name)
        if found:
            yield Interpreter(found, (), f"PATH:{name}")

    if os.name == "nt":
        py_launcher = which("py")
        if py_launcher:
            yield Interpreter(py_launcher, ("-3",), "py-launcher")

    current = sys.executable
    if current:
        yield Interpreter(current, (), "current-process")


def probe_interpreter(interpreter: Interpreter, timeout: float = 30.0) -> bool:
    """实际启动一次，确认它可执行且版本达标。"""
    try:
        completed = subprocess.run(  # noqa: S603 - argv 列表，无 shell
            [*interpreter.argv, "-c", _PROBE_CODE],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def first_existing(
    project_root: Path | str | None = None,
    environ: dict[str, str] | None = None,
    which: Which = shutil.which,
) -> Interpreter | None:
    """不探测、只按顺序取第一个候选（供构造命令行等「先写下来」的场景使用）。"""
    return next(iter_candidates(project_root, environ, which), None)


def unresolved_reason(
    project_root: Path | str | None = None,
    environ: dict[str, str] | None = None,
    which: Which = shutil.which,
) -> str:
    """探测全部候选并给出「为什么一个都不行」的可读说明。"""
    lines = ["无法解析可用的 Python 解释器（候选逐个实际执行探测，均失败）："]
    for interpreter in iter_candidates(project_root, environ, which):
        lines.append(f"  [FAIL] {interpreter.describe()}  <- {interpreter.source}")
    lines.append("修复：安装 Python >= 3.10 并加入 PATH，或重建项目 .venv，")
    lines.append(f"或用 {ENV_OVERRIDE}=<解释器绝对路径> 显式指定。")
    return "\n".join(lines)


def resolve(
    project_root: Path | str | None = None,
    environ: dict[str, str] | None = None,
    which: Which = shutil.which,
    probe: Probe = probe_interpreter,
) -> Interpreter:
    """返回第一个**探测通过**的候选；显式覆盖失败则立刻报错。"""
    env = os.environ if environ is None else environ
    for interpreter in iter_candidates(project_root, env, which):
        if probe(interpreter):
            return interpreter
        if interpreter.source == f"env:{ENV_OVERRIDE}":
            raise InterpreterNotFoundError(
                f"{ENV_OVERRIDE}={interpreter.executable} 不可用（无法执行或版本低于 "
                f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}）；显式覆盖失败不做静默回退。"
            )
    raise InterpreterNotFoundError(unresolved_reason(project_root, env, which))


def _normalize_target_argument(argument: str) -> str:
    """目标脚本相对路径：当前目录优先，其次仓库根（文档命令不依赖 CWD）。"""
    if argument.startswith("-"):
        return argument
    path = Path(argument)
    if path.is_absolute() or path.exists():
        return argument
    from_root = PROJECT_ROOT / path
    if from_root.exists():
        return str(from_root)
    return argument


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if not args:
        usage = __doc__.split("用法：", 1)[1].split("说明：", 1)[0].strip()
        print(f"用法：\n{usage}", file=sys.stderr)
        return 2

    mode = args[0]
    if mode in ("--print", "--explain", "--list"):
        try:
            if mode == "--list":
                for interpreter in iter_candidates():
                    verdict = "OK  " if probe_interpreter(interpreter) else "FAIL"
                    print(f"[{verdict}] {interpreter.describe()}  <- {interpreter.source}")
                return 0
            chosen = resolve()
        except InterpreterNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 127
        print(chosen.describe() if mode == "--print" else f"{chosen.describe()}  <- {chosen.source}")
        return 0

    target = [_normalize_target_argument(args[0]), *args[1:]]
    try:
        chosen = resolve()
    except InterpreterNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 127

    if target[0].endswith(".py") and not Path(target[0]).exists():
        print(f"目标脚本不存在：{target[0]}", file=sys.stderr)
        return 2

    launch = [*chosen.argv, *target]
    if os.name == "nt":
        # Windows 上 os.execv 走 _P_OVERLAY，实测**丢失目标退出码**（目标 sys.exit(7) →
        # 入口 exit 0），而本入口的契约是「退出码原样透传」，故改用子进程显式回传。
        try:
            completed = subprocess.run(launch, check=False)
        except OSError as exc:
            print(f"启动解释器失败：{chosen.describe()}（{exc}）", file=sys.stderr)
            return 127
        return completed.returncode

    try:
        os.execv(chosen.executable, launch)
    except OSError as exc:  # 解析后仍启动失败（权限 / 竞态删除）：如实报错，不假装跑过
        print(f"启动解释器失败：{chosen.describe()}（{exc}）", file=sys.stderr)
        return 127
    return 0  # pragma: no cover - POSIX 上 os.execv 成功时不会返回


if __name__ == "__main__":
    raise SystemExit(main())
