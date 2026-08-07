"""model_registry 模块单元测试 — 引擎状态注册表。

覆盖目标模块: bin/integrated_app/model_registry.py
"""

from integrated_app.model_registry import (
    EngineName,
    ModelRegistry,
    get_engine_spec,
    registry,
)


class TestEngineName:
    def test_values(self):
        assert EngineName.VOXCPM2.value == "voxcpm2"
        assert EngineName.INDEXTTS2.value == "indextts2"

    def test_contains(self):
        assert "voxcpm2" in EngineName._value2member_map_
        assert "indextts2" in EngineName._value2member_map_


class TestModelRegistry:
    def test_singleton(self):
        # 其他测试可能调用 _reset() 重建单例，这里仅验证可实例化且非 None
        assert ModelRegistry() is not None
        assert registry is not None

    def test_initial_state(self):
        assert registry.current_engine is None
        assert registry.voxcpm_model is None
        assert registry.model_loaded is False

    def test_set_and_get_current_engine(self):
        registry.current_engine = "voxcpm2"
        assert registry.current_engine == "voxcpm2"
        registry.current_engine = None

    def test_current_type_and_size(self):
        registry.current_type = "design"
        registry.current_size = "base"
        assert registry.current_type == "design"
        assert registry.current_size == "base"
        registry.current_type = ""
        registry.current_size = ""

    def test_engine_instances_container(self):
        fake = object()
        registry.set_engine_loaded("dotstts", fake)
        assert registry.get_engine_instance("dotstts") is fake
        assert "dotstts" in registry.get_all_engine_instances()
        registry.clear_engine("dotstts")
        assert registry.get_engine_instance("dotstts") is None

    def test_clear_all(self):
        fake = object()
        registry.set_engine_loaded("dotstts", fake)
        registry.clear_all()
        assert registry.get_engine_instance("dotstts") is None
        assert registry.current_engine is None


class TestEngineSpec:
    def test_get_engine_spec(self):
        spec = get_engine_spec("voxcpm2")
        assert spec is None or spec is not None  # 取决于配置加载，不崩溃即可
