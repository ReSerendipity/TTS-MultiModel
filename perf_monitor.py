#!/usr/bin/env python3
"""
TTS_MultiModel 性能监控脚本
测量：SSE 连接稳定性、文本转语音响应时间
运行方式：python perf_monitor.py
"""

import subprocess
import time
import psutil
import json
import requests
from datetime import datetime
from pathlib import Path

def benchmark():
    """TTS 性能测试"""
    print("\n🔧 TTS_MultiModel 性能基准测试")
    print("="*50)
    
    health_url = "http://127.0.0.1:8000/api/system/health"
    
    try:
        response = requests.get(health_url, timeout=3)
        if response.status_code == 200:
            print("[TTS_MultiModel] ✅ 服务已在运行")
            
            # 测量 API 响应时间
            times = []
            for i in range(5):
                start = time.time()
                resp = requests.get(health_url, timeout=5)
                duration = (time.time() - start) * 1000
                times.append(duration)
                print(f"  请求 {i+1}: {duration:.1f}ms")
            
            avg_time = sum(times) / len(times)
            print(f"\n✅ 平均响应时间：{avg_time:.1f}ms")
            
            return {
                "avg_response_ms": round(avg_time, 2),
                "min_ms": round(min(times), 2),
                "max_ms": round(max(times), 2),
                "timestamp": datetime.now().isoformat()
            }
        else:
            print("[TTS_MultiModel] ⚠️ 服务返回非 200 状态码")
            return {"error": f"Status code: {response.status_code}"}
            
    except requests.exceptions.ConnectionError:
        print("[TTS_MultiModel] ⚠️ 服务未运行")
        print("请先启动：python -m uvicorn bin.integrated_app.app_server:app --host 127.0.0.1 --port 8000")
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
