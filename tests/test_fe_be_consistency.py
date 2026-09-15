"""前后端一致性守卫（对应 docs/reports/前后端功能一致性检查报告_20260904.md §7）。

六个守卫分别拦截报告中不同层面的静默失效：

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

维护约定（参 KNOWN_GOTCHAS #36）：这些守卫都做过变异测试——
把任一模板的端点/字段/URL 故意改错，对应测试必须变红；否则说明断言写成了
永真。守卫 5 另配 ``test_url_literal_guard_is_not_vacuous`` 做恒真自证；
守卫 6 的「函数语义」一面由 ``tests/frontend/wire_generation_result.js``
（jsdom）承担，两者互补：本文件管**调用点**，jsdom 测试管**函数对不对**。
"""

import re
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
            if target not in registered and target not in _KNOWN_UNIMPLEMENTED_ENDPOINTS:
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
                if not _frontend_url_is_registered(url, registered, matchers):
                    offenders.append(f"{rel}:{lineno} -> {url}")

    assert checked >= 50, f"仅扫描到 {checked} 个前端 URL 字面量，断言可能已失效"
    assert offenders == [], f"前端引用了后端未注册的路径：{offenders}"


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
    ],
)
def test_guards_are_registered_and_not_todo(guard):
    """守卫本体不得被 skip/xfail 标记（变异测试的前置：确认它们真的会跑）。"""
    import tests.test_fe_be_consistency as mod

    fn = getattr(mod, guard)
    marks = {m.name for m in getattr(fn, "pytestmark", [])}
    assert not ({"skip", "skipif", "xfail"} & marks), f"{guard} 被标记跳过，守卫失效"
