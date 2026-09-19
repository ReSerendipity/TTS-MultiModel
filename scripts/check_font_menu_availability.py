#!/usr/bin/env python3
"""核对「切换标题字体」菜单是否诚实：本机没装的家族不能显示成可选。

需要服务已在 127.0.0.1:7869 运行（`python app/clean_launch.py`）。
用 Playwright 打开首屏，读 #fontPop 的条目状态，并统计加载期 Console 里的 CSP 报错。

    python scripts/check_font_menu_availability.py            # 打印实测结果，退出码 0/1
    python scripts/check_font_menu_availability.py --url http://127.0.0.1:7869/

WHY 单独一个脚本：字体失效是**没有任何可见报错**的那种失败——菜单能开、点了会写
localStorage、CSS 变量也变了，只有字形不变（GOTCHAS #127）。所以判据必须落在
"页面自己量出来的可用性"上，而不是人眼看一眼。

注意口径：可用性是「浏览器 + 本机字体回退表」的函数，同一台机器上 headless Chromium
与桌面 Edge 可能给出不同答案（本机实测 Noto Serif SC 在前者可用、在后者被判未安装）。
所以本脚本判的是**同一浏览器内**菜单结论与实测是否一致，不是跨浏览器比数量。
"""

from __future__ import annotations

import argparse
import contextlib
import sys

_PROBE_JS = """
() => {
  const pop = document.getElementById('fontPop');
  const btn = document.getElementById('font-toggle-btn');
  if (!pop || !btn) return {error: '找不到 #fontPop / #font-toggle-btn'};
  const items = [...pop.querySelectorAll('button[data-f]')];
  const width = (stack) => {
    const s = document.createElement('span');
    s.style.cssText = 'position:absolute;left:-9999px;white-space:pre;font-size:64px;font-family:' + stack;
    s.textContent = 'Hamburgefonstiv 永字八法';
    document.body.appendChild(s);
    const w = Math.round(s.getBoundingClientRect().width * 100) / 100;
    s.remove();
    return w;
  };
  // 反向复核菜单结论：与 monospace / serif 两档无关回都比宽度（sans-serif 本身可能
  // 就被某个候选家族占用，不能当基线）。
  const rows = items.map((b) => {
    const fam = /^\\s*"([^"]+)"/.exec(b.dataset.f)[1];
    const differs = Math.abs(width('"' + fam + '", monospace') - width('monospace')) > 0.5
                 && Math.abs(width('"' + fam + '", serif') - width('serif')) > 0.5;
    return {
      family: fam,
      label: b.textContent.trim().slice(0, 20),
      menuSaysAvailable: b.dataset.avail === '1',
      ariaDisabled: b.getAttribute('aria-disabled'),
      measuredAvailable: differs,
    };
  });
  return {
    total: items.length,
    available: rows.filter((r) => r.menuSaysAvailable).map((r) => r.family),
    rows,
  };
}
"""


def check(url: str) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright 未安装：pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    csp_lines: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.on("console", lambda m: csp_lines.append(m.text) if "Content Security Policy" in m.text else None)
        page.on("requestfailed", lambda r: csp_lines.append(f"requestfailed {r.url}") if "//fonts." in r.url else None)
        page.goto(url, wait_until="load")
        page.wait_for_function("() => document.querySelectorAll('#fontPop button[data-f]').length > 0")
        # @font-face 的字节是懒加载的：菜单会在 document.fonts.load 触发后重算一次。
        # 不等它就不公平——首屏那一刻量到的全是回退宽度，14 项都会被判"未安装"。
        with contextlib.suppress(Exception):
            page.wait_for_function("() => document.fonts.status === 'loaded'", timeout=20000)
        page.wait_for_timeout(1500)
        data = page.evaluate(_PROBE_JS)
        browser.close()

    if "error" in data:
        print(data["error"], file=sys.stderr)
        return 1

    bad = [r for r in data["rows"] if r["menuSaysAvailable"] != r["measuredAvailable"]]
    print(f"菜单条目 {data['total']} 项，本机可用 {len(data['available'])} 项：{data['available'] or '（无）'}")
    for r in data["rows"]:
        flag = "" if r["menuSaysAvailable"] == r["measuredAvailable"] else "   <== 菜单与实测不一致"
        print(f"  {r['family']:<24} 菜单={r['menuSaysAvailable']!s:<5} 实测={r['measuredAvailable']!s:<5}{flag}")
    if csp_lines:
        print(f"\n加载期出现 {len(csp_lines)} 条 CSP/外链失败：", file=sys.stderr)
        for line in csp_lines[:5]:
            print(f"  {line[:160]}", file=sys.stderr)

    ok = not bad and not csp_lines
    print("\n结论:", "通过" if ok else "不通过（菜单条目状态与实测不符，或有被拦的外链资源）")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:7869/")
    raise SystemExit(check(parser.parse_args().url))
