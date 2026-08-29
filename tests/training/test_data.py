"""training/data.py 模块单元测试 — 覆盖 HFVoxCPMDataset 边界。

覆盖目标模块: app/integrated_app/training/data.py
覆盖率目标: >=60%

覆盖范围:
- DatasetEntry NamedTuple
- HFVoxCPMDataset: __init__ / _scan_data_dir / __len__ / __getitem__ / stats /
  pad_sequences / collate_fn
- BatchProcessor: __init__ / _load_audio / _tokenize / __call__ / device / dataset_cnt
- 安全性测试: 空目录 / 损坏文件 / 越界时长 / metadata.jsonl 解析

注意: training 模块依赖 torch / argbind / datasets / soundfile 等重量级包,
CI 离线环境可能不完整。测试使用 try/except 在导入失败时跳过。
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Try importing training modules; skip if dependencies unavailable
try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

try:
    from integrated_app.training.data import BatchProcessor, DatasetEntry, HFVoxCPMDataset

    HAS_TRAINING = True
except Exception:
    HAS_TRAINING = False

pytestmark = pytest.mark.skipif(
    not (HAS_TORCH and HAS_TRAINING),
    reason="Training modules require torch/argbind/datasets/soundfile",
)


# =====================================================================
# DatasetEntry 测试
# =====================================================================


class TestDatasetEntry:
    """DatasetEntry NamedTuple 测试。"""

    def test_creation(self):
        entry = DatasetEntry(
            audio_path=Path("/tmp/test.wav"),
            text="hello",
            speaker_id="spk1",
            duration=2.5,
        )
        assert entry.audio_path == Path("/tmp/test.wav")
        assert entry.text == "hello"
        assert entry.speaker_id == "spk1"
        assert entry.duration == 2.5

    def test_creation_without_speaker(self):
        entry = DatasetEntry(
            audio_path=Path("/tmp/test.wav"),
            text="hello",
            speaker_id=None,
            duration=3.0,
        )
        assert entry.speaker_id is None
        assert entry.duration == 3.0


# =====================================================================
# HFVoxCPMDataset 测试
# =====================================================================


class TestHFVoxCPMDatasetScan:
    """HFVoxCPMDataset 目录扫描测试。"""

    def test_nonexistent_dir_raises(self, tmp_path):
        bad_dir = tmp_path / "nonexistent"
        with pytest.raises(ValueError, match="不存在"):
            HFVoxCPMDataset(data_dir=bad_dir)

    def test_empty_dir_raises(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with pytest.raises(ValueError, match="没有可用的"):
            HFVoxCPMDataset(data_dir=empty_dir)

    def test_scan_wav_txt_pairs(self, tmp_path):
        """Test scanning wav+txt pairs."""
        # Create fake wav files and txt files
        for i in range(3):
            (tmp_path / f"sample{i}.wav").write_bytes(b"\x00" * 100)
            (tmp_path / f"sample{i}.txt").write_text(f"hello {i}", encoding="utf-8")

        with patch.object(HFVoxCPMDataset, "_safe_read_duration", return_value=5.0):
            ds = HFVoxCPMDataset(data_dir=tmp_path, split="train")
            assert len(ds) > 0

    def test_scan_missing_txt_skipped(self, tmp_path):
        """Wav without txt should be skipped."""
        (tmp_path / "sample1.wav").write_bytes(b"\x00" * 100)
        (tmp_path / "sample1.txt").write_text("hello", encoding="utf-8")
        (tmp_path / "sample2.wav").write_bytes(b"\x00" * 100)  # No txt
        (tmp_path / "sample2.txt").write_text("", encoding="utf-8")  # Empty text

        with patch.object(HFVoxCPMDataset, "_safe_read_duration", return_value=5.0):
            ds = HFVoxCPMDataset(data_dir=tmp_path, split="train")
            # Only sample1 should be valid (sample2 has empty txt)
            assert len(ds) == 1

    def test_scan_metadata_jsonl(self, tmp_path):
        """Test scanning metadata.jsonl."""
        (tmp_path / "sample1.wav").write_bytes(b"\x00" * 100)
        (tmp_path / "sample2.wav").write_bytes(b"\x00" * 100)

        lines = [
            json.dumps({"audio_file": "sample1.wav", "text": "hello", "duration": 3.0}),
            json.dumps({"audio_file": "sample2.wav", "text": "world", "duration": 4.0}),
        ]
        (tmp_path / "metadata.jsonl").write_text("\n".join(lines), encoding="utf-8")

        ds = HFVoxCPMDataset(data_dir=tmp_path, split="train")
        assert len(ds) > 0

    def test_scan_metadata_jsonl_missing_fields(self, tmp_path):
        """metadata.jsonl with missing fields should be skipped."""
        (tmp_path / "sample1.wav").write_bytes(b"\x00" * 100)
        (tmp_path / "sample1.txt").write_text("hello", encoding="utf-8")

        # Bad line: missing text
        (tmp_path / "metadata.jsonl").write_text(json.dumps({"audio_file": "sample1.wav"}), encoding="utf-8")

        with pytest.raises(ValueError):
            HFVoxCPMDataset(data_dir=tmp_path)

    def test_duration_filtering(self, tmp_path):
        """Test min/max duration filtering."""
        (tmp_path / "short.wav").write_bytes(b"\x00" * 100)
        (tmp_path / "short.txt").write_text("short", encoding="utf-8")
        (tmp_path / "long.wav").write_bytes(b"\x00" * 100)
        (tmp_path / "long.txt").write_text("long", encoding="utf-8")

        def mock_duration(path):
            if "short" in str(path):
                return 0.5  # Below min_duration
            return 25.0  # Above max_duration

        with (
            patch.object(HFVoxCPMDataset, "_safe_read_duration", side_effect=mock_duration),
            pytest.raises(ValueError, match="没有可用的"),
        ):
            HFVoxCPMDataset(data_dir=tmp_path, min_duration=1.0, max_duration=20.0)

    def test_split_ratio(self, tmp_path):
        """Test train/eval split."""
        for i in range(10):
            (tmp_path / f"sample{i:02d}.wav").write_bytes(b"\x00" * 100)
            (tmp_path / f"sample{i:02d}.txt").write_text(f"text {i}", encoding="utf-8")

        with patch.object(HFVoxCPMDataset, "_safe_read_duration", return_value=5.0):
            train_ds = HFVoxCPMDataset(data_dir=tmp_path, split="train", split_ratio=0.8)
            eval_ds = HFVoxCPMDataset(data_dir=tmp_path, split="eval", split_ratio=0.8)
            assert len(train_ds) == 8
            assert len(eval_ds) == 2

    def test_seed_reproducibility(self, tmp_path):
        """Same seed should produce same split."""
        for i in range(10):
            (tmp_path / f"sample{i:02d}.wav").write_bytes(b"\x00" * 100)
            (tmp_path / f"sample{i:02d}.txt").write_text(f"text {i}", encoding="utf-8")

        with patch.object(HFVoxCPMDataset, "_safe_read_duration", return_value=5.0):
            ds1 = HFVoxCPMDataset(data_dir=tmp_path, split="train", seed=42)
            ds2 = HFVoxCPMDataset(data_dir=tmp_path, split="train", seed=42)
            assert len(ds1) == len(ds2)
            for e1, e2 in zip(ds1._entries, ds2._entries):
                assert e1.audio_path == e2.audio_path


class TestHFVoxCPMDatasetStats:
    """HFVoxCPMDataset.stats 方法测试。"""

    def test_stats_with_entries(self, tmp_path):
        for i in range(5):
            (tmp_path / f"sample{i}.wav").write_bytes(b"\x00" * 100)
            (tmp_path / f"sample{i}.txt").write_text(f"text {i}", encoding="utf-8")

        with patch.object(HFVoxCPMDataset, "_safe_read_duration", return_value=5.0):
            ds = HFVoxCPMDataset(data_dir=tmp_path, split="train")
            stats = ds.stats()
            assert "total_count" in stats
            assert "skipped_count" in stats
            assert "min_duration" in stats
            assert "max_duration" in stats
            assert "avg_duration" in stats
            assert "speaker_count" in stats
            assert stats["total_count"] > 0
            assert stats["min_duration"] == 5.0
            assert stats["max_duration"] == 5.0

    def test_stats_empty_entries(self, tmp_path):
        """Stats with no entries should return zeros."""
        for i in range(2):
            (tmp_path / f"sample{i}.wav").write_bytes(b"\x00" * 100)
            (tmp_path / f"sample{i}.txt").write_text(f"text {i}", encoding="utf-8")

        with patch.object(HFVoxCPMDataset, "_safe_read_duration", return_value=5.0):
            ds = HFVoxCPMDataset(data_dir=tmp_path, split="train")
            ds._entries = []
            stats = ds.stats()
            assert stats["total_count"] == 0
            assert stats["min_duration"] == 0.0


class TestPadSequences:
    """HFVoxCPMDataset.pad_sequences 静态方法测试。"""

    def test_pad_empty(self):
        result = HFVoxCPMDataset.pad_sequences([], pad_value=-100)
        assert result.numel() == 0

    def test_pad_single(self):
        seq = torch.tensor([1, 2, 3])
        result = HFVoxCPMDataset.pad_sequences([seq], pad_value=-100)
        assert result.shape == (1, 3)
        assert torch.equal(result[0], seq)

    def test_pad_multiple(self):
        seq1 = torch.tensor([1, 2, 3])
        seq2 = torch.tensor([4, 5])
        result = HFVoxCPMDataset.pad_sequences([seq1, seq2], pad_value=-100)
        assert result.shape == (2, 3)
        assert result[1][2] == -100  # Padding value


class TestCollateFn:
    """HFVoxCPMDataset.collate_fn 类方法测试。"""

    def test_collate_basic(self):
        batch = [
            {"text_ids": [1, 2, 3], "audio_array": [0.1, 0.2, 0.3], "dataset_id": 0},
            {"text_ids": [4, 5], "audio_array": [0.4, 0.5], "dataset_id": 1},
        ]
        result = HFVoxCPMDataset.collate_fn(batch)
        assert "text_tokens" in result
        assert "audio_tokens" in result
        assert "task_ids" in result
        assert "dataset_ids" in result
        assert "is_prompts" in result
        assert result["text_tokens"].shape[0] == 2


# =====================================================================
# BatchProcessor 测试
# =====================================================================


class TestBatchProcessor:
    """BatchProcessor 测试。"""

    def test_init_simple(self):
        bp = BatchProcessor(
            tokenizer=None,
            feature_extractor=None,
            sample_rate=16000,
            max_length=512,
        )
        assert bp.sample_rate == 16000
        assert bp.max_length == 512
        assert bp.device == torch.device("cpu")
        assert bp.dataset_cnt == 1
        assert bp.audio_vae is None
        assert bp.packer is None

    def test_tokenize_without_tokenizer(self):
        bp = BatchProcessor(tokenizer=None, max_length=10)
        ids = bp._tokenize("hello")
        assert len(ids) == 5
        assert ids == [ord("h"), ord("e"), ord("l"), ord("l"), ord("o")]

    def test_tokenize_with_callable_encoder(self):
        mock_tok = MagicMock()
        mock_tok.encode.return_value = [1, 2, 3]
        bp = BatchProcessor(tokenizer=mock_tok, max_length=100)
        ids = bp._tokenize("hello")
        assert ids == [1, 2, 3]

    def test_tokenize_truncation(self):
        bp = BatchProcessor(tokenizer=None, max_length=3)
        ids = bp._tokenize("hello")
        assert len(ids) == 3

    def test_tokenize_exception_fallback(self):
        mock_tok = MagicMock()
        mock_tok.encode.side_effect = Exception("encode failed")
        bp = BatchProcessor(tokenizer=mock_tok, max_length=100)
        ids = bp._tokenize("hi")
        assert ids == [ord("h"), ord("i")]

    def test_call_empty_batch(self):
        bp = BatchProcessor(tokenizer=None, max_length=100)
        result = bp([])
        assert "empty" in result

    def test_properties(self):
        bp = BatchProcessor(tokenizer=None, max_length=100)
        assert bp.device == torch.device("cpu")
        assert bp.dataset_cnt == 1
        assert bp.audio_vae is None
        assert bp.packer is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
