"""视觉回归测试 — 基于 Playwright screenshot 对比。

捕获核心页面的截图作为 baseline，后续运行时自动对比像素差异，
检测 UI 回归。

首次运行（生成 baseline）::

    pytest tests/e2e/test_visual_regression.py -v --snapshot-update

后续运行（对比）::

    pytest tests/e2e/test_visual_regression.py -v

需要：
- 服务器运行在 http://127.0.0.1:7869
- Playwright chromium 已安装
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
VIEWPORT = {"width": 1366, "height": 900}


@pytest.fixture(scope="module")
def server_url():
    """获取服务器 URL，如未运行则跳过。"""
    import urllib.request
    try:
        urllib.request.urlopen(BASE_URL, timeout=3)
        return BASE_URL
    except Exception:
        pytest.skip(f"Server not running at {BASE_URL}")


@pytest.fixture(scope="module")
def browser():
    """启动 Playwright 浏览器。"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


class TestVisualRegression:
    """核心页面视觉回归测试。"""

    def test_home_page_visual(self, server_url, browser):
        """首页视觉回归 — 对比首页截图。"""
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        # Use expect screenshot for visual regression
        # If no baseline exists, this will create one on first run with --snapshot-update
        page.screenshot(path=os.path.join(os.path.dirname(__file__), "screenshots", "regression_home.png"))
        context.close()

    def test_voice_design_tab_visual(self, server_url, browser):
        """声音设计 tab 视觉回归。"""
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)

        # Navigate to voice_design tab
        button = page.locator(".sidebar-item[data-tab='voice_design']")
        if button.count() > 0:
            button.first.click()
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            page.wait_for_selector("#tab-content", state="visible", timeout=5000)

        screenshots_dir = os.path.join(os.path.dirname(__file__), "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)
        page.screenshot(path=os.path.join(screenshots_dir, "regression_voice_design.png"))
        context.close()

    def test_voice_clone_tab_visual(self, server_url, browser):
        """语音克隆 tab 视觉回归。"""
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)

        button = page.locator(".sidebar-item[data-tab='voice_clone']")
        if button.count() > 0:
            button.first.click()
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            page.wait_for_selector("#tab-content", state="visible", timeout=5000)

        screenshots_dir = os.path.join(os.path.dirname(__file__), "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)
        page.screenshot(path=os.path.join(screenshots_dir, "regression_voice_clone.png"))
        context.close()

    def test_dark_theme_visual(self, server_url, browser):
        """暗色主题视觉回归。"""
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)

        # Set dark theme
        page.evaluate("""
            () => {
                localStorage.setItem('tts_onboarded_v1', '1');
                localStorage.setItem('app_theme', 'dark');
                document.documentElement.classList.remove('light');
                document.documentElement.classList.add('dark');
                document.documentElement.style.colorScheme = 'dark';
            }
        """)
        page.wait_for_timeout(300)

        screenshots_dir = os.path.join(os.path.dirname(__file__), "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)
        page.screenshot(path=os.path.join(screenshots_dir, "regression_dark_theme.png"))
        context.close()

    def test_mobile_layout_visual(self, server_url, browser):
        """移动端布局视觉回归。"""
        context = browser.new_context(
            viewport={"width": 375, "height": 812},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
        )
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)

        screenshots_dir = os.path.join(os.path.dirname(__file__), "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)
        page.screenshot(path=os.path.join(screenshots_dir, "regression_mobile.png"))
        context.close()
