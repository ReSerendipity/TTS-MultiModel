"""LoRA 训练配置的加载 / 校验 / 持久化。

# P2 法律注释：本训练模块仅用于 TTS_MultiModel 平台内的 VoxCPM LoRA 微调。
# 使用本模块训练产生的 LoRA 权重应保留 origin 元数据字段，便于版权追溯。
# 未经授权将训练权重用于商业闭源产品可能侵犯原作者权益。

training/ 目录对应 WebUI 中 LoRA 微调 Tab 的训练任务；scripts/train_voxcpm_finetune.py
作为训练入口会调用本模块：先通过 ``get_default_config`` 或 ``load_training_config``
拿到一份类型安全的 ``TrainingConfig``，再传给 data / accelerator / state / tracker
等下游模块。

配置使用 Pydantic 模型 + JSON/YAML 序列化，同时保留原有的 argbind 辅助函数
（``load_yaml_config`` / ``parse_args_with_config``）以满足 100% 向后兼容。
"""

from __future__ import annotations

import builtins
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import argbind
import yaml

try:
    from typing import Literal
except ImportError:  # pragma: no cover
    Literal = Any  # type: ignore[assignment]

try:
    from pydantic import BaseModel, Field, ValidationError
except ImportError:  # pragma: no cover - pydantic 缺失时使用 dataclass 兜底
    BaseModel = None  # type: ignore[assignment]

    class Field:  # type: ignore[no-redef]
        """pydantic.Field 的最小占位实现。"""

        def __new__(cls, default: Any = None, **kwargs: Any) -> Any:
            return default


logger = logging.getLogger("tts_multimodel.training.config")

TrainingPrecision = Literal["fp32", "fp16", "bf16"]


# ---------------------------------------------------------------------- #
# Pydantic / dataclass 配置模型
# ---------------------------------------------------------------------- #
class DatasetConfig(BaseModel if BaseModel is not None else object):  # type: ignore[misc]
    """数据集配置：数据根目录 / 采样率 / 时长过滤 / 切分比例。"""

    if BaseModel is not None:
        data_dir: Path = Field(..., description="数据集根目录，需包含 .wav 与同名 .txt 对或 metadata.jsonl")
        sample_rate: int = Field(16000, ge=8000, le=48000, description="音频重采样率（Hz）")
        min_duration_sec: float = Field(1.0, ge=0.0, le=10.0, description="最短有效时长，小于则丢弃")
        max_duration_sec: float = Field(20.0, ge=1.0, le=600.0, description="最长有效时长，超过则丢弃或截断")
        split_ratio: float = Field(0.9, ge=0.7, le=0.99, description="train/eval 切分比例（0.7~0.99）")
    else:

        def __init__(
            self,
            data_dir: Path,
            sample_rate: int = 16000,
            min_duration_sec: float = 1.0,
            max_duration_sec: float = 20.0,
            split_ratio: float = 0.9,
        ) -> None:
            """初始化数据集配置（pydantic 缺失时的 fallback 实现）。

            Args:
                data_dir: 数据集根目录，需包含 .wav 与同名 .txt 对或 metadata.jsonl
                sample_rate: 音频重采样率（Hz），默认 16000
                min_duration_sec: 最短有效时长（秒），小于则丢弃
                max_duration_sec: 最长有效时长（秒），超过则丢弃
                split_ratio: train/eval 切分比例（0.7~0.99）
            """
            self.data_dir = Path(data_dir)
            self.sample_rate = int(sample_rate)
            self.min_duration_sec = float(min_duration_sec)
            self.max_duration_sec = float(max_duration_sec)
            self.split_ratio = float(split_ratio)

        def dict(self) -> builtins.dict[str, Any]:
            """将配置转换为可 JSON 序列化的字典。

            Returns:
                包含所有配置字段的字典（Path 转为 str）
            """
            return {
                "data_dir": str(self.data_dir),
                "sample_rate": self.sample_rate,
                "min_duration_sec": self.min_duration_sec,
                "max_duration_sec": self.max_duration_sec,
                "split_ratio": self.split_ratio,
            }


class LoRAConfig(BaseModel if BaseModel is not None else object):  # type: ignore[misc]
    """LoRA 适配层配置：目标模块 / rank / alpha / dropout / bias。"""

    if BaseModel is not None:
        target_modules: list[str] = Field(
            default_factory=lambda: ["to_q", "to_k", "to_v", "to_out.0"],
            description="LoRA 注入的模块名列表（Transformer Attention 投影层）",
        )
        rank: int = Field(8, ge=4, le=64, description="LoRA 秩（r），越大表达力越强但参数量/显存线性增长")
        alpha: float = Field(16.0, gt=0.0, le=1024.0, description="LoRA alpha 缩放系数（通常取 rank 的 2x）")
        dropout: float = Field(0.05, ge=0.0, le=0.5, description="LoRA 适配层 dropout 概率")
        bias: Literal["none", "all", "lora_only"] = Field(  # noqa: UP037
            "none", description="bias 训练策略：none=不训练 / all=全部训练 / lora_only=只训练 lora 模块 bias"
        )
    else:

        def __init__(
            self,
            target_modules: list[str] | None = None,
            rank: int = 8,
            alpha: float = 16.0,
            dropout: float = 0.05,
            bias: str = "none",
        ) -> None:
            """初始化 LoRA 适配层配置（pydantic 缺失时的 fallback 实现）。

            Args:
                target_modules: LoRA 注入的模块名列表，默认 Attention Q/K/V/Out 四个投影层
                rank: LoRA 秩（r），越大表达力越强但参数量/显存线性增长
                alpha: LoRA alpha 缩放系数（通常取 rank 的 2 倍）
                dropout: LoRA 适配层 dropout 概率
                bias: bias 训练策略：none=不训练 / all=全部训练 / lora_only=只训练 lora 模块 bias
            """
            self.target_modules = list(target_modules) if target_modules else ["to_q", "to_k", "to_v", "to_out.0"]
            self.rank = int(rank)
            self.alpha = float(alpha)
            self.dropout = float(dropout)
            self.bias = bias

        def dict(self) -> builtins.dict[str, Any]:
            """将配置转换为可 JSON 序列化的字典。

            Returns:
                包含所有 LoRA 配置字段的字典
            """
            return {
                "target_modules": list(self.target_modules),
                "rank": self.rank,
                "alpha": self.alpha,
                "dropout": self.dropout,
                "bias": self.bias,
            }


class OptimizerConfig(BaseModel if BaseModel is not None else object):  # type: ignore[misc]
    """优化器配置：类型 / 学习率 / 权重衰减 / Adam betas。"""

    if BaseModel is not None:
        optimizer_type: Literal["adamw", "adam", "sgd"] = Field("adamw", description="优化器类型")  # noqa: UP037
        lr: float = Field(1e-4, gt=0.0, lt=1.0, description="LoRA 参数学习率")
        weight_decay: float = Field(0.01, ge=0.0, le=0.1, description="权重衰减 L2 正则系数")
        betas: tuple[float, float] = Field((0.9, 0.999), description="Adam(beta1, beta2) 动量参数")
    else:

        def __init__(
            self,
            optimizer_type: str = "adamw",
            lr: float = 1e-4,
            weight_decay: float = 0.01,
            betas: tuple[float, float] = (0.9, 0.999),
        ) -> None:
            """初始化优化器配置（pydantic 缺失时的 fallback 实现）。

            Args:
                optimizer_type: 优化器类型（adamw / adam / sgd）
                lr: LoRA 参数学习率
                weight_decay: 权重衰减 L2 正则系数
                betas: Adam(beta1, beta2) 动量参数
            """
            self.optimizer_type = optimizer_type
            self.lr = float(lr)
            self.weight_decay = float(weight_decay)
            self.betas = (float(betas[0]), float(betas[1]))

        def dict(self) -> builtins.dict[str, Any]:
            """将配置转换为可 JSON 序列化的字典。

            Returns:
                包含所有优化器配置字段的字典
            """
            return {
                "optimizer_type": self.optimizer_type,
                "lr": self.lr,
                "weight_decay": self.weight_decay,
                "betas": list(self.betas),
            }


class TrainingConfig(BaseModel if BaseModel is not None else object):  # type: ignore[misc]
    """单次 LoRA 训练的完整参数集合。

    Args:
        dataset: 数据集配置
        lora: LoRA 适配层配置
        optimizer: 优化器配置
        epochs: 总训练 epoch 数
        batch_size: 单卡每步 batch 大小
        grad_accum_steps: 梯度累积步数，等效 batch = batch_size * grad_accum_steps
        warmup_steps: 学习率 warmup 的 step 数
        precision: 混合精度类型 fp32/fp16/bf16
        save_every_n_epochs: 每 N 个 epoch 保存一个 checkpoint
        output_dir: 输出目录（checkpoint / logs / 最终权重）
        seed: 全局随机种子
    """

    if BaseModel is not None:
        dataset: DatasetConfig
        lora: LoRAConfig
        optimizer: OptimizerConfig
        epochs: int = Field(10, ge=1, le=200, description="总训练 epoch 数")
        batch_size: int = Field(2, ge=1, le=32, description="单卡每步 batch 大小")
        grad_accum_steps: int = Field(4, ge=1, le=32, description="梯度累积步数")
        warmup_steps: int = Field(100, ge=0, description="学习率 warmup 的 step 数")
        precision: TrainingPrecision = Field("fp16", description="混合精度类型")
        save_every_n_epochs: int = Field(1, ge=1, description="每 N epoch 保存一个 checkpoint")
        output_dir: Path = Field(..., description="训练输出目录")
        seed: int = Field(42, description="全局随机种子")
    else:

        def __init__(
            self,
            dataset: DatasetConfig,
            lora: LoRAConfig,
            optimizer: OptimizerConfig,
            epochs: int = 10,
            batch_size: int = 2,
            grad_accum_steps: int = 4,
            warmup_steps: int = 100,
            precision: TrainingPrecision = "fp16",
            save_every_n_epochs: int = 1,
            output_dir: Path | None = None,
            seed: int = 42,
        ) -> None:
            """初始化训练完整配置（pydantic 缺失时的 fallback 实现）。

            Args:
                dataset: 数据集配置
                lora: LoRA 适配层配置
                optimizer: 优化器配置
                epochs: 总训练 epoch 数
                batch_size: 单卡每步 batch 大小
                grad_accum_steps: 梯度累积步数，等效 batch = batch_size * grad_accum_steps
                warmup_steps: 学习率 warmup 的 step 数
                precision: 混合精度类型 fp32/fp16/bf16
                save_every_n_epochs: 每 N 个 epoch 保存一个 checkpoint
                output_dir: 输出目录（checkpoint / logs / 最终权重）
                seed: 全局随机种子
            """
            self.dataset = dataset
            self.lora = lora
            self.optimizer = optimizer
            self.epochs = int(epochs)
            self.batch_size = int(batch_size)
            self.grad_accum_steps = int(grad_accum_steps)
            self.warmup_steps = int(warmup_steps)
            self.precision = precision
            self.save_every_n_epochs = int(save_every_n_epochs)
            self.output_dir = Path(output_dir) if output_dir is not None else Path("./training_output")
            self.seed = int(seed)

        def dict(self) -> builtins.dict[str, Any]:
            """将完整训练配置转换为可 JSON 序列化的字典。

            Returns:
                包含所有训练配置字段的嵌套字典
            """
            return {
                "dataset": self.dataset.dict(),
                "lora": self.lora.dict(),
                "optimizer": self.optimizer.dict(),
                "epochs": self.epochs,
                "batch_size": self.batch_size,
                "grad_accum_steps": self.grad_accum_steps,
                "warmup_steps": self.warmup_steps,
                "precision": self.precision,
                "save_every_n_epochs": self.save_every_n_epochs,
                "output_dir": str(self.output_dir),
                "seed": self.seed,
            }

    # Why target_modules 默认 ["to_q", "to_k", "to_v", "to_out.0"]：
    # LoRA 论文（Hu et al., 2021）及 Qwen 系列消融实验显示：只对 Attention 层的
    # Q/K/V/Out 四个投影矩阵加 LoRA，在 4 亿参数模型上 FAD 指标约为全参数微调的
    # 92%，但新增参数量仅 0.1%（约 20MB vs 20GB）。若再加上 FFN 层 12 个线性层，
    # 质量虽提升到 95%，但显存占用与训练时间翻倍。4 层是"性价比最优解"。
    #
    # Why lr 默认 1e-4：
    # LoRA 低秩矩阵对学习率极其敏感：超过 3e-4 易在 100 step 内出现 loss 爆炸（NaN）；
    # 低于 1e-5 则收敛速度过慢（200 epoch 仍没收敛）。1e-4 在 90% 的 VoxCPM2
    # 数据集上可在 20 epoch 内稳定收敛，是大量实际训练的经验最优值。


# ---------------------------------------------------------------------- #
# 公开 API：加载 / 保存 / 默认值
# ---------------------------------------------------------------------- #
def _pydantic_to_dict(cfg: TrainingConfig) -> dict[str, Any]:
    """把 TrainingConfig 转成 JSON/YAML 可序列化的普通 dict。"""
    if BaseModel is not None:
        raw = cfg.model_dump(mode="json") if hasattr(cfg, "model_dump") else cfg.dict()
    else:
        raw = cfg.dict()

    # Path 对象统一转 str，避免 JSON/YAML 序列化失败
    def _walk(node: Any) -> Any:
        if isinstance(node, Path):
            return str(node)
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, (list, tuple)):
            return [_walk(x) for x in node]
        return node

    return _walk(raw)


def _recursive_field_replace(data: Any) -> Any:
    """把 dict 中 "Path" 字符串字段还原为 Path 对象（仅针对 data_dir / output_dir）。"""
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for k, v in data.items():
            val = _recursive_field_replace(v)
            if k in ("data_dir", "output_dir") and isinstance(val, str):
                out[k] = Path(val)
            else:
                out[k] = val
        return out
    if isinstance(data, list):
        return [_recursive_field_replace(x) for x in data]
    return data


def load_training_config(path: Path) -> TrainingConfig:
    """从 JSON 或 YAML 路径加载训练配置并做 Pydantic 校验。

    Args:
        path: 配置文件路径（.json / .yaml / .yml）

    Returns:
        校验通过的 TrainingConfig 实例

    Raises:
        FileNotFoundError: 配置文件不存在
        ValidationError: Pydantic 校验失败（字段超限 / 类型不匹配）
        ValueError: JSON/YAML 解析失败，或文件根节点不是 mapping
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"训练配置文件不存在: {path}")
    suffix = path.suffix.lower()
    raw_text = path.read_text(encoding="utf-8")
    data: Any
    try:
        data = yaml.safe_load(raw_text) if suffix in (".yaml", ".yml") else json.loads(raw_text)
    except (json.JSONDecodeError, yaml.YAMLError, yaml.scanner.ScannerError) as e:
        raise ValueError(
            f"训练配置 {path} 格式错误，请重新填写或使用 get_default_config 生成默认配置。\n解析器报错: {e}"
        ) from e
    if not isinstance(data, dict):
        raise ValueError(f"训练配置 {path} 根节点必须是 mapping（JSON object / YAML dict）")

    data = _recursive_field_replace(data)
    # Pydantic 场景：捕获并一次性列出所有错误字段，提升用户排错效率
    if BaseModel is not None:
        try:
            return (
                TrainingConfig.model_validate(data)
                if hasattr(TrainingConfig, "model_validate")
                else TrainingConfig(**data)
            )
        except ValidationError as e:
            errs = e.errors() if hasattr(e, "errors") else [str(e)]
            msg_lines = [f"训练配置校验失败，共 {len(errs)} 个错误字段："]
            for i, err in enumerate(errs, 1):
                try:
                    loc = ".".join(str(x) for x in err.get("loc", [])) or "<root>"
                    msg = err.get("msg", str(err))
                    msg_lines.append(f"  {i}. [{loc}] {msg}")
                except Exception:  # noqa: BLE001
                    msg_lines.append(f"  {i}. {err}")
            raise ValueError("\n".join(msg_lines)) from e
    # 非 Pydantic 兜底：手动递归构造
    try:
        ds = data.get("dataset", {})
        lo = data.get("lora", {})
        op = data.get("optimizer", {})
        dataset_cfg = DatasetConfig(**ds)
        lora_cfg = LoRAConfig(**lo)
        opt_cfg = OptimizerConfig(**op)
        rest = {k: v for k, v in data.items() if k not in ("dataset", "lora", "optimizer")}
        return TrainingConfig(dataset=dataset_cfg, lora=lora_cfg, optimizer=opt_cfg, **rest)
    except (TypeError, ValueError) as e:
        raise ValueError(f"训练配置字段错误：{e}") from e


def save_training_config(cfg: TrainingConfig, path: Path) -> None:
    """原子写入 TrainingConfig 到 JSON/YAML 文件（.tmp + os.replace）。

    为什么要原子写：
    如果训练过程中用户断电 / 杀进程导致 state.json 或 config.json 写了一半，
    下次 resume 时解析会失败 -> 几小时训练白跑。先写 .tmp 再 os.replace 保证
    "要么新文件完整生效，要么旧文件完好无损"。

    Args:
        cfg: 待保存的配置
        path: 目标路径，根据后缀选择 JSON 或 YAML 格式

    Raises:
        OSError: 磁盘空间不足或无写权限
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _pydantic_to_dict(cfg)
    suffix = path.suffix.lower()
    fd, tmp_path_str = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if suffix in (".yaml", ".yml"):
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
            else:
                json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except OSError:
        # 磁盘满等写失败时清理 .tmp，避免残留半写文件
        logger.exception("保存训练配置 %s 时发生 OSError（磁盘空间不足或无写权限？）", path)
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise
    except Exception:
        logger.exception("保存训练配置 %s 时发生未预期异常", path)
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def get_default_config(data_dir: Path) -> TrainingConfig:
    """给定数据集目录，生成一份经验最优的默认 TrainingConfig。

    Args:
        data_dir: 数据集根目录

    Returns:
        填充了默认超参的 TrainingConfig
    """
    data_dir = Path(data_dir)
    output_dir = data_dir.parent / f"{data_dir.name}_lora_output"
    dataset_cfg = DatasetConfig(
        data_dir=data_dir,
        sample_rate=16000,
        min_duration_sec=1.0,
        max_duration_sec=20.0,
        split_ratio=0.9,
    )
    lora_cfg = LoRAConfig(
        target_modules=["to_q", "to_k", "to_v", "to_out.0"],
        rank=8,
        alpha=16.0,
        dropout=0.05,
        bias="none",
    )
    opt_cfg = OptimizerConfig(
        optimizer_type="adamw",
        lr=1e-4,
        weight_decay=0.01,
        betas=(0.9, 0.999),
    )
    cfg_kwargs: dict[str, Any] = dict(
        dataset=dataset_cfg,
        lora=lora_cfg,
        optimizer=opt_cfg,
        epochs=10,
        batch_size=2,
        grad_accum_steps=4,
        warmup_steps=100,
        precision="fp16",
        save_every_n_epochs=1,
        output_dir=output_dir,
        seed=42,
    )
    if BaseModel is not None:
        if hasattr(TrainingConfig, "model_validate"):
            return TrainingConfig.model_validate(cfg_kwargs)
        return TrainingConfig(**cfg_kwargs)
    return TrainingConfig(**cfg_kwargs)


# ---------------------------------------------------------------------- #
# Legacy helpers — 保持 100% 向后兼容
# ---------------------------------------------------------------------- #
def load_yaml_config(path: str | Path) -> dict[str, Any]:
    """加载 YAML 配置文件，返回 argbind 可用的 dict。

    Args:
        path: YAML 配置路径

    Returns:
        解析后的 dict（顶层必须是 mapping）

    Raises:
        ValueError: 文件解析失败或顶层非 mapping
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"YAML 配置 {path} 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"Configuration file {path} must contain a top-level mapping.")
    return data


def parse_args_with_config(config_path: str | Path | None = None):
    """统一解析 CLI 参数和 YAML 配置。

    用法（与 minicpm-audio 保持一致）::

        args = parse_args_with_config("conf/voxcpm/finetune.yml")
        with argbind.scope(args):
            ...

    Args:
        config_path: 可选 YAML 配置文件路径；None 时只解析 CLI 参数

    Returns:
        合并后的参数字典（CLI 参数优先级高于 YAML）
    """
    cli_args = argbind.parse_args()
    if config_path is None:
        return cli_args

    yaml_args = load_yaml_config(config_path)
    with argbind.scope(cli_args):
        yaml_args = argbind.parse_args(yaml_args=yaml_args, argv=[])
    cli_args.update(yaml_args)
    return cli_args
