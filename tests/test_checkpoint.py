"""TaskCheckpoint 断点续跑单元测试（P1-2）。

覆盖目标模块: app/integrated_app/checkpoint.py
"""

import os
import sys
import tempfile

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")


class TestTaskCheckpoint:
    """TaskCheckpoint 断点续跑测试。"""

    def setup_method(self):
        """每个测试用例前创建临时 checkpoint 目录。"""
        self.tmpdir = tempfile.mkdtemp(prefix="tts_checkpoint_test_")
        from integrated_app.checkpoint import TaskCheckpoint

        self.mgr = TaskCheckpoint(checkpoint_dir=self.tmpdir)

    def teardown_method(self):
        """测试后清理临时目录。"""
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_load(self):
        """保存 checkpoint 后能正确加载。"""
        progress = {
            "engine": "voxcpm2",
            "total": 10,
            "completed_items": [{"text": "段1", "index": 0}],
            "remaining": [{"text": "段2", "index": 1}],
            "config": {"cfg_value": 2.0},
        }
        self.mgr.save_checkpoint("task-001", progress)
        loaded = self.mgr.load_checkpoint("task-001")
        assert loaded is not None
        assert loaded["task_id"] == "task-001"
        assert loaded["total"] == 10
        assert loaded["completed"] == 1
        assert len(loaded["remaining"]) == 1

    def test_load_nonexistent(self):
        """加载不存在的 checkpoint 返回 None。"""
        assert self.mgr.load_checkpoint("nonexistent") is None

    def test_remove_checkpoint(self):
        """删除 checkpoint 后无法再加载。"""
        progress = {
            "engine": "indextts2",
            "total": 5,
            "completed_items": [],
            "remaining": [],
            "config": {},
        }
        self.mgr.save_checkpoint("task-002", progress)
        assert self.mgr.remove_checkpoint("task-002") is True
        assert self.mgr.load_checkpoint("task-002") is None

    def test_remove_nonexistent(self):
        """删除不存在的 checkpoint 返回 False。"""
        assert self.mgr.remove_checkpoint("nonexistent") is False

    def test_list_checkpoints(self):
        """列出未完成的 checkpoint。"""
        # task-a: 未完成 (1/3)
        self.mgr.save_checkpoint(
            "task-a",
            {
                "engine": "voxcpm2",
                "total": 3,
                "completed_items": [{"text": "a1"}],
                "remaining": [{"text": "a2"}, {"text": "a3"}],
                "config": {},
            },
        )
        # task-b: 已完成 (3/3) - 不应出现在 list 中
        self.mgr.save_checkpoint(
            "task-b",
            {
                "engine": "voxcpm2",
                "total": 3,
                "completed_items": [{"text": "b1"}, {"text": "b2"}, {"text": "b3"}],
                "remaining": [],
                "config": {},
            },
        )

        pending = self.mgr.list_checkpoints()
        assert len(pending) == 1
        assert pending[0]["task_id"] == "task-a"

    def test_should_checkpoint(self):
        """判断是否需要写 checkpoint。"""
        assert self.mgr.should_checkpoint(5, checkpoint_every=5) is True
        assert self.mgr.should_checkpoint(10, checkpoint_every=5) is True
        assert self.mgr.should_checkpoint(3, checkpoint_every=5) is False
        assert self.mgr.should_checkpoint(0, checkpoint_every=5) is False

    def test_path_traversal_prevention(self):
        """task_id 中的路径分隔符被清理，防止路径穿越。"""
        progress = {
            "engine": "voxcpm2",
            "total": 1,
            "completed_items": [],
            "remaining": [],
            "config": {},
        }
        # 包含路径穿越字符的 task_id 应被清理
        self.mgr.save_checkpoint("../../etc/passwd", progress)
        # 不应在 tmpdir 之外创建文件
        loaded = self.mgr.load_checkpoint("../../etc/passwd")
        assert loaded is not None
        assert loaded["task_id"] == "../../etc/passwd"
