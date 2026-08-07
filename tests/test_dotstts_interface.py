"""dotstts 引擎接口与 engine_interface 注册表单元测试。

覆盖目标模块: bin/integrated_app/engines/dotstts_engine.py / engine_interface.py
"""

from integrated_app.engine_interface import (
    TTSEngine,
    engine_registry,
)


class TestDotsTTSEngineInterface:
    def test_class_implements_protocol(self):
        from integrated_app.engines.dotstts_engine import DotsTTSEngine

        assert issubclass(DotsTTSEngine, TTSEngine)

    def test_properties_exist(self):
        from integrated_app.engines.dotstts_engine import DotsTTSEngine

        assert isinstance(DotsTTSEngine.__dict__["version"], property)

    def test_init_invalid_model_dir(self):
        from integrated_app.engines.dotstts_engine import DotsTTSEngine

        engine = DotsTTSEngine(model_dir="/nonexistent/path")
        assert engine.is_ready() is False


class TestEngineRegistry:
    def test_registered_engines(self):
        names = engine_registry.list_engines()
        assert "voxcpm2" in names
        assert "indextts2" in names
        assert "dotstts" in names

    def test_get_engine_class(self):
        assert engine_registry.get("voxcpm2") is not None
        assert engine_registry.get("no-such-engine") is None

    def test_is_registered(self):
        assert engine_registry.is_registered("indextts2") is True
        assert engine_registry.is_registered("nonexistent") is False

    def test_display_name(self):
        assert engine_registry.get_display_name("voxcpm2")

    def test_vram_requirement(self):
        assert engine_registry.get_vram_requirement("indextts2") > 0

    def test_protocol_duck_typing(self):
        # TTSEngine 是 Protocol，运行时 isinstance 校验
        class FakeEngine:
            def is_ready(self):
                return False

        assert not isinstance(FakeEngine(), TTSEngine)
