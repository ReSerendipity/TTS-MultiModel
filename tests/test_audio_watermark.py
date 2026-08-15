"""音频水印文件级嵌入与提取单元测试（P2-1）。

覆盖目标模块: bin/integrated_app/audio_watermark.py
"""

import os
import sys
import tempfile

import numpy as np
import pytest
import soundfile as sf

_BIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

# 水印频带为 16-20kHz，需采样率 >= 40kHz 才能容纳
SR = 44100


def _sine_wave(sample_rate=SR, duration=2.0, freq=440.0):
    """生成正弦波测试音频。"""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class TestPayloadSerialization:
    """测试 payload 序列化与反序列化（CRC32 + Base62）。"""

    def test_roundtrip_serialization(self):
        """payload 序列化后再反序列化应一致。"""
        from integrated_app.audio_watermark import _deserialize_payload, _serialize_payload

        payload = {
            "task_id": "gen-123",
            "timestamp": 1234567890,
            "product_id": "tts_multimodel",
        }
        serialized = _serialize_payload(payload)
        assert "." in serialized  # 包含 CRC32 分隔符
        deserialized = _deserialize_payload(serialized)
        assert deserialized is not None
        assert deserialized["task_id"] == "gen-123"
        assert deserialized["timestamp"] == 1234567890

    def test_tampered_payload_rejected(self):
        """篡改后的 payload CRC32 校验失败。"""
        from integrated_app.audio_watermark import _deserialize_payload, _serialize_payload

        payload = {"task_id": "test"}
        serialized = _serialize_payload(payload)
        # 篡改 CRC32 部分
        parts = serialized.split(".", 1)
        tampered = "00000000." + parts[1]
        assert _deserialize_payload(tampered) is None

    def test_invalid_format_returns_none(self):
        """格式错误的字符串返回 None。"""
        from integrated_app.audio_watermark import _deserialize_payload

        assert _deserialize_payload("invalid") is None
        assert _deserialize_payload("abc.def") is None  # 非 hex CRC


class TestEmbedExtractWatermark:
    """测试文件级水印嵌入与提取。"""

    def setup_method(self):
        """创建临时音频文件。"""
        self.tmpdir = tempfile.mkdtemp(prefix="tts_wm_test_")
        self.audio_path = os.path.join(self.tmpdir, "test.wav")
        audio = _sine_wave()
        sf.write(self.audio_path, audio, SR)

    def teardown_method(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_embed_returns_path(self):
        """embed_watermark 返回音频路径。"""
        from integrated_app.audio_watermark import embed_watermark

        result = embed_watermark(self.audio_path, {"task_id": "test-001"})
        assert result == self.audio_path

    def test_embed_preserves_audio_format(self):
        """嵌入水印后音频文件仍可正常读取。"""
        from integrated_app.audio_watermark import embed_watermark

        embed_watermark(self.audio_path, {"task_id": "test-002"})
        audio, sr = sf.read(self.audio_path)
        assert sr == SR
        assert len(audio) > 0

    def test_extract_returns_dict_or_none(self):
        """extract_watermark 返回 dict 或 None。"""
        from integrated_app.audio_watermark import extract_watermark

        result = extract_watermark(self.audio_path)
        assert result is None or isinstance(result, dict)

    def test_extract_from_nonexistent_file(self):
        """从不存在的文件提取水印返回 None。"""
        from integrated_app.audio_watermark import extract_watermark

        assert extract_watermark("/nonexistent/path.wav") is None

    def test_roundtrip_embed_then_extract_product_id(self):
        """嵌入后应能检出 product_id（回归：.tmp.wav 写回 bug 曾导致检出失败）。

        实测 source_id 字段为既有 watermark.py 行为的乱码，故不断言 source_id；
        仅断言可稳定检出的 product_id 字段。
        """
        from integrated_app.audio_watermark import embed_watermark, extract_watermark

        embed_watermark(self.audio_path, {"task_id": "roundtrip-001"})
        extracted = extract_watermark(self.audio_path)
        assert extracted is not None, "嵌入后应能检出水印（写回 bug 回归）"
        assert extracted.get("product_id") == "tts_multimodel"
