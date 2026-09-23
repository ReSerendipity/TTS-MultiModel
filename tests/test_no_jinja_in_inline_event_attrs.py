"""issue #130 的闸：内联事件属性里不许出现 Jinja 插值。

`onclick="foo('{{ x }}')"` 这个写法**从原理上就是错的**，跟转义够不够仔细无关：
浏览器解析属性值时会把 HTML 实体解回去，所以 `{{ x|e }}` 产出的 `&#39;` 到 JS 里又变回 `'` ——
HTML 转义保护的是属性文本，不是 JS 字符串字面量。要拼数据进 JS 就得用 `|tojson`（且属性必须
用单引号，顺序不能反），更稳的做法是本次采用的：**数据只走 `data-*`，行为只走事件委托**。

#99 修了 JS 侧拼 HTML 的那一批，#116 修了别处，这 7 处（persona 名、历史文件名/id、engine_id、
后处理 prefix）是服务端模板里的残留。persona 名是用户在克隆页敲进去的 → 有真实控制点。

本闸扫全仓模板，所以同类写法再出现就会红，而不是靠人记得 review。
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TPL_DIR = PROJECT_ROOT / "app" / "integrated_app" / "templates"

#: `on<event>="..."` —— 双引号形态（全仓模板只用这一种；单引号形态一并挡在正则外没有意义，
#: 因为违规判定看的是"属性里有没有插值"，不是引号风格）。
_ON_ATTR = re.compile(r"""\son[a-z]+\s*=\s*"([^"]*)\"""", re.I)
_JINJA = re.compile(r"\{\{|\{%")

#: 本次改法留下的锚点：扫描器不能对着空目录"绿"给自己看。
_EXPECTED_DATA_ATTRS = (
    "data-persona-audio",
    "data-history-play",
    "data-history-download",
    "data-history-hide",
    "data-history-delete",
    "data-switch-model",
    "data-reprocess",
)


def _templates() -> list[Path]:
    return sorted(TPL_DIR.rglob("*.html"))


def test_no_jinja_interpolation_inside_inline_event_attrs() -> None:
    files = _templates()
    assert files, f"模板目录扫不到文件：{TPL_DIR} —— 本闸失效而不自知"

    attrs_scanned = 0
    offenders: list[str] = []
    for path in files:
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for value in _ON_ATTR.findall(line):
                attrs_scanned += 1
                if _JINJA.search(value):
                    offenders.append(f"{rel}:{line_no}  {value.strip()[:88]}")

    assert attrs_scanned >= 100, (
        f"只扫到 {attrs_scanned} 个内联事件属性（历史基线 250+），正则八成失配了 —— 一条永不命中的闸比没有闸更危险"
    )
    assert not offenders, (
        f"{attrs_scanned} 个内联事件属性里有 {len(offenders)} 处把 Jinja 插值拼进了 JS 上下文。\n"
        "  HTML 实体在属性解析时会被解回去，所以 `|e` 在这里不是转义手段：\n  "
        + "\n  ".join(offenders)
        + "\n  改法见 issue #130：数据放 data-*（属性上下文里 autoescape 是对的），行为放事件委托。"
    )


def test_the_seven_fixed_sites_now_carry_data_attrs() -> None:
    """反向钉住"扫描器有东西可扫"：那 7 处的数据必须真的待在 data-* 属性里。"""
    blob = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in _templates())
    missing = [name for name in _EXPECTED_DATA_ATTRS if f'{name}="{{' not in blob]
    assert not missing, f"这些 data-* 锚点丢了（或不再带插值）：{missing}"
