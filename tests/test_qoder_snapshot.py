"""scripts/qoder_snapshot.py 的快照 / 复验 / 回退能力测试（全部在 tmp_path 内完成）。

对应 Better Harness 待批 item6.3：``.qoder/`` 按 AGENTS.md §00 定案不入库，
编辑验证钩子等本地资产因此没有回退点。本测试锁住该机制的四个不变量：

1. create 产出的快照带逐文件 sha256 清单，且清单与磁盘内容一致；
2. restore 能把改动整体回退到快照态，**且还原前自动留存 pre-restore 备份**；
3. 快照被篡改时 restore **拒绝执行**（fail-closed）——回退点自己坏掉时不许写坏树；
4. diff 能识别新增 / 删除 / 内容变更，prune 只删最旧。

测试不碰真实 .qoder/（source/store 全部指向 tmp_path）。
"""

from __future__ import annotations

import os
import sys

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import qoder_snapshot as qs  # noqa: E402


def _seed_source(root):
    """造一个最小 .qoder 树：嵌套目录 + 两类文件。"""
    src = root / ".qoder"
    (src / "hooks").mkdir(parents=True)
    (src / "rules").mkdir(parents=True)
    (src / "hooks" / "edit_verify_loop.py").write_text("print('v1')\n", encoding="utf-8")
    (src / "rules" / "test-rules.md").write_text("# rules v1\n", encoding="utf-8")
    (src / "settings.json").write_text('{"a": 1}\n', encoding="utf-8")
    return src


def _make_args(source, store, **over):
    args = qs.build_parser().parse_args(["create"])
    args.source = source
    args.store = store
    for key, value in over.items():
        setattr(args, key, value)
    return args


def test_create_snapshot_manifest_matches_disk(tmp_path):
    src = _seed_source(tmp_path)
    store = tmp_path / "snaps"

    rc = qs.main(["--source", str(src), "--store", str(store), "create", "--note", "t"])

    assert rc == 0
    snaps = qs._list_snapshots(store)
    assert len(snaps) == 1
    manifest = qs._load_manifest(snaps[0])
    assert manifest["file_count"] == 3
    assert manifest["note"] == "t"
    assert set(manifest["files"]) == {
        "hooks/edit_verify_loop.py",
        "rules/test-rules.md",
        "settings.json",
    }
    # 清单不是摆设：逐文件复验必须为空。
    assert qs._verify_snapshot(snaps[0], manifest) == []
    # 清单本身也落在快照里。
    assert (snaps[0] / qs.MANIFEST_NAME).is_file()


def test_restore_rolls_back_and_keeps_pre_restore_backup(tmp_path):
    src = _seed_source(tmp_path)
    store = tmp_path / "snaps"
    qs.main(["--source", str(src), "--store", str(store), "create"])
    snap = qs._list_snapshots(store)[0]

    # 改文件 + 加文件 + 删文件，把当前状态搞得和快照不一样。
    (src / "hooks" / "edit_verify_loop.py").write_text("print('v2-broken')\n", encoding="utf-8")
    (src / "extra.txt").write_text("temp\n", encoding="utf-8")
    (src / "rules" / "test-rules.md").unlink()

    rc = qs.main(["--source", str(src), "--store", str(store), "restore", snap.name])

    assert rc == 0
    # 回退到位：改动撤销、新增消失、删除恢复。
    assert (src / "hooks" / "edit_verify_loop.py").read_text(encoding="utf-8") == "print('v1')\n"
    assert not (src / "extra.txt").exists()
    assert (src / "rules" / "test-rules.md").is_file()
    # 还原前的坏状态被自动留存，且可再次复验。
    guards = [p for p in qs._list_snapshots(store) if p.name.startswith("qoder-pre-restore-")]
    assert len(guards) == 1
    guard_manifest = qs._load_manifest(guards[0])
    assert "extra.txt" in guard_manifest["files"]
    assert qs._verify_snapshot(guards[0], guard_manifest) == []


def test_restore_refuses_corrupted_snapshot(tmp_path):
    src = _seed_source(tmp_path)
    store = tmp_path / "snaps"
    qs.main(["--source", str(src), "--store", str(store), "create"])
    snap = qs._list_snapshots(store)[0]

    # 篡改快照里的一个文件（清单未同步）——模拟磁盘损坏 / 半拷贝。
    victim = snap / "hooks" / "edit_verify_loop.py"
    victim.write_text("tampered\n", encoding="utf-8")
    before = (src / "hooks" / "edit_verify_loop.py").read_text(encoding="utf-8")

    rc = qs.main(["--source", str(src), "--store", str(store), "restore", snap.name])

    assert rc == 2  # fail-closed
    # 当前状态一根头发都没动。
    assert (src / "hooks" / "edit_verify_loop.py").read_text(encoding="utf-8") == before


def test_diff_detects_added_removed_changed(tmp_path):
    src = _seed_source(tmp_path)
    store = tmp_path / "snaps"
    qs.main(["--source", str(src), "--store", str(store), "create"])
    snap_a = qs._list_snapshots(store)[0]

    (src / "new.md").write_text("new\n", encoding="utf-8")  # 新增
    (src / "settings.json").unlink()  # 删除
    (src / "rules" / "test-rules.md").write_text("# rules v2\n", encoding="utf-8")  # 变更
    qs.main(["--source", str(src), "--store", str(store), "create"])
    snap_b = qs._list_snapshots(store)[-1]

    diff = qs._diff_manifests(qs._load_manifest(snap_a), qs._load_manifest(snap_b))
    assert diff["added"] == ["new.md"]
    assert diff["removed"] == ["settings.json"]
    assert diff["changed"] == ["rules/test-rules.md"]

    # 与当前 .qoder 对比：此时当前态 == snap_b，应报无差异（退出码 0）。
    rc = qs.main(["--source", str(src), "--store", str(store), "diff", snap_b.name, "--current"])
    assert rc == 0


def test_prune_keeps_only_newest(tmp_path):
    src = _seed_source(tmp_path)
    store = tmp_path / "snaps"
    for _ in range(3):
        qs.main(["--source", str(src), "--store", str(store), "create"])
    assert len(qs._list_snapshots(store)) == 3

    rc = qs.main(["--source", str(src), "--store", str(store), "prune", "--keep", "2"])

    assert rc == 0
    remaining = qs._list_snapshots(store)
    assert len(remaining) == 2


def test_create_without_source_fails(tmp_path):
    rc = qs.main(["--source", str(tmp_path / "nope"), "--store", str(tmp_path / "snaps"), "create"])
    assert rc == 2
