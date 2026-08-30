"""音频后处理模块。

覆盖 8 大能力域：
    1. 响度归一化：LUFS（pyloudnorm，感知响度标准）/ RMS（能量近似）双路径，
       支持 method="auto" 智能回退。
    2. 速度/节拍调整：基于线性插值的 tempo 调节，无需重采样。
    3. 音频效果链：基于 Spotify Pedalboard 的混响/延迟/压缩/EQ 等效果，
       支持预设（warm / bright / radio / cinematic）。
    4. VAD 静音裁切：能量阈值 + 可选 webrtcvad，头尾静音与爆音（pop）检测。
    5. TTS 输出专属裁切：trim_tts_output 处理爆音、内部长静音幻觉。
    6. 降噪：ZipEnhancer 模型优先，noisereduce 库为回退方案。
    7. 统一后处理流水线：enhance_audio 串联所有处理步骤。
    8. 参考音频预处理与验证：preprocess_reference_audio / validate_reference_audio。

可选依赖层次（均为可选，未安装时使用轻量回退，不会阻塞主流程）：
    pyloudnorm  >  pedalboard  >  noisereduce  >  webrtcvad  >  librosa
    (LUFS标准)    (效果器链)     (降噪回退)      (VAD加速)     (静音裁切)

调用入口：
    - VoxCPM2 子模块（design.py / clone.py / ultimate.py / script.py 等）：
      generate 时 normalize=True 自动调用 normalize_loudness()。
    - IndexTTS2 引擎（engines/indextts2_engine.py）：synthesize() 后处理阶段
      调用 enhance_audio() 统一流水线。
    - routes/generate/* 路由：HTTP 响应前通过 postprocess 参数触发
      trim_tts_output / denoise_audio / normalize_loudness。
    - persona_manager.py / generation.py：参考音频预处理阶段调用
      preprocess_reference_audio。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger("tts_multimodel")

# ---------------------------------------------------------------------------
# 模块顶层一次性 try/except ImportError 加载可选依赖：
#   - pyloudnorm 的 Meter() 初始化约数毫秒，pedalboard 首次加载也有编译开销；
#   - 推理时每段音频（长文本分段可达数十段）叠加会显著增加端到端延迟；
#   - 顶层一次性加载 + 全局 bool 标记，调用路径只需一次分支判断，开销可忽略。
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 可选依赖：pyloudnorm（LUFS 响度测量 / 归一化）
# ---------------------------------------------------------------------------
try:
    import pyloudnorm as _pyloudnorm

    _HAS_PYLOUDNORM: bool = True
except ImportError:
    _pyloudnorm: Any | None = None
    _HAS_PYLOUDNORM: bool = False

# ---------------------------------------------------------------------------
# 可选依赖：pedalboard（音频效果器链）
# ---------------------------------------------------------------------------
try:
    import pedalboard as _pedalboard

    _HAS_PEDALBOARD: bool = True
except ImportError:
    _pedalboard: Any | None = None
    _HAS_PEDALBOARD: bool = False

# ---------------------------------------------------------------------------
# 可选依赖：noisereduce（降噪回退方案）
# ---------------------------------------------------------------------------
try:
    import noisereduce as _noisereduce

    _HAS_NOISEREDUCE: bool = True
except ImportError:
    _noisereduce: Any | None = None
    _HAS_NOISEREDUCE: bool = False

# ---------------------------------------------------------------------------
# 可选依赖：webrtcvad（加速 VAD 静音检测）
# ---------------------------------------------------------------------------
try:
    import webrtcvad as _webrtcvad

    _HAS_WEBRTCVAD: bool = True
except ImportError:
    _webrtcvad: Any | None = None
    _HAS_WEBRTCVAD: bool = False

# webrtcvad 对象缓存：(sample_rate, aggressive_mode) -> Vad 对象
# 避免每次 trim_silence_vad 调用都重新构造（C 扩展初始化非零成本）
_vad_cache: dict[tuple[int, int], Any] = {}


# ======================================================================
# 1. 响度归一化（Loudness normalization）
# ======================================================================

# 常用 LUFS 响度目标值
LUFS_SPEECH: float = -16.0
"""语音/播客目标响度（-16 LUFS，消费级耳机聆听常用值）。"""

LUFS_CHATTERBOX: float = -27.0
"""Chatterbox 低响度标准（参考音频归一化，提升克隆一致性）。"""

LUFS_PODCAST: float = -16.0
"""播客目标响度（同 LUFS_SPEECH）。"""

LUFS_MUSIC: float = -14.0
"""音乐流媒体目标响度（Spotify/Apple Music 常用值）。"""


def _normalize_loudness_rms(
    audio: np.ndarray,
    sample_rate: int,
    target_lufs: float,
) -> np.ndarray:
    """RMS 能量近似响度归一化（pyloudnorm 不可用时的回退路径）。

    使用 RMS 能量作为感知响度的粗近似，覆盖 ~80% 普通用户场景。

    Args:
        audio: 输入音频数组 (float32, [-1, 1])。
        sample_rate: 采样率，单位 Hz。
        target_lufs: 目标响度，单位 LUFS（此处被近似为 dBFS）。

    Returns:
        归一化后的音频数组 (float32)。
    """
    if audio.size == 0:
        return audio

    try:
        rms = np.sqrt(np.mean(audio**2))
    except (FloatingPointError, ValueError):
        logger.debug("RMS 计算出现浮点异常，返回原数组")
        return audio

    if not np.isfinite(rms) or rms < 1e-10:
        return audio

    try:
        current_loudness = 20 * np.log10(rms)
        gain_db = target_lufs - current_loudness
        gain_linear = 10 ** (gain_db / 20.0)
    except (FloatingPointError, ValueError):
        logger.debug("响度增益计算出现浮点异常，返回原数组")
        return audio

    normalized = audio * gain_linear

    try:
        max_val = np.max(np.abs(normalized))
        if np.isfinite(max_val) and max_val > 0.99:
            normalized = normalized / max_val * 0.95
    except (FloatingPointError, ValueError):
        logger.debug("峰值裁切保护计算异常，跳过裁切")

    return normalized.astype(np.float32)


def _normalize_loudness_lufs(
    audio: np.ndarray,
    sample_rate: int,
    target_lufs: float,
) -> np.ndarray:
    """使用 pyloudnorm 进行准确的 ITU-R BS.1770 LUFS 归一化。

    Args:
        audio: 输入音频数组（float32，范围 [-1, 1]）。
        sample_rate: 采样率，单位 Hz。
        target_lufs: 目标响度，单位 LUFS。

    Returns:
        np.ndarray: 归一化后的音频数组（float32）。测量或归一化失败时自动回退到 RMS 近似。
    """
    if audio.size == 0:
        return audio

    meter = _pyloudnorm.Meter(sample_rate)
    try:
        current_loudness = meter.integrated_loudness(audio)
    except (ValueError, np.linalg.LinAlgError) as exc:
        logger.warning(
            "pyloudnorm loudness measurement failed: %s (audio.shape=%s, sample_rate=%d). Falling back to RMS.",
            exc,
            audio.shape,
            sample_rate,
        )
        return _normalize_loudness_rms(audio, sample_rate, target_lufs)
    except Exception as exc:
        logger.warning("pyloudnorm loudness measurement failed (%s), falling back to RMS", exc)
        return _normalize_loudness_rms(audio, sample_rate, target_lufs)

    if not np.isfinite(current_loudness) or current_loudness < -70.0:
        return audio

    try:
        normalized = _pyloudnorm.normalize.loudness(audio, current_loudness, target_lufs)
    except (ValueError, np.linalg.LinAlgError) as exc:
        logger.warning(
            "pyloudnorm normalization failed: %s "
            "(audio.shape=%s, sample_rate=%d, current_loudness=%.2f, target=%.2f). "
            "Falling back to RMS.",
            exc,
            audio.shape,
            sample_rate,
            current_loudness,
            target_lufs,
        )
        return _normalize_loudness_rms(audio, sample_rate, target_lufs)
    except Exception as exc:
        logger.warning("pyloudnorm normalization failed (%s), falling back to RMS", exc)
        return _normalize_loudness_rms(audio, sample_rate, target_lufs)

    try:
        max_val = np.max(np.abs(normalized))
        if np.isfinite(max_val) and max_val > 0.99:
            normalized = normalized / max_val * 0.95
    except (FloatingPointError, ValueError):
        logger.debug("峰值裁切保护计算异常，跳过裁切")

    return normalized.astype(np.float32)


def normalize_loudness(
    audio: np.ndarray,
    sample_rate: int = 24000,
    target_lufs: float = -16.0,
    method: str = "auto",
) -> np.ndarray:
    """将音频归一化到目标响度。

    行业标准参考（LUFS speech 默认 -16 的来源）：
        - ATSC A/85：北美广播电视语音响度标准 -24 LKFS（等效 LUFS）。
        - EBU R128：欧洲广播电视/流媒体语音标准 -23 LUFS。
        - 播客 / YouTube 语音内容常用 -16 ~ -14 LUFS（更响，适合耳机聆听）。
        - 本项目默认 -16 LUFS，兼顾平台响度竞争与不削波的安全余量。

    Args:
        audio: 输入音频数组 (float32, [-1, 1])。
        sample_rate: 采样率，单位 Hz。默认 24000。
        target_lufs: 目标响度，单位 LUFS。常用参考值：
            - ``-16.0``：语音 / 播客（默认，ATSC/EBU 消费级落地实践）；
            - ``-27.0``：Chatterbox 低响度标准；
            - ``-14.0``：音乐流媒体（Spotify/Apple Music）。
        method: 归一化方法。行为说明：
            - ``"lufs"``：强制使用 pyloudnorm（ITU-R BS.1770 感知响度）；
              若未安装 pyloudnorm 则回退 RMS 并告警。
            - ``"rms"``：强制使用 RMS 能量近似，无需额外依赖。
            - ``"auto"``（默认）：优先 LUFS，pyloudnorm 不可用时静默回退 RMS。
              Why: LUFS 是 ITU-R BS.1770 标准感知响度，与人耳主观听感一致；
              但 pyloudnorm 需额外编译依赖（portaudio），多数普通用户未安装，
              RMS 近似可覆盖 ~80% 场景，平衡质量与可运行性。

    Returns:
        归一化后的音频数组 (float32)。
    """
    if method == "lufs":
        if not _HAS_PYLOUDNORM:
            logger.warning(
                "pyloudnorm not installed; method='lufs' unavailable. "
                "Falling back to RMS normalization. "
                "Install pyloudnorm for accurate LUFS: pip install pyloudnorm"
            )
            return _normalize_loudness_rms(audio, sample_rate, target_lufs)
        return _normalize_loudness_lufs(audio, sample_rate, target_lufs)

    if method == "rms":
        return _normalize_loudness_rms(audio, sample_rate, target_lufs)

    # method == "auto"
    if _HAS_PYLOUDNORM:
        return _normalize_loudness_lufs(audio, sample_rate, target_lufs)
    return _normalize_loudness_rms(audio, sample_rate, target_lufs)


# ======================================================================
# 2. 速度调整（Tempo adjustment）
# ======================================================================


def adjust_tempo(audio: np.ndarray, sample_rate: int, factor: float) -> tuple[np.ndarray, int]:
    """在不修改采样率的前提下调整音频播放速度（线性插值，轻微改变音高）。

    若需保音高的变速，需引入 phase vocoder 或 WSOLA（如 librosa.effects.time_stretch），
    此处保持零依赖轻量实现。

    Args:
        audio: 输入音频数组。
        sample_rate: 原始采样率（原样返回）。
        factor: 速度系数。``> 1`` 更快，``< 1`` 更慢，``1.0`` 不变。

    Returns:
        ``(adjusted_audio, new_sample_rate)`` —— 后者恒等于输入 ``sample_rate``。
    """
    if factor <= 0 or factor == 1.0:
        return audio, sample_rate

    new_length = int(len(audio) / factor)
    try:
        indices = np.linspace(0, len(audio) - 1, new_length).astype(int)
        adjusted = audio[indices]
    except (FloatingPointError, ValueError, IndexError) as exc:
        logger.debug("adjust_tempo 插值异常，返回原数组: %s", exc)
        return audio, sample_rate

    return adjusted.astype(np.float32), sample_rate


def change_tempo(
    audio: np.ndarray,
    sample_rate: int,
    factor: float,
) -> np.ndarray:
    """调整音频播放速度（单返回值接口，便于流水线串联）。

    对 :func:`adjust_tempo` 的薄封装，仅返回处理后的音频，丢弃采样率。
    签名保持与 normalize_loudness 等函数一致，便于 ``functools.reduce`` 串联。

    Args:
        audio: 输入音频数组。
        sample_rate: 采样率，单位 Hz。
        factor: 速度系数。``> 1`` 更快，``< 1`` 更慢。

    Returns:
        变速后的音频数组 (float32)。
    """
    adjusted, _ = adjust_tempo(audio, sample_rate, factor)
    return adjusted


# ======================================================================
# 3. 语音增强（内置，无外部依赖）
# ======================================================================


def apply_voice_enhancement(audio: np.ndarray, sample_rate: int = 24000) -> np.ndarray:
    """语音专属增强：80Hz 高通 + 软压缩（无外部依赖）。

    Args:
        audio: 输入音频数组。
        sample_rate: 采样率，单位 Hz。默认 24000。

    Returns:
        增强后的音频数组 (float32)。
    """
    if audio.size == 0:
        return audio

    try:
        from scipy.signal import butter, lfilter
    except ImportError:
        logger.debug("apply_voice_enhancement: scipy 不可用，跳过增强处理")
        return audio

    nyquist = sample_rate / 2.0
    cutoff = 80.0 / nyquist
    b, a = butter(2, cutoff, btype="high", analog=False)
    enhanced = lfilter(b, a, audio)

    # Gentle compression
    threshold = 0.3
    ratio = 4.0
    compressed = np.zeros_like(enhanced)
    try:
        abs_signal = np.abs(enhanced)
    except (FloatingPointError, ValueError):
        logger.debug("apply_voice_enhancement: 绝对值计算异常，返回原数组")
        return audio
    above_threshold = abs_signal > threshold

    compressed[~above_threshold] = enhanced[~above_threshold]
    if np.any(above_threshold):
        gain = threshold + (abs_signal[above_threshold] - threshold) / ratio
        compressed[above_threshold] = np.sign(enhanced[above_threshold]) * gain

    try:
        peak = np.max(np.abs(compressed))
        if np.isfinite(peak) and peak > 0:
            compressed = compressed / peak * 0.708
    except (FloatingPointError, ValueError):
        logger.debug("apply_voice_enhancement: 峰值归一化异常，跳过")

    return compressed.astype(np.float32)


# ======================================================================
# 4. Pedalboard 音频效果器链
# ======================================================================

# 预设定义：每个预设映射到 (Pedalboard 类名, 参数字典) 列表
_EFFECT_PRESETS: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "warm": [
        ("Reverb", {"room_size": 0.4, "damping": 0.6, "wet_level": 0.15, "dry_level": 0.85}),
        ("Gain", {"gain_db": 2.0}),
        ("LowShelfFilter", {"cutoff_frequency_hz": 200, "gain_db": 3.0}),
    ],
    "bright": [
        ("HighShelfFilter", {"cutoff_frequency_hz": 4000, "gain_db": 4.0}),
        ("HighpassFilter", {"cutoff_frequency_hz": 100}),
        ("Gain", {"gain_db": 1.0}),
    ],
    "radio": [
        ("HighpassFilter", {"cutoff_frequency_hz": 300}),
        ("LowpassFilter", {"cutoff_frequency_hz": 3000}),
        ("Compressor", {"threshold_db": -20, "ratio": 5.0, "attack_ms": 5.0, "release_ms": 50.0}),
        ("Gain", {"gain_db": 3.0}),
    ],
    "cinematic": [
        ("Reverb", {"room_size": 0.8, "damping": 0.4, "wet_level": 0.3, "dry_level": 0.7}),
        ("Compressor", {"threshold_db": -18, "ratio": 3.0, "attack_ms": 10.0, "release_ms": 100.0}),
        ("LowShelfFilter", {"cutoff_frequency_hz": 150, "gain_db": 2.0}),
        ("Gain", {"gain_db": 1.5}),
    ],
}


class AudioEffectsProcessor:
    """基于 Spotify Pedalboard 的可链式音频效果处理器。

    若未安装 ``pedalboard``，所有处理方法均原样返回输入并在首次调用时告警。

    使用示例::

        proc = AudioEffectsProcessor(sample_rate=24000)
        result = proc.apply(audio, effects=["reverb", "gain"], reverb_room_size=0.5)
        result = proc.apply_preset(audio, "warm")

    Attributes:
        sample_rate (int): 音频采样率。
        _board (list[Any]): 效果器实例列表（内部使用）。
        _warned_no_pedalboard (bool): 是否已输出过 pedalboard 未安装告警（类级别标志）。
    """

    _warned_no_pedalboard: bool = False

    def __init__(self, sample_rate: int = 24000) -> None:
        """初始化音频效果处理器。

        Args:
            sample_rate: 待处理音频的采样率，单位 Hz，默认 24000。
        """
        self.sample_rate: int = sample_rate
        self._board: list[Any] = []

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    @classmethod
    def _warn_stub(cls) -> None:
        """首次调用时输出 pedalboard 未安装告警（仅告警一次）。"""
        if not cls._warned_no_pedalboard:
            logger.warning("pedalboard 未安装；音频效果将为空操作（no-op）。如需音频效果请安装: pip install pedalboard")
            cls._warned_no_pedalboard = True

    def _build_effect(self, name: str, **kwargs: Any) -> Any | None:
        """按名称实例化单个 pedalboard 效果器。

        Args:
            name: 效果器名称（小写别名）。
            **kwargs: 传递给效果器构造函数的参数。

        Returns:
            效果器实例；名称未知或实例化失败时返回 None。
        """
        if not _HAS_PEDALBOARD:
            return None

        effect_map: dict[str, type] = {
            "pitch_shift": _pedalboard.PitchShift,
            "reverb": _pedalboard.Reverb,
            "delay": _pedalboard.Delay,
            "chorus": _pedalboard.Chorus,
            "compression": _pedalboard.Compressor,
            "compressor": _pedalboard.Compressor,
            "gain": _pedalboard.Gain,
            "highpass": _pedalboard.HighpassFilter,
            "highpass_filter": _pedalboard.HighpassFilter,
            "lowpass": _pedalboard.LowpassFilter,
            "lowpass_filter": _pedalboard.LowpassFilter,
        }

        cls_ref = effect_map.get(name)
        if cls_ref is None:
            logger.warning("未知的 pedalboard 效果: %s（已跳过）", name)
            return None

        try:
            return cls_ref(**kwargs)
        except Exception as exc:
            logger.warning("创建效果 %s(%s) 失败: %s", name, kwargs, exc)
            return None

    def _build_preset(self, preset_name: str) -> list[Any]:
        """从预设定义构建 Pedalboard 效果链。

        Args:
            preset_name: 预设名称（"warm"/"bright"/"radio"/"cinematic"）。

        Returns:
            效果器实例列表；预设未知时返回空列表。
        """
        if not _HAS_PEDALBOARD:
            return []

        spec_list = _EFFECT_PRESETS.get(preset_name)
        if spec_list is None:
            logger.warning("未知预设: %s", preset_name)
            return []

        effects: list[Any] = []
        for effect_name, effect_kwargs in spec_list:
            if effect_name == "LowShelfFilter":
                try:
                    effects.append(_pedalboard.LowShelfFilter(**effect_kwargs))
                except Exception as exc:
                    logger.warning("创建 %s 失败: %s", effect_name, exc)
            elif effect_name == "HighShelfFilter":
                try:
                    effects.append(_pedalboard.HighShelfFilter(**effect_kwargs))
                except Exception as exc:
                    logger.warning("创建 %s 失败: %s", effect_name, exc)
            else:
                eff = self._build_effect(effect_name.lower(), **effect_kwargs)
                if eff is not None:
                    effects.append(eff)

        return effects

    def _process_with_board(self, audio: np.ndarray, effects: list[Any]) -> np.ndarray:
        """将音频送入 Pedalboard 实例运行效果处理。

        Args:
            audio: 输入音频数组。
            effects: 效果器实例列表。

        Returns:
            处理后的音频数组（float32）；处理失败时返回原音频。
        """
        if not effects or not _HAS_PEDALBOARD:
            return audio

        board = _pedalboard.Pedalboard(effects)
        try:
            result = board(audio, self.sample_rate) if audio.ndim == 1 else board(audio, self.sample_rate)
        except Exception as exc:
            logger.warning("Pedalboard 处理失败（%s），返回原音频", exc)
            return audio

        return result.astype(np.float32)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def apply(
        self,
        audio: np.ndarray,
        effects: list[str] | None = None,
        *,
        pitch_shift_semitones: int = 0,
        reverb_room_size: float = 0.5,
        reverb_damping: float = 0.5,
        reverb_wet_level: float = 0.2,
        reverb_dry_level: float = 0.8,
        delay_delay_seconds: float = 0.3,
        delay_feedback: float = 0.3,
        delay_mix: float = 0.3,
        chorus_rate_hz: float = 1.5,
        chorus_depth: float = 0.25,
        chorus_mix: float = 0.5,
        compression_threshold_db: float = -20.0,
        compression_ratio: float = 4.0,
        compression_attack_ms: float = 10.0,
        compression_release_ms: float = 100.0,
        gain_db: float = 0.0,
        highpass_cutoff_hz: float | None = None,
        lowpass_cutoff_hz: float | None = None,
    ) -> np.ndarray:
        """按名称列表与关键字参数应用一串效果。

        Args:
            audio: 输入音频数组 (float32)。
            effects: 效果名称列表。支持：
                ``"pitch_shift"`` / ``"reverb"`` / ``"delay"`` / ``"chorus"`` /
                ``"compression"`` / ``"compressor"`` / ``"gain"`` /
                ``"highpass"`` / ``"lowpass"``。
            pitch_shift_semitones: 移调半音数（0 为不移调）。
            reverb_room_size: 混响房间大小 (0–1)。
            reverb_damping: 混响阻尼 (0–1)。
            reverb_wet_level: 混响湿信号比例 (0–1)。
            reverb_dry_level: 混响干信号比例 (0–1)。
            delay_delay_seconds: 延迟时间（秒）。
            delay_feedback: 延迟反馈 (0–1)。
            delay_mix: 延迟混合比 (0–1)。
            chorus_rate_hz: 合唱调制频率（Hz）。
            chorus_depth: 合唱深度 (0–1)。
            chorus_mix: 合唱混合比 (0–1)。
            compression_threshold_db: 压缩阈值（dB）。
            compression_ratio: 压缩比。
            compression_attack_ms: 压缩启动（毫秒）。
            compression_release_ms: 压缩释放（毫秒）。
            gain_db: 增益（dB）。0 跳过。
            highpass_cutoff_hz: 高通截止频率；``None`` 跳过。
            lowpass_cutoff_hz: 低通截止频率；``None`` 跳过。

        Returns:
            处理后音频 (float32)。
        """
        if not _HAS_PEDALBOARD:
            self._warn_stub()
            return audio

        if not effects:
            return audio

        effect_instances: list[Any] = []

        for name in effects:
            if name == "pitch_shift" and pitch_shift_semitones != 0:
                effect_instances.append(self._build_effect("pitch_shift", semitones=pitch_shift_semitones))
            elif name == "reverb":
                effect_instances.append(
                    self._build_effect(
                        "reverb",
                        room_size=reverb_room_size,
                        damping=reverb_damping,
                        wet_level=reverb_wet_level,
                        dry_level=reverb_dry_level,
                    )
                )
            elif name == "delay":
                effect_instances.append(
                    self._build_effect(
                        "delay",
                        delay_seconds=delay_delay_seconds,
                        feedback=delay_feedback,
                        mix=delay_mix,
                    )
                )
            elif name == "chorus":
                effect_instances.append(
                    self._build_effect(
                        "chorus",
                        rate_hz=chorus_rate_hz,
                        depth=chorus_depth,
                        mix=chorus_mix,
                    )
                )
            elif name in ("compression", "compressor"):
                effect_instances.append(
                    self._build_effect(
                        "compression",
                        threshold_db=compression_threshold_db,
                        ratio=compression_ratio,
                        attack_ms=compression_attack_ms,
                        release_ms=compression_release_ms,
                    )
                )
            elif name == "gain" and gain_db != 0.0:
                effect_instances.append(self._build_effect("gain", gain_db=gain_db))
            elif name == "highpass" and highpass_cutoff_hz is not None:
                effect_instances.append(self._build_effect("highpass", cutoff_frequency_hz=highpass_cutoff_hz))
            elif name == "lowpass" and lowpass_cutoff_hz is not None:
                effect_instances.append(self._build_effect("lowpass", cutoff_frequency_hz=lowpass_cutoff_hz))
            else:
                logger.debug("Effect %s skipped (no parameters or unknown)", name)

        effect_instances = [e for e in effect_instances if e is not None]

        if not effect_instances:
            return audio

        return self._process_with_board(audio, effect_instances)

    def apply_preset(self, audio: np.ndarray, preset: str) -> np.ndarray:
        """应用命名效果预设。

        可用预设：``"warm"`` / ``"bright"`` / ``"radio"`` / ``"cinematic"``。

        Args:
            audio: 输入音频数组 (float32)。
            preset: 预设名称。

        Returns:
            处理后音频 (float32)。
        """
        if not _HAS_PEDALBOARD:
            self._warn_stub()
            return audio

        effects = self._build_preset(preset)
        if not effects:
            return audio

        return self._process_with_board(audio, effects)

    @staticmethod
    def available_presets() -> list[str]:
        """返回可用预设名称列表。

        Returns:
            list[str]: 所有预设名称的列表。
        """
        return list(_EFFECT_PRESETS.keys())

    @staticmethod
    def is_available() -> bool:
        """返回 pedalboard 是否已安装（效果链是否可用）。

        Returns:
            bool: pedalboard 可用返回 True，否则返回 False。
        """
        return _HAS_PEDALBOARD


def apply_effects_chain(
    audio: np.ndarray,
    sample_rate: int,
    effects: list[dict[str, Any]],
) -> np.ndarray:
    """应用 dict 列表描述的效果链（pedalboard 通用接口）。

    适合从 JSON/YAML 配置反序列化的场景。每个效果 dict 结构：
    ``{"type": "Reverb", "params": {"room_size": 0.5, ...}}``

    未安装 pedalboard 时静默 no-op，不抛出异常（效果链是可选增强）。
    每个效果独立 try/except：单个效果失败不中断剩余效果。

    Args:
        audio: 输入音频数组 (float32)。
        sample_rate: 采样率，单位 Hz。
        effects: 效果列表。每项为 ``{"type": str, "params": dict}``。
            ``type`` 支持的取值与 pedalboard 类名一一对应（大小写敏感）：
            ``Reverb`` / ``Delay`` / ``Chorus`` / ``Compressor`` /
            ``Gain`` / ``PitchShift`` / ``HighpassFilter`` / ``LowpassFilter`` /
            ``LowShelfFilter`` / ``HighShelfFilter`` / ``Distortion`` 等。

    Returns:
        处理后音频 (float32)。任何环节失败都至少返回原音频。
    """
    if not _HAS_PEDALBOARD:
        AudioEffectsProcessor._warn_stub()
        return audio

    if not effects:
        return audio

    processor = AudioEffectsProcessor(sample_rate=sample_rate)
    current: np.ndarray = audio

    for idx, spec in enumerate(effects):
        if not isinstance(spec, dict):
            logger.warning("apply_effects_chain: 第 %d 项不是 dict，跳过", idx)
            continue
        effect_type = spec.get("type")
        params = spec.get("params", {}) or {}
        if not isinstance(effect_type, str) or not effect_type:
            logger.warning("apply_effects_chain: 第 %d 项缺少 type，跳过", idx)
            continue
        if not isinstance(params, dict):
            logger.warning(
                "apply_effects_chain: 第 %d 项 params 不是 dict(type=%s)，跳过",
                idx,
                effect_type,
            )
            continue

        # 尝试通过 pedalboard 顶层 getattr 找到类，失败回退 effect_map
        effect_cls: Any = None
        try:
            effect_cls = getattr(_pedalboard, effect_type, None)
        except Exception:
            effect_cls = None
        if effect_cls is None:
            # 回退小写别名映射
            alias = effect_type.lower()
            alias_map: dict[str, Any] = {
                "pitch_shift": _pedalboard.PitchShift if _HAS_PEDALBOARD else None,
                "reverb": _pedalboard.Reverb if _HAS_PEDALBOARD else None,
                "delay": _pedalboard.Delay if _HAS_PEDALBOARD else None,
                "chorus": _pedalboard.Chorus if _HAS_PEDALBOARD else None,
                "compression": _pedalboard.Compressor if _HAS_PEDALBOARD else None,
                "compressor": _pedalboard.Compressor if _HAS_PEDALBOARD else None,
                "gain": _pedalboard.Gain if _HAS_PEDALBOARD else None,
                "highpass": _pedalboard.HighpassFilter if _HAS_PEDALBOARD else None,
                "highpass_filter": _pedalboard.HighpassFilter if _HAS_PEDALBOARD else None,
                "lowpass": _pedalboard.LowpassFilter if _HAS_PEDALBOARD else None,
                "lowpass_filter": _pedalboard.LowpassFilter if _HAS_PEDALBOARD else None,
                "low_shelf": _pedalboard.LowShelfFilter if _HAS_PEDALBOARD else None,
                "lowshelffilter": _pedalboard.LowShelfFilter if _HAS_PEDALBOARD else None,
                "high_shelf": _pedalboard.HighShelfFilter if _HAS_PEDALBOARD else None,
                "highshelffilter": _pedalboard.HighShelfFilter if _HAS_PEDALBOARD else None,
            }
            effect_cls = alias_map.get(alias)
        if effect_cls is None:
            logger.warning(
                "apply_effects_chain: 未找到效果类 type=%s，跳过。params=%s",
                effect_type,
                params,
            )
            continue

        try:
            instance = effect_cls(**params)
        except Exception as exc:
            logger.warning(
                "apply_effects_chain: 效果实例化失败 type=%s, params=%s: %s. 已跳过。",
                effect_type,
                params,
                exc,
            )
            continue

        try:
            current = processor._process_with_board(current, [instance])
        except Exception as exc:
            logger.warning(
                "apply_effects_chain: 效果处理失败 type=%s, params=%s: %s. 保留已处理结果，继续后续效果。",
                effect_type,
                params,
                exc,
            )
            continue

    return current


# ======================================================================
# 5. 静音裁切（能量阈值 + 可选 webrtcvad）
# ======================================================================


def _get_webrtcvad(sample_rate: int, aggressive_mode: int = 3) -> Any | None:
    """获取或创建缓存的 webrtcvad 对象。

    Args:
        sample_rate: 采样率。webrtcvad 仅支持 8000 / 16000 / 32000 / 48000。
        aggressive_mode: 激进程度 0–3，越大越倾向判为静音。

    Returns:
        Vad 对象或 ``None``（依赖缺失或采样率不支持）。
    """
    if not _HAS_WEBRTCVAD:
        return None
    if sample_rate not in (8000, 16000, 32000, 48000):
        return None
    aggressive_mode = max(0, min(3, aggressive_mode))
    key = (sample_rate, aggressive_mode)
    if key not in _vad_cache:
        try:
            vad = _webrtcvad.Vad(aggressive_mode)
            _vad_cache[key] = vad
        except Exception as exc:
            logger.debug("webrtcvad 创建失败: %s", exc)
            return None
    return _vad_cache[key]


def trim_silence_vad(
    audio: np.ndarray,
    sample_rate: int,
    threshold_db: float = -40.0,
    min_silence_sec: float = 0.5,
) -> np.ndarray:
    """裁切音频头尾静音（能量阈值 + 可选 webrtcvad 加速）。

    VAD 是优化功能——任何异常都会返回原数组，不阻塞主生成流程。

    Why min_silence_sec=0.5（不裁切到 0s 静音）：
        自然说话尾音（呼吸、气声、辅音余韵）是语音自然度的重要组成部分；
        切光会让结尾"戛然而止"产生机械感。0.5s 是"不突兀"的经验值，
        对应 24kHz 采样的 12000 个样本，能保留绝大部分自然收束。

    非语音内容（如带背景音乐的生成、歌唱合成）不建议开启——背景音乐会被误判。

    Args:
        audio: 输入音频数组 (float32, [-1, 1])。
        sample_rate: 采样率，单位 Hz。
        threshold_db: 静音能量阈值（dBFS）。默认 ``-40.0``。
            更小（更负）= 越不容易判定为静音（保留更多气声）；
            更大（接近 0）= 越激进地裁切掉非峰值段。
        min_silence_sec: 裁切后，在有效语音首尾额外保留的最短短语静音长度（秒）。
            默认 ``0.5``。实际 padding 会根据检测到的边界自适应，不会
            强行在末尾补 0。

    Returns:
        裁切后音频 (float32)。
    """
    try:
        if audio.size == 0:
            return audio

        threshold_linear: float = 10 ** (threshold_db / 20.0)
        min_silence_samples: int = int(sample_rate * min_silence_sec)

        # --- Stage 1: 能量阈值快速定位（无依赖） ---
        try:
            abs_audio = np.abs(audio)
        except (FloatingPointError, ValueError) as exc:
            logger.debug("trim_silence_vad abs() 异常: %s，返回原数组", exc)
            return audio

        above_threshold = abs_audio > threshold_linear
        if not np.any(above_threshold):
            return audio

        first_speech = int(np.argmax(above_threshold))
        last_speech = int(len(above_threshold) - 1 - np.argmax(above_threshold[::-1]))

        # --- Stage 2: webrtcvad 可用时，在候选窗口内二次确认边界 ---
        vad = _get_webrtcvad(sample_rate)
        if vad is not None:
            try:
                frame_ms = 30
                frame_samples = int(sample_rate * frame_ms / 1000)

                def _is_speech_frame(start_idx: int) -> bool:
                    end_idx = min(start_idx + frame_samples, audio.size)
                    frame = audio[start_idx:end_idx]
                    if frame.size < frame_samples:
                        pad = np.zeros(frame_samples - frame.size, dtype=audio.dtype)
                        frame = np.concatenate([frame, pad])
                    pcm = (frame * 32767.0).astype(np.int16).tobytes()
                    try:
                        return bool(vad.is_speech(pcm, sample_rate))
                    except Exception:
                        return abs_audio[start_idx:end_idx].mean() > threshold_linear

                # 向左扩展 first_speech 寻找真正语音起点
                probe = max(0, first_speech - min_silence_samples)
                while probe < first_speech:
                    if _is_speech_frame(probe):
                        first_speech = min(first_speech, probe)
                        break
                    probe += frame_samples

                # 向右收缩 last_speech 寻找真正语音终点
                probe = min(audio.size - frame_samples, last_speech)
                while probe > last_speech - min_silence_samples and probe >= 0:
                    if not _is_speech_frame(probe):
                        last_speech = max(last_speech, probe)
                        break
                    probe -= frame_samples
            except Exception as exc:
                logger.debug("trim_silence_vad webrtcvad 阶段异常，退回能量阈值结果: %s", exc)

        start = max(0, first_speech)
        end = min(audio.size, last_speech + 1)

        # 额外 padding：在检测边界外保留 min_silence_sec，但不越界
        start = max(0, start - min_silence_samples // 2)
        end = min(audio.size, end + min_silence_samples // 2)

        if start >= end:
            return audio

        result = audio[start:end]
        if result.size == 0:
            return audio
        return result.astype(np.float32)

    except Exception as exc:
        # VAD 是优化功能，全静音 / 零长度 / 任何异常都不应抛
        logger.debug("trim_silence_vad 捕获异常 type=%s: %s，返回原数组", type(exc).__name__, exc)
        return audio


def trim_tts_output(
    audio: np.ndarray,
    sample_rate: int = 24000,
    threshold_db: float = -40.0,
    padding_ms: int = 50,
    pop_threshold_db: float = -3.0,
    detect_internal_hallucination: bool = True,
    max_internal_silence_ms: int = 1000,
    fade_ms: int = 30,
) -> np.ndarray:
    """自动裁切 TTS 输出头尾静音 + 异常爆音（pop）+ 内部长静音幻觉检测。

    参考 VoiceBox 的 trim_tts_output 实现增强：
    1. 先用能量检测定位首次/末次非静音样本，再加少量 padding 避免切掉辅音；
    2. 对首尾 50ms 窗口内的超大瞬态（pop 爆音）单独检测和切除；
    3. 检测内部长静音段（speech -> silence -> hallucinated noise），发现则截断；
    4. 在末尾应用余弦淡入淡出，避免 click 噪声。

    与 trim_silence_vad 的区别：本函数面向 TTS 专属异常（爆音检测、幻觉检测），
    不依赖 webrtcvad；trim_silence_vad 侧重长语音自然边界裁切。

    Args:
        audio: 输入音频数组 (float32, [-1, 1])。
        sample_rate: 采样率，单位 Hz。默认 24000。
        threshold_db: 静音阈值（dBFS）。默认 ``-40.0``。
        padding_ms: 检测到的语音边界外额外保留的 padding（毫秒）。默认 ``50``。
            太小会切碎辅音 / 气声起始。
        pop_threshold_db: 首尾爆音判定阈值（dBFS）。默认 ``-3.0``。
            50ms 窗口内首个样本超过该值视为"数字爆音"（TTS 偶尔在段首产生）。
        detect_internal_hallucination: 是否检测内部长静音幻觉。默认 ``True``。
        max_internal_silence_ms: 内部静音超过此时长（毫秒）视为幻觉，截断。默认 1000。
        fade_ms: 末尾余弦淡出时长（毫秒）。默认 30。

    Returns:
        裁切后音频数组 (float32)。
    """
    try:
        if audio.size == 0:
            return audio

        threshold_linear = 10 ** (threshold_db / 20.0)
        pop_linear = 10 ** (pop_threshold_db / 20.0)
        padding_samples = int(sample_rate * padding_ms / 1000.0)

        # --- Detect and remove leading pops (向量化) ---
        pop_scan_end = min(sample_rate // 20, audio.size)
        leading_pop_end = 0
        if pop_scan_end > 0:
            leading_window = np.abs(audio[:pop_scan_end])
            above_pop = leading_window > pop_linear
            if np.any(above_pop):
                # 找到第一个超过阈值的位置
                first_pop_idx = int(np.argmax(above_pop))
                # 找到连续超过阈值区域的结束位置
                # 从 first_pop_idx 开始找第一个低于阈值的位置
                pop_region = above_pop[first_pop_idx:]
                # 整个窗口都是爆音
                pop_end_rel = pop_region.size if np.all(pop_region) else int(np.argmax(~pop_region))
                j = first_pop_idx + pop_end_rel
                j = min(j + padding_samples, pop_scan_end)
                leading_pop_end = max(leading_pop_end, j)

        # --- Detect and remove trailing pops (向量化) ---
        pop_scan_start = max(audio.size - sample_rate // 20, 0)
        trailing_pop_start = audio.size
        if pop_scan_start < audio.size:
            trailing_window = np.abs(audio[pop_scan_start:])
            above_pop_trail = trailing_window > pop_linear
            if np.any(above_pop_trail):
                # 反向找最后一个超过阈值的位置
                last_pop_rel = len(above_pop_trail) - 1 - int(np.argmax(above_pop_trail[::-1]))
                # 找连续爆音区域的起始位置（反向）
                pop_region_rev = above_pop_trail[: last_pop_rel + 1][::-1]
                if np.all(pop_region_rev):
                    pop_start_rel = 0
                else:
                    pop_start_rel_rev = int(np.argmax(~pop_region_rev))
                    pop_start_rel = last_pop_rel - pop_start_rel_rev
                j = pop_scan_start + pop_start_rel
                j = max(j - padding_samples, pop_scan_start)
                trailing_pop_start = min(trailing_pop_start, j)

        trimmed = audio[leading_pop_end:trailing_pop_start]

        if trimmed.size == 0:
            return audio

        # --- Detect internal long silence (hallucination) ---
        if detect_internal_hallucination:
            cut_sample = detect_long_silence(
                trimmed,
                sample_rate,
                frame_ms=20,
                silence_threshold_db=threshold_db,
                max_internal_silence_ms=max_internal_silence_ms,
            )
            if cut_sample is not None and cut_sample > 0:
                logger.debug(f"[trim_tts_output] 检测到内部长静音幻觉，在样本 {cut_sample} 处截断")
                trimmed = trimmed[:cut_sample]

        if trimmed.size == 0:
            return audio

        # --- Energy-based silence trimming ---
        try:
            abs_audio = np.abs(trimmed)
        except (FloatingPointError, ValueError) as exc:
            logger.debug("trim_tts_output abs() 异常: %s，返回原数组", exc)
            return audio
        above_threshold = abs_audio > threshold_linear

        if not np.any(above_threshold):
            return audio

        first_speech = int(np.argmax(above_threshold))
        last_speech = int(len(above_threshold) - 1 - np.argmax(above_threshold[::-1]))

        start = max(0, first_speech - padding_samples)
        end = min(len(trimmed), last_speech + padding_samples + 1)

        result = trimmed[start:end]
        if result.size == 0:
            return audio

        # --- Apply cosine fade-out to avoid clicks ---
        fade_samples = int(sample_rate * fade_ms / 1000.0)
        if fade_samples > 0 and result.size > fade_samples:
            fade = np.cos(np.linspace(0, np.pi / 2, fade_samples)) ** 2
            result[-fade_samples:] *= fade

        return result.astype(np.float32)

    except Exception as exc:
        logger.debug(
            "trim_tts_output 捕获异常 type=%s: %s，返回原数组",
            type(exc).__name__,
            exc,
        )
        return audio


# ======================================================================
# 6. 降噪（Noise suppression / denoising）
# ======================================================================


def reduce_noise(
    audio: np.ndarray,
    sample_rate: int,
    noise_sample: np.ndarray | None = None,
) -> np.ndarray:
    """降噪（ZipEnhancer 模型优先，noisereduce 库为回退）。

    Args:
        audio: 输入音频数组 (float32, [-1, 1])。
        sample_rate: 采样率，单位 Hz。
        noise_sample: 可选的纯噪声剖面片段。
            - 未提供（默认 ``None``）：取 ``audio`` 前 0.5 秒估计噪声剖面。
            - 已提供：必须是与 ``audio`` 同采样率的片段，建议 0.1–1 秒纯噪声。

    Returns:
        降噪后音频数组 (float32)。任何失败均返回原音频，不抛异常。
    """
    if audio.size == 0:
        return audio

    # --- Stage 1: 优先 ZipEnhancer 模型（同 denoise_audio） ---
    try:
        from .model_registry import registry

        enhancer = registry.voxcpm_enhancer_model
        if enhancer is not None:
            logger.debug("reduce_noise: 使用 ZipEnhancer 模型")
            try:
                if hasattr(enhancer, "denoise"):
                    result = enhancer.denoise(audio, sample_rate)
                elif callable(enhancer):
                    result = enhancer(audio, sample_rate)
                else:
                    result = None

                if result is not None:
                    if isinstance(result, np.ndarray):
                        return result.astype(np.float32)
                    if hasattr(result, "cpu"):
                        return result.cpu().numpy().astype(np.float32)
                    return np.asarray(result, dtype=np.float32)
            except Exception as exc:
                logger.warning("reduce_noise: ZipEnhancer 失败: %s", exc)
    except ImportError:
        pass

    # --- Stage 2: noisereduce 库 ---
    if _HAS_NOISEREDUCE:
        logger.debug("reduce_noise: 使用 noisereduce 库")
        try:
            if noise_sample is None:
                # 未提供噪声剖面：取音频前 0.5s 估计
                noise_len = min(audio.size, sample_rate // 2)
                noise_sample_use = audio[:noise_len] if noise_len > 0 else None
            else:
                noise_sample_use = noise_sample

            kwargs: dict[str, Any] = {
                "y": audio,
                "sr": sample_rate,
                "stationary": False,
            }
            if noise_sample_use is not None:
                kwargs["y_noise"] = noise_sample_use

            try:
                result = _noisereduce.reduce_noise(**kwargs)
            except OverflowError as exc:
                # noisereduce 内部整数运算在极短片段 / 极端振幅下可能溢出
                logger.warning(
                    "reduce_noise: noisereduce 抛出 OverflowError(%s)，返回原音频。audio.shape=%s, sample_rate=%d",
                    exc,
                    audio.shape,
                    sample_rate,
                )
                return audio
            return result.astype(np.float32)
        except Exception as exc:
            logger.warning("reduce_noise: noisereduce 失败: %s，返回原音频", exc)
            return audio

    logger.warning(
        "reduce_noise: 无可用降噪后端（ZipEnhancer 未加载 + noisereduce 未安装），"
        "原样返回。建议: pip install noisereduce"
    )
    return audio


def denoise_audio(
    audio: np.ndarray,
    sample_rate: int = 24000,
    *,
    prop_decrease: float = 1.0,
    use_enhancer: bool = True,
) -> np.ndarray:
    """使用可用降噪方法对音频去噪（向后兼容接口）。

    优先级：
        1. ZipEnhancer 模型（若 ``registry.voxcpm_enhancer_model`` 已加载且 ``use_enhancer=True``）
        2. ``noisereduce`` 库（若已安装）
        3. 原样返回 + 告警

    Args:
        audio: 输入音频数组 (float32, [-1, 1])。
        sample_rate: 采样率，单位 Hz。默认 24000。
        prop_decrease: 噪声去除比例 (0.0 – 1.0)。仅 noisereduce 生效，默认 1.0。
        use_enhancer: 是否尝试 ZipEnhancer 模型。默认 ``True``。

    Returns:
        去噪后音频 (float32)。
    """
    if audio.size == 0:
        return audio

    if use_enhancer:
        try:
            from .model_registry import registry

            enhancer = registry.voxcpm_enhancer_model
            if enhancer is not None:
                logger.debug("denoise_audio: 使用 ZipEnhancer 模型")
                try:
                    if hasattr(enhancer, "denoise"):
                        result = enhancer.denoise(audio, sample_rate)
                    elif callable(enhancer):
                        result = enhancer(audio, sample_rate)
                    else:
                        logger.warning("denoise_audio: ZipEnhancer 已加载但无可调用接口，跳过")
                        result = None

                    if result is not None:
                        if isinstance(result, np.ndarray):
                            return result.astype(np.float32)
                        if hasattr(result, "cpu"):
                            return result.cpu().numpy().astype(np.float32)
                        return np.asarray(result, dtype=np.float32)
                except Exception as exc:
                    logger.warning("denoise_audio: ZipEnhancer 失败: %s", exc)
        except ImportError:
            pass

    if _HAS_NOISEREDUCE:
        logger.debug("denoise_audio: 使用 noisereduce 库")
        try:
            try:
                result = _noisereduce.reduce_noise(
                    y=audio,
                    sr=sample_rate,
                    prop_decrease=prop_decrease,
                    stationary=False,
                )
            except OverflowError as exc:
                logger.warning(
                    "denoise_audio: noisereduce 抛出 OverflowError(%s)，跳过 noisereduce。"
                    "audio.shape=%s, sample_rate=%d",
                    exc,
                    audio.shape,
                    sample_rate,
                )
                result = None

            if result is not None:
                return result.astype(np.float32)
        except Exception as exc:
            logger.warning("denoise_audio: noisereduce 失败: %s", exc)

    logger.warning("denoise_audio: 无可用降噪后端，原样返回。建议: pip install noisereduce")
    return audio


# ======================================================================
# 7. 统一后处理流水线（Master enhance_audio pipeline）
# ======================================================================


def enhance_audio(
    audio: np.ndarray,
    sample_rate: int,
    normalize: bool = True,
    tempo_factor: float = 1.0,
    voice_enhancement: bool = False,
    target_lufs: float = -16.0,
    method: str = "auto",
    trim_silence: bool = False,
    denoise: bool = False,
    effects_preset: str | None = None,
) -> np.ndarray:
    """按顺序应用全部后处理步骤（统一入口流水线）。

    处理顺序（不可重排，每个阶段的输入是上一阶段的输出）：
        1. 降噪（若 ``denoise=True``）
        2. 语音增强：EQ + 软压缩（若 ``voice_enhancement=True``）
        3. 响度归一化（若 ``normalize=True``）
        4. 静音裁切（若 ``trim_silence=True``，走 trim_tts_output 爆音检测分支）
        5. 效果器预设（若 ``effects_preset`` 指定）
        6. 速度调整（若 ``tempo_factor != 1.0``）

    Args:
        audio: 输入音频数组。
        sample_rate: 采样率，单位 Hz。
        normalize: 是否执行响度归一化。默认 ``True``。
        tempo_factor: 速度系数（1.0 不变）。默认 ``1.0``。
        voice_enhancement: 是否执行高通 + 软压缩的语音增强。默认 ``False``。
        target_lufs: 归一化目标响度（LUFS）。默认 ``-16.0``。
        method: 归一化方法 ``"auto"`` / ``"lufs"`` / ``"rms"``。默认 ``"auto"``。
        trim_silence: 是否执行 TTS 头尾静音 + 爆音裁切。默认 ``False``。
        denoise: 是否执行降噪。默认 ``False``。
        effects_preset: Pedalboard 预设名 ``"warm"`` / ``"bright"`` / ...。

    Returns:
        处理后音频数组。
    """
    # 内存优化（H-R6）：不再无条件 result = audio.copy()。
    # Why 原来需要 copy：trim_tts_output 会通过末尾余弦淡出 result[-fade:] *= fade
    # 原地修改传入缓冲区；若直接对调用方的 audio 操作会污染其数据。
    # 优化策略（等价但更省内存）：
    #   1. result 初始别名 audio（零拷贝）；
    #   2. denoise/voice_enhancement/normalize/effects/tempo 均返回新数组，
    #      任一执行都会自然与 audio 解耦；
    #   3. 唯一原地修改的 trim_tts_output 执行前，若 result 仍别名 audio
    #      （前序步骤全部为 no-op）才做一次 copy，保护调用方输入。
    # 收益：无处理步骤 / 仅归一化等常见路径完全省去一次全量 float32 拷贝，
    #      降低长音频（分钟级）的内存峰值。
    result = audio

    if denoise:
        result = denoise_audio(result, sample_rate)

    if voice_enhancement:
        result = apply_voice_enhancement(result, sample_rate)

    if normalize:
        result = normalize_loudness(result, sample_rate, target_lufs, method=method)

    if trim_silence:
        # 保护调用方输入：仅当前序步骤未产生新数组（result 仍别名 audio）时才拷贝
        if result is audio:
            result = result.copy()
        result = trim_tts_output(result, sample_rate)

    if effects_preset is not None:
        proc = AudioEffectsProcessor(sample_rate=sample_rate)
        result = proc.apply_preset(result, effects_preset)

    if tempo_factor != 1.0:
        result, _ = adjust_tempo(result, sample_rate, tempo_factor)

    return result


# ======================================================================
# 8. 参考音频预处理与验证（参考 VoiceBox 实现）
# ======================================================================


def preprocess_reference_audio(
    audio: np.ndarray,
    sample_rate: int,
    peak_target: float = 0.95,
    trim_top_db: float = 40.0,
    edge_padding_ms: int = 100,
    normalize_loudness_flag: bool = True,
    target_lufs: float = LUFS_CHATTERBOX,
) -> np.ndarray:
    """在验证/存储前清理参考音频样本。

    参考 VoiceBox 的 preprocess_reference_audio 和 Chatterbox 的 norm_loudness 实现，
    用于在音色克隆前预处理参考音频，提高克隆质量。

    处理步骤：
        1. 移除 DC 偏移（减去均值）
        2. 裁切首尾静音（librosa.effects.trim，不可用时回退 trim_silence_vad）
        3. 添加边缘 padding（避免 TTS 引擎在边界处产生爆音）
        4. 峰值限制（防止削波）
        5. 可选响度归一化（参考 Chatterbox -27 LUFS 标准，提升克隆一致性）

    Args:
        audio: 单声道音频数组（float32）。
        sample_rate: ``audio`` 的采样率，单位 Hz。
        peak_target: 峰值振幅上限，范围 [0, 1]。仅当输入峰值超过该值时应用。
        trim_top_db: 边缘静音裁切阈值，相对于峰值的 dB 数。40dB 位于正常语音
            动态范围（≈30dB）之下，可保留柔和尾音同时捕获明显的首尾静音。
            值越小裁切越激进。
        edge_padding_ms: 仅当裁切缩短了波形时，在每个边缘回填的静音毫秒数。
            为 TTS 引擎提供短暂静音锚点，同时不会使输出比输入更长。
        normalize_loudness_flag: 是否执行响度归一化。默认 True，目标为
            Chatterbox 的 -27 LUFS 以获得一致的克隆效果。
        target_lufs: 当 normalize_loudness_flag=True 时的目标响度（LUFS）。

    Returns:
        np.ndarray: 预处理后的音频数组（float32）。
    """
    audio = audio.astype(np.float32, copy=False)

    if audio.size == 0:
        return audio

    # Step 1: Remove DC offset
    audio = audio - float(np.mean(audio))

    # Step 2: Trim leading/trailing silence
    try:
        import librosa

        trimmed, _ = librosa.effects.trim(audio, top_db=trim_top_db)
    except ImportError:
        # Fallback: simple energy-based trim if librosa not available
        trimmed = trim_silence_vad(audio, sample_rate, threshold_db=-trim_top_db)
    except Exception:
        trimmed = audio

    # Step 3: Add edge padding if trimming shortened the audio
    if 0 < trimmed.size < audio.size:
        pad_each = int(sample_rate * edge_padding_ms / 1000)
        headroom = (audio.size - trimmed.size) // 2
        pad = min(pad_each, max(headroom, 0))
        if pad > 0:
            trimmed = np.pad(trimmed, (pad, pad), mode="constant")
        audio = trimmed

    # Step 4: Peak limiting to prevent clipping
    peak = float(np.abs(audio).max())
    if peak > peak_target and peak > 0:
        audio = audio * (peak_target / peak)

    # Step 5: Loudness normalization (Chatterbox style -27 LUFS)
    if normalize_loudness_flag:
        try:
            audio = normalize_loudness(audio, sample_rate, target_lufs=target_lufs, method="auto")
        except Exception as exc:
            logger.debug("参考音频响度归一化失败（非关键）: %s", exc)

    return audio


def validate_reference_audio(
    audio_path: str,
    min_duration: float = 2.0,
    max_duration: float = 30.0,
    min_rms: float = 0.01,
) -> tuple[bool, str | None]:
    """验证用于语音克隆的参考音频。

    Args:
        audio_path: 音频文件路径。
        min_duration: 最小时长（秒），默认 2.0。
        max_duration: 最大时长（秒），默认 30.0。
        min_rms: 最小 RMS 能量阈值，低于此值视为静音。

    Returns:
        Tuple[bool, Optional[str]]: (是否有效, 错误消息)。有效时错误消息为 None。
    """
    result = validate_and_load_reference_audio(audio_path, min_duration, max_duration, min_rms)
    return (result[0], result[1])


def validate_and_load_reference_audio(
    audio_path: str,
    min_duration: float = 2.0,
    max_duration: float = 30.0,
    min_rms: float = 0.01,
) -> tuple[bool, str | None, np.ndarray | None, int | None]:
    """一次性完成参考音频验证与加载。

    在检查前先应用 :func:`preprocess_reference_audio`，避免略高电平的录音
    因削波被误拒。时长和 RMS 检查在预处理后的波形上执行。

    Args:
        audio_path: 音频文件路径。
        min_duration: 最小时长（秒），默认 2.0。
        max_duration: 最大时长（秒），默认 30.0。
        min_rms: 最小 RMS 能量阈值。

    Returns:
        Tuple[bool, Optional[str], Optional[np.ndarray], Optional[int]]:
            (是否有效, 错误消息, 音频数组, 采样率)。
            无效时错误消息非空，音频数组和采样率为 None。
    """
    try:
        import soundfile as sf

        audio, sr = sf.read(audio_path)

        # Convert to mono if needed
        if audio.ndim > 1:
            audio = np.mean(audio, axis=-1)

        # Convert to float32
        audio = audio.astype(np.float32) / 32768.0 if audio.dtype == np.int16 else audio.astype(np.float32)

        # Apply preprocessing
        audio = preprocess_reference_audio(audio, sr)
        duration = len(audio) / sr

        if duration < min_duration:
            return False, f"音频过短（最短需要 {min_duration} 秒）", None, None
        if duration > max_duration:
            return False, f"音频过长（最长 {max_duration} 秒）", None, None

        rms = np.sqrt(np.mean(audio**2))
        if rms < min_rms:
            return False, "音频音量过低或为静音", None, None

        return True, None, audio, sr
    except Exception as e:
        return False, f"音频验证失败: {str(e)}", None, None


def detect_long_silence(
    audio: np.ndarray,
    sample_rate: int,
    frame_ms: int = 20,
    silence_threshold_db: float = -40.0,
    max_internal_silence_ms: int = 1000,
) -> int | None:
    """检测可能表明模型幻觉的内部长静音段。

    参考 VoiceBox 的 trim_tts_output 实现，检测音频中超过阈值的内部静音段，
    这通常表明 TTS 模型产生了幻觉（语音 -> 静音 -> 噪声）。

    Args:
        audio: 输入音频数组（单声道 float32）。
        sample_rate: 采样率，单位 Hz。
        frame_ms: RMS 能量计算的帧大小（毫秒），默认 20ms。
        silence_threshold_db: 低于此 dB 阈值的帧判定为静音，默认 -40dB。
        max_internal_silence_ms: 内部静音超过此时长（毫秒）则在该处截断，默认 1000ms。

    Returns:
        Optional[int]: 应截断处的样本索引；未检测到长静音则返回 None。
    """
    frame_len = int(sample_rate * frame_ms / 1000)
    if frame_len == 0 or len(audio) < frame_len:
        return None

    n_frames = len(audio) // frame_len
    threshold_linear = 10 ** (silence_threshold_db / 20)

    # Compute per-frame RMS (向量化：reshape 后批量计算，比 Python 循环快 10-100x)
    frames = audio[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    is_speech = rms >= threshold_linear

    # Find first speech frame (向量化)
    speech_indices = np.where(is_speech)[0]
    if len(speech_indices) == 0:
        return None
    first_speech = max(0, int(speech_indices[0]) - 1)

    # Walk forward from first speech; detect long internal silence gaps
    # 使用向量化操作检测连续静音段
    max_silence_frames = int(max_internal_silence_ms / frame_ms)

    # 提取从 first_speech 开始的语音/静音序列
    segment = is_speech[first_speech:]
    if len(segment) == 0:
        return None

    # 找连续 False（静音）的运行长度
    # 方法：计算相邻 True 之间的距离
    # 先补一个 True 在开头，确保从语音段开始
    padded = np.concatenate([[True], segment])
    true_positions = np.where(padded)[0]

    # 计算两个相邻 True 之间的 False 数量
    if len(true_positions) >= 2:
        gaps = np.diff(true_positions) - 1
        long_silence_idx = np.where(gaps >= max_silence_frames)[0]
        if len(long_silence_idx) > 0:
            # 第一个长静音段的起始帧
            first_long_gap = int(long_silence_idx[0])
            cut_frame = first_speech + int(true_positions[first_long_gap] + 1)
            cut_sample = cut_frame * frame_len
            return cut_sample

    return None
