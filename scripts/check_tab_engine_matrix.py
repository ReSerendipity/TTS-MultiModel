#!/usr/bin/env python3
"""真机跑一遍「3 引擎 × 各功能页」矩阵：每页的表单必须打到本引擎的生成端点。

不加载任何模型（0 GPU、0 权重传输）：把 ``/api/model/status`` 与
``/api/model/load|switch`` 两个请求在页面里打桩成成功，其余请求照常放行，
于是可以纯 UI 地走完「切引擎 → 逐个点侧栏 → 看屏幕上真的是哪张表单」。

WHY 需要它：2026-09-19 发布前发现的 #131 —— 切到 IndexTTS 2.5 后侧栏已经换了，
但 #tab-content 里还是 VoxCPM2 的表单，点生成 → 400。这类「高亮与内容分家」在
pytest 与静态守卫里都看不出来（模板和后端各自都是对的），只有真点一遍才暴露。

    python scripts/check_tab_engine_matrix.py            # 需服务在 127.0.0.1:7869
    python scripts/check_tab_engine_matrix.py --url http://127.0.0.1:7870/

退出码 0 = 矩阵全绿；1 = 有格子不对（逐行打印）。
"""

from __future__ import annotations

import argparse
import sys

#: 引擎 → 该引擎功能页允许的生成端点前缀（indextts2 与 indextts20 共用生成端点）
OWN_PREFIX = {
    "voxcpm2": ("/api/generate/voxcpm",),
    "indextts2": ("/api/generate/indextts2",),
    "indextts20": ("/api/generate/indextts2",),
}

_STUB = """
(model) => {
  window.__origFetch = window.__origFetch || window.fetch;
  window.fetch = function (url) {
    const u = String(url);
    const json = (o) => Promise.resolve(new Response(JSON.stringify(o),
      { status: 200, headers: { 'Content-Type': 'application/json' } }));
    if (u.includes('/api/model/status')) return json({ loaded: true, engine: model });
    if (u.includes('/api/model/switch') || u.includes('/api/model/load')) return json({ status: 'ok' });
    return window.__origFetch.apply(this, arguments);
  };
}
"""

_UNSTUB = "() => { if (window.__origFetch) window.fetch = window.__origFetch; }"

_READ = """
() => {
  const form = document.querySelector('#tab-content form');
  const active = document.querySelector('.sidebar-item.active');
  return { formId: form ? form.id : null, action: form ? form.getAttribute('hx-post') : null,
           activeTab: active ? active.dataset.tab : null };
}
"""


def check(url: str) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright 未安装：pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    rows: list[tuple[str, str, str, str]] = []
    failures: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="load")
        page.wait_for_selector(".sidebar-item[data-tab]")
        # 首启引导遮罩会盖住侧栏（真实用户点一下蒙层即关）。这里同样关掉它，
        # 但后续一律用 DOM 级 click：Playwright 的 actionability 检查会因为
        # 任何浮层拦截而超时，而我们要验的是"页面自己的处理链路"，不是命中测试。
        page.evaluate("() => document.getElementById('onboarding-overlay')?.click()")
        page.wait_for_timeout(400)

        for model, prefixes in OWN_PREFIX.items():
            page.evaluate(_STUB, model)
            page.evaluate("(m) => window.switchModel(m)", model)
            page.wait_for_timeout(3500)
            items = page.eval_on_selector_all(
                f'.sidebar-item[data-model="{model}"]',
                "els => els.filter(e => !e.classList.contains('sidebar-item-hidden')).map(e => e.dataset.tab)",
            )
            for tab in items:
                page.eval_on_selector(f'.sidebar-item[data-tab="{tab}"]', "el => el.click()")
                page.wait_for_timeout(2200)
                state = page.evaluate(_READ)
                action = state.get("action") or "-"
                rows.append((model, tab, state.get("formId") or "(无表单)", action))
                if action == "-":
                    continue
                if not action.startswith(prefixes):
                    failures.append(f"{model} / {tab}: 屏幕上的表单打向 {action}，不属于该引擎")
                if state.get("activeTab") != tab:
                    failures.append(f"{model} / {tab}: 点完高亮停在 {state.get('activeTab')}")
        page.evaluate(_UNSTUB)
        browser.close()

    print(f"{'引擎':<12}{'功能页':<22}{'表单':<14}hx-post")
    for r in rows:
        print(f"{r[0]:<12}{r[1]:<22}{r[2]:<14}{r[3]}")
    print(f"\n共 {len(rows)} 格")
    if failures:
        print("不通过：", file=sys.stderr)
        for f in failures:
            print("  " + f, file=sys.stderr)
        return 1
    print("结论: 通过（每页表单都打到自己引擎的生成端点，高亮与内容一致）")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:7869/")
    raise SystemExit(check(parser.parse_args().url))
