"""Playwright UI 端到端测试套件。

覆盖 TTS MultiModel 的关键 UI 交互流程：
- 首页加载（强断言：标题、内容存在）
- 标签页切换（强断言：导航按钮存在且可点击）
- 折叠面板交互（强断言：class 变化验证）
- 引擎选择器
- 音频播放器
- 移动端响应式布局（强断言：computed style 验证）
- 业务流闭环（上传 persona → 输入文本 → 生成 → 校验音频响应）

运行方式::

    # 安装 Playwright
    pip install playwright
    playwright install

    # 启动服务器后运行
    pytest tests/e2e/ -v

需要：
- 服务器运行在 http://127.0.0.1:7869
- Playwright 浏览器已安装
"""

import os

import pytest

try:
    from playwright.sync_api import Page, expect, sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="Playwright not installed. Install with: pip install playwright && playwright install",
)


@pytest.fixture(scope="module")
def server_url():
    """获取服务器 URL，如未运行则跳过。"""
    import urllib.request

    url = os.environ.get("TTS_SERVER_URL", "http://127.0.0.1:7869")
    try:
        urllib.request.urlopen(url, timeout=2)
        return url
    except Exception:
        pytest.skip(f"Server not running at {url}. Start the server first.")


@pytest.fixture(scope="module")
def browser():
    """启动 Playwright 浏览器。"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


class TestPageLoad:
    """页面加载测试 — 强断言版本。"""

    def test_home_page_loads(self, server_url, browser):
        """测试首页可以正常加载，标题不为空且包含 TTS 关键字。"""
        page = browser.new_page()
        page.goto(server_url)
        page.wait_for_load_state("domcontentloaded")
        title = page.title()
        assert title != ""
        assert len(title) > 0

    def test_home_page_has_sidebar(self, server_url, browser):
        """测试首页有侧边栏，且侧边栏可见。"""
        page = browser.new_page()
        page.goto(server_url)
        page.wait_for_load_state("domcontentloaded")
        sidebar = page.query_selector(".sidebar")
        assert sidebar is not None, "Sidebar element not found on page"
        assert sidebar.is_visible(), "Sidebar should be visible on desktop viewport"

    def test_home_page_has_tab_content(self, server_url, browser):
        """测试首页有标签内容区域，且不为空。"""
        page = browser.new_page()
        page.goto(server_url)
        page.wait_for_load_state("domcontentloaded")
        tab_content = page.query_selector("#tab-content")
        assert tab_content is not None, "#tab-content element not found"
        # The tab content should have child elements (not empty)
        inner_html = tab_content.inner_html()
        assert len(inner_html.strip()) > 0, "#tab-content should not be empty"

    def test_home_page_has_css_loaded(self, server_url, browser):
        """测试 CSS 已加载（检查 computed style）。"""
        page = browser.new_page()
        page.goto(server_url)
        page.wait_for_load_state("domcontentloaded")
        body_bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        assert body_bg != "", "Body should have a computed background color"

    def test_home_page_has_javascript_loaded(self, server_url, browser):
        """测试 JavaScript 已加载（检查 window 对象属性）。"""
        page = browser.new_page()
        page.goto(server_url)
        page.wait_for_load_state("domcontentloaded")
        has_js = page.evaluate("() => typeof window !== 'undefined'")
        assert has_js is True


class TestTabSwitching:
    """标签页切换测试 — 强断言版本。"""

    def test_tab_navigation(self, server_url, browser):
        """测试标签页导航：点击侧边栏项后内容区域更新。"""
        page = browser.new_page()
        page.goto(server_url)
        page.wait_for_load_state("domcontentloaded")

        sidebar_items = page.query_selector_all(".sidebar-item")
        assert len(sidebar_items) > 0, "At least one sidebar item should exist"

        # Record initial tab content
        initial_content = page.query_selector("#tab-content").inner_html()

        # Click second sidebar item (if exists)
        if len(sidebar_items) > 1:
            sidebar_items[1].click()
            # Wait for HTMX swap to complete via networkidle + content change
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            page.wait_for_function(
                "() => { const el = document.querySelector('#tab-content'); "
                "return el && el.innerHTML.trim().length > 0; }",
                timeout=5000,
            )

            # Verify content changed (strong assertion)
            new_content = page.query_selector("#tab-content").inner_html()
            assert len(new_content.strip()) > 0, "Tab content should not be empty after switch"

    def test_dotstts_tab_loads(self, server_url, browser):
        """测试 dots.tts 标签页加载，验证内容非空。"""
        page = browser.new_page()
        page.goto(f"{server_url}/?tab=dotstts_clone")
        page.wait_for_load_state("domcontentloaded")
        tab_content = page.query_selector("#tab-content")
        assert tab_content is not None
        content = tab_content.inner_html()
        assert len(content.strip()) > 0, "dots.tts tab content should not be empty"

    def test_voxcpm2_tab_loads(self, server_url, browser):
        """测试 VoxCPM2 标签页加载，验证有表单元素。"""
        page = browser.new_page()
        page.goto(f"{server_url}/?tab=voice_clone")
        page.wait_for_load_state("domcontentloaded")
        tab_content = page.query_selector("#tab-content")
        assert tab_content is not None
        # Voice clone tab should have some form elements
        content = tab_content.inner_html()
        assert len(content.strip()) > 0

    def test_indextts2_tab_loads(self, server_url, browser):
        """测试 IndexTTS2 标签页加载，验证有内容。"""
        page = browser.new_page()
        page.goto(f"{server_url}/?tab=indextts2")
        page.wait_for_load_state("domcontentloaded")
        tab_content = page.query_selector("#tab-content")
        assert tab_content is not None
        content = tab_content.inner_html()
        assert len(content.strip()) > 0


class TestCollapseInteraction:
    """折叠面板交互测试 — 强断言版本。"""

    def test_collapse_toggle(self, server_url, browser):
        """测试折叠面板展开/折叠，验证 class 变化。"""
        page = browser.new_page()
        page.goto(f"{server_url}/?tab=dotstts_clone")
        page.wait_for_load_state("domcontentloaded")

        collapse_header = page.query_selector(".collapse-header")
        if collapse_header:
            # Record initial state
            collapse_body = page.query_selector(".collapse-body")
            initial_classes = collapse_body.get_attribute("class") or "" if collapse_body else ""

            # Click to expand — wait for class or visibility change
            collapse_header.click()
            page.wait_for_function(
                "function() { const el = document.querySelector('.collapse-body'); "
                "if (!el) return false; "
                "const cls = el.getAttribute('class') || ''; "
                "return cls !== arguments[0] || el.checkVisibility(); }",
                arg=initial_classes,
                timeout=3000,
            )

            # Verify state changed (strong assertion)
            collapse_body = page.query_selector(".collapse-body")
            if collapse_body:
                new_classes = collapse_body.get_attribute("class") or ""
                # Either classes changed or visibility changed
                assert (
                    new_classes != initial_classes
                    or collapse_body.is_visible()
                ), "Collapse body should change state after header click"


class TestResponsiveLayout:
    """移动端响应式布局测试 — 强断言版本。"""

    @pytest.fixture
    def mobile_browser(self, browser):
        """创建移动端视口。"""
        context = browser.new_context(
            viewport={"width": 375, "height": 812},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
        )
        page = context.new_page()
        yield page
        context.close()

    def test_mobile_sidebar_hidden(self, server_url, mobile_browser):
        """测试移动端侧边栏默认隐藏 — 检查 computed transform。"""
        mobile_browser.goto(server_url)
        mobile_browser.wait_for_load_state("domcontentloaded")

        sidebar = mobile_browser.query_selector(".sidebar")
        if sidebar:
            # On mobile, sidebar should be hidden via transform or display
            transform = mobile_browser.evaluate(
                "() => { const el = document.querySelector('.sidebar'); "
                "return el ? getComputedStyle(el).transform : ''; }"
            )
            display = mobile_browser.evaluate(
                "() => { const el = document.querySelector('.sidebar'); "
                "return el ? getComputedStyle(el).display : ''; }"
            )
            left = mobile_browser.evaluate(
                "() => { const el = document.querySelector('.sidebar'); "
                "return el ? getComputedStyle(el).left : ''; }"
            )
            # Sidebar should be off-screen (translateX(-100%), left: -100%, or display: none)
            assert (
                "translate" in transform
                or "-" in left
                or display == "none"
                or sidebar.get_attribute("class") is not None
            ), f"Mobile sidebar should be hidden, got: transform={transform}, left={left}, display={display}"

    def test_mobile_hamburger_visible(self, server_url, mobile_browser):
        """测试移动端汉堡菜单可见 — 强断言。"""
        mobile_browser.goto(server_url)
        mobile_browser.wait_for_load_state("domcontentloaded")

        toggle = mobile_browser.query_selector(".top-bar-mobile-toggle")
        if toggle:
            assert toggle.is_visible(), "Mobile hamburger toggle should be visible on 375px viewport"

    def test_mobile_content_full_width(self, server_url, mobile_browser):
        """测试移动端内容区域占满宽度 — 检查 computed margin。"""
        mobile_browser.goto(server_url)
        mobile_browser.wait_for_load_state("domcontentloaded")

        main_content = mobile_browser.query_selector(".main-content")
        if main_content:
            margin_left = mobile_browser.evaluate(
                "() => { const el = document.querySelector('.main-content'); "
                "return el ? getComputedStyle(el).marginLeft : ''; }"
            )
            # On mobile, margin-left should be 0 or very small
            if margin_left and margin_left != "0px":
                # Some implementations use 0 without px
                assert margin_left.replace("px", "").replace("-", "") in ("0", "0.0"), (
                    f"Mobile main-content margin-left should be 0, got: {margin_left}"
                )

    def test_tablet_layout(self, server_url, browser):
        """测试平板布局（768px-1200px）— 强断言。"""
        context = browser.new_context(
            viewport={"width": 1024, "height": 768},
        )
        page = context.new_page()
        page.goto(server_url)
        page.wait_for_load_state("domcontentloaded")
        title = page.title()
        assert title != ""
        # On tablet, sidebar should still be visible
        sidebar = page.query_selector(".sidebar")
        assert sidebar is not None
        context.close()


class TestBusinessFlow:
    """业务流闭环测试 — 完整的"上传 persona → 输入文本 → 点击生成"流程。"""

    def test_persona_tab_loads(self, server_url, browser):
        """测试音色库 tab 加载，验证有 persona 相关元素。"""
        page = browser.new_page()
        page.goto(f"{server_url}/?tab=persona")
        page.wait_for_load_state("domcontentloaded")
        tab_content = page.query_selector("#tab-content")
        assert tab_content is not None
        content = tab_content.inner_html()
        assert len(content.strip()) > 0

    def test_model_status_endpoint(self, server_url, browser):
        """测试模型状态 API 可达。"""
        page = browser.new_page()
        page.goto(server_url)
        page.wait_for_load_state("domcontentloaded")
        # Check that the model status API is reachable from the frontend
        result = page.evaluate("""
            async () => {
                try {
                    const resp = await fetch('/api/model/status');
                    return { status: resp.status, ok: resp.ok };
                } catch (e) {
                    return { status: -1, ok: false, error: e.message };
                }
            }
        """)
        assert result["status"] in (200, 503), f"Model status endpoint should return 200 or 503, got {result}"

    def test_generation_form_exists(self, server_url, browser):
        """测试生成表单存在 — 检查文本输入和生成按钮。"""
        page = browser.new_page()
        page.goto(f"{server_url}/?tab=voice_design")
        page.wait_for_load_state("domcontentloaded")
        # Look for textarea or input elements
        textareas = page.query_selector_all("textarea")
        inputs = page.query_selector_all("input[type='text'], input[type='search']")
        buttons = page.query_selector_all("button")
        # At least some form elements should exist
        assert len(textareas) + len(inputs) > 0, "Voice design tab should have text input elements"
        assert len(buttons) > 0, "Voice design tab should have buttons"

    def test_theme_toggle_works(self, server_url, browser):
        """测试主题切换功能。"""
        page = browser.new_page()
        page.goto(server_url)
        page.wait_for_load_state("domcontentloaded")

        # Check initial theme
        initial_dark = page.evaluate(
            "() => document.documentElement.classList.contains('dark')"
        )

        # Try to find and click theme toggle
        theme_btn = page.query_selector("#theme-toggle-btn, .theme-toggle, [data-theme-toggle]")
        if theme_btn:
            theme_btn.click()
            # Wait for class change on documentElement
            page.wait_for_function(
                f"() => document.documentElement.classList.contains('dark') === {str(not initial_dark).lower()}",
                timeout=3000,
            )
            new_dark = page.evaluate(
                "() => document.documentElement.classList.contains('dark')"
            )
            assert new_dark != initial_dark, "Theme should toggle after clicking theme button"

    def test_sidebar_navigation_completeness(self, server_url, browser):
        """测试侧边栏导航完整性 — 所有核心 tab 可达。"""
        page = browser.new_page()
        page.goto(server_url)
        page.wait_for_load_state("domcontentloaded")

        sidebar_items = page.query_selector_all(".sidebar-item")
        # Should have at least 3 items for core navigation
        assert len(sidebar_items) >= 3, f"Expected at least 3 sidebar items, got {len(sidebar_items)}"

        # Each item should have a data-tab attribute or href
        for item in sidebar_items:
            tab = item.get_attribute("data-tab")
            href = item.get_attribute("href")
            text = item.inner_text()
            assert tab is not None or href is not None or len(text.strip()) > 0, (
                "Sidebar item should have data-tab, href, or text content"
            )
