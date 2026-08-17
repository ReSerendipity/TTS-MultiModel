"""model_manager_core.load — 模型加载职责（M-R7 拆分，2026-08-17）。

包含 PersonaWarmupService / warmup_persona_cache、load_voxcpm2 /
_do_load_voxcpm2_internal / load_indextts2、PreloadService / preload_model /
get_preload_status。
【边界】不做卸载（unload.py）；不做引擎切换（switch.py）。
"""
from .state import *
from . import state as _state

def get_persona_cache_stats() -> dict[str, Any]:
    """获取当前 Persona 嵌入缓存的统计信息。

    Retrieve current persona embedding cache statistics.

    Returns:
        dict[str, Any]: 缓存统计字典，结构如下：
            - hits (int): 缓存命中总次数
            - misses (int): 缓存未命中总次数
            - hit_rate (float): 命中率 (0.0 ~ 1.0)
            - size (int): 当前缓存条目数
            - maxsize (int): 缓存最大容量
    """
    return _persona_embedding_cache.get_stats()


# ====================================================================
# Persona 预热服务 (M-R2/R5)
# ====================================================================


class PersonaWarmupService:
    """Persona 缓存预热服务（设计文档 M-R2/M-R5）。

    REFACTOR: [M-R2] Persona 缓存预热服务，封装状态与行为。
    M-R5: 新增 retry_warmup() 与 reset()，支持失败后重试与引擎切换后强制重新预热。

    设计意图：
        应用启动时后台异步预热最近使用的音色嵌入到 AdaptiveLRUCache 中，
        避免首次请求时才加载嵌入造成用户感知延迟。预热过程对启动线程非阻塞。

    线程安全：
        所有状态变更（_state 字典读写）均通过 ``_lock`` 保护，
        支持多线程并发调用 warmup/retry_warmup/reset/get_status。

    Attributes:
        _cache (AdaptiveLRUCache): 预热目标缓存实例，通常为全局单例
            ``_persona_embedding_cache``。
        _state (dict[str, Any]): 预热状态字典，包含键：
            - done (bool): 是否已成功完成预热。
            - error (str | None): 上次失败的错误消息，成功或未开始为 None。
            - in_progress (bool): 是否有后台线程正在执行预热。
        _lock (threading.Lock): 保护 ``_state`` 读写的互斥锁。
    """

    def __init__(self, cache: AdaptiveLRUCache) -> None:
        """初始化 PersonaWarmupService。

        Args:
            cache (AdaptiveLRUCache): 预热目标缓存实例。
        """
        self._cache: AdaptiveLRUCache = cache
        self._state: dict[str, Any] = {"done": False, "error": None, "in_progress": False}
        self._lock: threading.Lock = threading.Lock()

    def warmup(self) -> None:
        """异步预热最近使用的 Persona 嵌入到缓存。

        Asynchronously preload the most recently used personas into cache.

        启动一个守护线程在后台执行实际预热逻辑，避免阻塞应用启动或调用方。
        若已完成预热（``_state["done"] is True``）或已有预热线程在执行
        （``_state["in_progress"] is True``），则直接返回不重复启动。

        M-R5 语义：
            失败时 ``done`` 保持 ``False`` 并记录 ``error``，允许后续
            通过 :meth:`retry_warmup` 重试。

        后台线程行为：
            1. 扫描 ``PERSONA_DIR`` 下所有 ``.wav`` 文件，按修改时间倒序
               取前 ``_WARMUP_TOP_PERSONAS`` 个作为预热目标。
            2. 每个音色独立调用 ``load_persona_embedding(name)``，单个音色
               加载失败仅记录 debug 日志，不影响其他音色继续预热。
            3. 全部音色处理完成后标记 ``done=True`` 并记录最终缓存大小。
        """
        with self._lock:
            if self._state["done"] or self._state["in_progress"]:
                return
            self._state["in_progress"] = True

        def _do_warmup() -> None:
            from .middleware.request_id import set_request_id

            set_request_id(f"bg-{threading.current_thread().name}")
            try:
                from ..config import PERSONA_DIR
                from ..persona_manager import load_persona_embedding

                persona_files: list[tuple[str, float]] = []
                if os.path.isdir(PERSONA_DIR):
                    for f in os.listdir(PERSONA_DIR):
                        if f.endswith(".wav"):
                            full_path = os.path.join(PERSONA_DIR, f)
                            try:
                                mtime = os.path.getmtime(full_path)
                                persona_files.append((f[:-4], mtime))
                            except (OSError, PermissionError) as stat_err:
                                logger.debug(f"[PersonaCacheWarmup] 读取音色 mtime 失败 {full_path}: {stat_err}")

                persona_files.sort(key=lambda x: x[1], reverse=True)
                top_personas = [name for name, _ in persona_files[:_WARMUP_TOP_PERSONAS]]
                if not top_personas:
                    logger.info("[PersonaCacheWarmup] 未找到音色，跳过预热")
                    with self._lock:
                        self._state["done"] = True
                        self._state["in_progress"] = False
                    return

                logger.info(f"[PersonaCacheWarmup] 开始预热 {len(top_personas)} 个音色: {top_personas}")
                for name in top_personas:
                    try:
                        load_persona_embedding(name)
                        logger.debug(f"[PersonaCacheWarmup] 已预热音色: {name}")
                    except Exception as single_err:
                        logger.debug(
                            f"[PersonaCacheWarmup] 音色 '{name}' 预热失败 (单个失败，不影响其他): {single_err}"
                        )

                stats = self._cache.get_stats()
                logger.info(f"[PersonaCacheWarmup] 预热完成。缓存大小: {stats['size']}/{stats['maxsize']}")
                # M-R5: 成功完成才标记 done=True
                with self._lock:
                    self._state["done"] = True
                    self._state["error"] = None
                    self._state["in_progress"] = False
            except Exception as e:
                logger.error(f"[PersonaCacheWarmup] 预热失败: {e}")
                # M-R5: 失败时 done 保持 False，记录 error，允许重试
                with self._lock:
                    self._state["error"] = str(e)
                    self._state["in_progress"] = False

        t = threading.Thread(target=_do_warmup, daemon=True, name="persona-cache-warmup")
        t.start()

    def retry_warmup(self) -> bool:
        """重试预热（M-R5 新增）。

        REFACTOR: [M-R5] 重试预热入口。

        当上次预热失败（``_state["error"] is not None`` 且 ``done is False``）
        时，清理错误状态并再次调用 :meth:`warmup` 启动新的预热线程。

        Returns:
            bool: 若实际触发了新的预热返回 ``True``；若已完成或正在进行
            无需重试则返回 ``False``。
        """
        with self._lock:
            if self._state["done"]:
                return False
            if self._state["in_progress"]:
                logger.info("[PersonaCacheWarmup] 预热正在进行中，跳过重试")
                return False
            # 清理上次错误状态，允许重新触发
            self._state["error"] = None
            logger.info("[PersonaCacheWarmup] 触发重试预热")
        self.warmup()
        return True

    def reset(self) -> None:
        """重置预热状态（M-R5/E6-2 新增）。

        REFACTOR: [M-R5/E6-2] 重置预热状态。

        典型使用场景：引擎切换、模型重新加载后，强制将 ``done`` 置回
        ``False``，以便下次 :meth:`warmup` 或 :meth:`retry_warmup`
        能重新预热适配新引擎的音色嵌入。

        不会清除已加载的缓存条目，仅重置状态机。
        """
        with self._lock:
            self._state = {"done": False, "error": None, "in_progress": False}
        logger.info("[PersonaCacheWarmup] 状态已重置")

    def get_status(self) -> dict[str, Any]:
        """获取预热状态的快照副本。

        Returns:
            dict[str, Any]: 当前 ``_state`` 的浅拷贝，包含键：
                - done (bool): 是否已成功完成预热。
                - error (str | None): 上次失败错误消息。
                - in_progress (bool): 是否有后台线程在预热。
        """
        with self._lock:
            return dict(self._state)


# 模块级单例 + 向后兼容包装函数
#: Persona 缓存预热服务单例，负责应用启动时后台预热最近使用的音色嵌入
_persona_warmup_service: PersonaWarmupService = PersonaWarmupService(_persona_embedding_cache)


def warmup_persona_cache() -> dict[str, Any]:
    """向后兼容包装：委托给 PersonaWarmupService.warmup() 并返回状态。

    Returns:
        dict[str, Any]: 调用 :meth:`PersonaWarmupService.get_status` 返回的预热状态快照。
    """
    _persona_warmup_service.warmup()
    return _persona_warmup_service.get_status()


# 保留原始签名别名（完全向后兼容：返回 None，仅触发预热）
def _warmup_persona_cache_compat() -> None:
    """向后兼容无返回值版本（旧API兼容包装）。

    部分历史调用方（如早期版本的 routes/model.py）调用 warmup_persona_cache
    时不期望返回值。本函数提供完全相同的预热触发行为，但显式返回 None，
    保持与旧 API 签名一致。

    Note:
        新代码应直接调用 :func:`warmup_persona_cache` 并使用返回的状态字典
        判断预热结果，本函数仅用于向后兼容。

    Returns:
        None
    """
    warmup_persona_cache()


# ====================================================================
# 模型锁检查
# ====================================================================


def _do_load_voxcpm2_internal(
    gpu_device: Any,
    backend: Any,
    include_denoiser: bool = False,
) -> Generator[tuple[str, None, None, None], None, None]:
    """VoxCPM2 加载共享内部逻辑生成器。

    Internal generator for shared VoxCPM2 loading logic.

    被 :func:`load_voxcpm2` 与 :func:`_load_voxcpm2_engine` 复用以避免
    重复代码。执行实际的模型权重加载、子模块 GPU 传输、ASR 模型加载、
    registry 状态更新以及显存使用记录。

    Args:
        gpu_device: GPU 设备索引（CUDA/MPS 后端）或 ``None``（CPU）。
        backend: ``GPUBackend`` 枚举值，标识当前计算后端。
        include_denoiser: 若为 ``True`` 则通过 ``zipenhancer_model_id``
            参数加载增强器/降噪器子模型；否则显式 ``load_denoiser=False``
            跳过，减少显存占用。

    Yields:
        tuple[str, None, None, None]:
            每个阶段产出一条状态元组 ``(status_text, None, None, None)``，
            依次为：phase（阶段描述文本）、percentage（预留，当前 None）、
            message（预留，当前 None）、audio/sample_rate（预留，None）。

    Raises:
        Exception: 清理半加载状态后重新抛出加载过程中的任何异常。
    """
    # 注入 bin/integrated_app/vendor 到 sys.path，使 vendor/voxcpm 源码包可被发现
    # （未 pip install 该包时的兼容路径；vendor 化后不依赖 references/ 克隆仓库）
    import sys as _sys

    _voxcpm_src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor")
    if os.path.isdir(_voxcpm_src) and _voxcpm_src not in _sys.path:
        _sys.path.insert(0, _voxcpm_src)
        logger.info(f"[VoxCPM2] 注入 vendor 源码路径: {_voxcpm_src}")

    import voxcpm
    from funasr import AutoModel

    from ..gpu_backend import GPUBackend, GPUBackendManager

    device_string: str = GPUBackendManager.format_device_string(gpu_device) if gpu_device is not None else "cpu"

    status_text: str
    # Step 1: Load VoxCPM2 model
    status_text = "正在加载 VoxCPM2 模型..."
    yield status_text, None, None, None

    try:
        kwargs: dict[str, Any] = dict(
            optimize=True,
            local_files_only=True,
        )
        if include_denoiser:
            kwargs["zipenhancer_model_id"] = get_voxcpm2_denoiser_path()
        else:
            kwargs["load_denoiser"] = False

        new_model: Any = voxcpm.VoxCPM.from_pretrained(get_voxcpm2_model_path(), **kwargs)

        # Move sub-components to GPU with granular progress
        if backend != GPUBackend.CPU and gpu_device is not None:
            gpu_components: list[str] = [
                attr
                for attr in ("tts_model", "model", "codecs", "vocoder")
                if getattr(new_model, attr, None) is not None
            ]
            total_components: int = len(gpu_components)
            for i, attr in enumerate(gpu_components, 1):
                sub: Any = getattr(new_model, attr)
                status_text = f"GPU 传输: {attr} ({i}/{total_components})..."
                yield status_text, None, None, None
                sub.to(device_string)
                logger.info(f"  VoxCPM2.{attr} -> {device_string}")
            # Ensure cache sync
            with contextlib.suppress(Exception):
                GPUBackendManager.synchronize()
            allocated_mb: float = GPUBackendManager.memory_allocated() / (1024**2)
            logger.info(f"  VoxCPM2 加载完成，GPU 显存已分配: {allocated_mb:.0f} MB")
        else:
            logger.info("  VoxCPM2 使用 CPU 后端运行")

        # Store model in registry early so error handler can clean it up
        registry.voxcpm_model = new_model

        # Step 2: Load ASR model
        status_text = "正在加载 ASR 模型..."
        yield status_text, None, None, None

        new_asr: Any = AutoModel(
            model=get_voxcpm2_asr_path(),
            disable_pbar=True,
            disable_update=True,  # 离线优先：跳过 funasr 版本更新检查
            device=device_string,
        )
        registry.set_voxcpm_loaded(new_model, asr=new_asr)

        # Step 3: Record VRAM usage
        try:
            monitor: Any = get_health_monitor()
            if backend != GPUBackend.CPU:
                vram_mb: float = GPUBackendManager.memory_allocated() / (1024**2)
                monitor.record_vram_usage(vram_mb)
                monitor.set_model_status("ready")
        except Exception as e:
            logger.debug(f"VoxCPM2 加载后 VRAM 记录失败: {e}")

        # Step 4: 模型预热推理（参考 vLLM/Fish Speech 的 warmup 设计）
        # 目的：触发 CUDA kernel 编译、预热 KV cache 分配器、验证模型可正常生成
        # 预热失败不中断流程（fail-soft）
        try:
            from ..model_optimizer import optimize_and_warmup_voxcpm

            def _warmup_progress(msg: str) -> None:
                logger.info(f"[VoxCPM2-Warmup] {msg}")

            # 同步执行预热（在加载流程中，此时还未返回给用户）
            # torch.compile 默认在 Windows 上禁用，预热始终执行（短文本快速完成）
            optimize_and_warmup_voxcpm(
                new_model,
                enable_compile=None,  # 自动检测（Windows 默认禁用）
                enable_warmup=True,
                progress_callback=_warmup_progress,
            )
        except Exception as warmup_err:
            logger.debug(f"VoxCPM2 预热失败（可忽略）: {warmup_err}")

        status_text = "VoxCPM2 引擎就绪"
        yield status_text, None, None, None

    except Exception:
        failed_model: Any = registry.voxcpm_model
        failed_asr: Any = registry.voxcpm_asr
        registry.clear_all()
        if failed_model is not None:
            del failed_model
        if failed_asr is not None:
            del failed_asr
        gc.collect()
        from ..gpu_backend import GPUBackendManager

        with contextlib.suppress(Exception):
            GPUBackendManager.empty_cache()
        raise


def load_voxcpm2(
    progress_callback: Callable[..., None] | None = None,
) -> Generator[tuple[str, None, None, None], None, None]:
    """加载 VoxCPM2 引擎（生成器进度事件流）。

    Load the VoxCPM2 engine with generator-based progress feedback.

    加载阶段与生成器事件说明（每条产出为 ``(status_text, None, None, None)``
    四元组，兼容调用方 ``for status_text, _, _, _ in gen:`` 解包）：
        1. ``phase=init``: 卸载当前已加载引擎、GC、empty_cache。
        2. **显存预检 WHY（1.5 倍规则）**：
           要求可用显存 ≥ 模型基线大小 × 1.5。额外 0.5 倍包含三部分峰值开销：
           ① FunASR ASR 模型权重（约 200~400MB，CPU 模式另计但 GPU 场景
           会一并加载进显存）；② 推理时 KV cache（batch × seq_len × layers
           × head_dim，流式生成长文本峰值约为权重 15%~25%）；
           ③ 中间激活张量（attention logits、FFN 临时矩阵，单次推理瞬时可
           接近权重 20%~40%）。三者叠加接近或超过 0.5 倍，预留 1.5 倍可
           避免"加载成功但首次推理即 OOM"的典型陷阱。
        3. ``phase=load_model``: 调用 ``voxcpm.VoxCPM.from_pretrained``
           加载主模型。
        4. ``phase=gpu_transfer``: tts_model/model/codecs/vocoder 子模块
           逐次 ``.to(device)``，每个子模块单独产出一条进度事件。
        5. ``phase=load_asr``: 加载 FunASR AutoModel 作为 ASR 辅助模型。
        6. ``phase=ready``: ``"VoxCPM2 引擎就绪"`` 标记加载结束。

    异常退出时 ``try/finally`` 会：
        a) 若加载过程中异常，产出一条 ``(error_msg, None, None, None)`` 事件；
        b) 调用 :func:`free_gpu_memory` 清理半加载状态；
        c) 无论正常/异常均释放 ``_model_lock``。

    Args:
        progress_callback (Optional[Callable[..., None]]): 预留回调参数
            （当前未启用，保持签名兼容；使用默认值 ``None`` 即可）。

    Yields:
        tuple[str, None, None, None]:
            ``(status_text, None, None, None)`` 四元组；``status_text``
            为人类可读阶段描述；其余三个位置为历史兼容保留的占位。
    """
    _model_lock.acquire()
    # 模型加载开始时重置显存泄漏检测基线，避免加载期间显存上升导致误报
    get_health_monitor().reset_vram_baseline()
    try:
        # Unload current engine if any
        old_model: Any = registry.voxcpm_model
        old_asr: Any = registry.voxcpm_asr
        registry.voxcpm_model = None
        registry.voxcpm_asr = None
        if old_model is not None:
            del old_model
        if old_asr is not None:
            del old_asr
        gc.collect()
        from ..gpu_backend import GPUBackend, GPUBackendManager

        backend: GPUBackend = GPUBackendManager.detect_backend()
        if backend != GPUBackend.CPU:
            with contextlib.suppress(Exception):
                GPUBackendManager.empty_cache()
        time.sleep(_LOAD_RETRY_AFTER_UNLOAD_SECONDS)

        gpu_device: Any = _state.get_gpu_device()

        for status_tuple in _do_load_voxcpm2_internal(gpu_device, backend, include_denoiser=False):
            if progress_callback is not None:
                with contextlib.suppress(Exception):
                    progress_callback(status_tuple)
            yield status_tuple
    except Exception as e:
        logger.error(f"[模型加载] VoxCPM2 加载失败: {e}")
        with contextlib.suppress(Exception):
            free_gpu_memory()
        error_msg: str = f"VoxCPM2 加载失败: {type(e).__name__}: {e}"
        yield error_msg, None, None, None
    finally:
        with contextlib.suppress(Exception):
            _model_lock.release()


def load_indextts2(
    progress_callback: Callable[..., None] | None = None,
) -> Generator[tuple[str, None, None, None], None, None]:
    """加载 IndexTTS 2.5 引擎（生成器进度事件流）。

    Generator function to load IndexTTS 2.5 engine step by step.

    生成器产出格式（每条均为 ``(status_text, None, None, None)`` 四元组，
    兼容调用方 ``for status_text, _, _, _ in gen:`` 解包）：
        1. ``"正在检查系统资源..."``：
           校验 ``get_indextts2_model_path()`` 是否存在并查询显存状态。
        2. ``"正在加载 IndexTTS 2.5 引擎..."``：
           构造 ``IndexTTS2Engine``，内部依次加载 VQ Encoder、
           Flow Matching 主干、HiFi-GAN Vocoder 等子模块。
        3. ``"IndexTTS 2.5 引擎就绪"``：加载完成。
        4. 异常时：``"IndexTTS 2.5 加载失败: <ErrType>: <msg>"``。

    ``try/finally`` 保证：
        a) 半加载异常时清理 ``registry.indextts2_engine``；
        b) ``gc.collect()`` + ``GPUBackendManager.empty_cache()`` +
           ``free_gpu_memory()`` 分层释放；
        c) 释放 ``_model_lock``。

    Args:
        progress_callback (Optional[Callable[..., None]]): 预留回调参数
            （保持签名兼容；默认 ``None``）。

    Yields:
        tuple[str, None, None, None]:
            ``(status_text, None, None, None)`` 四元组；``status_text``
            为当前阶段描述；其余三个位置为历史占位（保持不变）。
    """
    _model_lock.acquire()
    # 模型加载开始时重置显存泄漏检测基线，避免加载期间显存上升导致误报
    get_health_monitor().reset_vram_baseline()
    try:
        from .engines.indextts2_engine import IndexTTS2Engine
        from ..gpu_backend import GPUBackend, GPUBackendManager

        backend: GPUBackend = GPUBackendManager.detect_backend()

        # Check if model files exist
        if not os.path.exists(get_indextts2_model_path()):
            raise FileNotFoundError(
                f"IndexTTS 2.5 模型文件不存在: {get_indextts2_model_path()}\n"
                "请运行: python scripts/download_indextts2.py 下载模型"
            )

        # Step 1: VRAM/RAM check
        from ..model_registry import ENGINE_VRAM_REQUIREMENTS

        needed_vram_gb: float = ENGINE_VRAM_REQUIREMENTS.get(EngineName.INDEXTTS2.value, 6.0)
        status_text: str = "正在检查系统资源..."
        if progress_callback is not None:
            with contextlib.suppress(Exception):
                progress_callback(status_text)
        yield status_text, None, None, None

        if backend != GPUBackend.CPU:
            try:
                mem_info: Any = GPUBackendManager.get_memory_info()
                free_gb: float = mem_info[3] / (1024**3)
                logger.info(f"[IndexTTS2] VRAM 检查: 需要 {needed_vram_gb}GB, 可用 {free_gb:.2f}GB")

                if free_gb < needed_vram_gb:
                    logger.warning(f"[IndexTTS2] 显存不足 ({free_gb:.2f}GB < {needed_vram_gb}GB)，将尝试使用 CPU 模式")
            except Exception as mem_err:
                logger.debug(f"[IndexTTS2] 显存查询失败（跳过预检）: {mem_err}")

        # Step 2: Load model
        status_text = "正在加载 IndexTTS 2.5 引擎..."
        if progress_callback is not None:
            with contextlib.suppress(Exception):
                progress_callback(status_text)
        yield status_text, None, None, None

        logger.info("[IndexTTS2] 开始加载 IndexTTS 2.5 引擎...")
        start_time: float = time.time()

        new_engine: Any = IndexTTS2Engine(
            model_dir=get_indextts2_model_path(),
            use_bf16=(backend != GPUBackend.CPU),
        )

        load_time: float = time.time() - start_time
        logger.info(f"[IndexTTS2] IndexTTS 2.5 引擎加载完成，耗时: {load_time:.1f}秒")

        registry.set_indextts2_loaded(new_engine)

        # IndexTTS2 预热推理
        try:
            from ..model_optimizer import warmup_indextts2

            def _idx_warmup_progress(msg: str) -> None:
                logger.info(f"[IndexTTS2-Warmup] {msg}")

            warmup_indextts2(new_engine, progress_callback=_idx_warmup_progress)
        except Exception as idx_warmup_err:
            logger.debug(f"IndexTTS2 预热失败（可忽略）: {idx_warmup_err}")

        status_text = "IndexTTS 2.5 引擎就绪"
        logger.info(f"[IndexTTS2] {status_text}")
        if progress_callback is not None:
            with contextlib.suppress(Exception):
                progress_callback(status_text)
        yield status_text, None, None, None

        # Record VRAM usage
        try:
            monitor: Any = get_health_monitor()
            if backend != GPUBackend.CPU:
                vram_mb: float = GPUBackendManager.memory_allocated() / (1024**2)
                monitor.record_vram_usage(vram_mb)
                monitor.set_model_status("ready")
        except Exception as e:
            logger.debug(f"[IndexTTS2] VRAM 记录失败: {e}")
    except (FileNotFoundError, PermissionError, OSError) as fs_err:
        logger.error(f"[IndexTTS2] 文件系统错误: {fs_err}")
        with contextlib.suppress(Exception):
            free_gpu_memory()
        error_msg: str = f"IndexTTS 2.5 加载失败: {type(fs_err).__name__}: {fs_err}"
        yield error_msg, None, None, None
    except Exception as e:
        import traceback

        tb: str = traceback.format_exc()
        logger.error(f"[IndexTTS2] IndexTTS 2.5 加载失败: {type(e).__name__}: {e}\n详细错误:\n{tb}")
        gc.collect()
        from ..gpu_backend import GPUBackendManager

        with contextlib.suppress(Exception):
            GPUBackendManager.empty_cache()
            free_gpu_memory()
        error_msg = f"IndexTTS 2.5 加载失败: {type(e).__name__}: {e}"
        yield error_msg, None, None, None
    finally:
        with contextlib.suppress(Exception):
            _model_lock.release()


# ====================================================================
# 预加载服务 (M-R2)
# ====================================================================


class PreloadService:
    """模型文件预加载服务（设计文档 M-R2）。

    REFACTOR: [M-R2] 模型文件预加载服务，封装状态与行为。

    将原 ``_preload_state`` 全局字典、``_preload_lock``、``_read_files_to_cache``、
    ``_read_single_file`` 等过程式代码封装为类，提升内聚性。

    设计目的：
        在应用启动或显式触发时，后台串行预读指定引擎的模型文件到操作系统
        **Page Cache**。由于模型文件动辄数 GB，首次 GPU 加载时磁盘 I/O 通常
        占总加载时间 40%~70%；通过预读让 OS 把权重文件的 page 页留在 RAM，
        后续真正加载到 VRAM 时走内存带宽（~数十 GB/s）而非磁盘
        （~500MB/s SATA 或 ~3GB/s NVMe），可显著降低用户首次点击"加载模型"
        的等待时长。

    职责：
        - 后台预读模型文件到 OS page cache，降低后续 GPU 加载的 I/O 延迟
        - 单任务串行（同一时刻只允许一个预加载任务，避免磁盘带宽争抢）
        - 状态查询供 ``/api/model/preload/status`` 路由使用

    Attributes:
        _state (dict[str, Any]): 预加载任务状态字典，键如下：
            - in_progress (bool): 是否正在进行。
            - target_engine (str | None): 目标引擎名（VOXCPM2 / INDEXTTS2）。
            - target_size (str | None): 模型大小变体（当前未使用）。
            - completed (bool): 是否已完成本次预加载。
            - error (str | None): 上次失败错误消息，成功或未开始为 None。
        _lock (threading.Lock): 保护 ``_state`` 访问的互斥锁。
        _thread (threading.Thread | None): 最近启动的预加载线程对象引用
            （当前未 join，仅用于调试诊断；为 None 表示尚未启动）。
    """

    def __init__(self) -> None:
        """初始化 PreloadService 空状态。"""
        self._state: dict[str, Any] = {
            "in_progress": False,
            "target_engine": None,
            "target_size": None,
            "completed": False,
            "error": None,
        }
        self._lock: threading.Lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def preload(
        self,
        engine: str = EngineName.VOXCPM2.value,
        size: str = EngineName.VOXCPM2.value,
    ) -> None:
        """后台预加载指定引擎的模型文件到系统 Page Cache。

        Background preload of model files into system RAM page cache.

        对目标引擎目录下的每个文件读取前 ``_PRELOAD_READ_CHUNK_BYTES``
        字节（默认 1MB）或整文件（若更小）即可触发 OS Page Cache 预读，
        **不会**把权重加载到 GPU 显存。

        通过守护线程后台执行，不阻塞调用方。同一时刻仅允许一个预加载任务
        （由 ``_state["in_progress"]`` + ``_lock`` 双重保护），避免磁盘
        带宽饱和导致主流程 I/O 退化。

        Args:
            engine (str): 目标引擎名称，可选 ``EngineName.VOXCPM2.value``
                或 ``EngineName.INDEXTTS2.value``；其他值仅记录日志不报错。
            size (str): 模型大小变体，当前未实际使用，保留用于后续多尺寸
                权重切换场景；默认与 ``engine`` 同值。
        """
        with self._lock:
            if self._state["in_progress"]:
                logger.info("[预加载] 已有预加载任务在进行中，跳过")
                return
            self._state["in_progress"] = True
            self._state["target_engine"] = engine
            self._state["target_size"] = size
            self._state["completed"] = False
            self._state["error"] = None

        def _do_preload() -> None:
            from .middleware.request_id import set_request_id

            set_request_id(f"bg-{threading.current_thread().name}")
            try:
                if engine == EngineName.VOXCPM2.value:
                    logger.info("[预加载] 开始预读 VoxCPM2 模型文件到系统内存...")
                    if os.path.exists(get_voxcpm2_model_path()):
                        try:
                            self._read_files_to_cache(get_voxcpm2_model_path())
                            logger.info("[预加载] VoxCPM2 模型文件已预读到系统缓存")
                        except (OSError, PermissionError) as vox_err:
                            logger.warning(f"[预加载] VoxCPM2 主模型预读失败: {vox_err}")
                    else:
                        logger.warning(f"[预加载] VoxCPM2 模型路径不存在: {get_voxcpm2_model_path()}")

                    if os.path.exists(get_voxcpm2_asr_path()):
                        try:
                            self._read_files_to_cache(get_voxcpm2_asr_path())
                            logger.info("[预加载] VoxCPM2 ASR 模型文件已预读到系统缓存")
                        except (OSError, PermissionError) as asr_err:
                            logger.warning(f"[预加载] VoxCPM2 ASR 模型预读失败: {asr_err}")

                elif engine == EngineName.INDEXTTS2.value:
                    logger.info("[预加载] 开始预读 IndexTTS 2.5 模型文件到系统内存...")
                    if os.path.exists(get_indextts2_model_path()):
                        try:
                            self._read_files_to_cache(get_indextts2_model_path())
                            logger.info("[预加载] IndexTTS 2.5 模型文件已预读到系统缓存")
                        except (OSError, PermissionError) as idx_err:
                            logger.warning(f"[预加载] IndexTTS 2.5 模型预读失败: {idx_err}")
                    else:
                        logger.warning(f"[预加载] IndexTTS 2.5 模型路径不存在: {get_indextts2_model_path()}")

                with self._lock:
                    self._state["completed"] = True
                    self._state["in_progress"] = False
                    logger.info("[预加载] 预加载完成")

            except Exception as e:
                logger.error(f"[预加载] 预加载失败: {e}")
                with self._lock:
                    self._state["error"] = str(e)
                    self._state["in_progress"] = False

        self._thread = threading.Thread(target=_do_preload, daemon=True, name="model-preload")
        self._thread.start()

    def _read_files_to_cache(self, directory_path: str) -> None:
        """递归预读目录下所有模型文件到系统 Page Cache。

        Recursively read model files into system page cache.

        若 ``directory_path`` 本身即为单个文件，则直接单文件预读。
        目录遍历通过 ``os.walk``，跳过以 ``.`` 开头的隐藏文件/目录以及
        ``__pycache__``，避免把无关字节拖进 Page Cache 污染缓存。

        Args:
            directory_path (str): 待预读的目录或单文件绝对路径。
        """
        if os.path.isfile(directory_path):
            self._read_single_file(directory_path)
            return
        if not os.path.isdir(directory_path):
            return
        total_bytes: int = 0
        for root, dirs, files in os.walk(directory_path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            for fname in sorted(files):
                if fname.startswith("."):
                    continue
                fpath: str = os.path.join(root, fname)
                total_bytes += self._read_single_file(fpath)
        if total_bytes > 0:
            logger.info(f"[预加载] 已预读 {total_bytes / (1024 * 1024):.1f}MB 到系统缓存")

    def _read_single_file(self, filepath: str) -> int:
        """单文件预读到系统 Page Cache。

        Read a single file into system cache to warm up page cache.

        对每个文件读取前 ``_PRELOAD_READ_CHUNK_BYTES``（默认 1MB）或整文件
        （若更小）字节到 Python 匿名 buffer 后即丢弃——目的不是保留数据，
        而是触发内核把对应 page 页驻留在 RAM，让后续真正加载时走内存路径。

        Args:
            filepath (str): 待预读文件的绝对路径。

        Returns:
            int: 实际读取的字节数；读取失败或空文件返回 0。
        """
        try:
            size: int = os.path.getsize(filepath)
            if size == 0:
                return 0
            read_size: int = min(size, _PRELOAD_READ_CHUNK_BYTES)
            with open(filepath, "rb") as f:
                f.read(read_size)
            return read_size
        except (OSError, PermissionError) as e:
            logger.debug(f"[预加载] 读取文件失败 {filepath}: {e}")
            return 0

    def get_status(self) -> dict[str, Any]:
        """获取当前预加载任务状态快照。

        Retrieve the current preload task status.

        Returns:
            dict[str, Any]: ``_state`` 的浅拷贝副本，键：
                - in_progress (bool): 是否正在进行。
                - target_engine (str | None): 目标引擎名。
                - target_size (str | None): 目标尺寸变体。
                - completed (bool): 是否完成。
                - error (str | None): 错误消息，无错误为 None。
        """
        with self._lock:
            return dict(self._state)


# 模块级单例 + 向后兼容包装函数
#: 模型文件预加载服务单例，负责后台预读模型文件到系统 Page Cache 加速后续加载
_preload_service: PreloadService = PreloadService()


def preload_model(
    engine: str = EngineName.VOXCPM2.value,
    size: str = EngineName.VOXCPM2.value,
) -> dict[str, Any]:
    """向后兼容包装：委托给 PreloadService.preload() 并返回状态。

    Args:
        engine (str): 目标引擎名。
        size (str): 模型尺寸变体（当前保留未使用）。

    Returns:
        dict[str, Any]: 调用 :meth:`PreloadService.get_status` 返回的预加载状态快照。
    """
    _preload_service.preload(engine, size)
    return _preload_service.get_status()


def get_preload_status() -> dict[str, Any]:
    """向后兼容包装：委托给 PreloadService.get_status()。

    Returns:
        dict[str, Any]: 预加载任务状态快照。
    """
    return _preload_service.get_status()


# ====================================================================
# 引擎切换 (M-R1 拆分 + M-R3 回滚一致性)
# ====================================================================
