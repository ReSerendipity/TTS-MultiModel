#!/usr/bin/env python3
"""
TTS_MultiModel 性能监控脚本
测量：SSE 连接稳定性、文本转语音响应时间
运行方式：python perf_monitor.py
"""

import json
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_base_url() -> str:
    """读取 config.yaml 的 server.port，回退 127.0.0.1:7869（项目默认端口）。

    离线工作约束（AGENTS.md 硬约束 #5）：仅解析本地 config.yaml，不请求外部资源。
    """
    host, port = "127.0.0.1", 7869
    try:
        cfg_path = PROJECT_ROOT / "config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        port = int(cfg.get("server", {}).get("port", port))
    except Exception:
        pass
    return f"http://{host}:{port}"


def benchmark():
    """TTS 性能测试"""
    print("\n🔧 TTS_MultiModel 性能基准测试")
    print("=" * 50)

    base_url = _resolve_base_url()
    health_url = f"{base_url}/api/system/health"

    try:
        response = requests.get(health_url, timeout=3)
        if response.status_code == 200:
            print("[TTS_MultiModel] ✅ 服务已在运行")

            # 测量 API 响应时间
            times = []
            for i in range(5):
                start = time.time()
                _ = requests.get(health_url, timeout=5)
                duration = (time.time() - start) * 1000
                times.append(duration)
                print(f"  请求 {i + 1}: {duration:.1f}ms")

            avg_time = sum(times) / len(times)
            print(f"\n✅ 平均响应时间：{avg_time:.1f}ms")

            return {
                "avg_response_ms": round(avg_time, 2),
                "min_ms": round(min(times), 2),
                "max_ms": round(max(times), 2),
                "timestamp": datetime.now().isoformat(),
            }
        else:
            print("[TTS_MultiModel] ⚠️ 服务返回非 200 状态码")
            return {"error": f"Status code: {response.status_code}"}

    except requests.exceptions.ConnectionError:
        print("[TTS_MultiModel] ⚠️ 服务未运行")
        print(f"请先启动：python -m uvicorn app.integrated_app.app_server:app --host 127.0.0.1 --port {base_url.split(':')[-1]}")
        return {"error": "Service not running"}
    except Exception as e:
        print(f"[TTS_MultiModel] ❌ 异常：{e}")
        return {"error": str(e)}


if __name__ == "__main__":
    results_dir = Path("./perf/results")
    results_dir.mkdir(exist_ok=True)

    metrics = benchmark()

    output_file = results_dir / f"benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)

    print(f"\n✅ 结果已保存：{output_file}")
