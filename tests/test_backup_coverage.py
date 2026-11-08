"""备份集覆盖口径（O4）的回归用例：整集备份要覆盖密钥 / 版本库 / 审计日志，并如实记账缺项。

债项 O4 的原文：`data/` 下 5 个运行密钥无任何备份覆盖、`generation_versioning` 库不在默认备份集、
`data/audit.log` 实测未落盘。只备 `history.db` 的问题不是「丢了点附件」，而是灾难恢复时：
密钥丢了 → 库里 PII 字段与 HMAC 全部解不开也验不了；版本链丢了 → 回滚找不到原始产物；
审计日志丢了 → 事后追责没证据。

这些用例钉四件事：
1. 覆盖计划的对象清单就是「口径」——少一类就判红（防止以后有人把密钥从计划里删掉当「优化」）；
2. 缺失必须如实记账（manifest 里 `present:false` + `reason`），不能因为文件不存在就静默跳过；
3. 整集备份的 manifest 带 sha256，`verify_full_backup` 能真的发现篡改与丢文件；
4. `verify` 自身不是空转——空 manifest、改过的文件、被删的文件三种形态都要报错。
"""

import json
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import backup_history_db as bk  # noqa: E402


def _seed_history(db_path: str, rows: int = 3) -> str:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE generation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL UNIQUE,
                created_timestamp REAL NOT NULL DEFAULT 0
            )
            """
        )
        for i in range(rows):
            conn.execute(
                "INSERT INTO generation_history (filename, filepath, created_timestamp) VALUES (?, ?, ?)",
                (f"f{i}.wav", os.path.join(os.path.dirname(db_path), f"f{i}.wav"), float(i)),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _make_tree(tmp_path):
    """造一套「有历史库、有 5 个密钥、有版本库、有审计日志」的现场。"""
    data = tmp_path / "data"
    save = tmp_path / "outputs"
    data.mkdir()
    save.mkdir()
    hist = _seed_history(str(data / "history.db"))
    _seed_history(str(save / "generation_versions.db"), rows=1)
    for key in bk.KEY_FILES:
        (data / key).write_bytes(b"k" * 32)
    for name in ("audit.log", "audit.log.1"):
        (data / name).write_text('{"event":"x"}\n', encoding="utf-8")
    return str(data), str(save), hist


def _read_manifest(bundle: str) -> dict:
    with open(os.path.join(bundle, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def test_coverage_plan_names_every_artifact_class():
    plan = bk.build_coverage_plan(
        data_dir="/nonexistent/data",
        save_dir="/nonexistent/save",
        history_db="/nonexistent/history.db",
        hmac_dir="/nonexistent/hmac",
    )
    kinds = {a["kind"] for a in plan}
    assert kinds == {"history_db", "versioning_db", "key", "audit_log"}

    names = {a["name"] for a in plan}
    for key in bk.KEY_FILES:
        assert key in names, f"覆盖计划漏了密钥 {key}"
    assert {"audit.log", "audit.log.1", "audit.log.2", "audit.log.3"} <= names
    assert "generation_versions.db" in names
    # 密钥必须标成 secret，否则 manifest 会漏掉「这份备份含明文密钥」的提示
    assert all(a["secret"] for a in plan if a["kind"] == "key")
    # 只有历史库是硬性必需项；密钥/版本库/审计日志按「存在才备、缺了就记账」处理
    assert [a["name"] for a in plan if a["required"]] == ["history.db"]


def test_coverage_plan_reports_presence_honestly(tmp_path):
    data, save, hist = _make_tree(tmp_path)
    plan = bk.build_coverage_plan(data_dir=data, save_dir=save, history_db=hist, hmac_dir=str(tmp_path))
    by_name = {a["name"]: a for a in plan}

    assert by_name["history.db"]["present"] and by_name["history.db"]["required"]
    assert by_name[".pii_key"]["present"]
    assert by_name["audit.log"]["present"]
    # 没造 audit.log.2/.3 → 必须 present:false 且带原因，而不是装作有
    assert by_name["audit.log.2"]["present"] is False
    assert by_name["audit.log.2"]["reason"] == "file_not_found"


def test_hmac_key_prefers_user_config_dir_over_legacy_data_dir(tmp_path):
    """`.history_hmac_key` 已迁到用户配置目录：两处都有时必须备新路径那份。"""
    data, save, hist = _make_tree(tmp_path)
    hmac_dir = tmp_path / "hmac"
    hmac_dir.mkdir()
    (hmac_dir / ".history_hmac_key").write_bytes(b"NEW" * 8)
    (tmp_path / "data" / ".history_hmac_key").write_bytes(b"OLD")

    plan = bk.build_coverage_plan(data_dir=data, save_dir=save, history_db=hist, hmac_dir=str(hmac_dir))
    entry = next(a for a in plan if a["name"] == ".history_hmac_key")
    assert entry["path"] == str(hmac_dir / ".history_hmac_key")


def test_full_backup_covers_secrets_and_hashes_them(tmp_path):
    data, save, hist = _make_tree(tmp_path)
    plan = bk.build_coverage_plan(data_dir=data, save_dir=save, history_db=hist, hmac_dir=str(tmp_path))
    bundle = bk.create_full_backup(plan, str(tmp_path / "backups"))

    manifest = _read_manifest(bundle)
    assert manifest["contains_secrets"] is True
    kinds = {e["kind"] for e in manifest["artifacts"] if e["present"]}
    assert {"history_db", "versioning_db", "key", "audit_log"} <= kinds

    for e in manifest["artifacts"]:
        if not e["present"]:
            continue
        path = os.path.join(bundle, e["file"])
        assert bk._sha256(path) == e["sha256"], f"{e['name']} 的 sha256 与 manifest 不一致"
        assert e["size"] == os.path.getsize(path)

    # 密钥备份的是内容，不是文件名
    key_entry = next(e for e in manifest["artifacts"] if e["name"] == ".pii_key")
    with open(os.path.join(bundle, key_entry["file"]), "rb") as f:
        assert f.read() == (tmp_path / "data" / ".pii_key").read_bytes()

    # 历史库走 SQLite backup API，恢复出来的行数必须与源一致
    hist_entry = next(e for e in manifest["artifacts"] if e["name"] == "history.db")
    assert bk._count_rows(os.path.join(bundle, hist_entry["file"])) == 3

    assert bk.verify_full_backup(bundle) == []


def test_full_backup_records_missing_items_instead_of_skipping(tmp_path):
    """缺项不是「不备份」，是「记账为缺失」——否则备份日志看起来和完整备份一样。"""
    data, save, hist = _make_tree(tmp_path)
    os.remove(os.path.join(data, ".watermark_key"))
    plan = bk.build_coverage_plan(data_dir=data, save_dir=save, history_db=hist, hmac_dir=str(tmp_path))
    bundle = bk.create_full_backup(plan, str(tmp_path / "backups"))

    manifest = _read_manifest(bundle)
    entry = next(e for e in manifest["artifacts"] if e["name"] == ".watermark_key")
    assert entry["present"] is False
    assert entry["reason"] == "file_not_found"
    assert entry["file"] == ""
    # 备份本身仍然成功（有历史库），verify 也不该因为"本来就没有的密钥"报错
    assert bk.verify_full_backup(bundle) == []


def test_verify_detects_tampering_and_deletion(tmp_path):
    data, save, hist = _make_tree(tmp_path)
    plan = bk.build_coverage_plan(data_dir=data, save_dir=save, history_db=hist, hmac_dir=str(tmp_path))
    bundle = bk.create_full_backup(plan, str(tmp_path / "backups"))
    manifest = _read_manifest(bundle)
    key_entry = next(e for e in manifest["artifacts"] if e["name"] == ".csrf_secret")

    # ① 改一个字节 → 必须报 sha256 不一致
    with open(os.path.join(bundle, key_entry["file"]), "ab") as f:
        f.write(b"x")
    errors = bk.verify_full_backup(bundle)
    assert any("sha256 不一致" in e for e in errors), errors

    # ② 删掉文件 → 必须报「记为在场但文件不存在」（不能因为找不到就当没有）
    os.remove(os.path.join(bundle, key_entry["file"]))
    errors = bk.verify_full_backup(bundle)
    assert any("文件不存在" in e for e in errors), errors


def test_verify_rejects_empty_or_missing_manifest(tmp_path):
    bundle = tmp_path / "b"
    bundle.mkdir()
    assert bk.verify_full_backup(str(bundle)) != []  # 没有 manifest

    (bundle / "manifest.json").write_text('{"artifacts": []}', encoding="utf-8")
    errors = bk.verify_full_backup(str(bundle))
    assert errors and "为空" in errors[0]
