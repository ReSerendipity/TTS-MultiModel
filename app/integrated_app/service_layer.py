"""服务层模块 (Chapter 11) — 将业务逻辑从路由处理器中分离。

提供三大服务类，封装核心业务逻辑：
1. TTSGenerationService — 统一语音生成入口（设计/克隆/终极/剧本/流式）
2. ModelService — 模型加载/卸载/引擎切换
3. PersonaService — 音色管理（列表/查询/创建/删除）

设计要点：
- 每个服务方法包含完整的引擎就绪检查、显存熔断检测、进度追踪、结果保存
- 延迟导入重量级依赖（torch / voxcpm / funasr 等），减少启动时间
- 所有异常统一转换为 TTSError 层次结构，便于路由层处理
- 日志统一使用 logging.getLogger("tts_multimodel")
- 线程安全：共享状态通过 registry 的 RLock 保护
【职责】业务服务层（跨路由/引擎的用例编排）。【边界】不直接碰模型加载细节；路由层经本层访问业务能力。

"""

import logging
import os
import threading
import time
from collections.abc import AsyncGenerator
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("tts_multimodel")

# 显存熔断阈值（百分比）
_VRAM_CIRCUIT_BREAKER_THRESHOLD = 90


# ======================================================================
# 数据类
# ======================================================================


@dataclass
class GenerationResult:
    """语音生成结果的数据类。

    Attributes:
        audio_path: 生成音频文件的绝对路径。
        message: 面向用户的结果消息。
        duration: 音频时长（秒）。
        engine: 使用的引擎名称。
        params: 实际使用的生成参数。
    """

    audio_path: str = ""
    message: str = ""
    duration: float = 0.0
    engine: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return asdict(self)


@dataclass
class LoadResult:
    """模型加载结果的数据类。

    Attributes:
        success: 是否加载成功。
        message: 面向用户的结果消息。
        engine: 加载的引擎名称。
        load_time: 加载耗时（秒）。
    """

    success: bool = False
    message: str = ""
    engine: str = ""
    load_time: float = 0.0


@dataclass
class SwitchResult:
    """引擎切换结果的数据类。

    Attributes:
        success: 是否切换成功。
        message: 面向用户的结果消息。
        from_engine: 源引擎名称。
        to_engine: 目标引擎名称。
        switch_time: 切换耗时（秒）。
    """

    success: bool = False
    message: str = ""
    from_engine: str = ""
    to_engine: str = ""
    switch_time: float = 0.0


@dataclass
class ModelStatus:
    """模型状态的数据类。

    Attributes:
        engine: 当前引擎名称。
        loaded: 是否已加载模型。
        ready: 是否就绪可用。
        vram_usage_percent: 显存占用百分比（-1 表示无法获取）。
        info: 引擎详细信息字典。
    """

    engine: str | None = None
    loaded: bool = False
    ready: bool = False
    vram_usage_percent: float = -1.0
    info: dict[str, Any] = field(default_factory=dict)


@dataclass
class PersonaInfo:
    """音色信息的数据类。

    Attributes:
        name: 音色名称。
        description: 音色描述。
        wav_path: 参考音频文件路径。
        exists: 参考音频文件是否存在。
        wav_size_kb: 音频文件大小（KB）。
        created_at: 创建时间字符串。
    """

    name: str = ""
    description: str = ""
    wav_path: str = ""
    exists: bool = False
    wav_size_kb: float = 0.0
    created_at: str = ""


# ======================================================================
# 辅助函数
# ======================================================================


def _check_vram_circuit_breaker() -> bool:
    """检查显存熔断器是否触发。

    当显存占用超过阈值时返回 True，表示应终止生成以防止 OOM。

    Returns:
        True 表示显存占用过高，应终止操作。
    """
    try:
        from .gpu_backend import GPUBackend, GPUBackendManager

        backend = GPUBackendManager.detect_backend()
        if backend == GPUBackend.CPU:
            return False

        from .gpu_utils import get_gpu_device

        device = get_gpu_device()
        if device is None:
            return False

        props = GPUBackendManager.get_device_properties(device)
        total = props.get("total_memory", 0)
        if total <= 0:
            return False

        allocated = GPUBackendManager.memory_allocated(device)
        usage_percent = (allocated / total) * 100

        if usage_percent > _VRAM_CIRCUIT_BREAKER_THRESHOLD:
            logger.warning(
                f"[VRAM熔断] 显存占用 {usage_percent:.1f}% 超过阈值 {_VRAM_CIRCUIT_BREAKER_THRESHOLD}%，终止生成"
            )
            return True

    except Exception as e:
        logger.debug(f"[VRAM熔断] 显存检查异常（忽略）: {e}")
    return False


def _get_vram_usage_percent() -> float:
    """获取当前显存占用百分比。

    Returns:
        显存占用百分比，无法获取时返回 -1.0。
    """
    try:
        from .gpu_backend import GPUBackend, GPUBackendManager

        backend = GPUBackendManager.detect_backend()
        if backend == GPUBackend.CPU:
            return 0.0

        from .gpu_utils import get_gpu_device

        device = get_gpu_device()
        if device is None:
            return -1.0

        props = GPUBackendManager.get_device_properties(device)
        total = props.get("total_memory", 0)
        if total <= 0:
            return -1.0

        allocated = GPUBackendManager.memory_allocated(device)
        return (allocated / total) * 100

    except Exception as e:
        logger.debug(f"[VRAM] 显存占用查询失败 (返回 -1.0): {e}")
        return -1.0


# ======================================================================
# TTSGenerationService — 统一语音生成服务
# ======================================================================


class TTSGenerationService:
    """统一语音生成服务。

    封装各引擎的生成逻辑，提供统一的调用入口。
    每个方法自动处理：
    - 引擎就绪检查
    - 显存熔断检测
    - 进度追踪（start/advance/complete）
    - 结果保存与版本记录
    - 异常转换与日志记录

    Usage::

        svc = TTSGenerationService()
        result = svc.generate_voice_design(
            text="你好世界",
            instruction="温柔的女性声音",
        )
    """

    def _ensure_engine_ready(self, expected_engine: str | None = None) -> None:
        """检查当前引擎是否就绪，未就绪时抛出异常。

        Args:
            expected_engine: 期望的引擎名称，None 表示任意引擎。

        Raises:
            EngineNotLoadedError: 引擎未加载。
            EngineSwitchError: 引擎不匹配期望值。
        """
        from .exceptions import EngineNotLoadedError, EngineSwitchError
        from .model_registry import registry

        if not registry.is_engine_ready():
            raise EngineNotLoadedError("引擎未加载，请先加载模型")

        if expected_engine and registry.current_engine != expected_engine:
            raise EngineSwitchError(f"当前引擎为 {registry.current_engine}，期望 {expected_engine}")

    def _save_version_record(
        self,
        audio_path: str,
        text: str,
        params: dict[str, Any],
        engine: str,
        parent_id: str | None = None,
    ) -> str | None:
        """保存生成版本记录（best-effort，失败不影响生成结果）。

        Args:
            audio_path: 音频文件路径。
            text: 输入文本。
            params: 生成参数。
            engine: 引擎名称。
            parent_id: 父版本 ID。

        Returns:
            生成记录 ID，失败时返回 None。
        """
        try:
            from .generation_versioning import get_version_manager

            vm = get_version_manager()
            return vm.save_generation(
                audio_path=audio_path,
                text=text,
                params=params,
                engine=engine,
                parent_id=parent_id,
            )
        except Exception as e:
            logger.warning(f"[TTSGenerationService] 保存版本记录失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 生成方法
    # ------------------------------------------------------------------

    def generate_voice_design(
        self,
        text: str,
        instruction: str = "",
        cfg_value: float = 2.0,
        inference_timesteps: int = 10,
        denoise: bool = True,
        normalize: bool = True,
        **params: Any,
    ) -> GenerationResult:
        """语音设计生成。

        Args:
            text: 输入文本。
            instruction: 语音描述指令（如 "温柔的女性声音"）。
            cfg_value: CFG 引导强度。
            inference_timesteps: 推理步数。
            denoise: 是否降噪。
            normalize: 是否响度归一化。
            **params: 其他生成参数。

        Returns:
            GenerationResult 包含音频路径和元信息。

        Raises:
            EngineNotLoadedError: 引擎未加载。
            GenerationError: 生成失败。
            InsufficientVRAMError: 显存不足。
        """
        self._ensure_engine_ready()
        if _check_vram_circuit_breaker():
            from .exceptions import InsufficientVRAMError

            raise InsufficientVRAMError("显存占用过高，终止生成")

        from .model_registry import registry

        engine_name = registry.current_engine or "unknown"

        start_time = time.time()
        try:
            engine = registry.get_current_engine()
            if engine is None:
                from .exceptions import EngineNotLoadedError

                raise EngineNotLoadedError("无法获取当前引擎实例")

            result = engine.generate_voice_design(
                text=text,
                instruction=instruction,
                normalize=normalize,
                cfg_value=cfg_value,
                inference_timesteps=inference_timesteps,
                denoise=denoise,
                **params,
            )

            # 解析结果：引擎返回 (audio_info, message) 或类似结构
            audio_path, message = self._extract_generation_result(result)
            duration = self._estimate_duration(audio_path)
            elapsed = time.time() - start_time

            gen_params = {
                "instruction": instruction,
                "cfg_value": cfg_value,
                "inference_timesteps": inference_timesteps,
                "denoise": denoise,
                "normalize": normalize,
            }

            self._save_version_record(audio_path, text, gen_params, engine_name)

            logger.info(
                f"[TTSGenerationService] 语音设计完成: {elapsed:.1f}s, 时长 {duration:.1f}s, 引擎 {engine_name}"
            )
            return GenerationResult(
                audio_path=audio_path,
                message=message,
                duration=duration,
                engine=engine_name,
                params=gen_params,
            )

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"[TTSGenerationService] 语音设计失败: {e}, 耗时 {elapsed:.1f}s")
            raise

    def generate_voice_clone(
        self,
        text: str,
        reference_audio: str | None = None,
        instruction: str = "",
        cfg_value: float = 2.0,
        inference_timesteps: int = 10,
        denoise: bool = True,
        normalize: bool = True,
        **params: Any,
    ) -> GenerationResult:
        """语音克隆生成。

        使用参考音频进行声音克隆，生成与参考音色相似的语音。

        Args:
            text: 输入文本。
            reference_audio: 参考音频路径。
            instruction: 语音描述指令。
            cfg_value: CFG 引导强度。
            inference_timesteps: 推理步数。
            denoise: 是否降噪。
            normalize: 是否响度归一化。
            **params: 其他生成参数。

        Returns:
            GenerationResult 包含音频路径和元信息。

        Raises:
            EngineNotLoadedError: 引擎未加载。
            GenerationError: 生成失败。
            InsufficientVRAMError: 显存不足。
        """
        self._ensure_engine_ready()
        if _check_vram_circuit_breaker():
            from .exceptions import InsufficientVRAMError

            raise InsufficientVRAMError("显存占用过高，终止生成")

        from .model_registry import registry

        engine_name = registry.current_engine or "unknown"

        start_time = time.time()
        try:
            engine = registry.get_current_engine()
            if engine is None:
                from .exceptions import EngineNotLoadedError

                raise EngineNotLoadedError("无法获取当前引擎实例")

            result = engine.generate_voice_clone(
                text=text,
                reference_audio_path=reference_audio,
                instruction=instruction,
                normalize=normalize,
                cfg_value=cfg_value,
                inference_timesteps=inference_timesteps,
                denoise=denoise,
                **params,
            )

            audio_path, message = self._extract_generation_result(result)
            duration = self._estimate_duration(audio_path)
            elapsed = time.time() - start_time

            gen_params = {
                "reference_audio": reference_audio,
                "instruction": instruction,
                "cfg_value": cfg_value,
                "inference_timesteps": inference_timesteps,
                "denoise": denoise,
                "normalize": normalize,
            }

            self._save_version_record(audio_path, text, gen_params, engine_name)

            logger.info(
                f"[TTSGenerationService] 语音克隆完成: {elapsed:.1f}s, 时长 {duration:.1f}s, 引擎 {engine_name}"
            )
            return GenerationResult(
                audio_path=audio_path,
                message=message,
                duration=duration,
                engine=engine_name,
                params=gen_params,
            )

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"[TTSGenerationService] 语音克隆失败: {e}, 耗时 {elapsed:.1f}s")
            raise

    def generate_ultimate_clone(
        self,
        text: str,
        instruction: str = "",
        ref_audio_path: str | None = None,
        advanced_cfg: float = 2.0,
        advanced_norm: bool = True,
        advanced_denoise: float = 1.0,
        advanced_steps: int = 10,
        advanced_seed: int = -1,
        **params: Any,
    ) -> GenerationResult:
        """终极克隆生成（完整参数控制）。

        仅支持 VoxCPM2 引擎，提供完整的高级参数控制，包括降噪强度、随机种子等。

        Args:
            text: 输入文本。
            instruction: 语音描述指令。
            ref_audio_path: 参考音频路径。
            advanced_cfg: CFG 引导强度。
            advanced_norm: 是否响度归一化。
            advanced_denoise: 降噪强度（0.0-1.0）。
            advanced_steps: 推理步数。
            advanced_seed: 随机种子（-1 为随机）。
            **params: 其他生成参数。

        Returns:
            GenerationResult 包含音频路径和元信息。

        Raises:
            EngineNotLoadedError: 引擎未加载。
            EngineSwitchError: 当前引擎不是 voxcpm2。
            GenerationError: 生成失败。
            InsufficientVRAMError: 显存不足。
        """
        self._ensure_engine_ready(expected_engine="voxcpm2")
        if _check_vram_circuit_breaker():
            from .exceptions import InsufficientVRAMError

            raise InsufficientVRAMError("显存占用过高，终止生成")

        from .model_registry import registry

        start_time = time.time()
        try:
            engine = registry.get_current_engine()
            if engine is None:
                from .exceptions import EngineNotLoadedError

                raise EngineNotLoadedError("无法获取当前引擎实例")

            # 终极克隆需要 ControllableTTSEngine 协议
            result = engine.generate_ultimate_clone(
                text=text,
                instruction=instruction,
                ref_audio_path=ref_audio_path,
                advanced_cfg=advanced_cfg,
                advanced_norm=advanced_norm,
                advanced_denoise=advanced_denoise,
                advanced_steps=advanced_steps,
                advanced_seed=advanced_seed,
                **params,
            )

            audio_path, message = self._extract_generation_result(result)
            duration = self._estimate_duration(audio_path)
            elapsed = time.time() - start_time

            gen_params = {
                "instruction": instruction,
                "ref_audio_path": ref_audio_path,
                "advanced_cfg": advanced_cfg,
                "advanced_norm": advanced_norm,
                "advanced_denoise": advanced_denoise,
                "advanced_steps": advanced_steps,
                "advanced_seed": advanced_seed,
            }

            self._save_version_record(audio_path, text, gen_params, "voxcpm2")

            logger.info(f"[TTSGenerationService] 终极克隆完成: {elapsed:.1f}s, 时长 {duration:.1f}s")
            return GenerationResult(
                audio_path=audio_path,
                message=message,
                duration=duration,
                engine="voxcpm2",
                params=gen_params,
            )

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"[TTSGenerationService] 终极克隆失败: {e}, 耗时 {elapsed:.1f}s")
            raise

    def generate_script(
        self,
        text: str,
        speaker_map: dict | None = None,
        persona_map: dict | None = None,
        **params: Any,
    ) -> GenerationResult:
        """剧本工坊生成（多角色对话）。

        支持多角色对话剧本生成，通过 speaker_map 和 persona_map 指定角色音色映射。

        Args:
            text: 剧本文本。
            speaker_map: 说话人映射。
            persona_map: 音色映射。
            **params: 其他生成参数。

        Returns:
            GenerationResult 包含音频路径和元信息。

        Raises:
            EngineNotLoadedError: 引擎未加载。
            GenerationError: 生成失败。
            InsufficientVRAMError: 显存不足。
        """
        self._ensure_engine_ready()
        if _check_vram_circuit_breaker():
            from .exceptions import InsufficientVRAMError

            raise InsufficientVRAMError("显存占用过高，终止生成")

        from .model_registry import registry

        engine_name = registry.current_engine or "unknown"

        start_time = time.time()
        try:
            engine = registry.get_current_engine()
            if engine is None:
                from .exceptions import EngineNotLoadedError

                raise EngineNotLoadedError("无法获取当前引擎实例")

            result = engine.generate_script(
                text=text,
                speaker_map=speaker_map,
                persona_map=persona_map,
                **params,
            )

            audio_path, message = self._extract_generation_result(result)
            duration = self._estimate_duration(audio_path)
            elapsed = time.time() - start_time

            gen_params = {
                "speaker_map": speaker_map,
                "persona_map": persona_map,
            }

            self._save_version_record(audio_path, text, gen_params, engine_name)

            logger.info(
                f"[TTSGenerationService] 剧本生成完成: {elapsed:.1f}s, 时长 {duration:.1f}s, 引擎 {engine_name}"
            )
            return GenerationResult(
                audio_path=audio_path,
                message=message,
                duration=duration,
                engine=engine_name,
                params=gen_params,
            )

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"[TTSGenerationService] 剧本生成失败: {e}, 耗时 {elapsed:.1f}s")
            raise

    async def generate_streaming(
        self,
        text: str,
        reference_audio_path: str | None = None,
        **params: Any,
    ) -> AsyncGenerator[bytes, None]:
        """流式生成（长文本分段流式输出）。

        Args:
            text: 输入文本。
            reference_audio_path: 参考音频路径。
            **params: 其他生成参数。

        Yields:
            音频数据分块（bytes）。

        Raises:
            EngineNotLoadedError: 引擎未加载。
        """
        self._ensure_engine_ready()
        if _check_vram_circuit_breaker():
            from .exceptions import InsufficientVRAMError

            raise InsufficientVRAMError("显存占用过高，终止生成")

        from .model_registry import registry

        try:
            engine = registry.get_current_engine()
            if engine is None:
                from .exceptions import EngineNotLoadedError

                raise EngineNotLoadedError("无法获取当前引擎实例")

            # 流式生成使用生成器
            gen = engine.generate_streaming(
                text=text,
                reference_audio_path=reference_audio_path,
                **params,
            )

            for chunk in gen:
                # 将音频数据块转为 bytes
                if isinstance(chunk, bytes):
                    yield chunk
                elif hasattr(chunk, "tobytes"):
                    yield chunk.tobytes()
                elif isinstance(chunk, tuple):
                    # 引擎可能返回 (sr, wav_data) 格式
                    # 只取音频数据部分
                    if len(chunk) >= 2 and hasattr(chunk[1], "tobytes"):
                        yield chunk[1].tobytes()
                else:
                    yield bytes(chunk)

        except Exception as e:
            logger.error(f"[TTSGenerationService] 流式生成失败: {e}")
            raise

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_generation_result(result: Any) -> tuple[str, str]:
        """从引擎生成结果中提取音频路径和消息。

        引擎返回格式多样：
        - (sample_rate, wav_data, filename), message
        - (audio_path,), message
        - audio_path, message

        Args:
            result: 引擎返回的原始结果。

        Returns:
            (audio_path, message) 元组。
        """
        if isinstance(result, tuple) and len(result) == 2:
            audio_info, message = result
            if isinstance(audio_info, tuple) and len(audio_info) >= 3:
                # (sample_rate, wav_data, filename) 格式
                filename = audio_info[2]
                from .config import SAVE_DIR

                audio_path = os.path.join(SAVE_DIR, filename) if filename else ""
            elif isinstance(audio_info, str):
                audio_path = audio_info
            elif isinstance(audio_info, tuple) and len(audio_info) >= 1:
                audio_path = str(audio_info[0])
            else:
                audio_path = str(audio_info) if audio_info else ""
            return audio_path, str(message)

        # 兜底：直接返回字符串表示
        return str(result) if result else "", "生成完成"

    @staticmethod
    def _estimate_duration(audio_path: str) -> float:
        """根据音频文件估算时长（秒）。

        Args:
            audio_path: 音频文件路径。

        Returns:
            估算时长（秒），无法获取时返回 0.0。
        """
        if not audio_path or not os.path.exists(audio_path):
            return 0.0
        try:
            import soundfile as sf

            info = sf.info(audio_path)
            return info.duration
        except Exception:
            # 回退：基于文件大小粗略估算（WAV 16-bit 24kHz 单声道 ≈ 48KB/s）
            try:
                file_size = os.path.getsize(audio_path)
                return file_size / 48000.0
            except OSError:
                return 0.0


# ======================================================================
# ModelService — 模型管理服务
# ======================================================================


class ModelService:
    """模型管理服务。

    封装 model_manager.py 的函数，提供错误处理和日志记录。

    Usage::

        svc = ModelService()
        result = svc.load_model("voxcpm2")
        status = svc.get_model_status()
    """

    def load_model(self, engine: str = "voxcpm2") -> LoadResult:
        """加载指定引擎的模型。

        Args:
            engine: 引擎名称（"voxcpm2" 或 "indextts2"）。

        Returns:
            LoadResult 包含加载结果信息。
        """
        from .exceptions import TTSError

        start_time = time.time()
        try:
            from .model_manager import load_indextts2, load_voxcpm2

            if engine == "voxcpm2":
                # 消费 generator 获取最终状态
                final_status = ""
                for status_tuple in load_voxcpm2():
                    final_status = status_tuple[0]
                load_time = time.time() - start_time

                # 检查是否成功加载
                from .model_registry import registry

                if registry.is_voxcpm_ready():
                    logger.info(f"[ModelService] VoxCPM2 加载成功，耗时 {load_time:.1f}s")
                    return LoadResult(
                        success=True,
                        message="VoxCPM2 加载成功",
                        engine="voxcpm2",
                        load_time=load_time,
                    )
                else:
                    return LoadResult(
                        success=False,
                        message=final_status or "VoxCPM2 加载失败",
                        engine="voxcpm2",
                        load_time=load_time,
                    )

            elif engine == "indextts2":
                final_status = ""
                for status_tuple in load_indextts2():
                    final_status = status_tuple[0]
                load_time = time.time() - start_time

                from .model_registry import registry

                if registry.is_indextts2_ready():
                    logger.info(f"[ModelService] IndexTTS2 加载成功，耗时 {load_time:.1f}s")
                    return LoadResult(
                        success=True,
                        message="IndexTTS 2.5 加载成功",
                        engine="indextts2",
                        load_time=load_time,
                    )
                else:
                    return LoadResult(
                        success=False,
                        message=final_status or "IndexTTS 2.5 加载失败",
                        engine="indextts2",
                        load_time=load_time,
                    )

            else:
                return LoadResult(
                    success=False,
                    message=f"不支持的引擎: {engine}",
                    engine=engine,
                )

        except TTSError:
            raise
        except Exception as e:
            load_time = time.time() - start_time
            logger.error(f"[ModelService] 模型加载异常: {e}")
            return LoadResult(
                success=False,
                message=f"加载失败: {type(e).__name__}: {e}",
                engine=engine,
                load_time=load_time,
            )

    def unload_model(self) -> None:
        """卸载当前加载的模型，释放显存资源。

        Raises:
            TTSError: 卸载过程出错。
        """
        from .model_manager import unload_model

        logger.info("[ModelService] 正在卸载模型...")
        try:
            unload_model()
            logger.info("[ModelService] 模型卸载完成")
        except Exception as e:
            logger.error(f"[ModelService] 模型卸载失败: {e}")
            raise

    def switch_engine(self, engine: str = "voxcpm2") -> SwitchResult:
        """切换引擎。

        Args:
            engine: 目标引擎名称。

        Returns:
            SwitchResult 包含切换结果信息。
        """
        from .exceptions import EngineSwitchError, TTSError
        from .model_registry import registry

        from_engine = registry.current_engine or "none"
        start_time = time.time()

        try:
            from .model_manager import switch_engine as _switch_engine

            # 消费 generator 获取最终状态
            final_status = ""
            for status_tuple in _switch_engine(engine):
                final_status = status_tuple[0]

            switch_time = time.time() - start_time

            logger.info(f"[ModelService] 引擎切换完成: {from_engine} -> {engine}, 耗时 {switch_time:.1f}s")
            return SwitchResult(
                success=True,
                message=final_status or "引擎切换成功",
                from_engine=from_engine,
                to_engine=engine,
                switch_time=switch_time,
            )

        except EngineSwitchError:
            raise
        except TTSError:
            raise
        except Exception as e:
            switch_time = time.time() - start_time
            logger.error(f"[ModelService] 引擎切换异常: {e}")
            raise EngineSwitchError(f"引擎切换失败: {type(e).__name__}: {e}") from e

    def get_model_status(self) -> ModelStatus:
        """获取当前模型状态。

        Returns:
            ModelStatus 包含引擎信息、就绪状态、显存占用等。
        """
        from .model_registry import registry

        engine = registry.current_engine
        loaded = registry.model_loaded
        ready = registry.is_engine_ready()
        vram_percent = _get_vram_usage_percent()
        info = registry.get_current_model_info()

        return ModelStatus(
            engine=engine,
            loaded=loaded,
            ready=ready,
            vram_usage_percent=round(vram_percent, 1),
            info=info,
        )


# ======================================================================
# PersonaService — 音色管理服务
# ======================================================================


class PersonaService:
    """音色管理服务。

    封装 persona_manager.py 的函数，提供验证、缓存和错误处理。

    Usage::

        svc = PersonaService()
        personas = svc.list_personas()
        info = svc.get_persona("my_voice")
        new = svc.create_persona("new_voice", "/path/to/audio.wav", "描述")
        deleted = svc.delete_persona("old_voice")
    """

    def __init__(self) -> None:
        """初始化 PersonaService，创建带 TTL 的音色信息缓存。"""
        self._cache: dict[str, PersonaInfo] = {}
        self._cache_lock = threading.Lock()
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 30.0

    def _is_cache_valid(self) -> bool:
        """检查缓存是否在有效期内。

        Returns:
            True 表示缓存有效可使用，False 表示需要重新加载。
        """
        return (time.time() - self._cache_timestamp) < self._cache_ttl

    def _invalidate_cache(self) -> None:
        """清除所有缓存数据并重置时间戳，在音色创建/删除后调用。"""
        with self._cache_lock:
            self._cache.clear()
            self._cache_timestamp = 0.0

    def list_personas(self, search_keyword: str = "") -> list[PersonaInfo]:
        """获取音色列表。

        Args:
            search_keyword: 搜索关键词（可选）。

        Returns:
            PersonaInfo 列表。
        """
        from .persona_manager import get_persona_detail_table, get_persona_list

        # 使用 get_persona_list 获取名称列表
        names = get_persona_list(search_keyword)
        if not names or names == ["(暂无音色)"]:
            return []

        # 使用 get_persona_detail_table 获取详情
        table_data = get_persona_detail_table(search_keyword)

        result: list[PersonaInfo] = []
        for row in table_data:
            if row[0] == "暂无音色":
                continue
            name = row[0]
            status = row[1]
            size_str = row[2]
            time_str = row[3]
            desc = row[4] if len(row) > 4 else ""

            # 解析文件大小
            wav_size_kb = 0.0
            if size_str and size_str != "-":
                try:
                    wav_size_kb = float(size_str.replace(" KB", ""))
                except (ValueError, AttributeError):
                    logger.debug(f"[PersonaService] 文件大小解析失败: {size_str!r}")

            info = PersonaInfo(
                name=name,
                description=desc if desc != "-" else "",
                wav_path=os.path.join(self._get_persona_dir(), f"{name}.wav"),
                exists=status.startswith("✅"),
                wav_size_kb=wav_size_kb,
                created_at=time_str if time_str != "-" else "",
            )
            result.append(info)

        # 更新缓存
        with self._cache_lock:
            for info in result:
                self._cache[info.name] = info
            self._cache_timestamp = time.time()

        return result

    def get_persona(self, name: str) -> PersonaInfo | None:
        """获取指定音色的详细信息。

        Args:
            name: 音色名称。

        Returns:
            PersonaInfo 实例，不存在时返回 None。
        """
        # 先查缓存
        with self._cache_lock:
            if self._is_cache_valid() and name in self._cache:
                return self._cache[name]

        # 验证名称合法性
        from .config import _PERSONA_NAME_RE
        from .persona_manager import load_persona_embedding

        if not name or not _PERSONA_NAME_RE.match(name):
            return None

        # 尝试加载音色嵌入来验证存在性
        embedding = load_persona_embedding(name)
        if embedding is None:
            return None

        wav_path, ref_text = embedding
        persona_dir = self._get_persona_dir()
        txt_path = os.path.join(persona_dir, f"{name}.txt")

        # 读取描述
        description = ref_text
        if not description and os.path.exists(txt_path):
            try:
                with open(txt_path, encoding="utf-8") as f:
                    description = f.read()
            except OSError as e:
                logger.debug(f"[PersonaService] 读取描述文件失败 {txt_path}: {e}")

        # 获取文件信息
        wav_size_kb = 0.0
        created_at = ""
        if os.path.exists(wav_path):
            try:
                stat = os.stat(wav_path)
                wav_size_kb = stat.st_size / 1024
                from datetime import datetime

                created_at = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError as e:
                logger.debug(f"[PersonaService] 获取文件状态失败 {wav_path}: {e}")

        info = PersonaInfo(
            name=name,
            description=description,
            wav_path=wav_path,
            exists=True,
            wav_size_kb=wav_size_kb,
            created_at=created_at,
        )

        # 更新缓存
        with self._cache_lock:
            self._cache[name] = info
            self._cache_timestamp = time.time()

        return info

    def create_persona(
        self,
        name: str,
        audio_path: str,
        description: str = "",
        overwrite: bool = False,
    ) -> PersonaInfo:
        """创建新音色。

        Args:
            name: 音色名称。
            audio_path: 参考音频文件路径。
            description: 音色描述。
            overwrite: 是否覆盖已有音色。

        Returns:
            创建的 PersonaInfo。

        Raises:
            PersonaError: 创建失败。
        """
        from .exceptions import PersonaError
        from .persona_manager import fn_save_persona

        message, needs_confirm = fn_save_persona(
            name=name,
            audio_input=audio_path,
            ref_text=description,
            overwrite=overwrite,
        )

        if needs_confirm and not overwrite:
            raise PersonaError(f"音色 [{name}] 已存在，需设置 overwrite=True 覆盖")

        if "失败" in message or "❌" in message:
            raise PersonaError(message)

        # 使缓存失效
        self._invalidate_cache()

        # 返回新创建的音色信息
        result = self.get_persona(name)
        if result is None:
            raise PersonaError(f"音色 [{name}] 创建后无法加载")

        logger.info(f"[PersonaService] 音色创建成功: {name}")
        return result

    def delete_persona(self, name: str) -> bool:
        """删除指定音色。

        Args:
            name: 音色名称。

        Returns:
            True 表示删除成功，False 表示失败或不存在。

        Raises:
            PersonaError: 删除过程出错。
        """
        from .persona_manager import delete_persona

        success, message = delete_persona(name)

        if success:
            # 使缓存失效
            self._invalidate_cache()
            logger.info(f"[PersonaService] 音色删除成功: {name}")
        else:
            logger.warning(f"[PersonaService] 音色删除失败: {name} - {message}")

        return success

    @staticmethod
    def _get_persona_dir() -> str:
        """获取音色存储目录路径（延迟导入避免循环依赖）。

        Returns:
            音色目录的绝对路径字符串。
        """
        from .config import PERSONA_DIR

        return PERSONA_DIR


# ======================================================================
# 模块级单例
# ======================================================================

_generation_service: TTSGenerationService | None = None
_model_service: ModelService | None = None
_persona_service: PersonaService | None = None
_service_lock = threading.Lock()


def get_generation_service() -> TTSGenerationService:
    """获取全局 TTSGenerationService 单例。"""
    global _generation_service
    if _generation_service is None:
        with _service_lock:
            if _generation_service is None:
                _generation_service = TTSGenerationService()
    return _generation_service


def get_model_service() -> ModelService:
    """获取全局 ModelService 单例。"""
    global _model_service
    if _model_service is None:
        with _service_lock:
            if _model_service is None:
                _model_service = ModelService()
    return _model_service


def get_persona_service() -> PersonaService:
    """获取全局 PersonaService 单例。"""
    global _persona_service
    if _persona_service is None:
        with _service_lock:
            if _persona_service is None:
                _persona_service = PersonaService()
    return _persona_service
