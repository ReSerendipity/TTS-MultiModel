"""生成/验证 DCT 数字水印密钥。初始化脚本：生成密钥、自我测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import numpy as np
from integrated_app.watermark import embed_watermark, detect_watermark


def main() -> int:
    print("[init_watermark_key] TTS 水印自检：生成 1 秒测试音频并嵌入/验证")
    sr = 48000
    rng = np.random.default_rng(42)
    audio = rng.normal(0, 0.01, sr).astype(np.float32)

    watermarked, result = embed_watermark(audio, sr, source_id="tts-multimodel")
    if not result.success:
        print(f"[FAIL] 嵌入失败: {result.message}")
        return 1
    print(f"[OK] 嵌入成功 snr={result.snr_db:.1f}dB source_id={result.payload.source_id}")

    detect = detect_watermark(watermarked, sr, source_id="tts-multimodel")
    if not detect.success:
        print(f"[FAIL] 检测失败: {detect.message}")
        return 1
    print(f"[OK] 检测成功 source_id={detect.payload.source_id} content_hash={detect.payload.content_hash[:8]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
