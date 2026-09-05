"""Tests for /readyz k8s-style readiness gate.

Covers P1-1 (backend design assessment): the /readyz endpoint must act as a
real traffic gate — 503 when no engine is loaded, 200 only when model_loaded
is True.  Unlike /api/health/ready (which returns 200 even in degraded state),
/readyz is intended for k8s readinessProbe.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_readyz_returns_503_when_model_not_loaded(client: TestClient) -> None:
    """Default offline test environment has no engine loaded → gate at 503."""
    resp = client.get("/readyz")
    assert resp.status_code == 503, f"expected 503, got {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert body.get("model_loaded") is False


def test_readyz_returns_200_when_model_loaded(client: TestClient, monkeypatch) -> None:
    """When system_ready reports model_loaded=True, /readyz must pass with 200."""
    from integrated_app.routes.system import health

    async def _fake_ready() -> dict:
        return {
            "status": "ready",
            "model_loaded": True,
            "current_engine": "voxcpm2",
            "engines": {},
        }

    monkeypatch.setattr(health, "ready", _fake_ready)
    resp = client.get("/readyz")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text[:200]}"
    body = resp.json()
    assert body.get("model_loaded") is True


def test_readyz_response_body_is_health_report(client: TestClient) -> None:
    """503 response body must carry the full health report (not an empty error)."""
    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    # health report always carries status + model_loaded keys
    assert "status" in body
    assert "model_loaded" in body
