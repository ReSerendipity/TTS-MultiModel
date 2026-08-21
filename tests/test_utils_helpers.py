"""integrated_app/utils.py 单元测试 — 通用工具函数。

覆盖目标模块: app/integrated_app/utils.py
"""

import os
import time

from integrated_app.utils import add_tag, cleanup_temp_files, get_role_color


class TestCleanupTempFiles:
    def test_none_files(self):
        assert cleanup_temp_files(None) >= 0

    def test_empty_list(self):
        assert cleanup_temp_files([]) == 0

    def test_explicit_files(self, tmp_path):
        f1 = tmp_path / "a.wav"
        f2 = tmp_path / "b.wav"
        f1.write_bytes(b"data")
        f2.write_bytes(b"data")
        removed = cleanup_temp_files([str(f1), str(f2)])
        assert removed == 2
        assert not f1.exists()

    def test_missing_files_ignored(self, tmp_path):
        assert cleanup_temp_files([str(tmp_path / "nope.wav")]) == 0

    def test_falsy_entries_skipped(self):
        assert cleanup_temp_files(["", None]) == 0

    def test_global_cleanup_ignores_fresh_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("integrated_app.utils.SAVE_DIR", str(tmp_path))
        fresh = tmp_path / "recent_part_1.wav"
        fresh.write_bytes(b"x")
        removed = cleanup_temp_files()
        assert removed == 0  # 新文件不应被清理

    def test_global_cleanup_removes_old_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("integrated_app.utils.SAVE_DIR", str(tmp_path))
        old = tmp_path / "indextts2_old.wav"  # 匹配 _TEMP_GLOB_PATTERNS
        old.write_bytes(b"x")
        old_time = time.time() - 7200  # 2 小时前
        os.utime(old, (old_time, old_time))
        removed = cleanup_temp_files()
        assert removed == 1
        assert not old.exists()


class TestGetRoleColor:
    def test_known_role(self):
        color_key, css = get_role_color("蓝色")
        assert isinstance(color_key, str)
        assert css.startswith("#")

    def test_strips_brackets(self):
        color_key, css = get_role_color("[红色]")
        assert isinstance(css, str)

    def test_unknown_role_defaults(self):
        color_key, css = get_role_color("不存在的角色名称xyz")
        assert color_key == "blue"
        assert css == "#3B82F6"

    def test_none_role(self):
        color_key, css = get_role_color(None)
        assert color_key == "blue"


class TestAddTag:
    def test_empty_tag_returns_text(self):
        assert add_tag("你好", "") == "你好"

    def test_no_voice_placeholder(self):
        assert add_tag("你好", "(暂无音色)") == "你好"

    def test_speaker_tag(self):
        result = add_tag("你好", "小明")
        assert "[小明]" in result

    def test_non_speaker_tag(self):
        result = add_tag("你好", "uv_break", is_speaker=False)
        assert "[uv_break]" in result

    def test_empty_text(self):
        result = add_tag("", "小明")
        assert "[小明]" in result
