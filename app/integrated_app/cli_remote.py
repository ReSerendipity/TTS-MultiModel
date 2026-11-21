#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""CLI → 常驻 HTTP 服务适配层（传输层选择，ADR-0005 补充）。

CLI 每条命令都是独立进程，``generate *`` 每次都要重新加载模型（实测约 36–41s）。
当本机已有常驻 HTTP 服务时（桌面版 GUI 运行时由 Tauri 壳拉起，或用户手工
``cli.py serve``），把"有模型加载成本"的生成命令改走 HTTP，即可把单次生成
从 ~43s 降到 ~7s。

设计约束（详见 docs/agents/TASK-DISPATCH-MODEL-REUSE-20261121.md）：
    - **默认行为不变**：没有服务时完全回落现有直调路径（cli_service*.py 不动）；
    - **服务发现**：``--server`` 显式 URL > 环境变量 ``TTS_CLI_SERVER`` >
      ``app/.server_port`` 端口文件（与 ``app_server.run_server`` 写端同公式）；
    - **CSRF**：Double-Submit Cookie 模式 —— 先 GET ``/api/health/ping`` 拿
      cookie，再在 POST 时同值放 ``X-CSRF-Token`` 头（缺头是 403）；
    - **生成端点返回 HTMX HTML 片段**（edge-tts 返回 JSON）：HTML 只提取
      ``data-audio-filename``，取不到即明确报错，不做静默重试；
    - **模型不自动加载**：收到 ``engine_not_ready`` 错误时自动
      ``POST /api/model/load`` 一次并重试一次，仍失败报 ``NOT_READY``；
    - **零新依赖**：全部使用标准库（urllib / http.cookiejar / re / json）。

本模块不 import ``cli_service*``（保持传输层独立），也不被 ``cli_service*``
import（单向依赖：cli.py → cli_remote.py）。
"""

from __future__ import annotations

import html as _html
import io
import json as _json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

from integrated_app.cli_output import Result, log, warn

# ---------------------------------------------------------------------------
# 常量：命令 → HTTP 端点 / 引擎映射
# ---------------------------------------------------------------------------

# generate 子命令族中可走 HTTP 的部分（postprocess/model/persona 等毫秒级操作
# 不在此列——直调更简单，走 HTTP 反而引入网络故障面）。
HTTP_CAPABLE_SUBS: frozenset[str] = frozenset(
    {
        "design",
        "clone",
        "ultimate",
        "prompt-continue",
        "script",
        "emotion",
        "breeze-design",
        "breeze-clone",
        "breeze-direction",
        "edge",
        "convert",
        "edit",
    }
)

# 各子命令对应的生成端点（form 字段映射见 _build_generate_request）
_SUB_ENDPOINT: dict[str, str] = {
    "design": "/api/generate/voxcpm_design",
    "clone": "/api/generate/voxcpm_clone",
    "ultimate": "/api/generate/voxcpm_ultimate",
    "prompt-continue": "/api/generate/voxcpm_prompt_continue",
    "script": "/api/generate/voxcpm_script",
    "emotion": "/api/generate/indextts2",
    "breeze-design": "/api/generate/breeze_design",
    "breeze-clone": "/api/generate/breeze_clone",
    "breeze-direction": "/api/generate/breeze_direction",
    "edge": "/api/generate/edge-tts/tts",
    "convert": "/api/generate/voicebox/convert",
    "edit": "/api/generate/step-audio-editx/edit",
}

# engine_not_ready 自动补载时对应的引擎（edge 无权重、convert/edit 不走
# _check_engine_ready 门禁，均无补载语义）
_SUB_LOAD_ENGINE: dict[str, str] = {
    "design": "voxcpm2",
    "clone": "voxcpm2",
    "ultimate": "voxcpm2",
    "prompt-continue": "voxcpm2",
    "script": "voxcpm2",
    "emotion": "indextts2",
    "breeze-design": "breeze-tts2",
    "breeze-clone": "breeze-tts2",
    "breeze-direction": "breeze-tts2",
}

# 写入 Result.data.engine 的值（与直调路径 result_from_generation 对齐）
_SUB_RESULT_ENGINE: dict[str, str] = {
    "design": "voxcpm2",
    "clone": "voxcpm2",
    "ultimate": "voxcpm2",
    "prompt-continue": "voxcpm2",
    "script": "voxcpm2",
    "emotion": "indextts2",
    "breeze-design": "breeze-tts2",
    "breeze-clone": "breeze-tts2",
    "breeze-direction": "breeze-tts2",
    "edge": "edge-tts",
    "convert": "voicebox",
    "edit": "step-audio-editx",
}

_TIMEOUT_PROBE_S = 2.0  # 服务发现健康探测（起服务约 20–60s，此超时只判"当前在不在"）
_TIMEOUT_HTTP_S = 10.0  # 握手 / 下载音频
_TIMEOUT_LOAD_S = 300.0  # POST /api/model/load（voxcpm2 实测 41s，留余量）
_TIMEOUT_GENERATE_S = 660.0  # 生成端点（服务端硬超时 600s，客户端多留 60s）

# ---------------------------------------------------------------------------
# HTML 片段解析（生成端点返回 text/html，不是 JSON）
# ---------------------------------------------------------------------------

_AUDIO_FILENAME_RE = re.compile(r'data-audio-filename="([^"]+)"')
_ERROR_TYPE_RE = re.compile(r'data-error-type="([^"]+)"')
_ERROR_MESSAGE_RE = re.compile(r'<div class="error-message">([^<]*)</div>')
_SUCCESS_MESSAGE_RE = re.compile(r'<div class="status-message[^"]*">([^<]*)</div>')


class RemoteError(Exception):
    """远端调用失败的统一异常。

    Attributes:
        error_type: 错误类别，取值：
            - ``engine_not_ready``：服务端模型未加载（可自动补载后重试一次）
            - ``validation``：服务端参数校验拒绝
            - ``unreachable``：连接被拒 / 服务不可达（回落直调）
            - ``timeout``：请求超时（明确报错，不自动重试，避免重复生成）
            - ``protocol``：响应不符合预期（如缺少 data-audio-filename）
            - ``http``：其他非 2xx 响应
        status: HTTP 状态码（传输层错误为 None）。
    """

    def __init__(self, message: str, error_type: str = "http", status: int | None = None):
        super().__init__(message)
        self.error_type = error_type
        self.status = status


def _extract_audio_filename(html_text: str) -> str:
    """从 HTMX 成功片段提取 ``data-audio-filename``。

    生成端点契约只依赖这一个 data-* 属性；取不到即明确报错（协议漂移时
    快速失败，绝不静默返回错误文件名）。
    """
    match = _AUDIO_FILENAME_RE.search(html_text)
    if match is None:
        raise RemoteError(
            "服务响应缺少 data-audio-filename 属性，无法定位生成的音频"
            "（服务端契约可能已变化，请改用 --no-remote 直调）",
            "protocol",
        )
    return _html.unescape(match.group(1))


def _parse_error_html(html_text: str) -> tuple[str, str]:
    """从错误片段提取 (error_type, message)，解析不出时给通用值。"""
    match_type = _ERROR_TYPE_RE.search(html_text)
    match_msg = _ERROR_MESSAGE_RE.search(html_text)
    error_type = _html.unescape(match_type.group(1)) if match_type else "general"
    message = _html.unescape(match_msg.group(1)).strip() if match_msg else "服务返回错误片段"
    return error_type, message


def _extract_success_message(html_text: str) -> str:
    """提取成功片段的状态文案（如「生成成功」），取不到时给通用文案。"""
    match = _SUCCESS_MESSAGE_RE.search(html_text)
    if match:
        return _html.unescape(match.group(1)).strip() or "远程生成完成"
    return "远程生成完成"


# ---------------------------------------------------------------------------
# 服务发现：--server > TTS_CLI_SERVER > app/.server_port > 健康探测
# ---------------------------------------------------------------------------


def _project_root() -> str:
    """推导项目根目录（与 config.get_project_root 同逻辑，但不 import config）。

    开发布局 ``<repo>/app/integrated_app/cli_remote.py`` → ``<repo>``；
    桌面布局 ``<InstallDir>/app/app/integrated_app/cli_remote.py`` →
    ``<InstallDir>/app``。
    """
    current = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(current)
    if os.path.basename(parent).lower() == "app":
        return os.path.dirname(parent)
    if os.path.basename(current).lower() == "app":
        return os.path.dirname(current)
    return parent


def _port_file_candidates() -> list[str]:
    """端口文件候选路径（按优先级）。

    写端（``app_server.run_server``）用的是
    ``dirname(dirname(app_server.py))/.server_port``，``app_server.py`` 与本模块
    同目录，因此候选 1 在开发/桌面两种布局下都与写端一致。候选 2 兜底一种
    拆分打包布局（cli 在 ``<X>/app/integrated_app/``，服务代码在
    ``<X>/app/app/integrated_app/``），文件不存在时零开销。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    app_pkg = os.path.dirname(here)  # .../app/integrated_app → .../app
    grandparent = os.path.dirname(app_pkg)
    if os.path.basename(app_pkg).lower() == "app":
        candidates = [
            os.path.join(app_pkg, ".server_port"),
            os.path.join(grandparent, "app", "app", ".server_port"),
        ]
    else:
        candidates = [
            os.path.join(app_pkg, ".server_port"),
            os.path.join(grandparent, "app", ".server_port"),
        ]
    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _read_port_from_file(path: str) -> int | None:
    """读取端口文件；不存在 / 非纯数字 / 空文件一律返回 None（静默忽略）。"""
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return None


def _probe_health(base_url: str, timeout: float = _TIMEOUT_PROBE_S) -> bool:
    """GET /api/health/ping 探测服务存活（不抛异常，只判在不在）。"""
    try:
        req = urllib.request.Request(base_url.rstrip("/") + "/api/health/ping", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 本地信任源
            return resp.status == 200
    except Exception:  # noqa: BLE001 - 探测失败即视为无服务
        return False


def detect_server(explicit_url: str | None = None) -> str | None:
    """按优先级发现本机常驻服务，返回 base_url；无服务返回 None。

    优先级：``explicit_url``（--server）> 环境变量 ``TTS_CLI_SERVER`` >
    端口文件候选（``app/.server_port`` 等）。每个候选地址都会做一次轻量
    health 探测（2s 超时），只有真正应答的地址才算"有服务"。

    回落规则（不阻塞、不误报）：
        - 端口文件 / 显式地址都不存在 → 静默返回 None（正常情况，不打警告）；
        - 端口文件存在但服务无响应 → stderr 一条提示后返回 None
          （可能服务已停止，也可能尚在启动中——``run_server`` 先写文件再监听）；
        - 显式 --server / 环境变量不可达 → 同样提示后返回 None。
    """
    candidates: list[str] = []
    if explicit_url:
        candidates.append(explicit_url.strip().rstrip("/"))
    env_url = os.environ.get("TTS_CLI_SERVER", "").strip()
    if env_url:
        candidates.append(env_url.rstrip("/"))
    explicit_had_value = bool(candidates)

    for path in _port_file_candidates():
        port = _read_port_from_file(path)
        if port is not None:
            candidates.append(f"http://127.0.0.1:{port}")
            break

    port_file_exists = any(os.path.isfile(p) for p in _port_file_candidates())

    for base in candidates:
        if _probe_health(base):
            log(f"发现常驻服务 {base}，本次生成走 HTTP 复用已加载模型")
            return base

    if explicit_had_value:
        warn("指定的服务地址不可达，已回落直调（首次会重新加载模型）")
    elif port_file_exists:
        warn("检测到 .server_port 但服务无响应（可能已停止或启动中），已回落直调")
    return None


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------


def _encode_multipart(fields: list[tuple[str, str]], files: list[tuple[str, str, bytes, str]]) -> tuple[str, bytes]:
    """构造 multipart/form-data 请求体（标准库实现，零依赖）。

    Args:
        fields: 普通表单字段 (name, value)。
        files: 文件字段 (name, filename, data, content_type)。

    Returns:
        (boundary, body_bytes)。
    """
    boundary = "----TTSCliRemote" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields:
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, filename, data, ctype in files:
        header = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode()
        chunks.append(header + data + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(chunks)


def _looks_like_csrf_reject(payload: bytes) -> bool:
    """判断 403 响应体是否为 CSRF 拒绝（非 CSRF 的 403 不重试）。"""
    return b"csrf" in payload.lower()


class RemoteClient:
    """常驻服务 HTTP 客户端（单进程内复用同一 CookieJar / CSRF token）。

    ``_request`` 只在传输层故障（连接被拒 / 超时）时抛 :class:`RemoteError`；
    4xx/5xx 一律以实际 status 返回，由调用方解析错误体（engine_not_ready
    的自动补载依赖错误片段内容，不能在这里吞掉）。
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._jar = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))
        self._csrf_token: str | None = None

    # -- 底层请求 ----------------------------------------------------------

    def _csrf_from_jar(self) -> str | None:
        """从 cookiejar 里取 CSRF token（cookie 名含 csrf 或 xsrf，大小写不敏感）。"""
        for cookie in self._jar:
            lowered = cookie.name.lower()
            if "csrf" in lowered or "xsrf" in lowered:
                return cookie.value
        return None

    def handshake(self) -> None:
        """GET /api/health/ping：签发并缓存 CSRF cookie（服务端 Double-Submit）。"""
        try:
            self._request("GET", "/api/health/ping")
        except RemoteError as exc:  # ping 失败统一转 unreachable，便于上层回落
            raise RemoteError(f"服务握手失败: {exc}", "unreachable", exc.status) from exc
        self._csrf_token = self._csrf_from_jar()

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = _TIMEOUT_HTTP_S,
    ) -> tuple[int, bytes, str]:
        """发请求并统一转译传输层异常；返回 (status, body, content_type)。

        urllib 对非 2xx 抛 HTTPError，这里读出 body 后以实际 status 返回，
        保留服务端错误片段供上层解析 engine_not_ready / validation。
        """
        req = urllib.request.Request(self.base_url + path, data=body, method=method)  # noqa: S310 - 本地信任源
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            try:
                payload = exc.read()
            except Exception:  # noqa: BLE001 - body 读不出时按空处理
                payload = b""
            ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
            return exc.code, payload, ctype
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
                raise RemoteError(f"请求 {path} 超时（>{timeout:.0f}s），已放弃且不自动重试", "timeout") from exc
            raise RemoteError(f"无法连接服务 {self.base_url}{path}: {reason}", "unreachable") from exc
        except TimeoutError as exc:
            raise RemoteError(f"请求 {path} 超时（>{timeout:.0f}s），已放弃且不自动重试", "timeout") from exc

    def _post(
        self, path: str, fields: dict[str, Any], files: list[tuple[str, str, bytes, str]], timeout: float
    ) -> tuple[int, bytes, str]:
        """POST（按有无 files 自动选 multipart / urlencoded），带 CSRF，403 拒绝时重握手重试一次。

        CSRF 403 是传输层握手问题（服务端未开始处理业务），重试不会产生
        重复生成；其余 403 原样返回交上层处理。
        """
        if files:
            clean_fields = [(k, str(v)) for k, v in fields.items() if v is not None]
            boundary, body = _encode_multipart(clean_fields, files)
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        else:
            body = urllib.parse.urlencode({k: v for k, v in fields.items() if v is not None}).encode("utf-8")
            headers = {"Content-Type": "application/x-www-form-urlencoded"}

        def _send() -> tuple[int, bytes, str]:
            sent_headers = dict(headers)
            if self._csrf_token:
                sent_headers["X-CSRF-Token"] = self._csrf_token
            return self._request("POST", path, body=body, headers=sent_headers, timeout=timeout)

        status, payload, ctype = _send()
        if status == 403 and _looks_like_csrf_reject(payload):
            self.handshake()
            if self._csrf_token:
                status, payload, ctype = _send()
        return status, payload, ctype

    def get_bytes(self, path: str, timeout: float = _TIMEOUT_HTTP_S) -> bytes:
        """GET 二进制（音频下载）；非 200 明确报错。"""
        status, payload, _ = self._request("GET", path, timeout=timeout)
        if status != 200:
            raise RemoteError(f"音频下载失败（HTTP {status}）: {path}", "http", status)
        return payload

    # -- 业务动作 ----------------------------------------------------------

    def load_model(self, engine: str) -> None:
        """POST /api/model/load（form: engine）。失败抛 RemoteError。"""
        status, payload, _ = self._post("/api/model/load", {"engine": engine}, [], _TIMEOUT_LOAD_S)
        if status != 200:
            raise RemoteError(f"模型加载请求失败（HTTP {status}）", "http", status)
        try:
            data = _json.loads(payload.decode("utf-8", errors="replace"))
            if str(data.get("status")) != "ok":
                raise RemoteError(f"模型加载失败: {data.get('message', '未知错误')}", "http", status)
        except (_json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RemoteError(f"模型加载响应解析失败: {exc}", "protocol", status) from exc
        log(f"服务端已加载引擎 {engine}")

    def generate_audio(
        self,
        endpoint: str,
        fields: dict[str, Any],
        files: list[tuple[str, str, bytes, str]] | None = None,
        *,
        load_engine: str | None = None,
    ) -> tuple[str, bytes, str]:
        """调用生成端点并下载音频，返回 (filename, audio_bytes, message)。

        处理两类响应：
            - text/html（HTMX 片段）：提取 ``data-audio-filename``；
            - application/json（edge-tts）：取 ``filename`` / ``size_bytes``。

        降级语义：
            - ``engine_not_ready`` 且给了 ``load_engine`` → 自动补载一次并重试
              一次（仍失败即抛，不做第二次重试）；
            - 其他失败一律直接抛 RemoteError（不静默重试，避免重复生成）。
        """
        post_args = (endpoint, fields, files or [], _TIMEOUT_GENERATE_S)
        status, payload, ctype = self._post(*post_args)
        outcome = self._interpret_generate_response(endpoint, status, payload, ctype)

        if outcome is None:
            # 失败片段：仅 engine_not_ready 允许"补载一次 + 重试一次"
            error_type, error_message = _parse_error_html(payload.decode("utf-8", errors="replace"))
            if error_type == "engine_not_ready" and load_engine:
                log(f"服务端引擎未就绪，自动加载 {load_engine} 后重试一次 ...")
                self.load_model(load_engine)
                status, payload, ctype = self._post(*post_args)
                outcome = self._interpret_generate_response(endpoint, status, payload, ctype)
                if outcome is None:
                    error_type, error_message = _parse_error_html(payload.decode("utf-8", errors="replace"))
                    raise RemoteError(error_message, error_type, status)
            else:
                raise RemoteError(error_message, error_type, status)

        filename, message = outcome
        audio = self.get_bytes(f"/api/audio/{urllib.parse.quote(filename)}")
        return filename, audio, message

    def _interpret_generate_response(
        self, endpoint: str, status: int, payload: bytes, ctype: str
    ) -> tuple[str, str] | None:
        """把生成端点响应归一化：成功返回 (filename, message)，失败片段返回 None。

        JSON 与 HTML 错误体都会被解析成可读 message；协议级失败（200 但缺
        filename）在此直接抛 RemoteError(protocol)。
        """
        text = payload.decode("utf-8", errors="replace")
        json_like = "json" in ctype.lower() or text.lstrip().startswith("{")
        if json_like:
            try:
                data = _json.loads(text)
            except _json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                if status == 200 and str(data.get("status")) == "ok":
                    size = data.get("size_bytes", "?")
                    return str(data.get("filename", "")), f"生成成功（{size} 字节）"
                detail = str(data.get("detail") or data.get("message") or f"生成请求失败（HTTP {status}）")
                raise RemoteError(detail, "validation" if status == 400 else "http", status)
        if status == 200 and _AUDIO_FILENAME_RE.search(text):
            return _extract_audio_filename(text), _extract_success_message(text)
        if _ERROR_TYPE_RE.search(text) or _ERROR_MESSAGE_RE.search(text):
            return None  # 交给调用方区分 engine_not_ready / 其他
        if status != 200:
            raise RemoteError(f"生成请求失败（HTTP {status}）{endpoint}", "http", status)
        raise RemoteError(
            "服务响应缺少 data-audio-filename 属性，无法定位生成的音频"
            "（服务端契约可能已变化，请改用 --no-remote 直调）",
            "protocol",
            status,
        )


# ---------------------------------------------------------------------------
# CLI 参数 → 端点表单映射
# ---------------------------------------------------------------------------


def _bool_str(value: Any) -> str:
    """表单 bool 字段（服务端以 "true"/"false" 字符串解析）。"""
    return "true" if bool(value) else "false"


def _is_default_voxcpm_path(model_path: Any) -> bool:
    """与 cli_service._uses_custom_model_path 同口径：非默认 --model-path 视为自定义。"""
    if not model_path:
        return True
    norm = str(model_path).replace("\\", "/").rstrip("/").lower()
    return norm.endswith("model/voxcpm2")


def supports_remote(args: Any, sub: str) -> bool:
    """该 (args, sub) 组合是否适合走 HTTP（不适合时 cli.py 回落直调）。

    规则：
        - design/clone/ultimate/prompt-continue/script 仅 voxcpm2 引擎走 HTTP
          （--engine indextts2 的直调语义在 /api 端点无对应物）；
        - script 带 --persona-map（角色→任意音频路径）回落直调：HTTP 端点只
          能按音色库名解析角色，语义不同。
    """
    if sub in {"design", "clone", "ultimate", "prompt-continue", "script"}:
        engine = getattr(args, "engine", None)
        if engine is not None and str(engine) != "voxcpm2":
            return False
    # HTTP 的 /api/generate/indextts2 端点固定加载 2.5；
    # emotion 显式指定 2.0 时语义无法表达，回落直调。
    if sub == "emotion" and str(getattr(args, "engine", "") or "") == "indextts20":
        return False
    return not (sub == "script" and getattr(args, "persona_map", None))


def _require_file(path: Any, label: str) -> str | None:
    """直调路径 run_* 同款的存在性前置校验：缺失时返回错误文案（None = 通过）。"""
    value = str(path or "")
    if not value or not os.path.isfile(value):
        return f"{label}不存在: {value or '(未提供)'}"
    return None


def _build_generate_request(args: Any, sub: str) -> tuple[str, dict[str, Any], list[tuple[str, str, bytes, str]]]:
    """把 CLI 参数映射为 (endpoint, form fields, upload files)。

    表单字段语义与各路由 Form(...) 定义一一对应；显式发送 denoise/norm，
    不依赖服务端默认值（服务端默认 "true"，直调路径默认 False，必须显式
    对齐，否则同一 CLI 参数在两条路径下产物不同）。
    """
    endpoint = _SUB_ENDPOINT[sub]
    fields: dict[str, Any] = {}
    files: list[tuple[str, str, bytes, str]] = []

    def _upload(form_name: str, path: str) -> None:
        with open(path, "rb") as fh:
            files.append((form_name, os.path.basename(path), fh.read(), "application/octet-stream"))

    if sub == "design":
        fields.update(
            text=args.text,
            instruction=getattr(args, "control", "") or "",
            cfg=getattr(args, "cfg_value", 2.0),
            steps=getattr(args, "inference_timesteps", 10),
            denoise=_bool_str(getattr(args, "denoise", False)),
            norm=_bool_str(getattr(args, "normalize", False)),
        )
    elif sub == "clone":
        fields.update(
            text=args.text,
            instruction=getattr(args, "control", "") or "",
            ref_audio_path=args.reference_audio,
            cfg=getattr(args, "cfg_value", 2.0),
            steps=getattr(args, "inference_timesteps", 10),
            denoise=_bool_str(getattr(args, "denoise", False)),
            norm=_bool_str(getattr(args, "normalize", False)),
            # CLI 使用即代表拥有参考音频使用权/授权（与直调路径无门禁一致；
            # 该字段是 WebUI 上传来源的门禁，CLI 侧语义等价于已勾选）
            has_consent=_bool_str(True),
        )
    elif sub == "ultimate":
        fields.update(
            text=args.text,
            instruction=getattr(args, "control", "") or "",
            ref_audio_path=args.reference_audio,
            ref_text=getattr(args, "ref_text", "") or "",
            cfg=getattr(args, "cfg_value", 2.0),
            steps=getattr(args, "inference_timesteps", 10),
            seed=getattr(args, "seed", -1),
            denoise_strength=getattr(args, "denoise_strength", 0.5),
            denoise=_bool_str(float(getattr(args, "denoise_strength", 0.5)) > 0),
            norm=_bool_str(getattr(args, "normalize", False)),
            has_consent=_bool_str(True),
        )
    elif sub == "prompt-continue":
        fields.update(text=args.text, prompt_text=getattr(args, "prompt_text", "") or "")
        _upload("prompt_wav", args.prompt_audio)
    elif sub == "script":
        # 剧本正文由 run_generate 读文件后放入 args.script_text（与直调同源）
        fields.update(
            text=getattr(args, "script_text", "") or "",
            lang=getattr(args, "lang", "中文") or "中文",
            cfg=getattr(args, "cfg_value", 2.0),
            steps=getattr(args, "inference_timesteps", 10),
            norm=_bool_str(getattr(args, "normalize", False)),
            denoise_strength=getattr(args, "denoise_strength", 0.5),
            denoise=_bool_str(float(getattr(args, "denoise_strength", 0.5)) > 0),
            seed=getattr(args, "seed", -1),
        )
    elif sub == "emotion":
        fields.update(
            text=args.text,
            lang=getattr(args, "lang", "") or "Auto",
            emo_text=getattr(args, "emotion_text", "") or "",
            emo_alpha=getattr(args, "emotion_alpha", 0.8),
            duration_scale=getattr(args, "duration_factor", 1.0),
            # 服务端 seed=0 表示随机（CLI 用 -1），需换算哨兵值
            seed=0 if int(getattr(args, "seed", -1)) < 0 else int(getattr(args, "seed", -1)),
            has_consent=_bool_str(True),
        )
        vector_raw = getattr(args, "emotion_vector", None)
        if vector_raw:
            values = [float(x) for x in str(vector_raw).split(",") if str(x).strip()]
            keys = (
                "emo_happy",
                "emo_angry",
                "emo_sad",
                "emo_afraid",
                "emo_disgusted",
                "emo_melancholic",
                "emo_surprised",
                "emo_calm",
            )
            for key, value in zip(keys, values):
                fields[key] = value
        if getattr(args, "reference_audio", None):
            _upload("ref_audio", args.reference_audio)
        if getattr(args, "emotion_audio", None):
            _upload("emo_audio", args.emotion_audio)
    elif sub == "breeze-design":
        fields.update(
            text=args.text,
            instruction=getattr(args, "control", "") or "",
            cfg_scale=getattr(args, "cfg_scale", 4.0),
            seed=getattr(args, "seed", -1),
            random_seed=_bool_str(int(getattr(args, "seed", -1)) < 0),
        )
    elif sub == "breeze-clone":
        fields.update(
            text=args.text,
            ref_text=getattr(args, "ref_text", "") or "",
            seed=getattr(args, "seed", -1),
            random_seed=_bool_str(int(getattr(args, "seed", -1)) < 0),
            has_consent=_bool_str(True),
        )
        _upload("ref_audio", args.reference_audio)
    elif sub == "breeze-direction":
        fields.update(
            text=args.text,
            instruction=getattr(args, "control", "") or "",
            ref_text=getattr(args, "ref_text", "") or "",
            cfg_scale=getattr(args, "cfg_scale", 4.0),
            seed=getattr(args, "seed", -1),
            random_seed=_bool_str(int(getattr(args, "seed", -1)) < 0),
            has_consent=_bool_str(True),
        )
        _upload("ref_audio", args.reference_audio)
    elif sub == "edge":
        fields.update(text=args.text, lang=getattr(args, "lang", "zh") or "zh")
    elif sub == "convert":
        fields.update(tau=getattr(args, "tau", 0.3))
        _upload("source_audio", args.source_audio)
        _upload("target_audio", args.target_audio)
    elif sub == "edit":
        fields.update(
            source_text=getattr(args, "source_text", "") or "",
            edit_type=getattr(args, "edit_type", "") or "",
            edit_info=getattr(args, "edit_info", "") or "",
            target_text=getattr(args, "target_text", "") or "",
        )
        _upload("source_audio", args.source_audio)
    else:  # pragma: no cover - HTTP_CAPABLE_SUBS 与映射表共同维护
        raise ValueError(f"generate {sub} 没有 HTTP 端点映射")
    return endpoint, fields, files


def run_generate(args: Any, parser: Any, sub: str, base_url: str) -> Result:
    """generate 子命令的 HTTP 传输入口，返回与直调路径同构的 Result。

    前置校验与直调路径（cli_service_ext.run_*）同款：文件缺失、参数缺失时
    返回 ``USAGE_ERROR``（剧本输入缺失沿用直调的 parser.error 退出方式）。
    """
    # script：剧本正文读文件（直调 run_script 也用 parser.error，保持同款退出）
    if sub == "script":
        script_path = getattr(args, "input", None)
        if not script_path or not os.path.isfile(script_path):
            parser.error(f"剧本文件不存在: {script_path}")
        args.script_text = Path(str(script_path)).read_text(encoding="utf-8")

    # 直调路径同款的文件存在性校验（返回 USAGE_ERROR，而非崩栈）
    file_checks: list[tuple[Any, str]] = []
    if sub == "ultimate":
        file_checks.append((args.reference_audio, "参考音频"))
    elif sub == "prompt-continue":
        file_checks.append((args.prompt_audio, "提示音频"))
    elif sub in ("emotion", "breeze-clone", "breeze-direction"):
        file_checks.append((args.reference_audio, "参考音频"))
    elif sub == "convert":
        file_checks.append((args.source_audio, "源音频"))
        file_checks.append((args.target_audio, "目标音色音频"))
    elif sub == "edit":
        file_checks.append((args.source_audio, "源音频"))
    for path, label in file_checks:
        failure = _require_file(path, label)
        if failure is not None:
            return Result.failure(failure, "USAGE_ERROR", command=sub)

    # prompt-continue：与直调 run_prompt_continue 同款提示文本校验
    if sub == "prompt-continue" and not getattr(args, "prompt_text", None) and not getattr(args, "prompt_file", None):
        return Result.failure("续写模式需要 --prompt-text 或 --prompt-file", "USAGE_ERROR", command=sub)

    # emotion：向量/情感来源校验与直调同口径
    if sub == "emotion":
        vector_raw = getattr(args, "emotion_vector", None)
        if vector_raw:
            try:
                values = [float(x) for x in str(vector_raw).split(",") if str(x).strip()]
            except ValueError:
                return Result.failure("--emotion-vector 需为逗号分隔的 8 个浮点数", "USAGE_ERROR", command="emotion")
            if len(values) != 8:
                return Result.failure(
                    f"--emotion-vector 需要 8 个值，实际 {len(values)}", "USAGE_ERROR", command="emotion"
                )
        if not any(
            (
                getattr(args, "emotion_text", None),
                getattr(args, "emotion_audio", None),
                vector_raw,
            )
        ):
            return Result.failure(
                "情感合成需至少提供 --emotion-text / --emotion-audio / --emotion-vector 之一",
                "USAGE_ERROR",
                command="emotion",
            )

    # 直调路径在自定义 --model-path 时只警告不阻断（service 忽略该参数），HTTP 同口径
    if sub in {"design", "clone", "ultimate", "prompt-continue", "script"} and not _is_default_voxcpm_path(
        getattr(args, "model_path", None)
    ):
        warn("检测到自定义 --model-path，service/HTTP 路径忽略该参数")

    endpoint, fields, files = _build_generate_request(args, sub)

    client = RemoteClient(base_url)
    try:
        client.handshake()
        filename, audio, message = client.generate_audio(endpoint, fields, files, load_engine=_SUB_LOAD_ENGINE.get(sub))
    except RemoteError as exc:
        error_code = "NOT_READY" if exc.error_type == "engine_not_ready" else "BUSINESS_ERROR"
        return Result.failure(str(exc), error_code, command=sub)

    # 落盘：-o 优先；否则落到项目 outputs/（与 service 直调产物同目录）
    output = getattr(args, "output", None)
    if output:
        dest = Path(output)
        dest.parent.mkdir(parents=True, exist_ok=True)
    else:
        save_dir = os.path.join(_project_root(), "outputs")
        os.makedirs(save_dir, exist_ok=True)
        dest = Path(save_dir) / filename
    dest.write_bytes(audio)

    duration = _probe_duration_bytes(audio)
    data: dict[str, Any] = {
        "audio_path": str(dest.resolve()),
        "message": message,
        "duration": duration,
        "engine": _SUB_RESULT_ENGINE[sub],
        "params": _result_params(args, sub),
        "transport": "http",
    }
    if sub == "edge":
        data["message"] = f"Edge-TTS 合成完成（{len(audio)} 字节）"
        data["params"] = {"lang": getattr(args, "lang", "zh") or "zh", "size_bytes": len(audio)}
    elif sub == "convert":
        data.update(
            success=True,
            source_audio=os.path.abspath(str(args.source_audio)),
            target_audio=os.path.abspath(str(args.target_audio)),
        )
    elif sub == "edit":
        data.update(
            success=True,
            source_audio=os.path.abspath(str(args.source_audio)),
            edit_type=getattr(args, "edit_type", "") or "",
            edit_info=getattr(args, "edit_info", "") or "",
        )
    return Result.success(data=data, command=sub)


def _result_params(args: Any, sub: str) -> dict[str, Any]:
    """构造 Result.data.params（transport 统一在 data 层标注，不进 params）。

    键名与直调路径各 handler 的 params 同构（design/clone 对齐 service 回显的
    instruction/cfg_value/inference_timesteps/denoise/normalize；ultimate 对齐
    advanced_* 系列等），避免 agent 在两条传输路径间切换时解析逻辑分叉。
    """
    if sub in ("design", "clone"):
        params: dict[str, Any] = {
            "instruction": getattr(args, "control", "") or "",
            "cfg_value": getattr(args, "cfg_value", None),
            "inference_timesteps": getattr(args, "inference_timesteps", None),
            "denoise": bool(getattr(args, "denoise", False)),
            "normalize": bool(getattr(args, "normalize", False)),
        }
        if sub == "clone":
            params["reference_audio"] = getattr(args, "reference_audio", None)
        return params
    if sub == "ultimate":
        return {
            "instruction": getattr(args, "control", "") or "",
            "ref_audio_path": getattr(args, "reference_audio", None),
            "advanced_cfg": getattr(args, "cfg_value", 2.0),
            "advanced_norm": bool(getattr(args, "normalize", False)),
            "advanced_denoise": float(getattr(args, "denoise_strength", 0.5)),
            "advanced_steps": getattr(args, "inference_timesteps", 10),
            "advanced_seed": getattr(args, "seed", -1),
            "ref_text": getattr(args, "ref_text", "") or "",
        }
    if sub == "prompt-continue":
        return {
            "prompt_wav": getattr(args, "prompt_audio", None),
            "prompt_text": getattr(args, "prompt_text", "") or "",
        }
    if sub == "script":
        return {
            "advanced_cfg": getattr(args, "cfg_value", 2.0),
            "advanced_norm": bool(getattr(args, "normalize", False)),
            "advanced_denoise": float(getattr(args, "denoise_strength", 0.5)),
            "advanced_steps": getattr(args, "inference_timesteps", 10),
            "advanced_seed": getattr(args, "seed", -1),
            "lang": getattr(args, "lang", "中文") or "中文",
        }
    if sub == "emotion":
        return {
            "reference_audio": getattr(args, "reference_audio", None),
            "emotion_text": getattr(args, "emotion_text", None),
            "emotion_audio": getattr(args, "emotion_audio", None),
            "emotion_vector": getattr(args, "emotion_vector", None),
            "emo_alpha": getattr(args, "emotion_alpha", 0.8),
            "duration_factor": getattr(args, "duration_factor", 1.0),
            "lang": getattr(args, "lang", None),
        }
    if sub.startswith("breeze-"):
        return {
            "instruction": getattr(args, "control", "") or "",
            "reference_audio": getattr(args, "reference_audio", None),
            "ref_text": getattr(args, "ref_text", "") or "",
            "cfg_scale": getattr(args, "cfg_scale", 4.0),
            "seed": getattr(args, "seed", -1),
        }
    if sub == "edge":
        return {"lang": getattr(args, "lang", "zh") or "zh"}
    if sub == "convert":
        return {"tau": getattr(args, "tau", 0.3)}
    if sub == "edit":
        return {
            "edit_type": getattr(args, "edit_type", "") or "",
            "edit_info": getattr(args, "edit_info", "") or "",
        }
    return {}


def _probe_duration_bytes(audio: bytes) -> float:
    """本地探测音频时长（HTTP 响应只有文件字节，无时长元数据）。

    soundfile 不可读（格式异常等）时返回 0.0，不让时长探测失败拖垮整体成功。
    """
    try:
        import soundfile as sf

        info = sf.info(io.BytesIO(audio))
        return round(float(info.duration), 3)
    except Exception:  # noqa: BLE001 - 时长拿不到不影响主流程
        return 0.0


# ---------------------------------------------------------------------------
# batch：逐条走 HTTP（复用同一 client，--resume 语义与直调一致）
# ---------------------------------------------------------------------------


def _save_bytes_as_mp3(audio: bytes, output_path: str) -> None:
    """把 WAV 字节转存为 MP3（pydub 缺失时降级存 WAV 并警告，与直调同口径）。"""
    try:
        from pydub import AudioSegment

        segment = AudioSegment.from_wav(io.BytesIO(audio))
        segment.export(output_path, format="mp3", bitrate="192k")
    except ImportError:
        warn("pydub not installed, falling back to WAV format")
        wav_path = output_path.rsplit(".", 1)[0] + ".wav"
        Path(wav_path).write_bytes(audio)


def run_batch_tasks(args: Any, tasks: list[dict], prompt_text: str | None, base_url: str) -> None:
    """batch 的 HTTP 传输入口：逐条生成并落盘到 --output-dir。

    与直调 ``cmd_batch`` 保持一致的行为：
        - ``--resume``：输出文件已存在且非空 → SKIP；
        - 单条失败不中断（计 FAILED 后继续）；
        - 进度/汇总日志走 stderr，返回 None（batch 不产 JSON 信封，与直调同）；
        - MP3 格式：服务端产物为 WAV，本地用 pydub 转码（缺失时降级 WAV）。

    全局音色参数决定每条任务走的端点（与直调 model.generate 的参数语义对应）：
        - ``--prompt-audio`` → ``voxcpm_prompt_continue``（上传 prompt 音频）；
        - ``--reference-audio`` → ``voxcpm_clone``（服务端读本机路径）；
        - 两者皆无 → ``voxcpm_design``。
    """
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)  # 与 cmd_batch 同款：先确保输出目录存在
    output_format = (getattr(args, "format", "wav") or "wav").lower()
    engine = getattr(args, "engine", None)
    resume_mode = bool(getattr(args, "resume", False))

    # 全局音色参数（与直调一致：denoise 仅在有 prompt/reference 音频时生效）
    prompt_audio = getattr(args, "prompt_audio", None)
    reference_audio = getattr(args, "reference_audio", None)
    denoise_enabled = bool(getattr(args, "denoise", False)) and (
        prompt_audio is not None or reference_audio is not None
    )

    client = RemoteClient(base_url)
    try:
        client.handshake()
    except RemoteError as exc:
        # 服务在探测后、握手前掉线：明确报错退出（不静默切直调，避免整批重复生成语义混乱）
        raise SystemExit(f"Error: 常驻服务握手失败: {exc}") from exc

    total = len(tasks)
    success_count = 0
    fail_count = 0
    skip_count = 0
    start_time = time.time()

    log("")
    log("=" * 50)
    log(
        f"Batch processing (HTTP): {total} tasks | Format: {output_format.upper()} | Engine: {engine or 'voxcpm2'}"
        f"{' | resume' if resume_mode else ''}"
    )
    log("=" * 50)
    log("")

    for i, task in enumerate(tasks, 1):
        text = task["text"]
        control = task.get("control") or getattr(args, "control", None)
        task_output_name = task.get("output")

        try:
            if task_output_name:
                out_name = task_output_name
                if not out_name.endswith(f".{output_format}"):
                    out_name = f"{out_name}.{output_format}"
            else:
                out_name = f"output_{i:03d}.{output_format}"
            output_file = output_dir / out_name

            if resume_mode and output_file.is_file() and output_file.stat().st_size > 0:
                skip_count += 1
                log(f"[{i}/{total}] SKIP (already exists): {out_name}")
                continue

            if prompt_audio:
                endpoint = _SUB_ENDPOINT["prompt-continue"]
                fields = {"text": text, "prompt_text": prompt_text or ""}
                with open(prompt_audio, "rb") as fh:
                    files = [("prompt_wav", os.path.basename(prompt_audio), fh.read(), "application/octet-stream")]
            elif reference_audio:
                endpoint = _SUB_ENDPOINT["clone"]
                fields = {
                    "text": text,
                    "instruction": control or "",
                    "ref_audio_path": reference_audio,
                    "cfg": getattr(args, "cfg_value", 2.0),
                    "steps": getattr(args, "inference_timesteps", 10),
                    "denoise": _bool_str(denoise_enabled),
                    "norm": _bool_str(getattr(args, "normalize", False)),
                    "has_consent": _bool_str(True),
                }
                files = []
            else:
                endpoint = _SUB_ENDPOINT["design"]
                fields = {
                    "text": text,
                    "instruction": control or "",
                    "cfg": getattr(args, "cfg_value", 2.0),
                    "steps": getattr(args, "inference_timesteps", 10),
                    "denoise": _bool_str(False),
                    "norm": _bool_str(getattr(args, "normalize", False)),
                }
                files = []

            _, audio, _ = client.generate_audio(endpoint, fields, files, load_engine="voxcpm2")

            if output_format == "mp3":
                _save_bytes_as_mp3(audio, str(output_file))
            else:
                output_file.write_bytes(audio)

            duration = _probe_duration_bytes(audio)
            elapsed = time.time() - start_time
            avg_time = elapsed / i
            eta = avg_time * (total - i)
            log(
                f"[{i}/{total}] Saved: {output_file.name} ({duration:.2f}s) | Elapsed: {elapsed:.1f}s | ETA: {eta:.1f}s"
            )
            success_count += 1

        except RemoteError as exc:
            fail_count += 1
            log(f"[{i}/{total}] FAILED: {exc}")
        except Exception as exc:  # noqa: BLE001 - 与直调 batch 同款：单条失败不中断
            fail_count += 1
            log(f"[{i}/{total}] FAILED: {exc}")

    elapsed_total = time.time() - start_time
    log("")
    log("=" * 50)
    log(
        f"Batch complete: {success_count}/{total} succeeded, {fail_count} failed, {skip_count} skipped "
        f"| Total time: {elapsed_total:.1f}s | Avg: {elapsed_total / max(success_count, 1):.2f}s/task"
    )
    log(f"Output: {output_dir.resolve()}")
    log("=" * 50)


# ---------------------------------------------------------------------------
# argv 预提取（--server / --no-remote 允许写在子命令之后）
# ---------------------------------------------------------------------------


def extract_transport_args(argv: list[str]) -> tuple[str | None, bool, list[str], str | None]:
    """从 argv 预提取 ``--server`` / ``--no-remote``（沿用 --json 的方案）。

    Returns:
        (server, no_remote, remaining_argv, error)。
        error 非 None 时表示 ``--server`` 缺值，调用方应 parser.error(error)。
    """
    server: str | None = None
    no_remote = False
    missing_value = False
    remaining: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--server":
            if i + 1 >= len(argv):
                missing_value = True
                i += 1
                continue
            server = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--server="):
            server = arg.split("=", 1)[1]
            i += 1
            continue
        if arg == "--no-remote":
            no_remote = True
            i += 1
            continue
        remaining.append(arg)
        i += 1
    error = "--server requires a URL value" if missing_value else None
    return server, no_remote, remaining, error


def remote_for_args(args: Any) -> str | None:
    """按 args.server / args.no_remote 决定是否发现服务（供 cli.py 统一调用）。"""
    if getattr(args, "no_remote", False):
        return None
    return detect_server(getattr(args, "server", None))


# 供调试：直接以模块方式探测（python -m integrated_app.cli_remote [url]）
if __name__ == "__main__":  # pragma: no cover
    import sys

    _found = detect_server(sys.argv[1] if len(sys.argv) > 1 else None)
    print(_found or "(no server)")
