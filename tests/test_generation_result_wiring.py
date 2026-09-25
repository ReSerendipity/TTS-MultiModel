"""生成结果渲染路径接线守卫（UPSTREAM_SYNC 落地 #18）。

AGENTS.md 自检清单里那条"改了生成结果渲染路径，htmx 与 SSE 两条路径是否都调用
``window.wireGenerationResult()``"不是形式主义：漏接的表现是**合成成功但后处理区
不出现、"保存为音色"被判缺少音频**，服务端一切正常、不报错。

本文件把这条人工核对变成机器判据：从模板里算出"哪些页面真的需要接线"
（既声明了 ``-pp-section`` / ``-result-audio``，又把结果 swap 进 ``-result`` 容器），
再核对 JS 钩子是否覆盖到它们。历史上钩子只认硬编码的 ``vd-result``，
于是 ultimate_clone / script / prompt_continue 三个页面静默失效。
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TABS = _REPO / "app" / "integrated_app" / "templates" / "tabs"
_APP_INIT = _REPO / "app" / "integrated_app" / "static" / "js" / "app_init.js"

_TARGET_RE = re.compile(r'hx-target="#([a-z0-9_]+-result)"')
_NEEDS_RE = re.compile(r"([a-z0-9_]+)-pp-section|([a-z0-9_]+)-result-audio")


def _tabs_swapping_into_result_container() -> dict[str, list[str]]:
    """返回 {tab 文件相对名: [结果容器 id, ...]}。"""
    found: dict[str, list[str]] = {}
    for tpl in sorted(_TABS.glob("*.html")):
        ids = _TARGET_RE.findall(tpl.read_text(encoding="utf-8"))
        if ids:
            found[tpl.name] = sorted(set(ids))
    return found


def _tabs_needing_wiring() -> dict[str, list[str]]:
    """只保留"确实有东西要接"的 tab：同文件里既出现 pp-section/result-audio，
    又出现指向 ``-result`` 容器的 hx-target。"""
    out: dict[str, list[str]] = {}
    for name, targets in _tabs_swapping_into_result_container().items():
        text = (_TABS / name).read_text(encoding="utf-8")
        if _NEEDS_RE.search(text):
            out[name] = targets
    return out


class TestHookCoversEveryNeedingTab:
    def test_hook_is_suffix_based_not_a_single_hardcoded_id(self) -> None:
        js = _APP_INIT.read_text(encoding="utf-8")
        assert "wireGenerationResult" in js
        # 钩子必须按 -result 后缀放行；写死单个 id 就是本次要拦的回归
        assert re.search(r"target\.id\s*!==?\s*'[a-z0-9]+-result'", js) is None, (
            "htmx:afterSwap 钩子里出现了写死的单个结果容器 id，其他 tab 会静默失去接线"
        )
        assert re.search(r"/-result\$/.test\(target\.id\)", js), "钩子不再按 -result 后缀匹配"

    def test_every_needing_tab_prefix_is_wired(self) -> None:
        js = _APP_INIT.read_text(encoding="utf-8")
        needs = _tabs_needing_wiring()
        assert needs, "没找到任何需要接线的 tab，本断言会空转（模板结构变了？）"
        suffix_based = re.search(r"/-result\$/.test\(target\.id\)", js) is not None
        for tab, targets in needs.items():
            for target in targets:
                covered = suffix_based or target in js
                assert covered, f"{tab} 的结果容器 #{target} 有 pp-section/result-audio，但没人调 wireGenerationResult"

    def test_voice_design_and_voice_clone_still_self_wire(self) -> None:
        """这两页走 SSE 手工解帧，不经过 htmx:afterSwap，必须保留各自显式调用。"""
        for tab in ("voice_design.html", "voice_clone.html"):
            text = (_TABS / tab).read_text(encoding="utf-8")
            assert "wireGenerationResult" in text, f"{tab} 的 SSE 路径显式接线被删了"


class TestWiringFunctionKeepsItsOwnGuards:
    def test_bails_out_without_result_suffix(self) -> None:
        js = _APP_INIT.read_text(encoding="utf-8")
        assert "/-result$/.test(rootEl.id)" in js, "函数自校验没了：错误片段会被误接线"

    def test_bails_out_without_audio_filename(self) -> None:
        js = _APP_INIT.read_text(encoding="utf-8")
        assert "data-audio-filename" in js


class TestOutputFilenameNamespacing:
    """不同端点共写一个输出目录，文件名必须自带前缀，否则互相覆盖。"""

    def test_each_generate_surface_uses_its_own_prefix(self) -> None:
        roots = [
            _REPO / "app" / "integrated_app" / "routes" / "generate",
        ]
        prefixes: set[str] = set()
        pattern = re.compile(r'f"([a-z_0-9]+)_\{(?:int\()?time')
        for root in roots:
            for py in root.rglob("*.py"):
                prefixes.update(pattern.findall(py.read_text(encoding="utf-8")))
        assert len(prefixes) >= 3, f"端点文件名前缀过少（{sorted(prefixes)}），可能共用同一命名空间"
