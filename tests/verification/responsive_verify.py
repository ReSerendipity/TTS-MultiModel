# -*- coding: utf-8 -*-
"""Capture responsive screenshots and metrics for comparison."""
import os
import sys
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTDIR = os.path.join(ROOT, "verification_output")
ACTUAL_URL = os.environ.get("TTS_ACTUAL_URL", "http://127.0.0.1:7869/")
REPLICA_URL = os.environ.get("TTS_REPLICA_URL", "")  # Optional: empty = skip replica comparison
WIDTHS = [1920, 1600, 1440, 1280, 1200, 1100, 1024, 900, 768, 480]


def test_target(browser, name, url):
    out = os.path.join(OUTDIR, name)
    os.makedirs(out, exist_ok=True)
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = context.new_page()
    results = []
    print(f"\n>>> 响应式测试 {'实际应用' if name == 'actual' else 'HTML复刻件'}: {url}")
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(url, wait_until="load", timeout=30000)
        page.wait_for_timeout(1000)
        path = os.path.join(out, f"responsive_{width}.png")
        page.screenshot(path=path, full_page=False)
        info = page.evaluate("""
            () => {
                const sidebar = document.getElementById('sidebar') || document.querySelector('.sidebar');
                const main = document.querySelector('.main-content');
                const topbar = document.querySelector('.top-bar');
                const toggleBtn = document.getElementById('sidebar-toggle-btn');
                const edgeToggle = document.getElementById('sidebar-edge-toggle');
                const mobileToggle = document.querySelector('.top-bar-mobile-toggle');
                const overlay = document.getElementById('sidebar-overlay');
                return {
                    width: window.innerWidth,
                    height: window.innerHeight,
                    sidebar_width: sidebar ? sidebar.offsetWidth : null,
                    sidebar_left: sidebar ? getComputedStyle(sidebar).left : null,
                    main_margin_left: main ? getComputedStyle(main).marginLeft : null,
                    topbar_height: topbar ? topbar.offsetHeight : null,
                    toggle_display: toggleBtn ? getComputedStyle(toggleBtn).display : null,
                    edge_toggle_display: edgeToggle ? getComputedStyle(edgeToggle).display : null,
                    mobile_toggle_display: mobileToggle ? getComputedStyle(mobileToggle).display : null,
                    overlay_display: overlay ? getComputedStyle(overlay).display : null,
                };
            }
        """)
        info["screenshot"] = path
        results.append(info)
        print(f"  {width}px: sidebar={info['sidebar_width']}, toggle={info['toggle_display']}, mobile={info['mobile_toggle_display']}")
    context.close()
    return results


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        actual = test_target(browser, "actual", ACTUAL_URL)
        replica = test_target(browser, "replica", REPLICA_URL)
        browser.close()

    report_path = os.path.join(OUTDIR, "responsive_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 响应式布局对比报告\n\n")
        f.write("| 宽度 | 实际 sidebar | 复刻 sidebar | 实际 toggle | 复刻 toggle | 实际 mobile | 复刻 mobile | 实际 overlay | 复刻 overlay |\n")
        f.write("|------|--------------|--------------|-------------|-------------|-------------|-------------|--------------|--------------|\n")
        for a, r in zip(actual, replica):
            f.write(f"| {a['width']} | {a['sidebar_width']} ({a['sidebar_left']}) | {r['sidebar_width']} ({r['sidebar_left']}) | {a['toggle_display']} | {r['toggle_display']} | {a['mobile_toggle_display']} | {r['mobile_toggle_display']} | {a['overlay_display']} | {r['overlay_display']} |\n")

        f.write("\n## 截图路径\n\n")
        for a, r in zip(actual, replica):
            f.write(f"- {a['width']}px: actual=`{a['screenshot']}`, replica=`{r['screenshot']}`\n")

    print(f"\n报告已保存: {report_path}")


if __name__ == "__main__":
    sys.exit(main())