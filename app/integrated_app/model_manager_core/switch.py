"""model_manager_core.switch — 引擎切换职责（M-R7 拆分，2026-08-17）。

包含 switch_engine 及 5 个辅助函数、热待机判断 _can_hot_standby、
引擎加载器 _load_voxcpm2_engine / _load_generic_engine。
【边界】加载委托 load.py；卸载委托 unload.py。
"""

from . import state as _state
from .load import _do_load_voxcpm2_internal, load_indextts2, load_indextts20
from .state import *
from .unload import unload_model


def _can_hot_standby(target_engine: str) -> bool:
    """判断是否可热待机（同时保留新旧双引擎在显存中）。

    Check if there's enough VRAM to keep both current and target engine loaded.

    热待机模式允许切换引擎时不卸载当前引擎，在显存充足的场景下把切换
    延迟从数十秒降低到数百毫秒。

    Args:
        target_engine (str): 目标引擎名称。

    Returns:
        bool: 若当前空闲显存足够容纳目标引擎（权重基线 × 安全裕度，见
            :func:`model_registry.estimate_engine_vram_need_gb`）返回 ``True``；
            否则返回 ``False``；CPU 模式或无法获取 GPU 信息时保守返回 ``False``。
    """
    from ..gpu_backend import GPUBackend, GPUBackendManager
    from ..model_registry import ENGINE_VRAM_REQUIREMENTS, INDEXTTS_VARIANTS, estimate_engine_vram_need_gb

    # IndexTTS 2.5 与 2.0 复用同一引擎槽位（互斥）：家族内互切绝不能热待机，
    # 否则新实例会覆盖同一槽位而旧实例未卸载 → 显存泄漏。强制走卸载-再加载路径。
    current_engine = registry.current_engine
    if current_engine in INDEXTTS_VARIANTS and target_engine in INDEXTTS_VARIANTS:
        logger.info("[热待机] IndexTTS 家族内互切复用同一槽位，禁用热待机以先卸载旧实例")
        return False

    backend: GPUBackend = GPUBackendManager.detect_backend()
    if backend == GPUBackend.CPU:
        return False

    gpu_device: Any = _state.get_gpu_device()
    if gpu_device is None:
        return False

    props: dict[str, Any] = GPUBackendManager.get_device_properties(gpu_device)
    total: int = props.get("total_memory", 0)
    if total <= 0:
        return False

    allocated: int = GPUBackendManager.memory_allocated(gpu_device)
    free_gb: float = (total - allocated) / (1024**3)

    current_vram: float = ENGINE_VRAM_REQUIREMENTS.get(registry.current_engine, 0.0)

    # 热待机 = 旧引擎常驻 + 再载入目标引擎，因此需要的是"当前空闲显存 ≥ 目标引擎
    # 完整需求 + 安全裕度"。注意这里的 free_gb 已包含当前引擎占用（allocated 计入
    # 当前引擎），所以目标引擎必须完整地塞进 free_gb 剩余空间。
    #
    # M-R1 fix: 原实现用 target_vram * 0.8（低估），会导致空闲显存本不足以双引擎
    # 常驻时仍误判为热待机，跳过卸载直接加载新引擎 → OOM。改为完整需求 + 余量后，
    # 显存不充裕时自然回退到"先卸载旧引擎再加载"的传统路径，避免 OOM。
    #
    # M-R8 fix: 倍数原先硬编码为 1.2，与预检的 1.5（可配）和加载器的 1.0 三套口径
    # 并存，导致日志里「需要 9.0GB / 目标需要 7.20GB / 需要 6.0GB」互相矛盾。
    # 统一走 estimate_engine_vram_need_gb，需求同源后才能横向对照日志。
    needed_gb: float = estimate_engine_vram_need_gb(target_engine)
    can_standby: bool = free_gb >= needed_gb

    if can_standby:
        logger.info(
            f"[热待机] 显存充足: 可用 {free_gb:.2f}GB, 目标需要 {needed_gb:.2f}GB, 当前引擎占用 {current_vram:.1f}GB"
        )
    else:
        logger.info(f"[热待机] 显存不足: 可用 {free_gb:.2f}GB, 目标需要 {needed_gb:.2f}GB")

    return can_standby


def _validate_engine_name(engine_name: str) -> str:
    """校验并规范化引擎名称（M-R1 拆分辅助函数 ①）。

    REFACTOR: [M-R1] 校验并规范化引擎名称。

    Args:
        engine_name (str): 待校验的引擎名称。

    Returns:
        str: 去除首尾空白后的规范化引擎名。

    Raises:
        EngineSwitchError: 引擎名称不在 ``EngineName`` 白名单内。
    """
    engine_name = engine_name.strip()
    if engine_name not in EngineName._value2member_map_:
        # 通用新式引擎：允许已注册到 engine_registry 的声明式引擎
        try:
            from ..engine_interface import engine_registry

            registered = engine_registry.is_registered(engine_name)
        except Exception:
            registered = False
        if not registered:
            raise EngineSwitchError(f"不支持的引擎: {engine_name}")
    return engine_name


def _snapshot_engine_state() -> dict[str, Any]:
    """快照当前引擎状态（M-R1 拆分辅助函数 ②）。

    REFACTOR: [M-R1] 快照当前引擎状态，用于失败时回滚。

    Returns:
        dict[str, Any]: 包含如下键的字典：
            - engine: ``registry.current_engine`` 当前引擎名。
            - had_voxcpm: 快照时 ``registry.voxcpm_model`` 是否有实例。
            - had_indextts2: 快照时 ``registry.indextts2_engine`` 是否有实例。

    注意 (M-R8): 这里**只能记录引擎名和布尔标志，绝不能存对象引用**。

    WHY: 快照字典是 :func:`switch_engine` 的局部变量，它的生命周期横跨
    ``unload_model()`` 和 ``finally`` 里的 ``free_gpu_memory()``。一旦字典
    持有 ``registry.voxcpm_model``，VoxCPM2 权重就还剩一个强引用，引用计数
    不归零 → ``gc.collect()`` 收不掉 → ``empty_cache()`` 释放 0 字节。实测
    表现为「卸载成功但已分配显存不变」（VoxCPM2 卸载后 allocated 恒为 6.04GB），
    紧接着新引擎在同一块显存上叠加加载导致超额订阅。IndexTTS2 走的是显式
    ``engine.unload()``（内部自行 del 子模块），所以不受该引用影响、能降到
    0.00GB —— 两个引擎表现的差异正是定位线索。

    回滚本来就不复用这些引用：见 :func:`_rollback_engine` 的 M-R3 WHY 注释，
    它只按 ``prev_state["engine"]`` 重新走完整加载流程。
    """
    return {
        "engine": registry.current_engine,
        "had_voxcpm": registry.voxcpm_model is not None,
        "had_indextts2": registry.indextts2_engine is not None,
    }


def _check_vram_prereq(engine_name: str, backend: Any, gpu_device: Any) -> float:
    """显存预检查（M-R1 拆分辅助函数 ③）。

    REFACTOR: [M-R1] 显存预检查。

    WHY: 需求侧 = ENGINE_VRAM_REQUIREMENTS 的权重基线 × 安全裕度（见
    :func:`model_registry.estimate_engine_vram_need_gb`），因为实际运行时还会
    叠加 ① ASR/Enhancer 权重、② KV cache、③ 中间激活。可用侧则把"卸载当前
    引擎预计可回收的基线"计入，所以这只是一道**软门禁**：它放行不代表显存真的
    够，卸载是否兑现由 :func:`_wait_vram_freed` 用实测差值复核。

    E2-1: 校验 props 有效性（total <= 0 时跳过预检，记录警告而非崩溃）。

    Args:
        engine_name (str): 目标引擎名称。
        backend: GPUBackend 枚举值。
        gpu_device: GPU 设备索引（CUDA/MPS）或 ``None``（CPU）。

    Returns:
        float: 可用显存（GB）。CPU 模式或无法获取显存时返回 ``0.0``。

    Raises:
        InsufficientVRAMError: 计入卸载收益后仍低于目标引擎需求。
    """
    from ..gpu_backend import GPUBackend, GPUBackendManager
    from ..model_registry import ENGINE_VRAM_REQUIREMENTS, estimate_engine_vram_need_gb

    # 需求口径统一由 estimate_engine_vram_need_gb 提供（权重基线 × 安全裕度），
    # 与热待机、引擎加载器同源，日志中的 needed 才可直接对照。
    needed_gb: float = estimate_engine_vram_need_gb(engine_name)

    # CPU 模式或无 GPU 设备：跳过显存预检
    if backend == GPUBackend.CPU or gpu_device is None:
        logger.warning("[引擎切换] CPU 模式或无 GPU 设备：跳过显存检查，模型将在 CPU 上运行（速度较慢）")
        return 0.0

    props: dict[str, Any] = GPUBackendManager.get_device_properties(gpu_device)
    total: int = props.get("total_memory", 0)
    # E2-1: 校验 props 有效性
    if total <= 0:
        logger.warning("[引擎切换] 无法获取 GPU 总显存，跳过预检")
        return 0.0

    allocated: int = GPUBackendManager.memory_allocated(gpu_device)
    free: int = total - allocated
    free_gb: float = free / 1024**3

    # M-R1 fix: 预检查需要把"卸载当前引擎可释放的显存"计入可用显存。
    #
    # WHY: switch_engine 的传统切换路径（非热待机）会先 unload_model() 再加载
    # 目标引擎。若当前引擎已占用显存（例如正在使用 VoxCPM2 ~6.5GB），预检只看
    # "当前空闲显存"会误判为显存不足而硬失败（InsufficientVRAMError），导致永远
    # 走不到"先卸载再加载"的路径。因此把当前引擎的基线占用视为卸载后可回收的
    # 显存，只有"卸载后仍装不下"才判定为硬失败。
    current_engine: str | None = registry.current_engine
    current_vram_gb: float = ENGINE_VRAM_REQUIREMENTS.get(current_engine, 0.0) if current_engine else 0.0
    effective_free_gb: float = free_gb + current_vram_gb

    logger.info(
        f"[引擎切换] VRAM 检查: 需要 {needed_gb:.2f}GB, "
        f"可用 {effective_free_gb:.2f}GB (当前空闲 {free_gb:.2f}GB + "
        f"卸载当前引擎 {current_engine or '无'} 预计可释放 {current_vram_gb:.2f}GB)"
    )

    if effective_free_gb < needed_gb:
        error_msg: str = (
            f"显存不足，无法加载 {engine_name}。即使卸载当前引擎"
            f" {current_engine or '无'}后可用约 {effective_free_gb:.2f}GB，"
            f"仍需要约 {needed_gb:.2f}GB。"
        )
        logger.error(f"[引擎切换] {error_msg}")
        raise InsufficientVRAMError(error_msg)

    return free_gb


def _wait_vram_freed(
    gpu_device: Any,
    max_wait: float = _VRAM_WAIT_MAX_SECONDS,
    poll_interval: float = _VRAM_POLL_INTERVAL_SECONDS,
    baseline_allocated: int | None = None,
    expected_release_bytes: int = 0,
) -> bool:
    """轮询等待显存释放（M-R1 拆分辅助函数 ④）。

    REFACTOR: [M-R1] 轮询等待显存释放。

    E2-2: 阈值相对化 —— 取 ``_VRAM_FREE_THRESHOLD_BYTES`` 与总显存的
    ``_VRAM_FREE_PERCENT_FLOOR%`` 中较小者，避免大显存设备上 500MB 阈值过松、
    小显存设备上 500MB 阈值过严。

    M-R8: 传入 ``baseline_allocated``（卸载前的 allocated 字节数）后，本函数
    改为核验**回收量**而非绝对空闲量。

    WHY: ``total - memory_allocated()`` 在被卸载的模型仍被强引用时依然"好看"
    —— 因为那部分显存压根没进 free，但只要卡上还有别的余量，绝对空闲就会轻松
    超过 500MB 阈值，于是旧实现每次都立刻返回 True 并打印「VRAM 已释放」，
    而实际上一个字节都没回收。只有和卸载前对比才可能暴露"假释放"。

    Args:
        gpu_device: GPU 设备索引。
        max_wait (float): 最大等待秒数，默认 ``_VRAM_WAIT_MAX_SECONDS``。
        poll_interval (float): 轮询间隔秒数，默认 ``_VRAM_POLL_INTERVAL_SECONDS``。
        baseline_allocated (int | None): 卸载前的 ``memory_allocated()`` 字节数；
            ``None`` 表示无基线，退化为 E2-2 的绝对空闲判断。
        expected_release_bytes (int): 预计可回收字节数（通常取被卸载引擎的显存
            基线）；实际回收达到其 50% 即认定释放成功。``<= 0`` 时同样退化为
            绝对空闲判断。

    Returns:
        bool: 回收到位（或无基线可对照时空闲量达标）返回 ``True``；轮询异常或
            超时返回 ``False``（由调用方决定是否继续切换流程）。
    """
    from ..gpu_backend import GPUBackendManager

    poll_start: float = time.time()
    while time.time() - poll_start < max_wait:
        time.sleep(poll_interval)
        try:
            allocated: int = GPUBackendManager.memory_allocated(gpu_device)
            if baseline_allocated is None or expected_release_bytes <= 0:
                props: dict[str, Any] = GPUBackendManager.get_device_properties(gpu_device)
                total: int = props.get("total_memory", 0)
                free: int = total - allocated
                # E2-2: 阈值相对化
                threshold: int = min(_VRAM_FREE_THRESHOLD_BYTES, total * _VRAM_FREE_PERCENT_FLOOR // 100)
                if free > threshold:
                    return True
                continue

            released: int = baseline_allocated - allocated
            if released >= expected_release_bytes * 0.5:
                logger.debug(f"[引擎切换] 显存回收达标: {released / 1024**3:.2f}GB")
                return True
        except Exception:
            # 显存查询异常时立即退出轮询，交由调用方决定后续行为
            break

    return False


def _rollback_engine(prev_state: dict[str, Any], error: Exception) -> None:
    """回滚到之前的引擎状态（M-R1 拆分辅助函数 ⑤ / M-R3 关键修复）。

    REFACTOR: [M-R1/M-R3] 回滚到之前的引擎状态。

    WHY M-R3: **不能只恢复引用而要重新加载 prev_engine。**

    考虑如下时序：
        1. 快照 ``prev_state = {"indextts2_engine": <obj A>, "engine": INDEXTTS2}``
        2. 执行 ``unload_model()`` → 内部调用 ``obj A.unload()``
           → IndexTTS2 内部释放：
             a) ``del self.vocoder``
             b) ``torch.cuda.empty_cache()``
             c) 关闭 CUDA IPC handle
        3. 此时 ``prev_state["indextts2_engine"]`` 仍引用 Python 对象 A，
           但 A 内部的 ``.tts_model / .vocoder / cuda stream`` 均已失效。
        4. 若仅 ``registry.indextts2_engine = prev_state["indextts2_engine"]``，
           下次推理会抛 ``RuntimeError: accessing unregistered storage`` 等
           底层 CUDA 错误，且极难定位。

    因此 M-R3 的修复方案是：**根据 ``prev_engine`` 名称重新走完整的
    model loading 流程**，虽然慢了数十秒，但保证回滚后引擎真正可用。

    失败处理：重新加载是 best-effort 的，失败时仅记录错误日志，
    不抛异常（避免掩盖原始的切换失败原因）。最坏情况下回滚后引擎
    不可用，用户需手动重新加载。

    Args:
        prev_state (dict[str, Any]): :func:`_snapshot_engine_state` 返回的快照。
        error (Exception): 触发回滚的异常（仅用于日志诊断）。
    """
    import traceback

    tb: str = traceback.format_exc()
    error_msg: str = f"引擎切换失败: {type(error).__name__}: {error}\n\n详细错误:\n{tb}"
    logger.error(f"[引擎切换] {error_msg}")

    prev_engine: str | None = prev_state["engine"]
    logger.info(f"[引擎切换] 开始回滚到之前的引擎状态 (prev_engine={prev_engine})...")

    # M-R3: 先清理可能残留的半加载状态（新引擎可能加载了一半）
    try:
        registry.voxcpm_model = None
        registry.voxcpm_asr = None
        registry.indextts2_engine = None
        registry.current_engine = None
        for gname in list(registry.get_all_engine_instances().keys()):
            registry.clear_engine(gname)
    except Exception as cleanup_err:
        logger.error(f"[引擎切换] 回滚前清理失败: {cleanup_err}")

    # M-R3: 根据 prev_engine 重新加载模型
    # 注意：set_voxcpm_loaded / set_indextts2_loaded 内部会设置 current_engine
    if prev_engine == EngineName.VOXCPM2.value:
        try:
            logger.info("[引擎切换] 回滚: 重新加载 VoxCPM2 模型...")
            from ..gpu_backend import GPUBackend, GPUBackendManager

            backend: GPUBackend = GPUBackendManager.detect_backend()
            gpu_device: Any = _state.get_gpu_device()
            # 消费 generator 但不向前端推送状态（回滚过程对前端透明）
            for _ in _do_load_voxcpm2_internal(gpu_device, backend, include_denoiser=False):
                pass
            logger.info("[引擎切换] 回滚: VoxCPM2 模型重新加载完成")
        except Exception as reload_err:
            logger.error(f"[引擎切换] 回滚时重新加载 VoxCPM2 失败: {reload_err}")
    elif prev_engine in (EngineName.INDEXTTS2.value, EngineName.INDEXTTS20.value):
        try:
            _is_v20 = prev_engine == EngineName.INDEXTTS20.value
            logger.info(f"[引擎切换] 回滚: 重新加载 {'IndexTTS 2.0' if _is_v20 else 'IndexTTS2'} 引擎...")
            from ..engines.indextts2_engine import IndexTTS2Engine
            from ..gpu_backend import GPUBackend, GPUBackendManager

            backend = GPUBackendManager.detect_backend()
            new_engine: Any = IndexTTS2Engine(
                model_dir=get_indextts20_model_path() if _is_v20 else get_indextts2_model_path(),
                use_bf16=(backend != GPUBackend.CPU),
                version="2.0" if _is_v20 else "2.5",
            )
            registry.set_indextts2_loaded(new_engine, engine_name=prev_engine)
            logger.info("[引擎切换] 回滚: IndexTTS 引擎重新加载完成")
        except Exception as reload_err:
            logger.error(f"[引擎切换] 回滚时重新加载 IndexTTS 失败: {reload_err}")
    elif prev_engine:
        # 通用新式引擎回滚：重新走声明式加载流程
        try:
            logger.info(f"[引擎切换] 回滚: 重新加载 {prev_engine} 引擎...")
            for _ in _load_generic_engine(prev_engine):
                pass
            logger.info(f"[引擎切换] 回滚: {prev_engine} 引擎重新加载完成")
        except Exception as reload_err:
            logger.error(f"[引擎切换] 回滚时重新加载 {prev_engine} 失败: {reload_err}")
    else:
        logger.warning("[引擎切换] 回滚: 之前没有已加载的引擎，所有状态已置空")

    # E4: 资源清理
    gc.collect()
    from ..gpu_backend import GPUBackendManager

    with contextlib.suppress(Exception):
        GPUBackendManager.empty_cache()


def switch_engine(
    engine_name: str = EngineName.VOXCPM2.value,
) -> Generator[tuple[str, None, None, None], None, None]:
    """切换活跃引擎（5 阶段流程 + 完整回滚）。

    Switch the active engine with full rollback on failure.

    REFACTOR: [M-R1] 拆分为 5 个职责单一的辅助函数，单函数圈复杂度 25 → 各 <8。
    REFACTOR: [M-R3] _rollback_engine 重新加载 prev_engine 模型，保证回滚后可用。

    5 阶段触发条件与产出事件（每条 ``(status_text, None, None, None)`` 四元组，
    兼容调用方 ``for status_text, _, _, _ in gen:`` 解包）：
        1. **validate**（阶段 ①）：
           调用 :func:`_validate_engine_name`。若引擎名不在白名单直接抛
           ``EngineSwitchError``，**不**占用锁、**不**触发回滚。
           产出事件：无（同步校验）。

        2. **snapshot**（阶段 ②）：
           获取 ``_model_lock`` 后调用 :func:`_snapshot_engine_state` 保存
           当前引擎引用，供 rollback 时判断 ``prev_engine`` 名称使用。

        3. **prereq**（阶段 ③）：
           调用 :func:`_check_vram_prereq` 做显存基线预检。若失败抛
           ``InsufficientVRAMError``（属于 TTSError 子类，直接抛出不触发
           回滚——因为 unload_model 尚未执行，旧引擎完好无损）。

        4. **wait**（阶段 ④，非热待机路径）：
           若 ``_can_hot_standby`` 判定为 False（显存不足以双引擎常驻），
           则先**同步**调用 :func:`unload_model`（with 语句内部 try/finally
           负责锁与显存清理），再做
           ``synchronize → empty_cache → ipc_collect → _wait_vram_freed``
           的显存同步链，确保后续加载拿到的是干净的空闲显存。

        5. **rollback**（异常路径）：
           若非 TTSError 类异常（CUDA OOM / RuntimeError / I/O 错误等）
           发生在 unload_model 之后，则调用 :func:`_rollback_engine`
           按 M-R3 规则**重新加载 prev_engine**，最后抛出 ``EngineSwitchError``
           包装原异常。TTSError 子类（InsufficientVRAMError 等）不触发回滚。

    Args:
        engine_name (str): 目标引擎名，默认 ``EngineName.VOXCPM2.value``。

    Yields:
        tuple[str, None, None, None]:
            每个阶段 ``(status_text, None, None, None)`` 四元组；
            ``status_text`` 为当前阶段描述；其余三位置为历史兼容占位。
            异常退出时 finally 前会额外产出一条 ``(error_msg, None, None, None)``
            事件，``error_msg`` 首段包含 ``"失败"`` / ``"error"`` 关键字供
            调用方识别。

    Raises:
        InsufficientVRAMError: 空闲显存低于目标引擎基线要求（预检失败）。
        EngineSwitchError: 切换失败且已尝试回滚（原异常挂在 ``__cause__`` 上）。
    """
    from ..gpu_backend import GPUBackend, GPUBackendManager
    from ..model_registry import ENGINE_VRAM_REQUIREMENTS

    # M-R1: 校验引擎名称（阶段 ①，锁外执行，失败不影响旧引擎）
    engine_name = _validate_engine_name(engine_name)
    logger.info(f"[引擎切换] 目标: {engine_name}")

    prev_state: dict[str, Any] = {}
    # M-R8: 是否已经真正执行过 unload —— 决定 TTSError 走不走回滚（见下方 except）
    did_unload: bool = False
    try:
        with _model_lock:
            # M-R1: 快照前置状态（阶段 ②）
            prev_state = _snapshot_engine_state()

            backend: GPUBackend = GPUBackendManager.detect_backend()
            gpu_device: Any = _state.get_gpu_device()
            logger.info(f"[引擎切换] 使用设备 {gpu_device if gpu_device is not None else 'CPU'} 进行显存检查")

            # M-R1: VRAM 预检查（阶段 ③）
            logger.info("[引擎切换] 开始 VRAM 预检查...")
            _check_vram_prereq(engine_name, backend, gpu_device)

            # 检查热待机可能性
            hot_standby: bool = _can_hot_standby(engine_name)
            if hot_standby:
                logger.info("[引擎切换] 显存充足，使用热待机模式，跳过卸载")
                _progress_mgr.update_phase("显存充足，直接加载新引擎...")
                status_text: str = "显存充足，直接加载新引擎"
                yield status_text, None, None, None
            else:
                logger.info("[引擎切换] 显存不足，使用传统切换模式")
                _progress_mgr.update_phase("正在卸载旧引擎...")
                status_text = "正在卸载旧引擎并释放显存"
                yield status_text, None, None, None

                # 卸载前记录 allocated 基线。_wait_vram_freed 需要它才能判断
                # "到底回收了多少"——绝对空闲量在假释放时照样超过 500MB 阈值。
                vram_baseline_bytes: int = 0
                if backend != GPUBackend.CPU and gpu_device is not None:
                    with contextlib.suppress(Exception):
                        vram_baseline_bytes = GPUBackendManager.memory_allocated(gpu_device)
                expected_release_bytes: int = int(
                    ENGINE_VRAM_REQUIREMENTS.get(prev_state["engine"] or "", 0.0) * 1024**3
                )

                # 阶段 ④：同步调用 unload_model（其内部用 with _model_lock 加锁，
                # 因为 _model_lock 是 RLock，允许同一线程重入 acquire）
                unload_model()
                did_unload = True

                # M-R1: 等待显存释放（unload 已释放锁，但 RLock 允许我们仍在外层 with 内）
                if backend != GPUBackend.CPU and gpu_device is not None:
                    with contextlib.suppress(Exception):
                        GPUBackendManager.synchronize(gpu_device)
                    with contextlib.suppress(Exception):
                        GPUBackendManager.empty_cache()
                    with contextlib.suppress(Exception):
                        GPUBackendManager.ipc_collect(gpu_device)

                    vram_freed: bool = _wait_vram_freed(
                        gpu_device,
                        baseline_allocated=vram_baseline_bytes,
                        expected_release_bytes=expected_release_bytes,
                    )
                    with contextlib.suppress(Exception):
                        GPUBackendManager.empty_cache()

                    if vram_freed:
                        logger.info("[引擎切换] VRAM 已释放")
                    else:
                        logger.warning(
                            f"[引擎切换] 卸载后显存回收未达预期（期望约 "
                            f"{expected_release_bytes / 1024**3:.2f}GB），旧引擎可能仍被引用；"
                            "继续切换流程，若新引擎加载失败会回滚到原引擎"
                        )

                _progress_mgr.update_phase("正在清理 VRAM...")
                status_text = "正在清理显存缓存"
                yield status_text, None, None, None

            # 阶段 ⑤：加载新引擎
            #
            # M-R9: 各 loader 生成器把终局失败 **yield** 成一条含「失败」的进度文本
            # 而不是 raise（FileNotFoundError/OSError 路径就是这样）。此前本函数把它
            # 当普通进度原样转发、正常收尾，调用方于是回 {"status":"ok"} 而 message
            # 里写着"IndexTTS 2.5 加载失败: …" —— 用户看到"成功"，实际一个引擎都没
            # 加载（且旧引擎已被卸载）。这里把失败文本升级成异常，交给下面的
            # did_unload 分支回滚原引擎。
            def _forward(loader: Any) -> Any:
                for status_tuple in loader:
                    yield status_tuple
                    text: str = str(status_tuple[0] or "")
                    if "失败" in text or "error" in text.lower():
                        raise EngineLoadError(f"{engine_name} 加载失败：{text}")

            if engine_name == EngineName.VOXCPM2.value:
                yield from _forward(_load_voxcpm2_engine(gpu_device, backend))
            elif engine_name == EngineName.INDEXTTS2.value:
                # #84: 热待机必须跳过加载前清场（语义就是双引擎常驻）；传统
                # 路径已先 unload_model()，传 True 只是幂等空操作。
                yield from _forward(load_indextts2(auto_unload=not hot_standby))
            elif engine_name == EngineName.INDEXTTS20.value:
                yield from _forward(load_indextts20(auto_unload=not hot_standby))
            else:
                # 通用新式引擎（声明式注册）
                yield from _forward(_load_generic_engine(engine_name))

    except TTSError as biz_err:
        # 业务异常原样抛出（InsufficientVRAMError 等），保留调用方的 HTTP 状态码映射
        #
        # M-R8: 但必须区分"预检阶段失败"和"卸载之后失败"。预检失败时
        # unload_model 尚未执行，旧引擎完好，无需回滚；而一旦已经卸载
        # （典型：加载器复核判定显存不足抛 InsufficientVRAMError），不恢复
        # 原引擎就会把用户留在"一个引擎都没有"的状态，只能重启进程才能恢复。
        if did_unload and prev_state:
            with contextlib.suppress(Exception):
                _rollback_engine(prev_state, biz_err)
        raise
    except Exception as e:
        error_msg: str = f"引擎切换失败: {type(e).__name__}: {e}"
        # 产出一条 error 事件给前端进度流（保持 tuple 格式兼容解包）
        yield error_msg, None, None, None
        # M-R1/M-R3: 委托回滚逻辑给 _rollback_engine（仅在 prev_state 已快照时）
        if prev_state:
            with contextlib.suppress(Exception):
                _rollback_engine(prev_state, e)
        raise EngineSwitchError(error_msg) from e
    finally:
        with contextlib.suppress(Exception):
            free_gpu_memory()


def _load_voxcpm2_engine(
    gpu_device: Any,
    backend: Any,
) -> Generator[tuple[str, None, None, None], None, None]:
    """引擎切换流程中加载 VoxCPM2（内部辅助生成器）。

    本函数为 :func:`switch_engine` 专用的 VoxCPM2 加载包装器，
    与公开的 :func:`load_voxcpm2` 区别在于：
    1. 已在调用方持有 ``_model_lock`` 的上下文内执行（不重复 acquire）。
    2. 强制启用 ``include_denoiser=True``，加载语音增强/降噪子模块，
       因为引擎切换场景通常为正式推理使用，需要完整音质。
    3. 通过 ``_progress_mgr.update_phase()`` 同步更新全局进度管理器，
       与前端 SSE 进度条联动。
    4. 每条产出前通过 logger 记录阶段日志便于追踪切换时序。

    Args:
        gpu_device: GPU 设备索引（CUDA/MPS 为 int，CPU 为 None）。
        backend: ``GPUBackend`` 枚举值，标识当前计算后端。

    Yields:
        tuple[str, None, None, None]:
            与 :func:`_do_load_voxcpm2_internal` 产出格式一致的四元组
            ``(status_text, None, None, None)``，直接透传给 switch_engine
            的生成器消费者（最终通过 SSE 推送给前端）。

    Raises:
        Exception: 加载过程中任何异常（权重缺失、CUDA OOM、文件权限等）
            会被 :func:`switch_engine` 的外层 try/except 捕获并触发回滚，
            本函数不做异常吞掉。
    """
    _progress_mgr.update_phase("正在加载新引擎...")
    for status in _do_load_voxcpm2_internal(gpu_device, backend, include_denoiser=True):
        logger.info(f"[引擎切换] {status[0]}")
        yield status


def _load_generic_engine(
    engine_name: str,
) -> Generator[tuple[str, None, None, None], None, None]:
    """加载通过 engine_registry 声明式注册的通用新式引擎（内部辅助生成器）。

    流程：
        1. 从 engine_registry 解析引擎类（触发懒导入）。
        2. 实例化（无参构造，引擎内部从 config 读取权重路径）。
        3. 调用 ``engine.load()`` 加载权重到显存/内存。
        4. 通过 ``registry.set_engine_loaded`` 注册到全局状态。
    """
    from ..engine_interface import engine_registry

    _progress_mgr.update_phase("正在加载新引擎...")
    status_text: str = f"正在解析引擎 {engine_name}..."
    logger.info(f"[引擎切换] {status_text}")
    yield status_text, None, None, None

    engine_class: Any = engine_registry.get(engine_name)
    if engine_class is None:
        raise EngineLoadError(
            f"引擎 '{engine_name}' 无法解析（未注册或依赖缺失）。请确认已安装对应依赖并下载模型权重。"
        )

    status_text = f"正在加载 {engine_name} 模型..."
    logger.info(f"[引擎切换] {status_text}")
    yield status_text, None, None, None

    start_time: float = time.time()
    engine: Any = engine_class()
    engine.load()
    load_time: float = time.time() - start_time
    logger.info(f"[引擎切换] {engine_name} 加载完成，耗时 {load_time:.1f}s")

    registry.set_engine_loaded(engine_name, engine)

    # VRAM 记录（best-effort）
    try:
        from ..gpu_backend import GPUBackend, GPUBackendManager

        monitor: Any = get_health_monitor()
        if GPUBackendManager.detect_backend() != GPUBackend.CPU:
            vram_mb: float = GPUBackendManager.memory_allocated() / (1024**2)
            monitor.record_vram_usage(vram_mb)
            monitor.set_model_status("ready")
    except Exception as e:
        logger.debug(f"[{engine_name}] VRAM 记录失败: {e}")

    status_text = f"{engine_name} 引擎就绪"
    logger.info(f"[引擎切换] {status_text}")
    yield status_text, None, None, None
