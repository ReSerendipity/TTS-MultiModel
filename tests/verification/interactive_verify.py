# -*- coding: utf-8 -*-
"""Verify interactive elements via JS evaluation (avoids viewport click issues)."""
import os
import sys
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTDIR = os.path.join(ROOT, "verification_output")
REPLICA_URL = os.environ.get("TTS_REPLICA_URL", "")  # Optional: empty = skip replica comparison

TABS = [
    "voice_design", "voice_clone", "ultimate_clone", "script",
    "prompt_continue", "lora", "lora_training", "indextts2_clone",
    "indextts2_emotion", "indextts2_duration", "settings", "history",
    "persona", "help",
]


def test_interactive():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(REPLICA_URL, wait_until="load", timeout=30000)
        page.wait_for_timeout(1000)

        # Theme toggle
        before = page.evaluate('() => document.documentElement.classList.contains("dark")')
        page.evaluate('() => { if (typeof toggleTheme === "function") toggleTheme(); }')
        after = page.evaluate('() => document.documentElement.classList.contains("dark")')
        page.evaluate('() => { if (typeof toggleTheme === "function") toggleTheme(); }')
        results.append({"feature": "theme-toggle", "ok": before != after, "before": before, "after": after})

        # Sidebar collapse
        page.evaluate('() => { if (typeof toggleSidebarCollapse === "function") toggleSidebarCollapse(); }')
        page.wait_for_timeout(600)
        collapsed = page.evaluate('() => { const sb = document.getElementById("sidebar"); return sb ? sb.classList.contains("collapsed") : null; }')
        body_collapsed = page.evaluate('() => document.body.classList.contains("sidebar-is-collapsed")')
        page.evaluate('() => { if (typeof toggleSidebarCollapse === "function") toggleSidebarCollapse(); }')
        page.wait_for_timeout(600)
        expanded = page.evaluate('() => { const sb = document.getElementById("sidebar"); return sb ? !sb.classList.contains("collapsed") : null; }')
        results.append({"feature": "sidebar-collapse", "ok": collapsed is True and expanded is True, "collapsed": collapsed, "body_collapsed": body_collapsed, "expanded": expanded})

        # Tab switching
        for tab_id in TABS:
            page.evaluate(f'() => {{ if (typeof switchPage === "function") switchPage("{tab_id}"); }}')
            page.wait_for_timeout(200)
            active = page.evaluate(f'() => {{ const el = document.querySelector(\'.sidebar-item[data-tab="{tab_id}"]\'); return el ? el.classList.contains("active") : null; }}')
            visible = page.evaluate(f'() => {{ const el = document.getElementById("page-{tab_id}"); return el ? el.classList.contains("active") : null; }}')
            title = page.evaluate('() => { const el = document.getElementById("top-page-title"); return el ? el.textContent : null; }')
            results.append({"feature": f"tab-{tab_id}", "ok": active is True and visible is True, "active": active, "visible": visible, "title": title})

        # Mobile sidebar open/close
        page.set_viewport_size({"width": 768, "height": 900})
        page.goto(REPLICA_URL, wait_until="load", timeout=30000)
        page.wait_for_timeout(800)
        page.evaluate('() => { if (typeof toggleSidebar === "function") toggleSidebar(); }')
        page.wait_for_timeout(500)
        mobile_open = page.evaluate('() => { const sb = document.getElementById("sidebar"); return sb ? sb.classList.contains("open") : null; }')
        overlay_visible = page.evaluate('() => { const ov = document.getElementById("sidebar-overlay"); return ov ? ov.classList.contains("visible") : null; }')
        page.evaluate('() => { if (typeof closeSidebar === "function") closeSidebar(); }')
        page.wait_for_timeout(500)
        mobile_closed = page.evaluate('() => { const sb = document.getElementById("sidebar"); return sb ? !sb.classList.contains("open") : null; }')
        results.append({"feature": "mobile-sidebar", "ok": mobile_open is True and mobile_closed is True, "open": mobile_open, "overlay": overlay_visible, "closed": mobile_closed})

        browser.close()
    return results


def main():
    results = test_interactive()
    report_path = os.path.join(OUTDIR, "interactive_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 复刻件交互功能验证报告\n\n")
        f.write("| 功能 | 结果 | 详情 |\n")
        f.write("|------|------|------|\n")
        for r in results:
            status = "通过" if r["ok"] else "失败"
            f.write(f"| {r['feature']} | {status} | {r} |\n")
    print(f"交互验证报告已保存: {report_path}")
    failed = [r for r in results if not r["ok"]]
    if failed:
        print(f"失败项: {len(failed)}")
        for r in failed:
            print(f"  - {r['feature']}")
    else:
        print("所有交互功能均通过")
