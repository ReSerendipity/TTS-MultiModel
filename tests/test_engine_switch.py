import logging
import re
from unittest.mock import MagicMock, patch

import pytest

from integrated_app.exceptions import EngineSwitchError, InsufficientVRAMError
from integrated_app.gpu_backend import GPUBackend
from integrated_app.model_registry import EngineName


class TestEngineSwitch:
    def test_rejects_unknown_engine(self):
        with pytest.raises(EngineSwitchError):
            from integrated_app.model_manager import switch_engine

            gen = switch_engine("unknown_engine")
            list(gen)

    def test_vram_check_raises_insufficient(self):
        with patch("integrated_app.gpu_backend.GPUBackendManager") as mock_gpu:
            mock_gpu.detect_backend.return_value = MagicMock(value="cuda")
            mock_gpu.get_device_properties.return_value = {"total_memory": 8 * 1024**3}
            mock_gpu.memory_allocated.return_value = 7 * 1024**3
            with (
                patch("integrated_app.model_manager_core.state.get_gpu_device", return_value=0),
                pytest.raises(InsufficientVRAMError),
            ):
                from integrated_app.model_manager import switch_engine

                gen = switch_engine("voxcpm2")
                list(gen)

    def test_engine_name_enum_values(self):
        assert EngineName.VOXCPM2.value == "voxcpm2"
        assert EngineName.INDEXTTS2.value == "indextts2"

    def test_registry_initial_state(self):
        from integrated_app.model_registry import ModelRegistry

        ModelRegistry._reset()
        r = ModelRegistry()
        assert r.voxcpm_model is None
        assert r.indextts2_engine is None
        assert r.current_engine is None
        assert r.model_loaded is False
        ModelRegistry._reset()


class TestSwitchVramRelease:
    """回归：引擎切换必须真正回收旧引擎显存。

    背景（2026-09-18）：切到 indextts2 时日志显示"清理完成"但 allocated 始终是
    6.04GB，新引擎在满载的卡上继续加载到 12.10GB / 总显存 11.94GB。
    """

    def test_snapshot_holds_no_live_model_references(self):
        """状态快照只能是名字与布尔标志，不得持有模型对象。

        快照字典是 switch_engine 的局部变量，生命周期横跨 unload_model() 与
        finally 里的 free_gpu_memory()；一旦持有 voxcpm_model，权重引用计数就
        不归零，empty_cache 一个字节都收不掉。
        """
        from integrated_app.model_manager_core.switch import _snapshot_engine_state

        sentinel = object()
        with patch("integrated_app.model_manager_core.switch.registry") as mock_reg:
            mock_reg.current_engine = EngineName.VOXCPM2.value
            mock_reg.voxcpm_model = sentinel
            mock_reg.indextts2_engine = None
            snapshot = _snapshot_engine_state()

        assert snapshot["engine"] == EngineName.VOXCPM2.value
        assert snapshot["had_voxcpm"] is True
        assert snapshot["had_indextts2"] is False
        assert all(item is not sentinel for item in snapshot.values())

    def test_unload_clears_every_voxcpm_slot(self):
        """卸载必须走 clear_voxcpm()，把 enhancer 与引擎门面实例一并摘掉。"""
        from integrated_app.model_manager_core.unload import unload_model

        with (
            patch("integrated_app.model_manager_core.unload.registry") as mock_reg,
            patch("integrated_app.model_manager_core.unload.free_gpu_memory") as mock_free,
            patch("integrated_app.model_manager_core.unload.get_health_monitor"),
            patch("integrated_app.model_manager_core.state.get_gpu_device", return_value=None),
            patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend", return_value=GPUBackend.CPU),
        ):
            mock_reg.indextts2_engine = None
            mock_reg.get_all_engine_instances.return_value = {}
            unload_model()

        mock_reg.clear_voxcpm.assert_called_once_with()
        mock_reg.clear_indextts2.assert_called_once_with()
        mock_free.assert_called_once_with()

    def test_wait_vram_freed_exposes_fake_release(self):
        """allocated 没下降时不得判定"已释放"——旧实现用绝对空闲量会误判通过。"""
        from integrated_app.model_manager_core.switch import _wait_vram_freed

        gb = 1024**3
        stuck = int(6.04 * gb)
        with (
            patch("integrated_app.gpu_backend.GPUBackendManager.memory_allocated", return_value=stuck),
            patch(
                "integrated_app.gpu_backend.GPUBackendManager.get_device_properties",
                return_value={"total_memory": int(11.94 * gb)},
            ),
        ):
            # 模型仍被强引用：回收量 0，即使 total-allocated 高达 5.9GB
            assert (
                _wait_vram_freed(
                    0,
                    max_wait=0.05,
                    poll_interval=0.01,
                    baseline_allocated=stuck,
                    expected_release_bytes=int(6.5 * gb),
                )
                is False
            )
            # 无基线可对照时退化为旧的绝对空闲判断（保持既有行为）
            assert _wait_vram_freed(0, max_wait=0.05, poll_interval=0.01) is True

    def test_wait_vram_freed_accepts_real_release(self):
        from integrated_app.model_manager_core.switch import _wait_vram_freed

        gb = 1024**3
        with (
            patch("integrated_app.gpu_backend.GPUBackendManager.memory_allocated", return_value=int(0.3 * gb)),
            patch(
                "integrated_app.gpu_backend.GPUBackendManager.get_device_properties",
                return_value={"total_memory": int(11.94 * gb)},
            ),
        ):
            assert (
                _wait_vram_freed(
                    0,
                    max_wait=0.5,
                    poll_interval=0.01,
                    baseline_allocated=int(6.04 * gb),
                    expected_release_bytes=int(6.5 * gb),
                )
                is True
            )

    def test_load_indextts2_raises_when_vram_insufficient(self):
        """显存不足必须硬失败并给出建议，而不是空喊"改用 CPU 模式"后继续在 cuda 上加载。"""
        from integrated_app.model_manager_core.load import load_indextts2

        gb = 1024**3
        with (
            patch("integrated_app.model_manager_core.load.get_indextts2_model_path", return_value="fake/model"),
            patch("integrated_app.model_manager_core.load.os") as mock_os,
            patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend", return_value=GPUBackend.CUDA),
            patch(
                "integrated_app.gpu_backend.GPUBackendManager.get_memory_info",
                return_value=(int(11.94 * gb), int(6.04 * gb), int(6.12 * gb), int(5.9 * gb)),
            ),
        ):
            mock_os.path.exists.return_value = True
            with pytest.raises(InsufficientVRAMError, match="显存不足"):
                list(load_indextts2())

    def test_vram_need_is_same_number_everywhere(self, caplog):
        """预检与热待机对同一引擎必须报出相同的 needed（原先 1.5/1.2/1.0 三套口径）。"""
        from integrated_app.model_manager_core.switch import _can_hot_standby, _check_vram_prereq

        gb = 1024**3
        with (
            caplog.at_level(logging.INFO, logger="tts_multimodel"),
            patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend", return_value=GPUBackend.CUDA),
            patch(
                "integrated_app.gpu_backend.GPUBackendManager.get_device_properties",
                return_value={"total_memory": int(11.94 * gb)},
            ),
            patch("integrated_app.gpu_backend.GPUBackendManager.memory_allocated", return_value=int(2.44 * gb)),
            patch("integrated_app.model_manager_core.state.get_gpu_device", return_value=0),
            patch("integrated_app.model_manager_core.switch.registry") as mock_reg,
        ):
            mock_reg.current_engine = None
            _check_vram_prereq(EngineName.INDEXTTS2.value, GPUBackend.CUDA, 0)
            _can_hot_standby(EngineName.INDEXTTS2.value)

        reported = {float(v) for v in re.findall(r"需要 (\d+\.\d+)GB", caplog.text)}
        assert reported == {9.0}


class TestUnloadForDirectLoad:
    """回归 #84：routes 直连专用加载器时，加载前必须先清场卸载驻留引擎。

    背景：load 端点对 voxcpm2 / indextts2 / indextts20 直接调专用加载器，
    绕过 switch_engine 的 M-R1 记账与卸载阶段，12GB 卡上直切必然 503。
    """

    def test_helper_unloads_resident_engine_and_waits(self):
        """有驻留引擎时：委托 unload_model、清缓存并按基线核验回收。"""
        from integrated_app.model_manager_core.load import _unload_loaded_engines_for_load

        gb = 1024**3
        with (
            patch("integrated_app.model_manager_core.unload.unload_model") as mock_unload,
            patch("integrated_app.model_manager_core.load.registry") as mock_reg,
            patch("integrated_app.model_registry.ENGINE_VRAM_REQUIREMENTS", {"voxcpm2": 6.5}),
            patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend", return_value=GPUBackend.CUDA),
            patch("integrated_app.gpu_backend.GPUBackendManager.memory_allocated", return_value=int(9.0 * gb)),
            patch("integrated_app.gpu_backend.GPUBackendManager.empty_cache") as mock_empty,
            patch("integrated_app.model_manager_core.switch._wait_vram_freed", return_value=True) as mock_wait,
            patch("integrated_app.model_manager_core.state.get_gpu_device", return_value=0),
        ):
            mock_reg.current_engine = "voxcpm2"
            mock_reg.voxcpm_model = object()
            mock_reg.indextts2_engine = None
            mock_reg.get_all_engine_instances.return_value = {}
            unloaded = _unload_loaded_engines_for_load("indextts2")

        assert unloaded == "voxcpm2"
        mock_unload.assert_called_once_with()
        mock_empty.assert_called_once_with()
        mock_wait.assert_called_once_with(0, baseline_allocated=int(9.0 * gb), expected_release_bytes=int(6.5 * gb))

    def test_helper_noop_when_nothing_loaded(self):
        """无驻留引擎时必须是零成本空操作（传统切换路径已卸载后复入此场景）。"""
        from integrated_app.model_manager_core.load import _unload_loaded_engines_for_load

        with (
            patch("integrated_app.model_manager_core.unload.unload_model") as mock_unload,
            patch("integrated_app.model_manager_core.load.registry") as mock_reg,
        ):
            mock_reg.current_engine = None
            mock_reg.voxcpm_model = None
            mock_reg.indextts2_engine = None
            mock_reg.get_all_engine_instances.return_value = {}
            unloaded = _unload_loaded_engines_for_load("voxcpm2")

        assert unloaded is None
        mock_unload.assert_not_called()

    def test_load_indextts2_unloads_resident_engine_before_precheck(self):
        """默认路径：load_indextts2 必须在预检前调用清场助手（接线证明）。"""
        from integrated_app.model_manager_core.load import load_indextts2

        gb = 1024**3
        with (
            patch(
                "integrated_app.model_manager_core.load._unload_loaded_engines_for_load",
                return_value="voxcpm2",
            ) as mock_clear,
            patch("integrated_app.model_manager_core.load.get_indextts2_model_path", return_value="fake/model"),
            patch("integrated_app.model_manager_core.load.os") as mock_os,
            patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend", return_value=GPUBackend.CUDA),
            patch(
                "integrated_app.gpu_backend.GPUBackendManager.get_memory_info",
                return_value=(int(11.94 * gb), int(6.04 * gb), int(6.12 * gb), int(5.9 * gb)),
            ),
            pytest.raises(InsufficientVRAMError, match="已自动卸载驻留引擎 voxcpm2"),
        ):
            mock_os.path.exists.return_value = True
            list(load_indextts2())

        mock_clear.assert_called_once_with("indextts2")

    def test_load_indextts2_skips_unload_when_auto_unload_false(self):
        """热待机传 auto_unload=False 时不得清场（双引擎常驻语义）。"""
        from integrated_app.model_manager_core.load import load_indextts2

        gb = 1024**3
        with (
            patch("integrated_app.model_manager_core.load._unload_loaded_engines_for_load") as mock_clear,
            patch("integrated_app.model_manager_core.load.get_indextts2_model_path", return_value="fake/model"),
            patch("integrated_app.model_manager_core.load.os") as mock_os,
            patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend", return_value=GPUBackend.CUDA),
            patch(
                "integrated_app.gpu_backend.GPUBackendManager.get_memory_info",
                return_value=(int(11.94 * gb), int(6.04 * gb), int(6.12 * gb), int(5.9 * gb)),
            ),
            pytest.raises(InsufficientVRAMError, match="显存不足"),
        ):
            mock_os.path.exists.return_value = True
            list(load_indextts2(auto_unload=False))

        mock_clear.assert_not_called()
