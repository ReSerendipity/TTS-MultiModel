"""batch_inference 模块单元测试 — 批量推理调度器。

覆盖目标模块: app/integrated_app/batch_inference.py
"""

import pytest

from integrated_app.batch_inference import (
    BatchInferencer,
    BatchInferenceResult,
    BatchInferenceStats,
    get_batch_inferencer,
)


def _dummy_inference(items):
    """返回与 items 等长的假音频张量。"""
    import torch

    return [torch.zeros(1000) for _ in items]


class TestBatchInferencer:
    def test_empty_items(self):
        inf = BatchInferencer()
        results, stats = inf.run([], _dummy_inference)
        assert results == []
        assert stats.total_items == 0

    def test_run_success(self):
        import torch

        inf = BatchInferencer(max_batch_size=4)
        items = [{"text": f"text-{i}"} for i in range(7)]
        results, stats = inf.run(items, _dummy_inference)
        assert len(results) == 7
        assert stats.total_items == 7
        assert stats.successful == 7
        assert all(r.success for r in results)
        assert all(r.audio is not None for r in results)
        assert all(isinstance(r.audio, torch.Tensor) for r in results)

    def test_on_item_done_callback(self):
        inf = BatchInferencer(max_batch_size=2)
        items = [{"text": "a"}, {"text": "b"}]
        done = []
        results, _ = inf.run(items, _dummy_inference, on_item_done=done.append)
        assert len(done) == 2
        assert done[0].index == 0

    def test_run_with_batch_failure(self):
        def flaky(items):
            if any(it["text"] == "boom" for it in items):
                raise RuntimeError("simulated failure")
            import torch

            return [torch.zeros(10) for _ in items]

        inf = BatchInferencer(max_batch_size=2)
        items = [{"text": "ok"}, {"text": "boom"}, {"text": "ok2"}]
        results, stats = inf.run(items, flaky)
        assert len(results) == 3
        failed = [r for r in results if not r.success]
        assert len(failed) >= 1
        assert any("simulated failure" in (r.error or "") for r in failed)

    def test_batch_size_dynamic_growth(self):
        inf = BatchInferencer(min_batch_size=1, max_batch_size=8)
        items = [{"text": f"t{i}"} for i in range(30)]
        _, stats = inf.run(items, _dummy_inference)
        assert stats.batch_size_used >= 1

    def test_get_batch_inferencer_singleton(self):
        a = get_batch_inferencer()
        b = get_batch_inferencer()
        assert a is b


class TestBatchInferenceResult:
    def test_fields(self):
        r = BatchInferenceResult(index=0, success=True, audio=None, elapsed_ms=1.5)
        assert r.index == 0
        assert r.success is True
        assert r.elapsed_ms == pytest.approx(1.5)


class TestBatchInferenceStats:
    def test_defaults(self):
        stats = BatchInferenceStats(total_items=3)
        assert stats.successful == 0
        assert stats.failed == 0
        assert stats.total_items == 3
        assert stats.batch_size_used == 0
