# -*- coding: utf-8 -*-
"""Comprehensive consistency verification between TTS MultiModel replica and actual app."""
import os
import sys
import time
import json
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTDIR = os.path.join(ROOT, "verification_output")
ACTUAL_URL = os.environ.get("TTS_ACTUAL_URL", "http://127.0.0.1:7869/")
REPLICA_URL = os.environ.get("TTS_REPLICA_URL", "")  # Optional: empty = skip replica comparison

TABS = [
    ("voice_design", "声音设计"),
    ("voice_clone", "语音克隆"),
    ("ultimate_clone", "终极克隆"),
    ("script", "剧本工坊"),
    ("prompt_continue", "Prompt 延续"),
    ("lora", "LoRA 管理"),
    ("lora_training", "LoRA 训练"),
    ("indextts2_clone", "IndexTTS2 克隆"),
    ("indextts2_emotion", "情感控制"),
    ("indextts2_duration", "时长控制"),
    ("settings", "设置"),
    ("history", "历史记录"),
    ("persona", "音色库"),
    ("help", "帮助"),
]

RESPONSIVE_WIDTHS = [1920, 1600, 1440, 1280, 1200, 1100, 1024, 900, 768, 480]


def safe_filename(name):
    return name.replace(" ", "_").replace("/", "_")


def collect_errors(page):
    errors = []
    def on_console(msg):
        if msg.type == "error":
            errors.append(f"[console] {msg.text}")
    def on_page(err):
        errors.append(f"[pageerror] {err}")
    page.on("console", on_console)
    page.on("pageerror", on_page)
    return errors


def inspect_sidebar_state(page, label):
    data = page.evaluate("""
        () => {
            const sidebar = document.getElementById('sidebar') || document.querySelector('.sidebar');
            const toggleBtn = document.getElementById('sidebar-toggle-btn');
            const edgeToggle = document.getElementById('sidebar-edge-toggle');
            const toggleIcon = document.getElementById('sidebar-toggle-icon');
            const overlay = document.getElementById('sidebar-overlay');
            const sidebarItems = document.querySelectorAll('.sidebar-item');
            const style = sidebar ? window.getComputedStyle(sidebar) : null;
            return {
                sidebar_width: sidebar ? sidebar.offsetWidth : null,
                sidebar_height: sidebar ? sidebar.offsetHeight : null,
                sidebar_classes: sidebar ? Array.from(sidebar.classList) : [],
                body_classes: Array.from(document.body.classList),
                toggle_display: toggleBtn ? getComputedStyle(toggleBtn).display : null,
                toggle_visibility: toggleBtn ? getComputedStyle(toggleBtn).visibility : null,
                edge_toggle_display: edgeToggle ? getComputedStyle(edgeToggle).display : null,
                edge_toggle_visibility: edgeToggle ? getComputedStyle(edgeToggle).visibility : null,
                toggle_title: toggleBtn ? toggleBtn.getAttribute('title') : null,
                toggle_aria_expanded: toggleBtn ? toggleBtn.getAttribute('aria-expanded') : null,
                toggle_icon_svg: toggleIcon ? toggleIcon.innerHTML.replace(/\\s+/g, ' ').trim() : null,
                overlay_classes: overlay ? Array.from(overlay.classList) : [],
                overlay_display: overlay ? getComputedStyle(overlay).display : null,
                overlay_opacity: overlay ? getComputedStyle(overlay).opacity : null,
                first_item_tabindex: sidebarItems.length ? sidebarItems[0].getAttribute('tabindex') : null,
                first_item_aria_hidden: sidebarItems.length ? sidebarItems[0].getAttribute('aria-hidden') : null,
                has_toggleSidebarCollapse: (typeof window.toggleSidebarCollapse !== 'undefined') ||
                    (window.TTSApp && window.TTSApp.sidebar && typeof window.TTSApp.sidebar.toggleCollapse === 'function'),
                has_toggleSidebar: typeof window.toggleSidebar !== 'undefined',
            };
        }
    """)
    return {**data, "label": label}


def capture_animation_frames(page, outdir, prefix):
    """Capture sidebar collapse animation at key timestamps."""
    frames = []
    sidebar = page.locator('#sidebar, .sidebar').first
    if not sidebar.count():
        return frames
    # start expanded
    page.evaluate('() => { const sb = document.getElementById("sidebar") || document.querySelector(".sidebar"); if (sb) { sb.classList.remove("collapsed"); sb.classList.remove("collapsing"); } }')
    page.wait_for_timeout(300)
    path = os.path.join(outdir, f"{prefix}_anim_00_expanded.png")
    page.screenshot(path=path)
    frames.append(("expanded", path))
    # trigger collapse
    page.evaluate('() => { if (typeof toggleSidebarCollapse === "function") toggleSidebarCollapse(); else if (window.TTSApp && window.TTSApp.sidebar && window.TTSApp.sidebar.toggleCollapse) window.TTSApp.sidebar.toggleCollapse(); }')
    for i, delay in enumerate([50, 100, 150, 250], start=1):
        page.wait_for_timeout(delay if i == 1 else delay - [50, 100, 150, 250][i-2])
        path = os.path.join(outdir, f"{prefix}_anim_0{i}_collapse_{delay}ms.png")
        page.screenshot(path=path)
        frames.append((f"collapse_{delay}ms", path))
    # trigger expand
    page.evaluate('() => { if (typeof toggleSidebarCollapse === "function") toggleSidebarCollapse(); else if (window.TTSApp && window.TTSApp.sidebar && window.TTSApp.sidebar.toggleCollapse) window.TTSApp.sidebar.toggleCollapse(); }')
    for i, delay in enumerate([50, 100, 250, 350], start=1):
        page.wait_for_timeout(delay if i == 1 else delay - [50, 100, 250, 350][i-2])
        path = os.path.join(outdir, f"{prefix}_anim_{i+4}_expand_{delay}ms.png")
        page.screenshot(path=path)
        frames.append((f"expand_{delay}ms", path))
    return frames


def test_responsive(page, url, outdir, prefix):
    results = []
    for width in RESPONSIVE_WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(url, wait_until="load", timeout=30000)
        page.wait_for_timeout(800)
        path = os.path.join(outdir, f"{prefix}_responsive_{width}.png")
        page.screenshot(path=path)
        info = page.evaluate("""
            () => {
                const sidebar = document.getElementById('sidebar') || document.querySelector('.sidebar');
                const main = document.querySelector('.main-content');
                const topbar = document.querySelector('.top-bar');
                const toggleBtn = document.getElementById('sidebar-toggle-btn');
                const mobileToggle = document.querySelector('.top-bar-mobile-toggle');
                return {
                    width: window.innerWidth,
                    sidebar_width: sidebar ? sidebar.offsetWidth : null,
                    sidebar_display: sidebar ? getComputedStyle(sidebar).display : null,
                    main_margin_left: main ? getComputedStyle(main).marginLeft : null,
                    topbar_height: topbar ? topbar.offsetHeight : null,
                    toggle_display: toggleBtn ? getComputedStyle(toggleBtn).display : null,
                    mobile_toggle_display: mobileToggle ? getComputedStyle(mobileToggle).display : null,
                };
            }
        """)
        info["screenshot"] = path
        results.append(info)
    return results


def compare_computed_styles(actual_page, replica_page, selectors):
    """Compare computed styles for given selectors."""
    results = []
    for sel in selectors:
        try:
            a = actual_page.evaluate(f"""
                () => {{
                    const el = document.querySelector({json.dumps(sel)});
                    if (!el) return null;
                    const s = getComputedStyle(el);
                    return {{
                        color: s.color,
                        backgroundColor: s.backgroundColor,
                        fontSize: s.fontSize,
                        fontFamily: s.fontFamily.split(',')[0],
                        fontWeight: s.fontWeight,
                        lineHeight: s.lineHeight,
                        padding: s.padding,
                        margin: s.margin,
                        borderRadius: s.borderRadius,
                        width: el.offsetWidth,
                        height: el.offsetHeight,
                    }};
                }}
            """)
            r = replica_page.evaluate(f"""
                () => {{
                    const el = document.querySelector({json.dumps(sel)});
                    if (!el) return null;
                    const s = getComputedStyle(el);
                    return {{
                        color: s.color,
                        backgroundColor: s.backgroundColor,
                        fontSize: s.fontSize,
                        fontFamily: s.fontFamily.split(',')[0],
                        fontWeight: s.fontWeight,
                        lineHeight: s.lineHeight,
                        padding: s.padding,
                        margin: s.margin,
                        borderRadius: s.borderRadius,
                        width: el.offsetWidth,
                        height: el.offsetHeight,
                    }};
                }}
            """)
            same = a == r
            results.append({"selector": sel, "actual": a, "replica": r, "same": same})
        except Exception as e:
            results.append({"selector": sel, "error": str(e)})
    return results


def test_interactive_elements(page, prefix):
    """Test basic interactive elements."""
    results = []
    # theme toggle
    try:
        before = page.evaluate('() => document.documentElement.classList.contains("dark")')
        if page.locator('#theme-toggle-btn').count():
            page.click('#theme-toggle-btn', timeout=2000)
            page.wait_for_timeout(200)
            after = page.evaluate('() => document.documentElement.classList.contains("dark")')
            results.append({"element": "theme-toggle-btn", "before_dark": before, "after_dark": after, "ok": before != after})
            page.click('#theme-toggle-btn', timeout=2000)
            page.wait_for_timeout(200)
        else:
            results.append({"element": "theme-toggle-btn", "ok": False, "reason": "not found"})
    except Exception as e:
        results.append({"element": "theme-toggle-btn", "ok": False, "reason": str(e)})

    # sidebar collapse toggle
    try:
        page.click('#sidebar-toggle-btn', timeout=2000)
        page.wait_for_timeout(500)
        collapsed1 = page.evaluate('() => { const sb = document.getElementById("sidebar") || document.querySelector(".sidebar"); return sb ? sb.classList.contains("collapsed") : null; }')
        page.click('#sidebar-toggle-btn', timeout=2000)
        page.wait_for_timeout(500)
        collapsed2 = page.evaluate('() => { const sb = document.getElementById("sidebar") || document.querySelector(".sidebar"); return sb ? sb.classList.contains("collapsed") : null; }')
        results.append({"element": "sidebar-toggle-btn", "collapsed_after_click": collapsed1, "expanded_after_click": collapsed2, "ok": collapsed1 is True and collapsed2 is False})
    except Exception as e:
        results.append({"element": "sidebar-toggle-btn", "ok": False, "reason": str(e)})

    # tab switching
    for tab_id, tab_name in TABS[:5]:
        try:
            btn = page.locator(f'button.sidebar-item[data-tab="{tab_id}"], .sidebar-item[data-tab="{tab_id}"]')
            if btn.count():
                btn.click(timeout=2000)
                page.wait_for_timeout(300)
                active = page.evaluate(f'() => {{ const el = document.querySelector(\'.sidebar-item[data-tab="{tab_id}"]\'); return el ? el.classList.contains("active") : null; }}')
                visible = page.evaluate(f'() => {{ const el = document.getElementById("page-{tab_id}") || document.querySelector(".tab-page[data-page=\'{tab_id}\']"); return el ? getComputedStyle(el).display !== "none" : null; }}')
                results.append({"element": f"tab-{tab_id}", "active": active, "visible": visible, "ok": active is True and visible is True})
            else:
                results.append({"element": f"tab-{tab_id}", "ok": False, "reason": "not found"})
        except Exception as e:
            results.append({"element": f"tab-{tab_id}", "ok": False, "reason": str(e)})
    return results


def screenshot(page, path):
    page.screenshot(path=path, full_page=False)
    return path


def test_target(browser, name, url, out_subdir):
    out = os.path.join(OUTDIR, out_subdir)
    os.makedirs(out, exist_ok=True)
    context = browser.new_context(viewport={"width": 1920, "height": 1080})
    page = context.new_page()
    errors = collect_errors(page)

    print(f"\n>>> 测试 {'实际应用' if name == 'actual' else 'HTML复刻件'}: {url}")
    page.goto(url, wait_until="load", timeout=30000)
    page.wait_for_timeout(1200)

    states = []
    paths = {}

    # initial state
    states.append(inspect_sidebar_state(page, "initial"))
    paths["initial"] = screenshot(page, os.path.join(out, "00_initial.png"))

    # click desktop collapse toggle
    try:
        page.click("#sidebar-toggle-btn", timeout=3000)
    except PWTimeout:
        print("    未找到桌面折叠按钮")
    page.wait_for_timeout(700)
    states.append(inspect_sidebar_state(page, "after_collapse_click"))
    paths["collapsed"] = screenshot(page, os.path.join(out, "01_collapsed.png"))

    # click again to expand (use edge toggle if desktop toggle is hidden)
    try:
        is_desktop_visible = page.evaluate(
            "() => { const b = document.getElementById('sidebar-toggle-btn'); return b && getComputedStyle(b).display !== 'none'; }"
        )
        if is_desktop_visible:
            page.click("#sidebar-toggle-btn", timeout=3000)
        else:
            page.click("#sidebar-edge-toggle", timeout=3000)
    except PWTimeout:
        pass
    page.wait_for_timeout(900)
    states.append(inspect_sidebar_state(page, "after_expand_click"))
    paths["expanded"] = screenshot(page, os.path.join(out, "02_expanded.png"))

    # capture animation frames
    anim_frames = capture_animation_frames(page, out, name)

    # responsive tests
    responsive_results = test_responsive(page, url, out, name)

    # interactive tests
    interactive_results = test_interactive_elements(page, name)

    # tab screenshots
    tab_paths = {}
    for tab_id, tab_name in TABS:
        try:
            btn = page.locator(f'button.sidebar-item[data-tab="{tab_id}"], .sidebar-item[data-tab="{tab_id}"]')
            if btn.count():
                btn.click(timeout=3000)
                page.wait_for_timeout(600)
            else:
                print(f"    警告: 未找到 {tab_id} 侧边栏按钮")
            tab_paths[tab_id] = screenshot(page, os.path.join(out, f"tab_{tab_id}.png"))
        except Exception as e:
            print(f"    切换 {tab_id} 失败: {e}")
            tab_paths[tab_id] = None

    context.close()
    return {
        "states": states,
        "paths": paths,
        "tab_paths": tab_paths,
        "anim_frames": anim_frames,
        "responsive": responsive_results,
        "interactive": interactive_results,
        "errors": errors,
    }


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        actual = test_target(browser, "actual", ACTUAL_URL, "actual")
        replica = test_target(browser, "replica", REPLICA_URL, "replica")
        browser.close()

    # style comparison on voice_design page
    print("\n>>> 计算样式对比...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        actual_page = context.new_page()
        actual_page.goto(ACTUAL_URL, wait_until="load", timeout=30000)
        actual_page.wait_for_timeout(1200)
        replica_page = context.new_page()
        replica_page.goto(REPLICA_URL, wait_until="load", timeout=30000)
        replica_page.wait_for_timeout(1200)
        selectors = [
            ".sidebar",
            ".sidebar-item.active",
            ".top-bar",
            ".top-bar-title",
            ".main-content",
            ".page-title",
            ".card",
            ".btn-primary",
            ".mini-monitor",
        ]
        style_comparison = compare_computed_styles(actual_page, replica_page, selectors)
        context.close()
        browser.close()

    # Report
    report_path = os.path.join(OUTDIR, "comprehensive_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# TTS MultiModel 复刻件与实际应用一致性验证报告（全面版）\n\n")
        f.write(f"- 实际应用: `{ACTUAL_URL}`\n")
        f.write(f"- HTML复刻件: `{REPLICA_URL}`\n")
        f.write(f"- 验证时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        f.write("## 侧边栏折叠状态对比\n\n")
        f.write("| 阶段 | 指标 | 实际应用 | 复刻件 | 一致 |\n")
        f.write("|------|------|----------|--------|------|\n")
        keys = [
            ("sidebar_width", "宽度"),
            ("sidebar_height", "高度"),
            ("sidebar_classes", "侧边栏类"),
            ("body_classes", "body类"),
            ("toggle_display", "桌面按钮 display"),
            ("toggle_visibility", "桌面按钮 visibility"),
            ("edge_toggle_display", "边缘按钮 display"),
            ("edge_toggle_visibility", "边缘按钮 visibility"),
            ("toggle_title", "按钮 title"),
            ("toggle_aria_expanded", "aria-expanded"),
            ("toggle_icon_svg", "切换图标 SVG"),
            ("overlay_classes", "遮罩类"),
            ("overlay_display", "遮罩 display"),
            ("overlay_opacity", "遮罩 opacity"),
            ("first_item_tabindex", "首项 tabindex"),
            ("first_item_aria_hidden", "首项 aria-hidden"),
            ("has_toggleSidebarCollapse", "toggleSidebarCollapse 函数"),
            ("has_toggleSidebar", "toggleSidebar 函数"),
        ]
        for a_state, r_state in zip(actual["states"], replica["states"]):
            label = a_state["label"]
            for key, desc in keys:
                av = a_state.get(key)
                rv = r_state.get(key)
                same = "是" if av == rv else "否"
                f.write(f"| {label} | {desc} | `{av}` | `{rv}` | {same} |\n")

        f.write("\n## 侧边栏折叠动画帧\n\n")
        for (a_label, a_path), (r_label, r_path) in zip(actual["anim_frames"], replica["anim_frames"]):
            f.write(f"- {a_label}: actual=`{a_path}`, replica=`{r_path}`\n")

        f.write("\n## 响应式布局对比\n\n")
        f.write("| 宽度 | 实际 sidebar宽 | 复刻 sidebar宽 | 实际 main margin-left | 复刻 main margin-left | 实际 桌面按钮 | 复刻 桌面按钮 | 实际 移动端按钮 | 复刻 移动端按钮 |\n")
        f.write("|------|----------------|----------------|------------------------|------------------------|---------------|---------------|-----------------|-----------------|\n")
        for a_resp, r_resp in zip(actual["responsive"], replica["responsive"]):
            f.write(f"| {a_resp['width']} | {a_resp['sidebar_width']} | {r_resp['sidebar_width']} | {a_resp['main_margin_left']} | {r_resp['main_margin_left']} | {a_resp['toggle_display']} | {r_resp['toggle_display']} | {a_resp['mobile_toggle_display']} | {r_resp['mobile_toggle_display']} |\n")

        f.write("\n## 计算样式对比（voice_design 页面）\n\n")
        f.write("| 选择器 | 一致 | 实际 | 复刻 |\n")
        f.write("|--------|------|------|------|\n")
        for item in style_comparison:
            if "error" in item:
                f.write(f"| {item['selector']} | 错误 | {item['error']} | - |\n")
            else:
                f.write(f"| {item['selector']} | {'是' if item['same'] else '否'} | `{item['actual']}` | `{item['replica']}` |\n")

        f.write("\n## 交互功能测试\n\n")
        f.write("### 实际应用\n\n")
        for item in actual["interactive"]:
            ok = "通过" if item.get("ok") else "失败"
            f.write(f"- **{item['element']}**: {ok} - {item}\n")
        f.write("\n### 复刻件\n\n")
        for item in replica["interactive"]:
            ok = "通过" if item.get("ok") else "失败"
            f.write(f"- **{item['element']}**: {ok} - {item}\n")

        f.write("\n## 控制台错误\n\n")
        for src, data in [("实际应用", actual), ("复刻件", replica)]:
            f.write(f"### {src}\n")
            if data["errors"]:
                for err in data["errors"]:
                    f.write(f"- {err}\n")
            else:
                f.write("- 无\n")
            f.write("\n")

        f.write("## 页面截图路径\n\n")
        for tab_id, _ in TABS:
            f.write(f"- {tab_id}: actual=`{actual['tab_paths'].get(tab_id)}`, replica=`{replica['tab_paths'].get(tab_id)}`\n")

    print(f"\n全面报告已保存: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
