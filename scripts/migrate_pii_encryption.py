#!/usr/bin/env python3
"""P1 安全整改：历史记录 PII 字段一次性加密迁移脚本。

将 ``generation_history.text_preview`` 中尚未加密的旧明文行批量加密为
``enc:`` 前缀的 Fernet 密文，与 pii_encryption_enabled 默认开启后的新记录
保持一致。

特性：
- 幂等：已加密行（``enc:`` 前缀）自动跳过，重复运行不会双重加密。
- 密钥解析与 history_db 完全一致（env → config → data/.pii_key 自动生成）。
- 批量 UPDATE，FTS5 索引经 ``generation_history_au`` 触发器自动同步。
- 事务保护：单批失败回滚该批，不影响已提交批次。

用法：
    python scripts/migrate_pii_encryption.py [--dry-run] [--batch-size 500]

退出码：0 成功，1 失败。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

# 确保项目根目录在 sys.path 中（脚本从仓库根运行）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "app"))

from integrated_app.history_db import (  # noqa: E402
    _PII_PREFIX,
    _encrypt_pii,
    _get_pii_cipher,
    get_history_db,
)


def migrate(dry_run: bool = False, batch_size: int = 500) -> int:
    """执行 PII 加密迁移。

    Args:
        dry_run: 仅统计待迁移行数，不实际写入。
        batch_size: 每批处理行数。

    Returns:
        实际迁移（或 dry-run 统计）的行数。
    """
    cipher = _get_pii_cipher()
    if cipher is None:
        print("[ERROR] 无法获取 PII 加密密钥。请检查 pii_encryption_enabled 配置或 TTS_PII_FERNET_KEY 环境变量。")
        return 1

    db = get_history_db()
    db_path = db._db_path  # noqa: SLF001 — 迁移脚本需直接访问底层 DB
    print(f"[INFO] 历史库路径: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        total = cur.execute(
            "SELECT COUNT(*) FROM generation_history WHERE text_preview != '' AND text_preview NOT LIKE ?",
            (f"{_PII_PREFIX}%",),
        ).fetchone()[0]
        print(f"[INFO] 待迁移明文记录数: {total}")

        if dry_run:
            print("[INFO] dry-run 模式，不执行写入。")
            return 0

        if total == 0:
            print("[INFO] 无需迁移（所有记录已加密或为空）。")
            return 0

        migrated = 0
        start = time.monotonic()
        while True:
            rows = cur.execute(
                "SELECT id, text_preview FROM generation_history "
                "WHERE text_preview != '' AND text_preview NOT LIKE ? LIMIT ?",
                (f"{_PII_PREFIX}%", batch_size),
            ).fetchall()
            if not rows:
                break
            updates: list[tuple[str, int]] = []
            for row in rows:
                encrypted = _encrypt_pii(row["text_preview"])
                if encrypted.startswith(_PII_PREFIX):
                    updates.append((encrypted, row["id"]))
            if updates:
                conn.executemany("UPDATE generation_history SET text_preview = ? WHERE id = ?", updates)
                conn.commit()
                migrated += len(updates)
                elapsed = time.monotonic() - start
                rate = migrated / elapsed if elapsed > 0 else 0
                print(f"[PROGRESS] 已迁移 {migrated}/{total} ({rate:.0f} 行/秒)")
            else:
                # 所有行加密失败（不应发生），跳出避免死循环
                print("[WARN] 本批次无成功加密记录，终止迁移。")
                break

        elapsed = time.monotonic() - start
        print(f"[DONE] 迁移完成: {migrated} 行，耗时 {elapsed:.1f}s")
        return 0
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"[ERROR] 迁移失败: {exc}")
        return 1
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="历史记录 PII 字段一次性加密迁移")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写入")
    parser.add_argument("--batch-size", type=int, default=500, help="每批处理行数（默认 500）")
    args = parser.parse_args()
    return migrate(dry_run=args.dry_run, batch_size=args.batch_size)


if __name__ == "__main__":
    sys.exit(main())
