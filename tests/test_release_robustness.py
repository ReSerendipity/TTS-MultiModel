"""发布前"故意破坏"鲁棒性测试（2026-09-19）。

只读目录/写盘失败/权重缺失这类环境在正常开发机上碰不到，却是发布版用户最常
遇到的意外。全部用 tmp_path + monkeypatch 模拟，**不触碰仓库里的 model/ 与 outputs/**。
"""

import os
from unittest.mock import patch

import numpy as np
import pytest

import integrated_app.generation as gen
from integrated_app.exceptions import AudioProcessingError, EngineLoadError, TTSError
from integrated_app.gpu_backend import GPUBackend
from integrated_app.model_manager_core import load as mgr_load
from integrated_app.model_manager_core import switch as sw


def _wav(seed: int = 0, n: int = 2205) -> np.ndarray:
    return (np.random.default_rng(seed).random(n) * 0.5).astype(np.float32)


class TestSaveAudioHostileDisk:
    def test_write_failure_is_wrapped_and_temp_cleaned(self, tmp_path, monkeypatch):
        """写盘失败（权限/磁盘满/杀软占用）要变成 AudioProcessingError，且不留 .tmp。

        注：不用 ``os.chmod(dir, 0o500)`` 造只读目录 —— Windows 上该调用不会让目录
        真的不可写（实测照样写出文件），必须直接让底层写入抛错。
        """
        monkeypatch.setattr(gen, "SAVE_DIR", str(tmp_path), raising=False)
        with (
            patch("soundfile.write", side_effect=OSError(13, "Permission denied")),
            pytest.raises(AudioProcessingError),
        ):
            gen.save_audio(_wav(), 22050, prefix="robust", format="wav")
        assert not [f for f in tmp_path.iterdir() if ".tmp" in f.name], "失败路径残留临时文件"

    def test_replace_failure_is_wrapped_and_temp_cleaned(self, tmp_path, monkeypatch):
        """临时文件写成功、但换名失败（磁盘满在两阶段之间发生）的中间态。"""
        monkeypatch.setattr(gen, "SAVE_DIR", str(tmp_path), raising=False)
        with (
            patch("os.replace", side_effect=OSError(28, "No space left on device")),
            pytest.raises(AudioProcessingError),
        ):
            gen.save_audio(_wav(1), 22050, prefix="robust2", format="wav")
        assert not [f for f in tmp_path.iterdir() if ".tmp" in f.name]

    def test_success_path_leaves_no_temp(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gen, "SAVE_DIR", str(tmp_path), raising=False)
        out_path, name = gen.save_audio(_wav(2), 22050, prefix="ok", format="wav")
        assert os.path.isfile(out_path)
        assert not [f for f in tmp_path.iterdir() if ".tmp" in f.name]


class TestMissingWeights:
    def test_loader_reports_download_command(self, monkeypatch):
        """权重缺失：文案必须给出"怎么修"，而不是裸堆栈。

        这些 loader 的既有契约是把终局失败 **yield** 成进度文本（app_server 的
        lifespan 与 /api/model/load 都靠"失败"关键字识别），所以这里断言文本而非异常。
        """
        monkeypatch.setattr(mgr_load, "get_indextts2_model_path", lambda: "Z:/definitely/not/here", raising=False)
        tuples = list(mgr_load.load_indextts2())
        last = str(tuples[-1][0])
        assert "加载失败" in last and "download_indextts2" in last

    def test_switch_engine_escalates_swallowed_loader_failure(self, monkeypatch):
        """M-R9 回归：loader 咽下来的失败文本不能让切换以 status=ok 收场。

        修复前 switch_engine 把 ``"IndexTTS 2.5 加载失败: …"`` 当普通进度转发后
        正常收尾 → 调用方回 {"status":"ok"}，而旧引擎已被卸载，用户实际无引擎可用。
        """
        events: list[str] = []

        def fake_loader(**_kwargs):  # auto_unload kwarg（#84）加入 loader 契约后桩需透传
            yield "正在加载 IndexTTS 2.5 引擎...", None, None, None
            yield "IndexTTS 2.5 加载失败: FileNotFoundError: 模型文件不存在", None, None, None

        with (
            patch.object(sw, "_validate_engine_name", side_effect=lambda n: n),
            patch.object(
                sw,
                "_snapshot_engine_state",
                return_value={"engine": "voxcpm2", "had_voxcpm": True, "had_indextts2": False},
            ),
            patch.object(sw, "_check_vram_prereq", return_value=11.0),
            patch.object(sw, "_can_hot_standby", return_value=False),
            patch.object(sw, "unload_model", side_effect=lambda: events.append("unload")),
            patch.object(sw, "load_indextts2", fake_loader),
            patch.object(
                sw,
                "_rollback_engine",
                side_effect=lambda s, e: events.append(f"rollback:{s['engine']}:{type(e).__name__}"),
            ),
            patch.object(sw._state, "get_gpu_device", return_value=None),
            patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend", return_value=GPUBackend.CPU),
            pytest.raises(EngineLoadError, match="加载失败"),
        ):
            list(sw.switch_engine("indextts2"))

        assert events == ["unload", "rollback:voxcpm2:EngineLoadError"], events

    def test_switch_engine_escalates_failure_text_on_hot_standby_path(self, monkeypatch):
        """热待机路径（未卸载）也必须升级成异常，但不能触发回滚。"""
        events: list[str] = []

        def fake_loader(**_kwargs):  # auto_unload kwarg（#84）加入 loader 契约后桩需透传
            yield "IndexTTS 2.5 加载失败: 显存不足", None, None, None

        with (
            patch.object(sw, "_validate_engine_name", side_effect=lambda n: n),
            patch.object(
                sw,
                "_snapshot_engine_state",
                return_value={"engine": "voxcpm2", "had_voxcpm": True, "had_indextts2": False},
            ),
            patch.object(sw, "_check_vram_prereq", return_value=11.0),
            patch.object(sw, "_can_hot_standby", return_value=True),
            patch.object(sw, "unload_model", side_effect=lambda: events.append("unload")),
            patch.object(sw, "load_indextts2", fake_loader),
            patch.object(sw, "_rollback_engine", side_effect=lambda s, e: events.append("rollback")),
            patch.object(sw._state, "get_gpu_device", return_value=None),
            patch("integrated_app.gpu_backend.GPUBackendManager.detect_backend", return_value=GPUBackend.CPU),
            pytest.raises(TTSError),
        ):
            list(sw.switch_engine("indextts2"))

        assert events == [], f"未执行过卸载却回滚了：{events}"
