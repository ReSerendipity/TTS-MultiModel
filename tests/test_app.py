def test_app_creation(app):
    assert app.title == "TTS MultiModel Voice Studio"


def test_health_ping(client):
    response = client.get("/api/health/ping")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data


def test_health_ready(client):
    response = client.get("/api/health/ready")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "models_available" in data
    assert "loading" in data
    assert "progress" in data


def test_auth_middleware_disabled(client):
    response = client.get("/api/health/ping")
    assert response.status_code == 200
