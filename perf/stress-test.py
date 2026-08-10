"""并发压力测试脚本。

对 API 发起不同并发级别的压力测试（默认 5/10/20 路并发），
测量各级别下的 QPS（每秒请求数）、平均延迟、P95 延迟和错误率。
使用轻量级 GET 端点（health/ping、model/status、gpu/status）进行压测，
避免触发实际 TTS 推理（推理级压测见 generation-benchmark.py）。

度量指标（每个并发级别）：
    concurrency     — 并发连接数
    total_requests  — 总请求数
    success_count   — 成功请求数
    error_count     — 失败请求数
    error_rate      — 错误率（0-1）
    total_time_s    — 总耗时（秒）
    qps             — 每秒请求数
    avg_latency_ms  — 平均延迟（ms）
    p50_latency_ms  — P50 中位延迟（ms）
    p95_latency_ms  — P95 延迟（ms）
    p99_latency_ms  — P99 延迟（ms）

输出：
    perf/results/stress-test_<timestamp>.json
    控制台打印各级别对比表

用法::
    python perf/stress-test.py
    python perf/stress-test.py --levels 5,10,20,50
    python perf/stress-test.py --endpoint /api/system/health/ping --per-level 200
    python perf/stress-test.py --host 127.0.0.1 --port 7869
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import httpx

_PERF_DIR = Path(__file__).resolve().parent
_RESULTS_DIR = _PERF_DIR / "results"

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 7869
_DEFAULT_LEVELS = [5, 10, 20]
_DEFAULT_PER_LEVEL = 100
_DEFAULT_ENDPOINT = "/api/system/health/ping"


def _single_request(
    client: httpx.Client,
    endpoint: str,
) -> tuple[int, float]:
    """发起一次 GET 请求。

    Returns:
        (status_code, latency_ms)
    """
    t0 = time.perf_counter()
    try:
        resp = client.get(endpoint, timeout=10.0)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return resp.status_code, elapsed_ms
    except Exception:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return 0, elapsed_ms


def _percentile(data: list[float], pct: float) -> float:
    """计算百分位数。

    Args:
        data: 数值列表。
        pct: 百分位（0-100）。

    Returns:
        对应百分位的值。
    """
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * pct / 100)
    idx = min(idx, len(sorted_data) - 1)
    return sorted_data[idx]


def run_stress_level(
    base_url: str,
    endpoint: str,
    concurrency: int,
    per_level: int,
) -> dict:
    """执行单个并发级别的压力测试。

    Args:
        base_url: 基础 URL。
        endpoint: 测试端点。
        concurrency: 并发线程数。
        per_level: 本级别总请求数。

    Returns:
        包含该级别所有度量指标的字典。
    """
    latencies: list[float] = []
    status_codes: list[int] = []
    errors: list[str] = []

    print(f"  并发={concurrency:3d}，请求={per_level:4d}...", end=" ")

    with httpx.Client(base_url=base_url) as client:
        start = time.perf_counter()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(_single_request, client, endpoint) for _ in range(per_level)]
            for future in as_completed(futures):
                try:
                    code, lat = future.result()
                    status_codes.append(code)
                    latencies.append(lat)
                    if code != 200:
                        errors.append(f"HTTP {code}")
                except Exception as e:
                    errors.append(str(e))

        total_time = time.perf_counter() - start

    success_count = sum(1 for c in status_codes if c == 200)
    error_count = len(status_codes) - success_count
    qps = per_level / total_time if total_time > 0 else 0

    result = {
        "concurrency": concurrency,
        "total_requests": per_level,
        "success_count": success_count,
        "error_count": error_count,
        "error_rate": round(error_count / per_level, 4) if per_level > 0 else 0,
        "total_time_s": round(total_time, 3),
        "qps": round(qps, 1),
        "avg_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0,
        "p50_latency_ms": round(_percentile(latencies, 50), 2),
        "p95_latency_ms": round(_percentile(latencies, 95), 2),
        "p99_latency_ms": round(_percentile(latencies, 99), 2),
        "min_latency_ms": round(min(latencies), 2) if latencies else 0,
        "max_latency_ms": round(max(latencies), 2) if latencies else 0,
    }

    if errors:
        # 只保留前 5 个错误样本
        result["error_samples"] = errors[:5]

    print(
        f"QPS={result['qps']:6.1f} | "
        f"avg={result['avg_latency_ms']:6.1f}ms | "
        f"p95={result['p95_latency_ms']:6.1f}ms | "
        f"err={result['error_rate']:.1%}"
    )

    return result


def run_stress_test(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    endpoint: str = _DEFAULT_ENDPOINT,
    levels: list[int] | None = None,
    per_level: int = _DEFAULT_PER_LEVEL,
) -> dict:
    """执行多级别并发压力测试。

    Args:
        host: 目标主机。
        port: 目标端口。
        endpoint: 测试端点。
        levels: 并发级别列表。
        per_level: 每级别总请求数。

    Returns:
        包含所有级别结果的字典。
    """
    if levels is None:
        levels = _DEFAULT_LEVELS

    base_url = f"http://{host}:{port}"

    print(f"[stress] 端点: {endpoint}")
    print(f"[stress] 并发级别: {levels}")
    print(f"[stress] 每级别请求数: {per_level}")
    print()

    # 预检：服务器是否可用
    with httpx.Client(base_url=base_url) as client:
        try:
            resp = client.get("/api/system/health/ping", timeout=5.0)
            if resp.status_code != 200:
                return {"error": f"服务器未就绪: HTTP {resp.status_code}", "results": []}
        except Exception as e:
            return {"error": f"无法连接服务器: {e}", "results": []}

    level_results = []
    for level in levels:
        result = run_stress_level(base_url, endpoint, level, per_level)
        level_results.append(result)
        # 级别间短暂冷却
        time.sleep(1.0)

    return {
        "script": "stress-test",
        "timestamp": datetime.now().isoformat(),
        "host": host,
        "port": port,
        "endpoint": endpoint,
        "per_level": per_level,
        "levels": levels,
        "results": level_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="TTS MultiModel 并发压力测试")
    parser.add_argument("--host", default=_DEFAULT_HOST, help="目标主机")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT, help="目标端口")
    parser.add_argument("--endpoint", default=_DEFAULT_ENDPOINT, help="测试端点")
    parser.add_argument("--levels", default="5,10,20", help="逗号分隔的并发级别")
    parser.add_argument("--per-level", type=int, default=_DEFAULT_PER_LEVEL, help="每级别总请求数")
    args = parser.parse_args()

    levels = [int(x) for x in args.levels.split(",")]

    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    result = run_stress_test(
        host=args.host,
        port=args.port,
        endpoint=args.endpoint,
        levels=levels,
        per_level=args.per_level,
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = _RESULTS_DIR / f"stress-test_{ts}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print("\n[stress] ═══ 压力测试摘要 ═══")
    print(f"  {'并发':>4s} | {'QPS':>6s} | {'avg(ms)':>8s} | {'p95(ms)':>8s} | {'p99(ms)':>8s} | {'错误率':>6s}")
    print(f"  {'─' * 4}─┼─{'─' * 6}─┼─{'─' * 8}─┼─{'─' * 8}─┼─{'─' * 8}─┼─{'─' * 6}")
    for r in result.get("results", []):
        print(
            f"  {r['concurrency']:4d} | {r['qps']:6.1f} | {r['avg_latency_ms']:8.1f} | "
            f"{r['p95_latency_ms']:8.1f} | {r['p99_latency_ms']:8.1f} | {r['error_rate']:5.1%}"
        )
    print(f"\n[stress] 结果已保存: {out_file}")


if __name__ == "__main__":
    main()
