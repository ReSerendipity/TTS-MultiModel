"""Tests verifying HTMX request handling for tab/fragment endpoints."""


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
