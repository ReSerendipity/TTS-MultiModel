#!/usr/bin/env python3
"""PII 加密密钥轮换脚本（数据治理评估 P0-3）。

用旧密钥解密所有 ``enc:`` 前缀的 text_preview 记录，用新密钥重新加密，
然后更新密钥文件。解决原实现"密钥一旦生成无法轮换"的治理缺口。

特性：
- 轮换前自动备份数据库（``data/history.db.bak_rotate_<timestamp>``）。
- 先验证旧密钥能解密全部记录，失败则中止（不写入）。
- 批量 UPDATE，FTS5 索引经触发器自动同步。
- 轮换后验证新密钥能解密全部记录。
- 支持 dry-run（仅统计，不写入、不换密钥）。
- 密钥文件默认 ``data/.pii_key``（与 ``_get_pii_cipher()`` 回退路径一致）。

用法：
    python scripts/rotate_pii_key.py [--dry-run] [--new-key KEY] [--old-key KEY] [--no-backup]

退出码：0 成功，1 失败。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "app"))

from integrated_app.history_db import (  # noqa: E402
    _PII_PREFIX,
    get_history_db,
)

try:
    from cryptography.fernet import Fernet
except ImportError:
    print("[ERROR] 需要 cryptography 库（pip install cryptography）")
    sys.exit(1)


def _resolve_old_key(explicit: str | None) -> str:
    """解析旧密钥：显式参数 > 环境变量 > config > data/.pii_key。"""
    if explicit:
        return explicit.strip()
    env_key = os.environ.get("TTS_PII_FERNET_KEY", "")
    if env_key:
        return env_key.strip()
    # config
    try:
        from integrated_app.config import get_config

        cfg_key = get_config().pydantic_config.security.pii_encryption_key.get_secret_value()
        if cfg_key:
            return cfg_key.strip()
    except Exception:
        pass
    # data/.pii_key
    key_path = _PROJECT_ROOT / "data" / ".pii_key"
    if key_path.exists():
        return key_path.read_text(encoding="utf-8").strip()
    raise RuntimeError("无法解析旧密钥：请用 --old-key 指定，或确保 data/.pii_key 存在")


def _verify_decrypt_all(conn: sqlite3.Connection, cipher: Fernet) -> tuple[int, str | None]:
    """验证 cipher 能解密所有 enc: 记录。返回 (加密记录数, 失败的 id 或 None)。"""
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, text_preview FROM generation_history WHERE text_preview LIKE ?",
        (f"{_PII_PREFIX}%",),
    ).fetchall()
    for row in rows:
        try:
            cipher.decrypt(row["text_preview"][len(_PII_PREFIX) :].encode("utf-8"))
        except Exception as exc:
            return len(rows), f"id={row['id']}: {exc}"
    return len(rows), None


def rotate(
    dry_run: bool = False, new_key_arg: str | None = None, old_key_arg: str | None = None, do_backup: bool = True
) -> int:
    """执行密钥轮换。"""
    # 1. 解析旧密钥
    try:
        old_key_str = _resolve_old_key(old_key_arg)
    except RuntimeError as exc:
        print(f"[ERROR] {exc}")
        return 1
    try:
        old_cipher = Fernet(old_key_str.encode("utf-8"))
    except Exception as exc:
        print(f"[ERROR] 旧密钥无效: {exc}")
        return 1

    # 2. 生成新密钥
    new_key_str = new_key_arg.strip() if new_key_arg else Fernet.generate_key().decode("utf-8")
    try:
        new_cipher = Fernet(new_key_str.encode("utf-8"))
    except Exception as exc:
        print(f"[ERROR] 新密钥无效: {exc}")
        return 1

    # 3. 连接数据库
    db = get_history_db()
    db_path = db._db_path  # noqa: SLF001
    print(f"[INFO] 历史库路径: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # 4. 验证旧密钥能解密全部记录
        encrypted_count, fail_info = _verify_decrypt_all(conn, old_cipher)
        print(f"[INFO] 加密记录数: {encrypted_count}")
        if fail_info:
            print(f"[ERROR] 旧密钥无法解密部分记录: {fail_info}")
            print("[ERROR] 中止轮换（未写入任何数据）")
            return 1
        print("[INFO] 旧密钥验证通过：全部记录可解密")

        if encrypted_count == 0:
            print("[INFO] 无加密记录，仅更新密钥文件")

        if dry_run:
            print("[INFO] dry-run 模式，不执行写入和密钥替换。")
            print(f"[INFO] 新密钥(前8字符): {new_key_str[:8]}...")
            return 0

        # 5. 备份
        if do_backup:
            ts = time.strftime("%Y%m%d_%H%M%S")
            bak_path = f"{db_path}.bak_rotate_{ts}"
            shutil.copy2(db_path, bak_path)
            print(f"[INFO] 已备份数据库: {bak_path}")

        # 6. 批量轮换：旧密钥解密 → 新密钥加密 → UPDATE
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT id, text_preview FROM generation_history WHERE text_preview LIKE ?",
            (f"{_PII_PREFIX}%",),
        ).fetchall()
        updates: list[tuple[str, int]] = []
        for row in rows:
            plaintext = old_cipher.decrypt(row["text_preview"][len(_PII_PREFIX) :].encode("utf-8")).decode("utf-8")
            re_encrypted = _PII_PREFIX + new_cipher.encrypt(plaintext.encode("utf-8")).decode("utf-8")
            updates.append((re_encrypted, row["id"]))
        if updates:
            conn.executemany("UPDATE generation_history SET text_preview = ? WHERE id = ?", updates)
            conn.commit()
            print(f"[INFO] 已轮换 {len(updates)} 条记录的加密密钥")

        # 7. 写入新密钥到 data/.pii_key
        key_path = _PROJECT_ROOT / "data" / ".pii_key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(new_key_str, encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(key_path, 0o600)
        print(f"[INFO] 新密钥已写入: {key_path}")

        # 8. 验证新密钥能解密全部记录
        verify_count, verify_fail = _verify_decrypt_all(conn, new_cipher)
        if verify_fail:
            print(f"[ERROR] 新密钥验证失败: {verify_fail}")
            print("[ERROR] 请从备份恢复数据库并回滚密钥文件")
            return 1
        print(f"[INFO] 新密钥验证通过：{verify_count} 条记录可解密")

        print("[DONE] 密钥轮换完成")
        return 0
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"[ERROR] 轮换失败: {exc}")
        return 1
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="PII 加密密钥轮换")
    parser.add_argument("--dry-run", action="store_true", help="仅验证旧密钥，不写入")
    parser.add_argument("--new-key", type=str, default=None, help="指定新密钥（默认自动生成）")
    parser.add_argument("--old-key", type=str, default=None, help="指定旧密钥（默认自动解析）")
    parser.add_argument("--no-backup", action="store_true", help="跳过数据库备份")
    args = parser.parse_args()
    return rotate(
        dry_run=args.dry_run, new_key_arg=args.new_key, old_key_arg=args.old_key, do_backup=not args.no_backup
    )


if __name__ == "__main__":
    sys.exit(main())
