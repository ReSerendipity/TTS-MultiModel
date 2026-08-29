"""training/packers.py 模块单元测试 — 覆盖数据打包逻辑。

覆盖目标模块: app/integrated_app/training/packers.py
覆盖率目标: >=60%

覆盖范围:
- LengthSortedBatchPacker: __init__ / pack (空列表 / 正常 / 异常条目 / 超大条目)
- DynamicBucketPacker: __init__ / _bucket_index / pack (分桶逻辑 / 异常 duration)
- AudioFeatureProcessingPacker: __init__ / _first_pad_position /
  unpad_text_tokens / unpad_audio_tokens / process_tts_data

注意: training 模块依赖 torch / einops 等重量级包, CI 离线环境可能不完整。
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Try importing training modules; skip if dependencies unavailable
try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

try:
    from integrated_app.training.data import DatasetEntry
    from integrated_app.training.packers import (
        AudioFeatureProcessingPacker,
        DynamicBucketPacker,
        LengthSortedBatchPacker,
    )

    HAS_PACKERS = True
except Exception:
    HAS_PACKERS = False

pytestmark = pytest.mark.skipif(
    not (HAS_TORCH and HAS_PACKERS),
    reason="Packers require torch/einops/datasets",
)


# =====================================================================
# LengthSortedBatchPacker 测试
# =====================================================================


class TestLengthSortedBatchPacker:
    """LengthSortedBatchPacker 测试。"""

    def test_init_defaults(self):
        packer = LengthSortedBatchPacker()
        assert packer.max_batch_tokens == 3000
        assert callable(packer.sort_key)

    def test_init_custom_params(self):
        packer = LengthSortedBatchPacker(max_batch_tokens=5000)
        assert packer.max_batch_tokens == 5000

    def test_init_invalid_max_tokens_fallback(self):
        packer = LengthSortedBatchPacker(max_batch_tokens=0)
        assert packer.max_batch_tokens == 3000

    def test_init_negative_max_tokens_fallback(self):
        packer = LengthSortedBatchPacker(max_batch_tokens=-1)
        assert packer.max_batch_tokens == 3000

    def test_pack_empty_list(self):
        packer = LengthSortedBatchPacker(max_batch_tokens=1000)
        assert packer.pack([]) == []

    def test_pack_single_entry(self):
        packer = LengthSortedBatchPacker(max_batch_tokens=1000)
        entry = DatasetEntry(
            audio_path=Path("/tmp/test.wav"),
            text="hello",
            speaker_id=None,
            duration=2.0,
        )
        batches = packer.pack([entry])
        assert len(batches) == 1
        assert len(batches[0]) == 1

    def test_pack_multiple_entries_fit_one_batch(self):
        packer = LengthSortedBatchPacker(max_batch_tokens=1000)
        entries = [
            DatasetEntry(Path("/tmp/1.wav"), "a", None, 1.0),
            DatasetEntry(Path("/tmp/2.wav"), "b", None, 2.0),
            DatasetEntry(Path("/tmp/3.wav"), "c", None, 3.0),
        ]
        # 1*50 + 2*50 + 3*50 = 300 < 1000 -> all in one batch
        batches = packer.pack(entries)
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_pack_entries_split_into_batches(self):
        packer = LengthSortedBatchPacker(max_batch_tokens=200)
        entries = [
            DatasetEntry(Path("/tmp/1.wav"), "a", None, 1.0),  # ~50 tokens
            DatasetEntry(Path("/tmp/2.wav"), "b", None, 2.0),  # ~100 tokens
            DatasetEntry(Path("/tmp/3.wav"), "c", None, 4.0),  # ~200 tokens
        ]
        batches = packer.pack(entries)
        # Should create multiple batches due to token limit
        assert len(batches) >= 2

    def test_pack_sorts_by_length_descending(self):
        """Bigger entries should be placed first."""
        packer = LengthSortedBatchPacker(max_batch_tokens=10000)
        entries = [
            DatasetEntry(Path("/tmp/short.wav"), "s", None, 1.0),
            DatasetEntry(Path("/tmp/long.wav"), "l", None, 10.0),
        ]
        batches = packer.pack(entries)
        # Long entry should be in first batch, first position
        assert batches[0][0].duration == 10.0

    def test_pack_negative_sort_key_skipped(self):
        packer = LengthSortedBatchPacker(
            max_batch_tokens=1000,
            sort_key=lambda e: -1,  # Always returns negative
        )
        entry = DatasetEntry(Path("/tmp/test.wav"), "hello", None, 2.0)
        batches = packer.pack([entry])
        assert batches == []

    def test_pack_sort_key_exception_skipped(self):
        packer = LengthSortedBatchPacker(
            max_batch_tokens=1000,
            sort_key=lambda e: (_ for _ in ()).throw(ValueError("bad")),  # Always raises
        )
        entry = DatasetEntry(Path("/tmp/test.wav"), "hello", None, 2.0)
        batches = packer.pack([entry])
        assert batches == []

    def test_pack_best_fit_placement(self):
        """Test best-fit: new entry goes into batch with least remaining space that fits."""
        packer = LengthSortedBatchPacker(max_batch_tokens=500)
        # First entry takes 50 tokens -> batch 0 has 450 remaining
        # Second entry takes 400 tokens -> best fit is batch 0 (450 remaining >= 400)
        entries = [
            DatasetEntry(Path("/tmp/a.wav"), "a", None, 1.0),  # 50 tokens
            DatasetEntry(Path("/tmp/b.wav"), "b", None, 8.0),  # 400 tokens
            DatasetEntry(Path("/tmp/c.wav"), "c", None, 1.0),  # 50 tokens
        ]
        batches = packer.pack(entries)
        # a and c should be in same batch (both 50 tokens, fits in 500)
        # b should be in its own batch or same batch
        total_entries = sum(len(b) for b in batches)
        assert total_entries == 3


# =====================================================================
# DynamicBucketPacker 测试
# =====================================================================


class TestDynamicBucketPacker:
    """DynamicBucketPacker 测试。"""

    def test_init_defaults(self):
        packer = DynamicBucketPacker()
        assert packer.bucket_boundaries == (1.0, 5.0, 10.0, 20.0)
        assert packer.max_batch_tokens == 3000

    def test_init_custom_boundaries(self):
        packer = DynamicBucketPacker(bucket_boundaries_sec=(2.0, 8.0))
        assert packer.bucket_boundaries == (2.0, 8.0)

    def test_init_invalid_max_tokens(self):
        packer = DynamicBucketPacker(max_batch_tokens=-5)
        assert packer.max_batch_tokens == 3000

    def test_bucket_index_short(self):
        packer = DynamicBucketPacker(bucket_boundaries_sec=(1.0, 5.0, 10.0, 20.0))
        assert packer._bucket_index(0.5) == 0
        assert packer._bucket_index(1.0) == 0

    def test_bucket_index_medium(self):
        packer = DynamicBucketPacker(bucket_boundaries_sec=(1.0, 5.0, 10.0, 20.0))
        assert packer._bucket_index(3.0) == 1
        assert packer._bucket_index(7.0) == 2

    def test_bucket_index_long(self):
        packer = DynamicBucketPacker(bucket_boundaries_sec=(1.0, 5.0, 10.0, 20.0))
        assert packer._bucket_index(15.0) == 3
        assert packer._bucket_index(100.0) == 4  # Beyond last boundary

    def test_pack_empty(self):
        packer = DynamicBucketPacker()
        assert packer.pack([]) == []

    def test_pack_multiple_buckets(self):
        packer = DynamicBucketPacker(
            bucket_boundaries_sec=(2.0, 8.0),
            max_batch_tokens=10000,
        )
        entries = [
            DatasetEntry(Path("/tmp/short.wav"), "s", None, 1.0),  # bucket 0
            DatasetEntry(Path("/tmp/med.wav"), "m", None, 5.0),  # bucket 1
            DatasetEntry(Path("/tmp/long.wav"), "l", None, 15.0),  # bucket 2
        ]
        batches = packer.pack(entries)
        # Each bucket should produce at least one batch
        total_entries = sum(len(b) for b in batches)
        assert total_entries == 3

    def test_pack_negative_duration_skipped(self):
        packer = DynamicBucketPacker(max_batch_tokens=10000)
        entry = DatasetEntry(Path("/tmp/bad.wav"), "bad", None, -1.0)
        batches = packer.pack([entry])
        assert batches == []

    def test_pack_type_error_duration_skipped(self):
        packer = DynamicBucketPacker(max_batch_tokens=10000)
        # Create entry with non-numeric duration
        entry = DatasetEntry(Path("/tmp/bad.wav"), "bad", None, "not_a_number")  # type: ignore
        batches = packer.pack([entry])
        assert batches == []


# =====================================================================
# AudioFeatureProcessingPacker 测试
# =====================================================================


class TestAudioFeatureProcessingPacker:
    """AudioFeatureProcessingPacker 测试。"""

    def _make_mock_audio_vae(self):
        """Create a mock AudioVAE for testing."""
        mock_vae = MagicMock()
        mock_vae.hop_length = 200
        mock_vae.sample_rate = 24000
        mock_vae.encode.return_value = torch.zeros(1, 8, 10)  # [B, D, T]
        return mock_vae

    def test_init(self):
        mock_vae = self._make_mock_audio_vae()
        packer = AudioFeatureProcessingPacker(
            dataset_cnt=2,
            max_len=512,
            patch_size=1,
            feat_dim=8,
            audio_vae=mock_vae,
        )
        assert packer.dataset_cnt == 2
        assert packer.max_len == 512
        assert packer.patch_size == 1
        assert packer.feat_dim == 8
        assert packer.audio_start_id == 101
        assert packer.audio_end_id == 102
        assert "tts" in packer.process_functions
        assert packer.task_id_map["tts"] == 1

    def test_first_pad_position_no_pad(self):
        mock_vae = self._make_mock_audio_vae()
        packer = AudioFeatureProcessingPacker(1, 512, 1, 8, mock_vae)
        tokens = torch.tensor([1, 2, 3])
        assert packer._first_pad_position(tokens) is None

    def test_first_pad_position_with_pad(self):
        mock_vae = self._make_mock_audio_vae()
        packer = AudioFeatureProcessingPacker(1, 512, 1, 8, mock_vae)
        tokens = torch.tensor([1, 2, -100, -100])
        assert packer._first_pad_position(tokens) == 2

    def test_unpad_text_tokens_no_pad(self):
        mock_vae = self._make_mock_audio_vae()
        packer = AudioFeatureProcessingPacker(1, 512, 1, 8, mock_vae)
        tokens = torch.tensor([1, 2, 3])
        result = packer.unpad_text_tokens(tokens)
        assert torch.equal(result, tokens)

    def test_unpad_text_tokens_with_pad(self):
        mock_vae = self._make_mock_audio_vae()
        packer = AudioFeatureProcessingPacker(1, 512, 1, 8, mock_vae)
        tokens = torch.tensor([1, 2, -100, -100])
        result = packer.unpad_text_tokens(tokens)
        assert torch.equal(result, torch.tensor([1, 2]))

    def test_unpad_audio_tokens(self):
        mock_vae = self._make_mock_audio_vae()
        packer = AudioFeatureProcessingPacker(1, 512, 1, 8, mock_vae)
        tokens = torch.tensor([0.1, 0.2, -100.0, -100.0])
        result = packer.unpad_audio_tokens(tokens)
        assert torch.equal(result, torch.tensor([0.1, 0.2]))

    def test_call_basic(self):
        """Test __call__ with a simple batch."""
        mock_vae = self._make_mock_audio_vae()
        packer = AudioFeatureProcessingPacker(dataset_cnt=1, max_len=512, patch_size=1, feat_dim=8, audio_vae=mock_vae)
        audio_tokens = torch.tensor([[0.1, 0.2, 0.3]], dtype=torch.float32)
        text_tokens = torch.tensor([[1, 2, 3]], dtype=torch.int32)
        task_ids = torch.tensor([1], dtype=torch.int32)
        dataset_ids = torch.tensor([0], dtype=torch.int32)
        is_prompts = [False]

        result = packer(audio_tokens, text_tokens, task_ids, dataset_ids, is_prompts)
        assert "text_tokens" in result
        assert "audio_feats" in result
        assert "text_mask" in result
        assert "audio_mask" in result
        assert "loss_mask" in result
        assert "position_ids" in result
        assert "labels" in result
        assert "audio_task_ids" in result
        assert "audio_dataset_ids" in result

    def test_process_tts_data(self):
        """Test process_tts_data method."""
        mock_vae = self._make_mock_audio_vae()
        packer = AudioFeatureProcessingPacker(dataset_cnt=1, max_len=512, patch_size=1, feat_dim=8, audio_vae=mock_vae)
        audio_token = torch.tensor([0.1, 0.2, 0.3, 0.4], dtype=torch.float32)
        text_token = torch.tensor([1, 2, 3], dtype=torch.int32)

        result = packer.process_tts_data(audio_token, text_token, is_prompt=False)
        assert len(result) == 8  # 8-tuple return
        audio_duration = result[6]
        text_token_count = result[7]

        assert text_token_count == 3
        assert audio_duration > 0

    def test_process_tts_data_with_prompt(self):
        """Test process_tts_data with is_prompt=True."""
        mock_vae = self._make_mock_audio_vae()
        packer = AudioFeatureProcessingPacker(dataset_cnt=1, max_len=512, patch_size=1, feat_dim=8, audio_vae=mock_vae)
        audio_token = torch.tensor([0.1, 0.2, 0.3, 0.4], dtype=torch.float32)
        text_token = torch.tensor([1, 2, 3], dtype=torch.int32)

        result = packer.process_tts_data(audio_token, text_token, is_prompt=True)
        loss_mask = result[4]
        # When is_prompt=True, audio loss_mask should be all zeros
        # (loss_mask layout: [text_zeros, audio_zeros_if_prompt, end_zero])
        assert torch.all(loss_mask == 0)

    def test_encode_audio(self):
        """Test encode_audio method."""
        mock_vae = self._make_mock_audio_vae()
        packer = AudioFeatureProcessingPacker(dataset_cnt=1, max_len=512, patch_size=1, feat_dim=8, audio_vae=mock_vae)
        wav = torch.tensor([0.1, 0.2, 0.3, 0.4], dtype=torch.float32)
        feat = packer.encode_audio(wav)
        # encode_audio returns [1, T', D]
        assert feat.dim() == 3
        assert feat.size(0) == 1

    def test_extract_audio_feats(self):
        """Test extract_audio_feats method."""
        mock_vae = self._make_mock_audio_vae()
        packer = AudioFeatureProcessingPacker(dataset_cnt=1, max_len=512, patch_size=1, feat_dim=8, audio_vae=mock_vae)
        audio_data = torch.randn(1000, dtype=torch.float32)
        feats, duration = packer.extract_audio_feats(audio_data)
        assert feats.dim() == 4  # [1, T_patch, P, D]
        assert duration > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
