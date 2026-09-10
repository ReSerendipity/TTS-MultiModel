"""水印密钥管理与 3 级失败策略测试（桌面分发与安全加固 P0）。

覆盖目标模块: app/integrated_app/watermark.py
验收口径：单测覆盖 3 级策略——
  ① 重试成功（返回水印音频）
  ② 重试仍失败（写 .provenance.json + 返回原始音频，provenance 档）
  ③ block 档失败（抛 WatermarkEmbedError，产出被阻断）
另有：env 注入统一密钥（空间隔离）、读时权限自愈不破坏功能。
"""

import os
from pathlib import Path

import numpy as np
import pytest

import integrated_app.watermark as wm_mod
from integrated_app.watermark import (
    WatermarkEmbedError,
    _get_watermark_secret,
    embed_watermark,
    watermark_audio,
)

SR = 44100


def _sine_wave(sample_rate=SR, duration=1.0, freq=440.0):
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


@pytest.fixture(autouse=True)
def _reset_secret_cache():
    """每个用例后清空水印密钥缓存，避免跨用例串密钥。"""
    yield
    wm_mod._watermark_secret_cache = None


class TestWatermarkKeyInjection:
    def test_env_key_injection(self, monkeypatch):
        """空间隔离：TTS_WATERMARK_KEY 注入统一密钥（64 hex = 32 字节）。"""
        # 其他测试文件（如 test_watermark.py）可能已填充模块级密钥缓存，
        # env 覆盖仅在冷缓存时生效（进程启动时先注入 env 再首次调用）——显式清缓存。
        wm_mod._watermark_secret_cache = None
        key_hex = os.urandom(32).hex()
        monkeypatch.setenv("TTS_WATERMARK_KEY", key_hex)
        assert _get_watermark_secret() == bytes.fromhex(key_hex)

    def test_invalid_env_key_falls_back(self, monkeypatch, tmp_path):
        """非法 env 值被忽略，回退文件密钥。"""
        monkeypatch.setenv("TTS_WATERMARK_KEY", "not-hex!!")
        key = _get_watermark_secret()
        assert key is not None and len(key) == 32

    def test_read_does_not_break_when_key_exists(self, tmp_path, monkeypatch):
        """已有密钥文件时读取正常，且读时自愈不抛异常。"""
        from integrated_app.security import secret_key as sk

        key_file = tmp_path / ".watermark_key"
        key_file.write_bytes(os.urandom(32))
        monkeypatch.setattr(wm_mod, "_WATERMARK_KEY_PATH", str(key_file))
        assert sk.harden_secret_file_permissions(key_file) is True
        key = _get_watermark_secret()
        assert key is not None and len(key) == 32


class TestWatermarkFailureStrategy:
    def test_retry_succeeds_returns_watermarked_audio(self, monkeypatch):
        """① 首次失败、重试成功 → 返回含水印音频。"""
        real = embed_watermark
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient failure")
            return real(*args, **kwargs)

        monkeypatch.setattr(wm_mod, "embed_watermark", flaky)
        audio = _sine_wave()
        out, meta = watermark_audio(audio, SR, enable=True, source_id="test")
        assert calls["n"] == 2  # 首次 + 重试 1 次
        assert meta["watermarked"] is True
        assert "watermark_failed" not in meta

    def test_provenance_mode_writes_sidecar_and_returns_original(self, monkeypatch, tmp_path):
        """② 重试仍失败 + provenance 档 → 写 .provenance.json 侧车 + 返回原始音频。"""

        def always_fail(*args, **kwargs):
            return args[0], wm_mod.WatermarkResult(success=False, message="embed failed", payload=None, snr_db=0.0)

        monkeypatch.setattr(wm_mod, "embed_watermark", always_fail)
        audio = _sine_wave()
        out_path = str(tmp_path / "out.wav")

        out, meta = watermark_audio(
            audio, SR, enable=True, source_id="test", output_path=out_path, failure_mode="provenance"
        )

        assert meta["watermarked"] is False
        assert meta["watermark_failed"] is True
        assert meta["failure_strategy"] == "provenance"
        assert out is audio  # 返回原始音频
        sidecar = Path(f"{out_path}.provenance.json")
        assert sidecar.exists()
        import json

        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        assert payload["watermark"]["embedded"] is False
        assert payload["audio_file"] == "out.wav"

    def test_provenance_skips_sidecar_without_output_path(self, monkeypatch):
        """无产出路径时 provenance 档不写盘（如 streaming 字节流），仍返回原始音频。"""

        def always_fail(*args, **kwargs):
            return args[0], wm_mod.WatermarkResult(success=False, message="embed failed", payload=None, snr_db=0.0)

        monkeypatch.setattr(wm_mod, "embed_watermark", always_fail)
        audio = _sine_wave()
        out, meta = watermark_audio(audio, SR, enable=True, source_id="test", failure_mode="provenance")
        assert meta["watermark_failed"] is True
        assert out is audio

    def test_block_mode_raises_and_blocks_output(self, monkeypatch):
        """③ block 档：重试仍失败 → 抛 WatermarkEmbedError，产出被阻断。"""

        def always_fail(*args, **kwargs):
            return args[0], wm_mod.WatermarkResult(success=False, message="embed failed", payload=None, snr_db=0.0)

        monkeypatch.setattr(wm_mod, "embed_watermark", always_fail)
        audio = _sine_wave()
        with pytest.raises(WatermarkEmbedError, match="block"):
            watermark_audio(audio, SR, enable=True, source_id="test", failure_mode="block")

    def test_default_mode_is_provenance(self, monkeypatch):
        """config 未配置时默认 provenance（fail-open 但留痕）。"""

        def always_fail(*args, **kwargs):
            return args[0], wm_mod.WatermarkResult(success=False, message="embed failed", payload=None, snr_db=0.0)

        monkeypatch.setattr(wm_mod, "embed_watermark", always_fail)
        audio = _sine_wave()
        out, meta = watermark_audio(audio, SR, enable=True, source_id="test")
        assert meta["failure_strategy"] == "provenance"
        assert out is audio

    def test_block_mode_raises_on_exception_too(self, monkeypatch):
        """embed 抛异常 + block 档 → 同样阻断。"""

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(wm_mod, "embed_watermark", boom)
        with pytest.raises(WatermarkEmbedError):
            watermark_audio(_sine_wave(), SR, enable=True, failure_mode="block")
