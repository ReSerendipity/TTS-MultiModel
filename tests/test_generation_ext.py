"""generation.py 单元测试补充 — 文本分段、音频合并与工具函数。

覆盖目标模块: bin/integrated_app/generation.py
"""

import os

import numpy as np

from integrated_app.generation import (
    increment_seed,
    merge_audio_segments,
    save_audio,
    split_into_chunks,
    split_text_for_tts,
)


class TestSplitTextForTTS:
    def test_short_text_single_segment(self):
        segments = split_text_for_tts("你好世界", max_chars=200)
        assert segments == ["你好世界"]

    def test_long_text_split(self):
        text = "这是第一句话。" * 30
        segments = split_text_for_tts(text, max_chars=50)
        assert len(segments) > 1
        assert all(len(s) <= 55 for s in segments)  # 允许标点边界溢出少量

    def test_empty_text(self):
        assert split_text_for_tts("") == [""]

    def test_whitespace_text(self):
        assert split_text_for_tts("   ") == ["   "]

    def test_preserves_content(self):
        text = "今天天气很好，我们一起去公园散步。明天可能下雨。"
        joined = "".join(split_text_for_tts(text, max_chars=100))
        assert "今天天气很好" in joined


class TestMergeAudioSegments:
    def test_merge_two_segments(self):
        seg1 = np.zeros(1000, dtype=np.float32)
        seg2 = np.ones(1000, dtype=np.float32) * 0.1
        merged, out_sr = merge_audio_segments([seg1, seg2], sr=24000)
        assert out_sr == 24000
        assert merged is not None
        # crossfade 重叠（50ms=1200 样本，取最短段 1/3=333）使总长略短于 2000
        assert 1500 <= len(merged) < 2000
        assert merged.dtype == np.float32

    def test_merge_single_segment(self):
        seg = np.zeros(100, dtype=np.float32)
        merged, _ = merge_audio_segments([seg], sr=24000)
        assert merged is not None
        assert len(merged) >= 100

    def test_merge_empty(self):
        merged, sr = merge_audio_segments([], sr=24000)
        assert merged is None
        assert sr == 24000


class TestSaveAudio:
    def test_save_wav(self, tmp_path, monkeypatch):
        monkeypatch.setattr("integrated_app.generation.SAVE_DIR", str(tmp_path))
        wav = np.zeros(2400, dtype=np.float32)
        path, filename = save_audio(wav, 24000, prefix="test", format="wav")
        assert os.path.exists(path)
        assert filename.endswith(".wav")


class TestHelpers:
    def test_increment_seed(self):
        assert increment_seed(100, 0) == 100
        assert increment_seed(100, 2) != 100

    def test_split_into_chunks(self):
        chunks = split_into_chunks("abcdefghij", max_chars=3)
        assert isinstance(chunks, list)
        assert all(isinstance(c, tuple) and len(c) == 2 for c in chunks)
        # 拼接回原文
        joined = "".join(c[0] for c in chunks)
        assert joined == "abcdefghij"
        # 索引递增
        indices = [c[1] for c in chunks]
        assert indices == sorted(indices)

    def test_split_into_chunks_empty(self):
        assert isinstance(split_into_chunks(""), list)
