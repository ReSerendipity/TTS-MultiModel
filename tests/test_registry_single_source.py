"""P0-2 回归：``_engines`` 单一事实源 + 旧私有字段 property 转发。

本批只做最小版：VoxCPM2 主模型、IndexTTS 引擎、VoxCPM2Engine 懒包装器都存
``_engines`` 的保留 key；旧 ``_voxcpm_model`` / ``_indextts2_engine`` /
``_voxcpm2_engine_instance`` 与公共 property 改为读写同一容器。通用引擎接口
（``get_all_engine_instances`` / ``get_engine_instance``）必须过滤保留槽位，
避免把主模型误当通用引擎重复计数。
"""

from __future__ import annotations

import pytest

from integrated_app.model_registry import multi_engine_registry, registry


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear_all()
    yield
    registry.clear_all()


def test_legacy_voxcpm_property_forwards_to_container():
    fake = object()
    registry.voxcpm_model = fake

    assert registry._engines[registry._KEY_VOXCPM_MODEL] is fake
    assert registry.voxcpm_model is fake
    assert registry.model_loaded is True
    assert "voxcpm2" not in registry.get_all_engine_instances()
    assert registry.get_engine_instance("voxcpm2") is None


def test_legacy_indextts_property_forwards_to_container():
    fake = object()
    registry.set_indextts2_loaded(fake, engine_name="indextts20")

    assert registry._engines[registry._KEY_INDEXTTS_ENGINE] is fake
    assert registry.indextts2_engine is fake
    assert registry.current_engine == "indextts20"
    assert registry.get_all_engine_instances() == {}
    names = multi_engine_registry.get_loaded_engine_names()
    assert "indextts20" in names
    assert "indextts2" not in names


def test_legacy_engine_wrapper_forwards_to_container():
    wrapper = object()
    registry._voxcpm2_engine_instance = wrapper

    assert registry._engines[registry._KEY_VOXCPM_ENGINE_INSTANCE] is wrapper
    assert registry._voxcpm2_engine_instance is wrapper
    assert "voxcpm2_engine" not in registry.get_all_engine_instances()


def test_clear_voxcpm_removes_model_and_wrapper():
    registry.set_voxcpm_loaded(object())
    registry._voxcpm2_engine_instance = object()

    registry.clear_voxcpm()

    assert registry.voxcpm_model is None
    assert registry._voxcpm2_engine_instance is None
    assert registry._KEY_VOXCPM_MODEL not in registry._engines
    assert registry._KEY_VOXCPM_ENGINE_INSTANCE not in registry._engines
    assert registry.model_loaded is False


def test_generic_engine_still_works_and_is_not_confused_with_reserved_slots():
    fake = object()
    registry.set_engine_loaded("generic_test_engine", fake)

    assert registry.get_engine_instance("generic_test_engine") is fake
    assert registry.get_all_engine_instances() == {"generic_test_engine": fake}
    assert registry.model_loaded is True

    registry.clear_engine("generic_test_engine")
    assert registry.model_loaded is False


def test_reserved_slots_reject_generic_registration_and_clearing():
    with pytest.raises(ValueError):
        registry.set_engine_loaded("voxcpm2", object())
    with pytest.raises(ValueError):
        registry.clear_engine("indextts2")

    assert registry.voxcpm_model is None
    assert registry.indextts2_engine is None


def test_clear_all_empties_single_container():
    registry.set_voxcpm_loaded(object())
    registry.set_indextts2_loaded(object())
    registry.set_engine_loaded("generic_test_engine", object())

    registry.clear_all()

    assert registry._engines == {}
    assert registry.model_loaded is False
