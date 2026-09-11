"""P2-2：训练会话临时产物 TTL 清理单元测试。

覆盖 ``security.training_data_ttl_days`` 的实际消费路径
（``integrated_app.utils.cleanup_expired_training_data``）——该配置此前
只声明未消费，被 ``scripts/check_config_refs.py`` 门禁标记为
「声明即生效的假安全感」。
"""

import os
import sys
import time
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "app"))


class TestTrainingDataCleanup:
    """cleanup_expired_training_data 清理超期训练会话临时产物。"""

    @pytest.fixture
    def fake_temp_dir(self, tmp_path, monkeypatch):
        """把系统临时目录重定向到 tmp_path。"""
        monkeypatch.setattr("integrated_app.utils.tempfile.gettempdir", lambda: str(tmp_path))
        return tmp_path

    @staticmethod
    def _make_old(path: Path, age_days: float = 120) -> None:
        """创建指定年龄的文件/目录（修改 mtime）。"""
        if path.is_dir():
            (path / "status.json").write_text("{}", encoding="utf-8")
        else:
            path.write_text("{}", encoding="utf-8")
        old_time = time.time() - age_days * 86400
        os.utime(str(path), (old_time, old_time))

    def test_expired_status_file_removed(self, fake_temp_dir):
        """超期的训练状态 JSON 被删除。"""
        from integrated_app.utils import cleanup_expired_training_data

        status = fake_temp_dir / "tts_multimodel_training_status.json"
        self._make_old(status, age_days=120)

        removed = cleanup_expired_training_data(ttl_days=90)
        assert removed == 1
        assert not status.exists()

    def test_recent_status_file_preserved(self, fake_temp_dir):
        """未超期的训练状态 JSON 保留（避免误删进行中的训练）。"""
        from integrated_app.utils import cleanup_expired_training_data

        status = fake_temp_dir / "tts_multimodel_training_status.json"
        self._make_old(status, age_days=3)

        removed = cleanup_expired_training_data(ttl_days=90)
        assert removed == 0
        assert status.exists()

    def test_expired_session_dir_removed(self, fake_temp_dir):
        """超期的训练会话临时目录被递归删除。"""
        from integrated_app.utils import cleanup_expired_training_data

        session_dir = fake_temp_dir / "tts_multimodel_train_abc123"
        session_dir.mkdir()
        self._make_old(session_dir, age_days=200)

        removed = cleanup_expired_training_data(ttl_days=90)
        assert removed == 1
        assert not session_dir.exists()

    def test_unrelated_temp_entries_untouched(self, fake_temp_dir):
        """不匹配前缀的临时条目不受影响。"""
        from integrated_app.utils import cleanup_expired_training_data

        other = fake_temp_dir / "some_other_app.tmp"
        self._make_old(other, age_days=365)

        removed = cleanup_expired_training_data(ttl_days=90)
        assert removed == 0
        assert other.exists()

    def test_ttl_zero_no_cleanup(self, fake_temp_dir):
        """ttl_days=0 时不清理任何条目。"""
        from integrated_app.utils import cleanup_expired_training_data

        status = fake_temp_dir / "tts_multimodel_training_status.json"
        self._make_old(status, age_days=365)

        removed = cleanup_expired_training_data(ttl_days=0)
        assert removed == 0
        assert status.exists()
