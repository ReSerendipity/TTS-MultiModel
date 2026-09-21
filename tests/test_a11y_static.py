"""可感知性（a11y + 静默失效）静态守卫 —— 纯扫模板源码，不需要浏览器。

七条守卫各自拦截一类「用户看不见任何提示就失败」的成因：

A1 图标按钮没有可读名称。只塞 <svg> 又不写 aria-label 的按钮，读屏软件只能念出
   「按钮」，键盘用户 Tab 过去完全不知道它是干什么的。
A2 模板外链 http(s) 资源。CSP 的 style-src / font-src / script-src 全是 'self'
   （middleware/security_headers.py），外链必然被浏览器拦掉：字体菜单的 14 个家族
   因此静默回退了很久，页面每次加载还多刷一条 CSP 报错（见 static/fonts/README.md）。
A3 同页 id 重复。getElementById 只返回文档序第一个，后一个节点从此不可达；锚点跳转、
   scroll-margin 兜底、表单联动都会「看着没坏但作用在错的元素上」。
A4 模态框缺可访问名称或角色。aria-modal 打开后读屏用户不知道自己进了什么。
A5 进度与错误播报通道。排队横幅 / 加载遮罩 / toast / 错误块必须带 aria-live 或 role，
   否则「生成失败」这条信息对读屏用户是完全静默的——只有眼睛能看见。
A6 客户端自己拼的错误块同样要 role="alert"。A5 只覆盖服务端 partial，而真机上大量
   失败路径是 JS 用 innerHTML/createElement 手拼的精简块（GOTCHAS #133 的第二面）。
A7 正文字体栈不得引用随包标题字体。那 14 个家族自托管后在所有平台必然可用，进正文栈
   就等于悄悄换掉全站正文（Windows 盖过微软雅黑、非 Windows 偏离 L6 基线）。

维护约定（同 test_fe_be_consistency.py）：每条守卫配一个 ``*_is_not_vacuous`` 变异自证，
把违例样本喂给同一个判定函数必须判红；否则说明断言已经写成永真。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "app" / "integrated_app"
_TPL_DIR = _APP / "templates"
_BASE_HTML = _TPL_DIR / "base.html"
_ERROR_PARTIAL = _TPL_DIR / "partials" / "error_message.html"

_BUTTON_RE = re.compile(r"<button\b([^>]*)>(.*?)</button>", re.S | re.I)
_SVG_RE = re.compile(r"<svg\b.*?</svg>", re.S | re.I)
_JINJA_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)
_ID_RE = re.compile(r"\bid=[\"']([^\"'{}%\s]+)[\"']")
_EXT_RE = re.compile(
    r"<(?:link|script|img|source|audio|video)\b[^>]*?\b(?:href|src)=[\"']\s*(?:https?:)?//([^\"'/]+)", re.I
)
_TAG_WITH_ATTRS_RE = re.compile(r"<[a-zA-Z][^>]*>", re.S)


def _templates() -> list[Path]:
    return sorted(_TPL_DIR.rglob("*.html"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _markup(html: str) -> str:
    """去掉 HTML 注释后再判定。

    注释里的 ``<link href="https://…">`` 浏览器根本不会请求，把它算成违例只会逼人删掉
    一条有解释价值的注释——base.html 就留着一条「以前这里是 Google Fonts 外链」的说明。
    """
    return _COMMENT_RE.sub("", html)


def _visible_text(fragment: str) -> str:
    """剥掉标签与 SVG，把 Jinja 插值当作「有文本」处理（{{ "help"|t(lang) }} 会渲染出字）。"""
    text = _SVG_RE.sub("", fragment)
    text = _JINJA_RE.sub("X", text)
    text = _TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _static_markup(html: str) -> str:
    """去掉注释与 <script> 块：JS 字符串里的 id 只有覆盖同一容器 innerHTML 时才进 DOM，不构成重复。"""
    return _SCRIPT_BLOCK_RE.sub("", _markup(html))


# ---------------------------------------------------------------------------
# A1：图标按钮必须有可读名称
# ---------------------------------------------------------------------------


def icon_buttons_without_name(html: str) -> list[str]:
    offenders: list[str] = []
    for attrs, inner in _BUTTON_RE.findall(_markup(html)):
        if _visible_text(inner):
            continue
        if "aria-label" in attrs or "aria-labelledby" in attrs or re.search(r"\btitle=", attrs):
            continue
        offenders.append(re.sub(r"\s+", " ", attrs).strip()[:80])
    return offenders


def test_icon_buttons_have_accessible_names():
    offenders: list[str] = []
    checked = 0
    for path in _templates():
        html = _read(path)
        checked += len(_BUTTON_RE.findall(_markup(html)))
        offenders += [f"{path.name} -> {o}" for o in icon_buttons_without_name(html)]

    assert checked >= 100, f"只扫到 {checked} 个 <button>，正则或模板目录已变，守卫可能失效"
    assert offenders == [], f"存在无文本、也无 aria-label/title 的按钮（读屏念不出来）：{offenders}"


def test_icon_button_guard_is_not_vacuous():
    assert icon_buttons_without_name('<button class="x"><svg viewBox="0 0 1 1"></svg></button>')
    assert icon_buttons_without_name('<button><i class="fas fa-x"></i></button>')
    # 合规形态必须判正
    assert not icon_buttons_without_name('<button aria-label="关闭"><svg/></button>')
    assert not icon_buttons_without_name('<button title="关闭"><svg/></button>')
    assert not icon_buttons_without_name("<button>关闭</button>")
    assert not icon_buttons_without_name('<button><svg/><span>{{ "help"|t(lang) }}</span></button>')


# ---------------------------------------------------------------------------
# A2：模板不得外链 http(s) 资源（CSP 只放行 'self'）
# ---------------------------------------------------------------------------


def external_resource_hosts(html: str) -> list[str]:
    return _EXT_RE.findall(_markup(html))


def test_templates_do_not_reference_external_origins():
    offenders: list[str] = []
    scanned = 0
    for path in _templates():
        scanned += 1
        for host in external_resource_hosts(_read(path)):
            offenders.append(f"{path.name} -> {host}")

    assert scanned >= 25, f"只扫到 {scanned} 个模板文件，守卫可能已失配"
    assert offenders == [], (
        "模板外链了 http(s) 资源，但 CSP 的 style-src/font-src/script-src 只允许 'self'，"
        f"浏览器会静默拦掉（自托管方式见 static/fonts/README.md）：{offenders}"
    )


def test_external_origin_guard_is_not_vacuous():
    assert external_resource_hosts('<link href="https://fonts.googleapis.com/css2?family=X" rel="stylesheet">')
    assert external_resource_hosts('<script src="//cdn.example.com/a.js"></script>')
    # 本地引用与注释里的示例都必须判正
    assert not external_resource_hosts('<link href="/static/css/styles.css" rel="stylesheet">')
    assert not external_resource_hosts('<!-- 以前是 <link href="https://fonts.googleapis.com/css2?family=X"> -->')
    assert not external_resource_hosts("<p>文档见 https://example.com/x</p>")


# ---------------------------------------------------------------------------
# A3：静态标记内 id 唯一，且不与 base.html 撞名
# ---------------------------------------------------------------------------


def duplicate_static_ids(html: str) -> list[str]:
    counts: Counter[str] = Counter(_ID_RE.findall(_static_markup(html)))
    return sorted(i for i, n in counts.items() if n > 1)


def ids_colliding_with_base(other_html: str) -> list[str]:
    base_ids = set(_ID_RE.findall(_static_markup(_read(_BASE_HTML))))
    return sorted(set(_ID_RE.findall(_static_markup(other_html))) & base_ids)


def test_static_markup_ids_are_unique():
    offenders: list[str] = []
    checked = 0
    for path in _templates():
        ids = _ID_RE.findall(_static_markup(_read(path)))
        checked += len(ids)
        if dup := duplicate_static_ids(_read(path)):
            offenders.append(f"{path.name}: {dup}")

    assert checked >= 200, f"只扫到 {checked} 个 id，正则可能已失配"
    assert offenders == [], f"同一模板内 id 重复，getElementById 只会拿到第一个：{offenders}"


def test_partial_ids_do_not_collide_with_base():
    offenders: list[str] = []
    for path in sorted((_TPL_DIR / "tabs").rglob("*.html")) + sorted((_TPL_DIR / "partials").rglob("*.html")):
        if path == _BASE_HTML:
            continue
        if clash := ids_colliding_with_base(_read(path)):
            offenders.append(f"{path.name}: {clash}")

    assert offenders == [], f"tab/局部模板的 id 与 base.html 撞名，同页会出现重复 id：{offenders}"


def test_duplicate_id_guard_is_not_vacuous():
    assert duplicate_static_ids('<div id="a"></div><div id="a"></div>')
    # Jinja 动态 id 与 <script> 字符串里的 id 不算违例
    assert not duplicate_static_ids('<div id="row-{{ i }}"></div><div id="row-{{ i }}"></div>')
    assert not duplicate_static_ids('<div id="a"></div><script>el.innerHTML = \'<b id="a"></b>\';</script>')
    # 与 base.html 撞名才判负，换个没占用过的 id 即合规
    assert ids_colliding_with_base('<div id="toast-container"></div>') == ["toast-container"]
    assert ids_colliding_with_base('<div id="totally-free-id"></div>') == []


# ---------------------------------------------------------------------------
# A4：模态框必须有角色与可访问名称
# ---------------------------------------------------------------------------


def unnamed_or_unroled_dialogs(html: str) -> list[str]:
    static_ids = set(_ID_RE.findall(_static_markup(html)))
    offenders: list[str] = []
    for tag in _TAG_WITH_ATTRS_RE.findall(_markup(html)):
        is_dialog = re.search(r"\brole=[\"'](?:alert)?dialog[\"']", tag)
        is_modal = re.search(r"\baria-modal=[\"']true[\"']", tag)
        if not (is_dialog or is_modal):
            continue
        if not is_dialog:
            offenders.append(f'aria-modal 元素缺 role="dialog": {tag[:80]}')
            continue
        labelledby = re.search(r"\baria-labelledby=[\"']([^\"']+)[\"']", tag)
        named = "aria-label=" in tag or bool(labelledby and labelledby.group(1).strip() in static_ids)
        if not named:
            offenders.append(f"对话框没有可访问名称（aria-label 或指向存在 id 的 aria-labelledby）: {tag[:80]}")
    return offenders


def test_dialogs_are_named_and_roled():
    offenders: list[str] = []
    found = 0
    for path in _templates():
        html = _read(path)
        found += len(re.findall(r"\baria-modal=|\brole=[\"'](?:alert)?dialog[\"']", html))
        offenders += [f"{path.name} -> {o}" for o in unnamed_or_unroled_dialogs(html)]

    assert found >= 2, f"只扫到 {found} 个对话框特征，守卫可能已失配"
    assert offenders == [], f"模态框角色/名称不合规：{offenders}"


def test_dialog_guard_is_not_vacuous():
    assert unnamed_or_unroled_dialogs('<div role="dialog" aria-modal="true"><p>x</p></div>')
    assert unnamed_or_unroled_dialogs('<div aria-modal="true" id="m"><h3 id="t">T</h3></div>')
    # aria-labelledby 指向不存在的 id 同样判负（名称拿不到 = 静默）
    assert unnamed_or_unroled_dialogs('<div role="dialog" aria-labelledby="ghost"><h3 id="t">T</h3></div>')
    assert not unnamed_or_unroled_dialogs('<div role="dialog" aria-label="新建音色"></div>')
    assert not unnamed_or_unroled_dialogs(
        '<div role="dialog" aria-modal="true" aria-labelledby="t"><h3 id="t">T</h3></div>'
    )


# ---------------------------------------------------------------------------
# A5：进度与错误必须走播报通道
# ---------------------------------------------------------------------------

#: base.html 里承担「用户必须知道现在在发生什么」的三个常驻区域
_ANNOUNCER_IDS = ("queue-wait-banner", "ml-loading-overlay", "toast-container")


def unannounced_region(html: str, element_id: str) -> str | None:
    for tag in _TAG_WITH_ATTRS_RE.findall(_markup(html)):
        if re.search(rf"\bid=[\"']{re.escape(element_id)}[\"']", tag):
            if re.search(r"\baria-live=|\brole=[\"'](status|alert|log)[\"']", tag):
                return None
            return f"#{element_id} 没有 aria-live / role=status|alert，读屏软件不会播报其内容变化"
    return f"#{element_id} 在 base.html 中已不存在（播报通道被删）"


def error_block_findings(html: str) -> list[str]:
    offenders: list[str] = []
    root = re.search(r"<div\b[^>]*class=[\"'][^\"']*tts-error-block[^\"']*[\"'][^>]*>", html)
    if not root:
        return ["错误块根节点 .tts-error-block 不见了"]
    if not re.search(r"\brole=[\"']alert[\"']", root.group(0)):
        offenders.append('错误块根节点缺 role="alert"：htmx 换进来后读屏用户不知道生成失败了')
    for attrs, inner in _BUTTON_RE.findall(_markup(html)):
        if not _visible_text(inner):
            offenders.append(f"错误块内的按钮无文字标签（只剩图标/空白）: {re.sub(chr(10), ' ', attrs)[:60]}")
    if "tts-error-actions" not in html:
        offenders.append("错误块缺 .tts-error-actions 容器")
    return offenders


def test_progress_and_error_channels_are_announced():
    base_html = _read(_BASE_HTML)
    offenders = [msg for cid in _ANNOUNCER_IDS if (msg := unannounced_region(base_html, cid))]
    offenders += error_block_findings(_read(_ERROR_PARTIAL))

    assert "aria-busy" in base_html, "加载遮罩的 aria-busy 被删了：读屏软件不再表示「正在忙」"
    assert offenders == [], f"进度/错误播报通道不合规：{offenders}"


def test_announcement_guard_is_not_vacuous():
    assert unannounced_region('<div id="a"></div>', "a")
    assert unannounced_region('<div id="missing"></div>', "a")
    assert unannounced_region('<div id="a" aria-hidden="true"></div>', "a")
    assert not unannounced_region('<div id="a" aria-live="polite"></div>', "a")
    assert not unannounced_region('<div id="a" role="status"></div>', "a")
    assert error_block_findings('<div class="tts-error-block"></div>')
    assert not error_block_findings(
        '<div class="tts-error-block" role="alert"><div class="tts-error-actions">'
        '<button type="button">重试</button></div></div>'
    )


# ---------------------------------------------------------------------------
# A6：客户端自己拼的错误块也必须带 role="alert"
# ---------------------------------------------------------------------------

_ERR_LITERAL_RE = re.compile(r"""<div class=["']tts-error-block["']([^>]*)>""")
_ERR_CLASSNAME_RE = re.compile(r"""\.className\s*=\s*["']tts-error-block["']""")


def _frontend_sources() -> list[Path]:
    return sorted(_TPL_DIR.rglob("*.html")) + sorted((_TPL_DIR.parent / "static" / "js").rglob("*.js"))


def client_error_block_findings(text: str) -> list[str]:
    """字面量形态要求同行有 role="alert"；className 赋值形态要求后两行内补上 setAttribute。"""
    lines = text.splitlines()
    offenders: list[str] = []
    for i, line in enumerate(lines):
        for m in _ERR_LITERAL_RE.finditer(line):
            if 'role="alert"' not in m.group(1) and "role='alert'" not in m.group(1):
                offenders.append(f'L{i + 1} 字面量错误块缺 role="alert"')
        if _ERR_CLASSNAME_RE.search(line):
            window = "\n".join(lines[i : i + 3])
            if "setAttribute('role', 'alert')" not in window and 'setAttribute("role", "alert")' not in window:
                offenders.append(f"L{i + 1} DOM 构造的错误块缺 setAttribute('role','alert')")
    return offenders


def test_client_built_error_blocks_are_announced():
    """JS/模板里手拼的 .tts-error-block 也要 role=alert。

    WHY 单列：A5 只管服务端 partial；真机上发现一次 400 之后，页面上出现的是
    voice_design 自己拼进 #vd-status 的**精简块**——没有 role、没有按钮，读屏用户
    完全不知道生成失败了（GOTCHAS #133 的第二面）。
    """
    offenders: list[str] = []
    sites = 0
    for path in _frontend_sources():
        rel = path.relative_to(_TPL_DIR.parent).as_posix()
        if "vendor" in rel or rel.endswith(".min.js") or rel.endswith("partials/error_message.html"):
            continue  # partial 由 A5 管
        text = path.read_text(encoding="utf-8", errors="replace")
        sites += len(_ERR_LITERAL_RE.findall(text)) + len(_ERR_CLASSNAME_RE.findall(text))
        offenders += [f"{rel} {o}" for o in client_error_block_findings(text)]

    assert sites >= 15, f"只扫到 {sites} 个客户端错误块构造点，正则可能已失配"
    assert offenders == [], f"这些错误块不会被读屏播报：{offenders}"


def test_client_error_block_guard_is_not_vacuous():
    assert client_error_block_findings('<div class="tts-error-block"><div>a</div></div>')
    assert client_error_block_findings("el.className = 'tts-error-block';\nel.textContent = 'x';")
    assert not client_error_block_findings('<div class="tts-error-block" role="alert">a</div>')
    assert not client_error_block_findings("el.className = 'tts-error-block';\nel.setAttribute('role', 'alert');")


# ---------------------------------------------------------------------------
# A7 正文栈 vs 随包标题字体
# ---------------------------------------------------------------------------

_VARIABLES_CSS = _APP / "static" / "css" / "variables.css"
_FONTS_CSS = _APP / "static" / "css" / "fonts.local.css"


def _stack_collisions(stack_decl: str, bundled: set[str]) -> list[str]:
    """正文字体栈里与随包标题字体撞车的家族名。"""
    return [f.strip().strip("'\"") for f in stack_decl.split(",") if f.strip().strip("'\"") in bundled]


def _body_stack_decl() -> str:
    m = re.search(r"--font-sans:\s*([^;]+);", _VARIABLES_CSS.read_text(encoding="utf-8"))
    assert m, "variables.css 里找不到 --font-sans 声明，解析规则已失效"
    return m.group(1)


def _bundled_title_families() -> set[str]:
    families = set(re.findall(r"font-family:\s*'([^']+)'", _FONTS_CSS.read_text(encoding="utf-8")))
    assert len(families) >= 12, f"只解析到 {len(families)} 个随包字体家族，扫描已失效"
    return families


def test_body_font_stack_excludes_bundled_title_faces():
    """正文栈不得引用随包标题字体家族。

    WHY：那 14 个家族现在由 ``fonts.local.css`` 以 @font-face 随包，**在所有平台都必然可用**。
    正文栈里一旦写进它们（历史上 'Noto Sans SC' 就在那里，当时因为 Google Fonts 被 CSP 挡掉
    而一直是死的），自托管之后它立刻变成活的：Windows 上盖过 Microsoft YaHei、非 Windows 上
    偏离 CI 生成的 L6 视觉基线——等于在"修字体静默失效"同一批改动里顺手改了全站正文。
    标题想用思源黑体走「切换标题字体」菜单，不经过 ``--font-sans``。
    """
    collisions = _stack_collisions(_body_stack_decl(), _bundled_title_families())
    assert collisions == [], (
        f"--font-sans 引用了随包标题字体 {collisions}：这会让全站正文换字体并偏离 L6 基线；"
        "标题用字体菜单选，不要放进正文栈"
    )


def test_body_font_stack_guard_is_not_vacuous():
    bundled = _bundled_title_families()
    assert "Noto Sans SC" in bundled, "随包字体集里没有 Noto Sans SC，样本失效"
    assert _stack_collisions("'Inter', 'Noto Sans SC', sans-serif", bundled) == ["Noto Sans SC"]
    assert _stack_collisions(_body_stack_decl(), bundled) == []
