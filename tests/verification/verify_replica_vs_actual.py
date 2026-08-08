# -*- coding: utf-8 -*-
"""Compare TTS MultiModel replica HTML against the actual rendered app."""
import os
import sys
import time
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
    """Return structured info about the current sidebar/collapse state."""
    data = page.evaluate("""
        () => {
            const sidebar = document.getElementById('sidebar');
            const toggleBtn = document.getElementById('sidebar-toggle-btn');
            const edgeToggle = document.getElementById('sidebar-edge-toggle');
            const toggleIcon = document.getElementById('sidebar-toggle-icon');
            const overlay = document.getElementById('sidebar-overlay');
            return {
                sidebar_width: sidebar ? sidebar.offsetWidth : null,
                sidebar_classes: sidebar ? Array.from(sidebar.classList) : [],
                body_classes: Array.from(document.body.classList),
                toggle_display: toggleBtn ? getComputedStyle(toggleBtn).display : null,
                edge_toggle_display: edgeToggle ? getComputedStyle(edgeToggle).display : null,
                toggle_title: toggleBtn ? toggleBtn.getAttribute('title') : null,
                toggle_icon_svg: toggleIcon ? toggleIcon.innerHTML : null,
                overlay_classes: overlay ? Array.from(overlay.classList) : [],
                has_toggleSidebarCollapse: (typeof window.toggleSidebarCollapse !== 'undefined') ||
                    (window.TTSApp && window.TTSApp.sidebar && typeof window.TTSApp.sidebar.toggleCollapse === 'function'),
                has_toggleSidebar: typeof window.toggleSidebar !== 'undefined',
            };
        }
    """)
    return {**data, "label": label}


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

    # tab screenshots: start from voice_design
    tab_paths = {}
    for tab_id, tab_name in TABS:
        try:
            btn = page.locator(f'button.sidebar-item[data-tab="{tab_id}"]')
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
    return {"states": states, "paths": paths, "tab_paths": tab_paths, "errors": errors}


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        actual = test_target(browser, "actual", ACTUAL_URL, "actual")
        replica = test_target(browser, "replica", REPLICA_URL, "replica")
        browser.close()

    # Report
    report_path = os.path.join(OUTDIR, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# TTS MultiModel 复刻件与实际应用一致性验证报告\n\n")
        f.write(f"- 实际应用: `{ACTUAL_URL}`\n")
        f.write(f"- HTML复刻件: `{REPLICA_URL}`\n\n")

        f.write("## 侧边栏折叠状态对比\n\n")
        f.write("| 阶段 | 指标 | 实际应用 | 复刻件 | 一致 |\n")
        f.write("|------|------|----------|--------|------|\n")
        for a_state, r_state in zip(actual["states"], replica["states"]):
            label = a_state["label"]
            keys = [
                ("sidebar_width", "宽度"),
                ("sidebar_classes", "侧边栏类"),
                ("body_classes", "body类"),
                ("toggle_display", "桌面按钮 display"),
                ("edge_toggle_display", "边缘按钮 display"),
                ("toggle_title", "按钮 title"),
                ("toggle_icon_svg", "切换图标 SVG"),
                ("has_toggleSidebarCollapse", "toggleSidebarCollapse 函数"),
            ]
            for key, desc in keys:
                av = a_state.get(key)
                rv = r_state.get(key)
                same = "是" if av == rv else "否"
                f.write(f"| {label} | {desc} | `{av}` | `{rv}` | {same} |\n")

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

    print(f"\n报告已保存: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
