"""生成速度基准测试脚本。

对不同文本长度、不同说话人/引擎组合批量测量 TTS 生成速度，计算 RTF
（Real-Time Factor = 生成时间 / 音频时长）。RTF < 1 表示比实时快，
RTF > 1 表示比实时慢。

度量指标（每次生成）：
    text_length_chars  — 输入文本字符数
    generation_time_s  — 生成耗时（秒）
    audio_duration_s   — 输出音频时长（秒）
    rtf                — 实时率 = generation_time / audio_duration
    engine             — 使用的引擎
    persona            — 使用的说话人/音色

测试矩阵：
    文本长度：短（20字）、中（100字）、长（300字）
    说话人：默认音色、gf1、旁白（如有）

输出：
    perf/results/generation-benchmark_<timestamp>.json
    控制台打印 RTF 对比表

用法::
    python perf/generation-benchmark.py
    python perf/generation-benchmark.py --engine voxcpm2 --repeats 3
    python perf/generation-benchmark.py --host 127.0.0.1 --port 7869
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

_PERF_DIR = Path(__file__).resolve().parent
_RESULTS_DIR = _PERF_DIR / "results"

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 7869

# ── 测试文本（不同长度）──────────────────────────────────────
_TEST_TEXTS = {
    "short": "这是一段简短的测试文本。",
    "medium": (
        "人工智能语音合成技术正在飞速发展，从最初的规则合成到参数式合成，"
        "再到如今的神经端到端合成，语音质量已经达到了接近真人的水平。"
        "多模型架构让不同场景可以选择最合适的引擎。"
    ),
    "long": (
        "语音合成技术是人工智能领域的重要研究方向之一。"
        "早期的语音合成系统主要基于规则和拼接方法，音质较差且自然度低。"
        "随着深度学习技术的发展，基于神经网络的端到端语音合成模型"
        "如 Tacotron、FastSpeech、VITS 等相继提出，大幅提升了合成语音的自然度和表现力。"
        "近年来，零样本语音克隆技术使得仅需几秒钟参考音频即可复制说话人音色成为可能，"
        "极大地拓展了语音合成技术的应用场景。"
        "本项目集成了 VoxCPM2、IndexTTS 2.0 和 dots.tts 三个引擎，"
        "覆盖了语音设计、语音克隆、剧本配音等多种应用需求。"
    ),
}

# ── 测试说话人 ──────────────────────────────────────────────
_DEFAULT_PERSONAS = ["default", "gf1", "nangongwan"]


def _generate_voxcpm2(
    client: httpx.Client,
    text: str,
    persona: str,
    mode: str = "design",
) -> dict:
    """执行一次 VoxCPM2 生成请求。

    Args:
        client: httpx 客户端。
        text: 待合成文本。
        persona: 说话人名称（default 时留空）。
        mode: 生成模式 design/clone。

    Returns:
        包含耗时、状态码、响应体的字典。
    """
    endpoint = "/api/generate/voxcpm2/design" if mode == "design" else "/api/generate/voxcpm2/clone"
    data = {"text": text}
    if persona != "default":
        data["persona_name"] = persona

    t0 = time.perf_counter()
    try:
        resp = client.post(endpoint, data=data, timeout=600.0)
        elapsed = time.perf_counter() - t0
        return {
            "status_code": resp.status_code,
            "elapsed_s": round(elapsed, 3),
            "response_snippet": resp.text[:500] if resp.status_code != 200 else "",
        }
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return {
            "status_code": 0,
            "elapsed_s": round(elapsed, 3),
            "error": str(e),
        }


def _generate_indextts2(
    client: httpx.Client,
    text: str,
) -> dict:
    """执行一次 IndexTTS2 生成请求。"""
    data = {"text": text}
    t0 = time.perf_counter()
    try:
        resp = client.post("/api/generate/indextts2/synthesize", data=data, timeout=600.0)
        elapsed = time.perf_counter() - t0
        return {
            "status_code": resp.status_code,
            "elapsed_s": round(elapsed, 3),
            "response_snippet": resp.text[:500] if resp.status_code != 200 else "",
        }
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return {
            "status_code": 0,
            "elapsed_s": round(elapsed, 3),
            "error": str(e),
        }


def _estimate_audio_duration(text: str, chars_per_second: float = 4.5) -> float:
    """根据文本字符数估算音频时长（粗略）。

    中文 TTS 约 3.5-5.5 字/秒，取中值 4.5。
    用于在无法获取实际音频时长时提供估算 RTF。

    Args:
        text: 输入文本。
        chars_per_second: 每秒字符数（默认 4.5）。

    Returns:
        估算的音频时长（秒）。
    """
    # 只计算中文字符和英文单词
    char_count = len(text.replace(" ", "").replace("\n", ""))
    return char_count / chars_per_second


def run_benchmark(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    engine: str = "voxcpm2",
    repeats: int = 1,
    personas: list[str] | None = None,
) -> dict:
    """执行生成速度基准测试。

    Args:
        host: 目标主机。
        port: 目标端口。
        engine: 测试引擎（voxcpm2/indextts2）。
        repeats: 每组测试重复次数（取平均）。
        personas: 测试说话人列表。

    Returns:
        包含全部测试结果的字典。
    """
    if personas is None:
        personas = _DEFAULT_PERSONAS

    base_url = f"http://{host}:{port}"
    results: list[dict] = []

    print(f"[gen-bench] 引擎: {engine}，重复: {repeats}，说话人: {personas}")
    print(f"[gen-bench] 文本长度: {list(_TEST_TEXTS.keys())}")
    print()

    with httpx.Client(base_url=base_url) as client:
        # 健康检查
        try:
            health = client.get("/api/system/health/ping", timeout=5.0)
            if health.status_code != 200:
                return {
                    "error": f"服务器未就绪: HTTP {health.status_code}",
                    "results": [],
                }
        except Exception as e:
            return {"error": f"无法连接服务器: {e}", "results": []}

        for text_key, text in _TEST_TEXTS.items():
            for persona in personas:
                for rep in range(repeats):
                    print(f"  [{text_key:6s}] persona={persona:12s} rep={rep + 1}/{repeats}...", end=" ")

                    if engine == "voxcpm2":
                        r = _generate_voxcpm2(client, text, persona)
                    elif engine == "indextts2":
                        r = _generate_indextts2(client, text)
                    else:
                        r = {"error": f"未知引擎: {engine}"}

                    audio_dur = _estimate_audio_duration(text)
                    gen_time = r.get("elapsed_s", 0)
                    rtf = gen_time / audio_dur if audio_dur > 0 else 0

                    entry = {
                        "text_key": text_key,
                        "text_length_chars": len(text),
                        "persona": persona,
                        "engine": engine,
                        "repeat": rep + 1,
                        "generation_time_s": gen_time,
                        "audio_duration_estimated_s": round(audio_dur, 2),
                        "rtf_estimated": round(rtf, 3),
                        "status_code": r.get("status_code", 0),
                        "error": r.get("error", ""),
                    }
                    results.append(entry)

                    if entry["error"]:
                        print(f"FAIL ({entry['error'][:50]})")
                    else:
                        print(f"RTF={rtf:.2f} ({gen_time:.2f}s / {audio_dur:.1f}s)")

    # 汇总统计
    successful = [r for r in results if not r["error"]]
    summary: dict = {"total_requests": len(results), "successful": len(successful)}
    if successful:
        rtfs = [r["rtf_estimated"] for r in successful]
        times = [r["generation_time_s"] for r in successful]
        summary["rtf_avg"] = round(sum(rtfs) / len(rtfs), 3)
        summary["rtf_min"] = round(min(rtfs), 3)
        summary["rtf_max"] = round(max(rtfs), 3)
        summary["gen_time_avg_s"] = round(sum(times) / len(times), 3)

        # 按文本长度分组统计
        by_length: dict[str, list[float]] = {}
        for r in successful:
            by_length.setdefault(r["text_key"], []).append(r["rtf_estimated"])
        summary["rtf_by_length"] = {k: round(sum(v) / len(v), 3) for k, v in by_length.items()}

    return {
        "script": "generation-benchmark",
        "timestamp": datetime.now().isoformat(),
        "host": host,
        "port": port,
        "engine": engine,
        "repeats": repeats,
        "results": results,
        "summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="TTS MultiModel 生成速度基准")
    parser.add_argument("--host", default=_DEFAULT_HOST, help="目标主机")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT, help="目标端口")
    parser.add_argument("--engine", default="voxcpm2", choices=["voxcpm2", "indextts2"], help="测试引擎")
    parser.add_argument("--repeats", type=int, default=1, help="每组测试重复次数")
    parser.add_argument("--personas", default=None, help="逗号分隔的说话人列表")
    args = parser.parse_args()

    personas = args.personas.split(",") if args.personas else None

    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    result = run_benchmark(
        host=args.host,
        port=args.port,
        engine=args.engine,
        repeats=args.repeats,
        personas=personas,
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = _RESULTS_DIR / f"generation-benchmark_{ts}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    s = result.get("summary", {})
    print("\n[gen-bench] ═══ RTF 摘要 ═══")
    print(f"  请求总数:    {s.get('total_requests', 0)}")
    print(f"  成功数:      {s.get('successful', 0)}")
    print(f"  RTF 平均:    {s.get('rtf_avg', 0):.3f}")
    print(f"  RTF 范围:    [{s.get('rtf_min', 0):.3f}, {s.get('rtf_max', 0):.3f}]")
    if "rtf_by_length" in s:
        print("  按文本长度:")
        for k, v in s["rtf_by_length"].items():
            print(f"    {k:6s}: RTF={v:.3f}")
    print(f"\n[gen-bench] 结果已保存: {out_file}")


if __name__ == "__main__":
    main()
