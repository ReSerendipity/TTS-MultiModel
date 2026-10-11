"""水印默认开关（NEXT 评估 P2-2）的单元测试。

验证：
1. ``watermark.watermark_enabled()`` 默认 True（config.yaml
   ``security.audio_watermark_enabled: true``，行为与 P2-2 前硬编码 enable=True 一致）；
2. 显式设为 false 时返回 False（唯一默认开关被真实消费）；
3. 配置读取异常时按最安全口径回退 True（fail-closed 到「开启水印」）；
4. ``generation.save_audio`` 保存路径确实把开关传给 watermark_audio.enable
   （接线验证，防止未来改回硬编码）。
"""

from __future__ import annotations

import inspect
import types

import numpy as np

from integrated_app import generation
from integrated_app.watermark import watermark_enabled


def _fake_get_config(enabled: bool):
    """构造带 pydantic_config.security.audio_watermark_enabled 的假 config 对象。"""
    security = types.SimpleNamespace(audio_watermark_enabled=enabled)
    return types.SimpleNamespace(pydantic_config=types.SimpleNamespace(security=security))


class TestWatermarkSwitch:
    """水印默认开关的行为测试。"""

    def test_default_enabled(self):
        """默认开启：真实配置下 watermark_enabled() 为 True。"""
        assert watermark_enabled() is True

    def test_respects_disabled_config(self, monkeypatch):
        """显式关闭：security.audio_watermark_enabled=false 时返回 False。"""
        monkeypatch.setattr("integrated_app.config.get_config", lambda: _fake_get_config(False))
        assert watermark_enabled() is False

    def test_respects_enabled_config(self, monkeypatch):
        """显式开启：security.audio_watermark_enabled=true 时返回 True。"""
        monkeypatch.setattr("integrated_app.config.get_config", lambda: _fake_get_config(True))
        assert watermark_enabled() is True

    def test_fails_closed_to_enabled_on_config_error(self, monkeypatch):
        """配置读取异常时回退 True（宁可多嵌不可漏嵌）。"""

        def _boom():
            raise RuntimeError("config not ready")

        monkeypatch.setattr("integrated_app.config.get_config", _boom)
        assert watermark_enabled() is True


class TestSaveAudioWiring:
    """保存路径对开关的真实接线（P2-2 前 enable=True 硬编码的回归防护）。"""

    def _call_save_audio(self, monkeypatch, tmp_path, switch_value: bool) -> dict:
        called: dict = {}

        def _fake_watermark_audio(wav, sr, enable, source_id, **kwargs):  # noqa: ARG001
            called["enable"] = enable
            wm_meta = {"sample_rate_out": sr, "watermarked": True, "source_id": source_id}
            return wav, wm_meta

        monkeypatch.setattr("integrated_app.watermark.watermark_audio", _fake_watermark_audio)
        monkeypatch.setattr("integrated_app.watermark.watermark_enabled", lambda: switch_value)
        monkeypatch.setattr(generation, "SAVE_DIR", str(tmp_path))

        wav = np.zeros(48000, dtype=np.float32)
        generation.save_audio(wav, sr=24000, prefix="wm_test", format="wav")
        return called

    def test_save_path_passes_enabled(self, monkeypatch, tmp_path):
        """开关为 True 时 enable=True 传给 watermark_audio。"""
        called = self._call_save_audio(monkeypatch, tmp_path, True)
        assert called.get("enable") is True

    def test_save_path_passes_disabled(self, monkeypatch, tmp_path):
        """开关为 False 时 enable=False 传给 watermark_audio（P2-2 前不会发生）。"""
        called = self._call_save_audio(monkeypatch, tmp_path, False)
        assert called.get("enable") is False

    def test_switch_is_live_attribute_not_hardcoded(self):
        """回归护栏：save_audio 源码中不得再出现硬编码 enable=True。"""
        src = inspect.getsource(generation.save_audio)
        assert "enable=watermark_enabled()" in src
        assert "enable=True" not in src

    def test_source_id_still_constant(self):
        """溯源常量不因开关而可配置（P2-2 只控制 enable）。"""
        src = inspect.getsource(generation.save_audio)
        assert "source_id=WATERMARK_SOURCE_ID" in src


class TestVoxcpm2StreamingWiring:
    """流式序列化路径对开关的接线（voxcpm2/streaming.py）。"""

    def test_streaming_module_uses_switch(self):
        """voxcpm2 流式序列化使用 watermark_enabled() 而非硬编码 True。"""
        from integrated_app.engines.voxcpm2 import streaming as vox_streaming

        src = inspect.getsource(vox_streaming)
        assert "enable=watermark_enabled()" in src
        assert "enable=True" not in src

    def test_streaming_source_id_constant(self):
        from integrated_app.engines.voxcpm2 import streaming as vox_streaming

        src = inspect.getsource(vox_streaming)
        assert "source_id=WATERMARK_SOURCE_ID" in src
