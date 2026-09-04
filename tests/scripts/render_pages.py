"""前端冒烟测试的模板渲染脚本（tests/package.json `test:frontend` 引用）。

用法（在 tests/ 目录下）：
    python scripts/render_pages.py
    node tests/frontend/smoke.js

职责：
    用应用注册过 i18n 过滤器的真实 Jinja2 环境（integrated_app.routes.tabs.templates）
    把 smoke.js 覆盖的页面渲染成静态 HTML，写入 tests/frontend/_rendered/。

Why 不用 TestClient 起 app：
    smoke.js 只需要纯静态 HTML 做结构断言；直接驱动模板环境比拉起整个
    FastAPI（torch/engines 导入）快一个数量级，且离线可用。

上下文约定：
    download_guide.html 的 `missing` 列表在路由层也未传入（渲染为空列表），
    页面的真实缺失模型清单由页面内 JS 动态 GET /api/model/download_hints 补齐，
    与线上行为一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent.parent
_APP_DIR = _TESTS_DIR.parent / "app"
_REPO_ROOT = _TESTS_DIR.parent
_TPL_DIR = _APP_DIR / "integrated_app" / "templates"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

_OUTPUT_DIR = _TESTS_DIR / "frontend" / "_rendered"

# 页面名 -> (模板名, 渲染上下文)
PAGES: dict[str, tuple[str, dict]] = {
    "download_guide.html": ("download_guide.html", {"lang": "zh-CN", "intercepted_path": ""}),
}


def main() -> int:
    # Why 不 import integrated_app.routes.tabs（尽管那里有现成的 templates）：
    # tabs 模块顶层拉起 fastapi / persona_manager / model_manager 整条重依赖链，
    # 而本脚本的设计意图（见模块 docstring）是"只驱动 Jinja2、离线快渲染"，
    # CI 的 Frontend Smoke 仅安装 jinja2+pyyaml。i18n 过滤器注册位于 i18n.py
    # （顶层零重依赖），自建最小环境即可与线上同构。
    # 2026-09-04 修复：CI Frontend Smoke Render pages 步骤 ModuleNotFoundError: fastapi。
    from jinja2 import Environment, FileSystemLoader

    from integrated_app.i18n import register_i18n_filters

    env = Environment(loader=FileSystemLoader(str(_TPL_DIR)))
    register_i18n_filters(env)

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for out_name, (template_name, ctx) in PAGES.items():
        try:
            html = env.get_template(template_name).render(**ctx)
            out_path = _OUTPUT_DIR / out_name
            out_path.write_text(html, encoding="utf-8")
            print(f"rendered {out_name} -> {out_path} ({len(html)} chars)")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL rendering {template_name}: {type(exc).__name__}: {exc}")
            return 1
    print(f"done: {ok}/{len(PAGES)} pages rendered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
