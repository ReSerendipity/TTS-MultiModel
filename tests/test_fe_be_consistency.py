"""前后端一致性守卫（对应 docs/reports/前后端功能一致性检查报告_20260904.md §7）。

十条守卫分别拦截报告中不同层面的静默失效：

1. ``test_all_hx_attributes_point_at_registered_routes`` —— URL 层（htmx）：
   模板中所有 hx-get/hx-post/hx-put/hx-delete 目标必须已注册（现有
   test_routes_htmx 只查 tabs 目录的 hx-post，A1/A2 类 405 漏网）。
2. ``test_form_fields_accepted_by_target_endpoints`` —— 字段层：
   每个表单提交的字段名必须至少被该表单某个提交目标端点声明
   （B2 滑杆缺 name 之外的另一面：控件发字段而后端不读 = 静默忽略）。
3. ``test_range_and_file_controls_have_name`` —— name 层：
   type=range/file 控件必须携带 name（B2 的直接形态：只有 id 没有 name
   导致 8 维情感向量整体静默失效）。
4. ``test_openai_model_map_subset_of_declared_engines`` —— 配置层：
   /v1/audio/speech 的模型→引擎映射必须 ⊆ config.yaml 声明引擎集。
5. ``test_frontend_url_literals_point_at_registered_routes`` —— URL 层（JS）：
   templates/ 与 static/js/ 里所有 ``'/xxx'`` 形式的 URL 字面量必须有后端
   承接。WHY 单列：守卫 1 只扫 hx-* 属性，**不覆盖 fetch() / new Audio() /
   内联 JS 里的 URL 字面量**——流式生成结果卡的 ``/output/`` 幽灵路径正是
   从这个缺口漏出去的（GOTCHAS #86）。
6. ``test_streaming_templates_use_shared_result_wiring`` —— 生成后接线层：
   每个自带 SSE 流式解析器的模板都必须调用 ``window.wireGenerationResult()``。
   WHY 单列：生成结果有 htmx 与 SSE 两条渲染路径，后者手动 innerHTML 注入、
   **不派发任何 htmx 事件**，挂在 htmx 监听上的接线逻辑它必然拿不到——流式
   分支因此漏掉「显示后处理区 / 回填保存表单文件名」，静默失效（GOTCHAS #91）。
7. ``test_unsafe_manual_fetches_carry_csrf_token`` —— CSRF 头层：
   模板与 static/js 里所有以 POST/PUT/PATCH/DELETE 发的**手写 fetch** 必须能拿到
   ``X-CSRF-Token``。WHY 单列：htmx 请求由 base.html 的 ``htmx:configRequest`` 统一
   注入，手写 fetch 没有这层便利；漏掉就是「按钮点了没反应」+ 403，且中间件回的是
   给开发者看的英文码。本次一次查出 4 处活路径（音色删除、历史恢复显示×2、命令面板
   卸载模型）与 3 处未接线的设置保存函数（GOTCHAS #130）。
8. ``test_sidebar_tabs_post_to_their_own_engine`` + ``test_programmatic_tab_jumps_use_goto_tab``
   —— 引擎归属层：每个侧栏功能页的表单必须打到**自己那个引擎**的生成端点，且
   程序化跳转必须用会真正拉内容的 ``gotoTab``。WHY 单列：真机上出现过「切到
   IndexTTS 2.5 后屏幕上还是 VoxCPM2 的表单」，点生成 → 400，而模板与后端各自都
   是对的 —— 错在"高亮与内容分家"这一层，静态扫描 + 调用点约束一起才封死（#131）。
9. ``test_inline_scripts_parse_as_javascript`` —— 语法层：模板里的每个内联
   ``<script>`` 必须能被 ``node --check`` 解析（Jinja 先占位化）。一处语法错会让
   整页 JS 静默全废 —— 没有错误块、没有进度条，用户只会看到"点了没反应"。
10. ``test_inline_event_handlers_are_defined`` —— 死按钮层：``onclick="foo()"`` 里的
    ``foo`` 必须真的在前端某处定义过。属性里调不存在的函数**不报错**，只在按下那一刻
    静默无反应，日志与常规测试都看不见（本次抓出「重试」按钮，#133）。已知欠债走
    ``_KNOWN_DEAD_HANDLERS`` 显式记账，并有 ``test_known_dead_handlers_list_only_shrinks`` 防膨胀。

维护约定（参 KNOWN_GOTCHAS #36）：这些守卫都做过变异测试——
把任一模板的端点/字段/URL 故意改错，对应测试必须变红；否则说明断言写成了
永真。守卫 5 另配 ``test_url_literal_guard_is_not_vacuous`` 做恒真自证；
守卫 6 的「函数语义」一面由 ``tests/frontend/wire_generation_result.js``
（jsdom）承担，两者互补：本文件管**调用点**，jsdom 测试管**函数对不对**。
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parent.parent / "app" / "integrated_app"
_TABS_DIR = _APP / "templates" / "tabs"
_TPL_DIR = _APP / "templates"

#: 已知「模板引用了但后端未实现」的端点欠债清单（与 test_routes_htmx 同一约定：
#: 新增欠债必须同时在此登记并在 KNOWN_GOTCHAS 留痕，且 test_known_debt_list_
#: only_shrinks 防止清单膨胀掩盖回归）。
_KNOWN_UNIMPLEMENTED_ENDPOINTS: frozenset[str] = frozenset()


def _registered(client) -> set[str]:
    return set(client.get("/openapi.json").json()["paths"])


def _path_is_served(client, url: str) -> bool:
    """openapi 之外的第二仲裁：这条路后端到底接不接得住。

    WHY 需要：``include_in_schema=False`` 的路由（``/api/system/logs-compat``、
    ``routes/pages.py`` 的 4 条）**注册且可访问，却不在 schema 里**；而 ``app.routes``
    也拿不全（路由是 lifespan 里按 ``hasattr(mod,'router')`` 挂进来的 ``_IncludedRouter``，
    顶层只有 26 条、下钻不到子路由）。用 ``OPTIONS`` 探：命中路由但方法不允许 → 405，
    路径不存在 → 404，且不会触发任何 handler 副作用。
    """
    try:
        return client.options(url).status_code != 404
    except Exception:  # noqa: BLE001 — 探测失败就当"未能证实存在"，交回原判定
        return False


# ---------------------------------------------------------------------------
# 守卫 1：URL 层
# ---------------------------------------------------------------------------


def test_all_hx_attributes_point_at_registered_routes(client):
    """templates/ 全目录（含子模板）的 hx-* 目标必须是真实注册的路由。"""
    registered = _registered(client)
    pattern = re.compile(r'hx-(get|post|put|delete)="([^"{}]+)"')
    offenders: list[str] = []
    checked = 0
    for tpl in sorted(_TPL_DIR.rglob("*.html")):
        for method, target in pattern.findall(tpl.read_text(encoding="utf-8")):
            checked += 1
            if target in registered or target in _KNOWN_UNIMPLEMENTED_ENDPOINTS or _path_is_served(client, target):
                continue
            offenders.append(f"{tpl.name} hx-{method} -> {target}")
    assert checked > 0, "未找到任何 hx-* 引用，断言可能已失效"
    assert offenders == [], f"模板引用了未注册的端点：{offenders}"


# ---------------------------------------------------------------------------
# 守卫 2：字段层（表单 name vs 端点 Form 参数）
# ---------------------------------------------------------------------------

_INPUT_RE = re.compile(r"<(?:input|select|textarea)\b[^>]*>", re.I)
_NAME_RE = re.compile(r'name\s*=\s*"([^"]+)"')
_FORM_RE = re.compile(r"<form\b[^>]*>", re.I)
_HX_TARGET_RE = re.compile(r'hx-(?:post|put|patch)="(/api[^"{}]+)"')

#: 纯前端状态字段豁免：radio 的互斥分组必须依赖 name（浏览器内建行为），
#: 该字段仅作 UI 状态切换、无后端语义（报告 B 类配对 7 判定「字段本身被
#: 忽略可接受」）。若未来要在后端消费，请从本清单移除并补齐端点参数。
_FRONTEND_ONLY_FIELDS: frozenset[str] = frozenset({"duration_mode"})


def _endpoint_accepted_fields(spec: dict, path: str) -> set[str]:
    """取端点声明的全部入参名（query 参数 + 表单/文件 body 字段）。"""
    accepted: set[str] = set()
    op = spec["paths"].get(path, {}).get("post") or spec["paths"].get(path, {}).get("put")
    if op is None:
        return accepted
    for p in op.get("parameters", []):
        accepted.add(p.get("name", ""))
    for _ct, v in op.get("requestBody", {}).get("content", {}).items():
        schema = v.get("schema", {})
        ref = schema.get("$ref", "")
        if ref:
            schema = spec["components"]["schemas"][ref.split("/")[-1]]
        accepted.update(schema.get("properties", {}).keys())
    return accepted


def test_form_fields_accepted_by_target_endpoints(client):
    """每个 <form> 的字段名必须被其提交目标端点（并集）声明，否则为静默忽略字段。

    WHY 用「表单内全部提交目标的并集」而非逐按钮精确匹配：克隆/极致页的
    「保存音色」按钮与生成按钮共用同一 <form>（htmx 按钮级 hx-post 覆盖表单
    action，但提交的都是整个表单的字段）。并集语义足以拦住 B2/B4/B8/B9 类
    「发了但没人读」的死字段，静态分析无法做到更精确的按按钮划分。
    """
    spec = client.get("/openapi.json").json()
    offenders: list[str] = []
    checked = 0
    for tpl in sorted((_TPL_DIR / "tabs").glob("*.html")):
        text = tpl.read_text(encoding="utf-8")
        for fm in _FORM_RE.finditer(text):
            end = text.find("</form>", fm.start())
            block = text[fm.start() : end if end != -1 else len(text)]
            targets = set(_HX_TARGET_RE.findall(block))
            if not targets:
                continue
            checked += 1
            accepted: set[str] = set()
            for t in targets:
                accepted |= _endpoint_accepted_fields(spec, t)
            fields = {m.group(1) for im in _INPUT_RE.finditer(block) if (m := _NAME_RE.search(im.group(0)))}
            unknown = sorted(f for f in fields if f and f not in accepted and f not in _FRONTEND_ONLY_FIELDS)
            if unknown:
                offenders.append(f"{tpl.name}: {unknown} (端点未声明)")
    assert checked >= 10, f"仅扫描到 {checked} 个表单，断言可能已失效"
    assert offenders == [], f"表单存在后端未声明的静默忽略字段：{offenders}"


# ---------------------------------------------------------------------------
# 守卫 3：name 层（range/file 控件必须有 name）
# ---------------------------------------------------------------------------


def test_range_and_file_controls_have_name(client):
    """tabs 模板中 type=range / type=file 控件必须带 name 属性。

    WHY：B2 的直接形态——indextts2.html 的 12 个情感滑杆/上传控件只有 id
    没有 name，浏览器根本不提交，8 维情感向量与情感音频模式整体静默失效，
    无任何报错。decorative 控件（确无可提交语义）必须显式加
    ``data-no-submit`` 并在下面排除，禁止静默裸奔。
    """
    pattern = re.compile(r'<input[^>]*type="(range|file)"[^>]*>', re.I)
    offenders: list[str] = []
    checked = 0
    for tpl in sorted(_TABS_DIR.glob("*.html")):
        for m in pattern.finditer(tpl.read_text(encoding="utf-8")):
            tag = m.group(0)
            checked += 1
            if not _NAME_RE.search(tag) and "data-no-submit" not in tag:
                offenders.append(f"{tpl.name}: {tag[:80]}")
    assert checked >= 15, f"仅扫描到 {checked} 个 range/file 控件，断言可能已失效"
    assert offenders == [], f"range/file 控件缺少 name 属性（永不提交）：{offenders}"


# ---------------------------------------------------------------------------
# 守卫 4：OpenAI 兼容端点（/v1/audio/speech）模型→引擎映射 ⊆ 声明引擎
# ---------------------------------------------------------------------------

_OPENAI_ENGINE_MAP_RE = re.compile(r"_MODEL_ENGINE_MAP\s*(?:\([^)]*\))?[^=]*=\s*\{(.*?)\}", re.S)


def test_openai_model_map_subset_of_declared_engines():
    """P2-6：/v1/audio/speech 的模型→引擎映射必须 ⊆ config.yaml 声明引擎集。

    静态分析 openai_api.py 的 ``_MODEL_ENGINE_MAP`` 字面量，与 config.yaml
    ``models.engines`` 声明集比对。WHY 不使用 engine_registry（会触发懒导入
    torch 链）：配置层声明是引擎的单一事实来源，映射引用了未声明的引擎
    （如未来新增 ``tts-2 -> voicebox`` 而 voicebox 不在 config 声明）即视为
    失配——与 check_engine_specs.py 的「config ↔ registry」三向一致性互为补充。
    """
    import yaml

    root = _APP.parent.parent
    cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    declared = set((cfg.get("models") or {}).get("engines") or {})
    src = (_APP / "openai_api.py").read_text(encoding="utf-8")
    block = _OPENAI_ENGINE_MAP_RE.search(src)
    assert block is not None, "openai_api.py 中未找到 _MODEL_ENGINE_MAP 定义，断言可能已失效"
    mapped = set(re.findall(r':\s*"([^"]+)"', block.group(1)))
    assert mapped, "未解析到任何引擎映射值，断言可能已失效（请检查映射书写格式）"
    unknown = sorted(mapped - declared)
    assert not unknown, f"OpenAI 模型映射引用了 config.yaml 未声明的引擎: {unknown}"


# ---------------------------------------------------------------------------
# 守卫 5：前端 URL 字面量（fetch / JS 字符串拼接）⊆ 已注册路由
# ---------------------------------------------------------------------------

#: 以 "/" 开头且紧跟字母的字符串字面量（排除 SVG 闭合标签 "/>"）
_FE_URL_RE = re.compile(r"""['"`](/[A-Za-z][^'"`\s]*)['"`]""")

#: 静态资源扩展名——归 /static 挂载管，不属于 API 路由
_FE_ASSET_SUFFIX_RE = re.compile(r"\.(?:js|css|png|jpe?g|svg|ico|woff2?|ttf|map)$", re.I)


def _url_matchers(registered: set[str]) -> list[tuple[str, re.Pattern[str]]]:
    """把 ``/api/audio/{filename}`` 这类路由模板编译成正则匹配器。"""
    matchers: list[tuple[str, re.Pattern[str]]] = []
    for path in sorted(registered):
        parts = re.split(r"\{[^}]+\}", path)
        matchers.append((path, re.compile("^" + "[^/]+".join(re.escape(p) for p in parts) + "$")))
    return matchers


def _frontend_url_is_registered(url: str, registered: set[str], matchers: list[tuple[str, re.Pattern[str]]]) -> bool:
    """判定前端 URL 字面量是否有后端路由承接。

    三种合法形态：① 精确命中；② 命中 ``{param}`` 模板；③ 以 ``/`` 结尾的
    前缀拼接（如 ``'/api/audio/' + filename``）命中某条注册路由的前缀。
    """
    if url in registered:
        return True
    if any(rx.match(url) for _p, rx in matchers):
        return True
    if url.endswith("/"):
        if any(p.startswith(url) and len(p) > len(url) for p in registered):
            return True
        if url.startswith("/static"):
            return True
    return False


def test_frontend_url_literals_point_at_registered_routes(client):
    """templates/ 与 static/js/ 里所有 ``'/xxx'`` 形式的 URL 必须有后端承接。

    WHY 单列这条守卫：既有的 ``test_all_hx_attributes_point_at_registered_routes``
    只扫 hx-* 属性，**完全不覆盖 fetch() / new Audio() / 内联 JS 里的 URL 字面量**。
    流式生成的结果卡正是从这个缺口漏出去的——``EmbeddedPlayer.html('/output/' +
    filename)`` 里的 ``/output/`` 从未注册过任何路由或静态挂载，生成明明成功
    （wav 已落盘、done 事件正常），用户却看到播放器点不动 + 服务端 404 刷屏
    （GOTCHAS #86）。这类「前端凭直觉写了个不存在的路径」必须在静态层拦死。
    """
    registered = _registered(client)
    matchers = _url_matchers(registered)
    offenders: list[str] = []
    checked = 0

    targets = sorted((_TPL_DIR).rglob("*.html")) + sorted((_APP / "static" / "js").rglob("*.js"))
    for path in targets:
        rel = path.relative_to(_APP).as_posix()
        if "vendor" in rel or path.name.endswith(".min.js"):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            for m in _FE_URL_RE.finditer(line):
                url = m.group(1).split("?")[0].split("#")[0]
                if not url or url.startswith("//"):
                    continue
                # Jinja 插值片段无法静态判定（与守卫 1 同一豁免口径）
                if "{" in url or "}" in url:
                    continue
                if _FE_ASSET_SUFFIX_RE.search(url):
                    continue
                checked += 1
                if _frontend_url_is_registered(url, registered, matchers) or _path_is_served(client, url):
                    continue
                offenders.append(f"{rel}:{lineno} -> {url}")

    assert checked >= 50, f"仅扫描到 {checked} 个前端 URL 字面量，断言可能已失效"
    assert offenders == [], f"前端引用了后端未注册的路径：{offenders}"


def test_off_schema_route_oracle_is_not_vacuous(client):
    """OPTIONS 兜底必须"只放行真存在的路由"，否则它会变成新的永真豁免。"""
    assert _path_is_served(client, "/api/system/logs-compat") is True  # 注册但不在 schema
    assert _path_is_served(client, "/api/no-such-route-anywhere") is False
    assert "/api/system/logs-compat" not in _registered(client), "它确实不在 openapi 里，所以才需要兜底"


def test_url_literal_guard_is_not_vacuous():
    """非永真证明：守卫必须能识别本次的幽灵路径形态（变异测试）。

    若有人把 ``_frontend_url_is_registered`` 改成恒 True，本测试立刻变红。
    """
    registered = {"/api/audio/{filename}", "/api/health/ping"}
    matchers = _url_matchers(registered)
    # 幽灵路径必须判负（本次 bug 的形态）
    assert not _frontend_url_is_registered("/output/streaming_1789389036.wav", registered, matchers)
    assert not _frontend_url_is_registered("/api/audio", registered, matchers)  # 缺尾斜杠 ≠ 前缀拼接
    # 三种合法形态必须判正
    assert _frontend_url_is_registered("/api/audio/streaming_1.wav", registered, matchers)  # {param} 模板
    assert _frontend_url_is_registered("/api/audio/", registered, matchers)  # 前缀拼接
    assert _frontend_url_is_registered("/api/health/ping", registered, matchers)  # 精确命中


# ---------------------------------------------------------------------------
# 守卫 6：生成后接线必须走统一入口（htmx 与 SSE 流式两条路径共用）
# ---------------------------------------------------------------------------


_APP_INIT_JS = _APP / "static" / "js" / "app_init.js"
# SSE 流式解析器的识别特征：两者同时出现才说明该模板自带流式结果渲染路径
_STREAM_MARKERS = ("currentEvent", "case 'audio'")
_WIRING_FN = "wireGenerationResult"


def _streaming_templates() -> list[Path]:
    """找出所有自带 SSE 流式解析器的模板。"""
    found: list[Path] = []
    for path in sorted(_TPL_DIR.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        if all(marker in text for marker in _STREAM_MARKERS):
            found.append(path)
    return found


def test_streaming_templates_use_shared_result_wiring():
    """流式生成结果必须走统一的「生成后接线」入口。

    WHY：htmx 路径与 SSE 流式路径共用 ``window.wireGenerationResult()`` 完成
    「显示后处理折叠区 + 回填保存表单的结果文件名」。2026-09-14 发现两个流式分支
    各自手写且各漏一项——voice_clone 漏显示后处理区（后处理永不出现）、
    voice_design 漏回填 vd-result-audio（点「保存为音色」被后端判为"缺少音频"）。
    本守卫要求：① 统一入口存在；② 每个流式模板都调用它（GOTCHAS #91）。
    """
    src = _APP_INIT_JS.read_text(encoding="utf-8")
    assert f"window.{_WIRING_FN} = function" in src, (
        f"{_APP_INIT_JS.name} 未定义 window.{_WIRING_FN}（生成后接线的统一入口）"
    )

    templates = _streaming_templates()
    assert len(templates) >= 2, f"仅发现 {len(templates)} 个流式模板，扫描规则可能已失效"
    missing = [
        str(path.relative_to(_TPL_DIR)) for path in templates if _WIRING_FN not in path.read_text(encoding="utf-8")
    ]
    assert missing == [], f"以下流式模板未调用 {_WIRING_FN}()，流式生成完成后后处理/保存为音色会静默失效：{missing}"


# ---------------------------------------------------------------------------
# 守卫 7：手写 fetch 的非安全方法必须带 X-CSRF-Token
# ---------------------------------------------------------------------------

#: 匹配 ``fetch( ... )``，允许一层嵌套括号（URL 里常有 encodeURIComponent(...)）
_FETCH_CALL_RE = re.compile(r"fetch\(\s*(?:[^()]|\([^()]*\))*\)", re.S)
_UNSAFE_METHOD_RE = re.compile(r"method\s*:\s*['\"](POST|PUT|PATCH|DELETE)['\"]", re.I)
_HEADERS_IDENT_RE = re.compile(r"headers\s*:\s*([A-Za-z_$][\w$]*)\s*[,}]")
_HEADERS_CALL_RE = re.compile(r"headers\s*:\s*([A-Za-z_$][\w$]*)\s*\(")
_CSRF_TOKEN_RE = re.compile(r"""['"]X-CSRF-Token['"]""")
#: 判定"这个 headers 变量有没有塞 token"时向前看的行数（同一函数体内的赋值）
_CSRF_LOOKBACK_LINES = 60


def _csrf_findings_in(text: str) -> list[tuple[int, str]]:
    """找出「非安全方法的 fetch 拿不到 X-CSRF-Token」的位置。

    CSRF 中间件（``middleware/csrf.py``）对 GET/HEAD/OPTIONS 之外的方法一律要求
    cookie + header 双提交，缺 header 直接 403。htmx 请求由 ``base.html`` 的
    ``htmx:configRequest`` 统一注入，所以只有**手写 fetch** 会漏 —— 漏了就是
    「按钮点了没反应」，且失败信息是给开发者看的英文码。
    """
    lines = text.splitlines()
    findings: list[tuple[int, str]] = []
    for match in _FETCH_CALL_RE.finditer(text):
        call = match.group(0)
        if not _UNSAFE_METHOD_RE.search(call):
            continue
        if _CSRF_TOKEN_RE.search(call):
            continue
        lineno = text[: match.start()].count("\n") + 1
        helper = _HEADERS_CALL_RE.search(call)
        if helper:
            # headers 由本文件的某个函数造出来：顺着函数定义查它有没有注入 token
            fn_name = helper.group(1)
            body = re.search(rf"function\s+{re.escape(fn_name)}\s*\((?:[^()]|\([^()]*\))*\)\s*\{{", text)
            if body and _CSRF_TOKEN_RE.search(text[body.start() : body.start() + 600]):
                continue
            findings.append((lineno, f"headers 由 {fn_name}() 构造，但该函数没有注入 X-CSRF-Token"))
            continue
        ident = _HEADERS_IDENT_RE.search(call)
        if ident:
            start = max(0, lineno - 1 - _CSRF_LOOKBACK_LINES)
            context = "\n".join(lines[start:lineno])
            name = ident.group(1)
            if _CSRF_TOKEN_RE.search(context) and re.search(rf"\b{re.escape(name)}\b\s*=", context):
                continue
            findings.append((lineno, f"headers 变量 {name} 在上方 {_CSRF_LOOKBACK_LINES} 行内没有注入 X-CSRF-Token"))
        else:
            findings.append((lineno, "fetch 调用完全没有 headers"))
    return findings


def test_unsafe_manual_fetches_carry_csrf_token():
    """模板与 static/js 里每个非安全方法的手写 fetch 都必须带 CSRF 头。"""
    targets = sorted(_TPL_DIR.rglob("*.html")) + sorted((_APP / "static" / "js").rglob("*.js"))
    offenders: list[str] = []
    checked = 0
    for path in targets:
        rel = path.relative_to(_APP).as_posix()
        if "vendor" in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _FETCH_CALL_RE.finditer(text):
            if not _UNSAFE_METHOD_RE.search(match.group(0)):
                continue
            checked += 1
        for lineno, reason in _csrf_findings_in(text):
            offenders.append(f"{rel}:{lineno} {reason}")

    assert checked >= 20, f"仅扫到 {checked} 处非安全 fetch，扫描规则可能已失配"
    assert offenders == [], (
        f"以下手写 fetch 用 POST/PUT/PATCH/DELETE 但拿不到 X-CSRF-Token，点击会被 CSRF 中间件 403 拦掉：{offenders}"
    )


def test_csrf_fetch_guard_is_not_vacuous():
    """变异自证：三种真实形态必须分别判负/判正。"""
    assert _csrf_findings_in("fetch('/api/x', { method: 'DELETE' })"), "完全没 headers 必须判负"
    assert _csrf_findings_in("fetch('/api/x', { method: 'POST', headers: { 'Content-Type': 'application/json' } })")
    assert _csrf_findings_in(
        "var h = { 'Content-Type': 'application/json' };\nfetch('/api/x', { method: 'POST', headers: h })"
    )
    # 合规：调用里直接写、或在上文给变量注入过
    assert not _csrf_findings_in("fetch('/api/x', { method: 'POST', headers: { 'X-CSRF-Token': t } })")
    assert not _csrf_findings_in(
        "var h = { 'Content-Type': 'application/json' };\n"
        "if (t) h['X-CSRF-Token'] = t;\n"
        "fetch('/api/x', { method: 'POST', headers: h })"
    )
    # GET 不受 CSRF 约束，不得报
    assert not _csrf_findings_in("fetch('/api/x', { method: 'GET' })")
    # headers 由 helper 构造：helper 里没注入就判负，注入了才判正
    assert _csrf_findings_in(
        "function h() { return { 'Content-Type': 'application/json' }; }\n"
        "fetch('/api/x', { method: 'POST', headers: h() })"
    )
    assert not _csrf_findings_in(
        "function h() { var o = {}; o['X-CSRF-Token'] = t; return o; }\n"
        "fetch('/api/x', { method: 'POST', headers: h() })"
    )


# ---------------------------------------------------------------------------
# 守卫 8：每个侧栏功能页的表单，必须打到自己那个引擎的生成端点
# ---------------------------------------------------------------------------

#: 侧栏项 → 该引擎专属的生成端点前缀。工具页（data-model="all"）不在此列，
#: 它们没有引擎绑定的生成表单。indextts2 与 indextts20 共用同一个生成端点，
#: 靠请求里的版本参数区分，所以两个 model 允许同一前缀。
_TAB_ENGINE_PREFIXES: dict[str, tuple[str, ...]] = {
    "voxcpm2": ("/api/generate/voxcpm",),
    "indextts2": ("/api/generate/indextts2",),
    "indextts20": ("/api/generate/indextts2",),
}
_SIDEBAR_ITEM_RE = re.compile(r"<button\b[^>]*class=\"[^\"]*sidebar-item[^\"]*\"[^>]*>", re.S)
_HX_POST_RE = re.compile(r'hx-post="(/api/generate/[^"{]+)"')


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf'{name}="([^"]+)"', tag)
    return m.group(1) if m else None


def _tab_engine_offenders(tab: str, model: str, tpl_html: str) -> tuple[int, list[str]]:
    """返回 (该页里的生成表单数, 打错引擎的违例)。"""
    endpoints = _HX_POST_RE.findall(tpl_html)
    bad = [
        f"{tab}: 表单打到 {e}，但该页属于 {model}" for e in endpoints if not e.startswith(_TAB_ENGINE_PREFIXES[model])
    ]
    return len(endpoints), bad


#: 唯一合法的 activateTab 形态：作为 onclick 处理器、参数是按钮自身（此时 htmx 的
#: click 触发器会真的拉内容）。传 tab 名或从 JS 里调 = 只挪高亮，必须改用 gotoTab。
_LEGAL_ACTIVATE_RE = re.compile(r"\.activateTab\(\s*this\s*\)")
_ANY_ACTIVATE_RE = re.compile(r"\.activateTab\(")


def _illegal_activate_tab_calls(text: str) -> list[int]:
    """列出「只改高亮不改内容」的 activateTab 调用行号。"""
    bad: list[int] = []
    for m in _ANY_ACTIVATE_RE.finditer(text):
        if _LEGAL_ACTIVATE_RE.match(text, m.start()):
            continue
        bad.append(text[: m.start()].count("\n") + 1)
    return bad


def test_sidebar_tabs_post_to_their_own_engine():
    """「3 引擎 × 各功能页」矩阵的静态面：模板本身必须接线正确。

    WHY 单列：真机上出现过「侧栏说在 IndexTTS 2.5、屏幕上还是 VoxCPM2 的表单」，
    根因在 JS（#131）；但另一半风险是**模板本身**把 A 引擎的页面接到了 B 引擎的
    端点 —— 那种错在哪个引擎下点都是错的，且不会有任何报错。本守卫把整张矩阵
    一次钉死。
    """
    base = (_TPL_DIR / "base.html").read_text(encoding="utf-8")
    # 模板文件名与 tab 名并不一一对应（lora → tabs/lora_manager.html），
    # 用后端那份权威映射解析，别在这里猜文件名。
    from integrated_app.routes.tabs import _TAB_TEMPLATES

    offenders: list[str] = []
    checked = 0
    for tag in _SIDEBAR_ITEM_RE.findall(base):
        tab = _attr(tag, "data-tab")
        model = _attr(tag, "data-model")
        if not tab or model not in _TAB_ENGINE_PREFIXES:
            continue
        rel = _TAB_TEMPLATES.get(tab)
        if not rel:
            offenders.append(f"{tab}: 侧栏有这一项，但后端 _TAB_TEMPLATES 里没有登记")
            continue
        tpl = _TPL_DIR / rel
        if not tpl.is_file():
            offenders.append(f"{tab}: 登记的模板 {rel} 不存在")
            continue
        n, bad = _tab_engine_offenders(tab, model, tpl.read_text(encoding="utf-8"))
        checked += n
        offenders += bad

    assert checked >= 6, f"只核对到 {checked} 个生成表单，扫描规则可能已失配"
    assert offenders == [], f"功能页与引擎端点不匹配：{offenders}"


def test_programmatic_tab_jumps_use_goto_tab():
    """非点击的跳转必须走 ``gotoTab``（内部补 click），不能用只改高亮的 ``activateTab``。

    WHY：``activateTab`` 只改样式，内容靠按钮的 ``hx-get`` 在被点击时拉；引擎切换、
    命令面板、以及空状态里的「去克隆/去设计」按钮都曾用它做过程序化跳转，结果是高亮
    与内容分家（GOTCHAS #131）。唯一合法形态是 ``onclick="...activateTab(this)"``。
    """
    offenders: list[str] = []
    scanned = 0
    targets = sorted((_APP / "static" / "js").rglob("*.js")) + sorted(_TPL_DIR.rglob("*.html"))
    for path in targets:
        rel = path.relative_to(_APP).as_posix()
        if "vendor" in rel or rel.endswith(".min.js") or rel.endswith("js/sidebar.js"):
            continue  # sidebar.js 是定义与导出处
        text = path.read_text(encoding="utf-8", errors="replace")
        scanned += 1
        offenders += [f"{rel}:{line}" for line in _illegal_activate_tab_calls(text)]

    assert scanned >= 30, f"只扫了 {scanned} 个前端文件，扫描范围可能已失效"
    assert offenders == [], f"以下调用只改侧栏高亮、不会拉页面内容，应改用 TTSApp.sidebar.gotoTab()：{offenders}"


def test_sidebar_engine_matrix_guard_is_not_vacuous():
    """变异自证：同一张模板换到别的引擎名下必须判负。"""
    voxcpm_tpl = (_TPL_DIR / "tabs" / "voice_clone.html").read_text(encoding="utf-8")
    # 正确接线：VoxCPM2 的页面打 voxcpm 端点
    assert _tab_engine_offenders("voice_clone", "voxcpm2", voxcpm_tpl)[0] >= 1
    # 同一个模板挂到 indextts2 名下 = 接线错，必须判负
    n, bad = _tab_engine_offenders("voice_clone", "indextts2", voxcpm_tpl)
    assert n >= 1 and bad, "把 voxcpm 页面算到 indextts2 名下必须被抓出"
    # 端点被改成别的引擎的页面，即使在正确名下也要判负
    mutated = voxcpm_tpl.replace('hx-post="/api/generate/voxcpm_clone"', 'hx-post="/api/generate/indextts2"', 1)
    assert mutated != voxcpm_tpl, "变异样本没生效"
    assert _tab_engine_offenders("voice_clone", "voxcpm2", mutated)[1]
    # activateTab 检测的自证：只有 activateTab(this) 合法
    assert _illegal_activate_tab_calls("x.TTSApp.sidebar.activateTab(btn);") == [1]
    assert _illegal_activate_tab_calls("x.TTSApp.sidebar.activateTab('voice_design');") == [1]
    assert _illegal_activate_tab_calls('onclick="TTSApp.sidebar.activateTab(this)"') == []
    assert _illegal_activate_tab_calls("x.TTSApp.sidebar.gotoTab('voice_design');") == []


# ---------------------------------------------------------------------------
# 守卫 9：模板内联 <script> 必须能被 JS 解析器接受
# ---------------------------------------------------------------------------

_INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def _js_probe(block: str) -> str:
    """把 Jinja 占位化后再交给解析器：``{{ … }}`` → 字符串字面量，``{% … %}`` → 删除。"""
    return re.sub(r"\{%.*?%\}", "", re.sub(r"\{\{.*?\}\}", '"X"', block, flags=re.S), flags=re.S)


def test_inline_scripts_parse_as_javascript():
    """内联脚本一处语法错，整页 JS 静默全废（没有报错块、没有进度条，只有"点了没反应"）。

    用 ``node --check`` 做纯语法校验，不执行任何脚本、不联网、不起服务。
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("本机没有 node，无法做 JS 语法校验")

    checked = 0
    failures: list[str] = []
    for path in sorted(_TPL_DIR.rglob("*.html")):
        for idx, block in enumerate(_INLINE_SCRIPT_RE.findall(path.read_text(encoding="utf-8", errors="replace"))):
            checked += 1
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tmp:
                tmp.write(_js_probe(block))
                tmp_path = Path(tmp.name)
            try:
                proc = subprocess.run([node, "--check", str(tmp_path)], capture_output=True, text=True)
                if proc.returncode != 0:
                    first = next((ln for ln in proc.stderr.splitlines() if ln.strip()), "")
                    failures.append(f"{path.name} 内联块#{idx}: {first[:160]}")
            finally:
                tmp_path.unlink(missing_ok=True)

    assert checked >= 25, f"只扫到 {checked} 个内联脚本块，正则可能已失配"
    assert failures == [], f"模板内联 JS 语法不通过：{failures}"


def test_js_probe_placeholder_transform_is_safe():
    """变异自证：真语法错必须被这个探针抓到，Jinja 本身不得造成误报。"""
    assert _js_probe('var a = {{ "label"|t(lang) }};').strip() == 'var a = "X";'
    assert _js_probe("{% if x %}var a = 1;{% endif %}").strip() == "var a = 1;"
    broken = _js_probe("function f( { return 1; }")
    assert broken == "function f( { return 1; }", "探针不该'修好'真正的语法错"


# ---------------------------------------------------------------------------
# 守卫 10：内联事件属性里调用的顶层函数必须有定义（防"按钮点了是死的"）
# ---------------------------------------------------------------------------

_ON_ATTR_RE = re.compile(r"""\bon(?:click|change|submit|input|keyup|keydown|blur|focus)\s*=\s*"([^"]*)""", re.S)
_TOP_CALL_RE = re.compile(r"(?<![.\w])(?:window\.)?([A-Za-z_$][\w$]*)\s*\(")
#: 内联事件里合法的浏览器全局，不算"未定义的页面函数"
_JS_GLOBAL_IN_ATTR: frozenset[str] = frozenset(
    {"confirm", "alert", "parseFloat", "parseInt", "isNaN", "if", "return", "void", "typeof", "new", "event"}
)
#: 已知「按钮存在但功能没实现」的欠债（同 _KNOWN_UNIMPLEMENTED_ENDPOINTS 的约定：
#: 显式记账而不是让整条断言失效）。登记前提：在 GOTCHAS 里留痕；实现了就必须从这里删掉
#: （test_known_dead_handlers_list_only_shrinks 会盯着）。
#: 2026-09-19：refreshHealthLogs / filterHealthLogs 已接上 /api/system/logs-compat（#133）。
_KNOWN_DEAD_HANDLERS: frozenset[str] = frozenset()


def _frontend_defined_names() -> set[str]:
    """收集前端里"定义过"的顶层函数名（window.X=、function X(、const X = function/()）。"""
    files = sorted(_TPL_DIR.rglob("*.html")) + sorted((_APP / "static" / "js").rglob("*.js"))
    blob = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in files)
    names: set[str] = set(re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", blob))
    names |= set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", blob))
    names |= set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:function|\(|async)", blob))
    return names


def _dead_inline_handlers(html: str, defined: set[str]) -> set[str]:
    """返回这段模板里「onclick 等属性调了、但全前端没定义」的顶层函数名。"""
    dead: set[str] = set()
    for attr in _ON_ATTR_RE.findall(html):
        for call in _TOP_CALL_RE.finditer(attr):
            name = call.group(1)
            if name not in defined and name not in _JS_GLOBAL_IN_ATTR:
                dead.add(name)
    return dead


def test_inline_event_handlers_are_defined():
    """``onclick="foo()"`` 里的 foo 必须在某处真的定义了。

    WHY：属性里调一个不存在的函数**不会报错**（只在触发那一刻静默无反应），所以
    "按钮画出来了、点了什么都没发生"这种缺陷既不进日志也不进测试。本次就是靠这条
    抓出重试按钮调的 ``_retryLastGeneration`` 从未存在（GOTCHAS #133）。
    """
    defined = _frontend_defined_names()
    offenders: dict[str, list[str]] = {}
    attrs_scanned = 0
    for path in sorted(_TPL_DIR.rglob("*.html")):
        html = path.read_text(encoding="utf-8", errors="replace")
        attrs_scanned += len(_ON_ATTR_RE.findall(html))
        for name in sorted(_dead_inline_handlers(html, defined) - _KNOWN_DEAD_HANDLERS):
            offenders.setdefault(name, []).append(path.name)

    assert defined, "一个顶层函数都没解析出来，说明扫描规则失效"
    assert attrs_scanned >= 100, f"只扫到 {attrs_scanned} 个内联事件属性，正则可能已失配"
    assert offenders == {}, f"内联事件属性调用了不存在的函数（按钮是死的）：{offenders}"


def test_known_dead_handlers_list_only_shrinks():
    """欠债清单里每个名字必须仍然"确实没定义"，且不得新增。"""
    defined = _frontend_defined_names()
    implemented = sorted(n for n in _KNOWN_DEAD_HANDLERS if n in defined)
    assert implemented == [], f"这些处理器已经实现了，应从 _KNOWN_DEAD_HANDLERS 移除：{implemented}"


def test_dead_handler_guard_is_not_vacuous():
    """变异自证：调一个不存在的函数必须判负，调存在的必须判正。"""
    defined = _frontend_defined_names() | {"realHandler"}
    assert _dead_inline_handlers('<button onclick="realHandler()">', defined) == set()
    assert _dead_inline_handlers('<button onclick="window.realHandler()">', defined) == set()
    assert _dead_inline_handlers('<button onclick="nopeHandler(1)">', defined) == {"nopeHandler"}
    # 方法调用与浏览器全局不得误报
    assert _dead_inline_handlers('<button onclick="document.getElementById(1).focus()">', defined) == set()
    assert _dead_inline_handlers('<button onclick="return confirm(1)">', defined) == set()


# ---------------------------------------------------------------------------
# 欠债清单防膨胀
# ---------------------------------------------------------------------------


def test_known_debt_list_only_shrinks(client):
    """_KNOWN_UNIMPLEMENTED_ENDPOINTS 里不得出现已注册端点（防清单变成垃圾桶）。"""
    registered = _registered(client)
    stale = sorted(p for p in _KNOWN_UNIMPLEMENTED_ENDPOINTS if p in registered)
    assert stale == [], f"欠债端点已实现应从清单移除：{stale}"


@pytest.mark.parametrize(
    "guard",
    [
        "test_all_hx_attributes_point_at_registered_routes",
        "test_form_fields_accepted_by_target_endpoints",
        "test_range_and_file_controls_have_name",
        "test_streaming_templates_use_shared_result_wiring",
        "test_unsafe_manual_fetches_carry_csrf_token",
        "test_sidebar_tabs_post_to_their_own_engine",
        "test_programmatic_tab_jumps_use_goto_tab",
        "test_inline_scripts_parse_as_javascript",
        "test_inline_event_handlers_are_defined",
    ],
)
def test_guards_are_registered_and_not_todo(guard):
    """守卫本体不得被 skip/xfail 标记（变异测试的前置：确认它们真的会跑）。"""
    import tests.test_fe_be_consistency as mod

    fn = getattr(mod, guard)
    marks = {m.name for m in getattr(fn, "pytestmark", [])}
    assert not ({"skip", "skipif", "xfail"} & marks), f"{guard} 被标记跳过，守卫失效"
