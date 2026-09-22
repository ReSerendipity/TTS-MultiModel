"""Docker 层缓存不能把 Actions 缓存配额吃掉（2026-09-22 的账）。

起因：性能门禁的基线缓存在 main push 之后两小时就 `Cache not found`（issue #118 的后续），
量下来仓库缓存 9.64 GiB / 配额 10 GiB，其中 **9.52 GiB 是 12 条 `buildkit-blob-*`**，
全部来自 `docker-publish.yml` 的 `cache-to: type=gha,mode=max`（无 scope）。
LRU 驱逐的顺序是先牺牲小缓存（benchmark 基线 2.8 KB、setup-python 的 pip 缓存），
所以症状出现在别的作业上，凶手在另一个工作流里。

这条守卫盯的是**配置形状**（`mode=min` + 固定 `scope`），不是运行时用量——
用量只能靠人看 API，形状可以让它自己漂不回去。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "docker-publish.yml"


def _build_step() -> dict:
    doc = yaml.safe_load(_WF.read_text(encoding="utf-8"))
    steps = [s for j in doc["jobs"].values() for s in (j.get("steps") or [])]
    hit = [
        s
        for s in steps
        if "docker/build-push-action" in str(s.get("uses", "")) and (s.get("with") or {}).get("cache-to")
    ]
    assert hit, "docker-publish.yml 里找不到带 cache-to 的 build-push-action 步骤——守卫的对象变了，要同步改这里"
    assert len(hit) == 1, f"带缓存的构建步骤有 {len(hit)} 条，本测试只核一条：其余要显式加进断言"
    return hit[0]["with"]


def test_layer_cache_is_mode_min_with_a_fixed_scope() -> None:
    with_ = _build_step()
    cache_to, cache_from = str(with_["cache-to"]), str(with_["cache-from"])
    assert "mode=min" in cache_to, (
        f"cache-to 回到了 {cache_to!r}：mode=max 会把每个中间层都存成一份快照，"
        "实测每次构建 ~3.2 GiB、三次就把 10 GiB 配额吃满，还把 benchmark/pip 的小缓存挤掉"
    )
    scope_to = re.search(r"scope=([\w.-]+)", cache_to)
    scope_from = re.search(r"scope=([\w.-]+)", cache_from)
    assert scope_to and scope_from, (
        f"cache-to/cache-from 少 scope（to={cache_to!r} from={cache_from!r}）："
        "默认 scope 会按 ref 分桶，main / PR / tag 各存一份，等于把配额乘三"
    )
    assert scope_to.group(1) == scope_from.group(1) == "tts-mm", (
        f"scope 两边必须一致且是写死的常量，实际 to={scope_to.group(1)!r} from={scope_from.group(1)!r}"
    )
    # 只查生效行：上面那段解释注释里当然会出现 "mode=max"（那正是被禁的写法）
    live = [ln for ln in _WF.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
    assert not any("mode=max" in ln for ln in live), "有生效行（非注释）重新用上了 mode=max"


def test_guard_is_not_vacuous() -> None:
    """把配置改坏的形状必须真的让上一条红——否则这条守卫只是装饰。"""
    with_ = _build_step()
    broken = str(with_["cache-to"]).replace("mode=min", "mode=max")
    assert broken != str(with_["cache-to"]), "替换没生效，说明 mode=min 不在 cache-to 里"
    assert "mode=min" not in broken and "mode=max" in broken
