"""vLLM 加速后端 - 用于 TTS 推理的高性能 LLM 加速。

提供与 vLLM（https://github.com/vllm-project/vllm）的可选集成，
用于 TTS 流水线中语言模型组件的高吞吐量推理加速。

vLLM 通过以下技术加速 VoxCPM2 的语言模型组件：
  - PagedAttention 实现高效 KV 缓存管理
  - 连续批处理支持并发请求
  - 张量级并行支持多 GPU 配置
  - 优化的 CUDA 内核加速注意力计算

本模块作为轻量级适配层，负责：
  1. 运行时检测 vLLM 是否可用
  2. 提供标准推理与 vLLM 推理的统一接口
  3. 管理 vLLM 引擎生命周期（初始化、预热、关闭）
  4. 当 vLLM 不可用时优雅回退到标准 PyTorch 推理

使用示例：
    # 检查可用性
    from integrated_app.vllm_backend import is_vllm_available

    # 初始化（可选）
    from integrated_app.vllm_backend import get_vllm_backend
    backend = get_vllm_backend()
    if backend.is_available():
        backend.initialize(model_path="model/VoxCPM2")

    # 推理使用
    output = backend.generate(input_ids, sampling_params)

注意：
    vLLM 是可选依赖项，不安装也不影响项目正常运行。
    安装命令：pip install vllm>=0.6.0
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("tts_multimodel.vllm_backend")


def is_vllm_available() -> bool:
    """检查 vLLM 是否已安装并可导入。

    Returns:
        vLLM 可用返回 True，否则返回 False。
    """
    try:
        import vllm  # noqa: F401

        return True
    except ImportError:
        return False


@dataclass
class VLLMConfig:
    """vLLM 后端配置参数。

    Attributes:
        tensor_parallel_size: 张量并行度，用于多 GPU 推理。
        gpu_memory_utilization: GPU 显存利用率（0-1），默认 0.85。
        max_model_len: 模型最大序列长度，默认 4096。
        dtype: 计算精度，可选 "auto"、"float16"、"bfloat16"。
        enforce_eager: 是否禁用 CUDA Graph（调试用），默认 False。
        trust_remote_code: 是否信任远程代码，默认 True。
        enable_prefix_caching: 是否启用前缀缓存，默认 True。
        block_size: PagedAttention 块大小，默认 16。
        swap_space: CPU 交换空间大小（GiB），默认 4。
        disable_log_stats: 是否禁用统计日志，默认 True。
    """

    tensor_parallel_size: int = 1
    gpu_memory_utilization: float = 0.85
    max_model_len: int = 4096
    dtype: str = "auto"
    enforce_eager: bool = False
    trust_remote_code: bool = True
    enable_prefix_caching: bool = True
    block_size: int = 16
    swap_space: int = 4
    disable_log_stats: bool = True

    def to_vllm_kwargs(self) -> dict[str, Any]:
        """转换为 vLLM LLMEngine 构造函数的关键字参数。

        Returns:
            可直接传入 vLLM LLM 构造函数的参数字典。
        """
        return {
            "tensor_parallel_size": self.tensor_parallel_size,
            "gpu_memory_utilization": self.gpu_memory_utilization,
            "max_model_len": self.max_model_len,
            "dtype": self.dtype,
            "enforce_eager": self.enforce_eager,
            "trust_remote_code": self.trust_remote_code,
            "enable_prefix_caching": self.enable_prefix_caching,
            "block_size": self.block_size,
            "swap_space": self.swap_space,
            "disable_log_stats": self.disable_log_stats,
        }


@dataclass
class VLLMStatus:
    """vLLM 后端状态信息。

    Attributes:
        available: vLLM 库是否可用。
        initialized: 引擎是否已初始化。
        model_path: 加载的模型路径。
        engine_type: 引擎类型（"vllm" 或 "fallback"）。
        init_time_s: 初始化耗时（秒）。
        error: 错误信息（如有）。
        gpu_count: 使用的 GPU 数量。
        gpu_memory_gb: GPU 总显存（GB）。
    """

    available: bool = False
    initialized: bool = False
    model_path: str = ""
    engine_type: str = ""
    init_time_s: float = 0.0
    error: str = ""
    gpu_count: int = 0
    gpu_memory_gb: float = 0.0


class VLLMBackend:
    """vLLM 加速后端，支持自动回退到标准 PyTorch 推理。

    封装 vLLM 的 LLMEngine，提供：
    - 延迟初始化（仅在首次需要时创建引擎）
    - 自动回退到标准 PyTorch 推理
    - 线程安全的引擎访问
    - 健康监控和状态报告

    该后端不会替换现有推理流水线，而是作为以下条件满足时的
    加速选项使用：
    - vLLM 已安装
    - GPU 显存充足
    - 模型架构兼容
    """

    def __init__(self, config: VLLMConfig | None = None):
        """初始化 vLLM 后端实例。

        Args:
            config: vLLM 配置，默认使用 VLLMConfig 默认值。
        """
        self._config = config or VLLMConfig()
        self._engine: Any = None
        self._status = VLLMStatus()
        self._lock = threading.Lock()
        self._generation_count = 0

    @property
    def is_available(self) -> bool:
        """检查 vLLM 库是否已安装。

        Returns:
            vLLM 可用返回 True。
        """
        return is_vllm_available()

    @property
    def is_ready(self) -> bool:
        """检查引擎是否已初始化并就绪。

        Returns:
            引擎就绪返回 True。
        """
        return self._status.initialized and self._engine is not None

    @property
    def status(self) -> VLLMStatus:
        """获取当前后端状态。

        Returns:
            VLLMStatus 状态对象。
        """
        return self._status

    def initialize(self, model_path: str) -> bool:
        """初始化 vLLM 引擎。

        加载模型到 vLLM 引擎中，执行 KV 缓存内存分配和引擎预热。

        Args:
            model_path: 模型权重路径或 HuggingFace 模型 ID。

        Returns:
            初始化成功返回 True，失败返回 False。
        """
        with self._lock:
            if self._status.initialized:
                logger.info("[vLLM] 引擎已初始化")
                return True

            if not self.is_available:
                self._status.error = "vLLM 未安装"
                logger.warning("[vLLM] vLLM 未安装。安装命令: pip install vllm>=0.6.0")
                return False

            start_time = time.time()

            try:
                import vllm  # noqa: F401
                from vllm import LLM

                logger.info(f"[vLLM] 正在初始化引擎，模型: {model_path}")
                logger.info(
                    f"[vLLM] 配置: TP={self._config.tensor_parallel_size}, "
                    f"GPU显存={self._config.gpu_memory_utilization:.0%}, "
                    f"最大长度={self._config.max_model_len}"
                )

                engine_kwargs = self._config.to_vllm_kwargs()
                engine_kwargs["model"] = model_path
                self._engine = LLM(**engine_kwargs)

                self._status.available = True
                self._status.initialized = True
                self._status.model_path = model_path
                self._status.engine_type = "vllm"
                self._status.init_time_s = time.time() - start_time

                try:
                    import torch

                    if torch.cuda.is_available():
                        self._status.gpu_count = torch.cuda.device_count()
                        self._status.gpu_memory_gb = torch.cuda.get_device_properties(0).total_mem / (1024**3)
                except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）
                    pass

                logger.info(
                    f"[vLLM] 引擎初始化完成，耗时 {self._status.init_time_s:.1f}s "
                    f"(GPU数量: {self._status.gpu_count}, "
                    f"显存: {self._status.gpu_memory_gb:.1f}GB)"
                )
                return True

            except Exception as e:
                self._status.error = str(e)
                logger.error(f"[vLLM] 初始化失败: {e}")
                return False

    def generate(
        self,
        prompt: str | list[int],
        max_tokens: int = 2048,
        temperature: float = 0.8,
        top_p: float = 0.95,
        top_k: int = 50,
        stop: list[str] | None = None,
        **kwargs,
    ) -> str | None:
        """使用 vLLM 引擎生成文本。

        Args:
            prompt: 输入提示（字符串或 token ID 列表）。
            max_tokens: 最大生成 token 数，默认 2048。
            temperature: 采样温度，默认 0.8。
            top_p: Top-p（核采样）参数，默认 0.95。
            top_k: Top-k 采样参数，默认 50。
            stop: 停止序列列表。
            **kwargs: 其他采样参数。

        Returns:
            生成的文本字符串，引擎未就绪时返回 None。
        """
        if not self.is_ready:
            logger.warning("[vLLM] 引擎未就绪，回退到标准推理")
            return None

        try:
            from vllm import SamplingParams

            sampling_params = SamplingParams(
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                stop=stop or [],
                **kwargs,
            )

            outputs = self._engine.generate([prompt], sampling_params)
            self._generation_count += 1

            if outputs:
                return outputs[0].outputs[0].text
            return None

        except Exception as e:
            logger.error(f"[vLLM] 生成失败: {e}")
            return None

    def shutdown(self) -> None:
        """关闭 vLLM 引擎并释放资源。

        释放 GPU 显存，清空 CUDA 缓存。
        """
        with self._lock:
            if self._engine is not None:
                try:
                    del self._engine
                    self._engine = None
                except Exception as e:
                    logger.warning(f"[vLLM] 引擎关闭错误: {e}")

                try:
                    import torch

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except ImportError:
                    pass

            self._status.initialized = False
            self._status.engine_type = ""
            logger.info(f"[vLLM] 引擎已关闭。累计服务生成次数: {self._generation_count}")

    def get_stats(self) -> dict:
        """获取后端统计信息。

        Returns:
            包含可用性、初始化状态、模型路径、初始化耗时、
            生成次数、GPU 信息、错误信息的字典。
        """
        return {
            "available": self.is_available,
            "initialized": self._status.initialized,
            "engine_type": self._status.engine_type,
            "model_path": self._status.model_path,
            "init_time_s": round(self._status.init_time_s, 2),
            "generation_count": self._generation_count,
            "gpu_count": self._status.gpu_count,
            "gpu_memory_gb": round(self._status.gpu_memory_gb, 1),
            "error": self._status.error,
        }


# ============================================================================
# 模块级单例
# ============================================================================

_backend_instance: VLLMBackend | None = None
_backend_lock = threading.Lock()


def get_vllm_backend(config: VLLMConfig | None = None) -> VLLMBackend:
    """获取或创建 vLLM 后端单例。

    使用双重检查锁定模式确保线程安全的单例创建。

    Args:
        config: 可选配置，仅在首次调用时生效。

    Returns:
        VLLMBackend 单例实例。
    """
    global _backend_instance
    if _backend_instance is None:
        with _backend_lock:
            if _backend_instance is None:
                _backend_instance = VLLMBackend(config)
    return _backend_instance


def check_vllm_config_compatibility(model_path: str) -> dict:
    """检查模型是否兼容 vLLM 加速。

    通过检查模型目录中的 config.json 判断模型架构是否在
    vLLM 支持的架构列表中。

    Args:
        model_path: 模型目录路径。

    Returns:
        兼容性信息字典，包含：
        - compatible: bool，是否兼容
        - reason: str，兼容性说明
        - vllm_installed: bool，vLLM 是否已安装
        - model_path: str，模型路径
    """
    result = {
        "compatible": False,
        "reason": "",
        "vllm_installed": is_vllm_available(),
        "model_path": model_path,
    }

    if not result["vllm_installed"]:
        result["reason"] = "vLLM 未安装"
        return result

    if not os.path.isdir(model_path):
        result["reason"] = f"模型路径不存在: {model_path}"
        return result

    config_path = os.path.join(model_path, "config.json")
    if not os.path.exists(config_path):
        result["reason"] = "模型目录中未找到 config.json"
        return result

    try:
        import json

        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)

        architecture = config.get("architecture", "")
        supported_archs = {
            "LlamaForCausalLM",
            "MistralForCausalLM",
            "Qwen2ForCausalLM",
            "MiniCPMForCausalLM",
            "PhiForCausalLM",
        }

        if architecture in supported_archs:
            result["compatible"] = True
            result["reason"] = f"模型架构 '{architecture}' 受 vLLM 支持"
        else:
            result["reason"] = (
                f"模型架构 '{architecture}' 可能不被直接支持。支持的架构: {', '.join(sorted(supported_archs))}"
            )

    except Exception as e:
        result["reason"] = f"检查模型配置失败: {e}"

    return result


VLLM_DISABLED = os.environ.get("TTS_VLLM_DISABLED", "0") == "1"
"""环境变量，设置为 "1" 可禁用 vLLM（用于测试场景）。"""
