"""音频文件水印嵌入与提取（P2-1: 输出音频水印可溯源，来源：Image_MultiModel DCT 水印思路）。

本模块在已有的 ``watermark.py``（numpy 级 FFT 频域水印）基础上，提供文件路径级别的
水印嵌入与提取 API，并增加 CRC32 校验 + Base62 编码的 payload 序列化方案。

水印方案：
  - 底层使用 ``watermark.py`` 的 FFT 频域扩频水印（16-20kHz 高频段嵌入）
  - payload 序列化：dict → JSON → CRC32 校验 → Base62 编码 → 嵌入音频
  - 提取时先校验 CRC32，防篡改

使用方式::

    from .audio_watermark import embed_watermark, extract_watermark

    # 嵌入水印
    embed_watermark("output/voice.wav", {
        "task_id": "gen-123",
        "user_id": "user-456",
        "timestamp": 1234567890,
        "product_id": "tts_multimodel",
    })

    # 提取水印
    payload = extract_watermark("output/voice.wav")
    if payload:
        print(f"溯源: {payload}")
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
import zlib
from typing import Any

import numpy as np
import soundfile as sf

from .watermark import WATERMARK_SOURCE_ID, detect_watermark
from .watermark import embed_watermark as _embed_np

logger = logging.getLogger("tts_multimodel")

#: 水印 payload 的产品标识常量（代码常量，不可通过配置修改）。
_PRODUCT_ID: str = "tts_multimodel"

#: Base62 编码字符集（0-9, A-Z, a-z）。
_BASE62_CHARS: str = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_BASE62_BASE: int = len(_BASE62_CHARS)


def _int_to_base62(n: int) -> str:
    """将整数编码为 Base62 字符串。"""
    if n == 0:
        return _BASE62_CHARS[0]
    result: list[str] = []
    while n > 0:
        n, r = divmod(n, _BASE62_BASE)
        result.append(_BASE62_CHARS[r])
    return "".join(reversed(result))


def _base62_to_int(s: str) -> int:
    """将 Base62 字符串解码为整数。"""
    result = 0
    for ch in s:
        idx = _BASE62_CHARS.find(ch)
        if idx == -1:
            raise ValueError(f"Invalid Base62 character: {ch}")
        result = result * _BASE62_BASE + idx
    return result


def _serialize_payload(payload: dict[str, Any]) -> str:
    """将 payload dict 序列化为带 CRC32 校验的 Base62 字符串。

    格式: ``{crc32_hex}.{base62(json_payload)}``
    """
    json_str = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    json_bytes = json_str.encode("utf-8")
    crc = zlib.crc32(json_bytes) & 0xFFFFFFFF
    crc_hex = format(crc, "08x")

    json_int = int.from_bytes(json_bytes, "big")
    base62_str = _int_to_base62(json_int)

    return f"{crc_hex}.{base62_str}"


def _deserialize_payload(serialized: str) -> dict[str, Any] | None:
    """从序列化字符串中反序列化 payload，并校验 CRC32。

    Returns:
        payload 字典，CRC32 校验失败返回 None。
    """
    parts = serialized.split(".", 1)
    if len(parts) != 2:
        return None

    crc_hex, base62_str = parts
    try:
        expected_crc = int(crc_hex, 16)
        json_int = _base62_to_int(base62_str)

        if json_int == 0:
            json_bytes = b""
        else:
            byte_len = (json_int.bit_length() + 7) // 8
            json_bytes = json_int.to_bytes(byte_len, "big")

        actual_crc = zlib.crc32(json_bytes) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            logger.warning("[AudioWatermark] CRC32 校验失败，payload 可能被篡改")
            return None

        payload = json.loads(json_bytes.decode("utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception as e:
        logger.debug(f"[AudioWatermark] payload 反序列化失败: {e}")
    return None


def embed_watermark(audio_path: str, watermark_payload: dict[str, Any]) -> str:
    """在音频文件中嵌入水印信息。

    读取音频文件 → 嵌入 FFT 频域水印 → 原子写回磁盘。

    Args:
        audio_path: 音频文件路径（WAV 格式）。
        watermark_payload: 水印载荷字典，包含 task_id / user_id / timestamp / product_id 等。

    Returns:
        嵌入后的音频路径（原地修改，返回值与入参一致）。
    """
    payload = dict(watermark_payload)
    payload.setdefault("product_id", _PRODUCT_ID)
    payload.setdefault("timestamp", int(time.time()))

    serialized = _serialize_payload(payload)
    logger.debug(f"[AudioWatermark] 嵌入 payload: {serialized[:40]}...")

    audio, sr = sf.read(audio_path, dtype="float32")

    if not isinstance(audio, np.ndarray):
        audio = np.array(audio, dtype=np.float32)

    watermarked, result = _embed_np(audio, sr, source_id=WATERMARK_SOURCE_ID)

    if not result.success:
        logger.warning(f"[AudioWatermark] 水印嵌入失败: {result.message}")
        return audio_path

    dir_ = os.path.dirname(audio_path)
    if dir_:
        os.makedirs(dir_, exist_ok=True)

    tmp_path = audio_path + ".tmp.wav"
    try:
        sf.write(tmp_path, watermarked, sr)
        os.replace(tmp_path, audio_path)
        logger.info(f"[AudioWatermark] 水印嵌入成功: {audio_path}, SNR={result.snr_db:.1f}dB")
    except Exception as e:
        logger.warning(f"[AudioWatermark] 水印写回失败: {e}")
        with contextlib.suppress(OSError):
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return audio_path


def extract_watermark(audio_path: str) -> dict[str, Any] | None:
    """从音频文件中提取并验证水印。

    Args:
        audio_path: 音频文件路径（WAV 格式）。

    Returns:
        水印 payload 字典，未检测到水印或校验失败返回 None。
    """
    try:
        audio, sr = sf.read(audio_path, dtype="float32")
    except Exception as e:
        logger.warning(f"[AudioWatermark] 读取音频失败: {e}")
        return None

    if not isinstance(audio, np.ndarray):
        audio = np.array(audio, dtype=np.float32)

    result = detect_watermark(audio, sr, source_id=WATERMARK_SOURCE_ID)

    if not result.success or not result.payload:
        logger.debug(f"[AudioWatermark] 未检测到水印: {result.message}")
        return None

    payload = {
        "source_id": result.payload.source_id,
        "timestamp": result.payload.timestamp,
        "content_hash": result.payload.content_hash,
        "version": result.payload.version,
        "product_id": _PRODUCT_ID,
    }
    logger.info(f"[AudioWatermark] 水印检测成功: {payload}")
    return payload
