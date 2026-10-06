#!/usr/bin/env python3
"""scripts/migrate_history_feedback.py — history.db 反馈字段幂等迁移（P2-#8）。

为 generation_history 表新增用户反馈字段：
  - feedback_rating  INTEGER   可空；1..5 星级，或 -1(点踩)/+1(点赞)，语义由 UI 决定
  - feedback_liked   INTEGER   可空；0=踩，1=赞，NULL=未表态
  - feedback_note    TEXT      默认 ''；用户文字备注
  - feedback_at      TEXT      可空；反馈提交时间（ISO/UTC，datetime('now')）

幂等：四个列均已存在则跳过；重复运行安全。迁移完成后写入 _schema_migrations。

回滚说明（SQLite 不支持 DROP COLUMN 的旧版本需重建表；3.35+ 可直接 DROP COLUMN）：
  -- 回滚 v008_feedback：删除四列并清版本记录
  ALTER TABLE generation_history DROP COLUMN feedback_rating;
  ALTER TABLE generation_history DROP COLUMN feedback_liked;
  ALTER TABLE generation_history DROP COLUMN feedback_note;
  ALTER TABLE generation_history DROP COLUMN feedback_at;
  DELETE FROM _schema_migrations WHERE version='v008_feedback';
  若 SQLite < 3.35，改用：CREATE TABLE generation_history_new AS SELECT <原25列> ...;
  DROP TABLE generation_history; ALTER TABLE generation_history_new RENAME TO generation_history;

用法：
  python scripts/migrate_history_feedback.py [--db data/history.db] [--dry-run]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

VERSION = "v008_feedback"
DESCRIPTION = "添加反馈字段（feedback_rating/feedback_liked/feedback_note/feedback_at）"

COLUMNS: list[tuple[str, str]] = [
    ("feedback_rating", "INTEGER"),
    ("feedback_liked", "INTEGER"),
    ("feedback_note", "TEXT DEFAULT ''"),
    ("feedback_at", "TEXT"),
]


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS _schema_migrations (
               version TEXT PRIMARY KEY,
               description TEXT NOT NULL,
               applied_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/history.db")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        conn.execute("BEGIN")
        _ensure_migrations_table(conn)
        existing = _existing_columns(conn, "generation_history")
        missing = [c for c, _ in COLUMNS if c not in existing]
        applied = {row[0] for row in conn.execute("SELECT version FROM _schema_migrations")}

        if not missing and VERSION in applied:
            print(f"[skip] {VERSION} 已应用（四列齐全），无需迁移。")
            conn.rollback()
            return 0

        n_before = conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0]
        print(f"[info] db={args.db} generation_history 行数={n_before}")
        for col, ddl in COLUMNS:
            if col not in missing:
                continue
            print(f"[alter] ADD COLUMN {col} {ddl}")
            if not args.dry_run:
                conn.execute(f"ALTER TABLE generation_history ADD COLUMN {col} {ddl}")

        if not args.dry_run:
            conn.execute(
                "INSERT OR IGNORE INTO _schema_migrations (version, description) VALUES (?, ?)",
                (VERSION, DESCRIPTION),
            )
            conn.commit()
            print(f"[ok] {VERSION} 已记录到 _schema_migrations。")
        else:
            conn.rollback()
            print("[dry-run] 未提交。")

        # 校验
        after = _existing_columns(conn, "generation_history")
        have = all(c in after for c, _ in COLUMNS)
        print(f"[verify] 反馈四列齐全={have}")
        return 0 if have else 1
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        print(f"[error] {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
