#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI 远程传输适配层（cli_remote）的单元测试——全部离线 mock，不起真服务。

覆盖：
    - 端口文件解析（数字 / 非数字 / 空文件 / 不存在）；
    - 服务发现优先级（显式 --server > TTS_CLI_SERVER > .server_port）与回落告警；
    - CSRF 握手与 X-CSRF-Token 头注入（含 403 拒绝后重握手重试一次）；
    - data-audio-filename 提取（含 HTML 转义、缺失属性时明确报错）；
    - engine_not_ready → 自动 POST /api/model/load → 重试一次 → 仍失败报错；
    - 超时不重试（避免重复生成）；
    - generate 表单字段映射（denoise/norm 显式对齐、seed 哨兵换算、文件上传）；
    - run_generate 的 Result 映射（USAGE_ERROR / NOT_READY / BUSINESS_ERROR / 成功同构）；
    - run_batch_tasks 的 --resume 跳过与单条失败不中断；
    - --no-remote 强制直调（不触发服务发现）。
"""

from __future__ import annotations

import contextlib
import email.message
import io
import json
import sys
import urllib.error
from http.cookiejar import Cookie
from types import SimpleNamespace

import pytest

from integrated_app import cli_remote
from integrated_app.cli_output import EXIT_BUSINESS_ERROR, EXIT_NOT_READY, EXIT_OK, Result

# ---------------------------------------------------------------------------
# 离线 HTTP 假件：FakeOpener 模拟 urllib opener 的应答序列
# ---------------------------------------------------------------------------


class _FakeHeaders:
    def __init__(self, headers: dict[str, str] | None = None):
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}

    def get(self, name: str, default: str = "") -> str:
        return self._headers.get(name.lower(), default)


class FakeResponse:
    """urllib 响应假件（支持 with 上下文）。"""

    def __init__(self, status: int = 200, body: bytes = b"", content_type: str = "text/html"):
        self.status = status
        self.body = body
        self.headers = _FakeHeaders({"Content-Type": content_type})

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(status: int, body: bytes, content_type: str = "text/html") -> urllib.error.HTTPError:
    """构造真实 HTTPError（验证 _request 对非 2xx 的 body 保留逻辑）。"""
    hdrs = email.message.Message()
    hdrs["Content-Type"] = content_type
    return urllib.error.HTTPError("http://fake", status, "err", hdrs, io.BytesIO(body))


class FakeOpener:
    """按序弹出应答的 opener 假件；应答可以是 FakeResponse / HTTPError / Exception。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[tuple[str, str, object]] = []

    def open(self, req, timeout=None):  # noqa: ARG002 - 签名对齐 urllib
        self.requests.append((req.get_method(), req.full_url, req))
        if not self.responses:
            raise AssertionError("FakeOpener 应答耗尽（请求次数超出预期）")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def csrf_header_of_last(self) -> str | None:
        _, _, req = self.requests[-1]
        return req.get_header("X-csrf-token")


def _csrf_cookie(value: str = "tok123") -> Cookie:
    return Cookie(
        version=0,
        name="csrf_token",
        value=value,
        port=None,
        port_specified=False,
        domain="127.0.0.1",
        domain_specified=False,
        domain_initial_dot=False,
        path="/",
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
    )


_PING_OK = FakeResponse(200, b"pong", "application/json")
_WAV_BYTES = b"RIFFfakewav"


def _success_html(filename: str = "voxcpm_design_1.wav", message: str = "生成成功") -> FakeResponse:
    body = (
        f'<div data-audio-filename="{filename}">'
        f'<audio class="tts-audio-hidden" src="/api/audio/{filename}"></audio>'
        f'<div class="status-message success">{message}</div></div>'
    ).encode()
    return FakeResponse(200, body, "text/html")


def _error_html(error_type: str, message: str, status: int = 400) -> FakeResponse:
    body = (
        f'<div class="tts-error-block" data-error-type="{error_type}"><div class="error-message">{message}</div></div>'
    ).encode()
    return FakeResponse(status, body, "text/html")


def _json_response(payload: dict, status: int = 200) -> FakeResponse:
    return FakeResponse(status, json.dumps(payload).encode("utf-8"), "application/json")


def _client(responses) -> tuple[cli_remote.RemoteClient, FakeOpener]:
    client = cli_remote.RemoteClient("http://127.0.0.1:7999")
    opener = FakeOpener(responses)
    client._opener = opener
    return client, opener


# ---------------------------------------------------------------------------
# 端口文件解析
# ---------------------------------------------------------------------------


class TestPortFile:
    def test_valid_port(self, tmp_path):
        p = tmp_path / ".server_port"
        p.write_text("7901", encoding="utf-8")
        assert cli_remote._read_port_from_file(str(p)) == 7901

    def test_whitespace_and_newline(self, tmp_path):
        p = tmp_path / ".server_port"
        p.write_text("  7901\n", encoding="utf-8")
        assert cli_remote._read_port_from_file(str(p)) == 7901

    def test_non_numeric_ignored(self, tmp_path):
        p = tmp_path / ".server_port"
        p.write_text("not-a-port", encoding="utf-8")
        assert cli_remote._read_port_from_file(str(p)) is None

    def test_empty_file_ignored(self, tmp_path):
        p = tmp_path / ".server_port"
        p.write_text("", encoding="utf-8")
        assert cli_remote._read_port_from_file(str(p)) is None

    def test_zero_and_garbage_ignored(self, tmp_path):
        p = tmp_path / ".server_port"
        p.write_text("0", encoding="utf-8")
        assert cli_remote._read_port_from_file(str(p)) is None

    def test_missing_file_ignored(self, tmp_path):
        assert cli_remote._read_port_from_file(str(tmp_path / "nope")) is None

    def test_candidates_first_matches_writer_formula(self):
        candidates = cli_remote._port_file_candidates()
        assert candidates, "至少一个候选"
        assert candidates[0].endswith("app/.server_port") or candidates[0].endswith("app\\.server_port")
        assert len(candidates) == len(set(candidates)), "候选不得重复"


# ---------------------------------------------------------------------------
# 服务发现
# ---------------------------------------------------------------------------


class TestDetectServer:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        """隔离真实端口文件与环境变量，避免本机正在跑的服务影响断言。"""
        monkeypatch.setattr(cli_remote, "_port_file_candidates", lambda: [])
        monkeypatch.delenv("TTS_CLI_SERVER", raising=False)
        self.tmp_path = tmp_path

    def test_explicit_url_probe_ok(self, monkeypatch):
        monkeypatch.setattr(cli_remote, "_probe_health", lambda base, timeout=2.0: base == "http://127.0.0.1:7001")
        assert cli_remote.detect_server("http://127.0.0.1:7001") == "http://127.0.0.1:7001"

    def test_env_var_used_without_explicit(self, monkeypatch):
        monkeypatch.setenv("TTS_CLI_SERVER", "http://127.0.0.1:7002")
        monkeypatch.setattr(cli_remote, "_probe_health", lambda base, timeout=2.0: base == "http://127.0.0.1:7002")
        assert cli_remote.detect_server(None) == "http://127.0.0.1:7002"

    def test_explicit_beats_env(self, monkeypatch):
        monkeypatch.setenv("TTS_CLI_SERVER", "http://127.0.0.1:7002")
        monkeypatch.setattr(cli_remote, "_probe_health", lambda base, timeout=2.0: base == "http://127.0.0.1:7001")
        assert cli_remote.detect_server("http://127.0.0.1:7001") == "http://127.0.0.1:7001"

    def test_port_file_discovery(self, monkeypatch):
        port_file = self.tmp_path / ".server_port"
        port_file.write_text("7901", encoding="utf-8")
        monkeypatch.setattr(cli_remote, "_port_file_candidates", lambda: [str(port_file)])
        monkeypatch.setattr(cli_remote, "_probe_health", lambda base, timeout=2.0: base == "http://127.0.0.1:7901")
        assert cli_remote.detect_server(None) == "http://127.0.0.1:7901"

    def test_no_candidates_silent_fallback(self, capsys):
        assert cli_remote.detect_server(None) is None
        captured = capsys.readouterr()
        assert "回落" not in captured.err, "无端口文件、无显式地址时应静默回落（不打警告）"

    def test_port_file_but_unreachable_warns(self, monkeypatch, capsys):
        port_file = self.tmp_path / ".server_port"
        port_file.write_text("7901", encoding="utf-8")
        monkeypatch.setattr(cli_remote, "_port_file_candidates", lambda: [str(port_file)])
        monkeypatch.setattr(cli_remote, "_probe_health", lambda base, timeout=2.0: False)
        assert cli_remote.detect_server(None) is None
        assert "无响应" in capsys.readouterr().err

    def test_explicit_unreachable_warns(self, monkeypatch, capsys):
        monkeypatch.setattr(cli_remote, "_probe_health", lambda base, timeout=2.0: False)
        assert cli_remote.detect_server("http://127.0.0.1:7003") is None
        assert "不可达" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# HTML 契约解析
# ---------------------------------------------------------------------------


class TestHtmlContract:
    def test_extract_filename_plain(self):
        assert cli_remote._extract_audio_filename('<div data-audio-filename="a_1.wav"></div>') == "a_1.wav"

    def test_extract_filename_html_escaped(self):
        assert cli_remote._extract_audio_filename('<div data-audio-filename="a&amp;b.wav"></div>') == "a&b.wav"

    def test_extract_filename_missing_raises(self):
        with pytest.raises(cli_remote.RemoteError) as excinfo:
            cli_remote._extract_audio_filename("<div>no attribute</div>")
        assert excinfo.value.error_type == "protocol"
        assert "data-audio-filename" in str(excinfo.value)

    def test_parse_error_block(self):
        error_type, message = cli_remote._parse_error_html(
            '<div class="tts-error-block" data-error-type="engine_not_ready">'
            '<div class="error-message">VoxCPM2 模型未加载，请先加载模型</div></div>'
        )
        assert error_type == "engine_not_ready"
        assert "模型未加载" in message

    def test_parse_error_block_missing_gives_general(self):
        error_type, message = cli_remote._parse_error_html("<div>something else</div>")
        assert error_type == "general"
        assert message

    def test_success_message_extraction(self):
        assert cli_remote._extract_success_message('<div class="status-message success">生成成功</div>') == "生成成功"
        assert cli_remote._extract_success_message("<div>none</div>") == "远程生成完成"


# ---------------------------------------------------------------------------
# RemoteClient：握手 / CSRF / 生成 / 补载重试
# ---------------------------------------------------------------------------


class TestRemoteClient:
    def test_happy_path_html(self):
        client, opener = _client([_PING_OK, _success_html("a.wav"), FakeResponse(200, _WAV_BYTES)])
        client._jar.set_cookie(_csrf_cookie("tok123"))
        client.handshake()
        filename, audio, message = client.generate_audio(
            "/api/generate/voxcpm_design", {"text": "hi", "norm": "false"}, None
        )
        assert filename == "a.wav"
        assert audio == _WAV_BYTES
        assert message == "生成成功"
        # 握手 + 生成 + 下载 共 3 个请求；POST 带 CSRF 头与表单 Content-Type
        assert len(opener.requests) == 3
        method, url, req = opener.requests[1]
        assert (method, url) == ("POST", "http://127.0.0.1:7999/api/generate/voxcpm_design")
        assert req.get_header("X-csrf-token") == "tok123"
        assert req.get_header("Content-type") == "application/x-www-form-urlencoded"

    def test_engine_not_ready_auto_load_then_retry(self):
        responses = [
            _PING_OK,
            _error_html("engine_not_ready", "VoxCPM2 模型未加载，请先加载模型"),
            _json_response({"status": "ok", "message": "加载完成", "engine": "voxcpm2"}),
            _success_html("b.wav"),
            FakeResponse(200, _WAV_BYTES),
        ]
        client, opener = _client(responses)
        client.handshake()
        filename, audio, _ = client.generate_audio(
            "/api/generate/voxcpm_design", {"text": "hi"}, None, load_engine="voxcpm2"
        )
        assert (filename, audio) == ("b.wav", _WAV_BYTES)
        methods_urls = [(m, u) for m, u, _ in opener.requests]
        assert methods_urls[2] == ("POST", "http://127.0.0.1:7999/api/model/load")
        # 重试的是同一个生成端点
        assert methods_urls[3][1].endswith("/api/generate/voxcpm_design")

    def test_engine_not_ready_twice_fails_not_ready(self):
        responses = [
            _PING_OK,
            _error_html("engine_not_ready", "未加载"),
            _json_response({"status": "ok", "message": "ok"}),
            _error_html("engine_not_ready", "仍未就绪"),
        ]
        client, opener = _client(responses)
        client.handshake()
        with pytest.raises(cli_remote.RemoteError) as excinfo:
            client.generate_audio("/api/generate/voxcpm_design", {"text": "hi"}, None, load_engine="voxcpm2")
        assert "仍未就绪" in str(excinfo.value)
        assert len(opener.requests) == 4, "补载重试只允许一次"

    def test_timeout_not_retried(self):
        client, opener = _client([_PING_OK, urllib.error.URLError(TimeoutError("timed out"))])
        client.handshake()
        with pytest.raises(cli_remote.RemoteError) as excinfo:
            client.generate_audio("/api/generate/voxcpm_design", {"text": "hi"}, None, load_engine="voxcpm2")
        assert excinfo.value.error_type == "timeout"
        assert len(opener.requests) == 2, "超时不得静默重试（避免重复生成）"

    def test_connection_refused_unreachable(self):
        client, opener = _client([_PING_OK, urllib.error.URLError(ConnectionRefusedError())])
        client.handshake()
        with pytest.raises(cli_remote.RemoteError) as excinfo:
            client.generate_audio("/api/generate/voxcpm_design", {"text": "hi"}, None, load_engine="voxcpm2")
        assert excinfo.value.error_type == "unreachable"
        assert len(opener.requests) == 2

    def test_json_endpoint_success(self):
        client, _ = _client(
            [
                _PING_OK,
                _json_response({"status": "ok", "filename": "e.wav", "size_bytes": 123}),
                FakeResponse(200, _WAV_BYTES),
            ]
        )
        client.handshake()
        filename, audio, message = client.generate_audio("/api/generate/edge-tts/tts", {"text": "hi"}, None)
        assert (filename, audio) == ("e.wav", _WAV_BYTES)
        assert "123" in message

    def test_json_endpoint_error_detail(self):
        client, _ = _client(
            [
                _PING_OK,
                _json_response(
                    {"status": "error", "error_type": "fallback_failed", "detail": "edge 不可用"}, status=503
                ),
            ]
        )
        client.handshake()
        with pytest.raises(cli_remote.RemoteError) as excinfo:
            client.generate_audio("/api/generate/edge-tts/tts", {"text": "hi"}, None)
        assert "edge 不可用" in str(excinfo.value)
        assert excinfo.value.status == 503

    def test_csrf_403_triggers_rehandshake_and_retry(self):
        responses = [
            _PING_OK,
            _http_error(403, b"CSRF token missing"),
            _PING_OK,
            _success_html("c.wav"),
            FakeResponse(200, _WAV_BYTES),
        ]
        client, opener = _client(responses)
        client._jar.set_cookie(_csrf_cookie("tok-old"))
        client.handshake()
        filename, _, _ = client.generate_audio("/api/generate/voxcpm_design", {"text": "hi"}, None)
        assert filename == "c.wav"
        assert len(opener.requests) == 5, "403(CSRF) → 重握手 → 重试一次"

    def test_200_without_filename_is_protocol_error(self):
        client, _ = _client([_PING_OK, FakeResponse(200, b"<div>unexpected</div>", "text/html")])
        client.handshake()
        with pytest.raises(cli_remote.RemoteError) as excinfo:
            client.generate_audio("/api/generate/voxcpm_design", {"text": "hi"}, None)
        assert excinfo.value.error_type == "protocol"

    def test_audio_download_404_raises(self):
        client, _ = _client([_PING_OK, _success_html("gone.wav"), _http_error(404, b"not found")])
        client.handshake()
        with pytest.raises(cli_remote.RemoteError) as excinfo:
            client.generate_audio("/api/generate/voxcpm_design", {"text": "hi"}, None)
        assert excinfo.value.status == 404

    def test_multipart_upload_content(self, tmp_path):
        wav = tmp_path / "p.wav"
        wav.write_bytes(b"WAVDATA")
        client, opener = _client([_PING_OK, _success_html("d.wav"), FakeResponse(200, _WAV_BYTES)])
        client.handshake()
        client.generate_audio(
            "/api/generate/voxcpm_prompt_continue",
            {"text": "hi", "prompt_text": "t"},
            [("prompt_wav", "p.wav", b"WAVDATA", "application/octet-stream")],
        )
        _, _, req = opener.requests[1]
        assert req.get_header("Content-type").startswith("multipart/form-data; boundary=")
        body = req.data
        assert b'name="prompt_wav"; filename="p.wav"' in body
        assert b"WAVDATA" in body
        assert b'name="text"' in body

    def test_load_model_failure_raises(self):
        client, _ = _client([_PING_OK, _json_response({"status": "error", "message": "显存不足"})])
        client.handshake()
        with pytest.raises(cli_remote.RemoteError) as excinfo:
            client.load_model("voxcpm2")
        assert "显存不足" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 表单字段映射
# ---------------------------------------------------------------------------


def _gen_args(**kwargs) -> SimpleNamespace:
    base = dict(
        text="hi",
        control=None,
        cfg_value=2.0,
        inference_timesteps=10,
        normalize=False,
        denoise=False,
        engine="voxcpm2",
        output=None,
        seed=-1,
        reference_audio=None,
        prompt_audio=None,
        prompt_text=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestBuildGenerateRequest:
    def test_design_fields(self):
        _, fields, files = cli_remote._build_generate_request(_gen_args(control="calm voice"), "design")
        assert files == []
        assert fields["text"] == "hi"
        assert fields["instruction"] == "calm voice"
        assert fields["cfg"] == 2.0
        assert fields["steps"] == 10
        assert fields["denoise"] == "false", "直调默认 denoise=False，必须显式对齐"
        assert fields["norm"] == "false"

    def test_design_normalize_true(self):
        _, fields, _ = cli_remote._build_generate_request(_gen_args(normalize=True), "design")
        assert fields["norm"] == "true"

    def test_clone_path_and_consent(self):
        _, fields, _ = cli_remote._build_generate_request(_gen_args(reference_audio="ref.wav"), "clone")
        assert fields["ref_audio_path"] == "ref.wav"
        assert fields["has_consent"] == "true"

    def test_ultimate_denoise_strength_mapping(self):
        _, fields, _ = cli_remote._build_generate_request(_gen_args(denoise_strength=0.5, ref_text="t"), "ultimate")
        assert fields["denoise_strength"] == 0.5
        assert fields["denoise"] == "true"
        _, fields_off, _ = cli_remote._build_generate_request(_gen_args(denoise_strength=0.0), "ultimate")
        assert fields_off["denoise"] == "false"

    def test_emotion_vector_and_seed_sentinel(self):
        args = _gen_args(
            engine="indextts2",
            emotion_vector="0.1,0.2,0,0,0,0,0,0.7",
            emotion_text="",
            emotion_alpha=0.9,
            duration_factor=1.5,
            lang=None,
        )
        _, fields, files = cli_remote._build_generate_request(args, "emotion")
        assert fields["emo_happy"] == 0.1
        assert fields["emo_calm"] == 0.7
        assert fields["emo_alpha"] == 0.9
        assert fields["duration_scale"] == 1.5
        assert fields["seed"] == 0, "服务端 0=随机（CLI 用 -1）"
        assert files == []

    def test_prompt_continue_uploads_file(self, tmp_path):
        wav = tmp_path / "prompt.wav"
        wav.write_bytes(b"PROMPTWAV")
        _, fields, files = cli_remote._build_generate_request(
            _gen_args(prompt_audio=str(wav), prompt_text="t"), "prompt-continue"
        )
        assert fields["prompt_text"] == "t"
        assert len(files) == 1
        name, filename, data, _ctype = files[0]
        assert (name, filename) == ("prompt_wav", "prompt.wav")
        assert data == b"PROMPTWAV"

    def test_breeze_seed_random(self):
        _, fields, _ = cli_remote._build_generate_request(_gen_args(cfg_scale=4.0), "breeze-design")
        assert fields["random_seed"] == "true"
        _, fields2, _ = cli_remote._build_generate_request(_gen_args(seed=42), "breeze-design")
        assert fields2["random_seed"] == "false"
        assert fields2["seed"] == 42

    def test_edge_fields(self):
        _, fields, files = cli_remote._build_generate_request(_gen_args(lang="zh"), "edge")
        assert fields == {"text": "hi", "lang": "zh"}
        assert files == []

    def test_convert_uploads_two_files(self, tmp_path):
        src = tmp_path / "s.wav"
        tgt = tmp_path / "t.wav"
        src.write_bytes(b"S")
        tgt.write_bytes(b"T")
        _, fields, files = cli_remote._build_generate_request(
            _gen_args(source_audio=str(src), target_audio=str(tgt), tau=0.4), "convert"
        )
        assert fields["tau"] == 0.4
        assert [f[0] for f in files] == ["source_audio", "target_audio"]


class TestSupportsRemote:
    def test_design_default_engine(self):
        assert cli_remote.supports_remote(_gen_args(), "design") is True

    def test_design_indextts2_falls_back(self):
        assert cli_remote.supports_remote(_gen_args(engine="indextts2"), "design") is False

    def test_script_with_persona_map_falls_back(self):
        args = _gen_args(persona_map=["旁白=n.wav"])
        assert cli_remote.supports_remote(args, "script") is False

    def test_script_without_persona_map(self):
        assert cli_remote.supports_remote(_gen_args(), "script") is True

    def test_emotion_always(self):
        assert cli_remote.supports_remote(_gen_args(engine="indextts2"), "emotion") is True


# ---------------------------------------------------------------------------
# run_generate：Result 映射（文件缺失 / 远端失败 / 成功同构）
# ---------------------------------------------------------------------------


class TestRunGenerate:
    def _patch_client(self, monkeypatch, fake):
        monkeypatch.setattr(cli_remote, "RemoteClient", lambda base_url: fake)

    def test_usage_error_when_reference_missing(self, monkeypatch):
        self._patch_client(monkeypatch, SimpleNamespace(handshake=lambda: None))
        args = _gen_args(reference_audio="missing.wav")
        result = cli_remote.run_generate(args, None, "ultimate", "http://x")
        assert result.ok is False
        assert result.error_code == "USAGE_ERROR"
        assert "参考音频不存在" in result.error

    def test_emotion_vector_validation(self, monkeypatch, tmp_path):
        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"RIFF")
        self._patch_client(monkeypatch, SimpleNamespace(handshake=lambda: None))
        args = _gen_args(engine="indextts2", emotion_vector="1,2,3", emotion_text=None, reference_audio=str(ref))
        result = cli_remote.run_generate(args, None, "emotion", "http://x")
        assert result.error_code == "USAGE_ERROR"
        assert "8 个值" in result.error

    def test_emotion_requires_source(self, monkeypatch, tmp_path):
        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"RIFF")
        self._patch_client(monkeypatch, SimpleNamespace(handshake=lambda: None))
        args = _gen_args(engine="indextts2", emotion_vector=None, emotion_text=None, reference_audio=str(ref))
        result = cli_remote.run_generate(args, None, "emotion", "http://x")
        assert result.error_code == "USAGE_ERROR"

    def test_remote_timeout_maps_to_business_error(self, monkeypatch):
        class FailingClient:
            def handshake(self):
                return None

            def generate_audio(self, *a, **k):
                raise cli_remote.RemoteError("超时", "timeout")

        self._patch_client(monkeypatch, FailingClient())
        result = cli_remote.run_generate(_gen_args(), None, "design", "http://x")
        assert result.ok is False
        assert result.error_code == "BUSINESS_ERROR"
        assert result.exit_code() == EXIT_BUSINESS_ERROR

    def test_remote_not_ready_maps_to_not_ready(self, monkeypatch):
        class NotReadyClient:
            def handshake(self):
                return None

            def generate_audio(self, *a, **k):
                raise cli_remote.RemoteError("仍未就绪", "engine_not_ready")

        self._patch_client(monkeypatch, NotReadyClient())
        result = cli_remote.run_generate(_gen_args(), None, "design", "http://x")
        assert result.ok is False
        assert result.error_code == "NOT_READY"
        assert result.exit_code() == EXIT_NOT_READY

    def test_success_contract(self, monkeypatch, tmp_path):
        import numpy as np
        import soundfile as sf

        buf = io.BytesIO()
        sf.write(buf, np.zeros(2205, dtype="float32"), 22050, format="WAV")
        wav_bytes = buf.getvalue()
        output = tmp_path / "out.wav"

        class OkClient:
            def handshake(self):
                return None

            def generate_audio(self, endpoint, fields, files, *, load_engine=None):
                return "voxcpm_design_1.wav", wav_bytes, "生成成功"

        self._patch_client(monkeypatch, OkClient())
        args = _gen_args(control="calm voice", output=str(output))
        result = cli_remote.run_generate(args, None, "design", "http://x")
        assert result.ok is True
        assert result.exit_code() == EXIT_OK
        assert result.command == "design"
        assert result.data["audio_path"] == str(output.resolve())
        assert output.is_file()
        assert result.data["transport"] == "http"
        assert result.data["engine"] == "voxcpm2"
        assert result.data["duration"] == pytest.approx(0.1, abs=0.01)
        assert result.data["params"]["instruction"] == "calm voice"


# ---------------------------------------------------------------------------
# run_batch_tasks：resume / 失败不中断 / mp3 分支
# ---------------------------------------------------------------------------


class TestRunBatchTasks:
    def _args(self, tmp_path, **kwargs) -> SimpleNamespace:
        base = dict(
            output_dir=str(tmp_path / "outs"),
            format="wav",
            resume=False,
            control=None,
            cfg_value=2.0,
            inference_timesteps=10,
            normalize=False,
            denoise=False,
            prompt_audio=None,
            reference_audio=None,
            engine=None,
        )
        base.update(kwargs)
        return SimpleNamespace(**base)

    def _patch_client(self, monkeypatch, fake):
        monkeypatch.setattr(cli_remote, "RemoteClient", lambda base_url: fake)

    def test_batch_generates_all_tasks(self, monkeypatch, tmp_path):
        calls: list[tuple[str, dict]] = []

        class FakeClient:
            def handshake(self):
                return None

            def generate_audio(self, endpoint, fields, files, *, load_engine=None):
                calls.append((endpoint, dict(fields)))
                return f"f{len(calls)}.wav", _WAV_BYTES, "生成成功"

        self._patch_client(monkeypatch, FakeClient())
        args = self._args(tmp_path)
        cli_remote.run_batch_tasks(args, [{"text": "a"}, {"text": "b"}], None, "http://x")
        outs = sorted(p.name for p in (tmp_path / "outs").iterdir())
        assert outs == ["output_001.wav", "output_002.wav"]
        assert calls[0][0].endswith("/api/generate/voxcpm_design")
        # 复用同一 client：两任务都发到同一端点（fake 记录即可）
        assert len(calls) == 2

    def test_batch_reference_uses_clone_endpoint(self, monkeypatch, tmp_path):
        calls = []

        class FakeClient:
            def handshake(self):
                return None

            def generate_audio(self, endpoint, fields, files, *, load_engine=None):
                calls.append((endpoint, dict(fields)))
                return "x.wav", _WAV_BYTES, "生成成功"

        self._patch_client(monkeypatch, FakeClient())
        args = self._args(tmp_path, reference_audio="ref.wav", control="calm voice", denoise=True)
        cli_remote.run_batch_tasks(args, [{"text": "a"}], None, "http://x")
        endpoint, fields = calls[0]
        assert endpoint.endswith("/api/generate/voxcpm_clone")
        assert fields["instruction"] == "calm voice"
        assert fields["denoise"] == "true", "denoise 仅在有 prompt/reference 音频时生效"
        assert fields["ref_audio_path"] == "ref.wav"

    def test_batch_resume_skips_existing(self, monkeypatch, tmp_path):
        out_dir = tmp_path / "outs"
        out_dir.mkdir()
        (out_dir / "output_001.wav").write_bytes(_WAV_BYTES)
        calls = []

        class FakeClient:
            def handshake(self):
                return None

            def generate_audio(self, endpoint, fields, files, *, load_engine=None):
                calls.append(endpoint)
                return "x.wav", _WAV_BYTES, "生成成功"

        self._patch_client(monkeypatch, FakeClient())
        args = self._args(tmp_path, resume=True)
        cli_remote.run_batch_tasks(args, [{"text": "a"}, {"text": "b"}], None, "http://x")
        assert len(calls) == 1, "已存在的输出必须 SKIP，只生成 1 条"

    def test_batch_single_failure_does_not_stop(self, monkeypatch, tmp_path):
        calls = []

        class FakeClient:
            def handshake(self):
                return None

            def generate_audio(self, endpoint, fields, files, *, load_engine=None):
                calls.append(endpoint)
                if len(calls) == 1:
                    raise cli_remote.RemoteError("超时", "timeout")
                return "x.wav", _WAV_BYTES, "生成成功"

        self._patch_client(monkeypatch, FakeClient())
        args = self._args(tmp_path)
        cli_remote.run_batch_tasks(args, [{"text": "a"}, {"text": "b"}], None, "http://x")
        assert len(calls) == 2, "单条失败不中断"
        assert (tmp_path / "outs" / "output_002.wav").is_file(), "第 2 条仍成功落盘"

    def test_batch_mp3_uses_local_transcode(self, monkeypatch, tmp_path):
        mp3_calls = []

        class FakeClient:
            def handshake(self):
                return None

            def generate_audio(self, endpoint, fields, files, *, load_engine=None):
                return "x.wav", _WAV_BYTES, "生成成功"

        self._patch_client(monkeypatch, FakeClient())
        monkeypatch.setattr(cli_remote, "_save_bytes_as_mp3", lambda audio, path: mp3_calls.append(path))
        args = self._args(tmp_path, format="mp3")
        cli_remote.run_batch_tasks(args, [{"text": "a"}], None, "http://x")
        assert len(mp3_calls) == 1
        assert mp3_calls[0].endswith(".mp3")


# ---------------------------------------------------------------------------
# argv 预提取
# ---------------------------------------------------------------------------


class TestExtractTransportArgs:
    def test_no_flags(self):
        server, no_remote, remaining, error = cli_remote.extract_transport_args(["generate", "design"])
        assert (server, no_remote, remaining, error) == (None, False, ["generate", "design"], None)

    def test_server_after_subcommand(self):
        argv = ["generate", "design", "--server", "http://127.0.0.1:7901"]
        server, no_remote, remaining, error = cli_remote.extract_transport_args(argv)
        assert server == "http://127.0.0.1:7901"
        assert no_remote is False
        assert remaining == ["generate", "design"]
        assert error is None

    def test_server_equals_form(self):
        server, _, remaining, error = cli_remote.extract_transport_args(["--server=http://x:1", "design"])
        assert (server, remaining, error) == ("http://x:1", ["design"], None)

    def test_no_remote_flag(self):
        _, no_remote, remaining, error = cli_remote.extract_transport_args(["batch", "--no-remote", "--json"])
        assert (no_remote, remaining, error) == (True, ["batch", "--json"], None)

    def test_server_missing_value_is_error(self):
        _, _, _, error = cli_remote.extract_transport_args(["generate", "design", "--server"])
        assert error is not None
        assert "--server" in error


# ---------------------------------------------------------------------------
# CLI 主入口：--no-remote 强制直调 / --server 传参生效
# ---------------------------------------------------------------------------


class TestMainTransportDispatch:
    def _run_main(self, monkeypatch, argv_extra):
        import integrated_app.cli as cli_mod
        import integrated_app.cli_service as cli_service_mod

        detect_calls: list[str | None] = []
        design_calls: list = []

        def _no_detect(url=None):
            raise AssertionError("detect_server 不应被调用")

        def _recording_detect(url=None):
            detect_calls.append(url)
            return None  # 探测失败 → 回落直调

        def _fake_run_design(args, parser):
            design_calls.append(args)
            return Result.success({"audio_path": "a.wav", "duration": 1.0, "engine": "voxcpm2"}, command="design")

        monkeypatch.setattr(cli_remote, "detect_server", _recording_detect)
        monkeypatch.setattr(cli_service_mod, "run_design", _fake_run_design)
        monkeypatch.setattr(sys, "argv", ["cli.py", "generate", "design", "--text", "hi", "--json", *argv_extra])

        import integrated_app.cli_output as cli_output_mod

        cli_output_mod._ORIGINAL_STDOUT = None
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            code = cli_mod.main()
        cli_output_mod._ORIGINAL_STDOUT = None
        return code, detect_calls, design_calls, buf_out.getvalue(), buf_err.getvalue()

    def test_no_remote_skips_detection(self, monkeypatch):
        import integrated_app.cli as cli_mod
        import integrated_app.cli_remote as remote_mod
        import integrated_app.cli_service as cli_service_mod

        def _boom(url=None):
            raise AssertionError("设置了 --no-remote，detect_server 不应被调用")

        design_seen = []
        monkeypatch.setattr(remote_mod, "detect_server", _boom)
        monkeypatch.setattr(
            cli_service_mod,
            "run_design",
            lambda args, parser: design_seen.append(args) or Result.success({"audio_path": "a.wav"}, command="design"),
        )
        monkeypatch.setattr(sys, "argv", ["cli.py", "generate", "design", "--text", "hi", "--json", "--no-remote"])
        import integrated_app.cli_output as cli_output_mod

        cli_output_mod._ORIGINAL_STDOUT = None
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = cli_mod.main()
        cli_output_mod._ORIGINAL_STDOUT = None
        assert code == 0
        assert len(design_seen) == 1

    def test_server_flag_forwarded_to_detection(self, monkeypatch):
        code, detect_calls, design_calls, out, _err = self._run_main(monkeypatch, ["--server", "http://127.0.0.1:7901"])
        assert code == 0
        assert detect_calls == ["http://127.0.0.1:7901"]
        assert len(design_calls) == 1, "探测失败（fake 返回 None）→ 回落直调"
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["command"] == "design"

    def test_server_flag_before_subcommand(self, monkeypatch):
        code, detect_calls, design_calls, _out, _err = self._run_main(monkeypatch, [])
        assert code == 0
        assert design_calls, "无 --server 时也应回落直调"
        # 位于子命令之前的写法单独验证（argv 顺序变化）
        import integrated_app.cli as cli_mod
        import integrated_app.cli_service as cli_service_mod

        seen = []
        monkeypatch.setattr(cli_remote, "detect_server", lambda url=None: seen.append(url) or None)
        monkeypatch.setattr(
            cli_service_mod,
            "run_design",
            lambda args, parser: Result.success({"audio_path": "a.wav"}, command="design"),
        )
        monkeypatch.setattr(
            sys, "argv", ["cli.py", "--server", "http://127.0.0.1:7902", "generate", "design", "--text", "hi", "--json"]
        )
        import integrated_app.cli_output as cli_output_mod

        cli_output_mod._ORIGINAL_STDOUT = None
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = cli_mod.main()
        cli_output_mod._ORIGINAL_STDOUT = None
        assert code == 0
        assert seen == ["http://127.0.0.1:7902"]


# ---------------------------------------------------------------------------
# 音频时长本地探测
# ---------------------------------------------------------------------------


class TestProbeDuration:
    def test_real_wav_duration(self):
        try:
            import soundfile as sf
        except ImportError:  # pragma: no cover
            pytest.skip("soundfile 不可用")
        import numpy as np

        buf = io.BytesIO()
        sf.write(buf, np.zeros(22050, dtype="float32"), 22050, format="WAV")
        assert cli_remote._probe_duration_bytes(buf.getvalue()) == pytest.approx(1.0, abs=0.05)

    def test_garbage_bytes_returns_zero(self):
        assert cli_remote._probe_duration_bytes(b"not audio at all") == 0.0
