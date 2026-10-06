"""LoRA 训练产物元数据卡（MLOps 落地 P0-#3 / P1-#6）。

本模块回答「这个 LoRA 音色从哪来」：

- 基座权重目录与对应 model card（docs/model-cards/）；
- 训练 / 验证 manifest 的 sha256（数据血缘，取代「用户自备、无记录」）；
- 训练时的 git commit（代码血缘）；
- 关键超参（与 configs/lora_config.yaml 对齐）；
- 产物文件相对路径（lora/{persona}/ 下的 safetensors）。

设计约束：

- **不 import torch / argbind / voxcpm**：与 tests/test_lora_training_config.py 同源思路，
  让本模块在无 CUDA、无训练依赖的环境也能被 pytest 直接导入校验。
- 纯 pydantic v2 + datetime，无外部服务；yaml 序列化在调用方做。
- lora/ 目录本身被 .gitignore（运行期产物），卡片随产物落盘到
  ``lora/{persona}/card.yaml``，本仓只固化 schema 与示例。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_PERSONA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class LoraHyperparams(BaseModel):
    """与 configs/lora_config.yaml 的键对齐（不强制全等，子集即可）。"""

    learning_rate: float = Field(gt=0, le=1.0)
    batch_size: int = Field(ge=1)
    grad_accum_steps: int = Field(ge=1)
    max_steps: int = Field(ge=1)
    lora_r: int = Field(ge=1, le=128)
    lora_alpha: float = Field(gt=0)
    sample_rate: int = Field(default=48000)
    max_grad_norm: float = Field(default=1.0, ge=0)


class LoraTrainCard(BaseModel):
    """一次 LoRA 训练产物的可追溯元数据。"""

    schema_version: str = "1.0"
    persona_name: Annotated[str, StringConstraints(pattern=_PERSONA)] = Field(
        description="音色目录名，落盘到 lora/{persona_name}/"
    )
    base_model_dir: str = Field(description="基座权重目录，如 model/VoxCPM2")
    base_model_card: str = Field(description="对应 docs/model-cards/*.yaml 相对路径")
    git_commit: Annotated[str, StringConstraints(pattern=_GIT_SHA.pattern)]
    train_manifest_sha256: Annotated[str, StringConstraints(pattern=_SHA256.pattern)]
    val_manifest_sha256: Annotated[str, StringConstraints(pattern=_SHA256.pattern)]
    hyperparams: LoraHyperparams
    trained_at: datetime = Field(description="训练完成时间，UTC")
    artifacts: list[str] = Field(
        default_factory=list,
        description="相对 lora/{persona_name}/ 的产物文件（.safetensors / lora_config.json）",
    )
    notes: str = ""

    @field_validator("trained_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    @field_validator("artifacts")
    @classmethod
    def _no_absolute_or_parent(cls, v: list[str]) -> list[str]:
        for item in v:
            p = Path(item)
            if p.is_absolute() or ".." in p.parts:
                raise ValueError(f"artifacts 必须是相对路径，拒绝: {item}")
        return v


def write_card(card: LoraTrainCard, out_dir: Path) -> Path:
    """把卡片写到 out_dir/card.yaml（调用方负责建目录）。"""
    import yaml  # 延迟导入：避免模块导入期依赖 yaml（测试仍可用 pydantic 校验）

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "card.yaml"
    target.write_text(
        yaml.safe_dump(card.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return target
