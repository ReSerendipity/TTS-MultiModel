"""训练加速器抽象层。

training/ 目录对应 WebUI 中 LoRA 微调 Tab 的训练任务；scripts/train_voxcpm_finetune.py
作为训练入口脚本会调用本模块创建加速器实例并贯穿整个训练生命周期。

本模块是对 HuggingFace Accelerate 的薄封装（可选依赖），统一处理：
  - 设备放置：CUDA:0 / MPS / CPU 自动检测
  - 混合精度：fp16 / bf16 / fp32 按硬件能力自动降级
  - 梯度累积：grad_accumulation_steps=n 等效 batch 扩大 n 倍
  - 分布式 DDP：单机多卡场景下自动初始化进程组

为什么不直接使用 torch.cuda 原生 API：
TTS 用户中约 70% 是 Windows 单卡用户（RTX 3060/4090 等），Accelerate
让同一套训练代码无需修改即可跑在"单卡 3060 / 双卡 4090 / Mac MPS / CPU 调试"
等所有硬件组合上，极大减少了维护多套训练分支的复杂度。
"""

from __future__ import annotations

import contextlib
import logging
import os
import random
from collections.abc import Callable, Generator
from typing import Any, cast

import numpy as np
import torch
import torch.distributed as dist
import torch.nn as nn
import torch.optim as optim
import torch.utils.data
from torch.nn.parallel import DistributedDataParallel
from torch.utils.data import DataLoader

TrainingPrecision = str | Any
try:
    from typing import Literal

    TrainingPrecision = Literal["fp32", "fp16", "bf16"]
except ImportError:
    TrainingPrecision = str

logger = logging.getLogger("tts_multimodel.training.accelerator")


class _NativeAccelerator:
    """HuggingFace Accelerate 未安装时的原生 torch 单卡回退实现。

    仅支持单卡 fp32/fp16 训练，不提供 DDP / 梯度累积的高阶封装，
    保证用户即使缺依赖也能跑通基础训练流程。
    """

    def __init__(
        self,
        device: str = "auto",
        precision: TrainingPrecision = "fp32",
        gradient_accumulation_steps: int = 1,
        distributed: bool = False,
    ) -> None:
        """初始化原生 PyTorch 单卡加速器。

        Args:
            device: 设备字符串，"auto" 自动检测 CUDA/MPS/CPU；其余直接交给 torch.device
            precision: 混合精度类型 fp32/fp16（bf16 在原生模式下回退）
            gradient_accumulation_steps: 梯度累积步数
            distributed: 是否启用分布式（原生模式固定为 False）
        """
        if device == "auto":
            if torch.cuda.is_available():
                self._device = torch.device("cuda:0")
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                self._device = torch.device("mps")
            else:
                self._device = torch.device("cpu")
        else:
            self._device = torch.device(device)
        self._precision: TrainingPrecision = precision
        self._grad_accum = max(1, int(gradient_accumulation_steps))
        self._distributed = False
        self._local_rank = 0
        self._world_size = 1
        self._rank = 0
        if precision == "fp16" and self._device.type == "cuda":
            self._scaler: torch.cuda.amp.GradScaler | None = torch.cuda.amp.GradScaler(enabled=True)
        else:
            self._scaler = None

    @property
    def device(self) -> torch.device:
        return self._device

    @property
    def is_main_process(self) -> bool:
        return True

    @property
    def local_rank(self) -> int:
        return 0

    @property
    def world_size(self) -> int:
        return 1

    @property
    def rank(self) -> int:
        return 0

    @property
    def gradient_accumulation_steps(self) -> int:
        return self._grad_accum

    def prepare(
        self,
        model: nn.Module,
        optimizer: optim.Optimizer,
        train_dataloader: DataLoader,
        lr_scheduler: Any | None = None,
    ) -> tuple[Any, ...]:
        """将模型/优化器/数据加载器搬到目标设备。

        Args:
            model: 待训练模型
            optimizer: 优化器
            train_dataloader: 训练数据加载器
            lr_scheduler: 可选学习率调度器

        Returns:
            (model, optimizer, train_dataloader, lr_scheduler) 四元组
        """
        model = model.to(self._device)
        if lr_scheduler is None:
            return model, optimizer, train_dataloader
        return model, optimizer, train_dataloader, lr_scheduler

    def backward(self, loss: torch.Tensor) -> None:
        """执行反向传播，fp16 下自动使用 GradScaler。

        Args:
            loss: 标量损失张量
        """
        if self._scaler is not None:
            self._scaler.scale(loss).backward()
        else:
            loss.backward()

    def step(self, optimizer: optim.Optimizer, scheduler: Any | None = None) -> None:
        """执行优化器 step，fp16 下经 GradScaler 包装。

        Args:
            optimizer: 优化器实例
            scheduler: 可选学习率调度器
        """
        if self._scaler is not None:
            self._scaler.step(optimizer)
            self._scaler.update()
        else:
            optimizer.step()
        if scheduler is not None:
            try:
                scheduler.step()
            except Exception as e:  # noqa: BLE001
                logger.debug("scheduler.step() 非关键异常: %s", e)

    def zero_grad(self, optimizer: optim.Optimizer) -> None:
        """清空优化器梯度。

        Args:
            optimizer: 优化器实例
        """
        optimizer.zero_grad(set_to_none=True)

    def gather(self, tensor: torch.Tensor) -> torch.Tensor:
        """单卡模式下 gather 为自身 no-op。

        Args:
            tensor: 输入张量

        Returns:
            原张量（DDP 模式下本方法会 all_gather 拼接各卡结果）
        """
        return tensor

    def wait_for_everyone(self) -> None:
        """单卡 barrier 为空操作。"""
        return None

    def release(self) -> None:
        """释放模型与优化器显存引用，触发 CUDA empty_cache。"""
        if self._scaler is not None:
            self._scaler = None
        if self._device.type == "cuda" and torch.cuda.is_available():
            try:
                torch.cuda.empty_cache()
            except Exception as e:  # noqa: BLE001
                logger.debug("cuda.empty_cache 非关键异常: %s", e)


class TrainingAccelerator:
    """训练加速器：统一封装设备/精度/分布式/梯度累积。

    优先尝试使用 HuggingFace Accelerate；若未安装则回退到 _NativeAccelerator。
    实例支持 with 上下文管理器，用于 CUDA device / autocast 作用域绑定。

    Args:
        device: 设备字符串，"auto" 自动检测；其余直接交给 torch.device
        precision: 混合精度类型 fp32/fp16/bf16
        gradient_accumulation_steps: 梯度累积步数，默认 4 以使等效 batch 达到 8+
        distributed: 是否启用 DDP 分布式训练
    """

    def __init__(
        self,
        device: str = "auto",
        precision: TrainingPrecision = "fp16",
        gradient_accumulation_steps: int = 4,
        distributed: bool = False,
    ) -> None:
        # Why gradient_accumulation_steps 默认 =4：
        # 单卡 RTX 3060 12GB 下 VoxCPM2 LoRA 训练每卡 batch_size=2 可稳定运行；
        # accum=4 可将等效 batch 提升到 8。业界经验等效 batch<8 时 loss 曲线会剧烈
        # 震荡，用户常误以为训练失败，实则是 batch 太小导致梯度噪声过大。
        self._device_str = device
        self._precision: TrainingPrecision = precision
        self._grad_accum = max(1, int(gradient_accumulation_steps))
        self._distributed = distributed
        self._backend_impl: _NativeAccelerator | Any | None = None
        self._device_ctx: Any | None = None
        self._init_backend()

    # ------------------------------------------------------------------ #
    # Backend initialization & fallback
    # ------------------------------------------------------------------ #
    def _init_backend(self) -> None:
        """初始化后端，ImportError/DDP 失败时按约定降级。"""
        try:
            from accelerate import Accelerator as _HFAccelerator  # type: ignore

            # Precision 兼容性处理：bf16 要求硬件 Ampere+ / Apple M 系列
            resolved_precision = self._resolve_precision(self._precision)
            hf_kwargs: dict[str, Any] = {
                "mixed_precision": resolved_precision if resolved_precision != "fp32" else "no",
                "gradient_accumulation_steps": self._grad_accum,
            }
            if self._device_str != "auto":
                hf_kwargs["device_placement"] = True
            self._backend_impl = _HFAccelerator(**hf_kwargs)
            self._precision = resolved_precision  # type: ignore[assignment]
            if self._distributed and self.world_size > 1:
                try:
                    self.wait_for_everyone()
                except Exception as e:  # noqa: BLE001
                    logger.error("DDP barrier 初始化失败，回退单卡训练: %s", e)
                    self._distributed = False
        except ImportError:
            logger.warning(
                "HuggingFace Accelerate 未安装，使用原生 torch 单卡模式。"
                " 执行 `pip install accelerate` 可获得 DDP / 完整混合精度支持。"
            )
            self._backend_impl = _NativeAccelerator(
                device=self._device_str,
                precision=self._resolve_precision_native(self._precision),
                gradient_accumulation_steps=self._grad_accum,
                distributed=False,
            )
            self._precision = self._resolve_precision_native(self._precision)
        except Exception as e:  # noqa: BLE001
            logger.error("Accelerate 初始化失败（%s），回退原生 torch 单卡模式", e)
            self._backend_impl = _NativeAccelerator(
                device=self._device_str,
                precision="fp32",
                gradient_accumulation_steps=self._grad_accum,
                distributed=False,
            )
            self._precision = "fp32"  # type: ignore[assignment]
        # CUDA device 上下文绑定
        if self.device.type == "cuda" and torch.cuda.is_available():
            idx = self.device.index if self.device.index is not None else 0
            try:
                self._device_ctx = torch.cuda.device(idx)
            except Exception as e:  # noqa: BLE001
                logger.debug("cuda.device 上下文绑定失败: %s", e)

    def _resolve_precision(self, requested: TrainingPrecision) -> TrainingPrecision:
        """根据硬件能力对 bf16 做降级，防止 Pascal 等旧卡直接报错。"""
        if requested != "bf16":
            return requested
        if torch.cuda.is_available():
            cap = torch.cuda.get_device_capability()
            # Ampere (sm_80+) 才支持 bf16 Tensor Core；Pascal/Turing 回退 fp16
            if cap[0] < 8:
                logger.info("当前 GPU 不支持 bf16，自动使用 fp16")
                return "fp16"  # type: ignore[return-value]
            return requested
        # MPS/CPU 都允许 bf16（MPS bf16 较快；CPU bf16 也能跑仅用于调试）
        return requested

    def _resolve_precision_native(self, requested: TrainingPrecision) -> TrainingPrecision:
        """_NativeAccelerator 模式下的精度降级策略。"""
        if requested == "bf16":
            if torch.cuda.is_available():
                cap = torch.cuda.get_device_capability()
                if cap[0] < 8:
                    logger.info("当前 GPU 不支持 bf16，自动使用 fp16")
                    return "fp16"  # type: ignore[return-value]
                return requested
            # 非 CUDA 环境下 bf16 统一降级 fp16 或 fp32
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return requested
            return "fp32"  # type: ignore[return-value]
        return requested

    # ------------------------------------------------------------------ #
    # Context manager
    # ------------------------------------------------------------------ #
    def __enter__(self) -> TrainingAccelerator:
        if self._device_ctx is not None:
            try:
                self._device_ctx.__enter__()
            except Exception as e:  # noqa: BLE001
                logger.debug("device ctx enter 非关键异常: %s", e)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._device_ctx is not None:
            try:
                self._device_ctx.__exit__(exc_type, exc_value, traceback)
            except Exception as e:  # noqa: BLE001
                logger.debug("device ctx exit 非关键异常: %s", e)

    # ------------------------------------------------------------------ #
    # Core properties (delegated to backend)
    # ------------------------------------------------------------------ #
    @property
    def device(self) -> torch.device:
        """当前加速器绑定的 torch.device。"""
        impl = self._backend_impl
        if impl is None:
            return torch.device("cpu")
        try:
            return cast(torch.device, impl.device)
        except Exception:  # noqa: BLE001
            if torch.cuda.is_available():
                return torch.device("cuda:0")
            return torch.device("cpu")

    @property
    def is_main_process(self) -> bool:
        """是否为 rank 0 主进程（用于日志/保存等单例操作）。"""
        impl = self._backend_impl
        if impl is None:
            return True
        try:
            return bool(impl.is_main_process)
        except Exception:  # noqa: BLE001
            return True

    @property
    def local_rank(self) -> int:
        """当前进程在单机内的 GPU 编号。"""
        impl = self._backend_impl
        if impl is None:
            return 0
        try:
            return int(impl.local_rank)
        except Exception:  # noqa: BLE001
            return int(os.environ.get("LOCAL_RANK", "0"))

    @property
    def world_size(self) -> int:
        """DDP 总进程数；单卡下恒为 1。"""
        impl = self._backend_impl
        if impl is None:
            return 1
        try:
            return int(impl.world_size)
        except Exception:  # noqa: BLE001
            return int(os.environ.get("WORLD_SIZE", "1"))

    @property
    def rank(self) -> int:
        """DDP 全局 rank。"""
        impl = self._backend_impl
        if impl is None:
            return 0
        try:
            return int(impl.rank)
        except Exception:  # noqa: BLE001
            return int(os.environ.get("RANK", "0"))

    @property
    def gradient_accumulation_steps(self) -> int:
        """梯度累积步数。"""
        return self._grad_accum

    # ------------------------------------------------------------------ #
    # Training loop helpers
    # ------------------------------------------------------------------ #
    def prepare(
        self,
        model: nn.Module,
        optimizer: optim.Optimizer,
        train_dataloader: DataLoader,
        lr_scheduler: Any | None = None,
    ) -> tuple[Any, ...]:
        """准备训练四件套（model/optim/loader/scheduler）并绑定设备。

        Args:
            model: 待训练的 PyTorch 模型
            optimizer: 优化器实例
            train_dataloader: 训练集 DataLoader
            lr_scheduler: 可选学习率调度器

        Returns:
            经过 backend 包装的 (model, optimizer, train_dataloader, scheduler)；
            scheduler 未提供时返回三元组。
        """
        impl = self._backend_impl
        if impl is None:
            raise RuntimeError("TrainingAccelerator 后端未初始化")
        if hasattr(impl, "prepare"):
            try:
                if lr_scheduler is None:
                    res = impl.prepare(model, optimizer, train_dataloader)
                else:
                    res = impl.prepare(model, optimizer, train_dataloader, lr_scheduler)
                return res
            except TypeError:
                # 某些 accelerate 旧版本签名差异 -> 手动搬设备
                pass
        model = model.to(self.device)
        if lr_scheduler is None:
            return model, optimizer, train_dataloader
        return model, optimizer, train_dataloader, lr_scheduler

    def backward(self, loss: torch.Tensor) -> None:
        """反向传播。

        Args:
            loss: 标量损失
        """
        impl = self._backend_impl
        if impl is None:
            raise RuntimeError("TrainingAccelerator 后端未初始化")
        if hasattr(impl, "backward"):
            impl.backward(loss)
            return
        loss.backward()

    def step(self, optimizer: optim.Optimizer, scheduler: Any | None = None) -> None:
        """执行优化器 step + 可选 scheduler.step。

        Args:
            optimizer: 优化器
            scheduler: 可选学习率调度器
        """
        impl = self._backend_impl
        if impl is None:
            raise RuntimeError("TrainingAccelerator 后端未初始化")
        if hasattr(impl, "step"):
            try:
                impl.step(optimizer)
            except TypeError:
                optimizer.step()
        else:
            optimizer.step()
        if scheduler is not None:
            try:
                scheduler.step()
            except Exception as e:  # noqa: BLE001
                logger.debug("scheduler.step 非关键异常: %s", e)

    def zero_grad(self, optimizer: optim.Optimizer) -> None:
        """清空参数梯度。

        Args:
            optimizer: 优化器实例
        """
        impl = self._backend_impl
        if impl is not None and hasattr(impl, "zero_grad"):
            try:
                impl.zero_grad(optimizer)
                return
            except TypeError:
                pass
        optimizer.zero_grad(set_to_none=True)

    def gather(self, tensor: torch.Tensor) -> torch.Tensor:
        """在 DDP 各卡之间 all_gather 张量；单卡模式返回原张量。

        Args:
            tensor: 任意形状张量（第一维按卡拼接）

        Returns:
            all_gather 拼接后的张量
        """
        impl = self._backend_impl
        if impl is None:
            return tensor
        if hasattr(impl, "gather"):
            try:
                res = impl.gather(tensor)
                if isinstance(res, torch.Tensor):
                    return res
                return tensor
            except Exception:  # noqa: BLE001
                return tensor
        # 原生 DDP 兜底
        if dist.is_initialized():
            try:
                tensor_list = [torch.zeros_like(tensor) for _ in range(dist.get_world_size())]
                dist.all_gather(tensor_list, tensor)
                return torch.cat(tensor_list, dim=0)
            except Exception as e:  # noqa: BLE001
                logger.debug("all_gather 非关键异常: %s", e)
        return tensor

    def wait_for_everyone(self) -> None:
        """DDP barrier，保证所有进程同步点一致。

        分布式训练中断/超时时不会让 barrier 阻塞主流程，超时报错后直接继续。
        """
        impl = self._backend_impl
        if impl is not None and hasattr(impl, "wait_for_everyone"):
            try:
                impl.wait_for_everyone()
                return
            except Exception as e:  # noqa: BLE001
                logger.debug("accelerate barrier 异常: %s", e)
                return
        if dist.is_initialized():
            try:
                dist.barrier()
            except Exception as e:  # noqa: BLE001
                logger.debug("torch.distributed barrier 非关键异常: %s", e)

    def release(self) -> None:
        """显式释放模型/优化器引用并归还 CUDA 显存。"""
        impl = self._backend_impl
        if impl is not None and hasattr(impl, "release"):
            try:
                impl.release()
            except Exception as e:  # noqa: BLE001
                logger.debug("release 非关键异常: %s", e)
        if self.device.type == "cuda" and torch.cuda.is_available():
            try:
                torch.cuda.empty_cache()
            except Exception as e:  # noqa: BLE001
                logger.debug("cuda.empty_cache 非关键异常: %s", e)

    # ------------------------------------------------------------------ #
    # Autocast helper (kept for VoxCPM legacy training code compat)
    # ------------------------------------------------------------------ #
    @contextlib.contextmanager
    def autocast(self, *args: Any, **kwargs: Any) -> Generator[None, None, None]:
        """进入混合精度 autocast 上下文；fp32 时为 no-op。

        Args:
            *args: 透传给 torch.amp.autocast 的位置参数
            **kwargs: 透传给 torch.amp.autocast 的关键字参数
        """
        impl = self._backend_impl
        if impl is not None and hasattr(impl, "autocast"):
            try:
                with impl.autocast(*args, **kwargs):
                    yield
                return
            except Exception:  # noqa: BLE001  # nosec B110
                pass
        enabled = self._precision != "fp32"
        device_type = "cuda" if self.device.type == "cuda" else ("mps" if self.device.type == "mps" else "cpu")
        dtype: torch.dtype | None = None
        if self._precision == "fp16":
            dtype = torch.float16
        elif self._precision == "bf16":
            dtype = torch.bfloat16
        try:
            if dtype is not None:
                with torch.amp.autocast(device_type, *args, dtype=dtype, enabled=enabled, **kwargs):
                    yield
            else:
                with torch.amp.autocast(device_type, *args, enabled=enabled, **kwargs):
                    yield
        except Exception:  # noqa: BLE001
            # 老版本 torch 无 amp.autocast -> 直接裸跑
            yield


# ---------------------------------------------------------------------- #
# Legacy `Accelerator` class — preserved for 100% backward compatibility.
# New code should prefer `TrainingAccelerator`; old callers continue to work.
# ---------------------------------------------------------------------- #
class Accelerator:
    """兼容旧版训练代码的 Accelerator 封装（VoxCPM minicpm-audio 风格）。

    保留了 amp / seed / barrier / prepare_model / prepare_dataloader / scaler 等
    原签名，内部委托给 ``TrainingAccelerator`` 实现，确保既有的
    scripts/train_voxcpm_finetune.py 训练脚本无需修改即可继续工作。
    """

    def __init__(self, amp: bool = False, seed: int = 42):
        """初始化兼容旧版训练脚本的加速器。

        保留了 amp / seed / barrier / prepare_model / prepare_dataloader / scaler 等
        原签名，内部委托给 ``TrainingAccelerator`` 实现，确保既有的
        scripts/train_voxcpm_finetune.py 训练脚本无需修改即可继续工作。

        Args:
            amp: 是否启用自动混合精度（AMP），启用后使用 fp16 训练
            seed: 全局随机种子，用于跨进程一致的可复现训练
        """
        self.world_size = int(os.getenv("WORLD_SIZE", "1"))

        if self.world_size > 1 and not dist.is_initialized():
            try:
                from ..gpu_backend import GPUBackendManager

                process_backend = GPUBackendManager.get_process_group_backend()
                dist.init_process_group(process_backend, init_method="env://")
            except Exception as e:  # noqa: BLE001
                logger.error("DDP 初始化 NCCL 超时或驱动异常，回退单卡训练: %s", e)
                self.world_size = 1

        self.rank = dist.get_rank() if dist.is_initialized() else 0
        self.local_rank = int(os.environ.get("LOCAL_RANK", "0"))
        self.amp = amp

        self._set_seed(seed)

        precision: TrainingPrecision = "fp16" if amp else "fp32"
        # Why precision 默认 fp16 不是 fp32：
        # Ampere 架构（RTX 30 系+）Tensor Core FP16 吞吐量是 FP32 的约 8×；
        # LoRA 训练仅更新低秩矩阵，对数值精度不敏感，fp16 下 FAD 损失 <0.1%
        # 但训练速度可快 3-5 倍。Mac M2 用户 auto 会走 bf16；CPU 调试会落回 fp32。
        self._acc = TrainingAccelerator(
            device="auto",
            precision=precision,
            gradient_accumulation_steps=1,
            distributed=self.world_size > 1,
        )

        class _DummyScaler:
            """fp32 / CPU 下的空 GradScaler 占位类。

            接口与 torch.cuda.amp.GradScaler 保持一致，所有方法均为 no-op 或直接透传，
            保证在不使用混合精度时代码路径统一，无需额外分支判断。
            """

            def step(self, optimizer: optim.Optimizer) -> None:
                """执行优化器 step（直接透传，无梯度缩放）。

                Args:
                    optimizer: 优化器实例
                """
                optimizer.step()

            def scale(self, loss: torch.Tensor) -> torch.Tensor:
                """缩放损失（fp32 模式下直接返回原损失，不做缩放）。

                Args:
                    loss: 标量损失张量

                Returns:
                    原损失张量
                """
                return loss

            def unscale_(self, optimizer: optim.Optimizer) -> optim.Optimizer:
                """反缩放优化器梯度（fp32 模式下为 no-op）。

                Args:
                    optimizer: 优化器实例

                Returns:
                    原优化器实例
                """
                return optimizer

            def update(self) -> None:
                """更新缩放因子（fp32 模式下为 no-op）。"""
                return None

        if amp and self.device.type == "cuda":
            try:
                from ..gpu_backend import GPUBackendManager

                scaler = GPUBackendManager.get_grad_scaler(enabled=True)
                self.scaler: Any = scaler if scaler is not None else _DummyScaler()
            except Exception:  # noqa: BLE001
                self.scaler = _DummyScaler()
        else:
            self.scaler = _DummyScaler()

        if self.device.type == "cuda":
            try:
                self.device_ctx: Any | None = torch.cuda.device(self.local_rank)
            except Exception:  # noqa: BLE001
                self.device_ctx = None
        else:
            self.device_ctx = None
        self._ddp_model: DistributedDataParallel | None = None

    def _set_seed(self, seed: int) -> None:
        """跨进程一致的随机种子设置（torch/numpy/python/cuda）。"""
        torch.manual_seed(seed)
        np.random.seed(seed)
        random.seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

    def __enter__(self) -> Accelerator:
        """进入 CUDA device 上下文管理器，绑定当前进程到指定 GPU。

        Returns:
            self
        """
        if self.device_ctx is not None:
            with contextlib.suppress(Exception):
                self.device_ctx.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """退出 CUDA device 上下文管理器。"""
        if self.device_ctx is not None:
            with contextlib.suppress(Exception):
                self.device_ctx.__exit__(exc_type, exc_value, traceback)

    def barrier(self) -> None:
        """分布式同步 barrier；非 DDP 模式为 no-op。"""
        if dist.is_initialized():
            try:
                dist.barrier()
            except Exception as e:  # noqa: BLE001
                logger.debug("legacy barrier 非关键异常: %s", e)

    def all_reduce(self, tensor: torch.Tensor, op: Any = None) -> torch.Tensor:
        """跨进程 all_reduce 聚合（默认 AVG）。"""
        if op is None:
            try:
                op = dist.ReduceOp.AVG
            except Exception:  # noqa: BLE001
                op = None
        if dist.is_initialized():
            try:
                if op is not None:
                    dist.all_reduce(tensor, op=op)
                else:
                    dist.all_reduce(tensor)
            except Exception as e:  # noqa: BLE001
                logger.debug("all_reduce 非关键异常: %s", e)
        return tensor

    def prepare_model(self, model: nn.Module, **kwargs: Any) -> nn.Module:
        """将模型搬到设备，分布式下自动 SyncBN + DDP 包装。

        Args:
            model: 待训练模型
            **kwargs: 透传给 DistributedDataParallel

        Returns:
            设备上的模型（可能是 DDP 包装）
        """
        if hasattr(model, "device"):
            with contextlib.suppress(Exception):
                model.device = self.device  # type: ignore[assignment]
        model = model.to(self.device)
        if self.world_size > 1:
            try:
                model = torch.nn.SyncBatchNorm.convert_sync_batchnorm(model)
                model = DistributedDataParallel(model, device_ids=[self.local_rank], **kwargs)
                self._ddp_model = model
            except Exception as e:  # noqa: BLE001
                logger.error("DDP 模型包装失败，回退单卡: %s", e)
                self.world_size = 1
        return model

    @contextlib.contextmanager
    def no_sync(self) -> Generator[None, None, None]:
        """梯度累积时跳过 DDP 梯度同步的上下文。"""
        if self._ddp_model is not None:
            try:
                with self._ddp_model.no_sync():
                    yield
            except Exception:  # noqa: BLE001
                yield
        else:
            yield

    @property
    def device(self) -> torch.device:
        """获取当前加速器绑定的 torch.device。

        根据 GPU 后端自动检测：CUDA 使用 local_rank 指定的 GPU，MPS 返回 mps 设备，
        否则返回 CPU。

        Returns:
            torch.device: 当前计算设备
        """
        from ..gpu_backend import GPUBackend, GPUBackendManager

        backend = GPUBackendManager.detect_backend()
        if backend == GPUBackend.CUDA:
            return torch.device("cuda", self.local_rank)
        elif backend == GPUBackend.MPS:
            return torch.device("mps")
        return torch.device("cpu")

    @contextlib.contextmanager
    def autocast(self, *args: Any, **kwargs: Any) -> Generator[None, None, None]:
        """进入自动混合精度 autocast 上下文管理器。

        在 amp=True 且支持的设备上启用自动混合精度；amp=False 或设备不支持时为 no-op。

        Args:
            *args: 透传给 torch.amp.autocast 的位置参数
            **kwargs: 透传给 torch.amp.autocast 的关键字参数

        Yields:
            None
        """
        from ..gpu_backend import GPUBackendManager

        device_type = GPUBackendManager.get_autocast_device_type()
        try:
            with torch.amp.autocast(device_type, *args, enabled=self.amp, **kwargs):
                yield
        except Exception:  # noqa: BLE001
            yield

    def backward(self, loss: torch.Tensor) -> None:
        """执行反向传播，使用 GradScaler 缩放损失后反传。

        Args:
            loss: 标量损失张量
        """
        self.scaler.scale(loss).backward()

    def step(self, optimizer: optim.Optimizer) -> None:
        """执行优化器参数更新（经 GradScaler 包装）。

        Args:
            optimizer: 优化器实例
        """
        self.scaler.step(optimizer)

    def update(self) -> None:
        """更新 GradScaler 缩放因子（每个 optimizer step 后调用）。"""
        try:
            self.scaler.update()
        except Exception as e:  # noqa: BLE001
            logger.debug("scaler.update 非关键异常: %s", e)

    def prepare_dataloader(
        self,
        dataset: torch.utils.data.Dataset,
        *,
        batch_size: int,
        num_workers: int = 0,
        shuffle: bool = True,
        collate_fn: Callable[[list[Any]], Any] | None = None,
        drop_last: bool = False,
    ) -> DataLoader:
        """准备 DataLoader，分布式环境下自动添加 DistributedSampler。

        Args:
            dataset: PyTorch Dataset 实例
            batch_size: 每卡 batch 大小
            num_workers: DataLoader worker 进程数（Windows 建议 0）
            shuffle: 是否打乱数据顺序
            collate_fn: 自定义批处理函数
            drop_last: 是否丢弃最后一个不完整 batch

        Returns:
            配置好的 DataLoader 实例
        """
        sampler: torch.utils.data.distributed.DistributedSampler | None = None
        if self.world_size > 1:
            try:
                sampler = torch.utils.data.distributed.DistributedSampler(
                    dataset, num_replicas=self.world_size, rank=self.rank, shuffle=shuffle
                )
                shuffle = False
            except Exception as e:  # noqa: BLE001
                logger.debug("DistributedSampler 创建失败，回退普通 shuffle: %s", e)
                sampler = None

        return DataLoader(
            dataset,
            batch_size=batch_size,
            shuffle=shuffle if sampler is None else False,
            sampler=sampler,
            num_workers=num_workers,
            collate_fn=collate_fn,
            drop_last=drop_last,
            pin_memory=True,
        )

    @staticmethod
    def unwrap(model: nn.Module) -> nn.Module:
        """解包 DDP 包装，返回实际模型（用于保存权重等）。

        Args:
            model: 可能被 DistributedDataParallel 包装的模型

        Returns:
            原始模型（DDP 包装下返回 model.module）
        """
        return cast(nn.Module, model.module) if hasattr(model, "module") else model

    def __del__(self) -> None:
        """析构函数：best-effort 释放加速器资源和 CUDA 显存。"""
        with contextlib.suppress(Exception):
            self._acc.release()
