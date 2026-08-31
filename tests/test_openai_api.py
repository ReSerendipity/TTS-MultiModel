"""Smoke tests for the OpenAI-compatible API module.

Covers:
- SpeechRequest / BatchSpeechRequest model validation
- TaskCancelManager lifecycle (register / cancel / unregister / cancel_all)
- BatchGenerationManager state transitions
- OpenAICompatibleRouter endpoint responses (model list, speech 503 on no engine, batch submit/status/cancel)
- Audio format conversion helper
"""

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from integrated_app.openai_api import (
    _MODEL_ENGINE_MAP,
    _VOICE_PERSONA_MAP,
    BatchGenerationManager,
    BatchSpeechRequest,
    OpenAICompatibleRouter,
    SpeechRequest,
    TaskCancelManager,
    _convert_audio_format,
    _stream_file,
    openai_router,
)

# ---------------------------------------------------------------------------
# Module-level fixture for OpenAI router tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def openai_client():
    """Create a TestClient with only the OpenAI router (no CSRF/middleware)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(openai_router.router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


class TestSpeechRequestValidation:
    """Test SpeechRequest pydantic model validation."""

    def test_default_values(self):
        req = SpeechRequest(input="hello")
        assert req.model == "tts-1"
        assert req.voice == "alloy"
        assert req.response_format == "wav"
        assert req.speed == 1.0

    def test_model_normalization(self):
        req = SpeechRequest(input="hello", model="TTS-1")
        assert req.model == "tts-1"

    def test_invalid_model(self):
        with pytest.raises(Exception, match="未知模型"):
            SpeechRequest(input="hello", model="gpt-4")

    def test_format_normalization(self):
        req = SpeechRequest(input="hello", response_format="WAV")
        assert req.response_format == "wav"

    def test_invalid_format(self):
        with pytest.raises(Exception, match="不支持的音频格式"):
            SpeechRequest(input="hello", response_format="flac")

    def test_speed_range_validation(self):
        with pytest.raises(Exception):
            SpeechRequest(input="hello", speed=0.1)
        with pytest.raises(Exception):
            SpeechRequest(input="hello", speed=5.0)

    def test_input_length_validation(self):
        with pytest.raises(Exception):
            SpeechRequest(input="")
        with pytest.raises(Exception):
            SpeechRequest(input="x" * 4097)

    def test_engine_map(self):
        assert _MODEL_ENGINE_MAP["tts-1"] == "voxcpm2"
        assert _MODEL_ENGINE_MAP["tts-1-hd"] == "indextts2"

    def test_voice_persona_map(self):
        assert "alloy" in _VOICE_PERSONA_MAP
        assert "shimmer" in _VOICE_PERSONA_MAP


class TestBatchSpeechRequestValidation:
    """Test BatchSpeechRequest pydantic model validation."""

    def test_valid_batch(self):
        req = BatchSpeechRequest(texts=["hello", "world"])
        assert len(req.texts) == 2
        assert req.model == "tts-1"

    def test_empty_texts(self):
        with pytest.raises(Exception):
            BatchSpeechRequest(texts=[])

    def test_too_many_texts(self):
        with pytest.raises(Exception):
            BatchSpeechRequest(texts=["x"] * 101)


# ---------------------------------------------------------------------------
# TaskCancelManager
# ---------------------------------------------------------------------------


class TestTaskCancelManager:
    """Test TaskCancelManager lifecycle."""

    def test_register_returns_id(self):
        mgr = TaskCancelManager()
        task_id = mgr.register()
        assert isinstance(task_id, str) and len(task_id) > 0

    def test_register_with_custom_id(self):
        mgr = TaskCancelManager()
        task_id = mgr.register("custom-id")
        assert task_id == "custom-id"

    def test_unregister(self):
        mgr = TaskCancelManager()
        task_id = mgr.register()
        assert mgr.unregister(task_id) is True
        assert mgr.unregister(task_id) is False  # Already removed

    def test_cancel_task(self):
        mgr = TaskCancelManager()
        task_id = mgr.register()
        assert mgr.cancel_task(task_id) is True
        assert mgr.is_cancelled(task_id) is True

    def test_cancel_nonexistent(self):
        mgr = TaskCancelManager()
        assert mgr.cancel_task("nonexistent") is False

    def test_get_active_count(self):
        mgr = TaskCancelManager()
        assert mgr.get_active_count() == 0
        mgr.register()
        mgr.register()
        assert mgr.get_active_count() == 2

    def test_get_active_task_ids(self):
        mgr = TaskCancelManager()
        mgr.register("task-1")
        mgr.register("task-2")
        ids = mgr.get_active_task_ids()
        assert "task-1" in ids and "task-2" in ids

    def test_cancel_all(self):
        mgr = TaskCancelManager()
        mgr.register("a")
        mgr.register("b")
        count = mgr.cancel_all()
        assert count == 2
        assert mgr.is_cancelled("a") is True
        assert mgr.is_cancelled("b") is True


# ---------------------------------------------------------------------------
# BatchGenerationManager
# ---------------------------------------------------------------------------


class TestBatchGenerationManager:
    """Test BatchGenerationManager state transitions."""

    def test_max_concurrent_clamped(self):
        mgr = BatchGenerationManager(max_concurrent=10)
        assert mgr._max_concurrent == 4  # Clamped to 4

    def test_max_concurrent_minimum(self):
        mgr = BatchGenerationManager(max_concurrent=0)
        assert mgr._max_concurrent == 1  # Clamped to 1

    @pytest.mark.asyncio
    async def test_submit_batch_initial_status(self):
        mgr = BatchGenerationManager(max_concurrent=1)
        batch_id = await mgr.submit_batch(["hello"], {"model": "tts-1"})
        status = mgr.get_batch_status(batch_id)
        assert status is not None
        assert status["total"] == 1
        assert status["status"] in ("pending", "in_progress", "completed", "failed", "partial")

    def test_get_status_nonexistent(self):
        mgr = BatchGenerationManager()
        assert mgr.get_batch_status("nonexistent") is None

    def test_get_results_nonexistent(self):
        mgr = BatchGenerationManager()
        assert mgr.get_batch_results("nonexistent") is None

    def test_cancel_nonexistent_batch(self):
        mgr = BatchGenerationManager()
        assert mgr.cancel_batch("nonexistent") is False

    def test_cleanup_nonexistent(self):
        mgr = BatchGenerationManager()
        assert mgr.cleanup_batch("nonexistent") is False

    @pytest.mark.asyncio
    async def test_cleanup_existing(self):
        mgr = BatchGenerationManager(max_concurrent=1)
        batch_id = await mgr.submit_batch(["hello"], {"model": "tts-1"})
        assert mgr.cleanup_batch(batch_id) is True
        assert mgr.get_batch_status(batch_id) is None

    @pytest.mark.asyncio
    async def test_list_batches(self):
        mgr = BatchGenerationManager(max_concurrent=1)
        await mgr.submit_batch(["a"], {"model": "tts-1"})
        await mgr.submit_batch(["b"], {"model": "tts-1"})
        batches = mgr.list_batches()
        assert len(batches) == 2


# ---------------------------------------------------------------------------
# OpenAICompatibleRouter endpoints
# ---------------------------------------------------------------------------


class TestOpenAIRouterEndpoints:
    """Test OpenAI-compatible API router endpoints via isolated TestClient.

    Uses a dedicated FastAPI app that only includes the OpenAI router,
    avoiding CSRF/auth middleware that would block test requests.
    """

    def test_list_models(self, openai_client):
        """GET /v1/models should return tts-1 and tts-1-hd."""
        resp = openai_client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        ids = [m["id"] for m in data["data"]]
        assert "tts-1" in ids
        assert "tts-1-hd" in ids

    def test_create_speech_no_model_loaded(self, openai_client):
        """POST /v1/audio/speech should return 503 when no model is loaded."""
        resp = openai_client.post(
            "/v1/audio/speech",
            json={"input": "hello", "model": "tts-1", "voice": "alloy"},
        )
        assert resp.status_code == 503

    def test_create_speech_invalid_model(self, openai_client):
        """POST /v1/audio/speech with invalid model should return 422."""
        resp = openai_client.post(
            "/v1/audio/speech",
            json={"input": "hello", "model": "gpt-4", "voice": "alloy"},
        )
        assert resp.status_code == 422

    def test_create_speech_invalid_format(self, openai_client):
        """POST /v1/audio/speech with invalid format should return 422."""
        resp = openai_client.post(
            "/v1/audio/speech",
            json={"input": "hello", "model": "tts-1", "response_format": "flac"},
        )
        assert resp.status_code == 422

    def test_batch_submit_and_status(self, openai_client):
        """POST /v1/audio/speech/batch then GET status."""
        resp = openai_client.post(
            "/v1/audio/speech/batch",
            json={"texts": ["hello", "world"], "model": "tts-1"},
        )
        assert resp.status_code == 200
        batch_id = resp.json()["batch_id"]

        status_resp = openai_client.get(f"/v1/audio/speech/batch/{batch_id}")
        assert status_resp.status_code == 200
        assert status_resp.json()["total"] == 2

    def test_batch_status_not_found(self, openai_client):
        """GET non-existent batch should return 404."""
        resp = openai_client.get("/v1/audio/speech/batch/nonexistent-id")
        assert resp.status_code == 404

    def test_batch_cancel_not_found(self, openai_client):
        """DELETE non-existent batch should return 404."""
        resp = openai_client.delete("/v1/audio/speech/batch/nonexistent-id")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Audio format conversion helper
# ---------------------------------------------------------------------------


class TestAudioFormatConversion:
    """Test _convert_audio_format and _stream_file helpers."""

    def test_wav_returns_same_path(self):
        assert _convert_audio_format("/tmp/test.wav", "wav") == "/tmp/test.wav"

    def test_stream_file_reads_chunks(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(b"hello world" * 100)
            f.flush()
            path = f.name

        try:
            chunks = list(_stream_file(path, chunk_size=100))
            assert b"".join(chunks) == b"hello world" * 100
        finally:
            os.unlink(path)

    def test_stream_file_empty_file(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            path = f.name

        try:
            chunks = list(_stream_file(path))
            assert chunks == []
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Router static methods
# ---------------------------------------------------------------------------


class TestRouterStaticMethods:
    """Test OpenAICompatibleRouter static generation methods."""

    def test_generate_voxcpm2_no_engine(self):
        """_generate_voxcpm2 should return None on exception."""
        body = SpeechRequest(input="hello")
        engine = MagicMock()
        engine.generate_voice_clone.side_effect = RuntimeError("no model")
        result = OpenAICompatibleRouter._generate_voxcpm2(engine, body)
        assert result is None

    def test_generate_voxcpm2_success(self):
        """_generate_voxcpm2 should return path on success.

        VoxCPM2 实际返回结构为 ``((sample_rate, wav, filename), message)``，
        且实现会校验路径真实存在后才返回（修复 500「音频生成失败」的整改）。
        mock 需符合该契约，并让返回的 basename 指向一个真实存在的临时文件。
        """
        body = SpeechRequest(input="hello")
        engine = MagicMock()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
            f.write(b"RIFF" + b"\x00" * 40)
            out_path = f.name
        try:
            engine.generate_voice_clone.return_value = ((24000, [], out_path), "ok")
            result = OpenAICompatibleRouter._generate_voxcpm2(engine, body)
            assert result == out_path
        finally:
            os.unlink(out_path)

    def test_generate_indextts2_no_engine(self):
        """_generate_indextts2 should return None on exception."""
        body = SpeechRequest(input="hello", model="tts-1-hd")
        engine = MagicMock()
        engine.infer.side_effect = RuntimeError("no model")
        result = OpenAICompatibleRouter._generate_indextts2(engine, body)
        assert result is None

    def test_generate_indextts2_success(self):
        """_generate_indextts2 should return path on success."""
        body = SpeechRequest(input="hello", model="tts-1-hd")
        engine = MagicMock()
        engine.infer.return_value = "/tmp/out.wav"
        result = OpenAICompatibleRouter._generate_indextts2(engine, body)
        assert result == "/tmp/out.wav"
