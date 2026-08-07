"""dots.tts 引擎适配器模块。

**功能定位**：
    本模块实现 dots.tts 语音合成引擎的进程内适配器。dots.tts 由 rednote-hilab
    团队开发，是一个 20 亿参数、完全连续、端到端自回归（AR）TTS 系统，
    主干结合语义编码器 + LLM（Qwen2.5 初始化）+ 基于 48kHz AudioVAE 的
    AR 流匹配声学头，特点是：
    - 48kHz 高保真零样本语音克隆
    - 强多语种能力与情感表现力
    - 连续隐变量建模（无离散 token）

**架构角色**：
    :class:`DotsTTSEngine` 是 :class:`..engine_interface.TTSEngine` Protocol
    的具体实现类，遵循"构造轻量、``load()`` 重量加载"的声明式引擎契约，
    由 :func:`model_manager._load_generic_engine` 统一调度加载。

**进程内集成方式**：
    dots.tts 发布为标准 pip 包（``pip install dots.tts``），本适配器在
    :meth:`load` 时懒导入 ``dots_tts.runtime.DotsTtsRuntime`` 并从本地
    ``pretrained_models/dots.tts/`` 目录加载权重快照。

**离线优先约束**：
    依赖缺失或权重缺失时，:meth:`load` 抛出带清晰指引的 :class:`EngineLoadError`，
    引导用户安装依赖并运行 ``python scripts/download_dotstts.py`` 下载权重，
    **绝不**在推理过程中自动联网下载。

**支持功能**：
    - ``generate_voice_clone``：48kHz 零样本语音克隆（核心能力）
    - ``generate_streaming``：低延迟分段流式合成（runtime.generate_stream）
    - ``generate_voice_design`` / ``generate_script``：不支持，抛 NotImplementedError
"""

import logging
import os
import time
from typing import Any

from ..config import DOTSTTS_MODEL_PATH, SAVE_DIR
from ..engine_interface import TTSEngine
from ..exceptions import EngineLoadError, EngineNotLoadedError, GenerationError

logger = logging.getLogger("tts_multimodel")


class DotsTTSEngine(TTSEngine):
    """dots.tts 引擎适配器（进程内推理）。

    遵循 TTSEngine 协议的"无参构造 + 显式 ``load()``"契约。

    Attributes:
        model_dir (str): dots.tts 权重快照目录（``pretrained_models/dots.tts``）。
        precision (str): 推理精度（CUDA 下 ``"bfloat16"``，CPU 下 ``"float32"``）。
        optimize (bool): 是否启用 torch.compile 加速（加载时预热，稳态更快）。
        _runtime (Any): 底层 ``dots_tts.runtime.DotsTtsRuntime`` 实例，
            未加载时为 ``None``。
    """

    def __init__(self, model_dir: str | None = None) -> None:
        """初始化引擎（仅解析路径与精度，不加载权重）。

        Args:
            model_dir: 权重目录；``None`` 时回退到 config 的 ``DOTSTTS_MODEL_PATH``。
        """
        from ..gpu_backend import GPUBackend, GPUBackendManager

        self.model_dir: str = model_dir or DOTSTTS_MODEL_PATH
        backend: GPUBackend = GPUBackendManager.detect_backend()
        # WHY bfloat16 仅在 CUDA：CPU/MPS 上 bf16 算子支持不完整且不会加速，
        # 统一回退 float32 保证可用性。
        self.precision: str = "bfloat16" if backend == GPUBackend.CUDA else "float32"
        # torch.compile 在 Windows / CPU 上默认关闭，避免冷启动过慢或不兼容。
        self.optimize: bool = backend == GPUBackend.CUDA and os.name != "nt"
        self._runtime: Any = None
        logger.info(
            f"[dots.tts] 初始化引擎: model_dir={self.model_dir}, precision={self.precision}, optimize={self.optimize}"
        )

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        """检查引擎是否已加载就绪。

        Returns:
            bool: ``_runtime`` 已初始化时返回 True。
        """
        return getattr(self, "_runtime", None) is not None

    def load(self) -> None:
        """加载 dots.tts 运行时（进程内）。

        流程：
            1. 校验权重目录存在。
            2. 懒导入 ``dots_tts.runtime.DotsTtsRuntime``。
            3. ``from_pretrained`` 加载本地权重快照。

        Raises:
            EngineLoadError: 权重缺失、依赖未安装或运行时初始化失败。
        """
        if self.is_ready():
            return

        if not os.path.isdir(self.model_dir):
            raise EngineLoadError(
                f"dots.tts 模型目录不存在: {self.model_dir}\n请运行: python scripts/download_dotstts.py 下载模型权重。",
                engine="dotstts",
            )

        try:
            from dots_tts.runtime import DotsTtsRuntime
        except ImportError as e:
            raise EngineLoadError(
                "dots.tts 依赖未安装，请运行: pip install dots.tts\n"
                "参考: https://github.com/studio-dots-ai/dots.tts\n"
                f"原始错误: {e}",
                engine="dotstts",
            ) from e

        logger.info("[dots.tts] 开始加载运行时...")
        start: float = time.time()
        try:
            self._runtime = DotsTtsRuntime.from_pretrained(
                self.model_dir,
                precision=self.precision,
                optimize=self.optimize,
            )
        except (RuntimeError, AttributeError, TypeError, ImportError) as e:
            # 依赖不兼容时的友好提示：版本冲突（transformers/numpy/pydantic）
            # 常见表现为 RuntimeError 或 AttributeError
            self._runtime = None
            raise EngineLoadError(
                f"dots.tts 运行时初始化失败（可能是依赖版本不兼容）: {type(e).__name__}: {e}\n"
                "建议：1) 检查 transformers/numpy/pydantic 版本是否满足 dots.tts 要求；\n"
                "      2) 使用独立 venv 安装 dots.tts 及其依赖以隔离版本冲突。",
                engine="dotstts",
            ) from e
        except Exception as e:
            self._runtime = None
            raise EngineLoadError(
                f"dots.tts 运行时初始化失败: {type(e).__name__}: {e}",
                engine="dotstts",
            ) from e
        logger.info(f"[dots.tts] 运行时加载完成，耗时 {time.time() - start:.1f}s")

    def unload(self) -> None:
        """卸载模型并释放显存（幂等）。"""
        logger.info("[dots.tts] 开始卸载模型...")
        try:
            if getattr(self, "_runtime", None) is not None:
                del self._runtime
            self._runtime = None
            import gc

            gc.collect()
            from ..gpu_backend import GPUBackend, GPUBackendManager

            if GPUBackendManager.detect_backend() != GPUBackend.CPU:
                GPUBackendManager.empty_cache()
        except Exception as e:
            logger.exception(f"[dots.tts] 卸载异常（已忽略）: {e}")
        logger.info("[dots.tts] 模型卸载完成")

    # ------------------------------------------------------------------
    # 推理
    # ------------------------------------------------------------------

    def generate_voice_clone(
        self,
        text: str,
        reference_audio_path: str | None = None,
        instruction: str = "",
        normalize: bool = True,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """48kHz 零样本语音克隆（TTSEngine 协议实现）。

        Args:
            text: 待合成文本。
            reference_audio_path: 说话人参考音频路径（必需，建议约 10 秒）。
            instruction: 参考音频对应转写文本（prompt_text）。留空则走
                x-vector 纯音色克隆。
            normalize: 是否启用 dots.tts 文本归一化（映射 normalize_text）。
            **kwargs: 额外参数：``num_steps``（默认 10）、``guidance_scale``
                （默认 1.2）、``seed``（默认 42）、``language`` 等。

        Returns:
            tuple[Any, str]: ``(输出音频路径, 结果消息)``。

        Raises:
            EngineNotLoadedError: 引擎未就绪。
            ValueError: 未提供参考音频。
            GenerationError: 推理失败。
        """
        if not self.is_ready():
            raise EngineNotLoadedError("dots.tts 引擎未加载", engine="dotstts")
        if not reference_audio_path:
            raise ValueError("dots.tts generate_voice_clone 需要 reference_audio_path")

        gen_kwargs: dict[str, Any] = {
            "text": text,
            "prompt_audio_path": reference_audio_path,
            "num_steps": kwargs.pop("num_steps", 10),
            "guidance_scale": kwargs.pop("guidance_scale", 1.2),
        }
        if instruction:
            gen_kwargs["prompt_text"] = instruction
        if normalize:
            gen_kwargs["normalize_text"] = True
        gen_kwargs.update(kwargs)

        try:
            result: dict[str, Any] = self._runtime.generate(**gen_kwargs)
        except Exception as e:
            raise GenerationError(f"dots.tts 推理失败: {type(e).__name__}: {e}") from e

        output_path: str = self._save_result(result)
        return output_path, f"dots.tts 克隆完成: {output_path}"

    def generate_streaming(
        self,
        text: str,
        reference_audio_path: str | None = None,
        **kwargs: Any,
    ):
        """低延迟分段流式合成（runtime.generate_stream）。

        Args:
            text: 待合成文本。
            reference_audio_path: 参考音频路径（必需）。
            **kwargs: 同 :meth:`generate_voice_clone`，另加 ``prompt_text``。

        Yields:
            tuple[Any, str]: 每段 ``(音频路径, 状态消息)``。

        Raises:
            EngineNotLoadedError: 引擎未就绪。
            ValueError: 未提供参考音频。
        """
        if not self.is_ready():
            raise EngineNotLoadedError("dots.tts 引擎未加载", engine="dotstts")
        if not reference_audio_path:
            raise ValueError("dots.tts generate_streaming 需要 reference_audio_path")

        gen_kwargs: dict[str, Any] = {
            "text": text,
            "prompt_audio_path": reference_audio_path,
            "num_steps": kwargs.pop("num_steps", 10),
            "guidance_scale": kwargs.pop("guidance_scale", 1.2),
        }
        prompt_text: str = kwargs.pop("prompt_text", "")
        if prompt_text:
            gen_kwargs["prompt_text"] = prompt_text
        gen_kwargs.update(kwargs)

        sr: int = int(getattr(self._runtime, "sample_rate", 48000))
        idx: int = 0
        try:
            for chunk in self._runtime.generate_stream(**gen_kwargs):
                idx += 1
                audio = chunk.detach().float().cpu().squeeze().numpy()
                path: str = self._save_wav(audio, sr, prefix=f"dotstts_stream_{idx}")
                yield path, f"已生成第 {idx} 段"
        except Exception as e:
            raise GenerationError(f"dots.tts 流式推理失败: {type(e).__name__}: {e}") from e

    def generate_voice_design(
        self, text: str, instruction: str = "", normalize: bool = True, **kwargs: Any
    ) -> tuple[Any, str]:
        """语音设计（dots.tts 不支持）。

        Raises:
            NotImplementedError: 始终抛出，提示切换到 VoxCPM2。
        """
        raise NotImplementedError("dots.tts 不支持语音设计，请切换到 VoxCPM2 引擎。")

    def generate_script(
        self,
        text: str,
        speaker_map: dict[str, Any] | None = None,
        persona_map: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """剧本工坊（dots.tts 不支持）。

        Raises:
            NotImplementedError: 始终抛出。
        """
        raise NotImplementedError("dots.tts 不支持剧本工坊，请切换到 VoxCPM2 引擎。")

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _save_result(self, result: dict[str, Any]) -> str:
        """将 runtime.generate 返回结果保存为 WAV 文件。

        Args:
            result: 包含 ``"audio"``（torch.Tensor）与 ``"sample_rate"`` 的字典。

        Returns:
            str: 保存的 WAV 文件路径。
        """
        audio_tensor: Any = result["audio"]
        sample_rate: int = int(result.get("sample_rate", 48000))
        audio = audio_tensor.float().cpu().squeeze().numpy()
        return self._save_wav(audio, sample_rate, prefix="dotstts_clone")

    @staticmethod
    def _save_wav(audio: Any, sample_rate: int, prefix: str = "dotstts") -> str:
        """将波形数据写入 SAVE_DIR 下的时间戳 WAV 文件。

        Args:
            audio: 波形数据（numpy ndarray，float）。
            sample_rate: 采样率 (Hz)。
            prefix: 输出文件名前缀。

        Returns:
            str: 保存的 WAV 文件绝对路径。
        """
        import soundfile as sf

        # P0 安全修复：写盘前强制嵌入水印，用于生成内容来源追溯。
        # source_id 为代码常量，不可通过配置篡改。
        try:
            import numpy as np

            from ..watermark import WATERMARK_SOURCE_ID, watermark_audio

            audio_wm, wm_meta = watermark_audio(
                np.asarray(audio, dtype=np.float32),
                sample_rate,
                enable=True,
                source_id=WATERMARK_SOURCE_ID,
            )
            if wm_meta.get("watermarked"):
                logger.debug("[dots.tts] 水印嵌入成功: snr=%.1fdB", wm_meta.get("snr_db", 0.0))
            audio = audio_wm
        except Exception as wm_exc:
            logger.warning("[dots.tts] 水印嵌入异常（已忽略）: %s", wm_exc)

        os.makedirs(SAVE_DIR, exist_ok=True)
        output_path: str = os.path.join(SAVE_DIR, f"{prefix}_{int(time.time() * 1000)}.wav")
        sf.write(output_path, audio, sample_rate)
        return output_path

    @property
    def version(self) -> str:
        """引擎版本标识。"""
        return "dots.tts (soar)"
