"""P0-1 回归：SSE 段内真增量输出与 ``prefer_streaming=False`` 回退。

旧实现把 ``model.generate_streaming()`` 先 ``list()`` 再 ``concatenate``，
所以 SSE 的首个 ``event: audio`` 仍要等整段推理结束。本测试用“每段产出
3 个小块”的桩模型锁定三件事：

1. 异步桥接生成器逐个产出块，不提前合并；
2. SSE 在**一个文本段**内下发多个 ``audio`` 事件，且合并后的 WAV 帧数等于
   所有块帧数之和（不丢块、不重复）；
3. ``prefer_streaming=false`` 走 ``generate`` 整段回退，只发一个 ``audio``。
"""

from __future__ import annotations

import base64
import io
import json
import threading
import wave

import numpy as np
import pytest

from integrated_app.model_registry import registry
from integrated_app.routes import audio as audio_module
from integrated_app.routes.generate.voxcpm2 import streaming as streaming_module


def _parse_sse_events(body: str) -> list[tuple[str, str]]:
    """把 ``event:`` / ``data:`` 两行一组解析为 (事件名, 数据) 列表。"""
    events: list[tuple[str, str]] = []
    event = ""
    data: list[str] = []
    for line in body.splitlines():
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:") :].lstrip())
        elif line == "" and event and data:
            events.append((event, "\n".join(data)))
            event, data = "", []
    return events


class _ChunkedStub:
    """模拟 VoxCPM2：``generate_streaming`` 每次推理产出一个小块。"""

    def __init__(self) -> None:
        self.stream_calls = 0
        self.generate_calls = 0

    def generate_streaming(self, **_kwargs):
        self.stream_calls += 1
        for value in (0.1, 0.2, 0.3):
            yield np.full(1600, value, dtype=np.float32)

    def generate(self, **_kwargs):
        self.generate_calls += 1
        return np.full(4800, 0.1, dtype=np.float32)


class _NoStreamingStub:
    """没有 ``generate_streaming`` 的模型，验证整段回退路径。"""

    def __init__(self) -> None:
        self.generate_calls = 0

    def generate(self, **_kwargs):
        self.generate_calls += 1
        return np.full(4800, 0.1, dtype=np.float32)


@pytest.mark.asyncio
async def test_iter_segment_chunks_yields_each_chunk(monkeypatch):
    stub = _ChunkedStub()
    monkeypatch.setattr(registry, "voxcpm_model", stub)

    chunks = [chunk async for chunk in streaming_module._iter_segment_chunks_async("短文本", None, 2.0, 10, True)]

    assert len(chunks) == 3
    assert [round(float(chunk[0]), 3) for chunk in chunks] == [0.1, 0.2, 0.3]
    assert stub.stream_calls == 1
    assert stub.generate_calls == 0


@pytest.mark.asyncio
async def test_first_chunk_is_available_before_generator_finishes(monkeypatch):
    """首块必须在整段推理结束前就能被消费，证明没有退回 ``list()`` 缓冲。"""
    release_second = threading.Event()

    class _GatedStub:
        def generate_streaming(self, **_kwargs):
            yield np.full(1600, 0.1, dtype=np.float32)
            release_second.wait(timeout=2.0)
            yield np.full(1600, 0.2, dtype=np.float32)

    monkeypatch.setattr(registry, "voxcpm_model", _GatedStub())
    agen = streaming_module._iter_segment_chunks_async("短文本", None, 2.0, 10, True)
    try:
        first = await anext(agen)
        assert len(first) == 1600
        assert not release_second.is_set(), "首块被整段缓冲，工作线程已越过闸门"
        release_second.set()
        second = await anext(agen)
        assert len(second) == 1600
    finally:
        release_second.set()
        await agen.aclose()


@pytest.mark.asyncio
async def test_iter_segment_chunks_falls_back_without_streaming(monkeypatch):
    stub = _NoStreamingStub()
    monkeypatch.setattr(registry, "voxcpm_model", stub)

    chunks = [chunk async for chunk in streaming_module._iter_segment_chunks_async("短文本", None, 2.0, 10, True)]

    assert len(chunks) == 1
    assert len(chunks[0]) == 4800
    assert stub.generate_calls == 1


def _post_sse(client, data: dict[str, str]):
    client.get("/")
    token = client.cookies.get("csrf_token") or ""
    headers = {"X-CSRF-Token": token} if token else {}
    return client.post("/api/generate/streaming_sse", data=data, headers=headers)


def test_sse_streams_multiple_audio_events_for_one_segment(client, monkeypatch, tmp_path):
    stub = _ChunkedStub()
    monkeypatch.setattr(registry, "voxcpm_model", stub)
    monkeypatch.setattr(streaming_module, "SAVE_DIR", str(tmp_path))
    monkeypatch.setattr(audio_module, "SAVE_DIR", str(tmp_path))

    resp = _post_sse(
        client,
        {"text": "短文本", "persona_name": "", "lang": "Auto", "prefer_streaming": "true"},
    )
    assert resp.status_code == 200, resp.text[:400]

    events = _parse_sse_events(resp.text)
    kinds = [kind for kind, _ in events]
    audio_payloads = [payload for kind, payload in events if kind == "audio"]
    assert len(audio_payloads) == 3, f"一个文本段应下发 3 个块，实际 {kinds}"
    assert stub.stream_calls == 1
    assert stub.generate_calls == 0

    for payload in audio_payloads:
        raw = base64.b64decode(payload)
        assert len(raw) == 1600 * 2

    done = json.loads(dict(events)["done"])
    wav = client.get(f"/api/audio/{done['filename']}")
    assert wav.status_code == 200
    with wave.open(io.BytesIO(wav.content), "rb") as wf:
        assert wf.getnframes() == 3 * 1600


def test_sse_prefer_streaming_false_uses_whole_segment_fallback(client, monkeypatch, tmp_path):
    stub = _ChunkedStub()
    monkeypatch.setattr(registry, "voxcpm_model", stub)
    monkeypatch.setattr(streaming_module, "SAVE_DIR", str(tmp_path))
    monkeypatch.setattr(audio_module, "SAVE_DIR", str(tmp_path))

    resp = _post_sse(
        client,
        {"text": "短文本", "persona_name": "", "lang": "Auto", "prefer_streaming": "false"},
    )
    assert resp.status_code == 200, resp.text[:400]

    events = _parse_sse_events(resp.text)
    kinds = [kind for kind, _ in events]
    assert kinds.count("audio") == 1
    assert "done" in kinds
    assert stub.generate_calls == 1
    assert stub.stream_calls == 0
