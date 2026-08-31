"""真实推理冒烟测试 — 验证 TTS 引擎在真实模型加载下能完成基本推理。

此测试需要真实模型权重和 GPU 环境，仅在本地有模型时运行。
CI 离线环境自动跳过。

运行方式:
    pytest tests/integration/test_real_inference_smoke.py -v -m integration

环境要求:
    - 模型已下载到 model/
    - GPU 可用（CUDA 或 MPS）
    - TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 MODELSCOPE_OFFLINE=1
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


class TestRealInferenceSmoke:
    """真实推理冒烟测试。"""

    @pytest.fixture(scope="class")
    def server_url(self):
        """获取服务器 URL，如未运行则跳过。"""
        import urllib.request

        url = os.environ.get("TTS_SERVER_URL", "http://127.0.0.1:7869")
        try:
            urllib.request.urlopen(url, timeout=5)
            return url
        except Exception:
            pytest.skip(f"服务器未运行于 {url}。请先启动服务器。")

    def test_health_check(self, server_url):
        """测试健康检查端点。"""
        import requests

        resp = requests.get(f"{server_url}/api/system/health", timeout=10)
        assert resp.status_code == 200

    def test_gpu_status(self, server_url):
        """测试 GPU 状态端点。"""
        import requests

        resp = requests.get(f"{server_url}/api/system/gpu", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert "gpu_available" in data or "device" in data

    def test_model_status(self, server_url):
        """测试模型状态端点。"""
        import requests

        resp = requests.get(f"{server_url}/api/model/status", timeout=10)
        assert resp.status_code == 200

    def test_load_voxcpm2(self, server_url):
        """测试加载 VoxCPM2 引擎。"""
        import requests

        resp = requests.post(
            f"{server_url}/api/model/load",
            json={"engine": "voxcpm2"},
            timeout=300,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") or data.get("loaded")

    def test_voxcpm2_clone(self, server_url):
        """测试 VoxCPM2 克隆推理（需要先加载引擎和 persona）。"""
        import requests

        # 查找可用的 persona
        personas_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "personas",
        )
        ref_wav = None
        if os.path.isdir(personas_dir):
            for f in os.listdir(personas_dir):
                if f.endswith(".wav"):
                    ref_wav = os.path.join(personas_dir, f)
                    break

        if not ref_wav:
            pytest.skip("未找到 persona wav 文件")

        with open(ref_wav, "rb") as f:
            resp = requests.post(
                f"{server_url}/api/generate/voxcpm2/clone",
                files={"ref_audio": f},
                data={"text": "你好，这是推理冒烟测试。"},
                timeout=300,
            )
        assert resp.status_code == 200
        # Either audio bytes or JSON with path
        ct = resp.headers.get("content-type", "")
        if "audio" in ct:
            assert len(resp.content) > 1000  # At least 1KB
        else:
            data = resp.json()
            assert data.get("audio_path") or data.get("success")

    def test_load_indextts2(self, server_url):
        """测试加载 IndexTTS2 引擎。"""
        import requests

        resp = requests.post(
            f"{server_url}/api/model/load",
            json={"engine": "indextts2"},
            timeout=300,
        )
        assert resp.status_code == 200

    def test_indextts2_synthesize(self, server_url):
        """测试 IndexTTS2 合成推理。"""
        import requests

        resp = requests.post(
            f"{server_url}/api/generate/indextts2/synthesize",
            data={
                "text": "你好，这是 IndexTTS2 推理冒烟测试。",
                "emotion_happy": "1.0",
            },
            timeout=300,
        )
        assert resp.status_code == 200

    def test_sse_events(self, server_url):
        """测试 SSE 事件流端点可连接。"""
        import requests

        resp = requests.get(
            f"{server_url}/api/sse/events",
            stream=True,
            timeout=5,
        )
        # SSE returns 200 and streams
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        resp.close()
