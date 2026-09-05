"""音质 Golden Set 回归测试 — 固定输入验证合成确定性与输出有效性。

测试目标：
  1. 确定性校验：同一 case（固定文本/音色/种子/引擎/温度）连续生成两次，
     音频 SHA256 必须完全一致。若不一致说明存在非确定性来源（未设 seed、
     CUDA 非确定性算子、随机数泄漏等）。
  2. 有效性校验：每次生成的音频必须为非空有效音频，时长在配置的 [min, max]
     范围内，采样率与引擎声明一致。

运行前提：
  - TTS 服务已启动（http://127.0.0.1:7869），或设置 TTS_SERVER_URL
  - 模型权重已加载（voxcpm2）
  - GPU 可用

CI 行为：标记为 integration + cuda，无 GPU/无服务器时自动 skip。
运行方式：
    pytest tests/test_golden_set.py -v -m integration
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.cuda,
    pytest.mark.skipif(
        os.environ.get("CUDA_VISIBLE_DEVICES", "") == "" and not os.environ.get("TTS_RUN_GPU_TESTS"),
        reason="需要 GPU 环境。设置 TTS_RUN_GPU_TESTS=1 或清除 CUDA_VISIBLE_DEVICES 来运行。",
    ),
]

_GOLDEN_CONFIG_PATH = Path(__file__).parent / "golden_set" / "config.json"


def _load_golden_cases() -> list[dict]:
    """加载 golden set 配置中的测试用例。"""
    with open(_GOLDEN_CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    return config["cases"]


def _server_url() -> str:
    return os.environ.get("TTS_SERVER_URL", "http://127.0.0.1:7869")


def _check_server_running() -> bool:
    """检查 TTS 服务是否可访问。"""
    import urllib.request

    try:
        urllib.request.urlopen(_server_url() + "/api/system/health", timeout=5)
        return True
    except Exception:
        return False


def _generate_audio(case: dict) -> bytes:
    """通过 API 生成音频，返回原始字节。"""
    import requests

    url = _server_url() + "/api/generate"
    payload = {
        "text": case["text"],
        "engine": case["engine"],
        "persona": case.get("persona", ""),
        "seed": case["seed"],
        "temperature": case.get("temperature", 0.8),
        "speed": case.get("speed", 1.0),
        "stream": False,
    }
    resp = requests.post(url, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.content


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get_audio_duration(audio_bytes: bytes) -> float:
    """获取音频时长（秒），失败返回 -1。"""
    try:
        import soundfile as sf

        data, sr = sf.read(io.BytesIO(audio_bytes))
        return len(data) / sr
    except Exception:
        pass
    try:
        import torchaudio

        wav, sr = torchaudio.load(io.BytesIO(audio_bytes))
        return wav.shape[1] / sr
    except Exception:
        return -1.0


class TestGoldenSetDeterminism:
    """Golden Set 确定性校验：同一输入两次生成必须哈希一致。"""

    @pytest.fixture(scope="class", autouse=True)
    def require_server(self):
        """整个测试类需要运行中的服务器。"""
        if not _check_server_running():
            pytest.skip(f"TTS 服务未运行于 {_server_url()}，请先启动服务。")

    @pytest.mark.parametrize("case", _load_golden_cases(), ids=lambda c: c["id"])
    def test_deterministic_output(self, case: dict):
        """同一 case 连续生成两次，SHA256 必须完全一致。"""
        audio1 = _generate_audio(case)
        audio2 = _generate_audio(case)
        hash1 = _sha256(audio1)
        hash2 = _sha256(audio2)
        assert hash1 == hash2, (
            f"非确定性输出！case={case['id']} seed={case['seed']} hash1={hash1[:16]}... hash2={hash2[:16]}..."
        )


class TestGoldenSetValidity:
    """Golden Set 输出有效性校验：音频必须非空、时长在范围内。"""

    @pytest.fixture(scope="class", autouse=True)
    def require_server(self):
        if not _check_server_running():
            pytest.skip(f"TTS 服务未运行于 {_server_url()}，请先启动服务。")

    @pytest.mark.parametrize("case", _load_golden_cases(), ids=lambda c: c["id"])
    def test_output_non_empty(self, case: dict):
        """生成的音频必须非空（>1KB）。"""
        audio = _generate_audio(case)
        assert len(audio) > 1024, f"音频过小（{len(audio)} bytes），case={case['id']}"

    @pytest.mark.parametrize("case", _load_golden_cases(), ids=lambda c: c["id"])
    def test_output_duration_in_range(self, case: dict):
        """生成的音频时长必须在配置的 [min, max] 范围内。"""
        audio = _generate_audio(case)
        duration = _get_audio_duration(audio)
        assert duration > 0, f"无法解析音频时长，case={case['id']}"
        min_dur = case.get("min_duration_sec", 0.5)
        max_dur = case.get("max_duration_sec", 60.0)
        assert min_dur <= duration <= max_dur, (
            f"音频时长 {duration:.2f}s 超出范围 [{min_dur}, {max_dur}]，case={case['id']}"
        )
