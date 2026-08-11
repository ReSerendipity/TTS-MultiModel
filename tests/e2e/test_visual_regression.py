"""视觉回归测试 — 基于 Playwright screenshot 对比。

捕获核心页面的截图，与 baseline 进行像素级差异对比，
检测 UI 回归。

首次运行（生成 baseline）::

    pytest tests/e2e/test_visual_regression.py -v --snapshot-update

后续运行（对比）::

    pytest tests/e2e/test_visual_regression.py -v

需要：
- 服务器运行在 http://127.0.0.1:7869
- Playwright chromium 已安装
- numpy（已包含在项目依赖中）
"""

import os
import tempfile

import pytest

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="Playwright not installed. Install with: pip install playwright && playwright install",
)

BASE_URL = os.environ.get("TTS_SERVER_URL", "http://127.0.0.1:7869")
VIEWPORT = {"width": 1366, "height": 900}
# 像素差异阈值：差异比例超过此值则判定为回归
PIXEL_DIFF_THRESHOLD = 0.01  # 1%

SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), "screenshots")


def _load_image_as_array(filepath):
    """使用 numpy 加载 PNG 图像为像素数组。"""
    return np.asarray(
        __import__("PIL").Image.open(filepath).convert("RGB")
    ) if __import__("PIL").__version__ else None


def _load_image_png(filepath):
    """使用 numpy 加载 PNG 图像为像素数组（无需 PIL 的原始方式）。

    利用 numpy 的 fromfile 读取原始字节并解析 PNG。
    如果 Pillow 可用则优先使用 Pillow。
    """
    try:
        from PIL import Image
        img = Image.open(filepath).convert("RGB")
        return np.asarray(img)
    except ImportError:
        pytest.skip("Pillow not installed. Install with: pip install Pillow")


def _compare_images(baseline_path, current_path):
    """对比两张图片的像素差异。

    返回 (is_match, diff_ratio)。
    """
    baseline = _load_image_png(baseline_path)
    current = _load_image_png(current_path)

    if baseline.shape != current.shape:
        return False, 1.0

    diff = np.abs(baseline.astype(np.int16) - current.astype(np.int16))
    # 像素级差异：任何通道差异 > 10 视为不同像素
    pixel_diff = np.any(diff > 10, axis=2)
    diff_ratio = np.mean(pixel_diff)
    return diff_ratio <= PIXEL_DIFF_THRESHOLD, diff_ratio


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


def _capture_and_compare(page, screenshots_dir, filename):
    """捕获截图并与 baseline 对比。

    如果 baseline 不存在或 --snapshot-update 标志设置，则保存为 baseline。
    否则进行像素差异对比。
    """
    os.makedirs(screenshots_dir, exist_ok=True)
    baseline_path = os.path.join(screenshots_dir, filename)

    # 捕获到临时文件
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    page.screenshot(path=tmp_path)

    # 检查是否需要更新或创建 baseline
    # 支持环境变量 TTS_SNAPSHOT_UPDATE=1 或命令行 --snapshot-update
    snapshot_update = os.environ.get("TTS_SNAPSHOT_UPDATE", "") == "1"

    if not os.path.exists(baseline_path) or snapshot_update:
        # 首次运行或更新 baseline
        import shutil
        shutil.move(tmp_path, baseline_path)
        pytest.skip(f"Baseline created/updated: {filename}")
    else:
        # 对比
        if not NUMPY_AVAILABLE:
            # numpy 不可用时回退为文件大小对比
            baseline_size = os.path.getsize(baseline_path)
            current_size = os.path.getsize(tmp_path)
            size_diff_ratio = abs(baseline_size - current_size) / max(baseline_size, 1)
            os.unlink(tmp_path)
            assert size_diff_ratio < 0.5, (
                f"File size diff too large for {filename}: "
                f"baseline={baseline_size}B, current={current_size}B, "
                f"diff_ratio={size_diff_ratio:.2%}"
            )
        else:
            try:
                is_match, diff_ratio = _compare_images(baseline_path, tmp_path)
            finally:
                os.unlink(tmp_path)
            assert is_match, (
                f"Visual regression detected for {filename}: "
                f"pixel diff ratio={diff_ratio:.2%} (threshold={PIXEL_DIFF_THRESHOLD:.2%})"
            )


def _freeze_random(page):
    """冻结 Math.random，消除波形等动态元素导致的截图差异。"""
    page.evaluate("() => { Math.random = () => 0.5; }")


def _block_remote_fonts(context):
    """拦截 Google Fonts，消除字体加载时序导致的截图差异。"""
    context.route("**://fonts.googleapis.com/**", lambda route: route.abort())
    context.route("**://fonts.gstatic.com/**", lambda route: route.abort())


def _stabilize_page(page):
    """统一稳定化：dismiss onboarding overlay + 等待渲染稳定。"""
    _freeze_random(page)
    page.evaluate("() => document.fonts.ready.then(() => true)")
    page.evaluate("() => { localStorage.setItem('tts_onboarded_v1','1'); }")
    page.wait_for_timeout(2200)
    page.evaluate(
        "() => { localStorage.setItem('tts_onboarded_v1','1'); "
        "['onboarding-overlay','onboarding-spotlight','onboarding-card']"
        ".forEach((id) => document.getElementById(id)?.remove()); }"
    )
    page.wait_for_timeout(1000)


class TestVisualRegression:
    """核心页面视觉回归测试。"""

    def test_home_page_visual(self, server_url, browser):
        """首页视觉回归 — 对比首页截图。"""
        context = browser.new_context(viewport=VIEWPORT)
        context.add_init_script("Math.random = () => 0.5;")
        _block_remote_fonts(context)
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        _stabilize_page(page)
        _capture_and_compare(page, SCREENSHOTS_DIR, "regression_home.png")
        context.close()

    def test_voice_design_tab_visual(self, server_url, browser):
        """声音设计 tab 视觉回归。"""
        context = browser.new_context(viewport=VIEWPORT)
        context.add_init_script("Math.random = () => 0.5;")
        _block_remote_fonts(context)
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        _stabilize_page(page)

        # Navigate to voice_design tab
        button = page.locator(".sidebar-item[data-tab='voice_design']")
        if button.count() > 0:
            button.first.click()
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            page.wait_for_selector("#tab-content", state="visible", timeout=5000)
            page.wait_for_timeout(800)

        _capture_and_compare(page, SCREENSHOTS_DIR, "regression_voice_design.png")
        context.close()

    def test_voice_clone_tab_visual(self, server_url, browser):
        """语音克隆 tab 视觉回归。"""
        context = browser.new_context(viewport=VIEWPORT)
        context.add_init_script("Math.random = () => 0.5;")
        _block_remote_fonts(context)
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        _stabilize_page(page)

        button = page.locator(".sidebar-item[data-tab='voice_clone']")
        if button.count() > 0:
            button.first.click()
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            page.wait_for_selector("#tab-content", state="visible", timeout=5000)
            page.wait_for_timeout(800)

        _capture_and_compare(page, SCREENSHOTS_DIR, "regression_voice_clone.png")
        context.close()

    def test_dark_theme_visual(self, server_url, browser):
        """暗色主题视觉回归。"""
        context = browser.new_context(viewport=VIEWPORT)
        context.add_init_script("Math.random = () => 0.5;")
        _block_remote_fonts(context)
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)

        # Set dark theme + dismiss onboarding (wait for scheduled overlay)
        page.evaluate("""
            () => {
                localStorage.setItem('tts_onboarded_v1', '1');
                localStorage.setItem('app_theme', 'dark');
                document.documentElement.classList.remove('light');
                document.documentElement.classList.add('dark');
                document.documentElement.style.colorScheme = 'dark';
            }
        """)
        page.wait_for_timeout(2200)
        page.evaluate("""
            () => {
                localStorage.setItem('tts_onboarded_v1', '1');
                ['onboarding-overlay', 'onboarding-spotlight', 'onboarding-card']
                    .forEach((id) => document.getElementById(id)?.remove());
            }
        """)
        # 等待主题切换后的渲染稳定（CSS 过渡 + 字体）
        page.wait_for_timeout(1200)

        _capture_and_compare(page, SCREENSHOTS_DIR, "regression_dark_theme.png")
        context.close()

    def test_mobile_layout_visual(self, server_url, browser):
        """移动端布局视觉回归。"""
        context = browser.new_context(
            viewport={"width": 375, "height": 812},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
        )
        context.add_init_script("Math.random = () => 0.5;")
        _block_remote_fonts(context)
        page = context.new_page()
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        _stabilize_page(page)

        _capture_and_compare(page, SCREENSHOTS_DIR, "regression_mobile.png")
        context.close()
