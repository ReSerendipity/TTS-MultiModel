#!/usr/bin/env python3
"""侧栏换页竞态核对：慢响应下"最后点的那一页"必须赢。

    python scripts/check_tab_switch_race.py                    # 默认 127.0.0.1:7869
    python scripts/check_tab_switch_race.py --url http://127.0.0.1:7871/ --trials 4

**为什么要注入延迟**：本机 `/tab/*` 只要 5~40 ms，两次点击的响应不会交错，看着怎么点都对。
真实世界里（服务端正在跑一次合成 —— 单 worker 串行队列；或者网络慢）先点的那一页会后到，
而侧栏 16 个入口全部 `hx-target="#tab-content"`，后到的旧响应会把新页面盖掉。
实测（给第一个 /tab/ 请求注入 1.5s、350 ms 后点第二个）：无 `hx-sync` 的树
**2/2 组末态停在先点那一页**，带 `hx-sync="#tab-content:queue last"` 之后 **0/2**。

判据是「末态 = 最后一次点击的目标页」，用**结构签名**（内容区 id 集合、表单 id、hx-post、
标签构成）与慢速基线比对，不看像素。

Console 里可能仍有 htmx 自己抛的一条 `insertBefore` TypeError（两个换页同拍交错时的内部
异常，末态不受影响；vendored 库不改）。它**报出来但不判负**，判负只看末态与渲染。

必须用 async_api：sync Playwright 的 route handler 会把请求串行化，两个请求不会同时在飞，
拿它做这个核对会得到"两边都对"的假阴性（本脚本第一版就栽在这里）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

DEFAULT_URL = "http://127.0.0.1:7869/"
LATENCY_S = 1.5
CLICK_GAP_MS = 350
SETTLE_MS = 3500

CLICK_JS = "(t) => document.querySelector(`[data-tab='${t}']`).dispatchEvent(new MouseEvent('click', {bubbles: true}))"

SIGN_JS = """
() => {
  const c = document.getElementById('tab-content');
  const active = document.querySelector('.sidebar-item.active');
  if (!c) return {error: '没有 #tab-content'};
  const pick = (sel, f) => [...c.querySelectorAll(sel)].map(f).filter(Boolean).sort().join(',');
  const counts = {};
  for (const el of c.querySelectorAll('h1,h2,h3,h4,p,a,button,input,table,tr,label,pre')) {
    const k = el.tagName.toLowerCase();
    counts[k] = (counts[k] || 0) + 1;
  }
  return {
    active: active ? active.getAttribute('data-tab') : null,
    ids: pick('[id]', (e) => e.id),
    forms: pick('form', (f) => f.id || '(无id)'),
    posts: pick('[hx-post]', (e) => e.getAttribute('hx-post')),
    shape: Object.keys(counts).sort().map((k) => k + ':' + counts[k]).join(','),
    chars: (c.innerText || '').trim().length,
  };
}
"""

#: (先点, 后点) 用例。后点的一页必须有可辨识的结构（表单或标签构成），且与先点的不同分组。
PAIRS = [
    ("voice_design", "persona"),
    ("history", "settings"),
    ("voice_clone", "indextts2_clone"),
    ("ultimate_clone", "indextts2_emotion"),
    ("script", "lora"),
    ("prompt_continue", "help"),
]


def _sign_diff(got: dict, want: dict) -> str:
    return ",".join(k for k in ("ids", "forms", "posts", "shape") if got.get(k) != want.get(k))


async def run(url: str, trials: int) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("playwright 未安装：pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    base = url if url.endswith("/") else url + "/"
    wrong = total = htmx_errors = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        errs: list[str] = []
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

        await page.goto(base, wait_until="load", timeout=60000)
        await page.wait_for_timeout(2200)
        tabs = await page.evaluate(
            "() => [...document.querySelectorAll('[data-tab]')].map(e => e.getAttribute('data-tab'))"
        )
        if len(tabs) < 8:
            print(f"只找到 {len(tabs)} 个侧栏入口，扫描可能失效", file=sys.stderr)
            await browser.close()
            return 2

        baseline: dict[str, dict] = {}
        for tab in tabs:
            await page.evaluate(CLICK_JS, tab)
            await page.wait_for_timeout(1500)
            st = await page.evaluate(SIGN_JS)
            if "error" in st or st["active"] != tab or not (st["ids"] or st["shape"]):
                print(f"基线取不到：{tab} -> {st}", file=sys.stderr)
                await browser.close()
                return 1
            baseline[tab] = st
        print(f"基线：{len(baseline)} 页；慢速逐页切换期 Console error = {len(errs)}")

        armed = {"on": False}  # 只拖慢下一次发起的 /tab/ 请求

        async def _slow_first(route):
            if armed["on"]:
                armed["on"] = False
                await asyncio.sleep(LATENCY_S)
            await route.continue_()

        await page.route("**/tab/**", _slow_first)

        for a, b in PAIRS[: max(1, min(trials, len(PAIRS)))]:
            errs.clear()
            armed["on"] = True
            await page.evaluate(CLICK_JS, a)  # 这一发被拖慢
            await page.wait_for_timeout(CLICK_GAP_MS)  # 人手改主意的速度
            await page.evaluate(CLICK_JS, b)
            await page.wait_for_timeout(SETTLE_MS)
            total += 1
            got = await page.evaluate(SIGN_JS)
            diff = _sign_diff(got, baseline[b])
            if got.get("active") != b or diff:
                wrong += 1
                print(
                    f"  失败：{a} → {CLICK_GAP_MS}ms → {b}；末态 active={got.get('active')!r} "
                    f"差异字段={diff or '（结构一致）'} ← 被先点那一页占住"
                )
            else:
                print(f"  OK：{a} → {b}，末态落在 {b}（最后一次点击赢）")
            if any("insertBefore" in e for e in errs):
                htmx_errors += 1
                print(f"      htmx 内部报错（不判负）：{errs[0][:70]}…")
        await browser.close()

    print(f"\n共 {total} 组竞态用例：末态错误 {wrong}；htmx 内部报错 {htmx_errors} 次（不计判负）")
    ok = wrong == 0 and total > 0
    print("结论:", "通过（最后一次点击总是赢）" if ok else "不通过（存在被旧响应盖掉的页面）")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--trials", type=int, default=6)
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.url, args.trials)))
