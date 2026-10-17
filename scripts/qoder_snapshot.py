#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
""".qoder 本地资产层快照 / 回退点机制（Better Harness 待批 item6.3）。

背景（口径与依据全文见 ``docs/qoder-snapshot.md``）：

- ``.qoder/`` 按 AGENTS.md §00 定案**不入库**（``.gitignore`` L209 整目录忽略），
  Qoder 钩子注册、编辑后验证钩子脚本、fixtures 等资产只存在于本机；
- 没有版本控制就没有回退点——P5（把 event 贯穿 4 个调用点）因此「既不能撤销也不能证明」；
- 本批次推荐口径：**给快照/回退点机制，而不是把整层入库**。本脚本就是该口径的
  可执行载体，不改变「.qoder/ 不进 git」的既定边界。

子命令：

- ``create``  把 ``.qoder/`` 整树复制为一份带 sha256 清单的快照
- ``list``    列出全部快照并逐份复验清单（CORRUPT 即告警）
- ``diff``    两个快照之间 / 快照与当前 ``.qoder`` 的差异（退出码 1=有差异）
- ``restore`` 把指定快照写回 ``.qoder/``；**先把当前状态存为 pre-restore 快照**，
              还原前逐文件复验 sha256，不符即中止（fail-closed，不写半个坏树）
- ``prune``   只保留最近 N 份快照

存储位置：默认 ``<repo>/.qoder-snapshots/``（与 .qoder 同在 .gitignore）；
``--out`` 可指向仓外耐久位置——``git clean -xfd`` 会连忽略文件一起删，
需要抗清理的快照必须放仓外。

离线优先（AGENTS.md 硬约束 #5）：仅读写本地文件，唯一的外部调用是
``git rev-parse HEAD`` 只为在清单里记一个当时的提交锚点，取不到就记 null，
不影响任何子命令的成败。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = PROJECT_ROOT / ".qoder"
DEFAULT_STORE = PROJECT_ROOT / ".qoder-snapshots"
MANIFEST_NAME = "manifest.json"
# store 下所有快照目录（create 的 qoder-snap- 与 restore 的 qoder-pre-restore-）共用此前缀。
SNAPSHOT_PREFIX = "qoder-"
CREATE_PREFIX = "qoder-snap-"
RESTORE_GUARD_PREFIX = "qoder-pre-restore-"


def _now_stamp() -> str:
    """UTC 时间戳（快照目录名按字典序即时间序）。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_rel_files(root: Path) -> list[str]:
    """root 下全部文件的相对路径（POSIX 风格，排序保证清单稳定）。"""
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and not p.is_symlink())


def _build_manifest(source: Path, note: str | None) -> dict:
    files: dict[str, dict[str, object]] = {}
    total = 0
    for rel in _iter_rel_files(source):
        target = source / rel
        size = target.stat().st_size
        total += size
        files[rel] = {"sha256": _sha256(target), "size": size}
    return {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source.name,
        "note": note or "",
        "git_head": _git_head(),
        "file_count": len(files),
        "total_bytes": total,
        "files": files,
    }


def _git_head() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    head = proc.stdout.strip()
    return head or None


def _unique_snapshot_dir(store: Path, prefix: str) -> Path:
    candidate = store / f"{prefix}{_now_stamp()}"
    seq = 2
    while candidate.exists():
        candidate = store / f"{prefix}{_now_stamp()}-{seq}"
        seq += 1
    return candidate


def _write_snapshot(source: Path, store: Path, note: str | None, prefix: str = CREATE_PREFIX) -> Path:
    """把 source 整树复制成一份快照并附清单，返回快照目录。"""
    if not source.is_dir():
        raise FileNotFoundError(f"源目录不存在：{source}")
    store.mkdir(parents=True, exist_ok=True)
    snap = _unique_snapshot_dir(store, prefix)
    shutil.copytree(source, snap)
    manifest = _build_manifest(source, note)
    # 清单按快照内容算，不按目标目录名；复制完成后落盘。
    (snap / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return snap


def _load_manifest(snap: Path) -> dict:
    manifest_path = snap / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"快照缺少 {MANIFEST_NAME}：{snap}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _verify_snapshot(snap: Path, manifest: dict) -> list[str]:
    """逐文件复验 sha256，返回不一致/缺失的清单（空=完好）。"""
    problems: list[str] = []
    for rel, meta in manifest.get("files", {}).items():
        target = snap / rel
        if not target.is_file():
            problems.append(f"缺失：{rel}")
            continue
        if _sha256(target) != meta.get("sha256"):
            problems.append(f"校验不符：{rel}")
    return problems


def _snapshot_sort_key(snap: Path) -> tuple[str, str]:
    """按清单里的创建时间排序（跨前缀也正确）；清单损坏的排最前，prune 先删。"""
    try:
        manifest = _load_manifest(snap)
        return (str(manifest.get("created_at", "")), snap.name)
    except (OSError, ValueError):
        return ("", snap.name)


def _list_snapshots(store: Path) -> list[Path]:
    if not store.is_dir():
        return []
    return sorted(
        (p for p in store.iterdir() if p.is_dir() and p.name.startswith(SNAPSHOT_PREFIX)),
        key=_snapshot_sort_key,
    )


def cmd_create(args: argparse.Namespace) -> int:
    snap = _write_snapshot(args.source, args.store, args.note)
    manifest = _load_manifest(snap)
    print(
        f"[OK] 快照已创建：{snap}\n"
        f"     文件 {manifest['file_count']} 个 / {manifest['total_bytes']} 字节"
        f"，git 锚点 {manifest['git_head'] or '（无）'}"
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    snaps = _list_snapshots(args.store)
    if not snaps:
        print(f"（{args.store} 下没有任何快照）")
        return 0
    broken = 0
    for snap in snaps:
        try:
            manifest = _load_manifest(snap)
            problems = _verify_snapshot(snap, manifest)
        except (OSError, ValueError) as exc:
            print(f"[CORRUPT] {snap.name}：{exc}")
            broken += 1
            continue
        status = "OK" if not problems else "CORRUPT"
        if problems:
            broken += 1
        print(
            f"[{status}] {snap.name}  文件 {manifest['file_count']} 个"
            f"，创建于 {manifest['created_at']}"
            f"，锚点 {manifest['git_head'] or '（无）'}"
        )
        for line in problems:
            print(f"    - {line}")
    return 1 if broken else 0


def _diff_manifests(a: dict, b: dict) -> dict[str, list[str]]:
    files_a = a.get("files", {})
    files_b = b.get("files", {})
    added = sorted(set(files_b) - set(files_a))
    removed = sorted(set(files_a) - set(files_b))
    changed = sorted(
        rel for rel in set(files_a) & set(files_b) if files_a[rel].get("sha256") != files_b[rel].get("sha256")
    )
    return {"added": added, "removed": removed, "changed": changed}


def _print_diff(title: str, diff: dict[str, list[str]]) -> int:
    total = sum(len(v) for v in diff.values())
    print(f"{title}：新增 {len(diff['added'])} / 删除 {len(diff['removed'])} / 变更 {len(diff['changed'])}")
    for rel in diff["added"]:
        print(f"  + {rel}")
    for rel in diff["removed"]:
        print(f"  - {rel}")
    for rel in diff["changed"]:
        print(f"  ~ {rel}")
    return 1 if total else 0


def cmd_diff(args: argparse.Namespace) -> int:
    store = args.store
    snap_a = store / args.snapshot_a if not Path(args.snapshot_a).is_dir() else Path(args.snapshot_a)
    manifest_a = _load_manifest(snap_a)
    if args.snapshot_b:
        snap_b = store / args.snapshot_b if not Path(args.snapshot_b).is_dir() else Path(args.snapshot_b)
        manifest_b = _load_manifest(snap_b)
        title = f"{snap_a.name} -> {snap_b.name}"
    elif args.current:
        if not args.source.is_dir():
            print(f"[FAIL] 当前源目录不存在：{args.source}", file=sys.stderr)
            return 2
        manifest_b = _build_manifest(args.source, None)
        title = f"{snap_a.name} -> 当前 {args.source.name}/"
    else:
        print("[FAIL] diff 需要第二个快照名或 --current", file=sys.stderr)
        return 2
    return _print_diff(title, _diff_manifests(manifest_a, manifest_b))


def cmd_restore(args: argparse.Namespace) -> int:
    snap = Path(args.snapshot) if Path(args.snapshot).is_dir() else args.store / args.snapshot
    if not snap.is_dir():
        print(f"[FAIL] 快照不存在：{snap}", file=sys.stderr)
        return 2
    try:
        manifest = _load_manifest(snap)
    except (OSError, ValueError) as exc:
        print(f"[FAIL] 清单不可读，拒绝还原（fail-closed）：{exc}", file=sys.stderr)
        return 2
    problems = _verify_snapshot(snap, manifest)
    if problems:
        print("[FAIL] 快照清单复验不符，拒绝还原（fail-closed）：", file=sys.stderr)
        for line in problems:
            print(f"    - {line}", file=sys.stderr)
        return 2

    # 先把当前状态存成 pre-restore 快照，再谈还原——回退点自己也要有回退点。
    if args.source.is_dir():
        guard = _write_snapshot(
            args.source,
            args.store,
            f"restore 前自动备份（目标快照 {snap.name}）",
            prefix=RESTORE_GUARD_PREFIX,
        )
        print(f"[OK] 当前状态已存为还原前备份：{guard.name}")
    else:
        print("（当前没有 .qoder/，跳过还原前备份）")

    staging = args.source.with_name(args.source.name + ".restore-staging")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(snap, staging, ignore=shutil.ignore_patterns(MANIFEST_NAME))
    if args.source.exists():
        shutil.rmtree(args.source)
    staging.rename(args.source)
    print(f"[OK] 已从 {snap.name} 还原 {manifest['file_count']} 个文件到 {args.source}")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    snaps = _list_snapshots(args.store)
    excess = snaps[: max(0, len(snaps) - args.keep)]
    for snap in excess:
        shutil.rmtree(snap)
        print(f"[OK] 已删除最旧快照：{snap.name}")
    if not excess:
        print(f"（快照 {len(snaps)} 份 ≤ 保留数 {args.keep}，无需清理）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=".qoder 本地资产层快照 / 回退点机制（docs/qoder-snapshot.md）",
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help=".qoder 目录（默认 <repo>/.qoder）")
    parser.add_argument(
        "--store", type=Path, default=DEFAULT_STORE, help="快照存储目录（默认 <repo>/.qoder-snapshots）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="创建一份快照")
    p_create.add_argument("--note", default=None, help="快照备注（写进 manifest）")
    p_create.set_defaults(func=cmd_create)

    p_list = sub.add_parser("list", help="列出快照并复验清单")
    p_list.set_defaults(func=cmd_list)

    p_diff = sub.add_parser("diff", help="对比两个快照或快照与当前 .qoder")
    p_diff.add_argument("snapshot_a", help="快照名（或快照目录路径）")
    p_diff.add_argument("snapshot_b", nargs="?", default=None, help="第二个快照名；缺省时配合 --current")
    p_diff.add_argument("--current", action="store_true", help="与当前 .qoder/ 对比")
    p_diff.set_defaults(func=cmd_diff)

    p_restore = sub.add_parser("restore", help="从快照还原（先自动备份当前状态）")
    p_restore.add_argument("snapshot", help="快照名（或快照目录路径）")
    p_restore.set_defaults(func=cmd_restore)

    p_prune = sub.add_parser("prune", help="只保留最近 N 份快照")
    p_prune.add_argument("--keep", type=int, default=10, help="保留份数（默认 10）")
    p_prune.set_defaults(func=cmd_prune)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
