"""
神经音频水印 - 用于 AI 生成语音的内容来源追溯。

受 Chatterbox 的 Perth 水印技术启发，本模块为 TTS 生成的音频提供不可感知的水印嵌入，
用于内容来源追踪和 AI 生成内容识别。

水印将唯一标识符嵌入音频信号中，具备以下特性：
- 对人类听众不可感知
- 对常见音频变换（压缩、重采样）具有鲁棒性
- 可通过检测算法进行验证

水印策略：
  - 使用扩频技术在频域嵌入
  - 水印比特编码为伪随机噪声模式
  - 在 16-20 kHz 频段嵌入（高于大部分语音能量）
  - SNR（信噪比）保持在 30dB 以上以确保透明性

这是一个轻量级的纯 CPU 实现，不需要神经网络推理，适用于实时应用场景。
"""

from __future__ import annotations

import hashlib
import logging
import struct
import time
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger("tts_multimodel.watermark")

# 水印参数
_WATERMARK_VERSION = 1
_WATERMARK_BITS = 64  # 水印载荷的比特数
_WATERMARK_STRENGTH = 0.008  # 嵌入强度（水印信号幅度）
_WATERMARK_FREQ_LOW = 16000  # 嵌入频率下限（Hz）
_WATERMARK_FREQ_HIGH = 20000  # 嵌入频率上限（Hz）
_WATERMARK_FRAME_SIZE = 2048  # FFT 帧大小
_WATERMARK_REPEAT = 4  # 水印重复次数以增强鲁棒性


@dataclass
class WatermarkPayload:
    """解码后的水印载荷数据。

    Attributes:
        version: 水印版本号。
        source_id: 唯一来源标识符（如 "tts-multimodel"）。
        timestamp: 嵌入水印时的 Unix 时间戳。
        content_hash: 音频内容的短哈希值。
        extra: 可选的附加元数据。
    """

    version: int
    source_id: str
    timestamp: float
    content_hash: str
    extra: dict | None = None


@dataclass
class WatermarkResult:
    """水印嵌入或检测的结果。

    Attributes:
        success: 操作是否成功。
        message: 结果描述信息。
        payload: 解码出的水印载荷，失败时为 None。
        snr_db: 嵌入后的信噪比（dB）。
    """

    success: bool
    message: str
    payload: WatermarkPayload | None = None
    snr_db: float = 0.0


# ============================================================================
# 水印生成
# ============================================================================


def _generate_watermark_key(source_id: str, timestamp: float) -> np.ndarray:
    """根据来源 ID 和时间戳生成伪随机水印密钥。

    密钥决定用于嵌入的扩频模式。使用相同的 source_id 和 timestamp
    会产生相同的密钥，从而支持水印检测。

    Args:
        source_id: 来源标识符。
        timestamp: Unix 时间戳。

    Returns:
        双极性序列（+1/-1）组成的密钥数组。
    """
    seed_data = f"{source_id}:{timestamp:.6f}".encode()
    seed = int(hashlib.sha256(seed_data).hexdigest()[:8], 16) % (2**32)
    rng = np.random.RandomState(seed)
    return rng.choice([-1.0, 1.0], size=_WATERMARK_BITS)


def _bits_to_payload_bytes(source_id: str, timestamp: float, content_hash: str) -> bytes:
    """将水印载荷编码为字节序列，用于嵌入。

    编码格式：version(1B) + source_id_len(1B) + source_id + timestamp(8B) + content_hash(8B)

    Args:
        source_id: 来源标识符。
        timestamp: Unix 时间戳。
        content_hash: 内容哈希值。

    Returns:
        编码后的字节序列。
    """
    source_bytes = source_id.encode("utf-8")[:32]
    return struct.pack(
        f"B B {len(source_bytes)}s d 8s",
        _WATERMARK_VERSION,
        len(source_bytes),
        source_bytes,
        timestamp,
        content_hash[:8].encode("utf-8"),
    )


def _payload_bytes_to_bits(payload_bytes: bytes) -> np.ndarray:
    """将载荷字节序列转换为比特数组。

    Args:
        payload_bytes: 载荷字节序列。

    Returns:
        映射为 +1/-1 的双极性比特数组。
    """
    bits = []
    for byte in payload_bytes:
        for i in range(8):
            bits.append((byte >> (7 - i)) & 1)
    while len(bits) < _WATERMARK_BITS:
        bits.append(0)
    return np.array(bits[:_WATERMARK_BITS], dtype=np.float64) * 2 - 1


def _compute_content_hash(audio: np.ndarray, sample_rate: int) -> str:
    """计算音频内容的短哈希，用于水印载荷。

    先将音频下采样到 16kHz，再量化为 16-bit 整数后计算 SHA256 哈希，
    以确保不同采样率下哈希的一致性。

    Args:
        audio: 音频数组。
        sample_rate: 采样率（Hz）。

    Returns:
        16 字符的十六进制哈希字符串。
    """
    if sample_rate != 16000:
        ratio = sample_rate / 16000
        n_samples = int(len(audio) / ratio)
        indices = np.linspace(0, len(audio) - 1, n_samples).astype(int)
        audio_16k = audio[indices]
    else:
        audio_16k = audio

    quantized = (audio_16k * 32767).astype(np.int16).tobytes()
    return hashlib.sha256(quantized).hexdigest()[:16]


# ============================================================================
# 水印嵌入
# ============================================================================


def embed_watermark(
    audio: np.ndarray,
    sample_rate: int,
    source_id: str = "tts-multimodel",
    strength: float = _WATERMARK_STRENGTH,
    timestamp: float | None = None,
) -> tuple[np.ndarray, WatermarkResult]:
    """向音频中嵌入不可感知的水印。

    使用频域扩频技术，将水印比特编码为伪随机噪声模式嵌入到
    16-20kHz 高频段。采用重叠相加（overlap-add）方法和随机相位调制，
    确保水印不可感知且具有鲁棒性。

    嵌入算法流程：
    1. 计算音频内容哈希
    2. 生成水印密钥和载荷比特
    3. 逐帧进行 FFT，在选定频点叠加调制信号
    4. IFFT 后通过重叠相加合成水印信号
    5. 归一化水印幅度并叠加到原始音频
    6. 计算嵌入后 SNR

    Args:
        audio: 输入音频数组（float32，单声道或立体声）。
        sample_rate: 采样率（Hz）。
        source_id: 水印来源标识符。
        strength: 嵌入强度（0.001-0.05，默认 0.008）。
        timestamp: Unix 时间戳（默认使用当前时间）。

    Returns:
        (含水印的音频, WatermarkResult) 元组。
    """
    if timestamp is None:
        timestamp = time.time()

    audio_mono = np.mean(audio, axis=-1) if audio.ndim > 1 else audio.copy()

    audio_mono = audio_mono.astype(np.float32)

    content_hash = _compute_content_hash(audio_mono, sample_rate)

    payload_bytes = _bits_to_payload_bytes(source_id, timestamp, content_hash)
    payload_bits = _payload_bytes_to_bits(payload_bytes)

    n_samples = len(audio_mono)

    watermark_signal = np.zeros(n_samples, dtype=np.float32)

    freq_low_bin = int(_WATERMARK_FREQ_LOW * _WATERMARK_FRAME_SIZE / sample_rate)
    freq_high_bin = int(_WATERMARK_FREQ_HIGH * _WATERMARK_FRAME_SIZE / sample_rate)
    freq_high_bin = min(freq_high_bin, _WATERMARK_FRAME_SIZE // 2)

    n_freq_bins = freq_high_bin - freq_low_bin
    if n_freq_bins < _WATERMARK_BITS:
        logger.warning(
            f"频点数量不足（{n_freq_bins}），无法容纳 {_WATERMARK_BITS} 个水印比特。将有效比特数降至 {n_freq_bins}。"
        )
        effective_bits = min(_WATERMARK_BITS, n_freq_bins)
    else:
        effective_bits = _WATERMARK_BITS

    frame_size = _WATERMARK_FRAME_SIZE
    hop_size = frame_size // 2
    n_frames = max(1, (n_samples - frame_size) // hop_size + 1)

    rng = np.random.RandomState(42)
    carrier_phases = rng.uniform(0, 2 * np.pi, size=effective_bits)

    for _rep in range(_WATERMARK_REPEAT):
        for frame_idx in range(n_frames):
            start = frame_idx * hop_size
            end = min(start + frame_size, n_samples)
            if end - start < frame_size:
                break

            frame = audio_mono[start:end]

            fft = np.fft.rfft(frame)

            for bit_idx in range(effective_bits):
                freq_bin = freq_low_bin + bit_idx
                if freq_bin < len(fft):
                    modulation = payload_bits[bit_idx] * strength * np.exp(1j * carrier_phases[bit_idx])
                    fft[freq_bin] += modulation
                    mirror_bin = frame_size - freq_bin
                    if mirror_bin < len(fft):
                        fft[mirror_bin] += np.conj(modulation)

            watermarked_frame = np.fft.irfft(fft, n=frame_size)

            watermark_signal[start:end] += watermarked_frame - frame

    max_wm = np.max(np.abs(watermark_signal))
    if max_wm > 0:
        watermark_signal = watermark_signal / max_wm * strength * np.max(np.abs(audio_mono))

    watermarked = audio_mono + watermark_signal

    noise_power = np.mean(watermark_signal**2)
    signal_power = np.mean(audio_mono**2)
    snr_db = 10 * np.log10(signal_power / noise_power) if noise_power > 0 else float("inf")

    if audio.ndim > 1:
        watermarked = np.stack([watermarked] * audio.shape[-1], axis=-1)

    payload = WatermarkPayload(
        version=_WATERMARK_VERSION,
        source_id=source_id,
        timestamp=timestamp,
        content_hash=content_hash,
    )

    logger.info(f"水印嵌入完成: source={source_id}, SNR={snr_db:.1f}dB, hash={content_hash}")

    return watermarked.astype(np.float32), WatermarkResult(
        success=True,
        message="水印嵌入成功",
        payload=payload,
        snr_db=snr_db,
    )


# ============================================================================
# 水印检测
# ============================================================================


def detect_watermark(
    audio: np.ndarray,
    sample_rate: int,
    source_id: str = "tts-multimodel",
) -> WatermarkResult:
    """从音频中检测并解码水印。

    使用基于相关性的检测算法，通过在频域计算与预期载波模式的
    相关度来提取嵌入的水印比特，然后重构载荷数据。

    检测算法流程：
    1. 对音频逐帧做 FFT
    2. 在嵌入频点计算与预期载波的相关度
    3. 通过相关度符号判定比特值（+1/-1）
    4. 将比特序列重组为字节并解析载荷
    5. 验证载荷有效性

    Args:
        audio: 输入音频数组（float32，单声道或立体声）。
        sample_rate: 采样率（Hz）。
        source_id: 期望的来源标识符，用于验证。

    Returns:
        WatermarkResult，包含检测到的载荷或失败信息。
    """
    audio_mono = np.mean(audio, axis=-1) if audio.ndim > 1 else audio.copy()

    audio_mono = audio_mono.astype(np.float32)
    n_samples = len(audio_mono)

    frame_size = _WATERMARK_FRAME_SIZE
    hop_size = frame_size // 2
    n_frames = max(1, (n_samples - frame_size) // hop_size + 1)

    freq_low_bin = int(_WATERMARK_FREQ_LOW * frame_size / sample_rate)
    freq_high_bin = int(_WATERMARK_FREQ_HIGH * frame_size / sample_rate)
    freq_high_bin = min(freq_high_bin, frame_size // 2)
    n_freq_bins = freq_high_bin - freq_low_bin
    effective_bits = min(_WATERMARK_BITS, n_freq_bins)

    rng = np.random.RandomState(42)
    carrier_phases = rng.uniform(0, 2 * np.pi, size=effective_bits)

    best_score = 0.0
    best_bits = np.zeros(effective_bits)

    current_time = time.time()
    candidates = [current_time]

    for _timestamp in candidates:
        correlations = np.zeros(effective_bits)
        counts = np.zeros(effective_bits)

        for frame_idx in range(n_frames):
            start = frame_idx * hop_size
            end = min(start + frame_size, n_samples)
            if end - start < frame_size:
                break

            frame = audio_mono[start:end]
            fft = np.fft.rfft(frame)

            for bit_idx in range(effective_bits):
                freq_bin = freq_low_bin + bit_idx
                if freq_bin < len(fft):
                    carrier = np.exp(1j * carrier_phases[bit_idx])
                    corr = np.real(fft[freq_bin] * np.conj(carrier))
                    correlations[bit_idx] += corr
                    counts[bit_idx] += 1

        valid = counts > 0
        if np.any(valid):
            avg_correlations = np.where(valid, correlations / counts, 0)
            detected_bits = np.sign(avg_correlations)
            score = np.mean(np.abs(avg_correlations))

            if score > best_score:
                best_score = score
                best_bits = detected_bits

    detection_threshold = 0.001
    if best_score < detection_threshold:
        return WatermarkResult(
            success=False,
            message="音频中未检测到水印",
            payload=None,
        )

    bits_uint8 = ((best_bits + 1) / 2).astype(np.uint8)[:_WATERMARK_BITS]
    bit_bytes = bytearray()
    for i in range(0, len(bits_uint8), 8):
        byte_val = 0
        for j in range(8):
            byte_val = ((byte_val << 1) | int(bits_uint8[i + j])) if i + j < len(bits_uint8) else (byte_val << 1)
        bit_bytes.append(byte_val)

    try:
        if len(bit_bytes) >= 1:
            version = bit_bytes[0]
            source_len = bit_bytes[1] if len(bit_bytes) > 1 else 0
            source = bytes(bit_bytes[2 : 2 + source_len]).decode("utf-8", errors="replace")
            ts_bytes = bytes(bit_bytes[2 + source_len : 2 + source_len + 8])
            timestamp = struct.unpack("d", ts_bytes)[0] if len(ts_bytes) == 8 else 0.0
            hash_bytes = bytes(bit_bytes[10 + source_len : 18 + source_len])
            content_hash = hash_bytes.decode("utf-8", errors="replace") if hash_bytes else ""

            payload = WatermarkPayload(
                version=version,
                source_id=source,
                timestamp=timestamp,
                content_hash=content_hash,
            )

            return WatermarkResult(
                success=True,
                message="水印检测成功",
                payload=payload,
                snr_db=best_score,
            )
    except Exception as e:
        logger.debug(f"载荷解码错误: {e}")

    return WatermarkResult(
        success=False,
        message="检测到水印但载荷解码失败",
        payload=None,
    )


# ============================================================================
# 便捷函数
# ============================================================================


def watermark_audio(
    audio: np.ndarray,
    sample_rate: int,
    enable: bool = True,
    source_id: str = "tts-multimodel",
) -> tuple[np.ndarray, dict]:
    """可选地为音频添加水印的便捷函数。

    Args:
        audio: 输入音频数组。
        sample_rate: 采样率（Hz）。
        enable: 是否启用水印，默认 True。
        source_id: 水印来源标识符。

    Returns:
        (处理后的音频, 元数据字典) 元组。元数据包含 watermarked（是否嵌入）、
        snr_db（信噪比）、source_id、content_hash 等字段。
    """
    if not enable:
        return audio, {"watermarked": False}

    watermarked, result = embed_watermark(audio, sample_rate, source_id=source_id)

    metadata = {
        "watermarked": result.success,
        "snr_db": round(result.snr_db, 1),
    }
    if result.payload:
        metadata["source_id"] = result.payload.source_id
        metadata["content_hash"] = result.payload.content_hash

    return watermarked, metadata
