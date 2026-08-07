"""generation_versioning 模块单元测试 — 生成版本管理。

覆盖目标模块: bin/integrated_app/generation_versioning.py
"""

from integrated_app.generation_versioning import (
    GenerationVersion,
    VersionManager,
    get_version_manager,
    reset_version_manager,
)


class TestGenerationVersion:
    def test_created_at_str_auto(self):
        v = GenerationVersion(
            version_id="v1",
            parent_id=None,
            audio_path="/tmp/a.wav",
            text="你好",
            params={"cfg": 2.0},
            engine="voxcpm2",
            created_at=0.0,
        )
        assert v.created_at_str  # 1970-01-01 00:00:00


class TestVersionManager:
    def test_memory_store(self, tmp_path):
        vm = VersionManager(db_path=None)
        vid = vm.save_generation(
            audio_path="/tmp/a.wav",
            text="你好世界",
            params={"cfg": 2.0},
            engine="voxcpm2",
        )
        assert vid is not None
        version = vm.get_version(vid)
        assert version is not None
        assert version.text == "你好世界"
        assert version.engine == "voxcpm2"
        assert version.params["cfg"] == 2.0

    def test_sqlite_store(self, tmp_path):
        db = str(tmp_path / "versions.db")
        vm = VersionManager(db_path=db)
        vid = vm.save_generation(
            audio_path="/tmp/a.wav",
            text="test",
            params={"cfg": 1.0},
            engine="indextts2",
        )
        assert vid is not None
        version = vm.get_version(vid)
        assert version is not None
        assert version.engine == "indextts2"
        assert version.text == "test"

    def test_save_with_parent(self, tmp_path):
        vm = VersionManager(db_path=str(tmp_path / "v.db"))
        parent = vm.save_generation(audio_path="/p.wav", text="p", params={}, engine="voxcpm2")
        child = vm.save_generation(audio_path="/c.wav", text="c", params={}, engine="voxcpm2", parent_id=parent)
        assert child is not None
        child_ver = vm.get_version(child)
        assert child_ver.parent_id == parent

    def test_get_missing_version(self, tmp_path):
        vm = VersionManager(db_path=str(tmp_path / "v.db"))
        assert vm.get_version("nonexistent") is None

    def test_get_version_chain(self, tmp_path):
        vm = VersionManager(db_path=str(tmp_path / "v.db"))
        root = vm.save_generation(audio_path="/r.wav", text="r", params={}, engine="voxcpm2")
        child = vm.save_generation(audio_path="/c.wav", text="c", params={}, engine="voxcpm2", parent_id=root)
        chain = vm.get_version_chain(child)
        # 根版本在前，目标版本在后
        assert [v.version_id for v in chain] == [root, child]

    def test_list_recent_ordered(self, tmp_path):
        vm = VersionManager(db_path=str(tmp_path / "v.db"))
        ids = []
        for i in range(3):
            vid = vm.save_generation(audio_path=f"/{i}.wav", text=f"t{i}", params={}, engine="voxcpm2")
            ids.append(vid)
        versions = vm.list_recent(limit=10)
        assert len(versions) == 3
        assert versions[0].version_id == ids[-1]  # 最新在前

    def test_list_recent_filter_engine(self, tmp_path):
        vm = VersionManager(db_path=str(tmp_path / "v.db"))
        vm.save_generation(audio_path="/a.wav", text="a", params={}, engine="voxcpm2")
        vm.save_generation(audio_path="/b.wav", text="b", params={}, engine="indextts2")
        vox = vm.list_recent(limit=10, engine="voxcpm2")
        assert len(vox) == 1
        assert vox[0].engine == "voxcpm2"

    def test_close(self, tmp_path):
        vm = VersionManager(db_path=str(tmp_path / "v.db"))
        vm.close()


class TestSingleton:
    def test_singleton(self):
        a = get_version_manager()
        b = get_version_manager()
        assert a is b

    def test_reset(self):
        reset_version_manager()
        assert get_version_manager() is not None
