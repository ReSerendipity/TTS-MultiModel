"""``path_guard`` 的判定边界，以及三个调用侧对恶意名字的拦截。

回归动机（2026-09-24 CodeQL py/path-injection 逐条复核）：仓库里此前的两种写法
各有洞 —— ``os.path.basename`` 只削分隔符，``startswith(realpath(dir))`` 少了
``os.sep``。后者是假包含：``PERSONA_DIR`` 为 ``personas`` 时，兄弟目录
``personas_evil`` 里的文件同样以 ``personas`` 开头，旧守卫直接放行。
本文件把这条边界钉住（``test_old_prefix_check_is_the_bug`` 是反面对照）。
"""

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "app"))

from integrated_app.path_guard import (  # noqa: E402
    MAX_FILENAME_LENGTH,
    ensure_within_dir,
    is_bare_filename,
    resolve_bare_in_dir,
)

EVIL_NAMES = [
    "../outside.wav",
    "..\\outside.wav",
    "sub/inner.wav",
    "C:\\Windows\\win.ini",
    "/etc/passwd",
    "./same.wav",
    "",
    ".",
    "..",
    ".hidden.wav",
    "nul\x00.wav",
    "line\nbreak.wav",
    "x" * (MAX_FILENAME_LENGTH + 1),
]


class TestIsBareFilename:
    def test_legit_names_pass(self):
        for name in ("a.wav", "音色_1.mp3", "temp_ref.wav", "metadata.json", "x" * MAX_FILENAME_LENGTH):
            assert is_bare_filename(name), name

    @pytest.mark.parametrize("name", EVIL_NAMES)
    def test_hostile_names_rejected(self, name):
        assert not is_bare_filename(name)


class TestEnsureWithinDir:
    def test_inside_returns_realpath(self, tmp_path):
        root = tmp_path / "personas"
        root.mkdir()
        target = root / "a.wav"
        target.write_bytes(b"RIFF")

        assert ensure_within_dir(str(root), str(root / ".." / "personas" / "a.wav")) == str(target.resolve())

    def test_outside_returns_none(self, tmp_path):
        root = tmp_path / "personas"
        root.mkdir()

        assert ensure_within_dir(str(root), str(tmp_path / "secret.wav")) is None
        assert ensure_within_dir(str(root), str(root)) is None
        assert ensure_within_dir(str(root), "") is None

    def test_old_prefix_check_is_the_bug(self, tmp_path):
        """少了 ``os.sep`` 的前缀比对会把兄弟目录判成"目录内"。"""
        root = tmp_path / "personas"
        root.mkdir()
        sibling = tmp_path / "personas_evil"
        sibling.mkdir()
        target = sibling / "trap.wav"
        target.write_bytes(b"RIFF")

        # 反面对照：旧写法确实放行 —— 这就是本文件要拦的那一类。
        assert str(target.resolve()).startswith(str(root.resolve()))
        # 新写法拒绝。
        assert ensure_within_dir(str(root), str(target)) is None


class TestResolveBareInDir:
    def test_bare_name_inside(self, tmp_path):
        root = tmp_path / "outputs"
        root.mkdir()
        (root / "temp_ref.wav").write_bytes(b"RIFF")

        assert resolve_bare_in_dir(str(root), "temp_ref.wav") == str((root / "temp_ref.wav").resolve())

    def test_extension_whitelist(self, tmp_path):
        root = tmp_path / "outputs"
        root.mkdir()

        assert resolve_bare_in_dir(str(root), "a.wav", allowed_extensions=frozenset({".wav"})) is not None
        assert resolve_bare_in_dir(str(root), "a.exe", allowed_extensions=frozenset({".wav"})) is None

    @pytest.mark.parametrize("name", EVIL_NAMES)
    def test_hostile_names_rejected(self, tmp_path, name):
        root = tmp_path / "personas"
        root.mkdir()

        assert resolve_bare_in_dir(str(root), name) is None

    def test_missing_file_still_resolves(self, tmp_path):
        """本模块只做路径判定，不做存在性检查——写路径需要能解析尚未创建的文件。"""
        root = tmp_path / "personas"
        root.mkdir()

        assert resolve_bare_in_dir(str(root), "brand_new.wav") == str((root / "brand_new.wav").resolve())


class TestGenerationTempFilenameGuard:
    def test_hostile_filename_raises_before_io(self):
        from integrated_app.exceptions import ValidationError
        from integrated_app.generation import preprocess_and_save_temp

        with pytest.raises(ValidationError, match="非法的临时文件名"):
            preprocess_and_save_temp(None, "../../outside.wav")

    def test_default_filename_is_accepted(self, tmp_path, monkeypatch):
        """默认 ``temp_ref.wav`` 必须仍然通过名字校验（守卫只拦非法名）。"""
        from integrated_app import generation

        monkeypatch.setattr(generation, "SAVE_DIR", str(tmp_path))
        with pytest.raises(Exception, match="不支持的音频输入类型"):
            generation.preprocess_and_save_temp(None)


class TestPersonaManagerContainment:
    """三个按名拼路径的入口都要用同一把尺子。"""

    @pytest.fixture
    def persona_env(self, tmp_path, monkeypatch):
        from integrated_app import persona_manager as pm

        monkeypatch.setattr(pm, "PERSONA_DIR", str(tmp_path))
        monkeypatch.setattr(pm.registry, "is_voxcpm_ready", lambda: False)
        return tmp_path, pm

    @pytest.mark.parametrize(
        "name",
        ["../personas_evil/trap", "..\\personas_evil\\trap", "sub/dir", "C:\\Windows\\win.ini", "/etc/passwd"],
    )
    def test_load_rejects_non_bare_name(self, persona_env, name):
        root, pm = persona_env
        sibling = root.parent / "personas_evil"
        sibling.mkdir(exist_ok=True)
        (sibling / "trap.wav").write_bytes(b"RIFF....WAVEfmt ")

        assert pm.load_persona_embedding(name) is None

    def test_delete_rejects_non_bare_name(self, persona_env):
        root, pm = persona_env

        ok, msg = pm.delete_persona("../outside")
        assert ok is False
        assert "格式不合法" in msg or "非法路径" in msg

    def test_save_rejects_non_bare_name(self, persona_env):
        root, pm = persona_env

        msg, needs_confirm = pm.fn_save_persona("../evil", b"RIFF", "ref")
        assert needs_confirm is False
        assert "❌" in msg
        assert list(root.iterdir()) == [], "非法名不得在 PERSONA_DIR 内留下半截文件"
        assert not (root.parent / "evil.wav").exists(), "非法名不得写到 PERSONA_DIR 之外"
