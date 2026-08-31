"""Persona 元数据按音色独立存储（数据治理修复）回归测试。

验证：
1. save/load 使用 ``<persona_name>.metadata.json`` 而非共享根目录 metadata.json；
2. 多个音色之间元数据相互隔离（无 last-writer-wins 污染）；
3. 旧版 .txt 三行格式仍可回退；
4. 导入旧版 zip（共享 metadata.json）后自动重命名为 per-persona 文件。
"""

import json
import zipfile

from integrated_app.persona_metadata import (
    PersonaExporter,
    PersonaMetadata,
    load_persona_metadata,
    save_persona_metadata,
)


def test_save_uses_per_persona_file(tmp_path):
    meta = PersonaMetadata(name="alice", description="测试音色", voice_type="御姐音")
    save_persona_metadata(str(tmp_path), "alice", meta)

    assert (tmp_path / "alice.metadata.json").exists()
    # 不应再写入共享根目录 metadata.json
    assert not (tmp_path / "metadata.json").exists()
    # 仍双写 .txt 兼容旧版
    assert (tmp_path / "alice.txt").exists()

    loaded = load_persona_metadata(str(tmp_path), "alice")
    assert loaded.name == "alice"
    assert loaded.voice_type == "御姐音"
    assert loaded.description == "测试音色"


def test_metadata_isolation_between_personas(tmp_path):
    save_persona_metadata(str(tmp_path), "alice", PersonaMetadata(name="alice", voice_type="御姐音"))
    save_persona_metadata(str(tmp_path), "bob", PersonaMetadata(name="bob", voice_type="正太音"))

    alice = load_persona_metadata(str(tmp_path), "alice")
    bob = load_persona_metadata(str(tmp_path), "bob")

    assert alice.voice_type == "御姐音"
    assert bob.voice_type == "正太音"
    # 关键：互不污染
    assert alice.name == "alice"
    assert bob.name == "bob"


def test_legacy_txt_fallback(tmp_path):
    with open(tmp_path / "carol.txt", "w", encoding="utf-8") as f:
        f.write("萝莉音\n这是描述\n这是特征")

    loaded = load_persona_metadata(str(tmp_path), "carol")
    assert loaded.name == "carol"
    assert loaded.voice_type == "萝莉音"
    assert loaded.description == "这是描述"


def test_export_import_roundtrip(tmp_path):
    # PersonaExporter 以目录名作为音色名打包整个目录，故构造名为 dave 的子目录
    pkg = tmp_path / "dave"
    pkg.mkdir()
    save_persona_metadata(str(pkg), "dave", PersonaMetadata(name="dave", voice_type="磁性男声"))
    with open(pkg / "dave.wav", "wb") as f:
        f.write(b"RIFF....WAVE")

    zip_path = tmp_path / "dave.zip"
    PersonaExporter.export_persona(str(pkg), str(zip_path))

    dest = tmp_path / "imported"
    dest.mkdir()
    name = PersonaExporter.import_persona(str(zip_path), str(dest))
    assert name == "dave"
    # 导入后应为 per-persona 文件（zip 内部布局为 <name>/...，故落在子目录内）
    assert (dest / "dave" / "dave.metadata.json").exists()


def test_import_renames_legacy_shared_metadata(tmp_path):
    # 构造旧版 zip：内部为共享 metadata.json（位于 <name>/ 子目录）
    pkg = tmp_path / "legacy"
    pkg.mkdir()
    zip_path = tmp_path / "legacy.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("legacy/legacy.txt", "御姐音\n旧版共享\n特征")
        zf.writestr(
            "legacy/metadata.json",
            json.dumps({"name": "legacy", "voice_type": "御姐音"}, ensure_ascii=False),
        )
    dest = tmp_path / "out"
    dest.mkdir()
    name = PersonaExporter.import_persona(str(zip_path), str(dest))
    assert name == "legacy"
    # 共享 metadata.json 应被重命名为 per-persona 文件
    assert (dest / "legacy" / "legacy.metadata.json").exists()
    assert not (dest / "legacy" / "metadata.json").exists()
