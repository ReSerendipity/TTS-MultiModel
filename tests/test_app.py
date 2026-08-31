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
    # 2026-08-31 同步新契约：/api/health/ready 委托给 routes/system/health.ready，
    # 返回深度探针结构（model_loaded/db_connected/gpu_available），
    # 替代旧的 models_available/loading/progress 三段式。
    assert "model_loaded" in data
    assert "db_connected" in data
    assert "gpu_available" in data


def test_auth_middleware_disabled(client):
    response = client.get("/api/health/ping")
    assert response.status_code == 200
