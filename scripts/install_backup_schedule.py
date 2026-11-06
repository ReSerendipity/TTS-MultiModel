#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""本机备份调度登记产物（O4 调度接线 · 只交付不注册，阶段 9）。

背景：门禁 O4 第四轮复评阻塞项之一为「备份无调度接线」。本机数据面（`data/` 整目录
gitignore）无法由 GitHub hosted runner 的 cron 触达，调度必须落在 Windows 任务计划程序
（schtasks）。按硬约束，**真实注册需所有者单独授权**——本脚本默认 ``--dry-run``，只打印
将要注册的任务契约（任务名/频率/触发时刻/完整命令行/运行身份/下一次应执行时刻），
不做任何系统写入；``--apply`` 才真正调用 ``schtasks /create``（本轮从未调用）。

频率口径自洽性（与成文节奏句对齐，见 ``docs/SRE_RUNBOOK.md``）：

- §3.3「每日全量备份」→ 本产物注册**每日**备份任务（默认触发 03:00，与既有台账示例
  文件名 ``history-20260831-030000`` 的备份时刻口径一致）；
- §3.2「演练步骤（每季度一次）」→ 全流程演练是**人工 SOP**，不由本产物注册为定时任务；
  ``backup_history_db.py --drill`` 的周频演练属备份链自检，亦不在此登记（避免与季度
  SOP 节奏混写）。本产物只接线「每日全量备份」这一条成文节奏。

幂等：任务名固定（``/tn``）且注册带 ``/f``——对同名任务是**就地更新**，重复注册
不产生第二个同名任务；``--dry-run`` 输出对同一组输入逐字节确定。

离线：仅调用本机 ``schtasks``（dry-run 下最多只读 ``/query`` 探测同名任务），
不请求任何外部服务。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import getpass
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from run_python import first_existing

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_SCRIPT = PROJECT_ROOT / "scripts" / "backup_history_db.py"
#: 引导解释器：由仓库唯一解析入口（scripts/run_python.py，F7）解析——优先项目 venv，
#: 不可用时退回 PATH 上的 python。旧写法把 .venv/Scripts/python.exe 写死为唯一解释器，
#: 2026-11-03 该路径失效（符号链接目标 D:\_venvs\tts 不存在）后注册出来的命令不可执行。
_BOOTSTRAP = first_existing(PROJECT_ROOT)
PYTHON_EXE = Path(_BOOTSTRAP.executable) if _BOOTSTRAP else PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON_PREFIX_ARGS: tuple[str, ...] = _BOOTSTRAP.prefix_args if _BOOTSTRAP else ()

DEFAULT_TASK_NAME = "TTS-MultiModel-Backup-Daily"
DEFAULT_TRIGGER = "03:00"
DEFAULT_KEEP = 14


def compute_next_run(trigger_hhmm: str, now: _dt.datetime) -> _dt.datetime:
    """计算「下一次应执行时刻」：每日触发，今天未到点则今天，否则明天（本机时区）。"""
    hh, mm = map(int, trigger_hhmm.split(":"))
    today_slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < today_slot:
        return today_slot
    return today_slot + _dt.timedelta(days=1)


def build_plan(
    task_name: str = DEFAULT_TASK_NAME,
    trigger_hhmm: str = DEFAULT_TRIGGER,
    keep: int = DEFAULT_KEEP,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """构造注册计划（纯函数，不触碰系统）。"""
    now = now or _dt.datetime.now()
    bootstrap = f'"{PYTHON_EXE}"'
    if PYTHON_PREFIX_ARGS:
        bootstrap = " ".join([bootstrap, *PYTHON_PREFIX_ARGS])
    command = f'{bootstrap} "{BACKUP_SCRIPT}" --keep {keep}'
    return {
        "task_name": task_name,
        "frequency": "每日（对齐 docs/SRE_RUNBOOK.md §3.3「每日全量备份」节奏句）",
        "trigger": f"{trigger_hhmm}（每日，本机时区）",
        "command": command,
        "working_dir": str(PROJECT_ROOT),
        "identity": (
            f"{getpass.getuser()}（当前登录用户，交互式运行；不配置服务账号、不存储密码，"
            "用户未登录时不执行——补注册授权后如需无人值守，属所有者决策项）"
        ),
        "next_run": compute_next_run(trigger_hhmm, now).strftime("%Y-%m-%d %H:%M:%S"),
        "query_argv": ["schtasks", "/query", "/tn", task_name],
        "create_argv": [
            "schtasks",
            "/create",
            "/f",
            "/tn",
            task_name,
            "/tr",
            command,
            "/sc",
            "daily",
            "/st",
            trigger_hhmm,
        ],
        "interpreter_exists": PYTHON_EXE.exists(),
        "script_exists": BACKUP_SCRIPT.exists(),
    }


def probe_existing(task_name: str, runner: Callable[..., Any] = subprocess.run) -> bool | None:
    """只读探测同名任务是否已存在。schtasks 不可用时返回 None（不视为错误）。"""
    argv = ["schtasks", "/query", "/tn", task_name]
    try:
        # GBK  locale 下 schtasks 报错文本非 UTF-8，errors="replace" 防解码线程崩
        res = runner(argv, capture_output=True, text=True, errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.returncode == 0


def render_dry_run(plan: dict[str, Any], existing: bool | None) -> str:
    """dry-run 输出契约：七要素 + 幂等声明 + 零写入哨兵。同输入必同输出。"""
    lines = [
        "[dry-run] 备份调度登记计划（Windows 任务计划程序），未做任何系统写入：",
        f"  任务名        : {plan['task_name']}",
        f"  频率          : {plan['frequency']}",
        f"  触发时刻      : {plan['trigger']}",
        f"  完整命令行    : {plan['command']}",
        f"  工作目录      : {plan['working_dir']}",
        f"  运行身份      : {plan['identity']}",
        f"  下一次应执行  : {plan['next_run']}",
    ]
    if existing is True:
        lines.append(
            f"  同名任务探测  : 已存在 → --apply 时将用 /f 就地更新该任务，不会产生第二个「{plan['task_name']}」"
        )
    elif existing is False:
        lines.append("  同名任务探测  : 不存在 → --apply 时将新建唯一任务")
    else:
        lines.append("  同名任务探测  : 未探测（schtasks 不可用）")
    if not plan["interpreter_exists"]:
        lines.append("  [warn] 未找到可用解释器，命令行将不可执行: " + str(PYTHON_EXE))
    if not plan["script_exists"]:
        lines.append("  [warn] 未找到备份入口脚本: " + str(BACKUP_SCRIPT))
    lines += [
        "  注册命令(供授权后使用，本 dry-run 未执行):",
        "    " + " ".join(_quote(a) for a in plan["create_argv"]),
        "[dry-run] 结束。未调用 schtasks /create /change /delete，未写注册表/文件。",
    ]
    return "\n".join(lines)


def _quote(arg: str) -> str:
    """仅为「展示」给 schtasks 命令行参数加引号；真实注册走 argv 列表。"""
    if arg.startswith("/") or arg.isdigit():
        return arg
    return f'"{arg}"' if (" " in arg or '"' in arg) else arg


def main(argv: list[str] | None = None, runner: Callable[..., Any] = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="显式声明只打印计划（缺省行为即是，无任何系统写入）")
    parser.add_argument(
        "--apply", action="store_true", help="真实注册（危险：写系统任务计划）。缺省仅 --dry-run 打印计划。"
    )
    parser.add_argument("--task-name", default=DEFAULT_TASK_NAME, help="任务名（幂等键）")
    parser.add_argument("--trigger", default=DEFAULT_TRIGGER, help="每日触发时刻 HH:MM（本机时区）")
    parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP,
        help="透传给 backup_history_db.py 的保留份数（默认 14，对齐 §3.3 旧文档 retain 14 口径）",
    )
    args = parser.parse_args(argv)

    if args.dry_run and args.apply:
        print("[error] --dry-run 与 --apply 互斥", file=sys.stderr)
        return 2

    try:
        hh, mm = args.trigger.split(":")
        if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
            raise ValueError(args.trigger)
    except ValueError:
        print(f"[error] 非法触发时刻: {args.trigger}", file=sys.stderr)
        return 2

    plan = build_plan(task_name=args.task_name, trigger_hhmm=args.trigger, keep=args.keep)

    if not args.apply:
        existing = probe_existing(args.task_name, runner=runner)
        print(render_dry_run(plan, existing))
        return 0

    print("[apply] 执行真实注册（写系统任务计划）...")
    res = runner(plan["create_argv"], capture_output=True, text=True, errors="replace", timeout=60)
    print(res.stdout)
    if res.stderr:
        print(res.stderr, file=sys.stderr)
    print(f"[apply] schtasks 退出码: {res.returncode}")
    return res.returncode


if __name__ == "__main__":
    raise SystemExit(main())
