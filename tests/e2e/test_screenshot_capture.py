"""Pytest-based screenshot capture for TTS MultiModel tabs.

Replaces the Node.js ``capture-screenshots.js`` script with a native pytest
implementation that reuses Playwright fixtures and integrates into the
existing test suite.

Captures each core tab in both light and dark themes::

    pytest tests/e2e/test_screenshot_capture.py -v

Requires:
- Server running at http://127.0.0.1:7869
- Playwright chromium installed
"""

import os

import pytest

try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="Playwright not installed. Install with: pip install playwright && playwright install",
)

BASE_URL = os.environ.get("TTS_SERVER_URL", "http://127.0.0.1:7869")

# Output directory: docs/screenshots/
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(ROOT, "docs", "screenshots")
DARK_DIR = os.path.join(OUTPUT_DIR, "dark")

TABS = [
    {"num": "01", "tab": "voice_design", "name": "voice_design"},
    {"num": "02", "tab": "voice_clone", "name": "voice_clone"},
    {"num": "03", "tab": "ultimate_clone", "name": "ultimate_clone"},
    {"num": "04", "tab": "script", "name": "script_workshop"},
    {"num": "05", "tab": "prompt_continue", "name": "prompt_continuation"},
    {"num": "06", "tab": "lora", "name": "lora"},
    {"num": "07", "tab": "lora_training", "name": "lora_training"},
    {"num": "08", "tab": "settings", "name": "settings"},
    {"num": "09", "tab": "history", "name": "history"},
    {"num": "10", "tab": "persona", "name": "persona_library"},
    {"num": "11", "tab": "help", "name": "help"},
]

VIEWPORT = {"width": 1366, "height": 900}


@pytest.fixture(scope="module")
def server_url():
    """Skip if server not running."""
    import urllib.request

    try:
        urllib.request.urlopen(BASE_URL, timeout=3)
        return BASE_URL
    except Exception:
        pytest.skip(f"Server not running at {BASE_URL}")


@pytest.fixture(scope="module")
def browser():
    """Launch Playwright browser."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


def _set_theme(page, theme):
    """Set theme and dismiss onboarding wizard."""
    page.evaluate(f"""
        () => {{
            localStorage.setItem('tts_onboarded_v1', '1');
            localStorage.setItem('app_theme', '{theme}');
            document.documentElement.classList.remove('dark', 'light');
            document.documentElement.classList.add('{theme}');
            document.documentElement.style.colorScheme = '{theme}';
        }}
    """)
    page.wait_for_timeout(300)


def _click_tab(page, tab_name):
    """Click a sidebar tab and wait for HTMX swap."""
    button = page.locator(f".sidebar-item[data-tab='{tab_name}']")
    if button.count() == 0:
        return False
    # 若按钮位于折叠分组（.sidebar-nav-section.section-collapsed）内，
    # 先点击分组标题 .sidebar-nav-label 展开，否则 display:none 无法点击
    page.evaluate(
        """(t) => {
        const btn = document.querySelector(`.sidebar-item[data-tab="${t}"]`);
        if (!btn) return;
        const sec = btn.closest('.sidebar-nav-section');
        if (sec && sec.classList.contains('section-collapsed')) {
            const label = sec.querySelector('.sidebar-nav-label');
            if (label) label.click();
        }
    }""",
        tab_name,
    )
    page.wait_for_timeout(400)
    button.first.click()
    page.wait_for_load_state("domcontentloaded", timeout=15000)
    page.wait_for_timeout(1200)
    return True


class TestScreenshotCapture:
    """Capture screenshots of all tabs in light and dark themes."""

    def test_capture_home_light(self, server_url, browser):
        """Capture home page in light theme."""
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(800)
        _set_theme(page, "light")

        filepath = os.path.join(OUTPUT_DIR, "home_viewport.png")
        page.screenshot(path=filepath, full_page=True)
        assert os.path.exists(filepath)
        context.close()

    def test_capture_home_dark(self, server_url, browser):
        """Capture home page in dark theme."""
        os.makedirs(DARK_DIR, exist_ok=True)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(800)
        _set_theme(page, "dark")

        filepath = os.path.join(DARK_DIR, "home_dark_viewport.png")
        page.screenshot(path=filepath, full_page=True)
        assert os.path.exists(filepath)
        context.close()

    @pytest.mark.parametrize("tab", TABS)
    def test_capture_tab_light(self, server_url, browser, tab):
        """Capture each tab in light theme."""
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(800)
        _set_theme(page, "light")

        clicked = _click_tab(page, tab["tab"])
        if not clicked:
            context.close()
            pytest.skip(f"Tab {tab['tab']} not found")

        filename = f"voxcpm2_{tab['num']}_{tab['name']}_viewport.png"
        filepath = os.path.join(OUTPUT_DIR, filename)
        page.screenshot(path=filepath, full_page=True)
        assert os.path.exists(filepath), f"Screenshot not saved: {filepath}"
        context.close()

    @pytest.mark.parametrize("tab", TABS)
    def test_capture_tab_dark(self, server_url, browser, tab):
        """Capture each tab in dark theme."""
        os.makedirs(DARK_DIR, exist_ok=True)
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        page.wait_for_timeout(800)
        _set_theme(page, "dark")

        clicked = _click_tab(page, tab["tab"])
        if not clicked:
            context.close()
            pytest.skip(f"Tab {tab['tab']} not found")

        filename = f"voxcpm2_{tab['num']}_{tab['name']}_dark_viewport.png"
        filepath = os.path.join(DARK_DIR, filename)
        page.screenshot(path=filepath, full_page=True)
        assert os.path.exists(filepath), f"Screenshot not saved: {filepath}"
        context.close()
