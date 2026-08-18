"""验证 WAV 音频文件中的数字水印。

用法: python scripts/verify_watermark.py <wav 文件> [source_id]
退出码: 0=验证通过, 1=失败/未检测到。
"""
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import numpy as np
from integrated_app.watermark import detect_watermark


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/verify_watermark.py <wav> [source_id]")
        return 2

    wav_path = Path(sys.argv[1])
    source_id = sys.argv[2] if len(sys.argv) > 2 else "tts-multimodel"

    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(sampwidth)
    if dtype is None:
        print(f"[FAIL] 不支持的采样位宽: {sampwidth}")
        return 1
    data = np.frombuffer(raw, dtype=dtype)
    data = data.reshape(-1, n_channels) if n_channels > 1 else data
    audio = data.astype(np.float32) / np.iinfo(dtype).max

    result = detect_watermark(audio, sr, source_id=source_id)
    if result.success and result.payload:
        p = result.payload
        print(f"[OK] 检测到水印: source_id={p.source_id} content_hash={p.content_hash[:12]}...")
        print(f"     置信度: {result.confidence if hasattr(result, 'confidence') else 'n/a'}")
        return 0
    print(f"[FAIL] 未检测到水印: {result.message}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
