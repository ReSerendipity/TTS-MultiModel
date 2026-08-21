"""mixed_precision 模块单元测试 — 混合精度配置与推理上下文。

覆盖目标模块: app/integrated_app/mixed_precision.py
"""

import pytest

from integrated_app.mixed_precision import (
    MixedPrecisionConfig,
    MixedPrecisionContext,
    apply_mixed_precision,
    detect_optimal_dtype,
)


class TestMixedPrecisionConfig:
    def test_default(self):
        cfg = MixedPrecisionConfig()
        assert cfg.enabled is True
        assert cfg.dtype == "auto"

    def test_invalid_dtype(self):
        with pytest.raises(ValueError):
            MixedPrecisionConfig(dtype="float64")

    def test_fp32_disables(self):
        cfg = MixedPrecisionConfig(dtype="fp32")
        assert cfg.enabled is False


class TestDetectOptimalDtype:
    def test_disabled_returns_fp32(self):
        cfg = MixedPrecisionConfig(enabled=False)
        assert detect_optimal_dtype(cfg) is __import__("torch").float32

    def test_explicit_bf16(self):
        import torch

        cfg = MixedPrecisionConfig(dtype="bf16")
        assert detect_optimal_dtype(cfg) is torch.bfloat16

    def test_explicit_fp16(self):
        import torch

        cfg = MixedPrecisionConfig(dtype="fp16")
        assert detect_optimal_dtype(cfg) is torch.float16

    def test_auto_cpu_returns_fp32(self, monkeypatch):
        import torch

        from integrated_app import gpu_backend

        class _FakeManager:
            @staticmethod
            def detect_backend():
                return gpu_backend.GPUBackend.CPU

        monkeypatch.setattr("integrated_app.gpu_backend.GPUBackendManager", _FakeManager)
        assert detect_optimal_dtype() is torch.float32

    def test_auto_cuda_ampere(self, monkeypatch):
        import torch

        from integrated_app import gpu_backend

        class _FakeManager:
            @staticmethod
            def detect_backend():
                return gpu_backend.GPUBackend.CUDA

            @staticmethod
            def get_device_properties(_idx):
                return {"major": 8, "minor": 0}

        monkeypatch.setattr("integrated_app.gpu_backend.GPUBackendManager", _FakeManager)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        assert detect_optimal_dtype() is torch.bfloat16

    def test_auto_cuda_pre_ampere(self, monkeypatch):
        import torch

        from integrated_app import gpu_backend

        class _FakeManager:
            @staticmethod
            def detect_backend():
                return gpu_backend.GPUBackend.CUDA

            @staticmethod
            def get_device_properties(_idx):
                return {"major": 7, "minor": 0}

        monkeypatch.setattr("integrated_app.gpu_backend.GPUBackendManager", _FakeManager)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        assert detect_optimal_dtype() is torch.float16


class TestApplyMixedPrecision:
    def test_disabled_skips(self):
        import torch

        cfg = MixedPrecisionConfig(enabled=False)
        model = object()
        out, dtype = apply_mixed_precision(model, cfg)
        assert out is model
        assert dtype is torch.float32

    def test_bf16_converts(self, monkeypatch):
        import torch

        class FakeModel:
            def bfloat16(self):
                return "converted-bf16"

        cfg = MixedPrecisionConfig(dtype="bf16")
        out, dtype = apply_mixed_precision(FakeModel(), cfg)
        assert out == "converted-bf16"
        assert dtype is torch.bfloat16

    def test_fp16_converts(self, monkeypatch):
        import torch

        class FakeModel:
            def half(self):
                return "converted-fp16"

        cfg = MixedPrecisionConfig(dtype="fp16")
        out, dtype = apply_mixed_precision(FakeModel(), cfg)
        assert out == "converted-fp16"
        assert dtype is torch.float16

    def test_fp32_keeps(self, monkeypatch):
        import torch

        model = object()
        cfg = MixedPrecisionConfig(dtype="fp32")
        out, dtype = apply_mixed_precision(model, cfg)
        assert out is model
        assert dtype is torch.float32


class TestMixedPrecisionContext:
    def test_disabled_context(self):
        cfg = MixedPrecisionConfig(enabled=False)
        with MixedPrecisionContext(cfg) as ctx:
            import torch

            assert ctx.dtype is torch.float32

    def test_fp32_context_no_autocast(self):
        cfg = MixedPrecisionConfig(dtype="fp32")
        with MixedPrecisionContext(cfg) as ctx:
            import torch

            assert ctx.dtype is torch.float32
            assert ctx.autocast_ctx is None

    def test_scale_loss_without_scaler(self):
        cfg = MixedPrecisionConfig(dtype="fp32")
        with MixedPrecisionContext(cfg) as ctx:
            loss = "loss-tensor"
            assert ctx.scale_loss(loss) is loss

    def test_unscale_and_step_without_scaler(self):
        cfg = MixedPrecisionConfig(dtype="fp32")
        with MixedPrecisionContext(cfg) as ctx:
            calls = []
            optimizer = type("Opt", (), {"step": lambda self: calls.append("step")})()
            ctx.unscale_and_step(optimizer)
            assert calls == ["step"]
