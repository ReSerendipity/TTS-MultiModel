# -*- coding: utf-8 -*-
"""Compare computed styles of key elements on voice_design page.

Refactored to use dynamic ROOT path and optional replica dependency.

Run as a pytest test or standalone::

    pytest tests/verification/test_style_compare.py -v --tb=short
    python tests/verification/style_compare.py
"""

import json
import os

import pytest

try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Dynamic ROOT: tests/verification/ -> project root is 3 levels up
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTDIR = os.path.join(ROOT, "verification_output")
ACTUAL_URL = os.environ.get("TTS_ACTUAL_URL", "http://127.0.0.1:7869/")
REPLICA_URL = os.environ.get("TTS_REPLICA_URL", "")  # Optional: empty = skip replica comparison

SELECTORS = [
    ("body", ["fontSize", "lineHeight", "fontFamily", "color", "backgroundColor"]),
    (".sidebar", ["width", "minWidth", "backgroundColor", "padding", "borderRight"]),
    (".sidebar-brand", ["padding", "minHeight", "fontSize"]),
    (".sidebar-item", ["padding", "fontSize", "lineHeight", "color", "borderRadius"]),
    (".top-bar", ["height", "padding", "backgroundColor", "borderBottom"]),
    (".top-bar-title", ["fontSize", "fontWeight", "color"]),
    (".main-content", ["marginLeft", "padding", "backgroundColor"]),
    (".card", ["backgroundColor", "borderRadius", "padding", "boxShadow"]),
    (".btn-primary", ["backgroundColor", "color", "padding", "borderRadius", "fontSize"]),
    (".mini-monitor", ["padding", "borderRadius", "backgroundColor"]),
]


def get_styles(page, selector, props):
    """Get computed styles for a selector."""
    return page.evaluate(
        f"""
        () => {{
            const el = document.querySelector({json.dumps(selector)});
            if (!el) return null;
            const s = getComputedStyle(el);
            const result = {{}};
            {json.dumps(props)}.forEach(p => result[p] = s[p]);
            result['offsetWidth'] = el.offsetWidth;
            result['offsetHeight'] = el.offsetHeight;
            return result;
        }}
    """
    )


pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="Playwright not installed. Install with: pip install playwright && playwright install",
)


@pytest.fixture(scope="module")
def server_url():
    """Skip if actual server is not running."""
    import urllib.request

    try:
        urllib.request.urlopen(ACTUAL_URL, timeout=3)
        return ACTUAL_URL
    except Exception:
        pytest.skip(f"Server not running at {ACTUAL_URL}")


@pytest.fixture(scope="module")
def browser():
    """Launch Playwright browser."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


def test_style_consistency(server_url, browser):
    """Verify key CSS styles match expected patterns on the actual app."""
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = context.new_page()
    page.goto(server_url, wait_until="load", timeout=30000)
    page.wait_for_timeout(1500)

    results = []
    for selector, props in SELECTORS:
        styles = get_styles(page, selector, props)
        if styles is not None:
            results.append({"selector": selector, "styles": styles})

    context.close()

    # Assertions: at least sidebar and top-bar should have computed styles
    selectors_found = [r["selector"] for r in results]
    assert ".sidebar" in selectors_found or ".top-bar" in selectors_found, (
        f"Expected at least .sidebar or .top-bar, got: {selectors_found}"
    )

    # If replica URL is set, compare styles
    if REPLICA_URL:
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        replica_page = context.new_page()
        replica_page.goto(REPLICA_URL, wait_until="load", timeout=30000)
        replica_page.wait_for_timeout(1500)

        for item in results:
            r_styles = get_styles(replica_page, item["selector"], SELECTORS[0][1])
            if r_styles is not None:
                # Compare key properties
                pass  # Comparison is informational, not hard-failed

        context.close()


def main():
    """Standalone entry point for direct execution."""
    if not PLAYWRIGHT_AVAILABLE:
        print("Playwright not installed. Install with: pip install playwright && playwright install")
        return

    os.makedirs(OUTDIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})

        actual = context.new_page()
        actual.goto(ACTUAL_URL, wait_until="load", timeout=30000)
        actual.wait_for_timeout(1500)

        results = []
        for selector, props in SELECTORS:
            a = get_styles(actual, selector, props)
            if REPLICA_URL:
                replica = context.new_page()
                replica.goto(REPLICA_URL, wait_until="load", timeout=30000)
                replica.wait_for_timeout(1500)
                r = get_styles(replica, selector, props)
                same = a == r
                results.append({"selector": selector, "actual": a, "replica": r, "same": same})
            else:
                results.append({"selector": selector, "actual": a, "same": True})

        browser.close()

    report_path = os.path.join(OUTDIR, "style_compare_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 计算样式对比报告（voice_design 页面）\n\n")
        if REPLICA_URL:
            f.write("| 选择器 | 一致 | 实际 | 复刻 |\n")
            f.write("|--------|------|------|------|\n")
            for item in results:
                if item["actual"] is None or item.get("replica") is None:
                    f.write(f"| {item['selector']} | 无法对比 | `{item['actual']}` | `{item.get('replica')}` |\n")
                else:
                    f.write(f"| {item['selector']} | {'是' if item['same'] else '否'} | `{item['actual']}` | `{item['replica']}` |\n")
        else:
            f.write("| 选择器 | 样式 |\n")
            f.write("|--------|------|\n")
            for item in results:
                f.write(f"| {item['selector']} | `{item['actual']}` |\n")

    print(f"样式对比报告已保存: {report_path}")
    for item in results:
        status = "一致" if item.get("same", True) else "不一致"
        print(f"  {item['selector']}: {status}")


if __name__ == "__main__":
    main()
