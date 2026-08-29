"""Extended pytest-based screenshot capture with interactive states.

Expands on the baseline ``test_screenshot_capture.py`` by capturing not just
the initial state of each tab, but also meaningful interactive states such as
sub-tabs (Alpine.js ``stabs``), expanded advanced-parameter panels, modals
(help drawer, health panel), model-tab switching and sidebar collapsed state.

The file intentionally re-uses the existing fixtures and conventions in this
``tests/e2e/`` package so the original module stays untouched and this test
file can be removed without side effects.

Run like::

    # Start UI-only test server first (no real model required)
    .\\WPy64-312101\\python\\python.exe app\\start_ui_test.py

    # Then in another terminal
    .\\WPy64-312101\\python\\python.exe -m pytest tests/e2e/test_screenshot_capture_extended.py -v

Output directory: ``docs/screenshots/`` (light) and ``docs/screenshots/dark/`` (dark),
suffixed with the interactive-state identifier so baseline and extended files never
collide (e.g. ``voxcpm2_01_voice_design_savedtab_adv_open_light_viewport.png``).
"""

from __future__ import annotations

import contextlib
import os

import pytest

try:
    from playwright.sync_api import Page, sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    PLAYWRIGHT_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="Playwright not installed. Install with: pip install playwright && playwright install",
)

BASE_URL = os.environ.get("TTS_SERVER_URL", "http://127.0.0.1:7869")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(ROOT, "docs", "screenshots")
DARK_DIR = os.path.join(OUTPUT_DIR, "dark")

VIEWPORT = {"width": 1366, "height": 900}

# --- Tab catalog -------------------------------------------------------------
# 每个 tab 定义：侧边栏 data-tab 值、前缀编号、基础名、交互动作清单。
# 每个交互 (kind, target, label) → 会被执行，然后截图，文件名追加 label。
# kind 取值:
#   "stab"        → 点击 tab 内部的子 stab（stabs 结构里的按钮，按 text 匹配）
#   "collapse"    → 展开高级参数折叠面板（点 .collapse-header）；collapse-body 初始为折叠态
#   "details"     → 打开 <details id="prefix-advanced-params">（partials/advanced_params.html）
#   "sidebar_close" → 点击侧栏折叠按钮，关闭侧栏后截图
#   "help_drawer" → 打开帮助抽屉模态框
#   "health_panel"→ 打开系统监控模态框
#   "model_tab"   → 切换顶部引擎标签（voxcpm2 / indextts2）
#   "none"        → 仅执行默认初始态（baseline 状态不在这里重复）

TAB_DEFS: list[dict] = [
    {
        "num": "01",
        "tab": "voice_design",
        "name": "voice_design",
        "actions": [
            # 描述创建（stab value: create）+ 高级参数展开
            ("stab", "create", "create_tab"),
            ("collapse", "#advanced-params-collapse .collapse-header", "create_tab_adv_open"),
            # 已保存音色（stab value: saved）
            ("stab", "saved", "saved_tab"),
            ("collapse", "#advanced-params-collapse .collapse-header", "saved_tab_adv_open"),
        ],
    },
    {
        "num": "02",
        "tab": "voice_clone",
        "name": "voice_clone",
        "actions": [
            ("stab", "saved", "saved_tab"),
            ("collapse", "#vc-form .collapse-header", "saved_tab_adv_open"),
            ("stab", "upload", "upload_tab"),
            ("collapse", "#vc-form .collapse-header", "upload_tab_adv_open"),
        ],
    },
    {
        "num": "03",
        "tab": "ultimate_clone",
        "name": "ultimate_clone",
        "actions": [
            ("stab", "saved", "saved_tab"),
            ("stab", "upload", "upload_tab"),
        ],
    },
    {
        "num": "04",
        "tab": "script",
        "name": "script_workshop",
        "actions": [
            ("details", "#script-advanced-params", "adv_open"),
        ],
    },
    {
        "num": "05",
        "tab": "prompt_continue",
        "name": "prompt_continuation",
        "actions": [
            ("details", "#prompt-advanced-params", "adv_open"),
        ],
    },
    {
        "num": "06",
        "tab": "lora",
        "name": "lora",
        "actions": [
            # lora_manager.html 里没有明显的高级参数折叠，保持作为存在性检查
        ],
    },
    {
        "num": "07",
        "tab": "lora_training",
        "name": "lora_training",
        "actions": [
            # Training tab 常用高级参数面板（若存在）
            ("details", "#lora-train-advanced-params", "adv_open"),
        ],
    },
    {
        "num": "08",
        "tab": "settings",
        "name": "settings",
        "actions": [],
    },
    {
        "num": "09",
        "tab": "history",
        "name": "history",
        "actions": [],
    },
    {
        "num": "10",
        "tab": "persona",
        "name": "persona_library",
        "actions": [],
    },
    {
        "num": "11",
        "tab": "help",
        "name": "help",
        "actions": [],
    },
    # IndexTTS 2 家族（不在原脚本中）
    {
        "num": "12",
        "tab": "indextts2_clone",
        "name": "indextts2_clone",
        "actions": [
            ("details", "#ixc-advanced-params", "adv_open"),
        ],
    },
    {
        "num": "13",
        "tab": "indextts2_emotion",
        "name": "indextts2_emotion",
        "actions": [
            # 情感面板通常会有 8 个滑块，默认展示即可
        ],
    },
    {
        "num": "14",
        "tab": "indextts2_duration",
        "name": "indextts2_duration",
        "actions": [],
    },
]

# 全局模态框（不依赖特定 tab 切换，在首页直接触发）
GLOBAL_STATES: list[tuple[str, str]] = [
    ("sidebar_closed", "侧栏折叠"),
    ("help_drawer", "帮助抽屉模态框"),
    ("health_panel", "系统监控模态框"),
    ("model_voxcpm2", "顶部引擎 VoxCPM2 高亮"),
    ("model_indextts2", "顶部引擎 IndexTTS2 高亮"),
]


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module")
def server_url():
    import urllib.request

    try:
        urllib.request.urlopen(BASE_URL, timeout=3)
        return BASE_URL
    except Exception:  # pragma: no cover - CI guard
        pytest.skip(f"Server not running at {BASE_URL}")


@pytest.fixture(scope="function")
def browser():
    """Launch a fresh Chromium per test to avoid shared-state flakiness in headless."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


# --- helpers ----------------------------------------------------------------


def _make_dirs() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(DARK_DIR, exist_ok=True)


def _dismiss_onboarding(page: Page) -> None:
    """Remove onboarding overlay if present, and make sure it never re-runs.

    onboarding.js schedules the spotlight overlay via setTimeout(boot, 2000)
    on fresh contexts where localStorage.tts_onboarded_v1 is missing.  We guard
    against it both by setting the flag early and by force-removing any DOM
    nodes it has already inserted.  Otherwise Playwright clicks fail with
    "element intercepted by #onboarding-overlay".
    """
    page.evaluate(
        """() => {
            localStorage.setItem('tts_onboarded_v1', '1');
            const overlay = document.getElementById('onboarding-overlay');
            const spotlight = document.getElementById('onboarding-spotlight');
            const card = document.getElementById('onboarding-card');
            if (overlay) overlay.remove();
            if (spotlight) spotlight.remove();
            if (card) card.remove();
            // Also cancel any scheduled boot by overriding the replay/boot hooks
            // (harmless no-ops if onboarding.js hasn't bound them yet).
            window.TTS_replayOnboarding = () => {};
        }"""
    )
    # Additional 2.2s wait to catch the scheduled boot() timeout from
    # onboarding.js, then a second sweep to remove anything it just mounted.
    page.wait_for_timeout(2200)
    page.evaluate(
        """() => {
            localStorage.setItem('tts_onboarded_v1', '1');
            ['onboarding-overlay', 'onboarding-spotlight', 'onboarding-card']
                .forEach((id) => document.getElementById(id)?.remove());
        }"""
    )
    page.wait_for_timeout(120)


def _set_theme(page: Page, theme: str) -> None:
    page.evaluate(
        """(t) => {
            localStorage.setItem('tts_onboarded_v1', '1');
            localStorage.setItem('app_theme', t);
            document.documentElement.classList.remove('dark', 'light');
            document.documentElement.classList.add(t);
            document.documentElement.style.colorScheme = t;
        }""",
        theme,
    )
    page.wait_for_timeout(300)


def _go_home(page: Page, theme: str) -> None:
    # Set tts_onboarded_v1 BEFORE page navigation so onboarding.js reads it
    # on the fresh context.  We preload an about:blank, seed localStorage on
    # the origin, then navigate to the real URL.
    with contextlib.suppress(Exception):
        page.goto(f"{BASE_URL}/favicon.ico", wait_until="domcontentloaded", timeout=15000)
    page.evaluate("""() => { localStorage.setItem('tts_onboarded_v1', '1'); }""")
    page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
    # networkidle can stall indefinitely when SSE/event-stream keeps connections
    # open; treat it as best-effort and fall back to a fixed settle time.
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        page.wait_for_timeout(1500)
    page.wait_for_timeout(800)
    _set_theme(page, theme)
    _dismiss_onboarding(page)


def _click_tab(page: Page, tab_name: str) -> bool:
    button = page.locator(f".sidebar-item[data-tab='{tab_name}']")
    if button.count() == 0:
        return False
    # 若按钮位于折叠分组（.sidebar-nav-section.section-collapsed）内，
    # 先点击分组标题 .sidebar-nav-label 展开，否则 max-height:0 无法点击
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
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        page.wait_for_timeout(1500)
    page.wait_for_timeout(1200)
    return True


def _save(page: Page, prefix: str, theme: str) -> str:
    suffix = "" if theme == "light" else "_dark"
    filename = f"{prefix}{suffix}_viewport.png"
    directory = DARK_DIR if theme == "dark" else OUTPUT_DIR
    filepath = os.path.join(directory, filename)
    page.screenshot(path=filepath, full_page=True)
    assert os.path.exists(filepath), f"Screenshot not saved: {filepath}"
    return filepath


def _stab_by_label(page: Page, value: str) -> bool:
    """Click a sub-stab whose Alpine.js binding sets the tab value to ``value``.

    Instead of matching on the translated text (which changes per locale), we
    inspect each ``.stab`` button's click handler and look for an assignment
    like ``@click="xxxTab = 'saved'"`` or ``@click="xxxTab = 'upload'"``.  This
    way the action target is the raw state value (e.g. ``"saved"``), stable
    across languages.
    """
    stabs = page.locator(".stabs .stab")
    count = stabs.count()
    for i in range(count):
        stab = stabs.nth(i)
        try:
            matched = stab.evaluate(
                """(el, target) => {
                    const handler =
                        el.getAttribute('@click') ||
                        el.getAttribute('x-on:click') ||
                        el.getAttribute('onclick') ||
                        '';
                    // Match literal string assignment in the handler, e.g.
                    //   vcTab = 'saved'    tab='upload'
                    const re = new RegExp(`=\\\\s*['"]` + target.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&') + `['"]`);
                    if (re.test(handler)) { el.click(); return true; }
                    // Fallback: data-stab attribute if available
                    if (el.dataset.stab === target) { el.click(); return true; }
                    return false;
                }""",
                value,
            )
            if matched:
                page.wait_for_timeout(400)
                return True
        except Exception:
            continue

    # Last-ditch: exact text match against label (useful if the stab has no
    # x-on:click binding but a literal matching text).
    fallback = stabs.filter(has_text=value)
    if fallback.count() > 0:
        fallback.first.click()
        page.wait_for_timeout(400)
        return True
    return False


def _open_collapse(page: Page, selector: str) -> bool:
    """Click a .collapse-header that is within the current tab area.

    The convention in voice_design / voice_clone is: collapse-header toggles
    data-collapsed on its container and adds/removes `.open` on the
    header/body.  We click once; if already open it's a no-op visually.
    """
    header = page.locator(selector).first
    if header.count() == 0:
        return False
    header.click()
    # Wait for transition
    page.wait_for_timeout(500)
    return True


def _open_details(page: Page, selector: str) -> bool:
    """Open a <details> element by ID (used by partials/advanced_params.html)."""
    el = page.locator(selector).first
    if el.count() == 0:
        return False
    # If already open we still return True
    is_open = el.evaluate("(e) => e.hasAttribute('open')")
    if not is_open:
        el.locator("summary").first.click()
        page.wait_for_timeout(350)
    return True


def _collapse_sidebar(page: Page) -> None:
    toggle = page.locator("#sidebar-toggle-btn")
    if toggle.count() > 0:
        toggle.first.click()
        page.wait_for_timeout(400)


def _open_help_drawer(page: Page) -> None:
    btn = page.locator(".sidebar-help-btn")
    if btn.count() > 0:
        btn.first.click()
        page.wait_for_timeout(400)


def _open_health_panel(page: Page) -> None:
    panel = page.locator("#mini-monitor")
    if panel.count() > 0:
        panel.first.click()
        page.wait_for_timeout(500)


def _switch_model_tab(page: Page, model: str) -> None:
    loc = page.locator(f".model-tab[data-model='{model}']")
    if loc.count() > 0:
        loc.first.click()
        page.wait_for_timeout(350)


# --- test class -------------------------------------------------------------


class TestExtendedScreenshotCapture:
    """Capture extended interactive-state screenshots in light & dark themes."""

    @pytest.mark.parametrize("theme", ["light", "dark"])
    @pytest.mark.parametrize("state_label,_title", GLOBAL_STATES)
    def test_global_states(self, server_url, browser, theme, state_label, _title):
        _make_dirs()
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        _go_home(page, theme)

        # 触发全局交互
        if state_label == "sidebar_closed":
            _collapse_sidebar(page)
        elif state_label == "help_drawer":
            _open_help_drawer(page)
        elif state_label == "health_panel":
            _open_health_panel(page)
        elif state_label.startswith("model_"):
            model_key = state_label[len("model_") :]
            _switch_model_tab(page, model_key)

        _save(page, f"global_{state_label}", theme)
        context.close()

    @pytest.mark.parametrize("tab", TAB_DEFS)
    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_tab_interactions(self, server_url, browser, tab, theme):
        _make_dirs()
        context = browser.new_context(viewport=VIEWPORT)
        page = context.new_page()
        _go_home(page, theme)

        if not _click_tab(page, tab["tab"]):
            context.close()
            pytest.skip(f"Tab {tab['tab']} not found")

        if not tab["actions"]:
            # 没有显式交互动作就跳过（避免重复 baseline 的截图）
            context.close()
            return

        for kind, target, label in tab["actions"]:
            ok = False
            if kind == "stab":
                ok = _stab_by_label(page, target)
            elif kind == "collapse":
                ok = _open_collapse(page, target)
            elif kind == "details":
                ok = _open_details(page, target)
            # 未实现的 kind → ok=False → skip

            if not ok:
                continue  # 某些模板如果暂未实现该 UI，就跳过，不让整个测试失败

            prefix = f"voxcpm2_{tab['num']}_{tab['name']}_{label}"
            _save(page, prefix, theme)

        context.close()
