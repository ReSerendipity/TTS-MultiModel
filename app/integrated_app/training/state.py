"""训练状态断点续训与 checkpoint 管理。

# P2 法律注释：本模块仅用于 TTS_MultiModel 平台内的 VoxCPM LoRA 微调。
# 训练产生的 checkpoint 应保留 origin 元数据，便于版权追溯。

training/ 目录对应 WebUI 中 LoRA 微调 Tab 的训练任务；scripts/train_voxcpm_finetune.py
在训练主循环中：
  - 每个 epoch 结束调用 ``StateManager.save`` 写入 ``checkpoint-N/state.json`` +
    ``pytorch_model.bin``（LoRA safetensors）；
  - 训练中断后再次启动，调用 ``StateManager.load_latest`` 找到最新 checkpoint
    并恢复 ``TrainingState``，实现断点续训。

本模块同时保留了旧版 minicpm-audio 风格的"运行时 TrainingState"（持有
generator / optimizer / scheduler / dataloaders / tracker 等对象引用），确保
scripts/train_voxcpm_finetune.py 的既有调用方式 100% 继续可用。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tts_multimodel.training.state")


# ---------------------------------------------------------------------- #
# 持久化 TrainingState：断点续训所需字段（100% 可 JSON 序列化）
# ---------------------------------------------------------------------- #
@dataclass
class TrainingState:
    """训练状态：运行时容器 + 可序列化的断点续训字段。

    为保持 100% 向后兼容，同时包含两类字段：
      - *持久化字段*：run_id、epoch/step 计数器、loss history、best loss
        等，StateManager.save/load 只对这些字段做 JSON 序列化；
      - *运行时字段（legacy）*：generator / optimizer / scheduler /
        train_loader / val_loader / tracker / batch_processor，保存时会被
        过滤掉，不会写入 JSON，但老代码（scripts/train_voxcpm_finetune.py）
        仍可直接读写 ``state.generator`` / ``state.optimizer``。

    Attributes:
        run_id: 本次训练运行的唯一 id（uuid4 hex，用于区分多轮训练）
        current_epoch: 当前已经完成的 epoch 数（从 0 开始，0 表示还没开始训练）
        total_epochs: 总 epoch 数
        current_step: 当前 epoch 内已完成的 step 数
        global_step: 跨 epoch 的累计 step 数
        best_eval_loss: 迄今最佳 eval loss（inf 表示尚未 eval 过）
        best_epoch: 取得 best_eval_loss 的 epoch 索引
        train_loss_history: 每个 step 的 train loss 列表（用于画 loss 曲线）
        eval_loss_history: 每个 epoch 的 eval loss 列表
        last_checkpoint_path: 最近一次保存的 checkpoint 目录路径
        start_time_ts: 训练开始时间戳（time.time()，单位秒）
        generator: LoRA 包装后的生成模型（运行时引用，不序列化）
        optimizer: 优化器（运行时引用，不序列化）
        scheduler: 学习率调度器（运行时引用，不序列化）
        train_loader: 训练集 DataLoader（运行时引用，不序列化）
        val_loader: 验证集 DataLoader（运行时引用，不序列化）
        tracker: TrainingTracker 实例（运行时引用，不序列化）
        batch_processor: BatchProcessor 实例（运行时引用，不序列化）
    """

    # 持久化字段
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    current_epoch: int = 0
    total_epochs: int = 10
    current_step: int = 0
    global_step: int = 0
    best_eval_loss: float = float("inf")
    best_epoch: int = 0
    train_loss_history: list[float] = field(default_factory=list)
    eval_loss_history: list[float] = field(default_factory=list)
    last_checkpoint_path: Path | None = None
    start_time_ts: float = field(default_factory=time.time)

    # 运行时对象引用（不序列化，保证向后兼容）
    generator: Any = None
    optimizer: Any = None
    scheduler: Any = None
    train_loader: Any = None
    val_loader: Any = None
    tracker: Any = None
    batch_processor: Any = None

    # 非序列化字段集合（供 StateManager 过滤）
    PERSISTENT_FIELDS: Any = field(
        default_factory=lambda: {
            "run_id",
            "current_epoch",
            "total_epochs",
            "current_step",
            "global_step",
            "best_eval_loss",
            "best_epoch",
            "train_loss_history",
            "eval_loss_history",
            "last_checkpoint_path",
            "start_time_ts",
        },
        init=False,
        repr=False,
        compare=False,
    )


# Why 保留最近 5 个 checkpoint：
# 每个 checkpoint ≈ 20MB LoRA safetensors + 1MB state JSON；
# 200 epoch 全保留 ≈ 4.2GB 磁盘。保留最近 5 个（约 100MB）既能回滚到
# "best epoch"，也能回退到"最近 4 个 epoch"，超过 5 个自动删最老的。
#
# Why 原子写 .tmp + os.replace：
# 训练进行到 epoch 10 保存 state.json 时，用户断电 / 杀进程会导致
# state.json 写了一半；若非原子写，恢复时 JSONDecodeError -> 几小时
# 训练白跑。先写 .tmp 再 os.replace 保证"要么新 state 完整生效，要么
# 旧 state 完好无损"。


# ---------------------------------------------------------------------- #
# StateManager：checkpoint 的保存 / 加载 / 清理
# ---------------------------------------------------------------------- #
class StateManager:
    """TrainingState 的持久化管理器（checkpoint 目录 + state.json 原子写）。

    Args:
        output_dir: 训练输出目录；checkpoint 会写入
            ``output_dir/checkpoint-{epoch}/`` 子目录中
    """

    CHECKPOINT_PREFIX: str = "checkpoint-"
    STATE_FILE: str = "state.json"
    WEIGHTS_FILE: str = "pytorch_model.bin"
    WEIGHTS_SAFE: str = "adapter_model.safetensors"

    def __init__(self, output_dir: Path) -> None:
        """初始化状态管理器。

        Args:
            output_dir: 训练输出目录；checkpoint 会写入
                ``output_dir/checkpoint-{epoch}/`` 子目录中
        """
        self.output_dir: Path = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _checkpoint_dir(self, epoch: int, suffix: str = "") -> Path:
        """构造指定 epoch 的 checkpoint 目录路径。

        Args:
            epoch: epoch 编号
            suffix: 可选后缀（如 "best" / "latest"）

        Returns:
            checkpoint 目录的完整 Path
        """
        name = f"{self.CHECKPOINT_PREFIX}{epoch}"
        if suffix:
            name = f"{name}_{suffix}"
        return self.output_dir / name

    @staticmethod
    def _parse_epoch(dir_name: str) -> int | None:
        """从 checkpoint-12 / checkpoint-12_best 目录名中解析 epoch 编号。

        Args:
            dir_name: 目录名

        Returns:
            解析出的 epoch 整数，解析失败返回 None
        """
        m = re.match(r"^checkpoint-(\d+)(?:_.*)?$", dir_name)
        if not m:
            return None
        try:
            return int(m.group(1))
        except (TypeError, ValueError):
            return None

    def _list_checkpoint_dirs(self) -> list[tuple[int, Path]]:
        """列出所有 checkpoint 目录。

        Returns:
            (epoch, path) 元组列表，按 epoch 升序排列
        """
        if not self.output_dir.exists():
            return []
        result: list[tuple[int, Path]] = []
        for entry in self.output_dir.iterdir():
            if not entry.is_dir():
                continue
            epoch = self._parse_epoch(entry.name)
            if epoch is None:
                continue
            result.append((epoch, entry))
        result.sort(key=lambda x: x[0])
        return result

    def _state_to_dict(self, state: TrainingState) -> dict[str, Any]:
        """将 TrainingState 的持久化字段转换为可 JSON 序列化的字典。

        Args:
            state: 训练状态实例

        Returns:
            只包含持久化字段的字典，Path 对象自动转为 str
        """
        raw = asdict(state)
        keep: dict[str, Any] = {}
        for k, v in raw.items():
            if k in TrainingState.PERSISTENT_FIELDS:
                keep[k] = str(v) if isinstance(v, Path) else v
        return keep

    def _dict_to_state(self, data: dict[str, Any]) -> TrainingState:
        """从字典还原 TrainingState（自动兼容缺失字段）。

        Args:
            data: 状态字典（从 JSON 加载）

        Returns:
            还原后的 TrainingState 实例
        """
        allowed = set(TrainingState.PERSISTENT_FIELDS)
        kwargs: dict[str, Any] = {}
        for k, v in data.items():
            if k not in allowed:
                continue
            if k == "last_checkpoint_path" and isinstance(v, str):
                kwargs[k] = Path(v)
            else:
                kwargs[k] = v
        return TrainingState(**kwargs)

    # ------------------------------------------------------------------ #
    # 原子写：state.json + safetensors
    # ------------------------------------------------------------------ #
    def save(
        self,
        state: TrainingState,
        suffix: str = "",
        model_state_dict_fn: Callable[[], dict[str, Any]] | None = None,
    ) -> Path:
        """保存一次 checkpoint：state.json（原子写） + LoRA safetensors / bin。

        Args:
            state: 当前 TrainingState
            suffix: 可选后缀（如 "best" / "latest"），会拼到 checkpoint-xxx_yyy
            model_state_dict_fn: 可选回调，返回需要额外保存的 state dict
                （LoRA 低秩权重）；若为 None 则只存 state.json

        Returns:
            本次写入的 checkpoint 目录 Path

        Raises:
            OSError: 磁盘空间不足且"激进清理 + 重试"后仍然写失败
        """
        epoch = max(0, int(state.current_epoch))
        ckpt_dir = self._checkpoint_dir(epoch, suffix=suffix)
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        state.last_checkpoint_path = ckpt_dir

        state_data = self._state_to_dict(state)
        state_path = ckpt_dir / self.STATE_FILE

        # ---- state.json：.tmp + os.replace 原子写 ----
        fd, tmp_state_str = tempfile.mkstemp(prefix=self.STATE_FILE + ".", suffix=".tmp", dir=str(ckpt_dir))
        tmp_state = Path(tmp_state_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state_data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_state, state_path)
        except OSError as e:
            self._cleanup_tmp(tmp_state)
            logger.exception("保存 state.json 首次失败，尝试激进清理后重试: %s", e)
            # 激进清理 + 再试一次（可能因为磁盘满）
            try:
                self.clean_old_keep_last_n(n=2)
                fd2, tmp_state2_str = tempfile.mkstemp(prefix=self.STATE_FILE + ".", suffix=".tmp", dir=str(ckpt_dir))
                tmp_state2 = Path(tmp_state2_str)
                try:
                    with os.fdopen(fd2, "w", encoding="utf-8") as f:
                        json.dump(state_data, f, ensure_ascii=False, indent=2)
                    os.replace(tmp_state2, state_path)
                except OSError as retry_exc:
                    self._cleanup_tmp(tmp_state2)
                    logger.exception("保存 state.json 重试仍失败（磁盘空间不足？）: %s", retry_exc)
                    raise OSError(
                        f"保存 state.json 失败（磁盘空间不足？请清理磁盘后重试）： {retry_exc}"
                    ) from retry_exc
            except OSError as outer_exc:
                logger.exception("保存训练状态最终失败: %s", outer_exc)
                raise OSError(f"保存训练状态失败（磁盘空间不足？请清理磁盘后重试）： {outer_exc}") from outer_exc
        except Exception as e:
            self._cleanup_tmp(tmp_state)
            logger.exception("保存 state.json 时未预期异常: %s", e)
            raise

        # ---- LoRA weights：同样 .tmp + replace 原子写 ----
        if model_state_dict_fn is not None:
            try:
                weights = model_state_dict_fn()
            except Exception as exc:  # noqa: BLE001
                logger.warning("model_state_dict_fn 回调异常，跳过权重保存: %s", exc)
                weights = None
            if weights:
                try:
                    self._save_weights_atomic(ckpt_dir, weights)
                except OSError as exc:
                    logger.exception("保存 LoRA 权重首次失败，尝试激进清理后重试: %s", exc)
                    # 权重写磁盘满：激进清理 + 再试一次
                    try:
                        self.clean_old_keep_last_n(n=2)
                        self._save_weights_atomic(ckpt_dir, weights)
                    except OSError as retry_exc:
                        # 把刚刚写入成功的 state.json 保留（下次还能 resume），
                        # 仅报错提示用户补权重
                        logger.exception(
                            "LoRA 权重重试写入仍失败（磁盘不足？），state.json 已保留，"
                            "请清理磁盘后重新训练或手动补充权重：%s",
                            retry_exc,
                        )
                        raise OSError(f"保存 LoRA 权重失败，磁盘空间不足：{retry_exc}") from retry_exc
        return ckpt_dir

    def _save_weights_atomic(self, ckpt_dir: Path, weights: dict[str, Any]) -> None:
        """原子写入 LoRA 权重文件（优先 safetensors，次选 torch .bin）。

        全程使用 .tmp 临时文件 + os.replace 保证原子性，避免写入中断导致文件损坏。

        Args:
            ckpt_dir: checkpoint 目录路径
            weights: 模型 state dict（LoRA 低秩权重）

        Raises:
            OSError: 磁盘空间不足或无写权限
        """
        # 1) safetensors
        try:
            from safetensors.torch import save_file  # type: ignore

            safe_path = ckpt_dir / self.WEIGHTS_SAFE
            fd, tmp_s = tempfile.mkstemp(prefix=self.WEIGHTS_SAFE + ".", suffix=".tmp", dir=str(ckpt_dir))
            tmp_safe = Path(tmp_s)
            os.close(fd)  # safetensors 自己开文件
            try:
                save_file(weights, str(tmp_safe))
                os.replace(tmp_safe, safe_path)
                return
            except OSError:
                self._cleanup_tmp(tmp_safe)
                raise
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("safetensors 保存异常，fallback 到 torch.save: %s", exc)
        # 2) fallback torch.bin
        bin_path = ckpt_dir / self.WEIGHTS_FILE
        fd, tmp_b = tempfile.mkstemp(prefix=self.WEIGHTS_FILE + ".", suffix=".tmp", dir=str(ckpt_dir))
        tmp_bin = Path(tmp_b)
        os.close(fd)
        try:
            import torch as _torch

            _torch.save(weights, str(tmp_bin))
            os.replace(tmp_bin, bin_path)
        except OSError:
            self._cleanup_tmp(tmp_bin)
            raise

    @staticmethod
    def _cleanup_tmp(path: Path) -> None:
        """清理临时文件（best-effort，失败静默）。

        Args:
            path: 待删除的临时文件路径
        """
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    # 加载：自动尝试最新 checkpoint，损坏时回退 N-1 / N-2 ... 最多 5 次
    # ------------------------------------------------------------------ #
    def load_latest(self) -> TrainingState | None:
        """找到最新（epoch 最大）的 checkpoint，返回还原后的 TrainingState。

        若最新 checkpoint 的 state.json 损坏，会自动回退到倒数第二、第三……
        最多尝试 5 个；全部失败则返回 None。

        Returns:
            还原后的 TrainingState，或 None（没有 checkpoint / 全部损坏）
        """
        ckpts = self._list_checkpoint_dirs()
        if not ckpts:
            return None
        # 从新到旧，最多尝试 5 个
        recent = list(reversed(ckpts))[:5]
        last_error: Exception | None = None
        for _epoch, ckpt_dir in recent:
            state_path = ckpt_dir / self.STATE_FILE
            if not state_path.exists():
                logger.debug("checkpoint %s 缺少 state.json，跳过", ckpt_dir.name)
                continue
            try:
                raw_text = state_path.read_text(encoding="utf-8")
                data = json.loads(raw_text)
            except (OSError, json.JSONDecodeError) as exc:
                logger.error("checkpoint %s 损坏（%s），尝试前一个 checkpoint", ckpt_dir, exc)
                last_error = exc
                continue
            try:
                state = self._dict_to_state(data)
            except (TypeError, ValueError) as exc:
                logger.error(
                    "checkpoint %s 字段解析失败（%s），尝试前一个 checkpoint",
                    ckpt_dir,
                    exc,
                )
                last_error = exc
                continue
            # 保证 last_checkpoint_path 指向真实目录
            state.last_checkpoint_path = ckpt_dir
            logger.info(
                "断点续训：加载 checkpoint %s (epoch=%d, best_eval_loss=%.6f)",
                ckpt_dir.name,
                state.current_epoch,
                state.best_eval_loss,
            )
            return state
        if last_error is not None:
            logger.error("所有尝试的 checkpoint 都失败，最后错误: %s", last_error)
        return None

    def load_weights(self, state: TrainingState) -> dict[str, Any] | None:
        """根据 state.last_checkpoint_path 加载 LoRA 权重（safetensors 优先）。

        Args:
            state: 已通过 load_latest 还原的 TrainingState

        Returns:
            state dict；无对应权重文件则返回 None
        """
        if state.last_checkpoint_path is None:
            return None
        ckpt_dir = Path(state.last_checkpoint_path)
        safe_path = ckpt_dir / self.WEIGHTS_SAFE
        if safe_path.exists():
            try:
                from safetensors.torch import load_file  # type: ignore

                return load_file(str(safe_path))
            except Exception as exc:  # noqa: BLE001
                logger.warning("safetensors 加载失败，尝试 .bin: %s", exc)
        bin_path = ckpt_dir / self.WEIGHTS_FILE
        if bin_path.exists():
            try:
                import torch as _torch

                return _torch.load(str(bin_path), map_location="cpu", weights_only=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("加载权重失败: %s", exc)
        return None

    # ------------------------------------------------------------------ #
    # 清理：保留最近 N 个
    # ------------------------------------------------------------------ #
    def clean_old_keep_last_n(self, n: int = 5) -> None:
        """只保留最新的 n 个 checkpoint 目录，其余最老的递归删除。

        Args:
            n: 保留数量（默认 5），<=0 时强制使用 1 防止全部删光
        """
        n = max(1, int(n))
        ckpts = self._list_checkpoint_dirs()
        if len(ckpts) <= n:
            return
        to_remove = ckpts[: len(ckpts) - n]
        for _epoch, ckpt_dir in to_remove:
            try:
                if ckpt_dir.is_dir():
                    shutil.rmtree(ckpt_dir, ignore_errors=True)
                    logger.info("已清理旧 checkpoint: %s", ckpt_dir)
            except Exception as exc:  # noqa: BLE001
                logger.debug("清理 checkpoint %s 异常（忽略）: %s", ckpt_dir, exc)


# ---------------------------------------------------------------------- #
# 额外导出：用于 data.py / packers.py 的类型回溯（防止循环 import）
# ---------------------------------------------------------------------- #
from typing import TYPE_CHECKING  # noqa: E402

if TYPE_CHECKING:
    pass

__all__ = ["TrainingState", "StateManager"]
