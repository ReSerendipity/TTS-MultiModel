"""HTTP 层复验 #155 的两处收敛：越界名字与异常文本都到不了响应体。

单元级断言（`test_path_guard.py` / `test_error_surface_leaks.py`）只证明函数本身；
这里经真实 ASGI 栈（CSRF 中间件 + 异常处理 + 路由匹配）跑一遍，锁三件事：

1. 非法音色名不会变成 500，也不会在磁盘上留下任何东西；
2. 失败响应里不含服务端绝对路径；
3. 拒绝时仍给出可操作提示（用户知道该改什么）。

不碰 GPU：conftest 已把 ``CUDA_VISIBLE_DEVICES`` 置空并关掉自动加载模型。
"""

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_APP_DIR = str(_PROJECT_ROOT / "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

#: 响应体里一出现就说明服务端目录结构外泄了
LEAK_MARKERS = ("C:\\", "D:\\", "/home/", "/Users/", "/srv/", "/app/", str(_PROJECT_ROOT))

#: 带目录成分的裸名：``%2F`` 那几条在路由层就匹配不到（404），另两条进白名单（400）
EVIL_NAMES = ["..%2Fevil", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "sub%2Fdir", ".hidden", "a" * 80]

#: 最小合法 WAV 头（与 test_routes_htmx 同源），用于过 save_uploaded_audio 的魔术字节
_WAV_BYTES: bytes = (
    b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00LIST\x1a\x00\x00\x00INFOISFT"
)


def _csrf_headers(client) -> dict[str, str]:
    """先 GET 一次拿 ``csrf_token`` Cookie。

    少了这一步，所有写请求都以 CSRF 403 结束，下面的断言就成了"假通过" ——
    正向对照（``test_legit_name_reaches_the_handler``）就是用来证明这条链真的通了。
    """
    client.get("/")
    return {"X-CSRF-Token": client.cookies.get("csrf_token") or ""}


def _assert_no_server_path(body: str) -> None:
    for marker in LEAK_MARKERS:
        assert marker not in body, body[:400]


class TestPersonaDeleteSurface:
    def test_legit_name_reaches_the_handler(self, client):
        """正向对照：合规但不存在的名字要走到业务分支并回「不存在」。"""
        resp = client.request("DELETE", "/api/persona/no_such_persona", headers=_csrf_headers(client))

        assert resp.status_code == 400, resp.text[:200]
        assert "不存在" in resp.text, resp.text[:200]

    @pytest.mark.parametrize("name", EVIL_NAMES)
    def test_illegal_names_never_500_or_leak(self, client, name):
        resp = client.request("DELETE", f"/api/persona/{name}", headers=_csrf_headers(client))

        assert resp.status_code in (400, 404), (name, resp.status_code, resp.text[:200])
        _assert_no_server_path(resp.text)

    def test_rejection_still_gives_actionable_hint(self, client):
        """白名单拒绝时必须说清规则，不能只回「失败」。"""
        resp = client.request("DELETE", "/api/persona/bad%20name%21", headers=_csrf_headers(client))

        assert resp.status_code == 400
        assert "1-50" in resp.text, resp.text[:300]


class TestSettingsErrorSurface:
    def test_permission_error_returns_fixed_hint(self, client, monkeypatch):
        from integrated_app.routes.system import settings

        async def _denied(*_a, **_k):
            raise OSError(13, "Permission denied", "/srv/tts/config/generation_defaults.json")

        monkeypatch.setattr(settings, "_load_json_file", _denied)
        resp = client.get("/api/system/generation_defaults")

        assert resp.status_code == 200
        assert resp.json()["status"] == "error"
        assert "/srv/tts" not in resp.text
        _assert_no_server_path(resp.text)

    def test_unknown_error_redacts_to_placeholder(self, client, monkeypatch):
        from integrated_app.routes.system import settings

        async def _boom(*_a, **_k):
            raise RuntimeError("解析 /srv/tts/config/generation_defaults.json 失败")

        monkeypatch.setattr(settings, "_load_json_file", _boom)
        resp = client.get("/api/system/generation_defaults")

        assert "[PATH]" in resp.text, resp.text[:200]
        assert "/srv/tts" not in resp.text


class TestPersonaSaveSurface:
    def test_traversal_name_rejected_without_writing_anything(self, client, tmp_path, monkeypatch):
        from integrated_app import persona_manager as pm

        root = tmp_path / "personas"
        root.mkdir()
        monkeypatch.setattr(pm, "PERSONA_DIR", str(root))

        resp = client.post(
            "/api/persona/save",
            data={"save_name": "../escaped", "ref_text": "x", "has_consent": "true"},
            files={"ref_audio": ("a.wav", _WAV_BYTES, "audio/wav")},
            headers=_csrf_headers(client),
        )

        assert resp.status_code == 200, resp.text[:200]
        assert "不合法" in resp.text, resp.text[:300]
        assert list(root.iterdir()) == [], "非法名不得在 PERSONA_DIR 内留下半截文件"
        assert not (tmp_path / "escaped.wav").exists(), "非法名不得写到 PERSONA_DIR 之外"
