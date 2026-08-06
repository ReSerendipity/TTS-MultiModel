"""GPU 工具函数集：OOM 模式识别、分层显存清理、GPU 设备选择与显存预检。

本模块提供 GPU 生命周期管理的"策略与判断"层，与 `gpu_backend.py` 形成明确分工：
- **本模块 (gpu_utils.py)**：专注策略决策——识别 OOM 模式、决定何时升级清理层级、
  计算显存安全裕度、选择 GPU 设备策略、生成 VRAM 安全报告。
- **协作模块 (gpu_backend.py)**：专注后端 API 调用——封装 CUDA/MPS/CPU 的
  torch.cuda.* / torch.mps.* 具体实现，提供统一跨后端接口。

核心硬约束参考（来自 AGENTS.md §6 项目硬约束）：
1. **显存预检**：模型加载前可用显存需为模型大小的 **1.5 倍** 以上。
   裕度来源：模型权重本身 + KV Cache + 中间激活张量 + ASR/Enhancer 等辅助模型。
2. **内存熔断**：推理过程中显存占用超过 **90%** 时，必须立即终止推理并调用
   `free_gpu_memory()` 执行分层清理。

典型调用链：
- 推理异常捕获 → `is_oom_error(exc)` 判定 → `free_gpu_memory()` 清理 → 重试
- 模型加载前 → `GPUMemoryMonitor.check_vram_safety()` → 通过/拒绝
- 引擎切换时 → `get_gpu_device()` + `get_gpu_memory_info()` → 决策
"""

import contextlib
import gc
import logging
import time
from typing import Any

import torch

#: 模块级日志记录器，命名空间 "tts_multimodel"，统一输出到应用主日志
logger = logging.getLogger("tts_multimodel")


def is_oom_error(exc: BaseException) -> bool:
    """检测异常是否由 GPU 显存不足（OOM）引发。

    在以下场景被调用：
    - PyTorch 抛出 CUDA OOM RuntimeError 时
    - 本项目自定义 `InsufficientVRAMError` 抛出前的预检链路
    - 推理/加载过程中捕获通用异常后判断是否触发熔断

    OOM 模式覆盖两类来源：
    - **PyTorch 原生**：如 "CUDA out of memory"、"out of memory"、"oom"
      （为什么需要这些：不同 torch 版本、不同后端的报错文案不一致）
    - **项目自定义异常链**：如 "insufficient vram"、"insufficientvram"
      （为什么需要这些：model_manager.py 在显存预检失败时会抛出
       自定义异常，文案包含 insufficient vram 关键字，需一并识别后
       触发分层清理流程）
    - **Python 内置 OOM**：MemoryError 子类（极端情况下系统级内存不足）

    Args:
        exc: 待检测的异常对象，接受 BaseException 全范围（兼容自定义异常）。

    Returns:
        bool: 若异常匹配任一 OOM 模式返回 True，否则返回 False。
    """
    error_str = str(exc).lower()
    oom_patterns = [
        "cuda out of memory",
        "out of memory",
        "oom",
        "insufficient vram",
        "insufficientvram",
    ]
    for pattern in oom_patterns:
        if pattern in error_str:
            return True
    if isinstance(exc, RuntimeError):
        error_upper = str(exc).upper()
        if "CUDA" in error_upper and ("memory" in error_str or "alloc" in error_str):
            return True
    return bool(isinstance(exc, MemoryError))


def _log_tier_result(tier_name: str, duration: float) -> None:
    """分层清理完成日志输出的内部辅助函数。

    将重复的 logger.debug 调用统一封装，保持公开 API 行为不变的同时
    消除三处相同格式的日志字符串冗余。

    Args:
        tier_name: 层级名称（如 "Tier 1 (轻量)"）。
        duration: 该层级执行耗时（秒）。
    """
    logger.debug(f"[GPU清理] {tier_name} 完成，耗时 {duration:.3f}s")


def _has_sufficient_free_vram(threshold_mb: int = 500) -> bool:
    """检测当前 GPU 空闲显存是否达到阈值（内部辅助函数）。

    Args:
        threshold_mb: 空闲显存阈值（MB），默认 500MB。

    Returns:
        bool: 空闲显存 >= 阈值返回 True；检测失败或不满足返回 False。
    """
    try:
        from .gpu_backend import GPUBackendManager

        device = get_gpu_device()
        mem_info = GPUBackendManager.get_memory_info(device)
        free_bytes = mem_info[3]
        if free_bytes >= threshold_mb * 1024 * 1024:
            logger.info(f"[GPU清理] 当前空闲显存充足 ({free_bytes / 1024**2:.0f}MB)，跳过后续层级")
            return True
    except Exception:
        pass
    return False


def free_gpu_memory() -> None:
    """执行三层递进式 GPU 显存清理策略。

    三层设计原因：大多数情况下轻量清理（gc + empty_cache）即可满足需求，
    只有在重度生成后（批量/长文本/多轮克隆）才需要升级到更激进的清理手段。
    每层之间通过 500MB 空闲阈值决定是否升级——已够用则提前返回，避免
    不必要的性能开销（尤其是 synchronize 和 cuBLAS 清理会阻塞设备流）。

    **Tier 1 — 轻量清理 (Light)**：
        调用序列：gc.collect() → GPUBackendManager.empty_cache()
        触发条件：每次调用的默认首步，无前置判断。
        说明：回收 Python 端引用循环的张量 → 触发 PyTorch 缓存分配器
             释放未使用的缓存块。覆盖 80% 以上的日常清理场景。

    **Tier 2 — 中等清理 (Medium)**：
        调用序列：GPUBackendManager.synchronize(device) → torch.cuda.empty_cache()
        触发条件：Tier 1 后空闲显存 < 500MB。
        说明：先同步等待 GPU 端所有异步 kernel 执行完毕（确保引用计数
             真正归零），再额外执行一次 empty_cache 回收同步后
             新释放的块。

    **Tier 3 — 重度清理 (Heavy)**：
        调用序列：GPUBackendManager.get_cuda_clear_workspaces_func()()
                  → GPUBackendManager.ipc_collect(device)
                  → torch.cuda.empty_cache()
        触发条件：Tier 2 后空闲显存仍 < 500MB。
        Why clearCublasWorkspaces：cuBLAS 在首次调用 GEMM 时会分配
             数百 MB 的持久化工作空间（Workspace），即便相关张量已被
             释放，该工作空间仍会持留于缓存池中不归还。重度批量生成
             （如剧本工坊、多角色批量克隆）后必须显式调用
             `torch.cuda.cublas.clearCublasWorkspaces()` 才能彻底回收。
             多进程场景下的 IPC 句柄（ipc_collect）同理，会残留共享
             内存映射，需要主动释放。

    异常隔离：每个 Tier、每层 GPU API 调用均使用独立 try/except，
    单步失败不跳过其他清理步骤（如 synchronize 抛错不影响后续
    empty_cache）。

    Returns:
        None
    """
    from .gpu_backend import GPUBackend, GPUBackendManager

    backend = GPUBackendManager.detect_backend()
    is_gpu = backend == GPUBackend.CUDA

    t0 = time.time()
    tier1_time = 0.0
    tier2_time = 0.0
    tier3_time = 0.0

    # --- Tier 1: Lightweight ---
    t_start = time.time()
    try:
        gc.collect()
    except Exception as e:
        logger.debug(f"[GPU清理] Tier 1 gc.collect() 异常（不影响后续）: {e}")
    if is_gpu:
        try:
            GPUBackendManager.empty_cache()
        except Exception as e:
            logger.debug(f"[GPU清理] Tier 1 empty_cache() 异常（不影响后续）: {e}")
    tier1_time = time.time() - t_start
    _log_tier_result("Tier 1 (轻量)", tier1_time)

    if is_gpu and _has_sufficient_free_vram(500):
        total_time = time.time() - t0
        logger.info(f"[GPU清理] 分层清理完成（Tier 1 即达阈值），总耗时 {total_time:.3f}s")
        return

    # --- Tier 2: Medium ---
    t_start = time.time()
    if is_gpu:
        try:
            device = get_gpu_device()
            if device is not None:
                try:
                    GPUBackendManager.synchronize(device)
                except Exception as e:
                    logger.debug(f"[GPU清理] Tier 2 synchronize() 异常（不影响后续）: {e}")
        except Exception as e:
            logger.debug(f"[GPU清理] Tier 2 get_gpu_device() 异常（不影响后续）: {e}")
        try:
            torch.cuda.empty_cache()
        except Exception as e:
            logger.debug(f"[GPU清理] Tier 2 empty_cache() 异常（不影响后续）: {e}")
    tier2_time = time.time() - t_start
    _log_tier_result("Tier 2 (中等)", tier2_time)

    if is_gpu and _has_sufficient_free_vram(500):
        total_time = time.time() - t0
        logger.info(f"[GPU清理] 分层清理完成（Tier 2 即达阈值），总耗时 {total_time:.3f}s")
        return

    # --- Tier 3: Heavy ---
    t_start = time.time()
    if is_gpu:
        # clearCublasWorkspaces: cuBLAS 持留的数百 MB 工作空间需显式清理
        # （Why: 重度批量生成后 cuBLAS workspace 不会被 empty_cache 回收）
        try:
            clear_func = GPUBackendManager.get_cuda_clear_workspaces_func()
            if clear_func:
                with contextlib.suppress(Exception):
                    clear_func()
        except Exception as e:
            logger.debug(f"[GPU清理] Tier 3 clearCublasWorkspaces() 异常（不影响后续）: {e}")

        try:
            device = get_gpu_device()
            if device is not None:
                try:
                    GPUBackendManager.ipc_collect(device)
                except Exception as e:
                    logger.debug(f"[GPU清理] Tier 3 ipc_collect() 异常（不影响后续）: {e}")
        except Exception as e:
            logger.debug(f"[GPU清理] Tier 3 get_gpu_device() 异常（不影响后续）: {e}")

        try:
            torch.cuda.empty_cache()
        except Exception as e:
            logger.debug(f"[GPU清理] Tier 3 empty_cache() 异常（不影响后续）: {e}")
    tier3_time = time.time() - t_start
    _log_tier_result("Tier 3 (重度)", tier3_time)

    total_time = time.time() - t0
    logger.info(
        f"[GPU清理] 分层清理完成，总耗时 {total_time:.3f}s "
        f"(T1={tier1_time:.3f}s, T2={tier2_time:.3f}s, T3={tier3_time:.3f}s)"
    )


def get_gpu_device() -> int | None:
    """选择可用的 GPU 设备索引（非最大显存策略）。

    策略说明：本函数返回**第一个可用的 CUDA GPU**，而非显存最大的 GPU。
    Why 选第一个而不是选显存最大：绝大多数 TTS 用户的机器为**单卡配置**，
    遍历检测第一块卡即可。多卡场景下（如工作站/服务器）用户通常通过
    环境变量 `CUDA_VISIBLE_DEVICES` 预先限定可见设备，此时 torch 枚举的
    第 0 块即为用户期望使用的卡。若确需基于显存/负载动态调度，应由上层
    调度模块（非本工具函数）自行实现复杂策略。

    Apple MPS 后端统一返回索引 0（MPS 无多设备概念）。
    CPU 后端或无可用 GPU 时返回 None，表示调用方应回退至 CPU 推理。

    异常细化：原先使用通用 `Exception` 捕获 `get_device_properties`，
    现细化为 `RuntimeError`（CUDA 驱动异常/设备被占用）和 `AssertionError`
    （torch 内部断言失败）两类，保留 debug 日志便于排障，其他异常则
    向上传播（避免静默吞掉真正严重的错误）。

    Returns:
        Optional[int]: 可用 GPU 的索引（整数，从 0 开始）；无可有 GPU
        时返回 None（代表 CPU 模式）。
    """
    from .gpu_backend import GPUBackend, GPUBackendManager

    backend = GPUBackendManager.detect_backend()

    if backend == GPUBackend.CPU:
        return None

    if backend == GPUBackend.CUDA:
        if not torch.cuda.is_available():
            return None

        for i in range(torch.cuda.device_count()):
            try:
                torch.cuda.get_device_properties(i)
                return i
            except RuntimeError as e:
                logger.debug(f"无法获取 GPU {i} 信息 (RuntimeError - 驱动/占用异常): {e}")
            except AssertionError as e:
                logger.debug(f"无法获取 GPU {i} 信息 (AssertionError - torch 内部断言): {e}")

        return None

    elif backend == GPUBackend.MPS:
        return 0

    return None


def get_gpu_memory_info() -> tuple[int, int, int, int]:
    """查询主 GPU 的显存使用信息（四元组）。

    内部委托给 `GPUBackendManager.get_memory_info(device)`，由后端模块
    负责 CUDA/MPS/CPU 的差异化实现。本函数的存在意义是为上层调用方
    提供稳定的、无需显式导入 gpu_backend 的统一入口。

    Returns:
        tuple[int, int, int, int]: 四元组，单位均为 bytes：
            - [0] total_bytes:     GPU 物理显存总容量
            - [1] allocated_bytes: torch 当前已分配给张量的显存
                                   （= 实际正在使用的张量大小之和）
            - [2] reserved_bytes:  torch 缓存分配器从 CUDA 申请的总显存
                                   （= allocated + 缓存池中空闲块）
            - [3] free_bytes:      GPU 上尚未被 torch 占用的空闲显存
                                   （= total - reserved - 驱动/其他进程占用）
        若 GPU 不可用或查询失败，返回 (0, 0, 0, 0) 而不是抛出异常，
        便于调用方直接解包而无需 try/except。
    """
    from .gpu_backend import GPUBackendManager

    try:
        device = get_gpu_device()
        return GPUBackendManager.get_memory_info(device)
    except Exception as e:
        logger.debug(f"[GPU显存] 查询失败，返回全 0 四元组: {e}")
        return (0, 0, 0, 0)


class GPUMemoryMonitor:
    """GPU VRAM 监控与容量预检的静态工具类。

    Why 设计为静态工具类（而非实例类）：本类无任何实例状态，所有方法
    的输入仅依赖于当前 GPU 实时状态和全局配置（ENGINE_VRAM_REQUIREMENTS）。
    设计为静态类可让任何线程、任何模块在任意时刻直接调用，无需传递
    monitor 对象句柄，也避免了多线程下的实例生命周期与状态一致性问题。

    典型调用场景：
    - 模型加载前：`GPUMemoryMonitor.check_vram_safety("voxcpm2")` 生成安全报告
    - 健康监控：`GPUMemoryMonitor.get_vram_info()` 采集当前使用率
    - 引擎切换预检：`GPUMemoryMonitor.can_load_model("indextts2")`
    """

    @staticmethod
    def get_vram_info() -> dict[str, int]:
        """查询当前 GPU VRAM 使用统计（字典形式）。

        基于 `get_gpu_memory_info()` 的四元组结果进行字典化封装，
        提供语义化 key 便于上层直接使用。对四元组解包长度异常进行
        try/except 保护，若解包失败返回全 0 字典（保证调用方总能
        拿到结构正确的 dict，不会因解构异常中断主流程）。

        Returns:
            dict[str, int]: 结构如下（单位 bytes）：
                {
                    "total": 总显存容量,
                    "used":  已分配给张量的显存 (= allocated),
                    "free":  空闲显存
                }
            GPU 不可用时返回全 0 字典。
        """
        try:
            total, allocated, reserved, free = get_gpu_memory_info()
        except (ValueError, TypeError) as e:
            logger.debug(f"[GPU显存] 四元组解包异常，返回全 0 字典: {e}")
            return {"total": 0, "used": 0, "free": 0}
        return {"total": total, "used": allocated, "free": free}

    @staticmethod
    def can_load_model(model_name: str = "voxcpm2") -> tuple[bool, int]:
        """检查当前空闲 VRAM 是否足以加载指定模型（含 1.5 倍安全裕度）。

        安全裕度说明（Why 1.5 倍）：TTS 推理的实际显存开销远不止模型权重
        本身，通常还包含：
        - KV Cache（自回归解码时的缓存，长度与 token 数成正比）
        - 中间激活张量（Attention、FFN 层的前向临时结果）
        - ASR/Enhancer 等辅助模型（克隆模式下的参考音频特征提取）
        因此需要模型权重大小的 1.5 倍作为安全裕度，避免加载成功但
        首次推理即 OOM 的尴尬场景。

        显存需求来源（Why 不硬编码 6.5GB）：使用
        `model_registry.ENGINE_VRAM_REQUIREMENTS` 字典动态查询，而非
        在本模块硬编码。原因是：当后续新增引擎（如 SqueezeTTS、VITS）
        时，只需在 config.yaml 中声明 vram_gb 规格并由 model_registry
        同步到 ENGINE_VRAM_REQUIREMENTS 字典即可，本模块无需修改，
        实现自动适配。若字典中找不到对应 key，回退默认 6.5GB。

        Args:
            model_name: 引擎/模型标识符，默认 "voxcpm2"。
                常见取值："voxcpm2"（VoxCPM 2.x 引擎，6.5GB）、
                "indextts2"（IndexTTS 2 情感引擎，6.0GB）。

        Returns:
            tuple[bool, int]:
                - [0] bool: 是否满足 1.5 倍安全裕度的加载条件。
                - [1] int:  当前实际空闲显存（bytes），供调用方日志输出。
        """
        from .model_registry import ENGINE_VRAM_REQUIREMENTS

        info = GPUMemoryMonitor.get_vram_info()
        needed_gb = ENGINE_VRAM_REQUIREMENTS.get(model_name, 6.5)
        # 1.5 倍安全裕度：权重 + KV cache + 中间激活 + 辅助模型
        needed = int(needed_gb * 1024**3 * 1.5)
        return info["free"] >= needed, info["free"]

    @staticmethod
    def check_vram_safety(model_name: str = "voxcpm2") -> dict[str, Any]:
        """生成指定模型加载的 VRAM 安全评估报告（结构化字典）。

        相比 `can_load_model()` 的简单二元判断，本方法返回更丰富的
        诊断信息，供 UI 展示、日志审计、或熔断决策使用。

        warning_level 分级说明：
            - "safe"     ：空闲显存 ≥ 1.5 倍需求，加载与推理均有充足裕度。
            - "warning"  ：1.2 倍需求 ≤ 空闲 < 1.5 倍。可能加载成功，
                           但长文本/克隆场景下推理阶段大概率 OOM，
                           建议先清理或切换到小模型。
            - "danger"   ：空闲 < 1.2 倍需求。加载阶段即高概率 OOM，
                           应直接拒绝加载或先强制分层清理。
            - "no_gpu"   ：无可检测 GPU（CPU 模式），由调用方决定是否
                           允许回退 CPU 推理。

        Args:
            model_name: 引擎/模型标识符，默认 "voxcpm2"。

        Returns:
            dict[str, Any]: 安全报告结构：
                {
                    "free_bytes":     int,   # 当前空闲显存（bytes）
                    "needed_bytes":   int,   # 1.5 倍安全裕度下的需求显存（bytes）
                    "has_headroom":   bool,  # 是否满足 1.5 倍安全裕度（= can_load_model 的第 0 项）
                    "warning_level":  str,   # "safe" | "warning" | "danger" | "no_gpu"
                    "suggestion":     str    # 中文可读的建议文案，供前端直接展示
                }
        """
        from .model_registry import ENGINE_VRAM_REQUIREMENTS

        info = GPUMemoryMonitor.get_vram_info()
        total = info["total"]
        free = info["free"]

        report: dict[str, Any] = {
            "free_bytes": free,
            "needed_bytes": 0,
            "has_headroom": False,
            "warning_level": "no_gpu",
            "suggestion": "未检测到可用 GPU，可尝试使用 CPU 模式推理（速度较慢）。",
        }

        if total == 0 and free == 0:
            return report

        needed_gb = ENGINE_VRAM_REQUIREMENTS.get(model_name, 6.5)
        # 1.5 倍安全裕度：权重 + KV cache + 中间激活 + 辅助模型
        needed_strict = int(needed_gb * 1024**3 * 1.5)
        needed_soft = int(needed_gb * 1024**3 * 1.2)

        report["needed_bytes"] = needed_strict

        if free >= needed_strict:
            report["has_headroom"] = True
            report["warning_level"] = "safe"
            report["suggestion"] = (
                f"显存充足：空闲 {free / 1024**3:.2f}GB ≥ 需求 "
                f"{needed_strict / 1024**3:.2f}GB（1.5x），可安全加载 {model_name}。"
            )
        elif free >= needed_soft:
            report["has_headroom"] = False
            report["warning_level"] = "warning"
            report["suggestion"] = (
                f"显存紧张：空闲 {free / 1024**3:.2f}GB，推荐裕度 "
                f"{needed_strict / 1024**3:.2f}GB（1.5x）。建议先执行显存清理，"
                f"或避免同时启用 ASR/Enhancer 等辅助模块。"
            )
        else:
            report["has_headroom"] = False
            report["warning_level"] = "danger"
            report["suggestion"] = (
                f"显存严重不足：空闲 {free / 1024**3:.2f}GB < 最低建议 "
                f"{needed_soft / 1024**3:.2f}GB（1.2x）。请先调用显存清理、"
                f"卸载当前已加载模型，或切换至更小规格的引擎。"
            )

        return report
