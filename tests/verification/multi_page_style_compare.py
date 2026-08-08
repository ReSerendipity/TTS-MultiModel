# -*- coding: utf-8 -*-
"""Compare computed styles across multiple pages."""
import json
import os
from playwright.sync_api import sync_playwright

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

SELECTORS = [
    (".sidebar", ["width", "backgroundColor", "borderRight"]),
    (".sidebar-item", ["padding", "fontSize", "lineHeight", "color", "borderRadius"]),
    (".top-bar", ["height", "backgroundColor", "borderBottom"]),
    (".top-bar-title", ["fontSize", "fontWeight"]),
    (".main-content", ["marginLeft", "padding"]),
    (".card", ["backgroundColor", "borderRadius", "padding", "boxShadow"]),
    (".mini-monitor", ["padding", "borderRadius", "backgroundColor"]),
]


def get_styles(page, selector, props):
    return page.evaluate(f"""
        () => {{
            const el = document.querySelector({json.dumps(selector)});
            if (!el) return null;
            const s = getComputedStyle(el);
            const result = {{}};
            {json.dumps(props)}.forEach(p => result[p] = s[p]);
            return result;
        }}
    """)


def switch_tab(page, tab_id, is_actual):
    if is_actual:
        # actual app uses HTMX, click via JS
        page.evaluate(f"""() => {{
            const item = document.querySelector('.sidebar-item[data-tab="{tab_id}"]');
            if (item) item.click();
        }}""")
    else:
        page.evaluate(f"""() => {{ if (typeof switchTab === "function") switchTab("{tab_id}"); }}""")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})

        actual = context.new_page()
        actual.goto(ACTUAL_URL, wait_until="load", timeout=30000)
        actual.wait_for_timeout(1500)

        replica = context.new_page()
        replica.goto(REPLICA_URL, wait_until="load", timeout=30000)
        replica.wait_for_timeout(1500)

        all_results = []
        for tab_id, tab_name in TABS:
            switch_tab(actual, tab_id, True)
            switch_tab(replica, tab_id, False)
            actual.wait_for_timeout(800)
            replica.wait_for_timeout(800)

            page_results = {"tab": tab_id, "items": []}
            for selector, props in SELECTORS:
                a = get_styles(actual, selector, props)
                r = get_styles(replica, selector, props)
                same = a == r
                page_results["items"].append({"selector": selector, "actual": a, "replica": r, "same": same})
            all_results.append(page_results)

        browser.close()

    report_path = os.path.join(OUTDIR, "multi_page_style_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 多页面计算样式对比报告\n\n")
        for page in all_results:
            f.write(f"## {page['tab']}\n\n")
            f.write("| 选择器 | 一致 | 实际 | 复刻 |\n")
            f.write("|--------|------|------|------|\n")
            for item in page["items"]:
                if item["actual"] is None or item["replica"] is None:
                    f.write(f"| {item['selector']} | 无法对比 | `{item['actual']}` | `{item['replica']}` |\n")
                else:
                    f.write(f"| {item['selector']} | {'是' if item['same'] else '否'} | `{item['actual']}` | `{item['replica']}` |\n")
            f.write("\n")

    print(f"多页面样式报告已保存: {report_path}")


if __name__ == "__main__":
    main()
