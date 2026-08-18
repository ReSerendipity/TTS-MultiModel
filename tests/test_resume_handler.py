"""tests for checkpoint resume handler wiring (default handler + engine registry).

只验证机制闭环：注册引擎推理函数 → 构造 handler → 续跑 remaining → 清理 checkpoint；
未注册引擎 → 保留 checkpoint 并返回 False。全部使用 fake 数据，不跑真实推理。
"""
from pathlib import Path

import pytest

from integrated_app.checkpoint import TaskCheckpoint
from integrated_app.batch_inference import (
    get_resume_inference_fn,
    make_checkpoint_resume_handler,
    register_resume_inference_fn,
)


@pytest.fixture
def mgr(tmp_path: Path) -> TaskCheckpoint:
    return TaskCheckpoint(checkpoint_dir=str(tmp_path / "checkpoints"))


def _make_cp(task_id: str, engine: str, total: int, completed_n: int) -> dict:
    items = [{"text": f"hello {i}"} for i in range(total)]
    return {
        "task_id": task_id,
        "engine": engine,
        "total": total,
        "completed": completed_n,
        "completed_items": items[:completed_n],
        "remaining": items[completed_n:],
        "config": {"engine": engine},
    }


def test_register_and_lookup():
    fn = lambda batch: [1.0] * len(batch)
    register_resume_inference_fn("voxcpm2", fn)
    assert get_resume_inference_fn("voxcpm2") is fn
    assert get_resume_inference_fn("no_such_engine") is None


def test_handler_resumes_and_cleans(mgr):
    import torch

    cp = _make_cp("task_x", "voxcpm2", total=10, completed_n=7)
    mgr.save_checkpoint(cp["task_id"], cp)

    received: list[list] = []

    def fake_fn(batch):
        received.append(list(batch))
        return [torch.zeros(8) for _ in batch]

    register_resume_inference_fn("voxcpm2", fake_fn)
    handler = make_checkpoint_resume_handler(mgr)

    assert handler(cp) is True
    assert len(received) == 1
    assert len(received[0]) == 3  # remaining items
    # checkpoint 已完成并被清理
    assert mgr.load_checkpoint("task_x") is None


def test_handler_keeps_checkpoint_for_unknown_engine(mgr):
    cp = _make_cp("task_y", "engine_not_registered", total=5, completed_n=2)
    mgr.save_checkpoint(cp["task_id"], cp)

    handler = make_checkpoint_resume_handler(mgr)
    assert handler(cp) is False
    # checkpoint 保留
    assert mgr.load_checkpoint("task_y") is not None


def test_handler_noop_when_checkpoint_already_done(mgr):
    cp = _make_cp("task_z", "voxcpm2", total=4, completed_n=4)
    mgr.save_checkpoint(cp["task_id"], cp)

    # 即使注册了引擎，完成态 checkpoint 也不应重复续跑
    register_resume_inference_fn("voxcpm2", lambda batch: [1.0] * len(batch))
    handler = make_checkpoint_resume_handler(mgr)
    assert handler(cp) is False
    # resume_state 语义：完成态 checkpoint 已被清理
    assert mgr.load_checkpoint("task_z") is None
