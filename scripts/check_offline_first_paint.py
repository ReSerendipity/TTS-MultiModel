#!/usr/bin/env python3
"""断网首屏核对：把"拔网线看首屏"变成可判定命令。

    python scripts/check_offline_first_paint.py                 # 默认 127.0.0.1:7869
    python scripts/check_offline_first_paint.py --url http://127.0.0.1:7871/

**口径（重要，别当成真拔网线）**：本脚本在浏览器里掐掉**所有目标主机不是 localhost 的
请求**（`route.abort()`），localhost 一律放行。所以它证明的是这三件事：
① 首屏加载过程**一次出网请求都不尝试**（连"试一下再回退"都没有）；
② 掐网之后页面仍然完整渲染（侧栏、内容区、控件都在），不是靠某个 CDN 兜住的；
③ 加载期没有未捕获 JS 异常、没有 Console error。
它**不覆盖**真断网时的 DNS/代理层失败、也不覆盖"服务本身起不来"——那属于启动链路，
另有 `docs/DOD.md` §5.2 的其它命令与人工项。

WHY 单独一个脚本：外链资源在 CSP `default-src 'self'` 下是**静默失败**的——请求被浏览器
拦掉，页面照常显示，只是某块东西永远不对（GOTCHAS #127 就是 Google Fonts 被 CSP 挡掉后
字体菜单一直"看起来是好的"）。人眼"看一眼首屏没问题"抓不到这类东西。
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from urllib.parse import urlparse

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}

# 首屏"渲染完整"的判据：全部取页面自己的 DOM，不看网络也不看截图。
_PAINT_JS = """
() => {
  const q = (sel) => document.querySelector(sel);
  const tabs = [...document.querySelectorAll('.sidebar-link, .nav-item, [data-tab]')];
  const visibleTabs = tabs.filter((el) => el.offsetParent !== null);
  const content = q('#tab-content');
  const form = content ? content.querySelector('form') : null;
  const text = (document.body.innerText || '');
  return {
    title: document.title || '',
    visibleTabCount: visibleTabs.length,
    hasContent: !!content,
    contentChars: content ? (content.innerText || '').trim().length : 0,
    hasForm: !!form,
    formId: form ? (form.id || '') : '',
    // 未翻译的 i18n 键会以 `some_key_name` 形态漏到屏幕上
    leaksUntranslatedKey: /\\b[a-z][a-z0-9]+(_[a-z0-9]+){2,}\\b/.test(text),
    // Jinja 没渲染时会把 {{ ... }} / {% ... %} 原样留在屏幕上
    hasRawJinja: /\\{\\{|%\\}/.test(text),
  };
}
"""


def _is_local(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in _LOCAL_HOSTS or host.startswith("127.")


def check(url: str) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright 未安装：pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    external: list[str] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    local_requests = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()

        def _guard(route):
            nonlocal local_requests
            req_url = route.request.url
            if req_url.startswith("data:") or req_url.startswith("blob:"):
                route.continue_()
                return
            if _is_local(req_url):
                local_requests += 1
                route.continue_()
            else:
                external.append(req_url)
                route.abort()

        page.route("**/*", _guard)
        page.on("console", lambda m: console_errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        try:
            page.goto(url, wait_until="load", timeout=60000)
        except Exception as e:  # noqa: BLE001 — 起不来就是要报出来的结论
            browser.close()
            print(f"首屏加载失败：{e}", file=sys.stderr)
            return 1

        with contextlib.suppress(Exception):
            page.wait_for_function(
                "() => document.querySelectorAll('[data-tab], .sidebar-link').length > 0", timeout=15000
            )
        with contextlib.suppress(Exception):
            page.wait_for_function("() => document.fonts.status === 'loaded'", timeout=15000)
        page.wait_for_timeout(1200)
        paint = page.evaluate(_PAINT_JS)

        # 再点几次侧栏换页：断网下 htmx 拉的是本机 partial，不该有任何出网动作。
        # 每次点完等换页落定——本脚本管的是"断网后还能不能正常渲染"，
        # 连点并发覆盖是另一件事，由 scripts/check_tab_switch_race.py 负责。
        tabs = page.query_selector_all("[data-tab]")
        clicked = 0
        for tab in tabs[:3]:
            with contextlib.suppress(Exception):
                tab.evaluate("el => el.dispatchEvent(new MouseEvent('click', {bubbles: true}))")
                clicked += 1
                page.wait_for_timeout(1500)
        page.wait_for_timeout(500)
        browser.close()

    print(f"放行本机请求 {local_requests} 次；尝试点击侧栏 {clicked} 个入口")
    print(
        f"首屏：标题={paint['title']!r} 可见标签={paint['visibleTabCount']} 内容区={paint['hasContent']} "
        f"内容字符={paint['contentChars']} 表单={paint['formId'] or '（本页无表单）'}"
    )
    if paint["hasRawJinja"]:
        print("  屏幕上残留未渲染的 Jinja 标记", file=sys.stderr)
    if paint["leaksUntranslatedKey"]:
        print("  屏幕上出现疑似未翻译的 i18n 键（形如 xxx_yyy_zzz）", file=sys.stderr)

    if external:
        uniq = sorted(set(external))
        print(f"\n发现 {len(external)} 次出网请求尝试（{len(uniq)} 个目标）：", file=sys.stderr)
        for u in uniq[:10]:
            print(f"  {u[:160]}", file=sys.stderr)
    for line in page_errors[:5]:
        print(f"  未捕获 JS 异常：{line[:200]}", file=sys.stderr)
    for line in console_errors[:5]:
        print(f"  Console：{line[:200]}", file=sys.stderr)

    ok = (
        not external
        and not page_errors
        and not console_errors
        and paint["visibleTabCount"] > 0
        and paint["hasContent"]
        and paint["contentChars"] > 20
        and not paint["hasRawJinja"]
        and not paint["leaksUntranslatedKey"]
    )
    print("\n结论:", "通过（首屏零出网、渲染完整、无 JS 报错）" if ok else "不通过（见上方明细）")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:7869/")
    args = parser.parse_args()
    sys.exit(check(args.url if args.url.endswith("/") else args.url + "/"))
