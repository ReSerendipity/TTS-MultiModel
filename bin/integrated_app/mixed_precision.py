"""FP16/BF16 混合精度推理辅助模块。

提供混合精度推理的配置、自动检测、模型转换与上下文管理功能：

- MixedPrecisionConfig: 混合精度配置数据类
- detect_optimal_dtype(): 自动检测当前硬件最优精度（BF16 > FP16 > FP32）
- apply_mixed_precision(): 将模型转换为指定精度
- MixedPrecisionContext: 推理上下文管理器（autocast + 可选 GradScaler）

BF16 需要 Ampere 及以上架构（compute capability >= 8.0），如 A100/RTX 30xx/40xx。
在 Volta/Turing 架构（V100/RTX 20xx/2080 Ti）上自动回退到 FP16。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("tts_multimodel")

# ---------------------------------------------------------------------------
# 配置数据类
# ---------------------------------------------------------------------------


@dataclass
class MixedPrecisionConfig:
    """混合精度推理配置。

    Attributes:
        enabled: 是否启用混合精度。为 False 时所有操作均使用 FP32。
        dtype: 目标精度。可选值：
            - "auto": 自动检测（优先 BF16，回退 FP16）
            - "bf16": 强制 BFloat16
            - "fp16": 强制 Float16
            - "fp32": 强制 Float32（禁用混合精度）
        autocast_enabled: 是否在推理时启用 torch.autocast。
            某些模型在 autocast 下可能出现数值问题，可关闭以仅做模型权重转换。
    """

    enabled: bool = True
    dtype: str = "auto"
    autocast_enabled: bool = True

    def __post_init__(self):
        """校验配置合法性。"""
        valid_dtypes = {"auto", "bf16", "fp16", "fp32"}
        if self.dtype not in valid_dtypes:
            raise ValueError(f"不支持的精度类型 '{self.dtype}'，可选值: {valid_dtypes}")
        if self.dtype == "fp32":
            self.enabled = False
            logger.info("[混合精度] dtype=fp32，已自动禁用混合精度")


# ---------------------------------------------------------------------------
# 精度检测
# ---------------------------------------------------------------------------


def detect_optimal_dtype(config: MixedPrecisionConfig | None = None):
    """检测当前硬件支持的最优精度类型。

    检测逻辑：
        1. 若配置指定了非 auto 的 dtype，直接使用
        2. 检测 CUDA GPU compute capability：
           - >= 8.0 (Ampere+): 支持 BF16，返回 torch.bfloat16
           - < 8.0 (Volta/Turing): 返回 torch.float16
        3. 非 CUDA 后端: 返回 torch.float32

    Args:
        config: 混合精度配置。为 None 时使用默认配置。

    Returns:
        torch.dtype: 检测到的最优精度类型。
    """
    import torch

    from .gpu_backend import GPUBackend, GPUBackendManager

    if config is None:
        config = MixedPrecisionConfig()

    # 未启用混合精度时直接返回 FP32
    if not config.enabled:
        logger.debug("[混合精度] 混合精度未启用，使用 FP32")
        return torch.float32

    # 显式指定精度
    if config.dtype != "auto":
        dtype_map = {
            "bf16": torch.bfloat16,
            "fp16": torch.float16,
            "fp32": torch.float32,
        }
        target_dtype = dtype_map[config.dtype]
        logger.info(f"[混合精度] 使用显式指定精度: {config.dtype} -> {target_dtype}")
        return target_dtype

    # 自动检测
    backend = GPUBackendManager.detect_backend()

    if backend == GPUBackend.CUDA:
        try:
            if not torch.cuda.is_available():
                logger.warning("[混合精度] CUDA 不可用，回退到 FP32")
                return torch.float32

            props = GPUBackendManager.get_device_properties(0)
            major = props.get("major", 0)
            minor = props.get("minor", 0)

            # Ampere 及以上 (compute capability >= 8.0) 支持 BF16
            if major >= 8:
                logger.info(f"[混合精度] 检测到 Ampere+ GPU (SM {major}.{minor})，使用 BF16")
                return torch.bfloat16
            else:
                logger.info(f"[混合精度] 检测到 Pre-Ampere GPU (SM {major}.{minor})，使用 FP16")
                return torch.float16
        except Exception as e:
            logger.warning(f"[混合精度] GPU 检测失败: {e}，回退到 FP16")
            return torch.float16

    elif backend == GPUBackend.MPS:
        # MPS 目前对 BF16 的支持有限，使用 FP16
        logger.info("[混合精度] MPS 后端，使用 FP16")
        return torch.float16

    # CPU 后端
    logger.info("[混合精度] CPU 后端，使用 FP32")
    return torch.float32


# ---------------------------------------------------------------------------
# 模型精度转换
# ---------------------------------------------------------------------------


def apply_mixed_precision(model, config: MixedPrecisionConfig | None = None):
    """将模型转换为指定的混合精度类型。

    根据 config.dtype 决定模型权重的存储精度：
        - bf16: model.bfloat16()
        - fp16: model.half()
        - fp32: model.float()（不转换）
        - auto: 调用 detect_optimal_dtype() 自动决定

    注意：此函数仅转换模型权重，不会启用 autocast。
    推理时还需配合 MixedPrecisionContext 使用。

    Args:
        model: 要转换的 PyTorch 模型。
        config: 混合精度配置。为 None 时使用默认配置。

    Returns:
        tuple: (转换后的模型, 实际使用的 torch.dtype)
    """
    import torch

    if config is None:
        config = MixedPrecisionConfig()

    # 未启用混合精度，直接返回
    if not config.enabled:
        logger.debug("[混合精度] 混合精度未启用，跳过模型转换")
        return model, torch.float32

    target_dtype = detect_optimal_dtype(config)

    if target_dtype == torch.bfloat16:
        logger.info("[混合精度] 将模型转换为 BFloat16")
        model = model.bfloat16()
    elif target_dtype == torch.float16:
        logger.info("[混合精度] 将模型转换为 Float16")
        model = model.half()
    else:
        logger.info("[混合精度] 保持模型 Float32 精度")

    return model, target_dtype


# ---------------------------------------------------------------------------
# 推理上下文管理器
# ---------------------------------------------------------------------------


class MixedPrecisionContext:
    """混合精度推理上下文管理器。

    在推理期间自动启用 torch.autocast 和可选的 GradScaler，
    确保数值稳定性和最佳性能。

    用法::

        config = MixedPrecisionConfig(enabled=True, dtype="auto")
        with MixedPrecisionContext(config) as ctx:
            output = model(input_tensor)
        # 离开上下文后自动恢复原始精度

    Args:
        config: 混合精度配置。
        use_grad_scaler: 是否使用 GradScaler（主要用于训练，
            推理时通常不需要）。默认 False。
        device: 指定 autocast 的目标设备。为 None 时自动检测。

    Attributes:
        config: 混合精度配置。
        dtype: 实际使用的 torch dtype（在 __enter__ 时确定）。
        autocast_ctx: torch.autocast 上下文管理器实例。
        grad_scaler: GradScaler 实例（仅在 use_grad_scaler=True 时创建）。
    """

    def __init__(
        self,
        config: MixedPrecisionConfig | None = None,
        use_grad_scaler: bool = False,
        device: str | None = None,
    ) -> None:
        """初始化混合精度上下文管理器。

        Args:
            config: 混合精度配置，为 None 时使用默认配置（auto 模式）。
            use_grad_scaler: 是否启用 GradScaler（主要用于训练场景，
                推理时通常不需要）。仅在 FP16 模式下有效。
            device: 指定 autocast 的目标设备类型（如 "cuda", "cpu", "mps"）。
                为 None 时自动检测当前后端。
        """
        self.config = config or MixedPrecisionConfig()
        self.use_grad_scaler = use_grad_scaler
        self.device = device
        self.dtype = None
        self.autocast_ctx = None
        self.grad_scaler = None

    def __enter__(self) -> MixedPrecisionContext:
        """进入混合精度推理上下文，启用 torch.autocast。

        根据配置检测最优精度类型，创建对应的 autocast 上下文管理器。
        如果配置为 FP32 或禁用混合精度，则不做任何操作直接返回。

        Returns:
            self，支持 with 语句的 as 绑定。
        """
        import torch

        from .gpu_backend import GPUBackendManager

        if not self.config.enabled or not self.config.autocast_enabled:
            logger.debug("[混合精度] autocast 未启用，跳过上下文设置")
            self.dtype = torch.float32
            return self

        self.dtype = detect_optimal_dtype(self.config)

        if self.dtype == torch.float32:
            logger.debug("[混合精度] FP32 模式，跳过 autocast")
            return self

        device_type = self.device if self.device is not None else GPUBackendManager.get_autocast_device_type()

        self.autocast_ctx = torch.amp.autocast(
            device_type=device_type,
            dtype=self.dtype,
        )
        self.autocast_ctx.__enter__()

        if self.use_grad_scaler:
            self.grad_scaler = GPUBackendManager.get_grad_scaler(enabled=(self.dtype == torch.float16))
            if self.grad_scaler is not None:
                logger.debug("[混合精度] 已启用 GradScaler")
            else:
                logger.debug("[混合精度] 当前后端不支持 GradScaler")

        logger.debug(f"[混合精度] 进入 autocast 上下文: device={device_type}, dtype={self.dtype}")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """退出混合精度上下文，关闭 autocast 并清理资源。

        Args:
            exc_type: 异常类型（如果 with 块内发生异常）。
            exc_val: 异常值（如果 with 块内发生异常）。
            exc_tb: 异常回溯（如果 with 块内发生异常）。

        Returns:
            False，表示不抑制异常，异常会继续向上传播。
        """
        if self.autocast_ctx is not None:
            self.autocast_ctx.__exit__(exc_type, exc_val, exc_tb)
            self.autocast_ctx = None

        logger.debug("[混合精度] 已退出 autocast 上下文")
        return False

    def scale_loss(self, loss):
        """使用 GradScaler 缩放损失值（训练场景）。

        Args:
            loss: 原始损失张量。

        Returns:
            缩放后的损失张量。若 GradScaler 未启用，直接返回原始 loss。
        """
        if self.grad_scaler is not None:
            return self.grad_scaler.scale(loss)
        return loss

    def unscale_and_step(self, optimizer):
        """使用 GradScaler 反缩放并执行优化器步骤（训练场景）。

        Args:
            optimizer: PyTorch 优化器实例。
        """
        if self.grad_scaler is not None:
            self.grad_scaler.unscale_(optimizer)
            self.grad_scaler.step(optimizer)
            self.grad_scaler.update()
        else:
            optimizer.step()
