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

import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger("tts_multimodel.watermark")


#: 水印嵌入失败时抛出的受控异常（桌面分发与安全加固 P0：block 档阻断产出）。
#: 调用点不得用裸 ``except Exception`` 吞掉——block 档语义是"产出被阻断"。
class WatermarkEmbedError(RuntimeError):
    """水印嵌入在 block 档失败——产出被阻断（未嵌入可溯源水印的音频不允许产出）。"""


#: 水印密钥环境变量（桌面分发与安全加固 P0：空间隔离，企业场景注入统一密钥）。
#: 值：64 位十六进制（32 字节），如 ``TTS_WATERMARK_KEY=<64 hex>``。
_WATERMARK_KEY_ENV = "TTS_WATERMARK_KEY"

#: 水印来源标识符常量（代码常量，不可通过配置修改，防止篡改溯源）。
#: 所有通过 TTS_MultiModel 生成的音频均嵌入此标识，用于内容来源追溯。
#: 此常量定义在 watermark 模块中，供 generation / generic_tts_engine / streaming 等写盘点统一引用。
WATERMARK_SOURCE_ID: str = "tts-multimodel"

#: v2 载荷 source_id 枚举映射（枚举码 1-255）。
#: 检测端未知枚举码解析为 "unknown"；嵌入端未知 source_id 使用通用码 255。
_SOURCE_ID_TO_CODE: dict[str, int] = {WATERMARK_SOURCE_ID: 1}
_SOURCE_CODE_TO_ID: dict[int, str] = {code: sid for sid, code in _SOURCE_ID_TO_CODE.items()}
_SOURCE_CODE_UNKNOWN = 255

# 水印参数
_WATERMARK_VERSION = 1
_WATERMARK_VERSION_V2 = 2  # 紧凑 8 字节载荷版本（64 bit 容量完全利用）
_WATERMARK_VERSION_V3 = 3  # P1-4a：HMAC 秘密密钥版（载荷同 v2，载波相位由密钥派生）
_WATERMARK_BITS = 64  # 水印载荷的比特数
_WATERMARK_STRENGTH = 0.062  # 嵌入强度（水印信号幅度）
_WATERMARK_FREQ_LOW = 16000  # 嵌入频率下限（Hz）
_WATERMARK_FREQ_HIGH = 20000  # 嵌入频率上限（Hz）
_WATERMARK_FRAME_SIZE = 2048  # FFT 帧大小
_WATERMARK_REPEAT = 4  # 水印重复次数以增强鲁棒性

#: 水印秘密密钥路径（P1-4a）。32 字节随机密钥，0600 权限，首次运行自动生成。
#: 用于 HMAC 派生 v3 载波相位，防止无密钥者伪造或检测水印。
_WATERMARK_KEY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", ".watermark_key"
)
_WATERMARK_KEY_MSG = b"tts-multimodel-watermark-v3-carrier"

# 模块级缓存：避免每次嵌入/检测都重读密钥文件
_watermark_secret_cache: bytes | None = None


def _get_watermark_secret() -> bytes | None:
    """加载或自动生成水印秘密密钥（P1-4a）。

    密钥存于 data/.watermark_key，32 字节，0600 权限。
    首次调用时若文件不存在则自动生成。读取失败返回 None（回退 v2 无密钥版）。
    桌面分发与安全加固 P0 变更：
      - 环境变量 ``TTS_WATERMARK_KEY`` 优先（企业空间隔离：统一密钥注入，每机无需各自生成）；
      - 读取密钥文件时调用 ``harden_secret_file_permissions`` 自愈过宽权限（读时硬化）。

    Returns:
        32 字节密钥；密钥文件不可读/不可写时返回 None。
    """
    global _watermark_secret_cache
    if _watermark_secret_cache is not None:
        return _watermark_secret_cache

    # P0：环境变量注入优先（企业统一密钥；hex 64 字符 = 32 字节）
    env_key = os.environ.get(_WATERMARK_KEY_ENV, "").strip()
    if env_key:
        try:
            key = bytes.fromhex(env_key)
            if len(key) != 32:
                logger.warning(f"TTS_WATERMARK_KEY 长度异常（{len(key)}B，期望 32B），忽略环境变量")
            else:
                _watermark_secret_cache = key
                return key
        except ValueError:
            logger.warning("TTS_WATERMARK_KEY 非合法 hex，忽略环境变量")

    try:
        key_dir = os.path.dirname(_WATERMARK_KEY_PATH)
        os.makedirs(key_dir, exist_ok=True)

        if os.path.exists(_WATERMARK_KEY_PATH):
            with open(_WATERMARK_KEY_PATH, "rb") as f:
                key = f.read().strip()
            if len(key) != 32:
                logger.warning(f"水印密钥长度异常 ({len(key)}B)，重新生成")
                key = secrets.token_bytes(32)
                with open(_WATERMARK_KEY_PATH, "wb") as f:
                    f.write(key)
            else:
                # P0：读时权限自愈（历史部署可能权限过宽）
                with contextlib.suppress(ImportError):
                    from .security.secret_key import harden_secret_file_permissions

                    harden_secret_file_permissions(_WATERMARK_KEY_PATH)
        else:
            key = secrets.token_bytes(32)
            with open(_WATERMARK_KEY_PATH, "wb") as f:
                f.write(key)
            with contextlib.suppress(OSError):
                os.chmod(_WATERMARK_KEY_PATH, 0o600)
            with contextlib.suppress(ImportError):
                from .security.secret_key import harden_secret_file_permissions

                harden_secret_file_permissions(_WATERMARK_KEY_PATH)

        _watermark_secret_cache = key
        return key
    except OSError as e:
        logger.debug(f"水印密钥加载失败，回退无密钥版: {e}")
        return None


#: v2 载波相位表（对齐零相位，crest 因子最低）与 v1 兼容表（旧 RandomState(42) 均匀相位）。
#: 嵌入端使用 v2 对齐相位表；检测端多假设检测同时尝试两表，旧格式样本 presence 仍可检出。
def _carrier_phases_v2(n_bins):
    return np.zeros(n_bins, dtype=np.float64)


def _carrier_phases_v1(n_bins):
    return np.random.RandomState(42).uniform(0, 2 * np.pi, size=n_bins)


def _carrier_phases_v3(n_bins: int) -> np.ndarray | None:
    """v3 密钥版载波相位（P1-4a）：由 HMAC-SHA256(secret, fixed_msg) 派生。

    固定消息使检测端无需知道嵌入时间戳即可重建相位（单遍检测）。
    密钥缺失时回退 v2 零相位。相位均匀分布 [0, 2π)，与 v1 兼容表同分布
    但种子由秘密密钥控制——无密钥者无法重建载波，因而无法检测或伪造水印。

    Args:
        n_bins: 相位数量（等于有效水印比特数）。

    Returns:
        相位数组；密钥不可用时返回 None（调用方应回退 v2）。
    """
    secret = _get_watermark_secret()
    if secret is None:
        return None
    digest = hmac.new(secret, _WATERMARK_KEY_MSG, hashlib.sha256).digest()
    seed = int.from_bytes(digest[:8], "big") % (2**32)
    return np.random.RandomState(seed).uniform(0, 2 * np.pi, size=n_bins)


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

    编码格式（v1 旧格式，仅保留用于旧文件兼容验证；当前嵌入使用 v2 紧凑格式）：
    version(1B) + source_id_len(1B) + source_id + timestamp(8B) + content_hash(8B)

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


def _build_payload_v2(
    source_id: str, timestamp: float, content_hash: str, version: int = _WATERMARK_VERSION_V2
) -> bytes:
    """构建 v2/v3 紧凑载荷（8 字节 = 64 bit，完全利用嵌入容量）。

    格式：
      byte0:    version（2=公开零相位版，3=HMAC 密钥版）
      byte1:    source_id 枚举码（1-255，见 _SOURCE_ID_TO_CODE；未知 source 用 255）
      byte2-5:  timestamp 秒级 uint32（大端，覆盖至 2106 年）
      byte6-7:  content_hash 前 2 字节（sha256 前 16 bit hex）

    Args:
        source_id: 来源标识符。
        timestamp: Unix 时间戳。
        content_hash: 内容哈希（16 位 hex 字符串，取前 4 字符）。
        version: 载荷版本号（默认 v2；v3 表示 HMAC 密钥版）。

    Returns:
        8 字节紧凑载荷。
    """
    code = _SOURCE_ID_TO_CODE.get(source_id)
    if code is None:
        logger.warning(f"source_id '{source_id}' 不在 v2 枚举表，使用通用码 {_SOURCE_CODE_UNKNOWN}")
        code = _SOURCE_CODE_UNKNOWN
    ts_sec = int(timestamp) & 0xFFFFFFFF
    hash_hex = (content_hash or "")[:4]
    if len(hash_hex) < 4:
        hash_hex = (hash_hex + "0000")[:4]
    hash_bytes = bytes.fromhex(hash_hex)
    return struct.pack(">BBI2s", version, code, ts_sec, hash_bytes)


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
        strength: 嵌入强度（0.001-0.062，默认 0.062）。
        timestamp: Unix 时间戳（默认使用当前时间）。

    Returns:
        (含水印的音频, WatermarkResult) 元组。
    """
    if timestamp is None:
        timestamp = time.time()

    audio_mono = np.mean(audio, axis=-1) if audio.ndim > 1 else audio.copy()

    audio_mono = audio_mono.astype(np.float32)

    content_hash = _compute_content_hash(audio_mono, sample_rate)

    # v2/v3 紧凑载荷：8 字节完整进入 64 bit 信号（version/source/timestamp/hash 全部可恢复）
    # P1-4a：密钥可用时嵌入 v3（HMAC 密钥版载波相位），否则回退 v2（公开零相位）。
    use_v3 = _get_watermark_secret() is not None
    payload_version = _WATERMARK_VERSION_V3 if use_v3 else _WATERMARK_VERSION_V2
    payload_bytes = _build_payload_v2(source_id, timestamp, content_hash, version=payload_version)
    payload_bits = _payload_bytes_to_bits(payload_bytes)

    n_samples = len(audio_mono)

    watermark_signal = np.zeros(n_samples, dtype=np.float32)

    freq_low_bin = int(_WATERMARK_FREQ_LOW * _WATERMARK_FRAME_SIZE / sample_rate)
    freq_high_bin = int(_WATERMARK_FREQ_HIGH * _WATERMARK_FRAME_SIZE / sample_rate)
    freq_high_bin = min(freq_high_bin, _WATERMARK_FRAME_SIZE // 2)

    # 低采样率（<=32kHz）时 16-20kHz 频带超出 Nyquist，频点可能不足甚至为负
    n_freq_bins = max(0, freq_high_bin - freq_low_bin)
    effective_bits = min(_WATERMARK_BITS, n_freq_bins)
    if effective_bits <= 0:
        logger.debug(f"采样率 {sample_rate}Hz 无法容纳水印频点（16-20kHz 超出 Nyquist），跳过水印嵌入")
        return audio.astype(np.float32), WatermarkResult(
            success=False,
            message=f"采样率 {sample_rate}Hz 过低，无法嵌入水印",
            payload=None,
        )

    frame_size = _WATERMARK_FRAME_SIZE
    hop_size = frame_size // 2
    n_frames = max(1, (n_samples - frame_size) // hop_size + 1)

    # P1-4a：v3 密钥版优先（HMAC 派生相位），不可用时回退 v2 零相位（crest 最低）
    carrier_phases = _carrier_phases_v3(effective_bits)
    if carrier_phases is None:
        carrier_phases = _carrier_phases_v2(effective_bits)

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
                    # BPSK 双极性调制：bit=1 -> +strength，bit=0 -> -strength
                    # 修复回归（2026-08-16）：_payload_bytes_to_bits 返回 ±1 双极性数组，
                    # `if payload_bits[bit_idx]` 对 +1/-1 恒为真 → 所有比特恒按 +1 嵌入，
                    # 检测端信息论上不可恢复 payload（旧文件解出 version=255 全 +1 指纹）。
                    # 判定应改为 `> 0`（等价于恢复原实现 `payload_bits[bit_idx] * strength`）。
                    bit_value = 1.0 if payload_bits[bit_idx] > 0 else -1.0
                    modulation = bit_value * strength * np.exp(1j * carrier_phases[bit_idx])
                    # 奇数 bin 逐帧相位翻转（2026-08-16，噪声 payload 修复）：
                    # hop=N/2 使相邻帧在 bin fb 的相位差为 π·fb，fb 为奇数时相邻帧
                    # 贡献相位相反完全抵消（水印信号仅存首/尾帧）；对奇数 bin 在
                    # 奇数帧取反后，跨帧贡献由相位相消变为相位相干（等效 +10~13dB）。
                    # 检测端同步按 (-1)^frame_idx 解除翻转（见 detect_watermark）。
                    if freq_bin % 2 == 1 and frame_idx % 2 == 1:
                        modulation = -modulation
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
        version=payload_version,
        source_id=source_id,
        timestamp=timestamp,
        content_hash=content_hash,
    )

    logger.debug(f"水印嵌入完成: source={source_id}, SNR={snr_db:.1f}dB, hash={content_hash}")

    return watermarked.astype(np.float32), WatermarkResult(
        success=True,
        message="水印嵌入成功",
        payload=payload,
        snr_db=snr_db,
    )


# ============================================================================
# 水印检测
# ============================================================================


def _parse_payload_v2(bit_bytes: bytes) -> WatermarkPayload:
    """按 v2 紧凑格式解析载荷（8 字节）。

    格式：version(1B)=2 + source枚举码(1B) + timestamp秒uint32(4B) + hash前2字节(2B)
    """
    if len(bit_bytes) < 8:
        raise ValueError(f"v2 载荷字节不足: {len(bit_bytes)}")
    version = bit_bytes[0]
    if version not in (_WATERMARK_VERSION_V2, _WATERMARK_VERSION_V3):
        raise ValueError(f"v2/v3 版本不合法: {version}")
    code = bit_bytes[1]
    if not 1 <= code <= 255:
        raise ValueError(f"source 枚举码不合法: {code}")
    (ts_sec,) = struct.unpack(">I", bytes(bit_bytes[2:6]))
    hash_hex = bytes(bit_bytes[6:8]).hex()
    source_id = _SOURCE_CODE_TO_ID.get(code, "unknown")
    return WatermarkPayload(
        version=version,
        source_id=source_id,
        timestamp=float(ts_sec),
        content_hash=hash_hex,
    )


def _parse_payload_v1(bit_bytes: bytes) -> WatermarkPayload:
    """按 v1 旧格式解析载荷（64-bit 截断语义，保持旧文件兼容）。

    v1 完整载荷为 version + source_len + source + timestamp(8B) + hash(8B)，
    但嵌入容量仅 64 bit = 8 字节（version + source_len + source 前 6 字节），
    timestamp/content_hash 从未进入信号 → 置默认值。
    """
    if len(bit_bytes) < 2:
        raise ValueError("载荷字节不足")
    version = bit_bytes[0]
    source_len = bit_bytes[1]
    if version != _WATERMARK_VERSION:
        raise ValueError(f"水印版本不合法: {version}")
    if not 1 <= source_len <= 32:
        raise ValueError(f"source_id 长度不合法: {source_len}")
    avail = len(bit_bytes) - 2
    source = bytes(bit_bytes[2 : 2 + min(source_len, avail)]).decode("utf-8")
    return WatermarkPayload(
        version=version,
        source_id=source,
        timestamp=0.0,
        content_hash="",
    )


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
    n_freq_bins = max(0, freq_high_bin - freq_low_bin)
    effective_bits = min(_WATERMARK_BITS, n_freq_bins)
    if effective_bits <= 0:
        return WatermarkResult(
            success=False,
            message=f"采样率 {sample_rate}Hz 过低，无法检测水印",
            payload=None,
        )

    best_score = 0.0
    best_bits = np.zeros(effective_bits)

    current_time = time.time()
    candidates = [current_time]

    # --- 检测端配套修复（2026-08-16）---
    # 1) 内容频谱泄漏抑制：以水印频带外相邻 bin（< freq_low_bin、> freq_high_bin）
    #    为参考，沿频域线性外推估计各水印 bin 处的内容频谱并扣除后再相关。
    #    依据（实测）：440Hz 正弦在 16-20kHz 的泄漏在载波方向投影 std≈0.36/帧，
    #    大于单帧水印信号（偶数 bin ~0.30、奇数 bin 边缘帧 ~0.05-0.10），不抑制无法可靠判定。
    n_fft = frame_size // 2 + 1
    low_refs = [b for b in range(freq_low_bin - 4, freq_low_bin) if b >= 0]
    high_refs = [b for b in range(freq_high_bin + 1, freq_high_bin + 5) if b < n_fft]
    use_content_suppression = bool(low_refs) and bool(high_refs)
    ref_lo_center = float(np.mean(low_refs)) if low_refs else 0.0
    ref_hi_center = float(np.mean(high_refs)) if high_refs else 0.0

    # 2) 重叠相加结构感知解翻转+加权（2026-08-16，噪声 payload 修复）：
    #    hop=N/2 时相邻帧在 bin fb 的相位差为 π·fb。嵌入端已对奇数 bin 做逐帧
    #    相位翻转（frame_idx 为奇数时调制取反），使奇数 bin 跨帧相干累积；
    #    检测端对奇数 bin 相关值同步乘 (-1)^frame_idx 解除翻转后再累加。
    #    边帧（仅与 1 个邻帧重叠）信号 1.5×、内帧（与 2 个邻帧重叠）2×，
    #    全部 bin 统一权重（修复前奇数 bin 内帧权重为 0，仅取边帧）。
    bit_bins = freq_low_bin + np.arange(effective_bits)
    odd_bin_mask = bit_bins % 2 == 1

    # 多假设检测（2026-08-16 + P1-4a）：
    # v3=HMAC 密钥版（优先，仅密钥持有者可检测）；v2=对齐零相位表（crest 最低）；
    # v1=RandomState(42) 均匀相位表（旧格式兼容）。三表同时尝试、取 presence 分最高者。
    phase_tables = []
    v3_phases = _carrier_phases_v3(effective_bits)
    if v3_phases is not None:
        phase_tables.append(v3_phases)
    phase_tables.append(_carrier_phases_v2(effective_bits))
    phase_tables.append(_carrier_phases_v1(effective_bits))
    for carrier_phases in phase_tables:
        carriers = np.exp(1j * carrier_phases)

        for _timestamp in candidates:
            correlations = np.zeros(effective_bits)
            whitened_correlations = np.zeros(effective_bits)
            counts = np.zeros(effective_bits)

            for frame_idx in range(n_frames):
                start = frame_idx * hop_size
                end = min(start + frame_size, n_samples)
                if end - start < frame_size:
                    break

                frame = audio_mono[start:end]
                fft = np.fft.rfft(frame)

                bin_vals = fft[bit_bins]
                if use_content_suppression:
                    ref_lo = np.mean(fft[low_refs])
                    ref_hi = np.mean(fft[high_refs])
                    t = (bit_bins - ref_lo_center) / (ref_hi_center - ref_lo_center)
                    bin_vals = bin_vals - (ref_lo + (ref_hi - ref_lo) * t)

                # 奇数 bin 解翻转：frame_idx 为奇数时相关值取反，
                # 与嵌入端 (-1)^frame_idx 相位翻转同步；偶数 bin 不变。
                flip_sign = np.where(odd_bin_mask, -1.0, 1.0) if frame_idx % 2 == 1 else 1.0

                # 原始相关：presence 判分用（保持能量量纲，阈值 0.001 语义不变）
                corr = np.real(bin_vals * np.conj(carriers)) * flip_sign

                # 频域白化（噪声鲁棒性增强，2026-08-16）：按帧内频带幅度中位数归一化后
                # 再与载波相关，抑制噪声/突发频谱帧间能量方差对判决的支配；
                # 白化相关仅用于比特判决，不参与 presence 判分。
                med = np.median(np.abs(bin_vals))
                corr_white = np.real(bin_vals / med * np.conj(carriers)) * flip_sign if med > 0 else corr

                is_edge = frame_idx == 0 or frame_idx == n_frames - 1
                weights = np.full(effective_bits, 1.5 if is_edge else 2.0)
                correlations += corr * weights
                whitened_correlations += corr_white * weights
                counts += weights

            valid = counts > 0
            if np.any(valid):
                avg_correlations = np.where(valid, correlations / counts, 0)
                avg_whitened = np.where(valid, whitened_correlations / counts, 0)
                detected_bits = np.sign(avg_whitened)
                detected_bits = np.where(detected_bits == 0, 1.0, detected_bits)
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
        if len(bit_bytes) < 2:
            raise ValueError("载荷字节不足")
        version = bit_bytes[0]
        if version in (_WATERMARK_VERSION_V2, _WATERMARK_VERSION_V3):
            # v2/v3 紧凑格式：8 字节完整载荷（source 枚举 / timestamp 秒 / hash 2 字节）
            # v3 与 v2 载荷格式相同，区别仅在载波相位由 HMAC 密钥派生（P1-4a）
            payload = _parse_payload_v2(bytes(bit_bytes))
        elif version == _WATERMARK_VERSION:
            # v1 旧格式：64-bit 截断语义（timestamp/content_hash 置默认值）
            payload = _parse_payload_v1(bytes(bit_bytes))
        else:
            raise ValueError(f"水印版本不合法: {version}")

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
        snr_db=best_score,
    )


# ============================================================================
# 便捷函数
# ============================================================================


def prepend_ai_indicator_tone(
    audio: np.ndarray,
    sample_rate: int,
    duration_ms: int = 200,
    freq: float = 880.0,
) -> np.ndarray:
    """在音频开头叠加短提示音，用于显式标识 AI 生成内容（P1-4b，可选）。

    生成一个 880Hz 正弦波短音，带 5ms 淡入淡出避免爆音，叠加到音频开头
    （不替换原始音频，仅在开头 N 毫秒叠加）。

    Args:
        audio: 输入音频数组（float32，单声道或立体声）。
        sample_rate: 采样率（Hz）。
        duration_ms: 提示音时长（毫秒），默认 200ms。
        freq: 提示音频率（Hz），默认 880Hz（A5）。

    Returns:
        叠加了提示音的音频数组（形状与输入一致）。
    """
    n_tone = int(sample_rate * duration_ms / 1000)
    if n_tone <= 0 or len(audio) == 0:
        return audio

    t = np.arange(n_tone, dtype=np.float64) / sample_rate
    tone = 0.15 * np.sin(2 * np.pi * freq * t).astype(np.float32)

    # 5ms 淡入淡出避免爆音
    fade = int(min(5e-3 * sample_rate, n_tone // 4))
    if fade > 0:
        tone[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
        tone[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)

    result = audio.copy()
    overlap = min(n_tone, len(result))
    if result.ndim > 1:
        for ch in range(result.shape[-1]):
            result[:overlap, ch] += tone[:overlap]
    else:
        result[:overlap] += tone[:overlap]

    # 防止削波
    np.clip(result, -1.0, 1.0, out=result)
    return result


def _resolve_failure_mode(failure_mode: str | None) -> str:
    """解析水印失败策略：参数 > config watermark.failure_mode > 默认 provenance。

    Args:
        failure_mode: 显式指定（"provenance" / "block"）；None 时读配置。

    Returns:
        "provenance" 或 "block"（非法值回退 provenance 并告警）。
    """
    if failure_mode in ("provenance", "block"):
        return failure_mode
    try:
        from .config import get_config

        wm_cfg = get_config().pydantic_config.watermark
        mode = getattr(wm_cfg, "failure_mode", "provenance")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"水印失败策略配置读取失败，使用默认 provenance: {e}")
        mode = "provenance"
    if mode not in ("provenance", "block"):
        logger.warning(f"非法 watermark.failure_mode={mode!r}，回退 provenance")
        mode = "provenance"
    return mode


def _write_provenance_sidecar(output_path: str | None, detail: dict[str, Any]) -> None:
    """把水印失败事实写成侧车元数据文件（<输出>.provenance.json）。

    侧车与产出同目录同基名，供事后溯源审计：此文件存在即表示该产出
    未经可溯源水印嵌入（默认 provenance 档的行为）。

    Args:
        output_path: 产出音频路径；None 时跳过写盘（仅记录日志）。
        detail: 侧车内容（source_id/version/原因/时间等）。
    """
    if not output_path:
        logger.warning("[WATERMARK] 无产出路径，跳过 .provenance.json 侧车写盘")
        return
    sidecar = f"{output_path}.provenance.json"
    try:
        parent = os.path.dirname(os.path.abspath(sidecar))
        os.makedirs(parent, exist_ok=True)
        payload = {
            "schema": "tts-multimodel.provenance.v1",
            "audio_file": os.path.basename(output_path),
            "watermark": {"embedded": False, **detail},
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.warning("[WATERMARK] 已写入溯源侧车（未嵌入水印）: %s", sidecar)
    except OSError as e:
        logger.error(f"[WATERMARK] 溯源侧车写入失败: {e}")


def _log_watermark_failure(detail: str, severity: str, outcome: str) -> None:
    """记录水印失败审计事件（桌面分发与安全加固 P0；模块级懒加载避免循环导入）。"""
    try:
        from .security.audit import log_audit

        log_audit(
            action="watermark_embed_failure",
            actor="system",
            detail=detail,
            severity=severity,
            outcome=outcome,
        )
    except Exception as e:  # noqa: BLE001 — 审计失败不阻断水印流程
        logger.debug(f"[WATERMARK] 审计事件写入失败: {e}")


def watermark_audio(
    audio: np.ndarray,
    sample_rate: int,
    enable: bool = True,
    source_id: str = "tts-multimodel",
    output_path: str | None = None,
    failure_mode: str | None = None,
) -> tuple[np.ndarray, dict]:
    """可选地为音频添加水印的便捷函数（桌面分发与安全加固 P0：3 级失败策略）。

    失败策略（config watermark.failure_mode，默认 provenance）：
      1. 重试 1 次：embed 失败（success=False 或抛异常）时重试一次；
      2. provenance（默认）：重试仍失败 → 写 ``<输出>.provenance.json`` 侧车 +
         返回原始音频（fail-open 但留痕）；
      3. block：重试仍失败 → 抛 ``WatermarkEmbedError``，产出被阻断（fail-closed）。
     任一失败路径均记审计事件（action=watermark_embed_failure）。

    Args:
        audio: 输入音频数组。
        sample_rate: 采样率（Hz）。
        enable: 是否启用水印，默认 True。
        source_id: 水印来源标识符。
        output_path: 产出音频路径（provenance 档写侧车用；None 时跳过侧车写盘）。
        failure_mode: 失败策略覆盖（"provenance"/"block"）；None 读 config。

    Returns:
        (处理后的音频, 元数据字典) 元组。元数据包含 watermarked（是否嵌入）、
        snr_db（信噪比）、source_id、content_hash 等字段；失败路径额外带
        watermark_failed / failure_strategy / failure_reason。

    Raises:
        WatermarkEmbedError: failure_mode=block 且重试后仍失败时抛出。
    """
    if not enable:
        return audio, {"watermarked": False}

    # P3：从 config 读取水印强度（修复死配置，原硬编码 0.062）
    strength = _WATERMARK_STRENGTH
    try:
        from .config import get_config

        # watermark 已在 config_models.WatermarkConfig 建模并挂到 AppConfig（P2-1 整改）
        wm_cfg = get_config().pydantic_config.watermark
        cfg_strength = getattr(wm_cfg, "strength", None)
        if cfg_strength is not None and isinstance(cfg_strength, (int, float)) and cfg_strength > 0:
            strength = float(cfg_strength)
    except Exception as e:
        logger.debug(f"水印强度配置读取失败，使用默认 {_WATERMARK_STRENGTH}: {e}")

    mode = _resolve_failure_mode(failure_mode)

    # P0：3 级失败策略——首次嵌入 + 重试 1 次
    last_error: str | None = None
    result: Any = None
    for _attempt in (1, 2):
        try:
            watermarked, result = embed_watermark(audio, sample_rate, source_id=source_id, strength=strength)
            if result.success:
                break
            last_error = result.message
        except Exception as e:  # noqa: BLE001 — embed 异常按失败处理（重试/降级）
            last_error = f"{type(e).__name__}: {e}"
            result = None

    if result is not None and result.success:
        metadata: dict[str, Any] = {
            "watermarked": True,
            "snr_db": round(result.snr_db, 1),
        }
        if result.payload:
            metadata["source_id"] = result.payload.source_id
            metadata["content_hash"] = result.payload.content_hash

        # P1-4b：可选 AI 标识提示音（默认关闭，由 config.security.ai_audio_prefix_enabled 控制）
        try:
            from .config import get_config

            sec_cfg = get_config().pydantic_config.security
            if getattr(sec_cfg, "ai_audio_prefix_enabled", False):
                prefix_ms = getattr(sec_cfg, "ai_audio_prefix_ms", 200)
                watermarked = prepend_ai_indicator_tone(watermarked, sample_rate, duration_ms=prefix_ms)
                metadata["ai_prefix"] = True
        except Exception as e:
            logger.debug(f"AI 标识提示音跳过（配置读取失败）: {e}")

        return watermarked, metadata

    # ---- 重试后仍失败：按策略降级/阻断 ----
    reason = last_error or "未知水印嵌入失败"
    detail = {
        "source_id": source_id,
        "sample_rate": sample_rate,
        "reason": reason,
        "strategy": mode,
    }
    if mode == "block":
        _log_watermark_failure(
            f"水印嵌入失败且 failure_mode=block，阻断产出: {reason}", severity="critical", outcome="blocked"
        )
        raise WatermarkEmbedError(f"水印嵌入失败（重试 1 次后仍失败），failure_mode=block 阻断产出: {reason}")

    _log_watermark_failure(
        f"水印嵌入失败，已写 .provenance.json 侧车（fail-open）: {reason}", severity="warning", outcome="failure"
    )
    _write_provenance_sidecar(output_path, detail)
    return audio, {
        "watermarked": False,
        "watermark_failed": True,
        "failure_strategy": "provenance",
        "failure_reason": reason,
    }
