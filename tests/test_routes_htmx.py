"""Tests verifying HTMX request handling for tab/fragment endpoints."""

import re
from pathlib import Path

import pytest

from integrated_app.routes.tabs import _TAB_TEMPLATES

_TABS_DIR = Path(__file__).resolve().parent.parent / "app" / "integrated_app" / "templates" / "tabs"

#: 已知「模板引用了但后端未实现」的端点。列在这里是为了把欠债显式记录在代码里，
#: 而不是让断言整体失效——任何新增的未注册端点仍会被这条测试拦下。
#: /api/persona/save：能力存在于 persona_manager.fn_save_persona / service_layer.create_persona，
#:   但没有 HTTP 路由；voice_design / voice_clone / ultimate_clone 的「保存音色」按钮当前 404。
#:   补该端点涉及文件上传、重名覆盖确认与 name 路径穿越校验，属独立功能，需单独评审。
_KNOWN_UNIMPLEMENTED_ENDPOINTS: frozenset[str] = frozenset({"/api/persona/save"})


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

    若有人把 /api/persona/save 真正实现了，就该把它从欠债清单里删掉；
    若清单被扩充去掩盖新坏掉的 URL，这条测试会失败。
    """
    registered = set(client.get("/openapi.json").json()["paths"])
    implemented_but_still_listed = sorted(p for p in _KNOWN_UNIMPLEMENTED_ENDPOINTS if p in registered)
    assert implemented_but_still_listed == [], (
        f"这些端点已注册，应从 _KNOWN_UNIMPLEMENTED_ENDPOINTS 移除：{implemented_but_still_listed}"
    )
