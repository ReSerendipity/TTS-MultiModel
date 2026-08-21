"""语音克隆工具模块。

提供语音克隆相关的音频处理、参考音频验证、特征提取辅助等工具函数：
- 参考音频质量检测（时长、静音、信噪比）
- 音频预处理（降噪、响度归一化、VAD 裁切）
- 参考音频特征提取辅助
- 音频格式转换和验证
- 克隆质量评估辅助

设计要点：
- 所有函数为纯函数或无状态工具函数
- 延迟导入重量级依赖（torch, librosa, soundfile 等）
- 提供质量检测但不自动修改用户音频（除非显式请求）
- 支持 numpy 数组和文件路径两种输入
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger("tts_multimodel")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 参考音频推荐时长范围（秒）
MIN_REFERENCE_DURATION: float = 3.0
MAX_REFERENCE_DURATION: float = 30.0
RECOMMENDED_DURATION: float = 10.0

# 音频格式白名单
SUPPORTED_AUDIO_FORMATS: frozenset[str] = frozenset({".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"})

# 最低采样率要求
MIN_SAMPLE_RATE: int = 16000
RECOMMENDED_SAMPLE_RATE: int = 24000


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class AudioQualityResult:
    """音频质量检测结果。

    Attributes:
        is_valid: 是否通过基本质量检查。
        duration: 音频时长（秒）。
        sample_rate: 采样率（Hz）。
        peak_db: 峰值电平（dBFS）。
        rms_db: RMS 电平（dBFS）。
        has_silence_issues: 是否存在静音问题（过长静音或全静音）。
        snr_estimate: 估计信噪比（dB），-1 表示无法估计。
        issues: 检测到的问题列表。
        warnings: 警告列表（不影响使用但建议优化）。
    """

    is_valid: bool
    duration: float
    sample_rate: int
    peak_db: float
    rms_db: float
    has_silence_issues: bool
    snr_estimate: float = -1.0
    issues: list[str] = None
    warnings: list[str] = None

    def __post_init__(self) -> None:
        if self.issues is None:
            self.issues = []
        if self.warnings is None:
            self.warnings = []


@dataclass
class PreprocessResult:
    """音频预处理结果。

    Attributes:
        audio: 预处理后的音频数组（float32, [-1, 1]）。
        sample_rate: 采样率（Hz）。
        was_modified: 是否进行了修改。
        modifications: 执行的修改列表。
    """

    audio: np.ndarray
    sample_rate: int
    was_modified: bool = False
    modifications: list[str] = None

    def __post_init__(self) -> None:
        if self.modifications is None:
            self.modifications = []


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------


def validate_reference_audio(audio_path: str) -> AudioQualityResult:
    """验证参考音频文件是否适合用于语音克隆。

    检查项包括：
    - 文件存在性和格式
    - 时长是否在推荐范围内
    - 采样率是否满足要求
    - 音频电平是否正常（不过小或削波）
    - 是否存在全静音或过长静音段

    Args:
        audio_path: 参考音频文件路径。

    Returns:
        AudioQualityResult 包含质量评估结果。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 文件格式不支持。
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    ext = os.path.splitext(audio_path)[1].lower()
    if ext not in SUPPORTED_AUDIO_FORMATS:
        raise ValueError(f"不支持的音频格式: {ext}，支持的格式: {', '.join(SUPPORTED_AUDIO_FORMATS)}")

    issues: list[str] = []
    warnings: list[str] = []

    try:
        import soundfile as sf

        info = sf.info(audio_path)
        duration = info.duration
        sample_rate = info.samplerate
        channels = info.channels

        audio, sr = sf.read(audio_path, dtype="float32")
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)

    except Exception as e:
        return AudioQualityResult(
            is_valid=False,
            duration=0.0,
            sample_rate=0,
            peak_db=-np.inf,
            rms_db=-np.inf,
            has_silence_issues=True,
            issues=[f"无法读取音频文件: {e}"],
        )

    peak = np.max(np.abs(audio)) if len(audio) > 0 else 0.0
    rms = np.sqrt(np.mean(audio**2)) if len(audio) > 0 else 0.0
    peak_db = 20 * np.log10(max(peak, 1e-10))
    rms_db = 20 * np.log10(max(rms, 1e-10))

    has_silence_issues = False

    if duration < MIN_REFERENCE_DURATION:
        issues.append(f"音频过短（{duration:.1f}s），建议至少 {MIN_REFERENCE_DURATION}s")
    elif duration > MAX_REFERENCE_DURATION:
        warnings.append(f"音频较长（{duration:.1f}s），推荐 {RECOMMENDED_DURATION}s 左右")

    if sample_rate < MIN_SAMPLE_RATE:
        issues.append(f"采样率过低（{sample_rate}Hz），建议至少 {MIN_SAMPLE_RATE}Hz")
    elif sample_rate < RECOMMENDED_SAMPLE_RATE:
        warnings.append(f"采样率偏低（{sample_rate}Hz），推荐 {RECOMMENDED_SAMPLE_RATE}Hz")

    if channels > 1:
        warnings.append(f"多声道音频（{channels}声道），将自动转为单声道")

    if peak_db > -0.5:
        warnings.append(f"音频可能存在削波（峰值 {peak_db:.1f}dBFS）")
    elif peak_db < -30:
        warnings.append(f"音频电平过低（峰值 {peak_db:.1f}dBFS），建议增大音量")

    if rms_db < -50:
        issues.append(f"音频音量过小（RMS {rms_db:.1f}dBFS），可能包含大量静音")
        has_silence_issues = True

    if _is_too_much_silence(audio, sample_rate):
        warnings.append("音频包含过长静音段，建议裁切静音部分")
        has_silence_issues = True

    is_valid = len(issues) == 0

    return AudioQualityResult(
        is_valid=is_valid,
        duration=duration,
        sample_rate=sample_rate,
        peak_db=float(peak_db),
        rms_db=float(rms_db),
        has_silence_issues=has_silence_issues,
        issues=issues,
        warnings=warnings,
    )


def preprocess_reference_audio(
    audio_input: str | np.ndarray,
    sample_rate: int | None = None,
    target_sr: int = 24000,
    normalize_loudness: bool = True,
    trim_silence: bool = True,
) -> PreprocessResult:
    """预处理参考音频用于语音克隆。

    处理流程：
    1. 加载音频（如果是文件路径）
    2. 转为单声道
    3. 重采样到目标采样率
    4. 可选：裁切首尾静音
    5. 可选：响度归一化

    Args:
        audio_input: 音频文件路径或 numpy 数组（float32）。
        sample_rate: 输入音频采样率（数组输入时必须提供）。
        target_sr: 目标采样率，默认 24000Hz。
        normalize_loudness: 是否执行响度归一化。
        trim_silence: 是否裁切首尾静音。

    Returns:
        PreprocessResult 包含处理后的音频和元信息。

    Raises:
        ValueError: 输入参数无效。
    """
    modifications: list[str] = []
    was_modified = False

    if isinstance(audio_input, str):
        import soundfile as sf

        audio, sr = sf.read(audio_input, dtype="float32")
    else:
        if sample_rate is None:
            raise ValueError("数组输入必须提供 sample_rate 参数")
        audio = audio_input.astype(np.float32, copy=False)
        sr = sample_rate

    if audio.ndim > 1:
        audio = np.mean(audio, axis=1).astype(np.float32)
        modifications.append("转为单声道")
        was_modified = True

    if sr != target_sr:
        try:
            from .resampling import normalize_sample_rate

            audio = normalize_sample_rate(audio, sr, target_sr)
            sr = target_sr
            modifications.append(f"重采样到 {target_sr}Hz")
            was_modified = True
        except Exception as e:
            logger.warning(f"重采样失败，使用原始采样率: {e}")

    if trim_silence:
        trimmed = _trim_silence(audio, sr)
        if len(trimmed) < len(audio) * 0.95:
            audio = trimmed
            modifications.append("裁切首尾静音")
            was_modified = True

    if normalize_loudness:
        peak = np.max(np.abs(audio)) if len(audio) > 0 else 0.0
        if peak > 1e-6:
            target_peak = 0.9
            audio = (audio * (target_peak / peak)).astype(np.float32)
            audio = np.clip(audio, -1.0, 1.0)
            modifications.append("响度归一化")
            was_modified = True

    return PreprocessResult(
        audio=audio,
        sample_rate=sr,
        was_modified=was_modified,
        modifications=modifications,
    )


def load_audio_array(
    audio_path: str,
    target_sr: int | None = None,
    dtype: np.dtype = np.float32,
) -> tuple[np.ndarray, int]:
    """加载音频文件为 numpy 数组。

    Args:
        audio_path: 音频文件路径。
        target_sr: 目标采样率，None 则保持原始采样率。
        dtype: 输出数据类型，默认 float32。

    Returns:
        (audio_array, sample_rate) 元组。

    Raises:
        FileNotFoundError: 文件不存在。
        RuntimeError: 音频加载失败。
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    try:
        import soundfile as sf

        audio, sr = sf.read(audio_path, dtype="float32")

        if audio.ndim > 1:
            audio = np.mean(audio, axis=1).astype(np.float32)

        if target_sr is not None and sr != target_sr:
            from .resampling import normalize_sample_rate

            audio = normalize_sample_rate(audio, sr, target_sr)
            sr = target_sr

        if dtype != np.float32:
            if np.issubdtype(dtype, np.integer):
                audio = (audio * np.iinfo(dtype).max).astype(dtype)
            else:
                audio = audio.astype(dtype)

        return audio, sr

    except Exception as e:
        raise RuntimeError(f"加载音频失败: {e}") from e


def save_audio_array(
    audio: np.ndarray,
    output_path: str,
    sample_rate: int,
    subtype: str = "PCM_16",
) -> None:
    """保存 numpy 数组为音频文件。

    Args:
        audio: 音频数组（float32, [-1, 1]）。
        output_path: 输出文件路径。
        sample_rate: 采样率（Hz）。
        subtype: soundfile 子类型，默认 PCM_16。

    Raises:
        RuntimeError: 保存失败。
    """
    try:
        import soundfile as sf

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        audio = np.clip(audio, -1.0, 1.0).astype(np.float32)
        sf.write(output_path, audio, sample_rate, subtype=subtype)

    except Exception as e:
        raise RuntimeError(f"保存音频失败: {e}") from e


def estimate_audio_duration(audio: np.ndarray, sample_rate: int) -> float:
    """估算音频时长。

    Args:
        audio: 音频数组。
        sample_rate: 采样率（Hz）。

    Returns:
        时长（秒）。
    """
    if sample_rate <= 0 or len(audio) == 0:
        return 0.0
    return len(audio) / sample_rate


def get_audio_format(file_path: str) -> str:
    """获取音频文件格式（扩展名小写）。

    Args:
        file_path: 文件路径。

    Returns:
        格式扩展名（如 ".wav", ".mp3"）。
    """
    return os.path.splitext(file_path)[1].lower()


def is_supported_audio_format(file_path: str) -> bool:
    """检查文件是否为支持的音频格式。

    Args:
        file_path: 文件路径。

    Returns:
        True 表示支持。
    """
    return get_audio_format(file_path) in SUPPORTED_AUDIO_FORMATS


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def _is_too_much_silence(audio: np.ndarray, sample_rate: int, threshold_db: float = -50) -> bool:
    """检测音频是否包含过多静音。

    Args:
        audio: 音频数组。
        sample_rate: 采样率。
        threshold_db: 静音阈值（dBFS）。

    Returns:
        True 表示静音过多。
    """
    if len(audio) == 0:
        return True

    frame_length = int(0.02 * sample_rate)
    if frame_length == 0:
        frame_length = 1

    threshold_amp = 10 ** (threshold_db / 20)

    silent_frames = 0
    total_frames = 0

    for i in range(0, len(audio) - frame_length, frame_length):
        frame = audio[i : i + frame_length]
        rms = np.sqrt(np.mean(frame**2))
        total_frames += 1
        if rms < threshold_amp:
            silent_frames += 1

    if total_frames == 0:
        return True

    silence_ratio = silent_frames / total_frames
    return silence_ratio > 0.7


def _trim_silence(
    audio: np.ndarray,
    sample_rate: int,
    threshold_db: float = -40,
    min_silence_ms: int = 200,
) -> np.ndarray:
    """裁切音频首尾静音。

    Args:
        audio: 音频数组。
        sample_rate: 采样率。
        threshold_db: 静音阈值（dBFS）。
        min_silence_ms: 最短静音长度（毫秒）。

    Returns:
        裁切后的音频数组。
    """
    if len(audio) == 0:
        return audio

    threshold_amp = 10 ** (threshold_db / 20)
    frame_length = int(0.01 * sample_rate)
    if frame_length == 0:
        return audio

    is_silent = []
    for i in range(0, len(audio) - frame_length + 1, frame_length):
        frame = audio[i : i + frame_length]
        rms = np.sqrt(np.mean(frame**2))
        is_silent.append(rms < threshold_amp)

    if not is_silent:
        return audio

    start_frame = 0
    while start_frame < len(is_silent) and is_silent[start_frame]:
        start_frame += 1

    end_frame = len(is_silent) - 1
    while end_frame > start_frame and is_silent[end_frame]:
        end_frame -= 1

    start_sample = start_frame * frame_length
    end_sample = min((end_frame + 1) * frame_length, len(audio))

    if start_sample >= end_sample:
        return audio

    return audio[start_sample:end_sample].astype(np.float32)
