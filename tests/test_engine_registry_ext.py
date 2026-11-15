"""engine_interface 注册表扩展测试 — 自定义引擎注册 + config 权威元数据。

覆盖目标模块: app/integrated_app/engine_interface.py
"""

from pathlib import Path

import pytest
import yaml

from integrated_app.engine_interface import InMemoryEngineRegistry


class TestRegistryCustomRegistration:
    def test_register_custom_engine(self):
        registry = InMemoryEngineRegistry()

        class FakeEngine:
            def is_ready(self):
                return True

        registry.register("fake", FakeEngine, display_name="Fake", vram_requirement=3.0)
        assert registry.is_registered("fake") is True
        assert registry.get("fake") is FakeEngine
        assert registry.get_display_name("fake") == "Fake"
        assert registry.get_vram_requirement("fake") == pytest.approx(3.0)
        assert "fake" in registry.list_engines()

    def test_register_duplicate_overwrites(self):
        registry = InMemoryEngineRegistry()

        class A:
            pass

        class B:
            pass

        registry.register("x", A)
        registry.register("x", B)
        assert registry.get("x") is B

    def test_default_display_name_falls_back_to_name(self):
        registry = InMemoryEngineRegistry()

        class A:
            pass

        registry.register("engine-a", A)
        assert registry.get_display_name("engine-a") == "engine-a"
        assert registry.get_vram_requirement("engine-a") == pytest.approx(6.0)

    def test_unregistered_queries(self):
        registry = InMemoryEngineRegistry()
        assert registry.get("nothing") is None
        assert registry.get_display_name("nothing") == "nothing"
        assert registry.get_vram_requirement("nothing") == pytest.approx(6.0)


def test_builtin_metadata_prefers_config_authority():
    """P0-2：内置引擎 metadata 以 config.yaml 的 EngineSpecConfig 为准。"""
    from integrated_app.engine_interface import engine_registry

    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    declared = cfg["models"]["engines"]["voxcpm2"]
    meta = engine_registry.get_metadata("voxcpm2")

    assert meta["languages"] == declared["languages"]
    assert meta["vram_requirement"] == pytest.approx(declared["vram_gb"])
    assert meta["sample_rate"] == declared["sample_rate"]
    assert meta["requires_gpu"] is declared["requires_gpu"]
