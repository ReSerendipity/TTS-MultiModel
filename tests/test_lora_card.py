"""LoRA 训练产物卡片 schema 守卫测试（MLOps 落地 P0-#3）。

不 import torch / argbind / voxcpm：与 tests/test_lora_training_config.py 同源思路，
在无 CUDA 环境直接校验 pydantic schema 与 write_card() 往返。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from integrated_app.training.lora_card import (
    LoraHyperparams,
    LoraTrainCard,
    write_card,
)

_REPO = Path(__file__).resolve().parents[1]
EXAMPLE = _REPO / "app" / "integrated_app" / "training" / "lora_card.example.yaml"


def _good_kwargs() -> dict:
    return {
        "persona_name": "alice",
        "base_model_dir": "model/VoxCPM2",
        "base_model_card": "docs/model-cards/VoxCPM2.yaml",
        "git_commit": "a" * 40,
        "train_manifest_sha256": "b" * 64,
        "val_manifest_sha256": "c" * 64,
        "hyperparams": LoraHyperparams(
            learning_rate=1e-4,
            batch_size=1,
            grad_accum_steps=8,
            max_steps=2000,
            lora_r=8,
            lora_alpha=16,
        ),
        "trained_at": datetime(2026, 10, 6, tzinfo=timezone.utc),
        "artifacts": ["adapter.safetensors", "lora_config.json"],
    }


def test_example_yaml_validates():
    """示例模板必须能被 schema 接受（占位 SHA 也满足格式）。"""
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    card = LoraTrainCard.model_validate(data)
    assert card.persona_name == "example_persona"
    assert card.hyperparams.lora_r == 8


def test_good_card_roundtrips():
    card = LoraTrainCard(**_good_kwargs())
    dumped = card.model_dump(mode="json")
    again = LoraTrainCard.model_validate(dumped)
    assert again.persona_name == "alice"
    assert again.trained_at.tzinfo is not None


def test_bad_git_commit_rejected():
    kw = _good_kwargs()
    kw["git_commit"] = "short"
    with pytest.raises(ValidationError):
        LoraTrainCard(**kw)


def test_bad_sha256_rejected():
    kw = _good_kwargs()
    kw["train_manifest_sha256"] = "zzz"  # 非十六进制
    with pytest.raises(ValidationError):
        LoraTrainCard(**kw)


def test_absolute_artifact_rejected():
    kw = _good_kwargs()
    # Windows 下 /etc/passwd 不算绝对路径（无盘符），用带盘符的绝对路径
    kw["artifacts"] = ["C:/Windows/system32/drivers/etc/hosts"]
    with pytest.raises(ValidationError):
        LoraTrainCard(**kw)


def test_parent_traversal_artifact_rejected():
    kw = _good_kwargs()
    kw["artifacts"] = ["../etc/passwd"]
    with pytest.raises(ValidationError):
        LoraTrainCard(**kw)


def test_write_card(tmp_path: Path):
    card = LoraTrainCard(**_good_kwargs())
    out = write_card(card, tmp_path / "lora" / "alice")
    assert out.exists()
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["persona_name"] == "alice"
    # 回读必须通过 schema
    LoraTrainCard.model_validate(data)
