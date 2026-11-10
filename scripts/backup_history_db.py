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
import hashlib
import json
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


# ============ 备份集覆盖口径（O4） ============
# 债项原文：`data/` 下 5 个运行密钥无任何备份覆盖、`generation_versioning` 库不在默认备份集、
# `data/audit.log` 实测未落盘。
# 只备 history.db 的「备份」在灾难恢复时是丢三样东西：① 密钥丢了 → 库里的 PII 字段与 HMAC 全部
# 解不开、也无法验证未被篡改；② 版本链丢了 → 回滚时找不到原始产物；③ 审计日志丢了 → 事后追责
# 没有证据。所以口径必须是「整集」，并且**缺失要如实记账**（manifest 里 present:false + 原因），
# 不能因为文件不存在就悄悄跳过——那和没做备份长得一模一样。
KEY_FILES = (".pii_key", ".history_hmac_key", ".watermark_key", ".csrf_secret", ".manifest_signing_key")
AUDIT_FILES = ("audit.log", "audit.log.1", "audit.log.2", "audit.log.3")


def _hmac_key_home() -> str:
    """`.history_hmac_key` 的现行家目录。

    2026-11 起该密钥迁到用户配置目录（Windows `%APPDATA%/tts-multimodel`、
    Linux/macOS `~/.config/tts-multimodel`），旧路径 `data/.history_hmac_key` 仍兼容。
    """
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or ""
        return os.path.join(appdata, "tts-multimodel") if appdata else ""
    return os.path.expanduser("~/.config/tts-multimodel")


def _artifact(name: str, kind: str, paths: list[str], required: bool, secret: bool) -> dict:
    """构造一条覆盖计划：按候选路径顺序找第一个存在的文件。"""
    found = next((p for p in paths if p and os.path.exists(p)), None)
    size = os.path.getsize(found) if found else 0
    return {
        "name": name,
        "kind": kind,
        "path": found,
        "candidates": [p for p in paths if p],
        "present": bool(found),
        "required": required,
        "secret": secret,
        "size": size,
        "reason": "" if found else "file_not_found",
    }


def build_coverage_plan(data_dir: str, save_dir: str, history_db: str, hmac_dir: str | None = None) -> list[dict]:
    """列出备份集应覆盖的对象及其现状（纯读，不写盘）。

    Args:
        data_dir: `data/` 目录（密钥与审计日志所在地）。
        save_dir: `outputs/` 目录（`generation_versions.db` 所在地）。
        history_db: 历史库路径。
        hmac_dir: `.history_hmac_key` 的用户配置目录（None 时按平台默认推断）。
    """
    hmac_home = hmac_dir if hmac_dir is not None else _hmac_key_home()
    plan: list[dict] = [
        _artifact("history.db", "history_db", [history_db], True, False),
        _artifact(
            "generation_versions.db",
            "versioning_db",
            [os.path.join(save_dir, "generation_versions.db")],
            False,
            False,
        ),
    ]
    for key in KEY_FILES:
        candidates = [os.path.join(data_dir, key)]
        if key == ".history_hmac_key":
            candidates.insert(0, os.path.join(hmac_home, key))
        plan.append(_artifact(key, "key", candidates, False, True))
    for log_name in AUDIT_FILES:
        plan.append(_artifact(log_name, "audit_log", [os.path.join(data_dir, log_name)], False, False))
    return plan


def _print_plan(plan: list[dict]) -> None:
    for a in plan:
        state = "有" if a["present"] else "缺"
        flag = "（密钥，含明文）" if a["secret"] else ""
        path = a["path"] or (a["candidates"][0] if a["candidates"] else "-")
        print(f"[coverage] {state}  {a['kind']:<14} {a['name']:<24} {path}{flag}")
    present = sum(1 for a in plan if a["present"])
    missing_req = [a["name"] for a in plan if a["required"] and not a["present"]]
    _log(f"覆盖计划：{present}/{len(plan)} 项在场；必需项缺失 {len(missing_req)} 个")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_db_consistent(src: str, dest: str) -> None:
    """库文件用 SQLite backup API 复制（运行期也一致），非 SQLite 或失败则退回字节复制。"""
    try:
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        try:
            d = sqlite3.connect(dest)
            try:
                s.backup(d)
                d.commit()
            finally:
                d.close()
        finally:
            s.close()
        return
    except sqlite3.Error:
        with contextlib.suppress(FileExistsError):
            os.remove(dest)
        shutil.copy2(src, dest)


def create_full_backup(plan: list[dict], backup_dir: str) -> str:
    """按覆盖计划生成整集备份目录（含 manifest.json）。

    Returns:
        str: 备份目录路径。
    """
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle = os.path.join(backup_dir, f"full_{stamp}")
    seq = 1
    while os.path.exists(bundle):
        bundle = os.path.join(backup_dir, f"full_{stamp}_{seq}")
        seq += 1
    os.makedirs(bundle, exist_ok=True)

    entries: list[dict] = []
    for a in plan:
        entry = {
            "name": a["name"],
            "kind": a["kind"],
            "present": False,
            "secret": a["secret"],
            "source": a["path"],
            "file": "",
            "size": 0,
            "sha256": "",
            "reason": a["reason"],
        }
        if a["present"] and a["path"]:
            dest = os.path.join(bundle, a["name"].lstrip(".") if a["kind"] == "key" else a["name"])
            if a["kind"] in ("history_db", "versioning_db"):
                _copy_db_consistent(a["path"], dest)
            else:
                shutil.copy2(a["path"], dest)
            entry.update(
                {
                    "present": True,
                    "file": os.path.basename(dest),
                    "size": os.path.getsize(dest),
                    "sha256": _sha256(dest),
                    "reason": "",
                }
            )
        entries.append(entry)

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "contains_secrets": any(e["secret"] and e["present"] for e in entries),
        "artifacts": entries,
    }
    manifest_path = os.path.join(bundle, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    missing = [e["name"] for e in entries if not e["present"]]
    _log(f"整集备份: {bundle}（{len(entries) - len(missing)}/{len(entries)} 项在场）")
    if missing:
        # 不静默：缺项进 manifest，也在 stdout 点名，让调度日志里能看见
        _log(f"⚠️ 未覆盖（已在 manifest 记账）: {', '.join(missing)}")
    return bundle


def verify_full_backup(bundle_dir: str) -> list[str]:
    """校验整集备份：manifest 必须存在且非空，每条 present 项的文件存在且 sha256 一致。"""
    manifest_path = os.path.join(bundle_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return [f"manifest 缺失: {manifest_path}"]
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError) as exc:
        return [f"manifest 不可读: {exc}"]

    artifacts = manifest.get("artifacts") or []
    if not artifacts:
        # 空 manifest 不算通过：它证明不了备份里有什么
        return ["manifest.artifacts 为空，备份内容不可证明"]

    errors: list[str] = []
    for e in artifacts:
        if not e.get("present"):
            continue
        path = os.path.join(bundle_dir, e.get("file", ""))
        if not os.path.exists(path):
            errors.append(f"{e.get('name')}: manifest 记为在场但文件不存在")
            continue
        actual = _sha256(path)
        if actual != e.get("sha256"):
            errors.append(f"{e.get('name')}: sha256 不一致（manifest={e.get('sha256')[:12]}… 实际={actual[:12]}…）")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="历史库备份 / 恢复 / 恢复演练")
    parser.add_argument("--db-path", default=None, help="源数据库路径（默认按 config.yaml 解析）")
    parser.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR, help="备份目录（默认 data/backups）")
    parser.add_argument("--keep", type=int, default=5, help="保留备份份数（默认 5，0=不清理）")
    parser.add_argument("--compress", action="store_true", help="备份后 gzip 压缩")
    parser.add_argument("--restore", default=None, help="从指定备份文件恢复")
    parser.add_argument("--target", default=None, help="恢复目标路径（配合 --restore 使用）")
    parser.add_argument("--drill", action="store_true", help="执行恢复演练（不修改生产库）")
    parser.add_argument("--full", action="store_true", help="按覆盖口径备份整个备份集（库 + 运行密钥 + 审计日志）")
    parser.add_argument("--coverage", action="store_true", help="只打印备份集覆盖计划（不写盘），有缺口时非零退出")
    parser.add_argument("--verify", default=None, help="校验一个覆盖备份目录（比对 manifest 的 sha256）")
    args = parser.parse_args()

    db_path = args.db_path or get_history_db_path()
    backup_dir = args.backup_dir
    if not os.path.isabs(backup_dir):
        backup_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), backup_dir)

    if args.verify:
        errors = verify_full_backup(args.verify)
        for e in errors:
            print(f"[backup-verify] {e}", file=sys.stderr)
        if errors:
            return 1
        _log(f"✅ 覆盖备份校验通过: {args.verify}")
        return 0

    if args.coverage or args.full:
        plan = build_coverage_plan(
            data_dir=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
            save_dir=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs"),
            history_db=db_path,
        )
        _print_plan(plan)
        if args.coverage:
            # 有缺口就非零退出：让"备份集不全"这件事成为可被调度的红灯，而不是一句备注
            missing = [a for a in plan if a["required"] and not a["present"]]
            return 1 if missing else 0
        bundle = create_full_backup(plan, backup_dir)
        _log(f"覆盖备份完成: {bundle}")
        return 0

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
