"""P1-3：outputs/uploads/ 递归 TTL 清理单元测试。"""

import os
import sys
import time
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "app"))


class TestUploadCleanup:
    """cleanup_expired_uploads 递归清理超期上传文件。"""

    @pytest.fixture
    def fake_save_dir(self, tmp_path, monkeypatch):
        """创建临时 outputs/ 目录并 monkeypatch SAVE_DIR。"""
        save_dir = tmp_path / "outputs"
        uploads = save_dir / "uploads"
        uploads.mkdir(parents=True)
        monkeypatch.setattr("integrated_app.utils.SAVE_DIR", str(save_dir))
        return save_dir

    def _make_old_file(self, path: Path, age_days: float = 40) -> None:
        """创建指定年龄的文件（修改 mtime）。"""
        path.write_bytes(b"fake audio data")
        old_time = time.time() - age_days * 86400
        os.utime(str(path), (old_time, old_time))

    def test_expired_file_removed(self, fake_save_dir):
        """超期文件被删除。"""
        from integrated_app.utils import cleanup_expired_uploads

        old_file = fake_save_dir / "uploads" / "old_ref.wav"
        self._make_old_file(old_file, age_days=40)

        removed = cleanup_expired_uploads(ttl_days=30)
        assert removed == 1
        assert not old_file.exists()

    def test_recent_file_preserved(self, fake_save_dir):
        """未超期文件保留。"""
        from integrated_app.utils import cleanup_expired_uploads

        new_file = fake_save_dir / "uploads" / "recent_ref.wav"
        self._make_old_file(new_file, age_days=5)

        removed = cleanup_expired_uploads(ttl_days=30)
        assert removed == 0
        assert new_file.exists()

    def test_recursive_subdirectory(self, fake_save_dir):
        """子目录中的超期文件也被递归清理。"""
        from integrated_app.utils import cleanup_expired_uploads

        subdir = fake_save_dir / "uploads" / "user_123" / "session_456"
        subdir.mkdir(parents=True)
        old_file = subdir / "deep_old.wav"
        self._make_old_file(old_file, age_days=60)

        removed = cleanup_expired_uploads(ttl_days=30)
        assert removed == 1
        assert not old_file.exists()

    def test_ttl_zero_no_cleanup(self, fake_save_dir):
        """ttl_days=0 时不清理任何文件。"""
        from integrated_app.utils import cleanup_expired_uploads

        old_file = fake_save_dir / "uploads" / "old.wav"
        self._make_old_file(old_file, age_days=365)

        removed = cleanup_expired_uploads(ttl_days=0)
        assert removed == 0
        assert old_file.exists()

    def test_mixed_ages(self, fake_save_dir):
        """混合年龄：仅删除超期文件。"""
        from integrated_app.utils import cleanup_expired_uploads

        old1 = fake_save_dir / "uploads" / "old1.wav"
        old2 = fake_save_dir / "uploads" / "old2.mp3"
        new1 = fake_save_dir / "uploads" / "new1.wav"
        self._make_old_file(old1, age_days=31)
        self._make_old_file(old2, age_days=100)
        self._make_old_file(new1, age_days=29)

        removed = cleanup_expired_uploads(ttl_days=30)
        assert removed == 2
        assert not old1.exists()
        assert not old2.exists()
        assert new1.exists()

    def test_nonexistent_upload_dir(self, fake_save_dir):
        """uploads 目录不存在时返回 0 不报错。"""
        # 删除 uploads 目录
        import shutil

        from integrated_app.utils import cleanup_expired_uploads

        shutil.rmtree(fake_save_dir / "uploads")

        removed = cleanup_expired_uploads(ttl_days=30)
        assert removed == 0
