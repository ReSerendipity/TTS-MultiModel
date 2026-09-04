"""前后端一致性守卫（对应 docs/reports/前后端功能一致性检查报告_20260904.md §7）。

三个守卫分别拦截报告中三类静默失效：

1. ``test_all_hx_attributes_point_at_registered_routes`` —— URL 层：
   模板中所有 hx-get/hx-post/hx-put/hx-delete 目标必须已注册（现有
   test_routes_htmx 只查 tabs 目录的 hx-post，A1/A2 类 405 漏网）。
2. ``test_form_fields_accepted_by_target_endpoints`` —— 字段层：
   每个表单提交的字段名必须至少被该表单某个提交目标端点声明
   （B2 滑杆缺 name 之外的另一面：控件发字段而后端不读 = 静默忽略）。
3. ``test_emotion_sliders_have_name`` —— name 层：
   type=range/file 控件必须携带 name（B2 的直接形态：只有 id 没有 name
   导致 8 维情感向量整体静默失效）。

维护约定（参 KNOWN_GOTCHAS #36）：这三条守卫都做过变异测试——
把任一模板的端点/字段故意改错，对应测试必须变红；否则说明断言写成了永真。
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
    ],
)
def test_guards_are_registered_and_not_todo(guard):
    """守卫本体不得被 skip/xfail 标记（变异测试的前置：确认它们真的会跑）。"""
    import tests.test_fe_be_consistency as mod

    fn = getattr(mod, guard)
    marks = {m.name for m in getattr(fn, "pytestmark", [])}
    assert not ({"skip", "skipif", "xfail"} & marks), f"{guard} 被标记跳过，守卫失效"
