"""cross_platform 模块单元测试 — 跨平台工具函数。

覆盖目标模块: app/integrated_app/cross_platform.py
"""

import os
import time

import pytest

from integrated_app.cross_platform import (
    atomic_write,
    file_lock,
    get_app_data_dir,
    get_cuda_visible_devices,
    get_env_bool,
    get_env_float,
    get_env_int,
    get_platform_name,
    get_system_info,
    get_temp_dir,
    get_terminal_size,
    is_admin,
    normalize_path,
    open_file_explorer,
    set_process_high_priority,
    supports_color,
)


class TestPlatformInfo:
    def test_get_platform_name(self):
        name = get_platform_name()
        assert name.lower() in ("windows", "linux", "macos", "darwin", "unknown")

    def test_get_system_info(self):
        info = get_system_info()
        assert "platform" in info
        assert "python_version" in info

    def test_is_admin_returns_bool(self):
        assert isinstance(is_admin(), bool)


class TestPathHelpers:
    def test_normalize_path(self):
        normalized = normalize_path("C:\\foo\\bar")
        assert "\\" not in normalized or os.sep in normalized
        assert isinstance(normalized, str)

    def test_get_app_data_dir(self):
        d = get_app_data_dir("test-app")
        assert isinstance(d, str)
        assert d

    def test_get_temp_dir(self):
        d = get_temp_dir()
        assert os.path.isdir(d)


class TestAtomicWrite:
    def test_write_bytes(self, tmp_path):
        target = tmp_path / "out.bin"
        atomic_write(str(target), b"\x00\x01")
        assert target.read_bytes() == b"\x00\x01"

    def test_write_text(self, tmp_path):
        target = tmp_path / "out.txt"
        atomic_write(str(target), "你好")
        assert target.read_text(encoding="utf-8") == "你好"

    def test_overwrite_existing(self, tmp_path):
        target = tmp_path / "out.txt"
        atomic_write(str(target), "first")
        atomic_write(str(target), "second")
        assert target.read_text(encoding="utf-8") == "second"


class TestFileLock:
    def test_lock_acquire_release(self, tmp_path):
        """Verify file lock mechanism works correctly."""
        lock_path = tmp_path / "test.lock"
        with file_lock(str(lock_path)):
            # Lock file should exist while holding lock (or be created in parent dir on some platforms)
            lock_exists = lock_path.exists() or any(p.is_file() for p in lock_path.parent.glob("*.lock"))
            assert lock_exists
        # After exit, lock should be released (file may still exist but not blocking)
        with file_lock(str(lock_path)):
            pass  # Should acquire without blocking

    def test_lock_is_exclusive(self, tmp_path):
        import threading

        lock_path = tmp_path / "test.lock"
        concurrent = [0]
        max_concurrent = [0]
        conflict_errors = []

        def worker():
            try:
                with file_lock(str(lock_path)):
                    concurrent[0] += 1
                    max_concurrent[0] = max(max_concurrent[0], concurrent[0])
                    time.sleep(0.2)
                    concurrent[0] -= 1
            except RuntimeError as e:
                conflict_errors.append(e)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        time.sleep(0.05)  # 确保 t1 已持有锁
        t2.start()
        t1.join()
        t2.join()
        # 非阻塞锁：同一时刻最多一个线程持有
        assert max_concurrent[0] == 1
        # t2 在 t1 持有期间尝试获取应冲突，但可能恰好在 t1 释放后获取成功
        assert len(conflict_errors) <= 1


class TestProcessControl:
    def test_set_process_high_priority_returns_bool(self):
        assert isinstance(set_process_high_priority(), bool)

    def test_open_file_explorer(self, tmp_path):
        assert isinstance(open_file_explorer(str(tmp_path)), bool)


class TestEnvHelpers:
    def test_get_env_bool(self, monkeypatch):
        monkeypatch.setenv("TEST_BOOL_1", "1")
        assert get_env_bool("TEST_BOOL_1") is True
        monkeypatch.setenv("TEST_BOOL_0", "0")
        assert get_env_bool("TEST_BOOL_0") is False
        assert get_env_bool("TEST_BOOL_MISSING", default=True) is True
        assert get_env_bool("TEST_BOOL_MISSING", default=False) is False

    def test_get_env_int(self, monkeypatch):
        monkeypatch.setenv("TEST_INT", "42")
        assert get_env_int("TEST_INT", 0) == 42
        assert get_env_int("TEST_INT_MISSING", 7) == 7
        monkeypatch.setenv("TEST_INT_BAD", "abc")
        assert get_env_int("TEST_INT_BAD", 3) == 3

    def test_get_env_float(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT", "1.5")
        assert get_env_float("TEST_FLOAT", 0.0) == pytest.approx(1.5)
        assert get_env_float("TEST_FLOAT_MISSING", 2.5) == pytest.approx(2.5)


class TestTerminal:
    def test_get_terminal_size(self):
        size = get_terminal_size()
        assert isinstance(size, tuple)
        assert len(size) == 2
        assert all(isinstance(v, int) for v in size)

    def test_supports_color_returns_bool(self):
        assert isinstance(supports_color(), bool)

    def test_get_cuda_visible_devices(self):
        devices = get_cuda_visible_devices()
        assert isinstance(devices, list)
