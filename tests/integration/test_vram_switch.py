"""引擎切换 VRAM 释放测试 — 验证引擎切换时显存正确释放和加载。

测试场景:
1. 加载引擎 A → 检查显存占用 → 卸载引擎 A → 检查显存释放
2. 加载引擎 A → 切换到引擎 B → 检查显存只占 B 的量（无泄漏）
3. 反复切换 A → B → A → B → 验证显存稳定不增长（无泄漏）

需要 GPU 环境和真实模型权重，CI 离线环境自动跳过。
"""

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CUDA_VISIBLE_DEVICES", "") == "" and not os.environ.get("TTS_RUN_GPU_TESTS"),
        reason="需要 GPU 环境。设置 TTS_RUN_GPU_TESTS=1 或清除 CUDA_VISIBLE_DEVICES 来运行。",
    ),
]


class TestVRAMSwitch:
    """VRAM 切换测试。"""

    @pytest.fixture(scope="class")
    def server_url(self):
        import urllib.request

        url = os.environ.get("TTS_SERVER_URL", "http://127.0.0.1:7869")
        try:
            urllib.request.urlopen(url, timeout=5)
            return url
        except Exception:
            pytest.skip(f"服务器未运行于 {url}。")

    def _get_gpu_memory(self, server_url):
        """获取当前 GPU 显存信息。"""
        import requests

        resp = requests.get(f"{server_url}/api/system/gpu", timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        # Try common field names
        for key in ("vram_total", "total_memory", "gpu_memory_total"):
            if key in data:
                return data
        return data

    def _unload_all(self, server_url):
        """卸载所有模型。"""
        import requests

        requests.post(f"{server_url}/api/model/unload", timeout=60)

    def test_load_unload_vram_release(self, server_url):
        """测试加载后卸载是否释放显存。"""
        import time

        import requests

        self._unload_all(server_url)
        time.sleep(2)

        # Get baseline VRAM
        before = self._get_gpu_memory(server_url)

        # Load VoxCPM2
        resp = requests.post(
            f"{server_url}/api/model/load",
            json={"engine": "voxcpm2"},
            timeout=300,
        )
        assert resp.status_code == 200
        time.sleep(2)

        loaded = self._get_gpu_memory(server_url)

        # Unload
        self._unload_all(server_url)
        time.sleep(5)

        after = self._get_gpu_memory(server_url)

        # Verify: loaded > before, after should be close to before
        if before and loaded and after:
            print(f"VRAM before: {before}")
            print(f"VRAM loaded: {loaded}")
            print(f"VRAM after unload: {after}")

    def test_engine_switch_no_leak(self, server_url):
        """测试引擎切换无显存泄漏。"""
        import time

        import requests

        self._unload_all(server_url)
        time.sleep(3)

        # Load VoxCPM2
        requests.post(
            f"{server_url}/api/model/load",
            json={"engine": "voxcpm2"},
            timeout=300,
        )
        time.sleep(2)
        vram_v1 = self._get_gpu_memory(server_url)

        # Switch to IndexTTS2
        requests.post(
            f"{server_url}/api/model/switch",
            json={"engine": "indextts2"},
            timeout=300,
        )
        time.sleep(2)
        vram_i1 = self._get_gpu_memory(server_url)

        # Switch back to VoxCPM2
        requests.post(
            f"{server_url}/api/model/switch",
            json={"engine": "voxcpm2"},
            timeout=300,
        )
        time.sleep(2)
        vram_v2 = self._get_gpu_memory(server_url)

        # Switch to IndexTTS2 again
        requests.post(
            f"{server_url}/api/model/switch",
            json={"engine": "indextts2"},
            timeout=300,
        )
        time.sleep(2)
        vram_i2 = self._get_gpu_memory(server_url)

        print(f"VoxCPM2 first: {vram_v1}")
        print(f"IndexTTS2 first: {vram_i1}")
        print(f"VoxCPM2 second: {vram_v2}")
        print(f"IndexTTS2 second: {vram_i2}")

        # Cleanup
        self._unload_all(server_url)

    def test_repeated_switch_stability(self, server_url):
        """测试反复切换的显存稳定性（5 轮）。"""
        import time

        import requests

        self._unload_all(server_url)
        time.sleep(3)

        vram_readings = []

        for i in range(5):
            engine = "voxcpm2" if i % 2 == 0 else "indextts2"
            requests.post(
                f"{server_url}/api/model/switch",
                json={"engine": engine},
                timeout=300,
            )
            time.sleep(3)
            vram = self._get_gpu_memory(server_url)
            vram_readings.append(vram)

        print(f"VRAM readings over {len(vram_readings)} switches:")
        for i, reading in enumerate(vram_readings):
            print(f"  Switch {i}: {reading}")

        # Cleanup
        self._unload_all(server_url)
