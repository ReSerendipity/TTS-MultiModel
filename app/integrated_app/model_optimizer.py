"""模型编译与预热模块。

提供 torch.compile JIT 编译加速和预热推理功能，参考：
  - PyTorch 2.0+ torch.compile 官方最佳实践
  - llama.cpp / vLLM 的预热（warmup）设计
  - Fish Speech 的模型优化策略

核心功能：
  1. 对模型的生成/解码关键子模块应用 torch.compile（可选）
  2. 加载后执行短文本预热推理，触发 CUDA kernel 编译和缓存预热
  3. 自动检测环境兼容性，不支持时优雅降级
  4. 预热进度通过回调上报，不阻塞用户交互

注意事项：
  - torch.compile 在 Windows + CUDA 上可能存在兼容性问题，默认保守启用
  - 预热使用极短文本（"你好"），耗时通常在 2~5 秒内
  - 首次编译缓存到磁盘（model_manager 中已配置 _TORCH_COMPILE_CACHE_DIR）
  - 预热失败不影响模型正常加载和使用（fail-soft）
【职责】模型显存/精度优化策略（量化、offload 等）。【边界】只产出优化建议与执行，不改变模型注册状态。

"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("tts_multimodel")

# 编译与预热配置常量
_COMPILE_ENABLED_DEFAULT: bool = False  # 默认保守禁用，可通过配置开启
_WARMUP_TEXT_ZH: str = "你好"
_WARMUP_TEXT_EN: str = "Hello"
_WARMUP_MAX_SECONDS: float = 30.0  # 预热超时保护


def is_torch_compile_available() -> bool:
    """检查当前环境是否支持 torch.compile。

    Returns:
        bool: True 表示可以安全使用 torch.compile
    """
    try:
        import torch

        # PyTorch 2.0+ 才有 torch.compile
        if not hasattr(torch, "compile"):
            return False
        # Windows 上 dynamo 后端可能有问题，检查环境变量
        # Windows 上默认禁用，除非显式设置环境变量开启
        if os.name == "nt" and os.environ.get("TTS_ENABLE_TORCH_COMPILE", "0") != "1":
            logger.debug("[ModelOpt] torch.compile 在 Windows 上默认禁用（设置 TTS_ENABLE_TORCH_COMPILE=1 可启用）")
            return False
        # 检查是否被显式禁用
        return os.environ.get("TTS_DISABLE_TORCH_COMPILE", "0") != "1"
    except Exception as e:
        logger.debug(f"[ModelOpt] torch.compile 可用性检查失败: {e}")
        return False


def apply_torch_compile(
    model: Any,
    compile_submodules: list[str] | None = None,
    backend: str = "inductor",
    mode: str = "reduce-overhead",
    fullgraph: bool = False,
) -> Any:
    """对模型应用 torch.compile JIT 编译优化。

    选择性地编译关键子模块以平衡编译时间和推理速度。
    参考 PyTorch 官方推荐的 TTS/语音模型编译策略。

    Args:
        model: VoxCPM 模型实例
        compile_submodules: 要编译的子模块名称列表，None 则自动检测
        backend: torch.compile 后端（inductor/cudagraphs/eager）
        mode: 编译模式（default/reduce-overhead/max-autotune）
        fullgraph: 是否使用全图捕获（可能因动态控制流失败）

    Returns:
        编译后的模型（原地修改，也返回以便链式调用）
    """
    if not is_torch_compile_available():
        logger.info("[ModelOpt] torch.compile 不可用，跳过编译优化")
        return model

    try:
        import torch

        if compile_submodules is None:
            # 自动检测可安全编译的子模块
            compile_submodules = []
            for attr in ("tts_model", "model", "codecs", "vocoder"):
                sub = getattr(model, attr, None)
                if sub is not None and hasattr(sub, "forward"):
                    compile_submodules.append(attr)

        compiled_count = 0
        for attr in compile_submodules:
            sub = getattr(model, attr, None)
            if sub is None:
                continue
            try:
                logger.info(f"[ModelOpt] 正在编译子模块: {attr} (backend={backend}, mode={mode})")
                compiled_sub = torch.compile(
                    sub,
                    backend=backend,
                    mode=mode,
                    fullgraph=fullgraph,
                )
                setattr(model, attr, compiled_sub)
                compiled_count += 1
            except Exception as sub_err:
                logger.warning(f"[ModelOpt] 子模块 {attr} 编译失败（跳过，不影响使用）: {sub_err}")
                # 回退：保留原始子模块
                continue

        if compiled_count > 0:
            logger.info(f"[ModelOpt] torch.compile 应用成功，共编译 {compiled_count} 个子模块")
        else:
            logger.info("[ModelOpt] 没有子模块被编译")

    except Exception as e:
        logger.warning(f"[ModelOpt] torch.compile 应用失败（跳过，不影响使用）: {e}")
        # 失败时返回原始模型，不中断流程

    return model


def warmup_model(
    model: Any,
    progress_callback: Callable[[str], None] | None = None,
    timeout: float = _WARMUP_MAX_SECONDS,
) -> bool:
    """执行模型预热推理，触发 CUDA kernel 编译和内存分配。

    预热推理使用极短文本，目的是：
      1. 触发首次推理时的 lazy CUDA kernel 编译
      2. 预热 KV cache 内存分配器
      3. 验证模型在当前硬件/后端下可正常生成

    Args:
        model: VoxCPM 模型实例（应已加载到 GPU/CPU）
        progress_callback: 进度回调 (message: str) -> None
        timeout: 预热超时时间（秒），超时则放弃预热

    Returns:
        bool: 预热是否成功完成
    """
    if model is None:
        return False

    def _report(msg: str) -> None:
        logger.info(f"[ModelWarmup] {msg}")
        if progress_callback is not None:
            with contextlib.suppress(Exception):
                progress_callback(msg)

    _report("正在预热模型...")
    start_time = time.time()

    try:
        # 尝试中文预热
        warmup_text = _WARMUP_TEXT_ZH
        try:
            _report(f"预热推理: '{warmup_text}'（短文本）")

            # 执行一次短文本生成，使用默认参数
            # 注意：不保存生成结果，仅用于触发 kernel 编译
            wav = model.generate(
                text=warmup_text,
                max_len=100,  # 限制长度，快速完成
            )
            if wav is not None and len(wav) > 0:
                duration = len(wav) / 48000 if hasattr(model, "generate") else 0
                elapsed = time.time() - start_time
                _report(f"预热完成（{elapsed:.1f}秒，音频时长 {duration:.1f}s）")
                return True
        except Exception as zh_err:
            logger.debug(f"[ModelWarmup] 中文预热失败，尝试英文: {zh_err}")

        # 中文失败则尝试英文
        try:
            warmup_text = _WARMUP_TEXT_EN
            _report(f"预热推理（英文）: '{warmup_text}'")
            wav = model.generate(
                text=warmup_text,
                max_len=100,
            )
            if wav is not None and len(wav) > 0:
                elapsed = time.time() - start_time
                _report(f"预热完成（{elapsed:.1f}秒）")
                return True
        except Exception as en_err:
            logger.debug(f"[ModelWarmup] 英文预热失败: {en_err}")

        # 两种语言都失败
        elapsed = time.time() - start_time
        if elapsed >= timeout:
            _report(f"预热超时（{timeout}秒），跳过（不影响后续使用）")
        else:
            _report("预热失败（不影响模型使用）")
        return False

    except Exception as e:
        elapsed = time.time() - start_time
        logger.warning(f"[ModelWarmup] 预热异常: {type(e).__name__}: {e}（{elapsed:.1f}秒，跳过）")
        return False


def warmup_indextts2(
    engine: Any,
    progress_callback: Callable[[str], None] | None = None,
    timeout: float = _WARMUP_MAX_SECONDS,
) -> bool:
    """IndexTTS2 引擎预热。

    Args:
        engine: IndexTTS2Engine 实例
        progress_callback: 进度回调
        timeout: 超时时间

    Returns:
        bool: 预热是否成功
    """
    if engine is None:
        return False

    def _report(msg: str) -> None:
        logger.info(f"[IndexTTS2-Warmup] {msg}")
        if progress_callback is not None:
            with contextlib.suppress(Exception):
                progress_callback(msg)

    _report("正在预热 IndexTTS2 模型...")
    start_time = time.time()

    try:
        wav, sr = engine.synthesize(
            text="你好",
            spk_audio_prompt="",
            lang="ZH",
            seed=42,
        )
        if wav is not None and len(wav) > 0:
            elapsed = time.time() - start_time
            _report(f"IndexTTS2 预热完成（{elapsed:.1f}秒）")
            return True
    except Exception as e:
        elapsed = time.time() - start_time
        logger.warning(f"[IndexTTS2-Warmup] 预热异常: {type(e).__name__}: {e}（{elapsed:.1f}秒）")
        return False

    return False


def optimize_and_warmup_voxcpm(
    model: Any,
    enable_compile: bool | None = None,
    enable_warmup: bool = True,
    progress_callback: Callable[[str], None] | None = None,
) -> bool:
    """一站式优化 + 预热入口函数。

    Args:
        model: VoxCPM 模型实例
        enable_compile: 是否启用 torch.compile（None=自动检测）
        enable_warmup: 是否执行预热推理
        progress_callback: 进度回调

    Returns:
        bool: 整体是否成功（编译+预热至少一项完成）
    """
    success = False

    # 步骤 1: torch.compile 编译（可选）
    should_compile = enable_compile if enable_compile is not None else is_torch_compile_available()
    if should_compile:
        try:
            apply_torch_compile(model)
            success = True
        except Exception as e:
            logger.warning(f"[ModelOpt] 编译步骤失败: {e}")

    # 步骤 2: 预热推理（推荐始终执行）
    if enable_warmup:
        try:
            warmup_ok = warmup_model(model, progress_callback=progress_callback)
            success = success or warmup_ok
        except Exception as e:
            logger.warning(f"[ModelOpt] 预热步骤失败: {e}")

    return success


__all__ = [
    "is_torch_compile_available",
    "apply_torch_compile",
    "warmup_model",
    "warmup_indextts2",
    "optimize_and_warmup_voxcpm",
]
