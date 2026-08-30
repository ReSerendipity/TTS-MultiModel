"""Tests verifying HTMX request handling for tab/fragment endpoints."""

import re
from pathlib import Path

import pytest

from integrated_app.routes.tabs import _TAB_TEMPLATES

_TABS_DIR = Path(__file__).resolve().parent.parent / "app" / "integrated_app" / "templates" / "tabs"

#: 已知「模板引用了但后端未实现」的端点。列在这里是为了把欠债显式记录在代码里，
#: 而不是让断言整体失效——任何新增的未注册端点仍会被这条测试拦下。
#: 2026-08-29：/api/persona/save 已实现（routes/persona.py::persona_save），故清空。
#: 新增欠债前请先确认它无法在当次任务内修复，并在 KNOWN_GOTCHAS 里留下对应条目。
_KNOWN_UNIMPLEMENTED_ENDPOINTS: frozenset[str] = frozenset()


def test_tab_voice_design_returns_html_for_htmx(client):
    """HX-Request tab endpoints return HTML fragments."""
    response = client.get("/tab/voice_design", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "text/html" in response.headers["Content-Type"]
    # The voice design tab should contain its fragment/container classes.
    assert "voice-design" in response.text.lower() or "vd-" in response.text.lower()


def test_tab_voice_design_redirects_without_htmx(client):
    """Non-HTMX requests to tab endpoints redirect to the home page with tab param."""
    response = client.get("/tab/voice_design", follow_redirects=False)
    assert response.status_code == 303
    assert "/?tab=voice_design" in response.headers["location"]


def test_every_registered_tab_template_exists_on_disk():
    """_TAB_TEMPLATES 的每个值都必须对应磁盘上真实存在的模板。

    WHY：tabs.py 在运行时会做 os.path.exists 兜底并返回 404，
    所以「注册了但文件缺失」只会在用户点击时才暴露。此处在 CI 阶段就拦住。
    """
    missing = [name for name, rel in _TAB_TEMPLATES.items() if not (_TABS_DIR.parent / rel).exists()]
    assert missing == [], f"已注册但模板文件缺失的 tab: {missing}"


@pytest.mark.parametrize("tab_name", ["indextts20_clone", "indextts20_emotion"])
def test_indextts20_tabs_render_own_templates(client, tab_name):
    """IndexTTS 2.0 拥有独立 tab 与独立模板，渲染结果不得含 2.5 的时长控件。"""
    response = client.get(f"/tab/{tab_name}", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "text/html" in response.headers["Content-Type"]
    body = response.text
    # 独立模板使用 it20 前缀的元素 ID，而非复用 2.5 的 it2 前缀
    assert "it20" in body, f"{tab_name} 未渲染 2.0 独立模板"
    assert "duration_mode" not in body, f"{tab_name} 不应出现 2.0 不支持的时长控制"


def test_tab_form_actions_point_at_registered_routes(client):
    """所有 tab 模板里的 hx-post 目标必须是应用真实注册的路由。

    WHY：历史上 indextts2_clone / indextts2_emotion / indextts2_duration 三个表单
    分别 POST 到 /api/generate/indextts2/{clone,emotion,duration}，而后端只注册了
    /api/generate/indextts2 一个端点（按字段是否非空隐式分派），
    导致 IndexTTS 侧栏页面点「生成」全部 404、完全无法输出音频。
    端点集合与模板是两处代码，编译期不会报错，只能靠这条断言守住。
    """
    registered = set(client.get("/openapi.json").json()["paths"])
    pattern = re.compile(r'hx-post="(/api[^"{}]*)"')
    offenders: list[str] = []
    checked = 0
    for template in sorted(_TABS_DIR.glob("*.html")):
        for target in pattern.findall(template.read_text(encoding="utf-8")):
            checked += 1
            if target not in registered and target not in _KNOWN_UNIMPLEMENTED_ENDPOINTS:
                offenders.append(f"{template.name} -> {target}")
    assert checked > 0, "未在任何 tab 模板中找到 hx-post 表单，断言可能已失效"
    assert offenders == [], f"表单提交了未注册的端点：{offenders}"


def test_persona_save_gap_is_still_the_only_known_one(client):
    """防止 _KNOWN_UNIMPLEMENTED_ENDPOINTS 变成垃圾桶。

    若有人把欠债端点真正实现了，就该把它从清单里删掉；
    若清单被扩充去掩盖新坏掉的 URL，这条测试会失败。
    """
    registered = set(client.get("/openapi.json").json()["paths"])
    implemented_but_still_listed = sorted(p for p in _KNOWN_UNIMPLEMENTED_ENDPOINTS if p in registered)
    assert implemented_but_still_listed == [], (
        f"这些端点已注册，应从 _KNOWN_UNIMPLEMENTED_ENDPOINTS 移除：{implemented_but_still_listed}"
    )


# ---------------------------------------------------------------------------
# POST /api/persona/save —— 三个模板的「保存音色」按钮的后端
# ---------------------------------------------------------------------------


def _csrf_headers(client) -> dict[str, str]:
    """取 CSRF cookie 并组装成中间件要求的头。"""
    client.get("/")
    return {"X-CSRF-Token": client.cookies.get("csrf_token") or ""}


#: 最小合法 WAV 头（RIFF + WAVE + fmt 块），用于通过 save_uploaded_audio 的
#: fail-closed 魔术字节校验；纯占位字节会被判为「伪装文件」而拒绝。
_WAV_BYTES: bytes = (
    b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00LIST\x1a\x00\x00\x00INFOISFT"
)


def test_persona_save_route_is_registered(client):
    assert "/api/persona/save" in client.get("/openapi.json").json()["paths"]


def test_persona_save_requires_name(client):
    resp = client.post("/api/persona/save", data={"save_name": "  "}, headers=_csrf_headers(client))
    assert resp.status_code == 200
    assert "名称" in resp.text


def test_persona_save_requires_audio_source(client):
    """既无上传也无生成结果时必须明确拒绝，而不是静默成功。"""
    resp = client.post("/api/persona/save", data={"save_name": "测试音色"}, headers=_csrf_headers(client))
    assert resp.status_code == 200
    assert "缺少音频" in resp.text


def test_persona_save_rejects_path_traversal_in_result_audio(client):
    """result_audio 来自客户端，绝不能成为任意文件读取的入口。"""
    resp = client.post(
        "/api/persona/save",
        data={"save_name": "测试音色", "result_audio": "../../../etc/passwd"},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200
    assert "无效" in resp.text or "已被清理" in resp.text


def test_persona_save_rejects_unsupported_extension(client):
    """扩展名不在白名单时必须拒绝。

    WHY 断言 200 而不是 400：save_uploaded_audio 的 _error_html 返回 400，
    但 HTMX 只把 2xx 换进 status 容器、app_init.js 的 responseError 监听器不渲染
    响应体，400 等于静默失败；因此端点把原提示以 200 片段回传，保证用户看得见。
    """
    resp = client.post(
        "/api/persona/save",
        data={"save_name": "测试音色"},
        files={"ref_audio": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200
    assert "不支持的音频格式" in resp.text
    # 回归：_error_html 的 HX-Trigger 曾因 json.dumps(ensure_ascii=False) 生成含中文的
    # 头值、而 HTTP 头只允许 latin-1，抛 UnicodeEncodeError 被静默降级吞掉，
    # 导致全站 toast 从未生效。这里断言头确实存在且已是 ASCII 安全。
    trigger = resp.headers.get("HX-Trigger")
    assert trigger is not None
    trigger.encode("latin-1")
    assert "tts-toast" in trigger


def test_persona_save_rejects_disguised_file(client):
    """扩展名与魔术字节不符时必须拒绝（继承 save_uploaded_audio 的 fail-closed 校验）。"""
    resp = client.post(
        "/api/persona/save",
        data={"save_name": "伪装", "ref_text": "x"},
        files={"ref_audio": ("payload.wav", b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff", "audio/wav")},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200
    assert "不匹配" in resp.text


def test_persona_save_passes_upload_and_overwrite_to_manager(client, monkeypatch):
    """确认路由把「已落盘的音频路径」、描述与 overwrite 正确透传给固化能力层。

    WHY 断言路径而不是 bytes：fn_save_persona 的 docstring 声称支持 bytes，
    但底层 preprocess_and_save_temp 只接受 路径/UploadFile/ndarray，
    传 bytes 会直接失败，因此路由必须先经 save_uploaded_audio 落盘再传路径。
    """
    captured: dict[str, object] = {}

    def fake_save(name, audio_input, ref_text, overwrite=False):
        captured["name"] = name
        captured["audio_input"] = audio_input
        captured["existed_at_call"] = bool(audio_input) and __import__("os").path.isfile(str(audio_input))
        captured["ref_text"] = ref_text
        captured["overwrite"] = overwrite
        return "✅ 已保存", False

    monkeypatch.setattr("integrated_app.routes.persona.fn_save_persona", fake_save)

    resp = client.post(
        "/api/persona/save",
        data={"save_name": "  我的音色  ", "ref_text": "温柔女声", "overwrite": "true"},
        files={"ref_audio": ("a.wav", _WAV_BYTES, "audio/wav")},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200
    assert captured["name"] == "我的音色"
    assert isinstance(captured["audio_input"], str)
    assert str(captured["audio_input"]).endswith(".wav")
    assert captured["existed_at_call"] is True
    assert captured["ref_text"] == "温柔女声"
    assert captured["overwrite"] is True


def test_persona_save_emits_confirm_header_on_duplicate(client, monkeypatch):
    """重名且未确认覆盖时，必须回 X-Persona-Confirm 头驱动前端翻隐藏字段。"""
    monkeypatch.setattr(
        "integrated_app.routes.persona.fn_save_persona",
        lambda name, audio_input, ref_text, overwrite=False: ("⚠️ 音色已存在", True),
    )
    resp = client.post(
        "/api/persona/save",
        data={"save_name": "dup", "ref_text": "x"},
        files={"ref_audio": ("a.wav", _WAV_BYTES, "audio/wav")},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200
    assert resp.headers.get("X-Persona-Confirm") == "1"


def test_persona_save_does_not_leak_internal_exception(client, monkeypatch):
    """固化失败不得把内部异常文本回显给用户。"""

    def boom(name, audio_input, ref_text, overwrite=False):
        raise RuntimeError("secret absolute path /home/x/personas/boom.wav")

    monkeypatch.setattr("integrated_app.routes.persona.fn_save_persona", boom)
    resp = client.post(
        "/api/persona/save",
        data={"save_name": "x", "ref_text": "y"},
        files={"ref_audio": ("a.wav", _WAV_BYTES, "audio/wav")},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200
    assert "secret" not in resp.text
    assert "保存失败" in resp.text
