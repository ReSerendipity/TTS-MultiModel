"""LoRA 训练编排器（Trainer）—— 将 config/data/accelerator/state/tracker 串联为完整训练流程。

本模块是 training/ 目录的顶层编排入口，负责：
1. 加载 TrainingConfig（从文件或默认值）
2. 创建 train/eval DataLoader
3. 初始化 TrainingAccelerator（设备/精度/分布式/梯度累积）
4. 加载基础模型并注入 LoRA 适配层
5. 执行训练主循环（forward → loss.backward → optimizer.step → 进度追踪）
6. 管理 checkpoint 保存/加载/断点续训
7. 训练结束后导出最终 LoRA 权重（.safetensors + 元数据 JSON）

与现有模块的关系：
- config.py：提供 TrainingConfig / LoRAConfig / OptimizerConfig
- data.py：提供 HFVoxCPMDataset / create_dataloaders
- accelerator.py：提供 TrainingAccelerator
- state.py：提供 TrainingState / StateManager
- tracker.py：提供 TrainingTracker

注意：
- 本模块为训练管线的编排框架，具体模型的 forward/loss 计算由引擎侧提供
  （如 VoxCPM2 的训练循环逻辑）。本模块定义通用接口和流程，引擎侧可继承
  或委托本模块执行。
- 实际训练脚本（scripts/train_voxcpm_finetune.py）当前使用 vendored 版
  voxcpm.training，本模块为现代重构版的统一入口，未来切换训练路径时使用。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("tts_multimodel.training.trainer")


class TrainStepFn(Protocol):
    """单步训练函数协议。

    引擎侧实现此协议，提供具体的 forward/loss/backward 逻辑。

    Args:
        batch: 一个 batch 的训练数据（由 data.py 的 BatchProcessor 处理）
        model: 基础模型（已注入 LoRA）
        optimizer: 优化器
        accelerator: TrainingAccelerator 实例
        global_step: 当前全局步数

    Returns:
        tuple[float, dict[str, float]]: (loss, 额外指标字典)
    """

    def __call__(
        self,
        batch: Any,
        model: Any,
        optimizer: Any,
        accelerator: Any,
        global_step: int,
    ) -> tuple[float, dict[str, float]]: ...


class EvalStepFn(Protocol):
    """单步评估函数协议。

    Args:
        batch: 一个 batch 的评估数据
        model: 基础模型（已注入 LoRA，eval 模式）

    Returns:
        tuple[float, dict[str, float]]: (eval_loss, 额外指标字典)
    """

    def __call__(
        self,
        batch: Any,
        model: Any,
    ) -> tuple[float, dict[str, float]]: ...


@dataclass
class TrainResult:
    """训练结果数据类。

    Attributes:
        success: 是否训练成功完成。
        output_dir: 输出目录路径。
        final_lora_path: 最终导出的 LoRA 权重路径（.safetensors）。
        total_steps: 实际执行的训练步数。
        total_epochs: 实际执行的 epoch 数。
        final_train_loss: 最终训练 loss。
        final_eval_loss: 最终评估 loss（无评估时为 None）。
        best_eval_loss: 最佳评估 loss（早停时记录）。
        early_stopped: 是否因早停而提前终止。
        message: 结果消息。
    """

    success: bool = False
    output_dir: str = ""
    final_lora_path: str = ""
    total_steps: int = 0
    total_epochs: int = 0
    final_train_loss: float = 0.0
    final_eval_loss: float | None = None
    best_eval_loss: float | None = None
    early_stopped: bool = False
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "success": self.success,
            "output_dir": self.output_dir,
            "final_lora_path": self.final_lora_path,
            "total_steps": self.total_steps,
            "total_epochs": self.total_epochs,
            "final_train_loss": self.final_train_loss,
            "final_eval_loss": self.final_eval_loss,
            "best_eval_loss": self.best_eval_loss,
            "early_stopped": self.early_stopped,
            "message": self.message,
        }


class LoRATrainer:
    """LoRA 训练编排器。

    将 training/ 目录的各模块串联为完整的 LoRA 微调训练流程。

    典型用法::

        from app.integrated_app.training.config import get_default_config
        from app.integrated_app.training.trainer import LoRATrainer

        config = get_default_config(data_dir=Path("data/my_dataset"))
        trainer = LoRATrainer(config)

        # 引擎侧提供具体的 train_step / eval_step 函数
        result = trainer.train(
            model=my_model,
            train_step_fn=my_train_step,
            eval_step_fn=my_eval_step,
        )
        print(f"训练完成: {result.final_lora_path}")
    """

    def __init__(self, config: Any) -> None:
        """初始化训练编排器。

        Args:
            config: TrainingConfig 实例（来自 training.config）。
        """
        self.config = config
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 延迟初始化的组件
        self._train_loader: Any = None
        self._eval_loader: Any = None
        self._accelerator: Any = None
        self._state_manager: Any = None
        self._tracker: Any = None
        self._optimizer: Any = None
        self._scheduler: Any = None

        # 训练状态
        self.global_step: int = 0
        self.current_epoch: int = 0
        self.train_loss_history: list[float] = []
        self.eval_loss_history: list[float] = []
        self.best_eval_loss: float | None = None
        self.early_stop_counter: int = 0

    def _setup_dataloaders(self, model: Any) -> None:
        """创建 train/eval DataLoader。

        Args:
            model: 基础模型（用于获取 tokenizer 等预处理组件）。
        """
        from .data import create_dataloaders

        logger.info("[LoRATrainer] 创建数据加载器...")
        self._train_loader, self._eval_loader = create_dataloaders(
            cfg=self.config,
            model=model,
        )
        logger.info(
            "[LoRATrainer] 数据加载器创建完成: train_batches=%d, eval_batches=%d",
            len(self._train_loader) if self._train_loader else 0,
            len(self._eval_loader) if self._eval_loader else 0,
        )

    def _setup_accelerator(self) -> None:
        """初始化训练加速器（设备/精度/分布式/梯度累积）。"""
        from .accelerator import TrainingAccelerator

        logger.info("[LoRATrainer] 初始化训练加速器...")
        self._accelerator = TrainingAccelerator(
            device="auto",
            precision=self.config.precision,
            grad_accum_steps=self.config.grad_accum_steps,
        )
        logger.info(
            "[LoRATrainer] 加速器初始化完成: device=%s, precision=%s", self._accelerator.device, self.config.precision
        )

    def _setup_optimizer(self, model: Any) -> None:
        """创建优化器和学习率调度器。

        Args:
            model: 基础模型（已注入 LoRA，仅训练 LoRA 参数）。
        """
        import torch

        logger.info("[LoRATrainer] 创建优化器...")

        # 仅收集 LoRA 参数（requires_grad=True 的参数）
        lora_params = [p for p in model.parameters() if p.requires_grad]
        total_lora_params = sum(p.numel() for p in lora_params)
        logger.info("[LoRATrainer] LoRA 可训练参数量: %.2fM", total_lora_params / 1e6)

        opt_cfg = self.config.optimizer
        if opt_cfg.optimizer_type == "adamw":
            self._optimizer = torch.optim.AdamW(
                lora_params,
                lr=opt_cfg.lr,
                weight_decay=opt_cfg.weight_decay,
                betas=opt_cfg.betas,
            )
        elif opt_cfg.optimizer_type == "adam":
            self._optimizer = torch.optim.Adam(
                lora_params,
                lr=opt_cfg.lr,
                weight_decay=opt_cfg.weight_decay,
                betas=opt_cfg.betas,
            )
        elif opt_cfg.optimizer_type == "sgd":
            self._optimizer = torch.optim.SGD(
                lora_params,
                lr=opt_cfg.lr,
                weight_decay=opt_cfg.weight_decay,
            )
        else:
            raise ValueError(f"不支持的优化器类型: {opt_cfg.optimizer_type}")

        # 学习率 warmup + cosine decay 调度器
        total_steps = self.config.epochs * len(self._train_loader) // self.config.grad_accum_steps
        self._scheduler = torch.optim.lr_scheduler.OneCycleLR(
            self._optimizer,
            max_lr=opt_cfg.lr,
            total_steps=max(total_steps, 1),
            pct_start=min(self.config.warmup_steps / max(total_steps, 1), 0.3),
            anneal_strategy="cos",
        )
        logger.info(
            "[LoRATrainer] 优化器创建完成: type=%s, lr=%.2e, total_steps=%d",
            opt_cfg.optimizer_type,
            opt_cfg.lr,
            total_steps,
        )

    def _setup_state_and_tracker(self) -> None:
        """初始化状态管理器和进度追踪器。"""
        from .state import StateManager
        from .tracker import TrainingTracker

        logger.info("[LoRATrainer] 初始化状态管理器和进度追踪器...")
        self._state_manager = StateManager(output_dir=self.output_dir)
        self._tracker = TrainingTracker(
            output_dir=self.output_dir,
            total_epochs=self.config.epochs,
        )
        logger.info("[LoRATrainer] 状态管理器和进度追踪器初始化完成")

    def _inject_lora(self, model: Any) -> Any:
        """向基础模型注入 LoRA 适配层。

        优先使用 peft 库（如果已安装），否则回退到引擎侧的 LoRA 注入方法。

        Args:
            model: 基础模型。

        Returns:
            已注入 LoRA 的模型。
        """
        lora_cfg = self.config.lora
        logger.info(
            "[LoRATrainer] 注入 LoRA 适配层: rank=%d, alpha=%.1f, target_modules=%s",
            lora_cfg.rank,
            lora_cfg.alpha,
            lora_cfg.target_modules,
        )

        # 尝试使用 peft 库
        try:
            from peft import LoraConfig, get_peft_model

            peft_config = LoraConfig(
                r=lora_cfg.rank,
                lora_alpha=lora_cfg.alpha,
                lora_dropout=lora_cfg.dropout,
                bias=lora_cfg.bias,
                target_modules=lora_cfg.target_modules,
            )
            model = get_peft_model(model, peft_config)
            model.print_trainable_parameters()
            logger.info("[LoRATrainer] LoRA 注入完成（使用 peft 库）")
            return model
        except ImportError:
            logger.info("[LoRATrainer] peft 库未安装，尝试引擎侧 LoRA 注入方法")

        # 回退：尝试模型自身的 inject_lora 方法
        if hasattr(model, "inject_lora"):
            model = model.inject_lora(
                rank=lora_cfg.rank,
                alpha=lora_cfg.alpha,
                dropout=lora_cfg.dropout,
                target_modules=lora_cfg.target_modules,
            )
            logger.info("[LoRATrainer] LoRA 注入完成（使用模型自身方法）")
            return model

        # 回退：尝试 VoxCPM2 的 LoRA 管理器
        try:
            from ..engines.voxcpm2.lora import LoRAManager

            lora_manager = LoRAManager(model)
            lora_manager.create_lora(
                lora_id="training",
                rank=lora_cfg.rank,
                alpha=lora_cfg.alpha,
                target_modules=lora_cfg.target_modules,
            )
            lora_manager.enable_lora("training")
            logger.info("[LoRATrainer] LoRA 注入完成（使用 VoxCPM2 LoRAManager）")
            return model
        except (ImportError, Exception) as e:
            logger.warning("[LoRATrainer] VoxCPM2 LoRAManager 注入失败: %s", e)

        raise RuntimeError(
            "无法注入 LoRA 适配层。请安装 peft 库（pip install peft），或确保模型具有 inject_lora 方法。"
        )

    def _run_eval(self, model: Any, eval_step_fn: EvalStepFn) -> float:
        """执行评估循环。

        Args:
            model: 模型（eval 模式）。
            eval_step_fn: 单步评估函数。

        Returns:
            平均评估 loss。
        """
        if self._eval_loader is None or len(self._eval_loader) == 0:
            return 0.0

        import torch

        model.eval()
        total_loss = 0.0
        num_batches = 0

        with torch.no_grad():
            for batch in self._eval_loader:
                loss, _ = eval_step_fn(batch, model)
                total_loss += loss
                num_batches += 1

        avg_loss = total_loss / max(num_batches, 1)
        model.train()
        return avg_loss

    def _check_early_stopping(self, eval_loss: float) -> bool:
        """检查是否触发早停。

        Args:
            eval_loss: 当前评估 loss。

        Returns:
            True 表示应触发早停。
        """
        patience = self.config.early_stopping_patience
        if patience <= 0:
            return False

        min_delta = self.config.early_stopping_min_delta

        if self.best_eval_loss is None or eval_loss < self.best_eval_loss - min_delta:
            self.best_eval_loss = eval_loss
            self.early_stop_counter = 0
            return False

        self.early_stop_counter += 1
        if self.early_stop_counter >= patience:
            logger.info("[LoRATrainer] 早停触发: 连续 %d 个 epoch eval loss 无改善", patience)
            return True

        return False

    def _export_final_lora(self, model: Any) -> str:
        """导出最终 LoRA 权重（.safetensors + 元数据 JSON）。

        Args:
            model: 训练完成的模型（含 LoRA 适配层）。

        Returns:
            导出的 LoRA 权重文件路径。
        """
        import json

        import torch

        lora_cfg = self.config.lora
        lora_name = f"lora_rank{lora_cfg.rank}_alpha{int(lora_cfg.alpha)}_steps{self.global_step}"
        safetensors_path = self.output_dir / f"{lora_name}.safetensors"
        metadata_path = self.output_dir / f"{lora_name}.json"

        # 收集 LoRA 状态字典
        lora_state_dict = {}
        for name, param in model.named_parameters():
            if "lora" in name.lower() and param.requires_grad:
                lora_state_dict[name] = param.detach().cpu()

        # 尝试使用 safetensors 保存
        try:
            from safetensors.torch import save_file

            save_file(lora_state_dict, str(safetensors_path))
        except ImportError:
            logger.warning("[LoRATrainer] safetensors 未安装，回退到 torch.save")
            torch.save(lora_state_dict, str(safetensors_path))

        # 保存元数据
        metadata = {
            "name": lora_name,
            "rank": lora_cfg.rank,
            "alpha": lora_cfg.alpha,
            "dropout": lora_cfg.dropout,
            "bias": lora_cfg.bias,
            "target_modules": lora_cfg.target_modules,
            "trained_steps": self.global_step,
            "trained_epochs": self.current_epoch,
            "final_train_loss": self.train_loss_history[-1] if self.train_loss_history else 0.0,
            "final_eval_loss": self.eval_loss_history[-1] if self.eval_loss_history else None,
            "base_model": getattr(model, "model_name", "unknown"),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        logger.info("[LoRATrainer] 最终 LoRA 权重导出完成: %s", safetensors_path)
        return str(safetensors_path)

    def train(
        self,
        model: Any,
        train_step_fn: TrainStepFn,
        eval_step_fn: EvalStepFn | None = None,
        resume_from: str | None = None,
    ) -> TrainResult:
        """执行完整的 LoRA 训练流程。

        Args:
            model: 基础模型（未注入 LoRA，本方法会自动注入）。
            train_step_fn: 单步训练函数（引擎侧实现）。
            eval_step_fn: 单步评估函数（引擎侧实现，None 时跳过评估）。
            resume_from: 断点续训的 checkpoint 路径（None 时从头开始）。

        Returns:
            TrainResult: 训练结果。
        """
        start_time = time.time()
        logger.info("=" * 60)
        logger.info("[LoRATrainer] 开始 LoRA 训练")
        logger.info("=" * 60)

        try:
            # 1. 初始化各组件
            self._setup_accelerator()
            model = self._inject_lora(model)
            self._setup_dataloaders(model)
            self._setup_optimizer(model)
            self._setup_state_and_tracker()

            # 2. 断点续训
            if resume_from:
                logger.info("[LoRATrainer] 从 checkpoint 恢复: %s", resume_from)
                # TODO: 实现 checkpoint 恢复逻辑（state_manager.load）

            # 3. 准备模型和优化器（加速器 prepare）
            model, self._optimizer, self._train_loader, self._scheduler = self._accelerator.prepare(
                model, self._optimizer, self._train_loader, self._scheduler
            )

            # 4. 训练主循环
            model.train()
            early_stopped = False

            for epoch in range(self.config.epochs):
                self.current_epoch = epoch
                logger.info("[LoRATrainer] 开始 Epoch %d/%d", epoch + 1, self.config.epochs)

                epoch_loss = 0.0
                num_batches = 0

                for batch_idx, batch in enumerate(self._train_loader):
                    # 单步训练
                    loss, metrics = train_step_fn(
                        batch=batch,
                        model=model,
                        optimizer=self._optimizer,
                        accelerator=self._accelerator,
                        global_step=self.global_step,
                    )

                    # 梯度累积
                    if (batch_idx + 1) % self.config.grad_accum_steps == 0:
                        self._optimizer.step()
                        self._scheduler.step()
                        self._optimizer.zero_grad()
                        self.global_step += 1

                    epoch_loss += loss
                    num_batches += 1
                    self.train_loss_history.append(loss)

                    # 进度追踪
                    if self._tracker:
                        self._tracker.on_step_end(
                            step=self.global_step,
                            loss=loss,
                            metrics=metrics,
                        )

                avg_epoch_loss = epoch_loss / max(num_batches, 1)
                logger.info("[LoRATrainer] Epoch %d 完成: avg_loss=%.4f", epoch + 1, avg_epoch_loss)

                # 5. 评估
                if eval_step_fn and self._eval_loader:
                    eval_loss = self._run_eval(model, eval_step_fn)
                    self.eval_loss_history.append(eval_loss)
                    logger.info("[LoRATrainer] Eval loss: %.4f", eval_loss)

                    # 早停检查
                    if self._check_early_stopping(eval_loss):
                        early_stopped = True
                        logger.info("[LoRATrainer] 早停触发，终止训练")
                        break

                # 6. 保存 checkpoint
                if (epoch + 1) % self.config.save_every_n_epochs == 0 and self._state_manager:
                    self._state_manager.save(
                        model=model,
                        optimizer=self._optimizer,
                        scheduler=self._scheduler,
                        epoch=epoch,
                        global_step=self.global_step,
                        train_loss=avg_epoch_loss,
                    )

                # Epoch 结束追踪
                if self._tracker:
                    self._tracker.on_epoch_end(
                        epoch=epoch,
                        avg_train_loss=avg_epoch_loss,
                        eval_loss=self.eval_loss_history[-1] if self.eval_loss_history else None,
                    )

            # 7. 训练结束
            if self._tracker:
                self._tracker.on_train_end(
                    total_steps=self.global_step,
                    final_loss=self.train_loss_history[-1] if self.train_loss_history else 0.0,
                )

            # 8. 导出最终 LoRA 权重
            final_lora_path = self._export_final_lora(model)

            elapsed = time.time() - start_time
            result = TrainResult(
                success=True,
                output_dir=str(self.output_dir),
                final_lora_path=final_lora_path,
                total_steps=self.global_step,
                total_epochs=self.current_epoch + 1,
                final_train_loss=self.train_loss_history[-1] if self.train_loss_history else 0.0,
                final_eval_loss=self.eval_loss_history[-1] if self.eval_loss_history else None,
                best_eval_loss=self.best_eval_loss,
                early_stopped=early_stopped,
                message=f"训练完成（耗时 {elapsed / 60:.1f} 分钟，{self.global_step} 步）",
            )

            logger.info("=" * 60)
            logger.info("[LoRATrainer] %s", result.message)
            logger.info("=" * 60)
            return result

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error("[LoRATrainer] 训练失败: %s", e, exc_info=True)
            return TrainResult(
                success=False,
                output_dir=str(self.output_dir),
                total_steps=self.global_step,
                total_epochs=self.current_epoch,
                final_train_loss=self.train_loss_history[-1] if self.train_loss_history else 0.0,
                message=f"训练失败: {e}（耗时 {elapsed / 60:.1f} 分钟）",
            )
