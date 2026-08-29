"""Tests for the top-level application server and static asset handling."""


def test_health_ping(client):
    """The lightweight liveness probe returns ok."""
    response = client.get("/api/health/ping")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data


def test_static_cache_headers(client):
    """Static assets are served with no-cache to ensure fresh content on reload."""
    response = client.get("/static/css/main.css")
    assert response.status_code == 200
    cache_control = response.headers.get("Cache-Control", "")
    assert "no-cache" in cache_control
