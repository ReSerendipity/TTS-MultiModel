"""断点续跑恢复逻辑测试（路线图 #6：任务取消 + 断点恢复）。

覆盖：
1. save -> load 往返（fake 任务数据，不跑真实推理）
2. should_checkpoint 阈值边界
3. 恢复续跑逻辑：批量任务中断 -> checkpoint 保留 remaining -> 续跑仅处理剩余项
4. list_resumable / resume_state 清理语义
5. 未启用 checkpoint 时单任务/小批量行为不变（回归保护）

覆盖目标模块: app/integrated_app/checkpoint.py + app/integrated_app/batch_inference.py
"""

import os
import sys

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

from integrated_app.batch_inference import BatchInferencer  # noqa: E402
from integrated_app.checkpoint import TaskCheckpoint  # noqa: E402


class _SimulatedKill(BaseException):
    """模拟进程崩溃/断电（BaseException 不被 except Exception 吞掉）。"""


def _fake_inference(items):
    """fake 批量推理函数：不跑真实模型，返回与 items 等长的零张量。"""
    import torch

    return [torch.zeros(16) for _ in items]


def _make_items(n: int, prefix: str = "line") -> list[dict]:
    """构造 fake 批量任务项（每项带文本指纹 + seed）。"""
    return [{"text": f"{prefix}-{i}", "seed": i} for i in range(n)]


def _save_progress(mgr: TaskCheckpoint, task_id: str, total: int, done: int, engine: str = "voxcpm2"):
    """按 run() 的落盘格式写一个"中断现场"。"""
    items = _make_items(total)
    mgr.save_checkpoint(
        task_id,
        {
            "engine": engine,
            "total": total,
            "completed_items": items[:done],
            "remaining": items[done:],
            "config": {"engine": engine, "endpoint": "script", "cfg": 2.0},
        },
    )
    return items


class TestSaveLoadRoundtrip:
    """save -> load 往返（fake 任务数据）。"""

    def test_roundtrip_with_fake_task_data(self, tmp_path):
        mgr = TaskCheckpoint(checkpoint_dir=str(tmp_path))
        progress = {
            "engine": "voxcpm2",
            "total": 12,
            "completed_items": [{"text": "seg-0", "seed": 0}, {"text": "seg-1", "seed": 1}],
            "remaining": [{"text": f"seg-{i}", "seed": i} for i in range(2, 12)],
            "config": {"endpoint": "script", "cfg": 2.0, "seed": 42},
        }
        mgr.save_checkpoint("batch-1", progress)
        loaded = mgr.load_checkpoint("batch-1")
        assert loaded is not None
        assert loaded["task_id"] == "batch-1"
        assert loaded["engine"] == "voxcpm2"
        assert loaded["total"] == 12
        assert loaded["completed"] == 2
        assert loaded["completed_items"] == progress["completed_items"]
        assert loaded["remaining"] == progress["remaining"]
        assert loaded["config"]["endpoint"] == "script"
        assert loaded["updated_at"] > 0

    def test_overwrite_keeps_latest_state(self, tmp_path):
        mgr = TaskCheckpoint(checkpoint_dir=str(tmp_path))
        mgr.save_checkpoint(
            "t",
            {
                "engine": "a",
                "total": 4,
                "completed_items": [{"i": 0}],
                "remaining": [{"i": 1}, {"i": 2}, {"i": 3}],
                "config": {},
            },
        )
        mgr.save_checkpoint(
            "t",
            {
                "engine": "a",
                "total": 4,
                "completed_items": [{"i": 0}, {"i": 1}, {"i": 2}],
                "remaining": [{"i": 3}],
                "config": {},
            },
        )
        loaded = mgr.load_checkpoint("t")
        assert loaded is not None
        assert loaded["completed"] == 3
        assert len(loaded["remaining"]) == 1


class TestShouldCheckpointThreshold:
    """should_checkpoint 阈值边界。"""

    @pytest.mark.parametrize(
        "completed,every,expected",
        [
            (0, 5, False),
            (1, 5, False),
            (4, 5, False),
            (5, 5, True),
            (10, 5, True),
            (15, 5, True),
            (2, 3, False),
            (3, 3, True),
            (6, 3, True),
            (100, 100, True),
            (101, 100, False),
        ],
    )
    def test_thresholds(self, tmp_path, completed, every, expected):
        mgr = TaskCheckpoint(checkpoint_dir=str(tmp_path))
        assert mgr.should_checkpoint(completed, every) is expected

    def test_default_every_is_5(self, tmp_path):
        mgr = TaskCheckpoint(checkpoint_dir=str(tmp_path))
        assert mgr.should_checkpoint(5) is True
        assert mgr.should_checkpoint(7) is False


class TestResumeFromCheckpoint:
    """恢复续跑逻辑（fake 推理，不跑真实模型）。"""

    def test_resume_continues_only_remaining(self, tmp_path):
        """中断现场 4/10 -> 续跑仅处理 remaining 6 项，完成后清理 checkpoint。"""
        mgr = TaskCheckpoint(checkpoint_dir=str(tmp_path))
        inf = BatchInferencer(max_batch_size=2)

        items = _save_progress(mgr, "batch-crash", total=10, done=4)

        seen: list[list[dict]] = []

        def spy_fn(batch):
            seen.append(batch)
            return _fake_inference(batch)

        results, stats = inf.resume_from_checkpoint(
            mgr,
            "batch-crash",
            spy_fn,
            checkpoint_every=3,
        )
        assert results is not None
        assert len(results) == 6
        assert stats.successful == 6
        assert stats.total_items == 6
        assert all(r.success for r in results)
        # 推理只收到 remaining 项，且与 checkpoint 记录一致
        flattened = [item for batch in seen for item in batch]
        assert flattened == items[4:]
        # 全部完成后 checkpoint 被清理
        assert mgr.load_checkpoint("batch-crash") is None

    def test_resume_no_checkpoint_returns_none(self, tmp_path):
        mgr = TaskCheckpoint(checkpoint_dir=str(tmp_path))
        inf = BatchInferencer(max_batch_size=2)
        assert inf.resume_from_checkpoint(mgr, "nope", _fake_inference) is None

    def test_resume_completed_checkpoint_is_cleaned(self, tmp_path):
        mgr = TaskCheckpoint(checkpoint_dir=str(tmp_path))
        inf = BatchInferencer(max_batch_size=2)
        _save_progress(mgr, "done-task", total=4, done=4)
        assert inf.resume_from_checkpoint(mgr, "done-task", _fake_inference) is None
        # 已完成的 checkpoint 文件被清理
        assert mgr.load_checkpoint("done-task") is None

    def test_crash_leaves_checkpoint_then_resume(self, tmp_path):
        """run() 中模拟进程崩溃 -> checkpoint 落盘 remaining -> 重启续跑。"""
        mgr = TaskCheckpoint(checkpoint_dir=str(tmp_path))
        inf = BatchInferencer(max_batch_size=1)  # 每批 1 项，offset 精确对应完成数
        items = _make_items(7)
        calls = {"n": 0}

        def crash_fn(batch):
            calls["n"] += 1
            if calls["n"] > 3:  # 第 4 次调用时模拟断电
                raise _SimulatedKill("simulated power loss")
            return _fake_inference(batch)

        with pytest.raises(_SimulatedKill):
            inf.run(
                items,
                crash_fn,
                checkpoint_mgr=mgr,
                checkpoint_task_id="batch-kill",
                checkpoint_every=3,
                checkpoint_meta={"engine": "voxcpm2", "endpoint": "script"},
            )

        # 崩溃后 checkpoint 保留：3/7 完成，remaining 4 项 + 元信息完整
        saved = mgr.load_checkpoint("batch-kill")
        assert saved is not None
        assert saved["completed"] == 3
        assert saved["total"] == 7
        assert saved["remaining"] == items[3:]
        assert saved["engine"] == "voxcpm2"
        assert saved["config"]["endpoint"] == "script"

        # 重启续跑：仅处理 remaining 4 项，完成后清理
        results, stats = inf.resume_from_checkpoint(mgr, "batch-kill", _fake_inference)
        assert len(results) == 4
        assert stats.successful == 4
        assert mgr.load_checkpoint("batch-kill") is None

    def test_run_without_checkpoint_writes_nothing(self, tmp_path):
        """未启用 checkpoint 时行为不变（单任务/小批量回归保护）。"""
        mgr = TaskCheckpoint(checkpoint_dir=str(tmp_path))
        inf = BatchInferencer(max_batch_size=2)
        results, stats = inf.run(_make_items(6), _fake_inference)
        assert stats.successful == 6
        assert len(results) == 6
        # 未启用 checkpoint：目录里没有任何文件
        assert mgr.list_checkpoints() == []
        assert mgr.list_resumable() == []


class TestListResumableAndResumeState:
    """list_resumable / resume_state 的清理与筛选语义。"""

    def test_list_resumable_filters_and_cleans(self, tmp_path):
        mgr = TaskCheckpoint(checkpoint_dir=str(tmp_path))
        _save_progress(mgr, "pending", total=5, done=2)
        _save_progress(mgr, "finished", total=5, done=5)
        _save_progress(mgr, "stale", total=5, done=2)
        # 人为制造 remaining 为空但 completed < total 的无效现场
        mgr.save_checkpoint(
            "stale",
            {
                "engine": "voxcpm2",
                "total": 5,
                "completed_items": [{"text": "x"}],
                "remaining": [],
                "config": {},
            },
        )

        resumable = mgr.list_resumable()
        assert [cp["task_id"] for cp in resumable] == ["pending"]
        # 已完成/无效的 checkpoint 文件被清理，pending 保留
        assert mgr.load_checkpoint("finished") is None
        assert mgr.load_checkpoint("stale") is None
        assert mgr.load_checkpoint("pending") is not None

    def test_resume_state_nonexistent(self, tmp_path):
        mgr = TaskCheckpoint(checkpoint_dir=str(tmp_path))
        assert mgr.resume_state("ghost") is None

    def test_resume_state_pending_returns_full_state(self, tmp_path):
        mgr = TaskCheckpoint(checkpoint_dir=str(tmp_path))
        items = _save_progress(mgr, "task-x", total=8, done=3)
        state = mgr.resume_state("task-x")
        assert state is not None
        assert state["completed"] == 3
        assert state["total"] == 8
        assert state["remaining"] == items[3:]
        # 未完成的 checkpoint 文件保留（不清理）
        assert mgr.load_checkpoint("task-x") is not None

    def test_resume_state_completed_cleans_file(self, tmp_path):
        mgr = TaskCheckpoint(checkpoint_dir=str(tmp_path))
        _save_progress(mgr, "task-done", total=3, done=3)
        assert mgr.resume_state("task-done") is None
        assert mgr.load_checkpoint("task-done") is None
