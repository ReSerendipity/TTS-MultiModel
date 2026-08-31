#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""历史库 / 运行态数据备份与恢复（SRE 评估 P3-1：DR 备份缺失）。

对应 ``docs/SRE_RUNBOOK.md`` §3.3「数据备份」：

- 对 ``data/tts_history.db``（SQLite）做**一致性快照**（先 checkpoint + 复制，
  不依赖在线服务停机）；
- 同时备份审计日志 ``data/audit.log`` 与当前 ``config.yaml``（治理可追溯）；
- 本地按时间命名保留，支持 ``--retain N`` 轮转（默认 14 份）；
- 提供 ``--restore <file>`` 一键恢复（恢复前自动备份当前版本，防误操作）。

离线优先（AGENTS.md 硬约束 #5）：仅读写本地文件，不请求任何外部服务。
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
BACKUP_DIR = PROJECT_ROOT / "backups"


def _sqlite_snapshot(src: Path, dst: Path) -> bool:
    """用 SQLite 在线备份 API 做一致性快照（不阻塞在线写入）。

    Args:
        src: 源数据库文件。
        dst: 目标备份文件。

    Returns:
        bool: 是否成功。
    """
    try:
        con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            bck = sqlite3.connect(str(dst))
            try:
                con.backup(bck)
            finally:
                bck.close()
        finally:
            con.close()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] SQLite 在线快照失败，回退到文件复制: {exc}", file=sys.stderr)
        try:
            shutil.copy2(src, dst)
            return True
        except Exception as exc2:  # noqa: BLE001
            print(f"[error] 文件复制也失败: {exc2}", file=sys.stderr)
            return False


def backup(retain: int) -> None:
    """执行一次备份并轮转旧备份。

    Args:
        retain: 保留的最近备份份数（含本次）。
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    targets = [
        ("tts_history.db", "history"),
        ("audit.log", "audit"),
    ]
    done = []
    for fname, label in targets:
        src = DATA_DIR / fname
        if not src.exists():
            print(f"[skip] 不存在，跳过: {src}")
            continue
        if fname.endswith(".db"):
            dst = BACKUP_DIR / f"{label}-{ts}.db"
            ok = _sqlite_snapshot(src, dst)
        else:
            dst = BACKUP_DIR / f"{label}-{ts}.log"
            shutil.copy2(src, dst)
            ok = True
        if ok:
            done.append(dst)
            print(f"[ok] 备份 {label}: {dst}")

    # config.yaml 一并备份（治理可追溯）
    cfg = PROJECT_ROOT / "config.yaml"
    if cfg.exists():
        cdir = BACKUP_DIR / f"config-{ts}.yaml"
        shutil.copy2(cfg, cdir)
        done.append(cdir)
        print(f"[ok] 备份 config: {cdir}")

    if not done:
        print("[info] 无可备份文件（data/ 为空？）")
        return

    # 轮转：按文件名时间倒序保留最近 retain 份
    all_backups = sorted(BACKUP_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    excess = all_backups[retain:]
    for old in excess:
        old.unlink()
        print(f"[prune] 删除旧备份: {old}")


def restore(backup_file: Path) -> None:
    """从备份恢复历史库（恢复前自动备份当前版本）。

    Args:
        backup_file: 备份文件路径。
    """
    if not backup_file.exists():
        print(f"[error] 备份文件不存在: {backup_file}", file=sys.stderr)
        sys.exit(1)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    target = DATA_DIR / "tts_history.db"
    # 先备份当前（防误操作）
    if target.exists():
        safe = DATA_DIR / f"tts_history.db.pre-restore-{int(time.time())}"
        shutil.copy2(target, safe)
        print(f"[ok] 已为当前库做恢复前快照: {safe}")
    shutil.copy2(backup_file, target)
    print(f"[done] 已恢复: {target}")


def main() -> None:
    """解析参数并分发。"""
    parser = argparse.ArgumentParser(description="TTS_MultiModel 数据备份/恢复")
    parser.add_argument("--retain", type=int, default=14, help="保留最近备份份数（默认 14）")
    parser.add_argument("--restore", metavar="FILE", help="从指定备份文件恢复历史库")
    args = parser.parse_args()

    if args.restore:
        restore(Path(args.restore).resolve())
    else:
        backup(retain=args.retain)


if __name__ == "__main__":
    main()
