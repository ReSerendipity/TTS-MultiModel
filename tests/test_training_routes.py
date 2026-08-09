"""routes/audio 与 routes/training 路由补充测试 — 精确状态码断言。

覆盖目标模块: bin/integrated_app/routes/audio.py / training.py
"""


class TestAudioRoutes:
    def test_get_generated_audio_missing(self, client):
        response = client.get("/api/audio/no_such_file_xyz.wav")
        assert response.status_code == 404

    def test_get_persona_audio_missing(self, client):
        response = client.get("/api/persona/audio/no_such_persona_xyz")
        assert response.status_code == 404

    def test_speaker_sample_missing(self, client):
        response = client.get("/api/speaker/sample/no_such_key_xyz")
        assert response.status_code == 404

    def test_history_table(self, client):
        response = client.get("/api/history/table")
        assert response.status_code == 200


class TestTrainingRoutes:
    def test_training_log(self, client):
        response = client.get("/api/training/log")
        assert response.status_code == 200

    def test_training_stop_noop(self, client):
        # 未在训练时停止 — 无 CSRF token 返回 403
        response = client.post("/api/training/stop")
        assert response.status_code == 403

    def test_validate_path_helper(self, tmp_path):
        from integrated_app.routes.training import _validate_path

        base = str(tmp_path)
        path = _validate_path(base, "config.yaml")
        assert path == str(tmp_path / "config.yaml")

        import pytest

        with pytest.raises(ValueError):
            _validate_path(base, "../outside.yaml")

    def test_validate_training_params(self):
        from integrated_app.routes.training import _validate_training_params

        errors = _validate_training_params({})
        assert isinstance(errors, list)
