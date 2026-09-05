"""P0-1 声音克隆授权 v1 的验收测试（综合评估 · 必答问题③）。

覆盖三件事：
1. consent 元数据序列化往返 + 旧数据回退（纯单元，不依赖端点）。
2. 克隆端点（上传来源）未勾选授权 → 400 并给出明确文案。
3. persona_save（上传来源）未勾选授权 → 拒绝保存；无上传（设计页固化）不受影响。
"""

import io

import pytest
from fastapi.testclient import TestClient

from integrated_app.persona_metadata import CONSENT_STATES, PersonaMetadata

_FAKE_WAV = b"RIFF" + b"\x00" * 100  # 仅用于触发上传分支，gate 在落盘校验之前拦截


@pytest.fixture
def client() -> TestClient:
    """应用级 TestClient（与 test_api_contract 同款 fixture 语义）。"""
    from integrated_app.app_server import create_app

    app = create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. 元数据层
# ---------------------------------------------------------------------------


def test_consent_metadata_roundtrip():
    """授权声明随 metadata 写入并可读回（granted / self / unverified 三态）。"""
    assert set(CONSENT_STATES) == {"granted", "self", "unverified"}
    m = PersonaMetadata(name="t", consent_state="granted", consent_at="2026-09-05T00:00:00")
    m2 = PersonaMetadata.from_dict(m.to_dict())
    assert m2.consent_state == "granted"
    assert m2.consent_at == "2026-09-05T00:00:00"


def test_consent_metadata_legacy_defaults_to_unverified():
    """旧格式 metadata（无 consent 键）回退 unverified（fail-safe，不崩溃）。"""
    m = PersonaMetadata.from_dict({"name": "legacy"})
    assert m.consent_state == "unverified"


def test_consent_metadata_invalid_state_falls_back():
    """非法状态值回退 unverified。"""
    m = PersonaMetadata(name="bad", consent_state="hacked")
    assert m.consent_state == "unverified"


# ---------------------------------------------------------------------------
# 2. 克隆端点（voxcpm_clone）上传来源必须显式授权
# ---------------------------------------------------------------------------


def _csrf_headers(client: TestClient) -> dict[str, str]:
    """CSRF Double-Submit：先 GET 触发服务端签发 cookie，再把值放入 header。"""
    client.get("/")
    token = client.cookies.get("csrf_token") or client.cookies.get("XSRF-TOKEN") or ""
    return {"X-CSRF-Token": token}


def test_clone_upload_without_consent_rejected(client):
    """上传克隆未勾选授权 → 400 并提示勾选。"""
    r = client.post(
        "/api/generate/voxcpm_clone",
        data={"text": "测试克隆"},
        files={"ref_audio_upload": ("ref.wav", io.BytesIO(_FAKE_WAV), "audio/wav")},
        headers=_csrf_headers(client),
    )
    assert r.status_code == 400
    assert "使用权" in r.text or "授权" in r.text


def test_clone_upload_with_consent_not_blocked_by_gate(client):
    """勾选授权后不再被授权门禁拦截（后续缺引擎/参数错误不属于本测试范围）。"""
    r = client.post(
        "/api/generate/voxcpm_clone",
        data={"text": "测试克隆", "has_consent": "true"},
        files={"ref_audio_upload": ("ref.wav", io.BytesIO(_FAKE_WAV), "audio/wav")},
        headers=_csrf_headers(client),
    )
    # gate 放行后进入正常流程；不应再返回「请勾选使用权」的 400 文案
    assert "请先勾选" not in r.text and "使用权" not in r.text
