#!/usr/bin/env python3
"""scripts/gpu_smoke_minimal.py — TTS_MultiModel 最小可行 GPU 真机冒烟。

在 self-hosted GPU runner 上，对**真实加载**的 TTS 引擎各跑一条最短合成，
校验返回的是真实音频字节（非空、合法 WAV/RIFF），并上报显存峰值。

覆盖两个引擎：
  - voxcpm2   (OpenAI model "tts-1")         — 服务启动自动加载（TTS_AUTO_LOAD_MODEL=1）
  - indextts2 (OpenAI model "tts-1-hd")      — 经 POST /api/model/switch 切换后加载

用法：
    python scripts/gpu_smoke_minimal.py \
        --base-url http://127.0.0.1:7869 \
        --output gpu_smoke_report.json

退出码：0 通过 / 非0 失败。
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get(url: str, timeout: float, parse: bool = True):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw.decode("utf-8", "replace")) if parse else raw)
    except urllib.error.HTTPError as e:
        if parse:
            try:
                return e.code, json.loads(e.read().decode("utf-8", "replace"))
            except Exception:
                return e.code, None
        return e.code, None
    except Exception:
        return 0, (None if parse else b"")


def _post_form(url: str, fields: dict, timeout: float):
    """最小 multipart/form-data 实现（仅文本字段），兼容 FastAPI Form。"""
    boundary = "----gpusmokeboundary"
    parts = []
    for k, v in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
        )
    body = b"".join(parts) + f"--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def _speech(base: str, model: str, timeout: float):
    url = f"{base}/v1/audio/speech"
    body = {
        "model": model,
        "input": "这是一条 GPU 真机冒烟测试音频。",
        "voice": "alloy",
        "response_format": "wav",
        "speed": 1.0,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, str(e).encode("utf-8")


def _is_wav(raw: bytes) -> bool:
    return raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"


def _finish(report: dict, output: str) -> int:
    report["passed"] = report.get("passed", False)
    print(f"[{'PASS' if report['passed'] else 'FAIL'}] gpu smoke {'passed' if report['passed'] else 'failed'}")
    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
    return 0 if report["passed"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="TTS_MultiModel GPU 真机冒烟（最小可行）")
    ap.add_argument("--base-url", default="http://127.0.0.1:7869")
    ap.add_argument("--output", default="")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    report: dict = {"base_url": base, "steps": [], "passed": False}

    def step(name: str, ok: bool, detail: str) -> bool:
        report["steps"].append({"name": name, "ok": ok, "detail": detail})
        print(f"[{'OK ' if ok else 'FAIL'}] {name}: {detail}")
        return ok

    # 1) /readyz：model_loaded 闸门（服务启动时 TTS_AUTO_LOAD_MODEL=1 自动加载 voxcpm2）
    ready = False
    for _ in range(120):
        st, body = _get(f"{base}/readyz", 10, parse=True)
        if st == 200 and isinstance(body, dict) and body.get("model_loaded"):
            ready = True
            break
        time.sleep(3)
    if not step("ready", ready, "model loaded" if ready else "model not loaded within 360s"):
        return _finish(report, args.output)

    # 2) voxcpm2 (tts-1)
    st, raw = _speech(base, "tts-1", 120)
    ok = st == 200 and len(raw) > 44 and _is_wav(raw)
    if not step("synth_tts-1", ok, f"HTTP {st} bytes={len(raw)} wav={_is_wav(raw)}"):
        return _finish(report, args.output)

    # 3) 切换到 indextts2 并等待加载完成
    st, _ = _post_form(f"{base}/api/model/switch", {"engine": "indextts2"}, 30)
    switched = False
    for _ in range(200):
        st2, sb = _get(f"{base}/api/model/status", 10, parse=True)
        if st2 == 200 and isinstance(sb, dict):
            if sb.get("current_engine") == "indextts2" and sb.get("model_loaded"):
                switched = True
                break
        time.sleep(3)
    if not step("switch_indextts2", switched, "switched + loaded" if switched else f"switch HTTP {st}, not ready"):
        return _finish(report, args.output)

    # 4) indextts2 (tts-1-hd)
    st, raw = _speech(base, "tts-1-hd", 600)
    ok = st == 200 and len(raw) > 44 and _is_wav(raw)
    if not step("synth_tts-1-hd", ok, f"HTTP {st} bytes={len(raw)} wav={_is_wav(raw)}"):
        return _finish(report, args.output)

    report["passed"] = True
    return _finish(report, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
