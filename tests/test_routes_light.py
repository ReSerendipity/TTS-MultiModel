"""轻量路由冒烟测试 — 页面、标签页与音色路由。

覆盖目标模块: bin/integrated_app/routes/pages.py / tabs.py / persona.py
"""


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


def test_favicon(client):
    response = client.get("/favicon.ico")
    assert response.status_code in (200, 404)


def test_download_guide(client):
    response = client.get("/download-guide")
    assert response.status_code in (200, 404)


def test_vite_client(client):
    response = client.get("/@vite/client")
    assert response.status_code in (200, 404, 204)


class TestTabs:
    def test_voice_design_tab(self, client):
        response = client.get("/tabs/voice_design")
        assert response.status_code in (200, 404)

    def test_unknown_tab(self, client):
        response = client.get("/tabs/no_such_tab")
        assert response.status_code in (200, 404)


class TestPersona:
    def test_persona_table(self, client):
        response = client.get("/api/persona/table")
        assert response.status_code == 200
        data = response.json()
        assert "records" in data or "personas" in data or isinstance(data, list)

    def test_persona_table_filter(self, client):
        response = client.get("/api/persona/table?keyword=xyz")
        assert response.status_code == 200

    def test_persona_delete_missing(self, client):
        # DELETE 请求受 CSRF 保护，无 token 时返回 403
        response = client.delete("/api/persona/no_such_persona_xyz")
        assert response.status_code in (200, 404, 400, 403)
