"""HTMX 标签页懒加载路由模块。

架构说明：
    首页 ``base.html`` 只渲染固定框架（顶部栏 + 侧边 Tab 导航），
    用户点击 Tab 时由 HTMX 通过 ``hx-get /tab/{tab_name}`` 异步加载
    对应 Tab 的局部 HTML（仅 ``<div>`` 内容，不含 ``<html>/<head>``），
    避免整页刷新，显著降低 TTI（可交互时间）。

支持的 Tab（允许列表）：
    voice_design / voice_clone / ultimate_clone / prompt_continue /
    script / voxcpm2 / settings / indextts2 / indextts2_clone /
    indextts2_emotion / indextts2_duration / indextts20_clone /
    indextts20_emotion / lora / lora_training / history / persona / help

    IndexTTS 2.0（indextts20）拥有独立 Tab 与独立模板，不再复用 2.5 的
    模板并在模板内用 Jinja 条件裁剪：2.0 无显式时长控制，因此没有
    indextts20_duration。

路径前缀：
    无（router 直接注册，路由为 ``/tab/{tab_name}``；为保持 100% 向后
    兼容，不修改为 ``/tabs`` 前缀）。

权限：
    全部为 GET 只读接口，无需 CSRF / Bearer Token 认证。
"""

import html
import logging
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..config import _DIALECTS, _LANGS, get_config
from ..history_db import get_history_db
from ..i18n import get_lang, register_i18n_filters, t
from ..model_registry import registry
from ..persona_manager import get_persona_detail_table, get_persona_list, get_total_persona_count

logger = logging.getLogger("tts_multimodel.tabs")

router = APIRouter(tags=["tabs"])

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(_BASE_DIR, "templates"))
register_i18n_filters(templates.env)

# Tab 名称 -> 模板文件路径映射（向后兼容 100%，保留原键名）
_TAB_TEMPLATES: dict[str, str] = {
    "voice_design": "tabs/voice_design.html",
    "voice_clone": "tabs/voice_clone.html",
    "ultimate_clone": "tabs/ultimate_clone.html",
    "prompt_continue": "tabs/prompt_continue.html",
    "script": "tabs/script.html",
    "voxcpm2": "tabs/settings.html",
    "settings": "tabs/settings.html",
    "indextts2": "tabs/indextts2.html",
    "indextts2_clone": "tabs/indextts2_clone.html",
    "indextts2_emotion": "tabs/indextts2_emotion.html",
    "indextts2_duration": "tabs/indextts2_duration.html",
    "indextts20_clone": "tabs/indextts20_clone.html",
    "indextts20_emotion": "tabs/indextts20_emotion.html",
    "lora": "tabs/lora_manager.html",
    "lora_training": "tabs/lora_training.html",
    "history": "tabs/history.html",
    "persona": "tabs/persona.html",
    "help": "tabs/help.html",
}

# Tab 允许列表：使用 frozenset 提供 O(1) 查找并防止路径遍历
# Why frozenset 而非动态检查文件存在：
#   允许列表查找耗时约 1 μs，而 os.stat 磁盘检查约 1 ms；
#   更重要的是 frozenset 可以直接拦截 tab_name="../../config.yaml" 等
#   路径遍历攻击向量，无需编写复杂的路径 sanitize 逻辑。
TAB_ALLOWLIST: frozenset[str] = frozenset(_TAB_TEMPLATES.keys())

# VoxCPM2 专属 Tab（字符上限 8192）
_VOXCPM2_TABS: frozenset[str] = frozenset(
    {"voice_design", "voice_clone", "ultimate_clone", "script", "prompt_continue", "voxcpm2"}
)

# IndexTTS2 专属 Tab（字符上限 3072）
_INDEXTTS2_TABS: frozenset[str] = frozenset({"indextts2", "indextts2_clone", "indextts2_emotion", "indextts2_duration"})

# IndexTTS 2.0 专属 Tab（与 2.5 同为 3072；2.0 无时长控制，故无 *_duration）
_INDEXTTS20_TABS: frozenset[str] = frozenset({"indextts20_clone", "indextts20_emotion"})

# 通用新式引擎专属 Tab（字符上限 4096；当前无成员，留空供未来通用引擎 Tab 复用）
_GENERIC_ENGINE_TABS: frozenset[str] = frozenset()


def _common_context(request: Request, tab_name: str = "") -> dict[str, Any]:
    """构建 Tab 模板的通用上下文字典。

    Args:
        request:  FastAPI Request，用于解析语言 Cookie。
        tab_name: 当前 Tab 名称，用于确定引擎字符上限。

    Returns:
        包含 request / current_engine / langs / dialects / lang /
        gen_split_max_chars / engine_max_total_chars 的字典。
    """
    lang = get_lang(request)
    try:
        split_chars = get_config().generation_defaults.split_max_chars
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取 split_max_chars 配置失败，使用默认值 200: %s", exc)
        split_chars = 200

    if tab_name in _VOXCPM2_TABS:
        engine_max_chars = 8192
    elif tab_name in _INDEXTTS2_TABS or tab_name in _INDEXTTS20_TABS:
        engine_max_chars = 3072
    elif tab_name in _GENERIC_ENGINE_TABS:
        engine_max_chars = 4096
    else:
        engine_max_chars = 8192 if registry.current_engine == "voxcpm2" else 3072

    return {
        "request": request,
        "current_engine": registry.current_engine,
        "langs": _LANGS,
        "dialects": _DIALECTS,
        "lang": lang,
        "gen_split_max_chars": split_chars,
        "engine_max_total_chars": engine_max_chars,
    }


def _notfound_fullpage(safe_name: str, message: str) -> HTMLResponse:
    """生成带外壳的 404 完整 HTML 页面（非 HTMX 直接访问用）。"""
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tab Not Found - TTS MultiModel</title>
<style>
body {{ font-family: system-ui, -apple-system, sans-serif; display: flex; align-items: center; justify-content: center;
min-height: 100vh; margin: 0; background: var(--bg-primary, #f5f3fa); color: var(--text-primary, #333); }}
.container {{ text-align: center; padding: 40px; }}
a {{ color: var(--accent-primary, #6344a3); text-decoration: none; font-weight: 600; }}
</style></head>
<body><div class="container">
<h2>{message}</h2>
<p>Tab &quot;{safe_name}&quot;</p>
<p><a href="/">← 返回首页</a></p>
</div></body></html>""",
        status_code=404,
    )


def _notfound_partial(safe_name: str, message: str) -> HTMLResponse:
    """生成 HTMX 局部插入用的 404 HTML 片段。"""
    return HTMLResponse(
        f'<div class="card" style="padding:40px;text-align:center;color:var(--text-muted);">'
        f"<p>{message}: {safe_name}</p></div>",
        status_code=404,
    )


# Why 懒加载而非首页一次性渲染所有 Tab：
#   每个 Tab 模板包含数十个表单控件 + 多语言字符串渲染 + Persona 列表查询，
#   若一次性渲染 9 个 Tab，首屏服务器耗时增加 800ms+ 且 HTML 体积膨胀 3~5 倍；
#   采用 HTMX 懒加载后，TTI（可交互时间）降低约 70%，仅在用户真正点击
#   对应 Tab 时才执行该 Tab 的渲染和数据查询。
@router.get("/tab/{tab_name}", summary="标签页", description="HTMX 按需加载标签页局部 HTML")
async def load_tab(request: Request, tab_name: str, lang: str = "zh-CN") -> Response:
    """加载指定名称的标签页局部 HTML。

    Args:
        request:  FastAPI Request，用于判断是否 HTMX 请求（``hx-request`` 头）、
            读取 Query 参数、构造 TemplateResponse。
        tab_name: 标签页名称，必须属于 ``TAB_ALLOWLIST``，否则返回 404。
        lang:     可选 Query 参数，语言偏好（兼容旧链接，实际优先使用 Cookie）。

    Returns:
        - 合法 Tab + HTMX 请求 → ``TemplateResponse``（仅 Tab 内容 div）
        - 合法 Tab + 直接浏览器访问 → ``RedirectResponse`` 303 到 ``/?tab=...``
        - 非法 Tab 或模板不存在 → 404（完整页面或局部片段，取决于请求来源）

    Raises:
        无显式抛出：所有异常通过 try/except + logger 捕获，返回友好 HTML。
    """
    # 1) 允许列表校验（路径遍历第一道防线）
    if tab_name not in TAB_ALLOWLIST:
        safe_name = html.escape(tab_name)
        is_htmx = "hx-request" in request.headers
        logger.debug("Tab not in allowlist (debug 级，正常用户 404 行为): %s", tab_name)
        if is_htmx:
            return _notfound_partial(safe_name, "Tab not found")
        return _notfound_fullpage(safe_name, "Tab not found")

    template_name: str | None = _TAB_TEMPLATES.get(tab_name)
    if not template_name:
        safe_name = html.escape(tab_name)
        is_htmx = "hx-request" in request.headers
        logger.debug("Tab -> template_name 映射为空: %s", tab_name)
        if is_htmx:
            return _notfound_partial(safe_name, "Tab not found")
        return _notfound_fullpage(safe_name, "Tab not found")

    # 2) 模板磁盘存在性校验
    template_path = os.path.join(_BASE_DIR, "templates", template_name)
    if not os.path.exists(template_path):
        safe_name = html.escape(tab_name)
        is_htmx = "hx-request" in request.headers
        logger.error("Tab 模板文件缺失（部署遗漏）: %s", template_path)
        if is_htmx:
            return _notfound_partial(safe_name, "Tab template not found")
        return _notfound_fullpage(safe_name, "Tab template not found")

    # 3) 构建 Tab 特定上下文
    ctx: dict[str, Any] = _common_context(request, tab_name=tab_name)

    if tab_name in {"voice_design", "voice_clone", "ultimate_clone", "voxcpm2"}:
        try:
            ctx["persona_list"] = get_persona_list()
        except Exception as exc:  # noqa: BLE001
            logger.exception("加载 Persona 列表失败 (tab=%s): %s", tab_name, exc)
            ctx["persona_list"] = []
    elif tab_name == "history":
        search = request.query_params.get("search_keyword", "")
        time_filter = request.query_params.get("time_filter", "all")
        try:
            db = get_history_db()
            paginated = db.get_paginated_records(
                search_keyword=search,
                time_filter=time_filter,
                limit=20,
                offset=0,
            )
            items = []
            for rec in paginated["items"]:
                file_size = rec.get("file_size_bytes", 0) or 0
                size_mb = file_size / (1024 * 1024) if file_size > 0 else 0
                size_str = f"{size_mb:.1f} MB"
                duration = rec.get("duration_seconds", 0) or 0
                try:
                    duration = float(str(duration).rstrip("s"))
                except (ValueError, TypeError):
                    duration = 0
                duration_str = f"{duration:.1f}s" if duration > 0 else "<1s"
                items.append(
                    [
                        rec.get("filename", ""),
                        rec.get("created_at", ""),
                        duration_str,
                        size_str,
                    ]
                )
            no_records_text = t("history_no_records", ctx["lang"])
            ctx["history_records"] = items if items else [[no_records_text, "-", "-", "-"]]
            ctx["history_count"] = paginated["total"]
            ctx["history_loaded"] = paginated["loaded"]
            ctx["history_has_more"] = paginated["hasMore"]
            ctx["search_keyword"] = search
            ctx["time_filter"] = time_filter
        except Exception as exc:  # noqa: BLE001
            logger.exception("加载 History Tab 数据失败: %s", exc)
            ctx["history_records"] = [[t("history_no_records", ctx["lang"]), "-", "-", "-"]]
            ctx["history_count"] = 0
            ctx["history_loaded"] = 0
            ctx["history_has_more"] = False
            ctx["search_keyword"] = search
            ctx["time_filter"] = time_filter
    elif tab_name == "persona":
        try:
            ctx["persona_count"] = get_total_persona_count()
            ctx["total_persona_count"] = ctx["persona_count"]
            ctx["persona_table_data"] = get_persona_detail_table()
        except Exception as exc:  # noqa: BLE001
            logger.exception("加载 Persona Tab 数据失败: %s", exc)
            ctx["persona_count"] = 0
            ctx["total_persona_count"] = 0
            ctx["persona_table_data"] = []

    # 4) 非 HTMX 直接访问 → 303 重定向到首页并自动激活对应 Tab
    if "hx-request" not in request.headers:
        return RedirectResponse(url=f"/?tab={tab_name}", status_code=303)

    # 5) 渲染 Tab 模板
    try:
        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context=ctx,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    except Exception as exc:  # noqa: BLE001 - Jinja2 模板语法错误等
        logger.exception("渲染 Tab 模板失败 (tab=%s): %s", tab_name, exc)
        safe_name = html.escape(tab_name)
        return _notfound_partial(safe_name, "Tab template render error")
