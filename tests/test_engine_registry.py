"""引擎注册一致性测试：注册表与实际引擎实现必须对齐（防热插拔回归）。

对应维护契约：engines 注册名与实现必须同步；任何新增/移除引擎都会在此失败。
"""

from integrated_app.engine_interface import engine_registry

EXPECTED_ENGINES = {"voxcpm2", "indextts2"}


def test_builtin_engines_registered():
    """内置引擎（VoxCPM2 / IndexTTS2）导入后必须全部出现在注册表。"""
    names = set(engine_registry.list_engines())
    missing = EXPECTED_ENGINES - names
    assert not missing, f"注册表缺少引擎: {sorted(missing)}（实际: {sorted(names)}）"


def test_registry_matches_engine_name_enum():
    """注册表引擎名必须与 EngineName 枚举完全一致。"""
    from integrated_app.model_registry import EngineName

    enum_values = {e.value for e in EngineName}
    registered = set(engine_registry.list_engines())
    assert enum_values == registered, f"枚举与注册表不一致: {sorted(enum_values)} vs {sorted(registered)}"
