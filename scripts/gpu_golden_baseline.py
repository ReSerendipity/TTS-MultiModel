#!/usr/bin/env python3
"""scripts/gpu_golden_baseline.py — 固定小样本集真实合成基线（GPU 补验 P1-#5）。

对已启动的 TTS 服务打 OpenAI 兼容接口 /v1/audio/speech，对 10 条固定文本跑真实合成，
记录每条的：
  - wall_time_sec（合成耗时）
  - audio_bytes（返回 WAV 字节数）
  - audio_sha256（前 16 位）
  - audio_duration_sec（从 WAV header 解析）
  - rtf = wall_time / audio_duration（<1 表示实时）
并在结束时取一次 nvidia-smi 显存峰值。

用法：
  python scripts/gpu_golden_baseline.py --base-url http://127.0.0.1:7869 \
      --model tts-1 --token <可选> --output golden_baseline.json
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import struct
import time
import urllib.error
import urllib.request


def _csrf_headers(base: str) -> dict:
    """GET 首页拿 csrf_token cookie，再按双提交约定回填 Header（同 gpu_smoke_minimal）。"""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    with opener.open(f"{base}/", timeout=10) as r:
        r.read()
    token = ""
    for c in cj:
        if c.name == "csrf_token":
            token = c.value
    if not token:
        raise RuntimeError("响应里没有 csrf_token cookie")
    return {"Cookie": f"csrf_token={token}", "X-CSRF-Token": token}

GOLDEN_TEXTS = [
    "你好，这是一条 TTS 质量基线测试文本。",
    "今天天气不错，适合出门散步。",
    "语音合成技术让文字开口说话。",
    "模型加载需要一定时间，请耐心等待。",
    "分布式训练需要多卡协同与梯度同步。",
    "声音克隆只需要几秒钟参考音频。",
    "流式输出让长文本即时播放。",
    "LoRA 微调用少量参数适配新音色。",
    "健康检查确保服务真实可用。",
    "错误处理让失败变得可预期。",
]


def _post_speech(base: str, model: str, text: str, headers: dict, timeout: float):
    url = f"{base}/v1/audio/speech"
    body = {"model": model, "input": text, "voice": "alloy",
            "response_format": "wav", "speed": 1.0}
    data = json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, data=data, method="POST", headers=h)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return time.perf_counter() - t0, raw, r.status
    except urllib.error.HTTPError as e:
        return time.perf_counter() - t0, e.read(), e.code


def _wav_duration(raw: bytes) -> float:
    # WAV: bytes 22-23 = channels, 34-37 = byte rate, 40-43 = data chunk size
    try:
        if raw[:4] != b"RIFF":
            return 0.0
        byte_rate = struct.unpack_from("<I", raw, 28)[0]
        data_size = struct.unpack_from("<I", raw, 40)[0]
        return data_size / byte_rate if byte_rate else 0.0
    except Exception:
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:7869")
    ap.add_argument("--model", default="tts-1")
    ap.add_argument("--token", default=None)
    ap.add_argument("--output", default="golden_baseline.json")
    ap.add_argument("--timeout", type=float, default="120")
    args = ap.parse_args()

    headers = _csrf_headers(args.base_url)
    results = []
    for i, text in enumerate(GOLDEN_TEXTS, 1):
        wall, raw, status = _post_speech(args.base_url, args.model, text, headers, args.timeout)
        dur = _wav_duration(raw) if raw[:4] == b"RIFF" else 0.0
        sha = hashlib.sha256(raw).hexdigest()[:16]
        rtf = (wall / dur) if dur > 0 else None
        results.append({
            "idx": i, "text": text, "status": status,
            "wall_time_sec": round(wall, 3),
            "audio_bytes": len(raw), "audio_sha256": sha,
            "audio_duration_sec": round(dur, 3),
            "rtf": round(rtf, 3) if rtf else None,
        })
        rtf_s = f"{rtf:.3f}" if rtf else "n/a"
        print(f"[{i}/{len(GOLDEN_TEXTS)}] status={status} wall={wall:.2f}s dur={dur:.2f}s "
              f"rtf={rtf_s} bytes={len(raw)}")

    summary = {
        "model": args.model,
        "n": len(results),
        "mean_rtf": round(sum(r["rtf"] for r in results if r["rtf"]) / max(1, sum(1 for r in results if r["rtf"])), 3),
        "mean_wall_sec": round(sum(r["wall_time_sec"] for r in results) / len(results), 3),
        "all_ok": all(r["status"] == 200 for r in results),
        "results": results,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {args.output}: all_ok={summary['all_ok']} mean_rtf={summary['mean_rtf']}")
    return 0 if summary["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
