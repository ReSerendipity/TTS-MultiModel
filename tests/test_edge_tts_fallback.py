"""阶段 4 D-2 自动降级链测试（离线，monkeypatch seam，不触 GPU / 不联网）。

覆盖（`docs/runbooks/2026-11-01-o3-failover-drill.md` §2b.4 三断点）：
① 主引擎可用 → 不走降级（回归保护：响应仍是 HTML，degraded 链路零触发）；
② 主引擎不可用 → 自动降级 edge-tts，返回 200 JSON 带 degraded 标记 + 产物判据；
③ 降级目标也失败 → 503 error_type=fallback_failed（与 engine_not_ready 可区分，不假成功）；
④ edge-tts 直连路由 /api/generate/edge-tts/tts 存在且语义正确（含 openapi 快照断言）。
"""

from __future__ import annotations

from typing import Any

import numpy as np
from fastapi.testclient import TestClient

from integrated_app.model_registry import registry
from integrated_app.routes.generate import utils as gen_utils

_FAKE_AUDIO = b"\xff\xf3" + b"\x00" * 60  # MP3 帧头 + 填充：判据=字节数>0（edge-tts 7.3.1 恒出 MP3，RIFF 不可用）


def _csrf_post(client: TestClient, url: str, data: dict[str, Any]) -> Any:
    """带 CSRF 双提交的 POST（先 GET 取 cookie 再回传 header）。"""
    client.get("/api/model/status")
    token = client.cookies.get("csrf_token")
    headers = {"X-CSRF-Token": token} if token else {}
    return client.post(url, data=data, headers=headers)


def test_main_engine_loaded_does_not_fallback(client: TestClient, monkeypatch: Any) -> None:
    """① 主引擎可用：走原 HTML 路径，不触发降级 seam（回归保护）。"""
    import integrated_app.routes.generate.voxcpm2.streaming as streaming_mod

    monkeypatch.setattr(registry, "voxcpm_model", object(), raising=False)
    called = {"edge": False}

    def _fake_segment(*_a: Any, **_k: Any) -> np.ndarray:
        return np.zeros(4800, dtype=np.float32)

    async def _fake_edge(*_a: Any, **_k: Any) -> tuple[str, int]:  # pragma: no cover - 断言不触发
        called["edge"] = True
        return ("should_not_happen.wav", 1)

    monkeypatch.setattr(streaming_mod, "_generate_segment_sync", _fake_segment)
    monkeypatch.setattr(gen_utils, "_synthesize_edge_tts_wav", _fake_edge)

    resp = _csrf_post(client, "/api/generate/streaming_audio", {"text": "回归保护测试。"})

    assert called["edge"] is False, "主引擎可用时不得调用降级 seam"
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html"), "主路径应保持 HTML 契约"
    assert b"degraded" not in resp.content


def test_auto_fallback_when_main_unavailable(client: TestClient, monkeypatch: Any) -> None:
    """② 主引擎不可用（未加载且权重缺失）：自动降级 edge-tts，200 JSON 带 degraded 标记。"""

    async def _fake_edge(text: str, lang: str = "zh") -> tuple[str, int]:
        assert "降级" in text
        return ("edge_fallback_test.mp3", len(_FAKE_AUDIO))

    monkeypatch.setattr(registry, "voxcpm_model", None, raising=False)
    monkeypatch.setattr(gen_utils, "_engine_model_files_present", lambda _name: False)
    monkeypatch.setattr(gen_utils, "_synthesize_edge_tts_wav", _fake_edge)

    resp = _csrf_post(client, "/api/generate/streaming_audio", {"text": "降级链测试。"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["status"] == "ok"
    assert body["filename"] == "edge_fallback_test.mp3"
    assert body["size_bytes"] == len(_FAKE_AUDIO) > 0
    assert body["degraded"]["from"] == "voxcpm2"
    assert body["degraded"]["to"] == "edge-tts"
    assert body["degraded"]["reason"] == "main_engine_unavailable"


def test_fallback_failure_is_distinguishable(client: TestClient, monkeypatch: Any) -> None:
    """③ 降级目标也失败：503 fallback_failed，与 engine_not_ready 可区分、不假成功。"""

    async def _broken_edge(text: str, lang: str = "zh") -> tuple[str, int]:
        raise gen_utils.EdgeTTSFallbackUnavailableError("模拟网络失败")

    monkeypatch.setattr(registry, "voxcpm_model", None, raising=False)
    monkeypatch.setattr(gen_utils, "_engine_model_files_present", lambda _name: False)
    monkeypatch.setattr(gen_utils, "_synthesize_edge_tts_wav", _broken_edge)

    resp = _csrf_post(client, "/api/generate/streaming_audio", {"text": "降级失败测试。"})

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "error"
    assert body["error_type"] == "fallback_failed"
    assert body["error_type"] != "engine_not_ready"
    assert "模拟网络失败" in body["detail"]
    assert body["degraded"]["attempted"] is True
    assert "filename" not in body, "失败响应不得携带产物字段（不假成功）"


def test_weightless_engine_guard_passes_without_instance(monkeypatch: Any) -> None:
    """守卫放宽口径：无权重引擎（edge-tts）无实例也放行；主引擎口径不变。"""
    assert gen_utils._is_weightless_engine("edge-tts") is True
    assert gen_utils._is_weightless_engine("voxcpm2") is False
    # edge-tts 无实例：_check_engine_ready 应放行（返回 None）
    assert gen_utils._check_engine_ready(None, "edge-tts") is None
    # 主引擎无实例：仍拦截（HTML 错误）
    blocked = gen_utils._check_engine_ready(None, "voxcpm2")
    assert blocked is not None and blocked.status_code == 400
    # 降级触发判据：voxcpm2 不可用=True，edge-tts 恒 False
    # 触发判据（未加载 + 权重缺失才 True）：本机 VoxCPM2 权重在盘，需显式模拟缺失
    monkey_files = {"missing": True}
    real_present = gen_utils._engine_model_files_present
    monkeypatch.setattr(
        gen_utils,
        "_engine_model_files_present",
        lambda name: False if monkey_files["missing"] else real_present(name),
    )
    assert gen_utils._main_engine_unavailable("voxcpm2") is True
    monkey_files["missing"] = False  # 权重在盘 + 未加载 → 不可降级（未加载≠不可用）
    assert gen_utils._main_engine_unavailable("voxcpm2") is False
    assert gen_utils._main_engine_unavailable("edge-tts") is False


def test_edge_tts_direct_route_and_openapi(client: TestClient, monkeypatch: Any) -> None:
    """④ 直连路由存在且语义正确；openapi 快照含新路由与 degraded/fallback_failed 字段。"""

    async def _fake_edge(text: str, lang: str = "zh") -> tuple[str, int]:
        return ("edge_direct.wav", len(_FAKE_AUDIO))

    monkeypatch.setattr(gen_utils, "_synthesize_edge_tts_wav", _fake_edge)

    ok = _csrf_post(client, "/api/generate/edge-tts/tts", {"text": "直连路由测试。"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["engine"] == "edge-tts" and body["filename"] == "edge_direct.wav"

    empty = _csrf_post(client, "/api/generate/edge-tts/tts", {"text": "  "})
    assert empty.status_code == 400 and empty.json()["error_type"] == "validation"

    spec = client.get("/openapi.json").json()
    assert "/api/generate/edge-tts/tts" in spec["paths"]
    sa = spec["paths"]["/api/generate/streaming_audio"]["post"]
    assert "fallback_failed" in str(sa.get("responses", {}))
    assert "degraded" in str(sa.get("responses", {}))
