"""参考音频路径归一化测试（独占复核缺陷修复）。

空字符串/纯空白必须等价于「未提供参考音频」，否则下游会把它当成
有效路径并触发 ASR/文件读取失败。
"""

import numpy as np
import pytest
from app.integrated_app.engines.voxcpm2 import streaming as st


class FakeModel:
    sample_rate = 48000

    def __init__(self):
        self.calls = []

    def generate_streaming(self, **kwargs):
        self.calls.append(kwargs)
        yield np.zeros(480, dtype=np.float32)


@pytest.mark.parametrize("value", ["", "   ", None])
def test_blank_reference_path_normalizes_to_none(value):
    assert st._normalize_reference_path(value) is None


def test_valid_reference_path_is_preserved():
    assert st._normalize_reference_path("C:/tmp/ref.wav") == "C:/tmp/ref.wav"


def test_non_string_value_is_preserved():
    value = (np.zeros(10, dtype=np.float32), 16000)
    assert st._normalize_reference_path(value) is value


@pytest.mark.parametrize("value", ["", "   "])
def test_stream_generate_treats_blank_reference_as_none(monkeypatch, value):
    monkeypatch.setattr(st, "_wav_to_bytes", lambda wav, sr: b"wav")
    model = FakeModel()

    list(st.stream_generate(model=model, segments=["你好。"], reference_audio=value, mode="binary"))

    assert model.calls, "generate_streaming 未被调用"
    assert model.calls[0]["reference_wav_path"] is None


def test_stream_generate_keeps_valid_reference_path(monkeypatch):
    monkeypatch.setattr(st, "_wav_to_bytes", lambda wav, sr: b"wav")
    model = FakeModel()

    list(st.stream_generate(model=model, segments=["你好。"], reference_audio="C:/tmp/ref.wav", mode="binary"))

    assert model.calls[0]["reference_wav_path"] == "C:/tmp/ref.wav"
