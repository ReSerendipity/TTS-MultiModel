"""冷启动时间度量脚本。

度量从服务器进程启动到 API 首次可响应（health/ping 返回 200）的耗时，
以及后续引擎加载（可选）的时间。冷启动时间是 TTS 项目最核心的性能指标之一，
因为 TTS 模型加载通常耗时数秒至数十秒。

度量指标：
    1. process_to_ping_ms  — 从 subprocess 启动到 /api/system/health/ping 首次 200 的毫秒数
    2. ping_to_ready_ms    — 从 ping 可用到 /api/system/health/ready 返回 ready 的毫秒数
    3. engine_load_ms      — （可选）从 ready 到指定引擎加载完成的毫秒数
    4. total_cold_start_ms — process_to_ping + ping_to_ready + engine_load 的总和

输出：
    perf/results/cold-start_<timestamp>.json
    控制台打印摘要表

用法::
    python perf/cold-start.py
    python perf/cold-start.py --engine voxcpm2
    python perf/cold-start.py --host 127.0.0.1 --port 7869
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

# ── 路径常量 ──────────────────────────────────────────────────
_PERF_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PERF_DIR.parent
_RESULTS_DIR = _PERF_DIR / "results"
_LAUNCH_SCRIPT = _PROJECT_ROOT / "app" / "clean_launch.py"

# ── 默认参数 ──────────────────────────────────────────────────
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 7869
_PING_TIMEOUT = 120.0  # 最长等待 120 秒
_ENGINE_READY_TIMEOUT = 600.0  # /readyz 变 200 的最长等待（大模型加载预留 10 分钟）
_POLL_INTERVAL = 0.2  # 轮询间隔 200ms


def _find_python() -> str:
    """查找可用的 Python 解释器，优先系统 Python。"""
    for candidate in (
        sys.executable,
        r"C:\Python312\python.exe",
        r"C:\Python311\python.exe",
        r"C:\Python310\python.exe",
    ):
        if candidate and os.path.isfile(candidate):
            return candidate
    return sys.executable


def _wait_for_endpoint(
    client: httpx.Client,
    url: str,
    timeout: float,
    interval: float = _POLL_INTERVAL,
) -> tuple[float, int]:
    """轮询等待端点返回 200。

    Returns:
        (elapsed_ms, status_code) — 首次 200 时的耗时（ms）和状态码。
        若超时返回 (timeout_ms, last_status_code)。
    """
    start = time.perf_counter()
    deadline = start + timeout
    last_status = 0
    while time.perf_counter() < deadline:
        try:
            resp = client.get(url, timeout=interval + 1)
            last_status = resp.status_code
            if resp.status_code == 200:
                return (time.perf_counter() - start) * 1000, 200
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
            OSError,
        ):
            pass
        time.sleep(interval)
    return (time.perf_counter() - start) * 1000, last_status


def measure_cold_start(
    host: str = _DEFAULT_HOST,
    port: int = _DEFAULT_PORT,
    engine: str | None = None,
) -> dict:
    """执行冷启动度量。

    Args:
        host: 目标主机。
        port: 目标端口（脚本会通过环境变量传给子进程）。
        engine: 可选引擎名称，若指定则额外度量引擎加载时间。

    Returns:
        包含所有度量指标的字典。
    """
    base_url = f"http://{host}:{port}"
    results: dict = {
        "script": "cold-start",
        "timestamp": datetime.now().isoformat(),
        "host": host,
        "port": port,
        "engine": engine,
    }

    python_exe = _find_python()
    env = os.environ.copy()
    env["TTS_PORT"] = str(port)
    env["TTS_HOST"] = host
    # 抑制浏览器自动打开
    env["TTS_NO_BROWSER"] = "1"

    # 修复（运维稳定性实施 2026-09-05）：此前 stdout=PIPE 但从不读取，
    # 服务日志写满管道缓冲区后子进程直接死锁、端口永远无法监听，
    # 导致 ping 120s 超时（实测两次复现）。改为落盘到临时日志文件，
    # 失败时还能把日志尾部打印出来辅助定位。
    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    launch_log = _RESULTS_DIR / "cold-start_launch.log"

    print(f"[cold-start] 启动服务器: {python_exe} {_LAUNCH_SCRIPT}（日志 → {launch_log}）")
    with open(launch_log, "w", encoding="utf-8", errors="replace") as log_fh:
        proc = subprocess.Popen(
            [python_exe, str(_LAUNCH_SCRIPT)],
            cwd=str(_PROJECT_ROOT),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )

        try:
            with httpx.Client(base_url=base_url) as client:
                # 1. 等待 ping 可用
                print("[cold-start] 等待 /api/system/health/ping 响应...")
                ping_ms, ping_status = _wait_for_endpoint(client, "/api/system/health/ping", _PING_TIMEOUT)
                results["process_to_ping_ms"] = round(ping_ms, 1)
                results["ping_status"] = ping_status

                if ping_status != 200:
                    results["error"] = f"ping 未在 {_PING_TIMEOUT}s 内返回 200 (last={ping_status})"
                    print(f"[cold-start] ⚠ {results['error']}")
                else:
                    print(f"[cold-start] ✓ ping 可用: {ping_ms:.0f}ms")

                    # 2. 等待 ready
                    ready_ms, ready_status = _wait_for_endpoint(client, "/api/system/health/ready", 60.0)
                    results["ping_to_ready_ms"] = round(ready_ms, 1)
                    results["ready_status"] = ready_status
                    print(f"[cold-start] ✓ ready 就绪: {ready_ms:.0f}ms")

                    # 3. 可选：引擎加载 —— 轮询 /readyz 直到返回 200
                    #    /readyz 在引擎未就绪时返回 503，就绪后返回 200，
                    #    因此「ping 就绪 → /readyz 200」的间隔 = 模型加载到可用的真实耗时，
                    #    也正是 K8s readinessProbe 的判定端点（运维稳定性评估 P0 修复后一致）。
                    #    Why 不用 POST /api/model/load：该端点受 CSRF 保护，脚本无 token 会 403。
                    engine_load_ms = 0.0
                    if engine:
                        print(
                            f"[cold-start] 等待引擎 {engine} 就绪（轮询 /readyz，需启动时设 TTS_AUTO_LOAD_MODEL=1）..."
                        )
                        engine_load_ms, readyz_status = _wait_for_endpoint(client, "/readyz", _ENGINE_READY_TIMEOUT)
                        results["readyz_status"] = readyz_status
                        results["engine_load_ms"] = round(engine_load_ms, 1)
                        if readyz_status == 200:
                            print(f"[cold-start] ✓ 引擎就绪（/readyz 200）: {engine_load_ms:.0f}ms")
                        else:
                            results["engine_load_error"] = (
                                f"/readyz 未在 {_ENGINE_READY_TIMEOUT}s 内变 200 (last={readyz_status})"
                            )
                            print(f"[cold-start] ⚠ {results['engine_load_error']}")

                    total = ping_ms + ready_ms + engine_load_ms
                    results["total_cold_start_ms"] = round(total, 1)
                    print("\n[cold-start] ═══ 冷启动摘要 ═══")
                    print(f"  进程→ping:     {ping_ms:>8.0f} ms")
                    print(f"  ping→ready:    {ready_ms:>8.0f} ms")
                    if engine:
                        print(f"  ready→/readyz: {engine_load_ms:>8.0f} ms（模型加载到可用 = MTTR 核心）")
                    print("  ─────────────────────────")
                    print(f"  总冷启动:       {total:>8.0f} ms ({total / 1000:.2f}s)")

        finally:
            # 优雅关闭子进程（with 块外再读取日志尾部时句柄已关闭）
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
                proc.wait()

    # ping 未通时，打印启动日志尾部辅助定位（此前完全看不到子进程输出）
    if results.get("ping_status") != 200:
        try:
            tail = launch_log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
            print("[cold-start] ── 启动日志尾部（诊断用）──")
            print("\n".join(tail))
        except OSError:
            pass

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="TTS MultiModel 冷启动时间度量")
    parser.add_argument("--host", default=_DEFAULT_HOST, help="目标主机")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT, help="目标端口")
    parser.add_argument("--engine", default=None, help="可选：额外度量引擎加载时间")
    args = parser.parse_args()

    _RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    result = measure_cold_start(host=args.host, port=args.port, engine=args.engine)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = _RESULTS_DIR / f"cold-start_{ts}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n[cold-start] 结果已保存: {out_file}")


if __name__ == "__main__":
    main()
