"""负载/压力测试 — 验证 API 并发处理能力。

使用 concurrent.futures.ThreadPoolExecutor 模拟并发请求，
测试 API 在高并发下的响应时间、错误率和资源稳定性。

运行方式::

    # 快速冒烟（少量并发）
    pytest tests/benchmarks/test_load_stress.py -v

    # 完整压测（CI 中跳过，手动运行）
    pytest tests/benchmarks/test_load_stress.py -v -m "stress"
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

pytestmark = pytest.mark.skipif(
    os.environ.get("TTS_SKIP_STRESS", "1") == "1",
    reason="Stress tests skipped by default. Set TTS_SKIP_STRESS=0 to run.",
)


class TestConcurrentAPIAccess:
    """并发 API 访问压力测试。"""

    @pytest.fixture
    def stress_client(self):
        from fastapi.testclient import TestClient
        from integrated_app.app_server import create_app
        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)
        return client

    def test_concurrent_health_check(self, stress_client):
        """并发 50 次 health ping 请求，全部应返回 200。"""
        def make_request():
            resp = stress_client.get("/api/health/ping")
            return resp.status_code

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request) for _ in range(50)]
            results = [f.result() for f in as_completed(futures)]

        assert all(code == 200 for code in results), \
            f"Not all requests succeeded: {set(results)}"

    def test_concurrent_model_status(self, stress_client):
        """并发 30 次 model status 请求。"""
        def make_request():
            resp = stress_client.get("/api/model/status")
            return resp.status_code

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request) for _ in range(30)]
            results = [f.result() for f in as_completed(futures)]

        assert all(code == 200 for code in results)

    def test_concurrent_mixed_endpoints(self, stress_client):
        """混合端点并发请求。"""
        endpoints = [
            "/api/health/ping",
            "/api/model/status",
            "/api/system/gpu/status",
            "/api/system/queue",
            "/v1/models",
        ]

        def make_request(url):
            resp = stress_client.get(url)
            return resp.status_code

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(make_request, endpoints[i % len(endpoints)])
                for i in range(50)
            ]
            results = [f.result() for f in as_completed(futures)]

        # All should be 200 (these are all GET endpoints that work without models)
        success_count = sum(1 for code in results if code == 200)
        assert success_count >= 40, f"Too many failures: {success_count}/50 succeeded"

    def test_response_time_under_load(self, stress_client):
        """负载下响应时间应在合理范围（< 2s for health ping）。"""
        def timed_request():
            start = time.time()
            resp = stress_client.get("/api/health/ping")
            elapsed = time.time() - start
            return resp.status_code, elapsed

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(timed_request) for _ in range(20)]
            results = [f.result() for f in as_completed(futures)]

        for code, elapsed in results:
            assert code == 200
            assert elapsed < 2.0, f"Response took {elapsed:.2f}s, expected < 2s"

    def test_no_memory_leak_under_repeated_requests(self, stress_client):
        """重复请求不应导致明显内存泄漏。"""
        import tracemalloc
        tracemalloc.start()
        snapshot1 = tracemalloc.take_snapshot()

        for _ in range(100):
            stress_client.get("/api/health/ping")

        snapshot2 = tracemalloc.take_snapshot()
        stats = snapshot2.compare_to(snapshot1, "lineno")
        tracemalloc.stop()

        # Total memory growth should be reasonable (< 5MB)
        total_growth = sum(s.size_diff for s in stats if s.size_diff > 0)
        assert total_growth < 5 * 1024 * 1024, \
            f"Memory growth {total_growth / 1024 / 1024:.2f}MB exceeds 5MB threshold"
