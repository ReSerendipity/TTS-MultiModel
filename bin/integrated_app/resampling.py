"""自动音频重采样管线：引擎切换时的采样率统一转换

本模块提供统一的音频重采样功能，用于在 TTS 引擎之间切换时
（如 VoxCPM2 @24kHz -> IndexTTS2 @16kHz）自动将输出音频
重采样到统一的采样率。

支持的常见 TTS 采样率：16000, 22050, 24000, 44100, 48000 Hz。

重采样后端优先级：
  1. scipy.signal.resample（频域方法，高质量）
  2. librosa.resample（时域方法，质量好）
  3. 线性插值回退（零依赖，质量一般）
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

import numpy as np

from .exceptions import AudioProcessingError

logger = logging.getLogger("tts_multimodel")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 常见 TTS 采样率集合
COMMON_SAMPLE_RATES: frozenset[int] = frozenset({16000, 22050, 24000, 44100, 48000})

# 各引擎默认输出采样率
ENGINE_SAMPLE_RATES: dict[str, int] = {
    "voxcpm2": 24000,
    "indextts2": 16000,
}

# 默认统一目标采样率
DEFAULT_TARGET_SR: int = 24000

# 极短音频阈值（样本数）：少于此值使用线性插值以避免频域方法产生振铃
SHORT_AUDIO_THRESHOLD: int = 64

# NaN/Inf 替换值
SAFE_FILL_VALUE: float = 0.0


# ---------------------------------------------------------------------------
# 重采样后端枚举
# ---------------------------------------------------------------------------


class ResampleBackend(Enum):
    """重采样后端选择"""

    SCIPY = "scipy"
    LIBROSA = "librosa"
    LINEAR = "linear"
    AUTO = "auto"  # 自动选择最佳可用后端


# ---------------------------------------------------------------------------
# 配置 dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResamplingConfig:
    """重采样管线配置

    Attributes:
        target_sr: 目标采样率（Hz），默认 24000
        backend: 重采样后端，默认 AUTO（自动选择）
        normalize_output: 是否对输出做峰值归一化，防止重采样后超出 [-1, 1]
        clip_output: 是否对输出做硬裁剪到 [-1, 1]
        force_mono: 是否强制转为单声道
        short_audio_threshold: 极短音频样本阈值，低于此值使用线性插值
        nan_protection: 是否启用 NaN/Inf 保护
    """

    target_sr: int = DEFAULT_TARGET_SR
    backend: ResampleBackend = ResampleBackend.AUTO
    normalize_output: bool = False
    clip_output: bool = True
    force_mono: bool = True
    short_audio_threshold: int = SHORT_AUDIO_THRESHOLD
    nan_protection: bool = True


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------


def _sanitize_audio(audio: np.ndarray, nan_protection: bool = True) -> np.ndarray:
    """清洗音频数组：确保 float32、处理 NaN/Inf、极短音频保护

    Args:
        audio: 输入音频数组
        nan_protection: 是否替换 NaN/Inf

    Returns:
        清洗后的 float32 音频数组

    Raises:
        AudioProcessingError: 输入为空或非数组
    """
    if not isinstance(audio, np.ndarray):
        try:
            audio = np.asarray(audio, dtype=np.float32)
        except (ValueError, TypeError) as exc:
            raise AudioProcessingError(f"无法将输入转换为 numpy 数组: {exc}") from exc

    if audio.size == 0:
        raise AudioProcessingError("输入音频数组为空")

    audio = audio.astype(np.float32, copy=False)

    if nan_protection:
        nan_count = np.isnan(audio).sum()
        inf_count = np.isinf(audio).sum()
        if nan_count > 0 or inf_count > 0:
            logger.warning(
                "检测到 %d 个 NaN 和 %d 个 Inf 值，已替换为 %s",
                int(nan_count),
                int(inf_count),
                SAFE_FILL_VALUE,
            )
            audio = np.where(np.isfinite(audio), audio, SAFE_FILL_VALUE).astype(np.float32)

    return audio


def _to_mono(audio: np.ndarray) -> np.ndarray:
    """将多声道音频转为单声道（取均值）

    Args:
        audio: 输入音频数组，形状 (samples,) 或 (samples, channels)

    Returns:
        单声道音频数组，形状 (samples,)
    """
    if audio.ndim == 1:
        return audio
    if audio.ndim == 2:
        return np.mean(audio, axis=-1).astype(np.float32)
    # 高维展平为单声道
    return np.mean(audio, axis=tuple(range(1, audio.ndim))).astype(np.float32)


def _clip_audio(audio: np.ndarray) -> np.ndarray:
    """硬裁剪音频到 [-1, 1] 范围

    Args:
        audio: 输入音频数组

    Returns:
        裁剪后的音频数组
    """
    return np.clip(audio, -1.0, 1.0).astype(np.float32)


def _normalize_peak(audio: np.ndarray) -> np.ndarray:
    """峰值归一化到 0.95（留一点余量）

    Args:
        audio: 输入音频数组

    Returns:
        归一化后的音频数组
    """
    peak = np.max(np.abs(audio))
    if peak > 1e-10:
        audio = audio * (0.95 / peak)
    return audio.astype(np.float32)


def _detect_backend() -> ResampleBackend:
    """自动检测最佳可用重采样后端

    Returns:
        可用的后端枚举值
    """
    try:
        import scipy.signal  # noqa: F401

        return ResampleBackend.SCIPY
    except ImportError:
        pass

    try:
        import librosa  # noqa: F401

        return ResampleBackend.LIBROSA
    except ImportError:
        pass

    logger.info("scipy 和 librosa 均不可用，回退到线性插值重采样")
    return ResampleBackend.LINEAR


# ---------------------------------------------------------------------------
# 核心重采样实现
# ---------------------------------------------------------------------------


def _resample_scipy(audio: np.ndarray, source_sr: int, target_sr: int) -> np.ndarray:
    """使用 scipy.signal.resample 进行频域重采样

    Args:
        audio: 单声道 float32 音频数组
        source_sr: 源采样率
        target_sr: 目标采样率

    Returns:
        重采样后的音频数组
    """
    from scipy.signal import resample

    num_samples = int(len(audio) * target_sr / source_sr)
    if num_samples < 1:
        num_samples = 1
    result = resample(audio, num_samples)
    return result.astype(np.float32)


def _resample_librosa(audio: np.ndarray, source_sr: int, target_sr: int) -> np.ndarray:
    """使用 librosa.resample 进行时域重采样

    Args:
        audio: 单声道 float32 音频数组
        source_sr: 源采样率
        target_sr: 目标采样率

    Returns:
        重采样后的音频数组
    """
    import librosa

    result = librosa.resample(y=audio, orig_sr=source_sr, target_sr=target_sr)
    return result.astype(np.float32)


def _resample_linear(audio: np.ndarray, source_sr: int, target_sr: int) -> np.ndarray:
    """使用线性插值进行重采样（零依赖回退方案）

    Args:
        audio: 单声道 float32 音频数组
        source_sr: 源采样率
        target_sr: 目标采样率

    Returns:
        重采样后的音频数组
    """
    source_len = len(audio)
    if source_len < 2:
        # 极短音频：直接返回单个样本或零填充
        return audio.copy()

    target_len = int(source_len * target_sr / source_sr)
    if target_len < 1:
        target_len = 1

    # 构建插值索引（源信号中的浮点位置）
    source_indices = np.linspace(0, source_len - 1, target_len)

    # 线性插值
    floor_indices = np.floor(source_indices).astype(np.intp)
    ceil_indices = np.minimum(floor_indices + 1, source_len - 1)
    frac = (source_indices - floor_indices).astype(np.float32)

    result = audio[floor_indices] * (1.0 - frac) + audio[ceil_indices] * frac
    return result.astype(np.float32)


# 后端调度表
_BACKEND_FUNCS: dict[ResampleBackend, callable] = {
    ResampleBackend.SCIPY: _resample_scipy,
    ResampleBackend.LIBROSA: _resample_librosa,
    ResampleBackend.LINEAR: _resample_linear,
}


def _do_resample(
    audio: np.ndarray,
    source_sr: int,
    target_sr: int,
    backend: ResampleBackend,
) -> np.ndarray:
    """执行单次重采样

    Args:
        audio: 单声道 float32 音频数组
        source_sr: 源采样率
        target_sr: 目标采样率
        backend: 重采样后端

    Returns:
        重采样后的音频数组
    """
    func = _BACKEND_FUNCS.get(backend)
    if func is None:
        raise AudioProcessingError(f"不支持的重采样后端: {backend}")
    return func(audio, source_sr, target_sr)


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def normalize_sample_rate(
    audio: np.ndarray,
    source_sr: int,
    target_sr: int,
    backend: ResampleBackend = ResampleBackend.AUTO,
    normalize_output: bool = False,
    clip_output: bool = True,
    force_mono: bool = True,
    nan_protection: bool = True,
) -> np.ndarray:
    """统一的音频重采样函数：将音频从源采样率转换到目标采样率

    这是模块的主要入口函数，支持自动后端选择、声道转换、
    NaN/Inf 保护和后处理。

    Args:
        audio: 输入音频数组（任意 dtype，1D 或 2D）
        source_sr: 源采样率（Hz）
        target_sr: 目标采样率（Hz）
        backend: 重采样后端，AUTO 自动选择最佳可用
        normalize_output: 是否峰值归一化输出
        clip_output: 是否硬裁剪输出到 [-1, 1]
        force_mono: 是否强制转为单声道
        nan_protection: 是否替换 NaN/Inf

    Returns:
        重采样后的 float32 单声道音频数组

    Raises:
        AudioProcessingError: 输入无效或重采样失败

    Examples:
        >>> # VoxCPM2 @24kHz -> IndexTTS2 @16kHz
        >>> resampled = normalize_sample_rate(audio_24k, 24000, 16000)
    """
    # 参数验证
    if source_sr <= 0:
        raise AudioProcessingError(f"源采样率必须为正整数，收到: {source_sr}")
    if target_sr <= 0:
        raise AudioProcessingError(f"目标采样率必须为正整数，收到: {target_sr}")

    # 采样率相同，直接返回清洗后的音频
    if source_sr == target_sr:
        audio = _sanitize_audio(audio, nan_protection=nan_protection)
        if force_mono:
            audio = _to_mono(audio)
        if clip_output:
            audio = _clip_audio(audio)
        if normalize_output:
            audio = _normalize_peak(audio)
        return audio

    # 清洗输入
    audio = _sanitize_audio(audio, nan_protection=nan_protection)

    # 多声道转单声道
    if force_mono:
        audio = _to_mono(audio)

    # 选择后端
    if backend == ResampleBackend.AUTO:
        # 极短音频强制使用线性插值（避免频域振铃）
        actual_backend = ResampleBackend.LINEAR if len(audio) < SHORT_AUDIO_THRESHOLD else _detect_backend()
    else:
        actual_backend = backend

    # 执行重采样
    try:
        result = _do_resample(audio, source_sr, target_sr, actual_backend)
    except Exception as exc:
        # 回退到线性插值
        if actual_backend != ResampleBackend.LINEAR:
            logger.warning(
                "使用 %s 后端重采样失败 (%s)，回退到线性插值",
                actual_backend.value,
                exc,
            )
            try:
                result = _resample_linear(audio, source_sr, target_sr)
            except Exception as fallback_exc:
                raise AudioProcessingError(f"重采样失败（主后端和回退后端均失败）: {fallback_exc}") from fallback_exc
        else:
            raise AudioProcessingError(f"重采样失败: {exc}") from exc

    # NaN/Inf 后处理保护（重采样可能引入异常值）
    if nan_protection and not np.all(np.isfinite(result)):
        result = np.where(np.isfinite(result), result, SAFE_FILL_VALUE).astype(np.float32)

    # 后处理
    if normalize_output:
        result = _normalize_peak(result)
    if clip_output:
        result = _clip_audio(result)

    logger.debug(
        "重采样完成: %dHz -> %dHz, %d -> %d 样本, 后端=%s",
        source_sr,
        target_sr,
        len(audio),
        len(result),
        actual_backend.value,
    )

    return result


def detect_sample_rate(audio_length: int, duration_seconds: float) -> int:
    """根据音频长度和时长反推最可能的采样率

    在源采样率未知时，利用音频样本数和时长估计采样率，
    并就近匹配到 COMMON_SAMPLE_RATES 中的标准值。

    Args:
        audio_length: 音频样本总数
        duration_seconds: 音频时长（秒）

    Returns:
        估计的采样率（Hz），匹配到最近的标准采样率

    Raises:
        AudioProcessingError: 时长为零或负数
    """
    if duration_seconds <= 0:
        raise AudioProcessingError(f"音频时长必须为正数，收到: {duration_seconds}")

    estimated_sr = audio_length / duration_seconds

    # 匹配到最近的标准采样率
    best_sr = min(COMMON_SAMPLE_RATES, key=lambda sr: abs(sr - estimated_sr))
    deviation = abs(best_sr - estimated_sr) / estimated_sr

    if deviation > 0.05:
        logger.warning(
            "估计采样率 %.0f Hz 与最近标准采样率 %d Hz 偏差 %.1f%%，请确认音频时长是否正确",
            estimated_sr,
            best_sr,
            deviation * 100,
        )

    return best_sr


def batch_resample(
    audio_segments: Sequence[np.ndarray],
    source_sr: int,
    target_sr: int,
    config: ResamplingConfig | None = None,
) -> list[np.ndarray]:
    """批量重采样：对多个音频段统一执行重采样

    适用于流式生成中将多个分段音频统一转换到目标采样率的场景。

    Args:
        audio_segments: 音频数组列表
        source_sr: 源采样率（Hz）
        target_sr: 目标采样率（Hz）
        config: 重采样配置，None 使用默认配置

    Returns:
        重采样后的音频数组列表（与输入等长）

    Raises:
        AudioProcessingError: 批量重采样失败
    """
    if not audio_segments:
        return []

    cfg = config or ResamplingConfig()

    results: list[np.ndarray] = []
    for i, seg in enumerate(audio_segments):
        try:
            resampled = normalize_sample_rate(
                audio=seg,
                source_sr=source_sr,
                target_sr=target_sr,
                backend=cfg.backend,
                normalize_output=cfg.normalize_output,
                clip_output=cfg.clip_output,
                force_mono=cfg.force_mono,
                nan_protection=cfg.nan_protection,
            )
            results.append(resampled)
        except AudioProcessingError as exc:
            logger.error("批量重采样第 %d 段失败: %s", i, exc)
            raise

    logger.debug("批量重采样完成: %d 段, %dHz -> %dHz", len(results), source_sr, target_sr)
    return results


# ---------------------------------------------------------------------------
# ResamplingPipeline 类
# ---------------------------------------------------------------------------


class ResamplingPipeline:
    """可配置的音频重采样管线

    封装重采样配置和状态，提供简洁的调用接口。在引擎切换场景中，
    可以为每个引擎创建对应的管线实例，统一管理目标采样率。

    Attributes:
        config: 重采样配置

    Examples:
        >>> pipeline = ResamplingPipeline(ResamplingConfig(target_sr=24000))
        >>> # VoxCPM2 输出 @24kHz，无需重采样
        >>> audio = pipeline.process(audio_24k, source_sr=24000)
        >>> # IndexTTS2 输出 @16kHz，自动重采样到 24kHz
        >>> audio = pipeline.process(audio_16k, source_sr=16000)
    """

    def __init__(self, config: ResamplingConfig | None = None) -> None:
        """初始化重采样管线

        Args:
            config: 重采样配置，None 使用默认配置（target_sr=24000）
        """
        self.config = config or ResamplingConfig()
        # 缓存已检测的后端，避免重复 import 探测
        self._detected_backend: ResampleBackend | None = None

    @property
    def target_sr(self) -> int:
        """目标采样率"""
        return self.config.target_sr

    def process(
        self,
        audio: np.ndarray,
        source_sr: int,
        target_sr: int | None = None,
    ) -> np.ndarray:
        """处理单段音频：按需重采样到目标采样率

        如果 source_sr == target_sr，仅做清洗和后处理（不执行重采样）。

        Args:
            audio: 输入音频数组
            source_sr: 源采样率（Hz）
            target_sr: 目标采样率，None 使用配置中的 target_sr

        Returns:
            处理后的音频数组
        """
        effective_target_sr = target_sr if target_sr is not None else self.config.target_sr

        return normalize_sample_rate(
            audio=audio,
            source_sr=source_sr,
            target_sr=effective_target_sr,
            backend=self.config.backend,
            normalize_output=self.config.normalize_output,
            clip_output=self.config.clip_output,
            force_mono=self.config.force_mono,
            nan_protection=self.config.nan_protection,
        )

    def process_batch(
        self,
        audio_segments: Sequence[np.ndarray],
        source_sr: int,
        target_sr: int | None = None,
    ) -> list[np.ndarray]:
        """批量处理音频段

        Args:
            audio_segments: 音频数组列表
            source_sr: 源采样率（Hz）
            target_sr: 目标采样率，None 使用配置中的 target_sr

        Returns:
            处理后的音频数组列表
        """
        effective_target_sr = target_sr if target_sr is not None else self.config.target_sr

        return batch_resample(
            audio_segments=audio_segments,
            source_sr=source_sr,
            target_sr=effective_target_sr,
            config=self.config,
        )

    def resample_for_engine(
        self,
        audio: np.ndarray,
        from_engine: str,
        to_engine: str | None = None,
    ) -> tuple[np.ndarray, int]:
        """为引擎切换场景重采样音频

        根据源引擎和目标引擎的默认采样率自动确定 source_sr 和 target_sr，
        执行重采样，并返回 (重采样后音频, 目标采样率)。

        Args:
            audio: 输入音频数组
            from_engine: 源引擎名称（如 "voxcpm2", "indextts2"）
            to_engine: 目标引擎名称，None 使用管线配置的 target_sr 推导

        Returns:
            (重采样后音频, 目标采样率)

        Raises:
            AudioProcessingError: 引擎名称未知
        """
        source_sr = ENGINE_SAMPLE_RATES.get(from_engine)
        if source_sr is None:
            raise AudioProcessingError(f"未知源引擎: {from_engine}，已知引擎: {list(ENGINE_SAMPLE_RATES.keys())}")

        if to_engine is not None:
            target_sr = ENGINE_SAMPLE_RATES.get(to_engine)
            if target_sr is None:
                raise AudioProcessingError(f"未知目标引擎: {to_engine}，已知引擎: {list(ENGINE_SAMPLE_RATES.keys())}")
        else:
            target_sr = self.config.target_sr

        result = self.process(audio, source_sr, target_sr)
        return result, target_sr

    def get_engine_sample_rate(self, engine_name: str) -> int:
        """获取引擎的默认输出采样率

        Args:
            engine_name: 引擎名称

        Returns:
            采样率（Hz）

        Raises:
            AudioProcessingError: 引擎名称未知
        """
        sr = ENGINE_SAMPLE_RATES.get(engine_name)
        if sr is None:
            raise AudioProcessingError(f"未知引擎: {engine_name}，已知引擎: {list(ENGINE_SAMPLE_RATES.keys())}")
        return sr

    def __repr__(self) -> str:
        """返回管线的字符串表示（用于调试和日志）。

        Returns:
            包含目标采样率和后端信息的字符串。
        """
        return f"ResamplingPipeline(target_sr={self.config.target_sr}, backend={self.config.backend.value})"


# ---------------------------------------------------------------------------
# 全局默认管线实例（惰性使用）
# ---------------------------------------------------------------------------

_default_pipeline: ResamplingPipeline | None = None


def get_default_pipeline() -> ResamplingPipeline:
    """获取全局默认重采样管线实例（单例）

    配置从 AppConfig 读取 target_sr，若配置不可用则使用默认值 24000。

    Returns:
        ResamplingPipeline 实例
    """
    global _default_pipeline

    if _default_pipeline is not None:
        return _default_pipeline

    # 尝试从应用配置读取目标采样率
    target_sr = DEFAULT_TARGET_SR
    try:
        from .config import get_config

        cfg = get_config()
        target_sr = cfg.generation.default_sample_rate
    except Exception:
        pass

    _default_pipeline = ResamplingPipeline(ResamplingConfig(target_sr=target_sr))
    return _default_pipeline


def reset_default_pipeline() -> None:
    """重置全局默认管线实例（用于配置变更后刷新）"""
    global _default_pipeline
    _default_pipeline = None
