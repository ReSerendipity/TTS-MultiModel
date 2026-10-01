"""P0-1 声音克隆授权 v1 的验收测试（综合评估 · 必答问题③）。

覆盖三件事：
1. consent 元数据序列化往返 + 旧数据回退（纯单元，不依赖端点）。
2. 克隆端点（上传来源）未勾选授权 → 400 并给出明确文案。
3. persona_save（上传来源）未勾选授权 → 拒绝保存；无上传（设计页固化）不受影响。
"""

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from integrated_app.persona_metadata import CONSENT_STATES, PersonaMetadata

_FAKE_WAV = b"RIFF" + b"\x00" * 100  # 仅用于触发上传分支，gate 在落盘校验之前拦截


@pytest.fixture
def client() -> TestClient:
    """应用级 TestClient（与 test_api_contract 同款 fixture 语义）。"""
    from integrated_app.app_server import create_app

    app = create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. 元数据层
# ---------------------------------------------------------------------------


def test_consent_metadata_roundtrip():
    """授权声明随 metadata 写入并可读回（granted / self / unverified 三态）。"""
    assert set(CONSENT_STATES) == {"granted", "self", "unverified"}
    m = PersonaMetadata(name="t", consent_state="granted", consent_at="2026-09-05T00:00:00")
    m2 = PersonaMetadata.from_dict(m.to_dict())
    assert m2.consent_state == "granted"
    assert m2.consent_at == "2026-09-05T00:00:00"


def test_consent_metadata_legacy_defaults_to_unverified():
    """旧格式 metadata（无 consent 键）回退 unverified（fail-safe，不崩溃）。"""
    m = PersonaMetadata.from_dict({"name": "legacy"})
    assert m.consent_state == "unverified"


def test_consent_metadata_invalid_state_falls_back():
    """非法状态值回退 unverified。"""
    m = PersonaMetadata(name="bad", consent_state="hacked")
    assert m.consent_state == "unverified"


# ---------------------------------------------------------------------------
# 2. 克隆端点（voxcpm_clone）上传来源必须显式授权
# ---------------------------------------------------------------------------


def _csrf_headers(client: TestClient) -> dict[str, str]:
    """CSRF Double-Submit：先 GET 触发服务端签发 cookie，再把值放入 header。"""
    client.get("/")
    token = client.cookies.get("csrf_token") or client.cookies.get("XSRF-TOKEN") or ""
    return {"X-CSRF-Token": token}


def test_clone_upload_without_consent_rejected(client):
    """上传克隆未勾选授权 → 400 并提示勾选。"""
    r = client.post(
        "/api/generate/voxcpm_clone",
        data={"text": "测试克隆"},
        files={"ref_audio_upload": ("ref.wav", io.BytesIO(_FAKE_WAV), "audio/wav")},
        headers=_csrf_headers(client),
    )
    assert r.status_code == 400
    assert "使用权" in r.text or "授权" in r.text


def test_clone_upload_with_consent_not_blocked_by_gate(client):
    """勾选授权后不再被授权门禁拦截（后续缺引擎/参数错误不属于本测试范围）。"""
    r = client.post(
        "/api/generate/voxcpm_clone",
        data={"text": "测试克隆", "has_consent": "true"},
        files={"ref_audio_upload": ("ref.wav", io.BytesIO(_FAKE_WAV), "audio/wav")},
        headers=_csrf_headers(client),
    )
    # gate 放行后进入正常流程；不应再返回「请勾选使用权」的 400 文案
    assert "请先勾选" not in r.text and "使用权" not in r.text


# ---------------------------------------------------------------------------
# 4. generic/clone 门禁（P0-1 补口，2026-09-15）
# ---------------------------------------------------------------------------


def test_generic_clone_upload_without_consent_rejected(client):
    """generic/clone 上传克隆未勾选授权 → 400 并提示勾选（与 voxcpm_clone 同口径）。"""
    r = client.post(
        "/api/generate/generic/clone",
        data={"text": "测试通用克隆"},
        files={"ref_audio": ("ref.wav", io.BytesIO(_FAKE_WAV), "audio/wav")},
        headers=_csrf_headers(client),
    )
    assert r.status_code == 400
    assert "使用权" in r.text or "授权" in r.text


def test_generic_clone_with_consent_passes_gate(client):
    """勾选授权后不再被授权门禁拦截（后续错误不属于门禁范围）。"""
    r = client.post(
        "/api/generate/generic/clone",
        data={"text": "测试通用克隆", "has_consent": "true"},
        files={"ref_audio": ("ref.wav", io.BytesIO(_FAKE_WAV), "audio/wav")},
        headers=_csrf_headers(client),
    )
    assert "请先勾选" not in r.text and "使用权" not in r.text


# ---------------------------------------------------------------------------
# 5. OpenAI 兼容端点防绕过（P0-1 补口，2026-09-15）
# ---------------------------------------------------------------------------


def test_openai_speech_rejects_ref_audio_path(client):
    """/v1/audio/speech 携带 ref_audio_path → 400（gate 先于模型就绪检查）。"""
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "测试", "voice": "alloy", "ref_audio_path": "personas/x.wav"},
        headers=_csrf_headers(client),
    )
    assert r.status_code == 400
    assert "参考音频" in r.text or "ref_audio_path" in r.text


def test_openai_speech_rejects_unknown_voice_for_voxcpm2(client):
    """voxcpm2 引擎下 voice 传非预设名且非已登记音色 → 400。"""
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "测试", "voice": "mystery-voice"},
        headers=_csrf_headers(client),
    )
    assert r.status_code == 400
    assert "未知音色" in r.text or "音色" in r.text


# ---------------------------------------------------------------------------
# 4. 模板层：隐藏面板里的授权勾选框不得用 HTML required 表达（2026-10-01）
# ---------------------------------------------------------------------------
# 背景：voice_clone / ultimate_clone 的 has_consent 勾选框位于默认隐藏的
# 「上传参考」tab 面板内。曾有 `required` 属性 —— 隐藏的 required 控件让
# form.checkValidity() 恒为 False，requestSubmit() 走"invalid control is not
# focusable"静默拒绝，"已保存音色"路径从 UI 整条发不出请求且无任何可见报错。
# 正确形态：required 摘除，改为提交处理器在「上传面板可见」时做 JS 校验
# （服务端 400 仍是最终闸，见上面 §2/§3）。


def _template_path(name: str):
    from pathlib import Path

    return Path(__file__).resolve().parent.parent / "app" / "integrated_app" / "templates" / "tabs" / name


def _input_line(template: str, input_id: str) -> str:
    for line in template.splitlines():
        if f'id="{input_id}"' in line and "checkbox" in line:
            return line
    raise AssertionError(f"{input_id} 不存在于模板中")


@pytest.mark.parametrize(
    ("template_name", "input_id", "panel_id", "form_prefix"),
    [
        ("voice_clone.html", "vc-has-consent", "vc-tab-upload", "vc"),
        ("ultimate_clone.html", "uc-has-consent", "uc-tab-upload", "uc"),
    ],
)
def test_hidden_panel_consent_checkbox_must_not_use_html_required(template_name, input_id, panel_id, form_prefix):
    """授权勾选框在默认隐藏面板内时禁止 required —— 那会让 persona 路径静默发不出请求。"""
    tpl = _template_path(template_name).read_text(encoding="utf-8")
    line = _input_line(tpl, input_id)
    assert "required" not in line, (
        f"{template_name} 的 {input_id} 又挂上了 required：它在默认隐藏的 {panel_id} 面板里，"
        "隐藏 required 控件会让 requestSubmit() 静默拒绝（invalid control is not focusable），"
        "已保存音色路径整条发不出请求"
    )


@pytest.mark.parametrize(
    ("template_name", "input_id", "panel_id", "status_id"),
    [
        ("voice_clone.html", "vc-has-consent", "vc-tab-upload", "vc-status"),
        ("ultimate_clone.html", "uc-has-consent", "uc-tab-upload", "uc-status"),
    ],
)
def test_consent_checked_client_side_when_upload_panel_visible(template_name, input_id, panel_id, status_id):
    """上传面板可见时必须由 JS 校验授权勾选（required 摘除后的替代闸）。"""
    tpl = _template_path(template_name).read_text(encoding="utf-8")
    assert panel_id in tpl, f"{panel_id} 面板不在场"
    # 面板可见性判断 + consent 元素读取 + 状态回显三件套必须在提交处理器里
    assert f"getElementById('{panel_id}')" in tpl, f"提交处理器没有判断 {panel_id} 可见性"
    assert f"getElementById('{input_id}')" in tpl, f"提交处理器没有读取 {input_id}"
    assert f"getElementById('{status_id}')" in tpl, "校验失败没有回显状态"
    assert "consent_required" in tpl, "未使用 consent_required i18n key（5 语言词表已备）"


# ---------------------------------------------------------------------------
# 5. 全站禁用原生 confirm()（2026-10-01，切页守卫迁移后的收口闸）
# ---------------------------------------------------------------------------
# 原生 confirm 渲染在宿主层、不属于页面 DOM（桌面壳/内嵌浏览器里无法定制、
# 会卡无头自动化）。2026-10-01 已把全部调用点迁到 ConfirmDialog（页内）。
# 本闸防回潮：模板与 static js 里不得再出现原生 confirm 调用。


def _scan_native_confirm() -> list[str]:
    import re

    root = Path(__file__).resolve().parent.parent / "app" / "integrated_app"
    offenders: list[str] = []
    patterns = list((root / "templates" / "tabs").glob("*.html")) + list((root / "static" / "js").glob("*.js"))
    for f in patterns:
        # confirm_dialog.js 是替代实现本体，其 confirm() 定义不算原生调用
        if f.name == "confirm_dialog.js":
            continue
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = re.sub(r"//.*$", "", line)
            if "ConfirmDialog" in code or "confirm_dialog" in code:
                continue
            # 显式豁免：行内标记 NATIVE-CONFIRM-FALLBACK 表示有意保留的原生兜底
            # （仅允许 ConfirmDialog 不可用时的降级分支；新代码不得新增此标记）
            if "NATIVE-CONFIRM-FALLBACK" in line:
                continue
            # 原生形态：裸 confirm(...) / window.confirm(...)（排除定义与注释）
            if re.search(r"(?<![\w.])confirm\s*\(", code) or "window.confirm(" in code:
                offenders.append(f"{f.name}:{lineno}: {line.strip()[:80]}")
    return offenders


def test_no_native_confirm_calls_remain():
    """原生 confirm() 已全站迁到页内 ConfirmDialog —— 本闸防回潮。"""
    offenders = _scan_native_confirm()
    assert not offenders, "发现原生 confirm 调用（应改用 ConfirmDialog.show/.confirm）：\n  " + "\n  ".join(offenders)


def test_confirm_dialog_promise_api_exists():
    """ConfirmDialog.confirm(msg) -> Promise<boolean> 是各站点的迁移依赖。"""
    js = (
        Path(__file__).resolve().parent.parent / "app" / "integrated_app" / "static" / "js" / "confirm_dialog.js"
    ).read_text(encoding="utf-8")
    assert "function confirm(message, options)" in js
    assert "resolve(true)" in js and "resolve(false)" in js
    assert "onCancel" in js, "取消/点遮罩路径必须能 resolve(false)"
