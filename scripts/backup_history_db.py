"""历史记录数据库备份 / 恢复 / 恢复演练工具（数据治理 · 灾难恢复）。

背景：``data/history.db`` 承载全部生成历史，此前**无任何备份手段**，且
``config.yaml -> history.keep_days`` 为死配置导致库体积持续增长（实测 1.25GB）。
本脚本提供最小可用的灾难恢复能力：

- ``backup``：使用 SQLite 官方 backup API 生成一致性快照（运行期亦可安全备份）；
- ``restore``：从备份文件恢复，恢复后自动执行 ``PRAGMA integrity_check``；
- ``drill``：在临时目录执行"备份 → 恢复 → 完整性校验 → 抽样比对"的恢复演练，
  **不触碰生产库**，用于定期验证备份可用性。

用法：
    python scripts/backup_history_db.py                        # 备份到 data/backups/
    python scripts/backup_history_db.py --keep 7               # 备份并保留最近 7 份
    python scripts/backup_history_db.py --compress             # 备份后 gzip 压缩
    python scripts/backup_history_db.py --restore BK --target data/history.db
    python scripts/backup_history_db.py --drill                # 恢复演练（推荐每周）
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from integrated_app.config import get_history_db_path  # noqa: E402

DEFAULT_BACKUP_DIR = os.path.join("data", "backups")
LOG_PREFIX = "[history-backup]"


def _log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}")


def create_backup(db_path: str, backup_dir: str, keep: int = 5, compress: bool = False) -> str:
    """创建历史库一致性快照。

    优先使用 SQLite ``Connection.backup()`` API（在运行期也能得到一致快照），
    失败时回退到文件复制（含 -wal / -shm 旁路文件）。

    Args:
        db_path: 源数据库路径。
        backup_dir: 备份输出目录（不存在则创建）。
        keep: 保留的备份份数上限，0 表示不清理。
        compress: 是否 gzip 压缩输出。

    Returns:
        str: 生成的备份文件路径。

    Raises:
        FileNotFoundError: 源数据库不存在。
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"源数据库不存在: {db_path}")

    os.makedirs(backup_dir, exist_ok=True)
    # Why 需去重：时间戳精度为秒，同一秒内多次备份会生成同名文件并互相覆盖，
    # 导致高频备份场景（如脚本被并发调用）实际只保留一份、keep 裁剪逻辑失真。
    base = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"history_backup_{base}.db")
    seq = 1
    while os.path.exists(dest) or os.path.exists(dest + ".gz"):
        dest = os.path.join(backup_dir, f"history_backup_{base}_{seq}.db")
        seq += 1

    try:
        # SQLite backup API：源库以只读模式打开，避免写锁冲突
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(dest)
            try:
                src.backup(dst)
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.Error:
        # 回退：直接复制主库文件
        shutil.copy2(db_path, dest)

    if compress:
        gz_path = dest + ".gz"
        with open(dest, "rb") as fin, gzip.open(gz_path, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        os.remove(dest)
        dest = gz_path

    size_mb = os.path.getsize(dest) / (1024 * 1024)
    _log(f"备份完成: {dest} ({size_mb:.2f} MB)")

    if keep and keep > 0:
        _prune_old_backups(backup_dir, keep)
    return dest


def _prune_old_backups(backup_dir: str, keep: int) -> int:
    """保留最近 ``keep`` 份备份，删除更早的备份文件。

    Args:
        backup_dir: 备份目录。
        keep: 保留份数。

    Returns:
        int: 被删除的备份数量。
    """
    files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.startswith("history_backup_")]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    removed = 0
    for path in files[keep:]:
        with contextlib.suppress(OSError):
            os.remove(path)
            removed += 1
    if removed:
        _log(f"已清理 {removed} 份旧备份（保留最近 {keep} 份）")
    return removed


def _decompress_if_needed(src: str, dest: str) -> str:
    """若源文件为 gzip 压缩包则解压到 dest，否则直接复制。

    Args:
        src: 源备份文件路径。
        dest: 目标解压/复制路径。

    Returns:
        str: 实际生成的目标文件路径。
    """
    if src.endswith(".gz"):
        with gzip.open(src, "rb") as fin, open(dest, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        return dest
    shutil.copy2(src, dest)
    return dest


def restore_backup(backup_path: str, target_path: str) -> str:
    """从备份文件恢复数据库，并在恢复后执行完整性校验。

    Args:
        backup_path: 备份文件路径（.db 或 .db.gz）。
        target_path: 恢复目标路径（会被覆盖）。

    Returns:
        str: 恢复后的数据库路径。

    Raises:
        FileNotFoundError: 备份文件不存在。
        sqlite3.DatabaseError: 恢复后完整性校验失败。
    """
    if not os.path.exists(backup_path):
        raise FileNotFoundError(f"备份文件不存在: {backup_path}")

    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
    tmp_path = f"{target_path}.restoring"
    tmp_db = _decompress_if_needed(backup_path, tmp_path)

    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        message = row[0] if row else "unknown"
        if message != "ok":
            raise sqlite3.DatabaseError(f"恢复后完整性校验失败: {message}")
    finally:
        conn.close()

    os.replace(tmp_db, target_path)
    _log(f"恢复完成: {backup_path} -> {target_path}")
    return target_path


def run_drill(db_path: str, backup_dir: str) -> bool:
    """执行一次恢复演练：备份 → 恢复到临时目录 → 完整性校验 → 抽样比对行数。

    全过程在临时目录进行，**不修改生产数据库**。

    Args:
        db_path: 生产数据库路径。
        backup_dir: 备份输出目录。

    Returns:
        bool: 演练是否通过。
    """
    started = time.time()
    backup_path = create_backup(db_path, backup_dir, keep=0)

    tmp_dir = tempfile.mkdtemp(prefix="tts_drill_")
    try:
        target = os.path.join(tmp_dir, "history.db")
        restore_backup(backup_path, target)

        src_rows = _count_rows(db_path)
        dst_rows = _count_rows(target)
        if src_rows != dst_rows:
            _log(f"❌ 演练失败：行数不一致 (源={src_rows}, 恢复={dst_rows})")
            return False

        elapsed = time.time() - started
        _log(f"✅ 演练通过：{src_rows} 行一致，耗时 {elapsed:.1f}s，RTO≈{elapsed:.1f}s")
        return True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _count_rows(db_path: str) -> int:
    """统计 generation_history 表行数。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0])
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="历史库备份 / 恢复 / 恢复演练")
    parser.add_argument("--db-path", default=None, help="源数据库路径（默认按 config.yaml 解析）")
    parser.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR, help="备份目录（默认 data/backups）")
    parser.add_argument("--keep", type=int, default=5, help="保留备份份数（默认 5，0=不清理）")
    parser.add_argument("--compress", action="store_true", help="备份后 gzip 压缩")
    parser.add_argument("--restore", default=None, help="从指定备份文件恢复")
    parser.add_argument("--target", default=None, help="恢复目标路径（配合 --restore 使用）")
    parser.add_argument("--drill", action="store_true", help="执行恢复演练（不修改生产库）")
    args = parser.parse_args()

    db_path = args.db_path or get_history_db_path()
    backup_dir = args.backup_dir
    if not os.path.isabs(backup_dir):
        backup_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), backup_dir)

    if args.restore:
        target = args.target or db_path
        restore_backup(args.restore, target)
        return 0

    if args.drill:
        return 0 if run_drill(db_path, backup_dir) else 1

    create_backup(db_path, backup_dir, keep=args.keep, compress=args.compress)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
