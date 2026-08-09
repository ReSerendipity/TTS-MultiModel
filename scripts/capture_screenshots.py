"""统一截图脚本 — Python Playwright 版（替代 Node.js 版）。

功能与 tests/capture-screenshots.js 完全一致，但使用 Python Playwright API，
消除 Node.js 依赖，统一技术栈。

用法::

    # 1. 启动服务器
    python bin/start_ui_test.py

    # 2. 运行截图脚本
    python scripts/capture_screenshots.py

    # 或指定 URL
    python scripts/capture_screenshots.py --url http://127.0.0.1:7869

输出目录: docs/screenshots/
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "docs" / "screenshots"
DARK_DIR = OUTPUT_DIR / "dark"

VIEWPORTS = {
    "desktop": {"width": 1366, "height": 900},
}

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


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def set_theme(page, theme: str) -> None:
    """设置页面主题（light/dark）。"""
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


def click_tab(page, tab_name: str) -> bool:
    """点击侧边栏标签页按钮。"""
    button = page.locator(f".sidebar-item[data-tab='{tab_name}']")
    if button.count() == 0:
        print(f"  [skip] Tab button not found: data-tab='{tab_name}'")
        return False
    button.first.click()
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_function(
        "() => { const el = document.querySelector('#tab-content'); "
        "return el && el.innerHTML.trim().length > 0; }",
        timeout=5000,
    )
    return True


def capture_tab(page, tab: dict, theme: str) -> bool:
    """捕获单个标签页截图。"""
    suffix = "_dark_viewport" if theme == "dark" else "_viewport"
    filename = f"voxcpm2_{tab['num']}_{tab['name']}{suffix}.png"
    out_dir = DARK_DIR if theme == "dark" else OUTPUT_DIR
    file_path = out_dir / filename

    page.goto("http://127.0.0.1:7869/", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(800)
    set_theme(page, theme)

    if not click_tab(page, tab["tab"]):
        return False

    page.screenshot(path=str(file_path), full_page=True)
    rel_path = file_path.relative_to(REPO_ROOT)
    print(f"  Captured: {rel_path}")
    return True


def capture_home_page(page, theme: str) -> None:
    """捕获首页截图。"""
    suffix = "_dark_viewport" if theme == "dark" else "_viewport"
    filename = f"home{suffix}.png"
    out_dir = DARK_DIR if theme == "dark" else OUTPUT_DIR
    file_path = out_dir / filename

    page.goto("http://127.0.0.1:7869/", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_load_state("networkidle", timeout=15000)
    page.wait_for_timeout(2000)
    set_theme(page, theme)
    page.screenshot(path=str(file_path), full_page=True)
    rel_path = file_path.relative_to(REPO_ROOT)
    print(f"  Captured: {rel_path}")


def main():
    parser = argparse.ArgumentParser(description="TTS MultiModel Screenshot Capture (Python)")
    parser.add_argument("--url", default="http://127.0.0.1:7869", help="Server URL")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: Playwright not installed. Install with: pip install playwright && playwright install")
        sys.exit(1)

    print("TTS MultiModel - Screenshot Capture (Python)")
    print("=" * 44)
    print(f"Base URL: {args.url}")
    print(f"Output:   {OUTPUT_DIR}")
    print(f"Dark sub: {DARK_DIR}")
    print()

    ensure_dir(OUTPUT_DIR)
    ensure_dir(DARK_DIR)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        print("Checking if server is running...")
        try:
            resp = page.goto(args.url, timeout=10000, wait_until="domcontentloaded")
            if not resp or not resp.ok:
                raise ConnectionError(f"server responded {resp.status if resp else 'no response'}")
            print("Server is running!")
        except Exception as e:
            print(f"ERROR: Server is not running at {args.url}")
            print(f"  {e}")
            sys.exit(1)

        for vp_name, vp_size in VIEWPORTS.items():
            print(f"\n=== Viewport: {vp_name} ({vp_size['width']}x{vp_size['height']}) ===")
            page.set_viewport_size(vp_size)

            for theme in ["light", "dark"]:
                print(f"\n--- Theme: {theme} ---")
                try:
                    capture_home_page(page, theme)
                except Exception as e:
                    print(f"  home failed: {e}")

                for tab in TABS:
                    try:
                        capture_tab(page, tab, theme)
                    except Exception as e:
                        print(f"  [err] {tab['tab']}: {e}")

        print("\n" + "=" * 44)
        print("Screenshot capture complete!")
        browser.close()


if __name__ == "__main__":
    main()
