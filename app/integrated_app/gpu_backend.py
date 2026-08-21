"""GPU 后端抽象层 - 基于 Strategy 模式统一封装 PyTorch CUDA/MPS/CPU 后端差异。

本模块为 TTS_MultiModel 项目提供统一的 GPU 后端访问接口，采用 Strategy 设计模式
将不同硬件厂商的后端实现（CUDA/MPS/CPU/ROCm/XPU）封装为独立的策略类，通过
``GPUBackendManager`` 统一调度，消除上层模块（gpu_utils.py、model_manager.py、
engines/* 等）中的 if/elif 长链，实现对多后端的透明访问。

架构说明：
    - ``GPUBackend`` 枚举：定义受支持的后端类型标识符
    - ``_CUDAStrategy`` / ``_MPSStrategy`` / ``_CPUStrategy`` /
      ``_ROCmStrategy`` / ``_XPUStrategy``：各后端的具体策略实现，
      暴露统一的静态方法接口
    - ``GPUBackendManager``：统一管理器，持有后端 -> 策略的注册表，
      负责后端自动检测、策略查找与方法分发

硬约束（必读）：
    本项目实际模型（VoxCPM2 / IndexTTS2）**仅正式支持 NVIDIA CUDA** 后端进行
    GPU 加速推理。MPS 与 CPU 仅作为回退路径提供：
    - MPS（Apple Silicon）：部分算子不支持，PyTorch 会静默回退到 CPU，
      推理质量与速度均无官方保证
    - CPU：可运行但速度极慢，仅用于调试或无 GPU 场景
    ROCm（AMD）/ XPU（Intel）为 **API 完整性预留**，当前未注册实际策略，
    使用时会自动回退到 CPU 策略。

使用示例：
    >>> backend = GPUBackendManager.detect_backend()
    >>> device = GPUBackendManager.get_device()
    >>> total, allocated, reserved, free = GPUBackendManager.get_memory_info()
"""

import logging
from collections.abc import Callable
from enum import Enum
from typing import Any

import torch

#: 模块级日志记录器，命名空间 "tts_multimodel"
logger = logging.getLogger("tts_multimodel")


class GPUBackend(Enum):
    """受支持的 GPU 后端枚举。

    枚举成员及实际支持情况：
        CUDA:
            NVIDIA GPU 后端。**本项目唯一正式支持的 GPU 后端**，
            推理质量、速度与稳定性均经过验证。
        MPS:
            Apple Silicon Metal Performance Shaders 后端。
            回退路径，部分算子不支持时会静默降级为 CPU，不保证推理质量。
        CPU:
            纯 CPU 回退路径。任何场景下均可用，仅用于调试或无 GPU 环境。
        ROCM:
            AMD GPU 后端。**API 完整性预留**，未注册实际策略，
            使用时自动回退到 CPU 策略。
        XPU:
            Intel GPU 后端。**API 完整性预留**，未注册实际策略，
            使用时自动回退到 CPU 策略。
    """

    CUDA = "cuda"
    MPS = "mps"
    CPU = "cpu"
    ROCM = "rocm"
    XPU = "xpu"


# ---------------------------------------------------------------------------
# Strategy classes – 每个后端族对应一个策略类，由 GPUBackendManager 调度
# ---------------------------------------------------------------------------


class _CUDAStrategy:
    """CUDA 后端策略（NVIDIA GPU）。

    适用场景：
        本项目的主推理后端。VoxCPM2 与 IndexTTS2 均在 CUDA 下经过完整验证，
        可获得最佳的推理速度与质量。

    限制：
        - 依赖可用的 NVIDIA GPU + 正确版本的 CUDA Toolkit + PyTorch CUDA 构建
        - 所有 ``torch.cuda.*`` 调用已封装 try/except，避免 CUDA 未初始化或
          驱动异常时导致上层崩溃，失败时返回与 CPU 策略一致的默认值
    """

    @staticmethod
    def get_device(index: int = 0) -> torch.device:
        """获取指定索引的 CUDA 设备对象。

        Args:
            index: CUDA 设备索引，从 0 开始。默认值为 0。

        Returns:
            对应索引的 ``torch.device("cuda:{index}")`` 对象。
        """
        return torch.device(f"cuda:{index}")

    @staticmethod
    def get_device_count() -> int:
        """获取可用的 CUDA 设备数量。

        Returns:
            可用 CUDA 设备数。CUDA 不可用时返回 0。
        """
        try:
            return torch.cuda.device_count()
        except RuntimeError as e:
            logger.debug(f"获取 CUDA 设备数量失败: {e}")
            return 0

    @staticmethod
    def get_device_name(index: int = 0) -> str:
        """获取指定 CUDA 设备的名称。

        Args:
            index: CUDA 设备索引。默认值为 0。

        Returns:
            设备名称字符串（如 "NVIDIA GeForce RTX 4090"）。
            CUDA 不可用或索引越界时返回 ``"CPU"``。
        """
        try:
            if torch.cuda.is_available() and index < torch.cuda.device_count():
                return torch.cuda.get_device_name(index)
        except RuntimeError as e:
            logger.debug(f"获取 CUDA 设备 {index} 名称失败: {e}")
        return "CPU"

    @staticmethod
    def get_device_properties(index: int = 0) -> dict[str, Any]:
        """获取指定 CUDA 设备的属性字典。

        Args:
            index: CUDA 设备索引。默认值为 0。

        Returns:
            包含设备属性的字典，键包括：
            - ``name`` (str): 设备名称
            - ``total_memory`` (int): 总显存字节数
            - ``major`` (int): 计算能力主版本号
            - ``minor`` (int): 计算能力次版本号
            CUDA 不可用或索引越界时返回 CPU 占位字典。
        """
        try:
            if torch.cuda.is_available() and index < torch.cuda.device_count():
                props = torch.cuda.get_device_properties(index)
                return {
                    "name": props.name,
                    "total_memory": props.total_memory,
                    "major": props.major,
                    "minor": props.minor,
                }
        except RuntimeError as e:
            logger.debug(f"获取 CUDA 设备 {index} 属性失败: {e}")
        return {"name": "CPU", "total_memory": 0, "major": 0, "minor": 0}

    @staticmethod
    def memory_allocated(device: torch.device | None = None) -> int:
        """获取指定 CUDA 设备上当前已分配的显存字节数。

        Args:
            device: 目标设备，为 ``None`` 时使用默认 CUDA 设备。

        Returns:
            已分配显存字节数。CUDA 调用失败时返回 0。
        """
        try:
            return torch.cuda.memory_allocated(device)
        except RuntimeError as e:
            logger.debug(f"查询 CUDA 已分配显存失败: {e}")
            return 0

    @staticmethod
    def memory_reserved(device: torch.device | None = None) -> int:
        """获取指定 CUDA 设备上由缓存分配器保留的显存字节数。

        Args:
            device: 目标设备，为 ``None`` 时使用默认 CUDA 设备。

        Returns:
            保留显存字节数。CUDA 调用失败时返回 0。
        """
        try:
            return torch.cuda.memory_reserved(device)
        except RuntimeError as e:
            logger.debug(f"查询 CUDA 保留显存失败: {e}")
            return 0

    @staticmethod
    def empty_cache() -> None:
        """释放 CUDA 缓存分配器中所有未占用的显存，归还给操作系统。

        注意：
            已被张量实际占用的显存不会被释放。若需更彻底的清理，
            请结合 ``get_cuda_clear_workspaces_func()`` 使用。
        """
        try:
            torch.cuda.empty_cache()
        except RuntimeError as e:
            logger.debug(f"清空 CUDA 缓存失败: {e}")

    @staticmethod
    def synchronize(device: torch.device | None = None) -> None:
        """在指定 CUDA 设备上等待所有流中的内核完成。

        Args:
            device: 目标设备，为 ``None`` 时同步当前 CUDA 设备。
        """
        try:
            if device is None:
                torch.cuda.synchronize()
            else:
                torch.cuda.synchronize(device)
        except RuntimeError as e:
            logger.debug(f"CUDA 设备同步失败: {e}")

    @staticmethod
    def get_memory_info(index: int = 0) -> tuple[int, int, int, int]:
        """获取指定 CUDA 设备的完整显存信息元组。

        Args:
            index: CUDA 设备索引。默认值为 0。

        Returns:
            四元组 ``(total, allocated, reserved, free)``，单位均为字节：
            - total: 设备总显存
            - allocated: 当前已分配显存
            - reserved: 缓存分配器保留显存
            - free: 可用显存（total - allocated）
            CUDA 不可用或调用失败时返回 ``(0, 0, 0, 0)``。
        """
        try:
            if not torch.cuda.is_available():
                return (0, 0, 0, 0)
            props = torch.cuda.get_device_properties(index)
            total = props.total_memory
            allocated = torch.cuda.memory_allocated(index)
            reserved = torch.cuda.memory_reserved(index)
            return (total, allocated, reserved, total - allocated)
        except RuntimeError as e:
            logger.debug(f"获取 CUDA 设备 {index} 显存信息失败: {e}")
            return (0, 0, 0, 0)

    @staticmethod
    def get_cuda_clear_workspaces_func() -> Callable[[], None] | None:
        """获取 CUDA cuBLAS 工作区清理函数（若存在）。

        Returns:
            可调用对象 ``torch._C._cuda_clearCublasWorkspaces``，
            若当前 PyTorch 版本未暴露该内部 API 则返回 ``None``。
        """
        try:
            return torch._C._cuda_clearCublasWorkspaces
        except AttributeError:
            return None

    @staticmethod
    def ipc_collect(device: torch.device | None = None) -> None:
        """强制执行 CUDA IPC 收集，释放多进程间不再使用的 GPU 内存。

        Args:
            device: 目标设备，为 ``None`` 时对所有 CUDA 设备执行。
        """
        try:
            if device is None:
                torch.cuda.ipc_collect()
            else:
                torch.cuda.ipc_collect(device)
        except Exception as e:
            logger.debug(f"CUDA IPC 收集失败: {e}")

    @staticmethod
    def get_grad_scaler(enabled: bool = True) -> Any | None:
        """获取适用于 CUDA 后端的 ``torch.amp.GradScaler`` 实例。

        Args:
            enabled: 是否启用梯度缩放。为 ``False`` 时直接返回 ``None``。

        Returns:
            ``torch.amp.GradScaler("cuda")`` 实例，或 ``None``。
        """
        try:
            if enabled:
                return torch.amp.GradScaler("cuda")
        except Exception as e:
            logger.debug(f"创建 CUDA GradScaler 失败: {e}")
        return None

    @staticmethod
    def get_autocast_device_type() -> str:
        """获取 ``torch.amp.autocast`` 所需的 device_type 字符串。

        Returns:
            固定返回 ``"cuda"``。
        """
        return "cuda"

    @staticmethod
    def get_process_group_backend() -> str:
        """获取分布式进程组推荐后端字符串。

        Returns:
            固定返回 ``"nccl"``（NVIDIA Collective Communications Library）。
        """
        return "nccl"

    @staticmethod
    def format_device_string(index: int = 0) -> str:
        """格式化可直接用于 ``tensor.to()`` 的 CUDA 设备字符串。

        Args:
            index: CUDA 设备索引。默认值为 0。

        Returns:
            形如 ``"cuda:0"`` 的设备字符串。
        """
        return f"cuda:{index}"


class _MPSStrategy:
    """Apple MPS 后端策略（Metal Performance Shaders）。

    适用场景：
        Apple Silicon（M1/M2/M3 等芯片）macOS 设备上的回退推理路径。
        当 CUDA 不可用时自动启用。

    限制：
        - PyTorch MPS 后端对部分算子支持不完善，遇到不支持的算子时
          **会静默回退到 CPU 执行**，可能导致推理速度下降或结果差异
        - MPS 不提供细粒度显存查询接口，``memory_allocated`` /
          ``memory_reserved`` / ``get_memory_info`` 均返回 0
        - 不支持 GradScaler 混合精度训练，autocast 降级为 cpu 模式
    """

    @staticmethod
    def get_device(index: int = 0) -> torch.device:
        """获取 MPS 设备对象。

        Args:
            index: 设备索引（MPS 不支持多设备，此参数忽略）。默认值为 0。

        Returns:
            ``torch.device("mps")`` 对象。
        """
        return torch.device("mps")

    @staticmethod
    def get_device_count() -> int:
        """获取 MPS 设备数量。

        Returns:
            固定返回 1（Apple Silicon 仅有一个 MPS 设备）。
        """
        return 1

    @staticmethod
    def get_device_name(index: int = 0) -> str:
        """获取 MPS 设备名称。

        Args:
            index: 设备索引（忽略）。默认值为 0。

        Returns:
            固定返回 ``"Apple MPS"``。
        """
        return "Apple MPS"

    @staticmethod
    def get_device_properties(index: int = 0) -> dict[str, Any]:
        """获取 MPS 设备属性。

        Args:
            index: 设备索引（忽略）。默认值为 0。

        Returns:
            占位字典。MPS 不提供显存容量查询，``total_memory`` 为 0。
        """
        return {"name": "Apple MPS", "total_memory": 0, "major": 0, "minor": 0}

    @staticmethod
    def memory_allocated(device: torch.device | None = None) -> int:
        """获取 MPS 已分配显存（不支持）。

        Args:
            device: 目标设备（忽略）。

        Returns:
            固定返回 0（MPS 未暴露显存分配统计接口）。
        """
        return 0

    @staticmethod
    def memory_reserved(device: torch.device | None = None) -> int:
        """获取 MPS 保留显存（不支持）。

        Args:
            device: 目标设备（忽略）。

        Returns:
            固定返回 0（MPS 未暴露显存保留统计接口）。
        """
        return 0

    @staticmethod
    def empty_cache() -> None:
        """释放 MPS 缓存（空实现）。

        MPS 内存由 Metal 框架自动管理，PyTorch 未暴露显式缓存清理接口。
        """
        pass

    @staticmethod
    def synchronize(device: torch.device | None = None) -> None:
        """同步 MPS 设备（空实现）。

        Args:
            device: 目标设备（忽略）。
        """
        pass

    @staticmethod
    def get_memory_info(index: int = 0) -> tuple[int, int, int, int]:
        """获取 MPS 显存信息（不支持）。

        Args:
            index: 设备索引（忽略）。默认值为 0。

        Returns:
            ``(0, 0, 0, 0)``——MPS 未提供显存查询接口。
        """
        return (0, 0, 0, 0)

    @staticmethod
    def get_cuda_clear_workspaces_func() -> Callable[[], None] | None:
        """获取 cuBLAS 工作区清理函数（MPS 不适用）。

        Returns:
            固定返回 ``None``。
        """
        return None

    @staticmethod
    def ipc_collect(device: torch.device | None = None) -> None:
        """执行 IPC 收集（MPS 不适用，空实现）。

        Args:
            device: 目标设备（忽略）。
        """
        pass

    @staticmethod
    def get_grad_scaler(enabled: bool = True) -> Any | None:
        """获取 MPS 梯度缩放器（不支持）。

        Args:
            enabled: 是否启用（忽略）。

        Returns:
            固定返回 ``None``——MPS 不支持 GradScaler。
        """
        return None

    @staticmethod
    def get_autocast_device_type() -> str:
        """获取 autocast 设备类型字符串。

        MPS 对混合精度支持不完善，autocast 降级使用 CPU 模式。

        Returns:
            固定返回 ``"cpu"``。
        """
        return "cpu"

    @staticmethod
    def get_process_group_backend() -> str:
        """获取分布式进程组推荐后端。

        Returns:
            固定返回 ``"gloo"``（MPS 不支持 NCCL）。
        """
        return "gloo"

    @staticmethod
    def format_device_string(index: int = 0) -> str:
        """格式化 MPS 设备字符串。

        Args:
            index: 设备索引（忽略）。默认值为 0。

        Returns:
            固定返回 ``"mps"``。
        """
        return "mps"


class _CPUStrategy:
    """CPU 回退策略。

    适用场景：
        - 无任何可用 GPU（CUDA/MPS 均不可用）时的最终回退
        - ROCm / XPU 等预留后端（未注册策略）被显式选择时的降级路径
        - 调试场景，需要在纯 CPU 上复现问题

    限制：
        - 推理速度极慢，仅适用于短文本或开发调试
        - 无显存概念，所有内存相关查询返回 0
    """

    @staticmethod
    def get_device(index: int = 0) -> torch.device:
        """获取 CPU 设备对象。

        Args:
            index: 设备索引（忽略，CPU 无多设备概念）。默认值为 0。

        Returns:
            ``torch.device("cpu")`` 对象。
        """
        return torch.device("cpu")

    @staticmethod
    def get_device_count() -> int:
        """获取 CPU "设备" 数量。

        Returns:
            固定返回 0——CPU 不被视为独立 GPU 设备。
        """
        return 0

    @staticmethod
    def get_device_name(index: int = 0) -> str:
        """获取 CPU 设备名称。

        Args:
            index: 设备索引（忽略）。默认值为 0。

        Returns:
            固定返回 ``"CPU"``。
        """
        return "CPU"

    @staticmethod
    def get_device_properties(index: int = 0) -> dict[str, Any]:
        """获取 CPU 设备属性占位字典。

        Args:
            index: 设备索引（忽略）。默认值为 0。

        Returns:
            CPU 占位字典，``total_memory`` 为 0（上层应通过 psutil 等
            其他途径查询系统内存）。
        """
        return {"name": "CPU", "total_memory": 0, "major": 0, "minor": 0}

    @staticmethod
    def memory_allocated(device: torch.device | None = None) -> int:
        """获取 CPU "已分配显存"（无意义）。

        Args:
            device: 目标设备（忽略）。

        Returns:
            固定返回 0。
        """
        return 0

    @staticmethod
    def memory_reserved(device: torch.device | None = None) -> int:
        """获取 CPU "保留显存"（无意义）。

        Args:
            device: 目标设备（忽略）。

        Returns:
            固定返回 0。
        """
        return 0

    @staticmethod
    def empty_cache() -> None:
        """释放 CPU "缓存"（空实现）。

        Why pass（为什么是无操作）：
            CPU 内存由操作系统的虚拟内存管理机制统一管理，
            PyTorch 在 CPU 上没有专门的缓存分配器（caching allocator），
            张量内存通过标准 malloc/free 分配，不存在 CUDA 那种
            "保留但未使用"的显存池。因此没有可显式释放的缓存。
            如需降低 RSS，应显式删除不再使用的张量引用并触发 Python GC。
        """
        pass

    @staticmethod
    def synchronize(device: torch.device | None = None) -> None:
        """同步 CPU 设备（空实现）。

        CPU 上所有计算均为同步执行，无需额外同步。

        Args:
            device: 目标设备（忽略）。
        """
        pass

    @staticmethod
    def get_memory_info(index: int = 0) -> tuple[int, int, int, int]:
        """获取 CPU 显存信息（无意义）。

        Args:
            index: 设备索引（忽略）。默认值为 0。

        Returns:
            ``(0, 0, 0, 0)``。
        """
        return (0, 0, 0, 0)

    @staticmethod
    def get_cuda_clear_workspaces_func() -> Callable[[], None] | None:
        """获取 cuBLAS 清理函数（CPU 不适用）。

        Returns:
            固定返回 ``None``。
        """
        return None

    @staticmethod
    def ipc_collect(device: torch.device | None = None) -> None:
        """执行 IPC 收集（CPU 不适用，空实现）。

        Args:
            device: 目标设备（忽略）。
        """
        pass

    @staticmethod
    def get_grad_scaler(enabled: bool = True) -> Any | None:
        """获取 CPU 梯度缩放器（不支持）。

        Args:
            enabled: 是否启用（忽略）。

        Returns:
            固定返回 ``None``——混合精度训练主要面向 GPU。
        """
        return None

    @staticmethod
    def get_autocast_device_type() -> str:
        """获取 autocast 设备类型。

        Returns:
            固定返回 ``"cpu"``。
        """
        return "cpu"

    @staticmethod
    def get_process_group_backend() -> str:
        """获取分布式进程组后端。

        Returns:
            固定返回 ``"gloo"``——CPU 环境通用后端。
        """
        return "gloo"

    @staticmethod
    def format_device_string(index: int = 0) -> str:
        """格式化 CPU 设备字符串。

        Args:
            index: 设备索引（忽略）。默认值为 0。

        Returns:
            固定返回 ``"cpu"``。
        """
        return "cpu"


class _ROCmStrategy:
    """ROCm 后端策略（AMD GPU，API 完整性预留）。

    适用场景：
        暂未启用。当前本项目未注册 ROCm 策略到 Manager，
        显式选择 ROCm 时会自动回退到 ``_CPUStrategy``。
        此类仅用于展示接口一致性，便于未来 AMD 支持扩展。

    限制：
        - 未经过 VoxCPM2 / IndexTTS2 推理验证
        - 当前无 ROCm 环境测试
    """

    @staticmethod
    def get_device(index: int = 0) -> torch.device:
        """获取指定索引的 ROCm 设备对象。

        Args:
            index: ROCm 设备索引。默认值为 0。

        Returns:
            ``torch.device("cuda:{index}")``（ROCm 在 PyTorch 中
            复用 CUDA 设备类型标识符）。
        """
        return torch.device(f"cuda:{index}")

    @staticmethod
    def get_device_count() -> int:
        """获取 ROCm 设备数量。

        Returns:
            当前为预留接口，固定返回 0。
        """
        return 0

    @staticmethod
    def get_device_name(index: int = 0) -> str:
        """获取 ROCm 设备名称。

        Args:
            index: 设备索引。默认值为 0。

        Returns:
            占位字符串 ``"AMD ROCm (reserved)"``。
        """
        return "AMD ROCm (reserved)"

    @staticmethod
    def get_device_properties(index: int = 0) -> dict[str, Any]:
        """获取 ROCm 设备属性。

        Args:
            index: 设备索引。默认值为 0。

        Returns:
            占位字典。
        """
        return {"name": "AMD ROCm (reserved)", "total_memory": 0, "major": 0, "minor": 0}

    @staticmethod
    def memory_allocated(device: torch.device | None = None) -> int:
        """获取 ROCm 已分配显存。

        Args:
            device: 目标设备（忽略）。

        Returns:
            固定返回 0。
        """
        return 0

    @staticmethod
    def memory_reserved(device: torch.device | None = None) -> int:
        """获取 ROCm 保留显存。

        Args:
            device: 目标设备（忽略）。

        Returns:
            固定返回 0。
        """
        return 0

    @staticmethod
    def empty_cache() -> None:
        """释放 ROCm 缓存（预留空实现）。"""
        pass

    @staticmethod
    def synchronize(device: torch.device | None = None) -> None:
        """同步 ROCm 设备（预留空实现）。

        Args:
            device: 目标设备（忽略）。
        """
        pass

    @staticmethod
    def get_memory_info(index: int = 0) -> tuple[int, int, int, int]:
        """获取 ROCm 显存信息。

        Args:
            index: 设备索引。默认值为 0。

        Returns:
            ``(0, 0, 0, 0)``。
        """
        return (0, 0, 0, 0)

    @staticmethod
    def get_cuda_clear_workspaces_func() -> Callable[[], None] | None:
        """获取 ROCm 工作区清理函数（不适用）。

        Returns:
            ``None``。
        """
        return None

    @staticmethod
    def ipc_collect(device: torch.device | None = None) -> None:
        """执行 ROCm IPC 收集（预留空实现）。

        Args:
            device: 目标设备（忽略）。
        """
        pass

    @staticmethod
    def get_grad_scaler(enabled: bool = True) -> Any | None:
        """获取 ROCm 梯度缩放器。

        Args:
            enabled: 是否启用（忽略）。

        Returns:
            ``None``。
        """
        return None

    @staticmethod
    def get_autocast_device_type() -> str:
        """获取 ROCm autocast 设备类型。

        Returns:
            ROCm 复用 CUDA 后端，返回 ``"cuda"``。
        """
        return "cuda"

    @staticmethod
    def get_process_group_backend() -> str:
        """获取 ROCm 分布式后端。

        Returns:
            ``"rccl"``（AMD RCCL 库，等价于 NVIDIA NCCL）。
        """
        return "rccl"

    @staticmethod
    def format_device_string(index: int = 0) -> str:
        """格式化 ROCm 设备字符串。

        Args:
            index: 设备索引。默认值为 0。

        Returns:
            ROCm 复用 CUDA 命名，返回 ``"cuda:{index}"``。
        """
        return f"cuda:{index}"


class _XPUStrategy:
    """XPU 后端策略（Intel GPU，API 完整性预留）。

    适用场景：
        暂未启用。当前本项目未注册 XPU 策略到 Manager，
        显式选择 XPU 时会自动回退到 ``_CPUStrategy``。
        此类仅用于展示接口一致性，便于未来 Intel GPU 支持扩展。

    限制：
        - 未经过 VoxCPM2 / IndexTTS2 推理验证
        - 依赖 Intel Extension for PyTorch (IPEX)
    """

    @staticmethod
    def get_device(index: int = 0) -> torch.device:
        """获取指定索引的 XPU 设备对象。

        Args:
            index: XPU 设备索引。默认值为 0。

        Returns:
            ``torch.device("xpu:{index}")``。
        """
        return torch.device(f"xpu:{index}")

    @staticmethod
    def get_device_count() -> int:
        """获取 XPU 设备数量。

        Returns:
            当前为预留接口，固定返回 0。
        """
        return 0

    @staticmethod
    def get_device_name(index: int = 0) -> str:
        """获取 XPU 设备名称。

        Args:
            index: 设备索引。默认值为 0。

        Returns:
            占位字符串 ``"Intel XPU (reserved)"``。
        """
        return "Intel XPU (reserved)"

    @staticmethod
    def get_device_properties(index: int = 0) -> dict[str, Any]:
        """获取 XPU 设备属性。

        Args:
            index: 设备索引。默认值为 0。

        Returns:
            占位字典。
        """
        return {"name": "Intel XPU (reserved)", "total_memory": 0, "major": 0, "minor": 0}

    @staticmethod
    def memory_allocated(device: torch.device | None = None) -> int:
        """获取 XPU 已分配显存。

        Args:
            device: 目标设备（忽略）。

        Returns:
            固定返回 0。
        """
        return 0

    @staticmethod
    def memory_reserved(device: torch.device | None = None) -> int:
        """获取 XPU 保留显存。

        Args:
            device: 目标设备（忽略）。

        Returns:
            固定返回 0。
        """
        return 0

    @staticmethod
    def empty_cache() -> None:
        """释放 XPU 缓存（预留空实现）。"""
        pass

    @staticmethod
    def synchronize(device: torch.device | None = None) -> None:
        """同步 XPU 设备（预留空实现）。

        Args:
            device: 目标设备（忽略）。
        """
        pass

    @staticmethod
    def get_memory_info(index: int = 0) -> tuple[int, int, int, int]:
        """获取 XPU 显存信息。

        Args:
            index: 设备索引。默认值为 0。

        Returns:
            ``(0, 0, 0, 0)``。
        """
        return (0, 0, 0, 0)

    @staticmethod
    def get_cuda_clear_workspaces_func() -> Callable[[], None] | None:
        """获取 XPU 工作区清理函数（不适用）。

        Returns:
            ``None``。
        """
        return None

    @staticmethod
    def ipc_collect(device: torch.device | None = None) -> None:
        """执行 XPU IPC 收集（预留空实现）。

        Args:
            device: 目标设备（忽略）。
        """
        pass

    @staticmethod
    def get_grad_scaler(enabled: bool = True) -> Any | None:
        """获取 XPU 梯度缩放器。

        Args:
            enabled: 是否启用（忽略）。

        Returns:
            ``None``。
        """
        return None

    @staticmethod
    def get_autocast_device_type() -> str:
        """获取 XPU autocast 设备类型。

        Returns:
            ``"xpu"``。
        """
        return "xpu"

    @staticmethod
    def get_process_group_backend() -> str:
        """获取 XPU 分布式后端。

        Returns:
            ``"ccl"``（Intel oneCCL 通信库）。
        """
        return "ccl"

    @staticmethod
    def format_device_string(index: int = 0) -> str:
        """格式化 XPU 设备字符串。

        Args:
            index: 设备索引。默认值为 0。

        Returns:
            ``"xpu:{index}"``。
        """
        return f"xpu:{index}"


# ---------------------------------------------------------------------------
# GPUBackendManager – 统一 GPU 后端管理器（Strategy 注册表模式）
# ---------------------------------------------------------------------------


class GPUBackendManager:
    """统一 GPU 后端管理器。

    **设计模式**：Strategy 注册表模式。

    Manager 维护一张 ``GPUBackend`` 枚举 -> 策略类的注册表（``_strategies``），
    所有后端相关的操作均通过查表获得对应策略后派发执行，而非在每个方法内
    编写 if/elif 长链。

    设计优势（开闭原则）：
        新增后端时只需：
        1) 定义新的 Strategy 类，实现统一的静态方法接口
        2) 调用 ``register_strategy()`` 注册到 Manager
        **无需修改 GPUBackendManager 本身的任何代码**，符合对扩展开放、
        对修改关闭的设计原则。

    默认回退链（优先级从高到低）：
        CUDA -> MPS -> CPU

    即：``detect_backend()`` 会按此顺序检测，找到第一个可用后端即返回；
    上层调用所有方法时若 ``backend=None``，会自动使用检测到的最优后端。

    使用示例：
        >>> backend = GPUBackendManager.detect_backend()
        >>> device = GPUBackendManager.get_device()
        >>> total, allocated, reserved, free = GPUBackendManager.get_memory_info()
        >>> total_gb = GPUBackendManager.get_total_memory_gb()
    """

    _cached_backend: GPUBackend | None = None

    # Why 注册表（不用 if/elif 长链）：
    #   开闭原则 - 新增 ROCm/XPU 等后端时，只需定义新 Strategy 类 + 调用
    #   register_strategy()，不需要修改 Manager 中十几个方法的 if/elif 分支，
    #   降低了漏改、错改的风险，也避免了 Manager 代码随后端数量线性膨胀。
    _strategies: dict[GPUBackend, Any] | None = None

    @classmethod
    def _ensure_strategies_initialized(cls) -> None:
        """惰性初始化策略注册表（仅首次调用时执行）。

        采用延迟初始化（Lazy Initialization）模式：模块导入时不立即创建
        策略字典，而是在首次调用任何需要策略派发的方法时才填充 ``_strategies``。
        这样做的好处是：
        1. 避免模块导入时实例化不需要的策略类（虽然策略类只有静态方法，但
           保持惰性符合"按需加载"原则）。
        2. 允许第三方代码在模块导入后、首次使用前调用 ``register_strategy()``
           注册自定义策略，此时 _strategies 尚未初始化，不会覆盖自定义注册。

        默认注册映射：
            - CUDA → _CUDAStrategy（NVIDIA GPU 主后端）
            - MPS → _MPSStrategy（Apple Silicon 回退）
            - CPU → _CPUStrategy（最终兜底）
            - ROCM → _CPUStrategy（API 预留，暂未实现，回退 CPU）
            - XPU → _CPUStrategy（API 预留，暂未实现，回退 CPU）
        """
        if cls._strategies is None:
            cls._strategies = {
                GPUBackend.CUDA: _CUDAStrategy,
                GPUBackend.MPS: _MPSStrategy,
                GPUBackend.CPU: _CPUStrategy,
                GPUBackend.ROCM: _CPUStrategy,
                GPUBackend.XPU: _CPUStrategy,
            }

    # ------------------------------------------------------------------
    # 注册表管理 & 后端检测
    # ------------------------------------------------------------------

    @classmethod
    def detect_backend(cls) -> GPUBackend:
        """自动检测当前环境的最优可用 GPU 后端。

        检测优先级（为什么 CUDA 第一）：
            1. **CUDA**：本项目 VoxCPM2 和 IndexTTS2 的推理质量、速度、
               稳定性均在 CUDA 上经过完整验证，是唯一官方支持的 GPU 后端。
               因此第一优先级检测 CUDA，只要可用就直接选用。
            2. **MPS**：Apple Silicon 回退路径。注意 PyTorch MPS 后端
               对部分算子支持不完善，遇到不支持的算子会**静默 fallback
               到 CPU**，导致推理速度与质量均不可控，因此优先级低于 CUDA。
            3. **CPU**：最终兜底路径，任何环境下均可用。

        检测结果会缓存到 ``_cached_backend``，后续调用直接返回缓存值。
        若需强制重新检测，请先调用 ``clear_cache()``。

        Returns:
            检测到的最优 ``GPUBackend`` 枚举值。
        """
        if cls._cached_backend is not None:
            return cls._cached_backend

        # 1. 检测 NVIDIA CUDA（最高优先级）
        try:
            if torch.cuda.is_available():
                cls._cached_backend = GPUBackend.CUDA
                logger.info("[GPU Backend] 检测到 NVIDIA CUDA 后端")
                return cls._cached_backend
        except Exception as e:
            logger.debug(f"CUDA 可用性检测异常，跳过: {e}")

        # 2. 检测 Apple MPS（次优先级）
        try:
            mps_available = torch.backends.mps.is_available()
        except AttributeError:
            # Why AttributeError 捕获：
            #   旧版本 PyTorch（< 1.12）未实现 torch.backends.mps 属性，
            #   直接访问会抛 AttributeError。此处优雅降级，当作 MPS 不可用处理。
            mps_available = False
        except Exception as e:
            logger.debug(f"MPS 可用性检测异常: {e}")
            mps_available = False

        if mps_available:
            cls._cached_backend = GPUBackend.MPS
            logger.info("[GPU Backend] 检测到 Apple MPS 后端")
            return cls._cached_backend

        # 3. 回退到 CPU
        cls._cached_backend = GPUBackend.CPU
        logger.warning("[GPU Backend] 未检测到 GPU，使用 CPU 后端")
        return cls._cached_backend

    @classmethod
    def register_strategy(cls, backend: GPUBackend, strategy: Any) -> None:
        """向注册表注册（或覆盖）指定后端对应的策略类。

        Args:
            backend: 目标后端枚举值。
            strategy: 实现了统一静态方法接口的策略类（非实例）。
        """
        cls._ensure_strategies_initialized()
        cls._strategies[backend] = strategy
        logger.info(f"[GPU Backend] 已注册后端 {backend.value} 的策略: {strategy.__name__}")

    @classmethod
    def _resolve_backend_and_index(
        cls, backend_arg: Any = None, index_arg: int = 0, device_arg: Any = None
    ) -> tuple[GPUBackend | None, int, Any]:
        """内部辅助方法：智能解析 (backend, index, device) 参数。

        历史遗留问题：多个调用点将 int 设备索引或 torch.device 作为第一个参数
        传入，而不是 GPUBackend 枚举。本方法统一做类型自适应，保证上层调用
        无论传 (backend, index)、(int_index,)、(torch.device,) 都能正确工作。

        Returns:
            (resolved_backend, resolved_index, resolved_device)
        """
        resolved_backend: GPUBackend | None = None
        resolved_index: int = index_arg
        resolved_device: Any = device_arg

        if backend_arg is None:
            resolved_backend = None
        elif isinstance(backend_arg, GPUBackend):
            resolved_backend = backend_arg
        elif isinstance(backend_arg, int):
            # 第一个参数是 int —— 当作设备索引，自动检测后端
            resolved_backend = None
            resolved_index = backend_arg if backend_arg >= 0 else 0
        elif isinstance(backend_arg, torch.device):
            # 第一个参数是 torch.device —— 提取索引，自动检测后端
            resolved_backend = None
            resolved_index = backend_arg.index if backend_arg.index is not None else 0
            resolved_device = backend_arg
        else:
            # 未知类型，自动检测后端
            logger.debug(
                f"[GPU Backend] _resolve_backend_and_index: 未识别的参数类型 {type(backend_arg)}，自动检测后端"
            )
            resolved_backend = None

        return resolved_backend, resolved_index, resolved_device

    @classmethod
    def get_strategy(cls, backend: GPUBackend | None = None) -> Any:
        """获取指定后端对应的策略类。

        Args:
            backend: 目标后端枚举。为 ``None`` 时自动调用
                ``detect_backend()`` 选择最优后端。也兼容传入 int 设备索引
                或 torch.device（历史调用方式），此时自动检测后端。

        Returns:
            对应的策略类。若指定后端未在注册表中注册，则发出
            ``logger.warning`` 并回退返回 ``_CPUStrategy``（保证上层不崩溃）。

        Raises:
            无显式抛出——未注册后端通过 warning + CPU 回退方式处理。
        """
        cls._ensure_strategies_initialized()
        # 参数自适应：非 GPUBackend 类型时自动检测后端
        if backend is not None and not isinstance(backend, GPUBackend):
            backend = None
        if backend is None:
            backend = cls.detect_backend()
        strategy = cls._strategies.get(backend)
        if strategy is None:
            try:
                backend_name = backend.value if hasattr(backend, "value") else str(backend)
            except Exception:
                backend_name = str(backend)
            logger.warning(
                f"[GPU Backend] 后端 {backend_name} 未注册策略，"
                f"自动回退到 CPU 策略。请通过 register_strategy() 注册对应实现。"
            )
            return _CPUStrategy
        return strategy

    @classmethod
    def clear_cache(cls) -> None:
        """清除后端检测缓存，下次调用 ``detect_backend()`` 将重新检测。"""
        cls._cached_backend = None

    @classmethod
    def is_available(cls, backend: GPUBackend | None = None) -> bool:
        """判断指定后端（或自动检测的最优后端）是否为 GPU 加速后端。

        Args:
            backend: 待检查的后端枚举。为 ``None`` 时自动调用
                ``detect_backend()`` 选择后端再判断。

        Returns:
            ``True`` 表示后端为 CUDA 或 MPS（GPU 加速），
            ``False`` 表示为 CPU（纯 CPU 计算）。
        """
        if backend is None:
            backend = cls.detect_backend()
        return backend != GPUBackend.CPU

    # ------------------------------------------------------------------
    # 策略派发方法（backend=None 时自动 detect）
    # ------------------------------------------------------------------

    @classmethod
    def get_device(cls, backend: GPUBackend | None = None, index: int = 0) -> torch.device | None:
        """获取指定后端与索引的设备对象。

        Args:
            backend: 目标后端枚举。为 ``None`` 时自动检测最优后端。
            index: 设备索引（从 0 开始）。默认值为 0。

        Returns:
            对应的 ``torch.device`` 对象。
        """
        strategy = cls.get_strategy(backend)
        return strategy.get_device(index)

    @classmethod
    def get_device_count(cls, backend: GPUBackend | None = None) -> int:
        """获取指定后端的可用设备数量。

        Args:
            backend: 目标后端枚举。为 ``None`` 时自动检测最优后端。

        Returns:
            可用设备数（CPU 返回 0）。
        """
        strategy = cls.get_strategy(backend)
        return strategy.get_device_count()

    @classmethod
    def get_device_name(cls, backend: GPUBackend | None = None, index: int = 0) -> str:
        """获取指定后端与索引的设备名称。

        Args:
            backend: 目标后端枚举。为 ``None`` 时自动检测最优后端。
            index: 设备索引。默认值为 0。

        Returns:
            设备名称字符串（如 "NVIDIA GeForce RTX 4090"）。
        """
        strategy = cls.get_strategy(backend)
        return strategy.get_device_name(index)

    @classmethod
    def get_device_properties(cls, backend: Any = None, index: int = 0) -> dict[str, Any]:
        """获取指定后端与索引的设备属性字典。

        Args:
            backend: 目标后端枚举（GPUBackend）。为 ``None`` 时自动检测最优后端。
                兼容历史调用方式：也可传入 int 设备索引或 torch.device 对象。
            index: 设备索引（仅当 backend 为 GPUBackend 枚举时有效）。默认值为 0。

        Returns:
            设备属性字典，通常包含 ``name``、``total_memory``、
            ``major``、``minor`` 等键。
        """
        resolved_backend, resolved_index, _ = cls._resolve_backend_and_index(backend, index)
        strategy = cls.get_strategy(resolved_backend)
        return strategy.get_device_properties(resolved_index)

    @classmethod
    def memory_allocated(
        cls,
        backend: Any = None,
        device: Any = None,
    ) -> int:
        """获取指定后端上当前已分配的显存字节数。

        Args:
            backend: 目标后端枚举（GPUBackend）。为 ``None`` 时自动检测最优后端。
                兼容历史调用方式：也可传入 int 设备索引或 torch.device 对象。
            device: 目标设备对象，为 ``None`` 时使用该后端的默认设备。
                当第一个参数传入 int/torch.device 时自动推断。

        Returns:
            已分配显存字节数（CPU/MPS 等不支持时返回 0）。
        """
        resolved_backend, _, resolved_device = cls._resolve_backend_and_index(backend, device_arg=device)
        strategy = cls.get_strategy(resolved_backend)
        return strategy.memory_allocated(resolved_device)

    @classmethod
    def memory_reserved(
        cls,
        backend: Any = None,
        device: Any = None,
    ) -> int:
        """获取指定后端上缓存分配器保留的显存字节数。

        Args:
            backend: 目标后端枚举（GPUBackend）。为 ``None`` 时自动检测最优后端。
                兼容历史调用方式：也可传入 int 设备索引或 torch.device 对象。
            device: 目标设备对象，为 ``None`` 时使用该后端的默认设备。
                当第一个参数传入 int/torch.device 时自动推断。

        Returns:
            保留显存字节数（CPU/MPS 等不支持时返回 0）。
        """
        resolved_backend, _, resolved_device = cls._resolve_backend_and_index(backend, device_arg=device)
        strategy = cls.get_strategy(resolved_backend)
        return strategy.memory_reserved(resolved_device)

    @classmethod
    def empty_cache(cls, backend: GPUBackend | None = None) -> None:
        """释放指定后端缓存分配器中未占用的内存。

        Args:
            backend: 目标后端枚举。为 ``None`` 时自动检测最优后端。
        """
        strategy = cls.get_strategy(backend)
        strategy.empty_cache()

    @classmethod
    def synchronize(
        cls,
        backend: GPUBackend | None = None,
        device: torch.device | None = None,
    ) -> None:
        """等待指定后端设备上所有流的计算完成。

        Args:
            backend: 目标后端枚举。为 ``None`` 时自动检测最优后端。
            device: 目标设备对象，为 ``None`` 时同步默认设备。
        """
        strategy = cls.get_strategy(backend)
        strategy.synchronize(device)

    @classmethod
    def get_memory_info(cls, backend: GPUBackend | None = None, index: int = 0) -> tuple[int, int, int, int]:
        """获取指定后端与索引设备的完整显存信息。

        Args:
            backend: 目标后端枚举。为 ``None`` 时自动检测最优后端。
            index: 设备索引。默认值为 0。

        Returns:
            四元组 ``(total, allocated, reserved, free)``，单位均为字节。
        """
        strategy = cls.get_strategy(backend)
        try:
            return strategy.get_memory_info(index)
        except Exception as e:
            logger.error(f"获取 GPU 显存信息失败 (backend={backend}, index={index}): {e}")
            return (0, 0, 0, 0)

    @classmethod
    def get_cuda_clear_workspaces_func(cls, backend: GPUBackend | None = None) -> Callable[[], None] | None:
        """获取指定后端的 cuBLAS 工作区清理函数（若存在）。

        Args:
            backend: 目标后端枚举。为 ``None`` 时自动检测最优后端。

        Returns:
            cuBLAS 工作区清理可调用对象，仅 CUDA 后端可能返回非 ``None`` 值。
        """
        strategy = cls.get_strategy(backend)
        return strategy.get_cuda_clear_workspaces_func()

    @classmethod
    def ipc_collect(
        cls,
        backend: GPUBackend | None = None,
        device: torch.device | None = None,
    ) -> None:
        """强制执行指定后端的 IPC 收集，释放多进程间闲置内存。

        Args:
            backend: 目标后端枚举。为 ``None`` 时自动检测最优后端。
            device: 目标设备对象，为 ``None`` 时对所有设备执行。
        """
        strategy = cls.get_strategy(backend)
        strategy.ipc_collect(device)

    @classmethod
    def get_grad_scaler(cls, backend: GPUBackend | None = None, enabled: bool = True) -> Any | None:
        """获取指定后端的梯度缩放器实例（若适用）。

        Args:
            backend: 目标后端枚举。为 ``None`` 时自动检测最优后端。
            enabled: 是否启用梯度缩放。为 ``False`` 时跳过创建直接返回 ``None``。

        Returns:
            梯度缩放器实例（通常为 ``torch.amp.GradScaler``），
            不支持时返回 ``None``。
        """
        strategy = cls.get_strategy(backend)
        return strategy.get_grad_scaler(enabled)

    @classmethod
    def get_autocast_device_type(cls, backend: GPUBackend | None = None) -> str:
        """获取指定后端用于 ``torch.amp.autocast`` 的 device_type 字符串。

        Args:
            backend: 目标后端枚举。为 ``None`` 时自动检测最优后端。

        Returns:
            autocast device_type 字符串（如 ``"cuda"``、``"cpu"``、``"xpu"``）。
        """
        strategy = cls.get_strategy(backend)
        return strategy.get_autocast_device_type()

    @classmethod
    def get_process_group_backend(cls, backend: GPUBackend | None = None) -> str:
        """获取指定后端推荐的分布式进程组后端字符串。

        Args:
            backend: 目标后端枚举。为 ``None`` 时自动检测最优后端。

        Returns:
            进程组后端字符串（如 ``"nccl"``、``"gloo"``、``"rccl"``、``"ccl"``）。
        """
        strategy = cls.get_strategy(backend)
        return strategy.get_process_group_backend()

    @classmethod
    def format_device_string(cls, backend: GPUBackend | None = None, index: int = 0) -> str:
        """格式化可直接用于 ``tensor.to()`` 的设备字符串。

        Args:
            backend: 目标后端枚举。为 ``None`` 时自动检测最优后端。
            index: 设备索引。默认值为 0。

        Returns:
            形如 ``"cuda:0"`` 的设备字符串。
        """
        strategy = cls.get_strategy(backend)
        return strategy.format_device_string(index)

    @classmethod
    def get_total_memory_gb(cls, backend: GPUBackend | None = None, index: int = 0) -> float:
        """获取指定后端与索引设备的总显存容量（GB 为单位，方便上层比较阈值）。

        Args:
            backend: 目标后端枚举。为 ``None`` 时自动检测最优后端。
            index: 设备索引。默认值为 0。

        Returns:
            以 GB 为单位的浮点显存容量（如 ``24.0`` 表示 24GB）。
            不支持显存查询的后端（CPU/MPS 等）返回 ``0.0``。
        """
        total_bytes, _allocated, _reserved, _free = cls.get_memory_info(backend, index)
        return total_bytes / (1024**3) if total_bytes > 0 else 0.0
