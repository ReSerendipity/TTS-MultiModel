#!/usr/bin/env python3
"""流式生成首包延迟基线（UPSTREAM_SYNC A3 验收）。

为什么要单独一个脚本而不是复用 generation-benchmark.py：
    批量合成的"总耗时"和流式体验的"首包延迟"是两个正交指标。用户按下
    生成后多久听到第一个字，取决于分段策略 + streaming_prefix_len +
    VAE 流式解码，跟 RTF 没有直接关系。混在一个脚本里量，改动了分段逻辑
    也不会体现在报表上。

测什么（每轮）：
    ttfb_ms          — 请求发出到收到第一个 ``event: audio`` 的毫秒数
    first_segment_ms — 到收到第一个 audio 之前那段 progress/meta 的耗时
    total_ms         — 到 ``event: done``
    audio_duration_s — done 事件里回传的合成时长
    rtf              — total_ms/1000 / audio_duration_s
    clipping_chunks  — done.quality 里的削波块数（A3"无爆音"的客观判据）
    quality_issues   — done.quality.issues（断裂/极低音量的段号列表）

用法::
    python perf/streaming-baseline.py --host 127.0.0.1 --port 7869
    python perf/streaming-baseline.py --repeats 7 --chars 200
    python perf/streaming-baseline.py --prefix-len 2      # 和默认 4 对比首包差多少

输出：perf/results/streaming-baseline_<timestamp>.json
"""

from __future__ import annotations

import argparse
import base64
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

_RESULTS_DIR = Path(__file__).resolve().parent / "results"
# 实际挂载路径由 /openapi.json 核对：voxcpm2 的 generate 路由前缀是 /api/generate，
# 所以这里是 /api/generate/streaming_sse，**不带** voxcpm2 段。
_ENDPOINT = "/api/generate/streaming_sse"

# 一段固定语料：长度可控、无敏感词、含中英数字混排（贴近真实输入）
_SAMPLE_TEXT = (
    "欢迎使用本语音合成平台。今天是 2026 年 9 月 22 日，天气不错，"
    "适合把这段话读出来听听效果。系统会在稍后返回结果，请耐心等待一下。"
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="流式生成首包延迟基线")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7869)
    p.add_argument("--repeats", type=int, default=5, help="采样轮数（>=5 才有意义算 P95）")
    p.add_argument("--chars", type=int, default=200, help="正文字符数（重复语料拼到该长度）")
    p.add_argument("--prefix-len", type=int, default=None, help="streaming_prefix_len，留空用服务端默认")
    p.add_argument("--persona", default="", help="音色名，留空走默认音色")
    p.add_argument("--timeout", type=float, default=300.0)
    return p.parse_args()


def build_text(chars: int) -> str:
    if chars <= 0:
        raise ValueError("--chars 必须为正数")
    text = _SAMPLE_TEXT
    while len(text) < chars:
        text += _SAMPLE_TEXT
    return text[:chars]


def parse_sse_line(line: str) -> tuple[str, str]:
    """把一行 SSE 拆成 (event, data)；非 data 行返回空串。"""
    if line.startswith("event:"):
        return line[len("event:") :].strip(), ""
    if line.startswith("data:"):
        return "", line[len("data:") :].strip()
    return "", ""


def run_once(client: httpx.Client, base_url: str, args: argparse.Namespace, text: str) -> dict[str, Any]:
    """跑一轮流式请求，返回该轮指标。

    注意：分段长度**不是**请求参数——服务端 ``split_text_for_tts`` 自己决定
    （``_STREAMING_DEFAULT_SEGMENT_CHARS`` 是个从未被引用的死常量）。所以本脚本
    只能通过 ``--chars`` 改正文长度来间接影响段数。
    """
    form: dict[str, Any] = {
        "text": text,
        "instruction": "",
        "persona_name": args.persona,
        "cfg_value": "2.0",
        "inference_timesteps": "10",
        "denoise": "true",
    }
    if args.prefix_len is not None:
        form["streaming_prefix_len"] = str(args.prefix_len)

    headers = {"X-CSRF-Token": client.cookies.get("csrf_token", "")}
    start = time.perf_counter()
    ttfb_ms: float | None = None
    first_event_ms: float | None = None
    total_ms: float = 0.0
    done: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    audio_bytes = 0
    pending_event = ""
    error_msg = ""

    with client.stream("POST", base_url + _ENDPOINT, data=form, headers=headers, timeout=args.timeout) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            event, data = parse_sse_line(line)
            if event:
                pending_event = event
                if first_event_ms is None:
                    first_event_ms = (time.perf_counter() - start) * 1000.0
                continue
            if not data:
                continue
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            total_ms = elapsed_ms
            if pending_event == "meta":
                meta = json.loads(data)
            elif pending_event == "audio":
                if ttfb_ms is None:
                    ttfb_ms = elapsed_ms
                audio_bytes += len(base64.b64decode(data))
            elif pending_event == "done":
                done = json.loads(data)
            elif pending_event == "error":
                error_msg = data

    if ttfb_ms is None:
        raise RuntimeError(f"整轮没收到任何 audio 事件（error={error_msg or '无'}）")

    sample_rate = int(meta.get("sample_rate") or 48000)
    channels = int(meta.get("channels") or 1)
    sample_width = int(meta.get("bits") or 16) // 8
    audio_duration_s = audio_bytes / float(sample_rate * channels * sample_width)
    quality = done.get("quality") or {}
    if not audio_duration_s:
        raise RuntimeError("音频时长算出来是 0，分片解码可能没出数据")
    return {
        "ttfb_ms": round(ttfb_ms, 1),
        "first_event_ms": round(first_event_ms if first_event_ms is not None else ttfb_ms, 1),
        "total_ms": round(total_ms, 1),
        "done_event_present": bool(done),
        "audio_duration_s": round(audio_duration_s, 3),
        "rtf": round((total_ms / 1000.0) / audio_duration_s, 3),
        "sample_rate": sample_rate,
        "segments": meta.get("total_segments"),
        "clipping_chunks": quality.get("clipping_chunks"),
        "max_peak": quality.get("max_peak"),
        "avg_rms": quality.get("avg_rms"),
        "quality_issues": quality.get("issues") or [],
        "error": error_msg or None,
    }


def percentile(values: list[float], pct: float) -> float:
    """最近秩法百分位（样本量小，不插值，避免把噪声读成结论）。"""
    if not values:
        raise ValueError("空样本")
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * len(ordered) - 0.5))))
    return float(ordered[rank])


def main() -> int:
    args = parse_args()
    base_url = f"http://{args.host}:{args.port}"
    text = build_text(args.chars)

    with httpx.Client() as client:
        try:
            # /api/health 从未注册过，现役是 /api/system/health（写错只会得到 404 →
            # 被这里的"服务没起"分支误报成服务不可用）
            health = client.get(f"{base_url}/api/system/health", timeout=10.0)
            health.raise_for_status()
            # CSRF 是 Double-Submit：先 GET 首页拿服务端签发的 csrf_token cookie，
            # 再把同一个值放进 X-CSRF-Token 头。少了这一步，POST 一律 403。
            client.get(base_url + "/", timeout=20.0)
            if not client.cookies.get("csrf_token"):
                print("拿不到 csrf_token cookie，POST 会被 CSRF 中间件拒（403）")
                return 2
        except Exception as exc:  # noqa: BLE001 - 连接期任何失败都是"服务没起"
            print(f"服务不可用：{base_url}（{type(exc).__name__}: {exc}）")
            print("请先启动：python app/clean_launch.py")
            return 2

        runs: list[dict[str, Any]] = []
        for i in range(args.repeats):
            try:
                m = run_once(client, base_url, args, text)
            except Exception as exc:  # noqa: BLE001 - 单轮失败不该废掉整批
                print(f"  轮 {i + 1} 失败：{type(exc).__name__}: {exc}")
                runs.append({"error": f"{type(exc).__name__}: {exc}"})
                continue
            runs.append(m)
            print(
                f"  轮 {i + 1}: 首包 {m['ttfb_ms']:.0f}ms · 总 {m['total_ms']:.0f}ms · "
                f"音频 {m['audio_duration_s']:.2f}s · RTF {m['rtf']} · 削波块 {m['clipping_chunks']}"
            )

    ok = [r for r in runs if r.get("error") is None]
    if not ok:
        print("没有任何一轮成功，不写基线文件")
        return 1

    ttfb = [float(r["ttfb_ms"]) for r in ok]
    summary = {
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "endpoint": _ENDPOINT,
        "config": {
            "chars": args.chars,
            "repeats": args.repeats,
            "streaming_prefix_len": args.prefix_len,
            "persona": args.persona or "(default)",
        },
        "runs_ok": len(ok),
        "runs_failed": len(runs) - len(ok),
        "ttfb_ms": {
            "p50": round(percentile(ttfb, 50), 1),
            "p95": round(percentile(ttfb, 95), 1),
            "min": round(min(ttfb), 1),
            "max": round(max(ttfb), 1),
            "mean": round(statistics.fmean(ttfb), 1),
        },
        "audio_quality": {
            "clipping_chunks_total": sum(int(r.get("clipping_chunks") or 0) for r in ok),
            "segments_with_issues": sum(len(r.get("quality_issues") or []) for r in ok),
        },
        "runs": runs,
    }

    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = _RESULTS_DIR / f"streaming-baseline_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n首包 P50={summary['ttfb_ms']['p50']}ms  P95={summary['ttfb_ms']['p95']}ms")
    print(f"削波块合计={summary['audio_quality']['clipping_chunks_total']}")
    print(f"已写入 {out.relative_to(_RESULTS_DIR.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
