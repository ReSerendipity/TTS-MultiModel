# -*- coding: utf-8 -*-
"""Playwright test script for TTS MultiModel page switching verification.

Identified bugs in tts_multimodel_replica.html:
  BUG-1: switchPage() references getElementById('page-title') but actual element id is 'top-page-title'
         -> Causes TypeError: Cannot set properties of null (setting 'textContent')
         -> This prevents ALL page switching from working
  BUG-2: DOMContentLoaded references getElementById('theme-toggle-top') which doesn't exist
         -> The theme toggle button id is 'theme-toggle-btn'
         -> Causes: Cannot read properties of null (reading 'addEventListener')
  BUG-3: pages[] array has 'prompt_continuation' but sidebar button uses data-tab='prompt_continue'
         -> page div id is 'page-prompt_continue', so switchPage would fail for this tab
  BUG-4: pages[] array has 'personas' but sidebar button uses data-tab='persona'
         -> page div id is 'page-personas', so switchPage would fail for this tab
  BUG-5: toggleSidebarCollapse() is called in HTML onclick but only toggleSidebar() is defined
"""

import os
import sys

import pytest

pytest.importorskip("playwright")  # Playwright 为可选（E2E）依赖，未安装时跳过本模块
from playwright.sync_api import sync_playwright

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOT_DIR = os.path.join(_REPO_ROOT, "screenshots")
URL = os.environ.get("TTS_REPLICA_URL", "http://127.0.0.1:8765/tts_multimodel_replica.html")

# Tabs with (sidebar data-tab value, page div id, display name)
TABS = [
    ("voice_design", "page-voice_design", "声音设计"),
    ("voice_clone", "page-voice_clone", "语音克隆"),
    ("ultimate_clone", "page-ultimate_clone", "终极克隆"),
    ("script", "page-script", "剧本工坊"),
    ("prompt_continue", "page-prompt_continue", "Prompt延续"),
    ("lora", "page-lora", "LoRA管理"),
    ("lora_training", "page-lora_training", "LoRA训练"),
    ("indextts2_clone", "page-indextts2_clone", "IndexTTS2克隆"),
    ("indextts2_emotion", "page-indextts2_emotion", "情感控制"),
    ("indextts2_duration", "page-indextts2_duration", "时长控制"),
    ("settings", "page-settings", "设置"),
    ("history", "page-history", "历史记录"),
    ("persona", "page-personas", "音色管理"),
    ("help", "page-help", "帮助"),
]

# Workaround: call the core switchPage logic directly (bypassing the broken getElementById line)
SWITCH_JS = """
(args) => {
    const [tabId, pageDivId, tabName] = args;
    // Remove all active tab-pages
    document.querySelectorAll('.tab-page').forEach(p => p.classList.remove('active'));
    // Activate target
    const target = document.getElementById(pageDivId);
    if (target) target.classList.add('active');
    // Update sidebar highlight
    document.querySelectorAll('.sidebar-item').forEach(item => {
        item.classList.remove('active');
        if (item.dataset.tab === tabId) item.classList.add('active');
    });
    // Update title
    const titleEl = document.getElementById('top-page-title');
    if (titleEl) titleEl.textContent = tabName;
}
"""


def main():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    results = []
    bugs_found = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        # Capture console errors
        console_errors = []
        page.on(
            "console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None
        )
        page.on("pageerror", lambda exc: console_errors.append(f"[PAGEERROR] {exc}"))

        # 1. Open page
        print(">>> 打开页面...")
        page.goto(URL, wait_until="networkidle")
        page.wait_for_timeout(1000)

        # Report console errors (bugs)
        print(f"    控制台错误数: {len(console_errors)}")
        for err in console_errors:
            print(f"      {err}")
            if "addEventListener" in err and "null" in err:
                bugs_found.append("BUG-2: DOMContentLoaded 中引用了不存在的 #theme-toggle-top 元素")
            if "textContent" in err and "null" in err:
                bugs_found.append("BUG-1: switchPage 中引用了不存在的 #page-title (应为 #top-page-title)")

        # 2. Verify BUG-1: switchPage is broken
        print("\n>>> 验证 BUG-1: switchPage 函数是否正常...")
        try:
            page.evaluate("switchTab('voice_clone')")
            bug1_fixed = True
        except Exception as e:
            bug1_fixed = False
            err_msg = str(e)
            print(f"    ✗ switchPage 抛出异常: {err_msg[:120]}")
            if "page-title" in err_msg or "textContent" in err_msg:
                bugs_found.append("BUG-1 已确认: switchPage 中 getElementById('page-title') 返回 null")
                results.append(("BUG-1 验证", None, "CONFIRMED: switchPage 引用不存在的 #page-title"))
        if bug1_fixed:
            results.append(("BUG-1 验证", None, "FIXED: switchPage 工作正常"))

        # 3. Screenshot default page (voice_design)
        print("\n>>> 截取首页（声音设计）...")
        path = os.path.join(SCREENSHOT_DIR, "01_voice_design_default.png")
        page.screenshot(path=path, full_page=False)
        results.append(("首页（声音设计）默认状态", path, "OK - 默认显示正常"))
        print(f"    保存: {path}")

        # 4. Test sidebar button click (should fail due to BUG-1)
        print("\n>>> 测试侧边栏按钮点击（受 BUG-1 影响）...")
        btn = page.locator('button.sidebar-item[data-tab="voice_clone"]')
        btn.click()
        page.wait_for_timeout(300)
        # The page should NOT have switched (BUG-1)
        active_after_click = page.evaluate(
            "() => Array.from(document.querySelectorAll('.tab-page.active')).map(e => e.id)"
        )
        print(f"    点击后 active 页面: {active_after_click}")
        if "page-voice_clone" not in active_after_click:
            results.append(("侧边栏按钮点击-语音克隆", None, "FAIL: 页面未切换 (BUG-1 导致)"))
            bugs_found.append("BUG-1 影响: 所有侧边栏按钮点击均无法切换页面")
        else:
            results.append(("侧边栏按钮点击-语音克隆", None, "OK: 页面切换成功"))

        # 5. Test each tab using WORKAROUND (direct JS to bypass broken line)
        print("\n>>> 使用 JS 直接切换方式测试各页面（绕过 BUG-1）...")
        for i, (tab_id, page_div_id, tab_name) in enumerate(TABS):
            print(f"    切换到: {tab_name} ({tab_id})...")
            try:
                page.evaluate(SWITCH_JS, [tab_id, page_div_id, tab_name])
                page.wait_for_timeout(300)

                # Verify visibility
                is_visible = page.evaluate(
                    f"() => document.getElementById('{page_div_id}').classList.contains('active')"
                )
                has_active = page.evaluate(
                    f"() => document.querySelector('button.sidebar-item[data-tab=\"{tab_id}\"]').classList.contains('active')"
                )

                filename = f"{i + 1:02d}_{tab_id}.png"
                path = os.path.join(SCREENSHOT_DIR, filename)
                page.screenshot(path=path, full_page=False)

                status = "OK" if (is_visible and has_active) else f"WARN: visible={is_visible}, active={has_active}"
                results.append((f"页面切换-{tab_name}", path, status))
                print(f"      ✓ visible={is_visible}, sidebar_active={has_active}")

            except Exception as e:
                results.append((f"页面切换-{tab_name}", None, f"ERROR: {e}"))
                print(f"      ✗ 异常: {e}")

        # 6. Test dark mode (BUG-2: theme-toggle-top doesn't exist, use theme-toggle-btn)
        print("\n>>> 测试深色主题切换...")
        try:
            # First check if toggleTheme function works
            page.evaluate("""
                () => {
                    const isDark = !document.body.classList.contains('dark');
                    document.body.classList.toggle('dark', isDark);
                    // Also toggle html for CSS variable inheritance
                    document.documentElement.classList.toggle('dark', isDark);
                }
            """)
            page.wait_for_timeout(500)

            has_dark = page.evaluate("() => document.body.classList.contains('dark')")
            path = os.path.join(SCREENSHOT_DIR, "15_dark_mode.png")
            page.screenshot(path=path, full_page=False)
            results.append(("深色主题", path, f"OK (dark={has_dark})"))
            print(f"    dark class = {has_dark}")

            # Try the actual toggleTheme button
            print("    测试主题切换按钮...")
            try:
                toggle_btn = page.locator("#theme-toggle-btn")
                toggle_btn.click()
                page.wait_for_timeout(300)
                is_light_now = not page.evaluate("() => document.body.classList.contains('dark')")
                results.append(("主题切换按钮", None, f"OK: 切换成功 (light={is_light_now})"))
            except Exception as e:
                results.append(("主题切换按钮", None, f"FAIL: {str(e)[:100]}"))
                bugs_found.append("BUG-2 影响: 主题切换按钮初始化可能失败")

            # Toggle back
            page.evaluate("""
                () => {
                    document.body.classList.remove('dark');
                    document.documentElement.classList.remove('dark');
                }
            """)

        except Exception as e:
            results.append(("深色主题", None, f"ERROR: {e}"))
            print(f"    ✗ 异常: {e}")

        # 7. Test sidebar collapse (BUG-5: toggleSidebarCollapse not defined)
        print("\n>>> 测试侧边栏折叠...")
        try:
            sidebar = page.locator("#sidebar")
            width_before = sidebar.evaluate("el => el.offsetWidth")
            print(f"    折叠前宽度: {width_before}px")

            # Check if toggleSidebarCollapse exists
            has_collapse_fn = page.evaluate("typeof toggleSidebarCollapse")
            print(f"    toggleSidebarCollapse 类型: {has_collapse_fn}")
            if has_collapse_fn == "undefined":
                bugs_found.append("BUG-5: toggleSidebarCollapse() 未定义，HTML onclick 引用了不存在的函数")

            # Use toggleSidebar as fallback
            has_toggle_fn = page.evaluate("typeof toggleSidebar")
            print(f"    toggleSidebar 类型: {has_toggle_fn}")

            if has_toggle_fn == "function":
                page.evaluate("toggleSidebar()")
                page.wait_for_timeout(600)
                width_after = sidebar.evaluate("el => el.offsetWidth")
                is_collapsed = sidebar.evaluate("el => el.classList.contains('collapsed')")
                print(f"    折叠后宽度: {width_after}px, collapsed={is_collapsed}")

                path = os.path.join(SCREENSHOT_DIR, "16_sidebar_collapsed.png")
                page.screenshot(path=path, full_page=False)

                if width_after < width_before:
                    results.append(("侧边栏折叠", path, f"OK (宽{width_before}->{width_after})"))
                else:
                    results.append(("侧边栏折叠", path, f"WARN: 宽度未变化 ({width_before}->{width_after})"))

                # Expand back
                page.evaluate("toggleSidebar()")
                page.wait_for_timeout(600)
            else:
                results.append(("侧边栏折叠", None, "FAIL: toggleSidebar 函数不存在"))

        except Exception as e:
            results.append(("侧边栏折叠", None, f"ERROR: {e}"))
            print(f"    ✗ 异常: {e}")

        # 8. Test BUG-3 and BUG-4 ID mismatches
        print("\n>>> 验证 ID 不匹配问题...")
        # BUG-3: prompt_continuation vs prompt_continue
        pages_has_prompt = page.evaluate("() => pages.some(p => p.id === 'prompt_continuation')")
        sidebar_has_prompt = page.evaluate("() => !!document.querySelector('[data-tab=\"prompt_continue\"]')")
        page_has_prompt = page.evaluate("() => !!document.getElementById('page-prompt_continue')")
        print(f"    pages[] 有 prompt_continuation: {pages_has_prompt}")
        print(f"    侧边栏有 prompt_continue: {sidebar_has_prompt}")
        print(f"    页面有 page-prompt_continue: {page_has_prompt}")
        if pages_has_prompt and sidebar_has_prompt:
            bugs_found.append("BUG-3: pages[] 用 'prompt_continuation' 但侧边栏/页面用 'prompt_continue'")

        # BUG-4: personas vs persona
        pages_has_personas = page.evaluate("() => pages.some(p => p.id === 'personas')")
        sidebar_has_persona = page.evaluate("() => !!document.querySelector('[data-tab=\"persona\"]')")
        page_has_personas = page.evaluate("() => !!document.getElementById('page-personas')")
        print(f"    pages[] 有 personas: {pages_has_personas}")
        print(f"    侧边栏有 persona: {sidebar_has_persona}")
        print(f"    页面有 page-personas: {page_has_personas}")
        if pages_has_personas and sidebar_has_persona:
            bugs_found.append("BUG-4: pages[] 用 'personas' 但侧边栏按钮用 'persona'")

        browser.close()

    # Deduplicate bugs
    unique_bugs = list(dict.fromkeys(bugs_found))

    # Summary
    print("\n" + "=" * 70)
    print("测试结果汇总")
    print("=" * 70)

    for name, path, status in results:
        if status.startswith("OK"):
            icon = "✓"
        elif "WARN" in status:
            icon = "⚠"
        elif "CONFIRMED" in status:
            icon = "🔍"
        else:
            icon = "✗"
        print(f"  {icon} {name}: {status}")
        if path:
            print(f"    截图: {path}")

    print("\n" + "=" * 70)
    print("发现的 Bug 列表")
    print("=" * 70)
    for bug in unique_bugs:
        print(f"  🐛 {bug}")

    fail_count = sum(1 for _, _, s in results if "FAIL" in s or "ERROR" in s)
    print(f"\n总计: {len(results)} 项, 失败/错误: {fail_count}, Bug: {len(unique_bugs)}")
    return fail_count


if __name__ == "__main__":
    sys.exit(main())
