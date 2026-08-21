"""engines/voxcpm2/decorators.py 单元测试 — 生成上下文装饰器。

覆盖目标模块: app/integrated_app/engines/voxcpm2/decorators.py
"""

import pytest

from integrated_app.engines.voxcpm2.decorators import with_generation_context
from integrated_app.exceptions import EngineSwitchError, GenerationError


class TestWithGenerationContext:
    def test_model_not_loaded_raises(self, monkeypatch):
        from integrated_app import model_registry

        monkeypatch.setattr(model_registry.registry, "voxcpm_model", None)

        @with_generation_context(phase_name="测试")
        def fake_gen():
            return "ok"

        with pytest.raises(EngineSwitchError):
            fake_gen()

    def test_success_path(self, monkeypatch):
        from integrated_app import model_registry

        # mock 模型已加载
        monkeypatch.setattr(model_registry.registry, "voxcpm_model", object())
        monkeypatch.setattr("integrated_app.engines.voxcpm2.decorators._check_voxcpm2_lock", lambda: True)

        @with_generation_context(phase_name="测试", use_progress=False)
        def fake_gen():
            return "ok"

        assert fake_gen() == "ok"

    def test_lock_busy_raises(self, monkeypatch):
        from integrated_app import model_registry

        monkeypatch.setattr(model_registry.registry, "voxcpm_model", object())
        monkeypatch.setattr("integrated_app.engines.voxcpm2.decorators._check_voxcpm2_lock", lambda: False)

        @with_generation_context(phase_name="测试")
        def fake_gen():
            return "ok"

        with pytest.raises(GenerationError):
            fake_gen()

    def test_exception_marked_error(self, monkeypatch):
        from integrated_app import model_registry

        monkeypatch.setattr(model_registry.registry, "voxcpm_model", object())
        monkeypatch.setattr("integrated_app.engines.voxcpm2.decorators._check_voxcpm2_lock", lambda: True)

        @with_generation_context(phase_name="测试")
        def failing_gen():
            raise ValueError("boom")

        with pytest.raises(Exception):
            failing_gen()

    def test_cleanup_called(self, monkeypatch):
        from integrated_app import model_registry

        monkeypatch.setattr(model_registry.registry, "voxcpm_model", object())
        monkeypatch.setattr("integrated_app.engines.voxcpm2.decorators._check_voxcpm2_lock", lambda: True)
        cleaned = []

        @with_generation_context(phase_name="测试", use_progress=False, cleanup_fn=lambda: cleaned.append(1))
        def fake_gen():
            return "ok"

        fake_gen()
        assert cleaned == [1]

    def test_tracker_usage(self, monkeypatch):
        from integrated_app import model_registry

        monkeypatch.setattr(model_registry.registry, "voxcpm_model", object())
        monkeypatch.setattr("integrated_app.engines.voxcpm2.decorators._check_voxcpm2_lock", lambda: True)

        @with_generation_context(phase_name="测试", use_progress=False)
        def fake_gen():
            return "ok"

        fake_gen()
