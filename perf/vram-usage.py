"""显存占用持续监控脚本。

在指定时间段内持续采样 GPU 显存使用情况，支持在采样过程中触发引擎切换，
以监控不同引擎加载/卸载时的显存变化趋势。结果以时间序列 JSON 输出，
可由 report_generator.py 绘制为折线图。

度量指标（每次采样）：
    timestamp_ms   — 采样时刻 Unix 毫秒时间戳
    vram_used_mb   — 已分配显存（MB）
    vram_total_mb  — 显存总量（MB）
    vram_percent   — 显存占用百分比（0-100）
    gpu_util_pct   — GPU 核心利用率（0-100）
    device_name    — GPU 设备名
    current_engine — 当前活跃引擎名称

输出：
    perf/results/vram-usage_<timestamp>.json
    控制台打印峰值/谷值摘要

用法::
    python perf/vram-usage.py --duration 60
    python perf/vram-usage.py --duration 120 --switch voxcpm2 --switch-at 30
    python perf/vram-usage.py --host 127.0.0.1 --port 7869
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
_DEFAULT_DURATION = 60.0
_DEFAULT_INTERVAL = 1.0


def _sample_gpu(client: httpx.Client) -> dict:
    """采样一次 GPU 状态。

    Returns:
        包含显存/利用率/设备名/当前引擎的字典。
        API 不可用时返回带 error 字段的字典。
    """
    sample: dict = {
        "timestamp_ms": int(time.time() * 1000),
    }
    try:
        resp = client.get("/api/system/gpu/status", timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            sample["vram_used_mb"] = data.get("vram_used_mb", 0.0)
            sample["vram_total_mb"] = data.get("vram_total_mb", 0.0)
            sample["vram_percent"] = data.get("vram_percent", 0.0)
            sample["gpu_util_pct"] = data.get("utilization_gpu_pct", 0.0)
            sample["device_name"] = data.get("device_name", "unknown")
        else:
            sample["error"] = f"HTTP {resp.status_code}"
    except Exception as e:
        sample["error"] = str(e)

    # 获取当前引擎
    try:
        resp2 = client.get("/api/model/status", timeout=5.0)
        if resp2.status_code == 200:
            status = resp2.json()
            sample["current_engine"] = status.get("current_engine", status.get("engine", "none"))
        else:
            sample["current_engine"] = "unknown"
    except Exception:
        sample["current_engine"] = "unknown"

    return sample


def monitor_vram(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    duration: float = _DEFAULT_DURATION,
    interval: float = _DEFAULT_INTERVAL,
    switch_engine: str | None = None,
    switch_at: float = 0.0,
) -> dict:
    """执行显存持续监控。

    Args:
        host: 目标主机。
        port: 目标端口。
        duration: 总监控时长（秒）。
        interval: 采样间隔（秒）。
        switch_engine: 可选，在指定时间点切换到此引擎。
        switch_at: 切换引擎的时间点（秒，从监控开始计算）。

    Returns:
        包含采样序列和摘要统计的字典。
    """
    base_url = f"http://{host}:{port}"
    samples: list[dict] = []
    events: list[dict] = []

    print(f"[vram-usage] 开始监控 {duration:.0f}s，间隔 {interval:.1f}s")
    if switch_engine and switch_at > 0:
        print(f"[vram-usage] 将在 {switch_at:.0f}s 时切换引擎到 {switch_engine}")

    start_time = time.perf_counter()
    deadline = start_time + duration

    with httpx.Client(base_url=base_url) as client:
        while time.perf_counter() < deadline:
            elapsed = time.perf_counter() - start_time

            # 引擎切换
            if switch_engine and elapsed >= switch_at and not any(e.get("action") == "switch" for e in events):
                print(f"[vram-usage] 切换引擎 → {switch_engine}")
                t0 = time.perf_counter()
                try:
                    resp = client.post(
                        "/api/model/switch",
                        data={"engine": switch_engine},
                        timeout=120.0,
                    )
                    switch_ms = (time.perf_counter() - t0) * 1000
                    events.append(
                        {
                            "timestamp_ms": int(time.time() * 1000),
                            "action": "switch",
                            "engine": switch_engine,
                            "status": resp.status_code,
                            "duration_ms": round(switch_ms, 1),
                        }
                    )
                    print(f"[vram-usage] 切换完成: {switch_ms:.0f}ms (HTTP {resp.status_code})")
                except Exception as e:
                    events.append(
                        {
                            "timestamp_ms": int(time.time() * 1000),
                            "action": "switch",
                            "engine": switch_engine,
                            "error": str(e),
                            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                        }
                    )
                    print(f"[vram-usage] 切换失败: {e}")

            # 采样
            sample = _sample_gpu(client)
            sample["elapsed_s"] = round(elapsed, 2)
            samples.append(sample)

            # 进度输出
            used = sample.get("vram_used_mb", 0)
            pct = sample.get("vram_percent", 0)
            engine = sample.get("current_engine", "?")
            print(
                f"  [{elapsed:5.1f}s] VRAM: {used:>7.1f}MB ({pct:5.1f}%) | "
                f"Util: {sample.get('gpu_util_pct', 0):5.1f}% | Engine: {engine}"
            )

            remaining = deadline - time.perf_counter()
            sleep_time = min(interval, max(0, remaining))
            time.sleep(sleep_time)

    # 摘要统计
    valid_samples = [s for s in samples if "error" not in s and "vram_used_mb" in s]
    summary: dict = {"sample_count": len(samples), "valid_count": len(valid_samples)}
    if valid_samples:
        vram_values = [s["vram_used_mb"] for s in valid_samples]
        util_values = [s.get("gpu_util_pct", 0) for s in valid_samples]
        summary["vram_peak_mb"] = round(max(vram_values), 1)
        summary["vram_min_mb"] = round(min(vram_values), 1)
        summary["vram_avg_mb"] = round(sum(vram_values) / len(vram_values), 1)
        summary["util_avg_pct"] = round(sum(util_values) / len(util_values), 1)
        summary["device_name"] = valid_samples[0].get("device_name", "unknown")

    return {
        "script": "vram-usage",
        "timestamp": datetime.now().isoformat(),
        "host": host,
        "port": port,
        "duration_s": duration,
        "interval_s": interval,
        "switch_engine": switch_engine,
        "switch_at_s": switch_at,
        "samples": samples,
        "events": events,
        "summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="TTS MultiModel 显存持续监控")
    parser.add_argument("--host", default=_DEFAULT_HOST, help="目标主机")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT, help="目标端口")
    parser.add_argument("--duration", type=float, default=_DEFAULT_DURATION, help="监控时长（秒）")
    parser.add_argument("--interval", type=float, default=_DEFAULT_INTERVAL, help="采样间隔（秒）")
    parser.add_argument("--switch", default=None, help="在监控中途切换到此引擎")
    parser.add_argument("--switch-at", type=float, default=0.0, help="切换引擎的时间点（秒）")
    args = parser.parse_args()

    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    result = monitor_vram(
        host=args.host,
        port=args.port,
        duration=args.duration,
        interval=args.interval,
        switch_engine=args.switch,
        switch_at=args.switch_at,
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = _RESULTS_DIR / f"vram-usage_{ts}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    s = result.get("summary", {})
    print("\n[vram-usage] ═══ 显存摘要 ═══")
    print(f"  采样数:      {s.get('sample_count', 0)}")
    print(f"  显存峰值:    {s.get('vram_peak_mb', 0):.1f} MB")
    print(f"  显存最低:    {s.get('vram_min_mb', 0):.1f} MB")
    print(f"  显存平均:    {s.get('vram_avg_mb', 0):.1f} MB")
    print(f"  GPU平均利用率: {s.get('util_avg_pct', 0):.1f}%")
    print(f"  设备:        {s.get('device_name', 'unknown')}")
    print(f"\n[vram-usage] 结果已保存: {out_file}")


if __name__ == "__main__":
    main()
