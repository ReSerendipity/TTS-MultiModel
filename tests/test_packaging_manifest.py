"""分发产物完整性守卫：许可声明随包 + 自托管字体真的进包。

三条静态守卫，全部只看仓库里的**白名单文本**，不跑 PowerShell / docker build：

1. ``test_license_docs_are_listed_by_every_packaging_path`` —— 许可声明必须出现在
   便携包与发布 staging 两条根文件白名单里。
   WHY：打包脚本用的是**白名单**，新增根文件默认不进包，且构建不报错——
   ``THIRD_PARTY_NOTICES.md``（随包 777 个 OFL 标题字体的许可声明表）加好后就这样
   静默漏了两次，只有翻解开的产物才会发现。
2. ``test_docker_context_keeps_license_and_font_files`` —— 用 moby 的 .dockerignore
   语义（``*`` 不跨 ``/``、父目录命中即整棵剪枝、后匹配覆盖前匹配）复演一遍镜像
   上下文，断言许可文本与字体资产在、密钥与权重不在。
3. ``test_bundle_exclude_patterns_keep_the_font_payload`` —— 把便携包的排除规则按
   PowerShell ``-like`` 语义（同时比对整条相对路径与叶子名）套到字体路径上。

另有 ``test_fonts_local_css_urls_resolve``：``fonts.local.css`` 里每个 ``url()`` 必须
对应一个真实存在的 woff2。这是"字体静默失效"在分发侧的形态——CSS 引用了不存在的
文件时浏览器不报错，只是悄悄不用该字体（GOTCHAS #127）。
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
_STATIC = _ROOT / "app" / "integrated_app" / "static"
_FONTS = _STATIC / "fonts"
_FONTS_CSS = _STATIC / "css" / "fonts.local.css"

# Apache-2.0 §4 要求 NOTICE 随作；随包字体是 OFL，许可文本须与使用者同交付。
_LICENSE_DOCS = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md")


# ---------------------------------------------------------------------------
# 解析器
# ---------------------------------------------------------------------------


def _ps_string_array(text: str, variable: str) -> list[str]:
    """取 ``$Variable = @('a', 'b', …)`` 里的字符串字面量（可跨行）。"""
    m = re.search(rf"\${variable}\s*=\s*@\((.*?)\)", text, re.DOTALL)
    assert m, f"未找到 ${variable} 数组——打包脚本白名单改名或删掉了？"
    return re.findall(r"'([^']*)'", m.group(1))


def _foreach_string_array(text: str, must_contain: str) -> list[str]:
    """取 ``foreach ($x in @('a', …))`` 中同时包含 ``must_contain`` 的那一项。"""
    candidates = [
        re.findall(r"'([^']*)'", g) for g in re.findall(r"foreach\s*\(\$\w+\s+in\s+@\((.*?)\)\s*\)", text, re.DOTALL)
    ]
    hit = [c for c in candidates if must_contain in c]
    assert hit, f"未找到含 {must_contain!r} 的 foreach 白名单——脚本结构变了"
    assert len(hit) == 1, f"{must_contain!r} 命中 {len(hit)} 个白名单，无法定位"
    return hit[0]


def _missing_license_docs(listed: list[str]) -> list[str]:
    return [doc for doc in _LICENSE_DOCS if doc not in listed]


def _whitelist_loop_hardfails(text: str, loop_header: str) -> bool:
    """白名单循环在「条目指向的文件不存在」时是否硬失败。

    静默跳过会让构建全绿却少发文件，正是 SECURITY.md 那次漏发的根因。
    """
    i = text.index(loop_header)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return "throw" in text[i : j + 1]
    raise AssertionError(f"未找到 {loop_header} 的配对大括号")


# ---------------------------------------------------------------------------
# moby .dockerignore 语义（够用且保守的复演）
# ---------------------------------------------------------------------------


def _dockerignore_rules() -> list[tuple[str, bool]]:
    rules: list[tuple[str, bool]] = []
    for raw in (_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:].strip()
        rules.append((line.rstrip("/").replace("\\", "/"), negate))
    assert len(rules) >= 20, f".dockerignore 规则数异常（{len(rules)}），解析可能失效"
    return rules


def _go_match(pattern: str, rel: str) -> bool:
    """Go filepath.Match：``*`` 不跨 ``/``，``**`` 跨。整串锚定匹配。"""
    out = ""
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern.startswith("**", i):
                out += ".*"
                i += 2
                continue
            out += "[^/]*"
        elif ch == "?":
            out += "[^/]"
        else:
            out += re.escape(ch)
        i += 1
    return re.fullmatch(out, rel) is not None


def _docker_last_match(rel: str, rules: list[tuple[str, bool]]) -> bool:
    """只看该路径自身：后匹配的规则覆盖先匹配的。返回 True = 被排除。"""
    excluded = False
    for pattern, negate in rules:
        if _go_match(pattern, rel):
            excluded = not negate
    return excluded


def _docker_excluded(rel: str, rules: list[tuple[str, bool]]) -> bool:
    """Docker 在目录树上剪枝：父目录被排除时，子路径的任何例外也救不回来。"""
    parts = rel.split("/")
    for i in range(1, len(parts)):
        if _docker_last_match("/".join(parts[:i]), rules):
            return True
    return _docker_last_match(rel, rules)


# ---------------------------------------------------------------------------
# PowerShell -like 语义
# ---------------------------------------------------------------------------


def _lib_string_array(variable: str) -> list[str]:
    text = (_SCRIPTS / "portable_bundle_lib.ps1").read_text(encoding="utf-8")
    m = re.search(rf"\$script:{variable}\s*=\s*@\((.*?)\)", text, re.DOTALL)
    assert m, f"未找到 $script:{variable}——打包库的禁区清单改名或删掉了？"
    return re.findall(r"'([^']*)'", m.group(1))


def _bundle_excluded(rel: str, patterns: list[str]) -> bool:
    """对齐 ``Test-TTSMultiModelPathExcluded`` 的三级判定：本机私有叶子名、禁止叶子
    模式、调用方传入的通配（整条相对路径或叶子名命中即排除）。"""
    norm = rel.replace("/", "\\").lower()
    leaf = norm.rsplit("\\", 1)[-1]
    for denied in _lib_string_array("DeniedLeafNames"):
        d = denied.lower()
        if leaf == d or fnmatch.fnmatch(leaf, f"{d}*"):
            return True
    for forbidden in _lib_string_array("ForbiddenLeafPatterns"):
        if fnmatch.fnmatch(leaf, forbidden.lower()):
            return True
    for p in patterns:
        pat = p.replace("/", "\\").lower()
        if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(leaf, pat):
            return True
    return False


# ---------------------------------------------------------------------------
# 守卫 1：根文件白名单
# ---------------------------------------------------------------------------


def test_license_docs_are_listed_by_every_packaging_path():
    """便携包与发布 staging 的根文件白名单都要含 LICENSE / NOTICE / THIRD_PARTY_NOTICES.md。"""
    bundle_script = (_SCRIPTS / "build_portable_bundle.ps1").read_text(encoding="utf-8")
    staging_script = (_SCRIPTS / "assemble_release_staging.ps1").read_text(encoding="utf-8")
    bundle = _ps_string_array(bundle_script, "CoreIncludeFiles")
    staging = _foreach_string_array(staging_script, "config.yaml")

    assert len(bundle) >= 10, f"便携包白名单只剩 {len(bundle)} 项，解析规则可能已失效"
    assert len(staging) >= 6, f"staging 根文件白名单只剩 {len(staging)} 项，解析规则可能已失效"

    offenders = [
        f"{name}: 缺 {missing}"
        for name, listed in (("build_portable_bundle.ps1", bundle), ("assemble_release_staging.ps1", staging))
        for missing in _missing_license_docs(listed)
    ]
    missing_files = [
        f for f in sorted(set(bundle) | set(staging)) if "." in f and not (_ROOT / f.replace("\\", "/")).exists()
    ]
    assert not offenders, f"许可声明未随包（白名单是逐项列举，新增根文件默认不进包）：{offenders}"
    assert not missing_files, (
        f"白名单指向仓库中不存在的文件（构建脚本对这种项直接 throw，不再静默跳过）：{missing_files}"
    )
    # 白名单漏项之所以能长期存在，是因为两条链路都"缺文件就跳过"；现在必须硬失败。
    assert _whitelist_loop_hardfails(bundle_script, "foreach ($f in $CoreIncludeFiles)"), (
        "build_portable_bundle.ps1 的根文件循环不再对缺失项 throw —— 静默少发文件的老 bug 会复现"
    )
    assert _whitelist_loop_hardfails(staging_script, "foreach ($f in @('start_portable.py'")


def test_license_doc_guard_is_not_vacuous():
    """变异自证：把 NOTICE 从白名单里摘掉 / 把 throw 换回静默跳过，守卫 1 都要报错。"""
    bundle_script = (_SCRIPTS / "build_portable_bundle.ps1").read_text(encoding="utf-8")
    bundle = _ps_string_array(bundle_script, "CoreIncludeFiles")
    assert _missing_license_docs(bundle) == []
    mutated = [x for x in bundle if x != "NOTICE"]
    assert _missing_license_docs(mutated) == ["NOTICE"], "摘掉 NOTICE 后守卫未报警——断言是空转的"
    assert len(mutated) == len(bundle) - 1

    header = "foreach ($f in $CoreIncludeFiles)"
    assert _whitelist_loop_hardfails(bundle_script, header)
    at = bundle_script.index(header)
    loop = bundle_script[at : bundle_script.index("\n    }", at) + len("\n    }")]
    assert not _whitelist_loop_hardfails(loop.replace("throw", "continue"), header), (
        "把 throw 换回静默跳过后仍判通过——断言空转"
    )


# ---------------------------------------------------------------------------
# 守卫 2：Docker 镜像上下文
# ---------------------------------------------------------------------------

_MUST_SHIP = (
    "LICENSE",
    "NOTICE",
    "README.md",
    "THIRD_PARTY_NOTICES.md",
    "app/integrated_app/static/css/fonts.local.css",
    "app/integrated_app/static/fonts/NotoSansSC-0.woff2",
    "app/integrated_app/static/fonts/licenses/NotoSansSC.OFL.txt",
    "app/integrated_app/static/fonts/README.md",
    "app/integrated_app/security/manifest_signing_public_key.pem",
    ".env.example",
)
_MUST_NOT_SHIP = (
    ".env",
    "SECURITY.md",
    "tests/test_packaging_manifest.py",
    "docs/DOD.md",
    "model/whatever.bin",
    "dist/package.zip",
    "logs/app.log",
    ".git/config",
)


@pytest.mark.parametrize("rel", _MUST_SHIP)
def test_docker_context_keeps_license_and_font_files(rel: str):
    """许可文本、字体与验签公钥必须留在镜像上下文里。"""
    assert (_ROOT / rel).exists(), f"测试引用的仓库文件不存在：{rel}"
    assert not _docker_excluded(rel, _dockerignore_rules()), f"{rel} 被 .dockerignore 挡在镜像外，用户拿不到许可/字体"


@pytest.mark.parametrize("rel", _MUST_NOT_SHIP)
def test_docker_context_blocks_runtime_and_private_paths(rel: str):
    """密钥、权重、测试与文档不进镜像层。"""
    assert _docker_excluded(rel, _dockerignore_rules()), f"{rel} 会进镜像层"


def test_dockerignore_guard_is_not_vacuous():
    """变异自证：删掉 ``!THIRD_PARTY_NOTICES.md`` 这一行，许可声明就该被挡住。"""
    rules = _dockerignore_rules()
    assert not _docker_excluded("THIRD_PARTY_NOTICES.md", rules), "当前规则下就已排除，断言无意义"
    without_exception = [(p, n) for p, n in rules if not (p == "THIRD_PARTY_NOTICES.md" and n)]
    assert _docker_excluded("THIRD_PARTY_NOTICES.md", without_exception), "例外行不是承重的——守卫空转"
    # 剪枝语义：被整目录排除时，子路径的例外也救不回来（Docker 走目录树时直接跳过）。
    pruned = _dockerignore_rules() + [("!docs/DOD.md", True)]
    assert _docker_excluded("docs/DOD.md", pruned), "父目录剪枝语义未生效，复演器与 Docker 不一致"


# ---------------------------------------------------------------------------
# 守卫 3：便携包排除规则 vs 字体负载
# ---------------------------------------------------------------------------

_FONT_SAMPLES = (
    "app/integrated_app/static/css/fonts.local.css",
    "app/integrated_app/static/fonts/NotoSansSC-0.woff2",
    "app/integrated_app/static/fonts/ZhiMangXing-0.ttf",
    "app/integrated_app/static/fonts/licenses/Cinzel.OFL.txt",
    "app/integrated_app/static/fonts/README.md",
)


def test_bundle_exclude_patterns_keep_the_font_payload():
    """字体资产不得被便携包排除规则命中（排除规则是逐目录递归判定的）。"""
    patterns = _ps_string_array(
        (_SCRIPTS / "build_portable_bundle.ps1").read_text(encoding="utf-8"), "CoreExcludePatterns"
    )
    assert len(patterns) >= 15, f"排除规则数异常（{len(patterns)}），解析可能失效"
    dropped = [p for p in _FONT_SAMPLES if _bundle_excluded(p, patterns)]
    assert not dropped, f"字体资产会被便携包丢弃：{dropped}"
    # 同一条规则须仍能挡住真正的禁区，否则说明端口实现成"永远不排除"。
    assert _bundle_excluded("app/integrated_app/.env", patterns)
    assert _bundle_excluded("config.yaml.bak", patterns)
    assert _bundle_excluded("app/integrated_app/data/x.db", patterns)


def test_exclude_guard_is_not_vacuous():
    """变异自证：加一条 ``*.woff2`` 排除，字体守卫必须立刻失败。"""
    patterns = _ps_string_array(
        (_SCRIPTS / "build_portable_bundle.ps1").read_text(encoding="utf-8"), "CoreExcludePatterns"
    )
    mutated = patterns + ["*.woff2"]
    assert any(_bundle_excluded(p, mutated) for p in _FONT_SAMPLES), "复演的排除语义太弱，抓不到显式排除"


# ---------------------------------------------------------------------------
# 守卫 4：CSS 引用与磁盘文件对账
# ---------------------------------------------------------------------------


def _css_urls(css: Path) -> list[str]:
    return re.findall(r"url\('([^']+)'\)", css.read_text(encoding="utf-8"))


def _url_target(url: str) -> Path:
    """/static/... 由应用挂到 app/integrated_app/static/，不是仓库根级的 static/。"""
    assert url.startswith("/static/"), f"字体 CSS 里出现非 /static/ 的绝对引用：{url}"
    return _STATIC / url[len("/static/") :]


def test_fonts_local_css_urls_resolve():
    """每个 CSS 里的 ``url()`` 都要有对应文件，且不允许留着没人引用的字体。

    覆盖 ``static/css/*.css`` 而非只 fonts.local.css：正文用的 Inter 由 variables.css 自己
    ``@font-face``，同样会"引用不到就静默回退"。
    """
    css_files = sorted((_STATIC / "css").glob("*.css"))
    urls_by_file = {c.name: _css_urls(c) for c in css_files}
    assert len(urls_by_file["fonts.local.css"]) >= 700, (
        f"fonts.local.css 只解析到 {len(urls_by_file['fonts.local.css'])} 个 url()，解析规则可能已失效"
    )
    all_urls = {u for urls in urls_by_file.values() for u in urls}
    dangling = sorted({u for u in all_urls if not _url_target(u).exists()})
    assert not dangling, f"CSS 引用了不存在的字体文件（浏览器静默回退，用户只会看到默认字体）：{dangling[:5]}"

    referenced = {Path(u).name for u in all_urls}
    shipped = sorted(p.name for p in list(_FONTS.glob("*.woff2")) + list(_FONTS.glob("*.ttf")))
    assert len(shipped) >= 700, f"随包字体只有 {len(shipped)} 个，扫描可能失效"
    orphans = [n for n in shipped if n not in referenced]
    assert not orphans, f"随包但无人引用的字体（白占体积）：{orphans[:5]}"


def test_font_license_text_covers_every_bundled_family():
    """每个随包字体家族都要有对应许可文本，否则声明表与实物不符。"""
    families = set(re.findall(r"font-family:\s*'([^']+)'", _FONTS_CSS.read_text(encoding="utf-8")))
    assert len(families) >= 12, f"只解析到 {len(families)} 个家族，解析可能失效"
    licenses = {p.name for p in (_FONTS / "licenses").glob("*.txt")}
    missing = sorted(f for f in families if not any(f.replace(" ", "").lower() in n.lower() for n in licenses))
    assert not missing, f"以下字体家族缺许可文本：{missing}"
