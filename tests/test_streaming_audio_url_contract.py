"""流式生成音频播放地址契约测试（回归守卫）。

背景（GOTCHAS #86）：
    流式生成（``POST /api/generate/streaming_sse``）完成后，前端用
    ``EmbeddedPlayer`` 内嵌播放器播放合并后的 WAV。播放地址一度被写成
    ``/output/{filename}``，但本服务**从未注册过** ``/output`` 路由或静态挂载
    —— 音频文件的唯一合法出口是 ``GET /api/audio/{filename}``
    （``routes/audio.py``，带三重路径校验 + Range 支持）。

    症状：生成其实成功了（``outputs/streaming_<ts>.wav`` 已落盘），但结果卡
    的播放器点不动、波形空白，服务端日志刷 ``GET /output/xxx.wav 404``。
    （每个结果卡会打两次请求：``new Audio(src)`` 一次 + 波形 ``fetch(src)`` 一次。）

本测试锁定两件事：
    1. 行为契约：``/api/audio/{filename}`` 能真正吐出文件；``/output/{filename}`` 必 404。
    2. 源码字面量：``app/`` 下的模板与路由里不得再出现 ``/output/`` 播放地址。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_DIR = _REPO_ROOT / "app"

# 允许出现的音频 URL 前缀（唯一合法出口）
_VALID_AUDIO_PREFIX = "/api/audio/"

# 扫描范围：会拼接音频播放地址的源码文件（排除二进制/构建产物）
_SCAN_SUFFIXES = (".py", ".html", ".js")

# 注释里说明历史遗留路径是允许的（如 streaming.py 的修复注释），
# 只拦截真正被拼进 HTML/URL 的字面量。
_ALLOWED_MARKERS = ("历史遗留", "从未注册", "GOTCHAS")


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for path in _APP_DIR.rglob("*"):
        if not path.is_file() or path.suffix not in _SCAN_SUFFIXES:
            continue
        rel = path.relative_to(_APP_DIR).as_posix()
        # 跳过 vendored 第三方代码与缓存
        if rel.startswith(("integrated_app/vendor/", "integrated_app/static/vendor/")):
            continue
        if "__pycache__" in rel:
            continue
        files.append(path)
    return files


class TestAudioUrlBehaviour:
    """行为层：验证音频出口真实可用、幽灵路径真实不可用。"""

    @pytest.fixture
    def saved_audio(self, tmp_path, monkeypatch):
        """在临时目录造一个假音频，并把 audio 路由的 SAVE_DIR 指向它。

        注：绝不写仓库的 ``outputs/``（禁区目录，禁止 AI 自动改动）。
        """
        from integrated_app.routes import audio as audio_module

        name = "contract_check_1.wav"
        (tmp_path / name).write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
        monkeypatch.setattr(audio_module, "SAVE_DIR", str(tmp_path), raising=False)
        return name

    def test_api_audio_serves_file(self, client, saved_audio):
        """``GET /api/audio/{filename}`` 必须 200 且返回音频。"""
        resp = client.get(f"{_VALID_AUDIO_PREFIX}{saved_audio}")
        assert resp.status_code == 200, f"音频唯一出口 {_VALID_AUDIO_PREFIX}{saved_audio} 不可用（{resp.status_code}）"
        assert "audio" in resp.headers.get("content-type", "")

    def test_ghost_output_path_is_404(self, client, saved_audio):
        """``GET /output/{filename}`` 必须 404 —— 该路径从未实现，属历史幽灵地址。"""
        resp = client.get(f"/output/{saved_audio}")
        assert resp.status_code == 404, "居然有 /output 路由了？请同步修正 GOTCHAS #86 与前端播放地址约定"


class TestAudioUrlSources:
    """源码层：禁止任何模块再把幽灵路径写进播放地址。"""

    def test_no_output_literal_in_sources(self):
        offenders: list[str] = []
        for path in _iter_source_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if "/output/" not in line:
                    continue
                if any(marker in line for marker in _ALLOWED_MARKERS):
                    continue
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")
        assert offenders == [], "以下位置仍在使用不存在的 /output/ 播放地址：\n" + "\n".join(offenders)

    @pytest.mark.parametrize("name", ["voice_clone.html", "voice_design.html"])
    def test_streaming_templates_use_api_audio(self, name):
        """voice_clone / voice_design 的 SSE done 分支必须拼 ``/api/audio/``。"""
        text = (_APP_DIR / "integrated_app" / "templates" / "tabs" / name).read_text(encoding="utf-8", errors="replace")
        assert re.search(r"EmbeddedPlayer\.html\(\s*['\"]" + re.escape(_VALID_AUDIO_PREFIX), text), (
            f"{name} 的 EmbeddedPlayer.html(...) 未使用 {_VALID_AUDIO_PREFIX}"
        )

    def test_streaming_route_uses_api_audio(self):
        """streaming_audio 路由返回的自动播放 HTML 必须拼 ``/api/audio/``。"""
        text = (_APP_DIR / "integrated_app" / "routes" / "generate" / "voxcpm2" / "streaming.py").read_text(
            encoding="utf-8", errors="replace"
        )
        assert '<source src="/api/audio/' in text
        assert '_EMBEDDED_PLAYER_HTML.format(audio_url="/api/audio/"' in text


# ---------------------------------------------------------------------------
# 端到端闭环：SSE 流式生成 → done.filename → 按该地址真能取到音频
# ---------------------------------------------------------------------------


def _parse_sse_events(body: str) -> list[tuple[str, str]]:
    """把 ``text/event-stream`` 响应体解析成 ``[(event, data), ...]``。"""
    events: list[tuple[str, str]] = []
    event = ""
    data = ""
    for raw in body.split("\n"):
        line = raw.rstrip("\r")
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data += line[len("data:") :].strip()
        elif line == "":
            if event:
                events.append((event, data))
            event, data = "", ""
    if event:
        events.append((event, data))
    return events


class TestStreamingEndToEnd:
    """端到端：真实路由链路跑一遍流式生成，再按 ``done`` 给的地址取件。

    WHY 这层不可省：前面两层守卫只能证明「前端拼的字符串在路由表里存在」，
    证明不了「SSE 跑完给出的 filename 真能取到音频」。本测试用**桩模型**替换
    ``registry.voxcpm_model``（不加载 GPU/权重），驱动完整链路：
    ``split_text_for_tts`` 分段 → 逐段推理 → ``event: audio`` 推 base64 →
    ``_merge_and_save_wav`` 合并写盘 → ``event: done`` 带 filename →
    ``GET /api/audio/{filename}`` 取回音频。这正是用户报障时断掉的那一环。
    """

    @pytest.fixture
    def stub_model(self, monkeypatch):
        """把 registry.voxcpm_model 换成返回静音的桩模型（property 带 setter，可注入）。"""
        import numpy as np

        from integrated_app.model_registry import registry

        class _StubModel:
            """最小 VoxCPM2 契约：generate_streaming / generate。"""

            def generate_streaming(self, **_kwargs):
                yield np.zeros(4800, dtype=np.float32)  # 0.1s @48kHz

            def generate(self, **_kwargs):
                return np.zeros(4800, dtype=np.float32)

        monkeypatch.setattr(registry, "voxcpm_model", _StubModel())
        return registry

    @pytest.fixture
    def isolated_save_dir(self, tmp_path, monkeypatch):
        """把 streaming 与 audio 两个模块各自的 SAVE_DIR 都指向临时目录。

        注：绝不写仓库的 ``outputs/``（禁区目录，禁止 AI 自动改动）。
        """
        from integrated_app.routes import audio as audio_module
        from integrated_app.routes.generate.voxcpm2 import streaming as streaming_module

        monkeypatch.setattr(streaming_module, "SAVE_DIR", str(tmp_path))
        monkeypatch.setattr(audio_module, "SAVE_DIR", str(tmp_path))
        return tmp_path

    def test_sse_done_filename_is_playable(self, client, stub_model, isolated_save_dir):
        """流式生成给出的 filename，必须能通过 ``/api/audio/`` 取到音频。"""
        # CSRF 是 Double-Submit Cookie：先 GET 拿 cookie，再注入同名 header
        client.get("/")
        token = client.cookies.get("csrf_token") or ""
        headers = {"X-CSRF-Token": token} if token else {}

        resp = client.post(
            "/api/generate/streaming_sse",
            data={"text": "你好世界，这是一次流式生成闭环测试。", "persona_name": "", "lang": "Auto"},
            headers=headers,
        )
        assert resp.status_code == 200, f"SSE 端点未返回 200：{resp.status_code} {resp.text[:400]}"

        events = _parse_sse_events(resp.text)
        kinds = [e for e, _ in events]
        assert "meta" in kinds, f"缺少 meta 事件：{kinds}"
        assert "audio" in kinds, f"未收到任何 audio 事件（段级推送没发生）：{kinds}"
        assert "done" in kinds, f"未收到 done 事件（事件序列 {kinds}）：{resp.text[:400]}"

        done = json.loads(dict(events)["done"])
        assert done.get("status") == "done", done
        filename = done.get("filename")
        assert filename, f"done 事件未携带 filename：{done}"

        # 落盘校验：合并后的 wav 真的写进了 SAVE_DIR
        assert (isolated_save_dir / filename).is_file(), f"done.filename 未落盘：{filename}"

        # 取件校验：前端拿到 filename 后正是拼 /api/audio/ 去播的
        audio = client.get(f"{_VALID_AUDIO_PREFIX}{filename}")
        assert audio.status_code == 200, (
            f"按 done.filename 取不到音频（{audio.status_code}）——这正是用户报障的断点：{filename}"
        )
        assert audio.headers.get("content-type", "").startswith("audio/"), audio.headers
        assert len(audio.content) > 44, "返回内容不足以构成一个 WAV 文件"
        assert audio.content[:4] == b"RIFF", "返回内容不是 WAV（缺少 RIFF 头）"

        # 非空转证明：取回字节数必须与「SSE 实际推送的段数 × 每段样本数」严格对应。
        # 桩模型每段产出 4800 个 float32 → int16 单声道 16-bit：每段 4800×2 字节。
        segment_count = kinds.count("audio")
        expected_bytes = 44 + 4800 * 2 * segment_count
        assert len(audio.content) == expected_bytes, (
            f"取回音频字节数 {len(audio.content)} != 期望 {expected_bytes}"
            f"（{segment_count} 段 × 4800 样本 × 2 字节 + 44 字节 WAV 头）——链路存在空转或数据错位"
        )
