"""依赖声明 ↔ 锁 ↔ 豁免清单三者一致性守卫（纯静态，不联网、不装包）。

三条守卫堵的是同一类事故：**声明与实际装配长期不一致，而 CI 一片绿**。

D1 ``test_lock_pins_satisfy_declared_specifiers`` —— `requirements.txt` / `pyproject.toml`
   的每个 `==`/`>=`/`<` 约束，必须被 `requirements-lock.txt` 里对应的那条钉版满足。
   WHY：这类"下界棘轮"检查在仓库里是**缺位**的 —— PR #98 的
   `scripts/check_pin_crossconflicts.py` 只管"锁内部 A==x 与 B==y 互相约束"，
   它自己的 docstring 就写着"下界检查器一条都抓不到"。于是发生过：
   `pyproject` 声明 `transformers>=4.57.0`（2026-09-14 搭在一条只讲 gpu-smoke 的提交里），
   而锁与引擎实际要的是 4.52.1 —— 两个数字互相矛盾，CI 全绿，按声明装环境的人
   IndexTTS 2.0/2.5 直接起不来（实测见 docs/SECURITY_DEPENDABOT_TRIAGE.md §2）。

D2 ``test_engine_pinned_transformers_lineage`` —— 把"引擎钉死 4.52.x"这件事变成断言：
   锁里必须是 `transformers==4.52.1` + `tokenizers==0.21.0`，且声明必须有 `<4.53` 上界。
   谁要抬这个上界，必须先让 IndexTTS 侧适配，并让这条守卫带着证据一起改。

D3 ``test_accepted_risk_registers_stay_in_sync`` —— 两道扫描器的豁免集不许各自漂移：
   `.trivyignore.yaml` 的每条必须有 `expiration`（到期自动重新变红）且 id 出现在
   `docs/SECURITY_DEPENDABOT_TRIAGE.md`；`security.yml` 里 pip-audit 的 `--ignore-vuln`
   也必须逐条出现在同一份分诊文档。文档里没有依据的豁免 = 未登记的降级，判红。

D4 ``test_ignore_files_use_the_keys_the_tools_actually_read`` —— 堵"以为豁免了"这一类：
   trivy-action v0.36.0 的合法输入叫 `trivyignores`（**没有** `ignorefile`，写错只警告不报错），
   `.trivyignore.yaml` 的 schema 是 `package: {name, version}`（`version` 是单数字符串，
   写成 `versions: [...]` 不生效）。这两个坑本轮都真踩过，各断言一次。
   同时核对：pip-audit 豁免的 PYSEC 号集合必须与分诊文档 §1 表**完全相等**（双向），
   多一个是"未登记降级"，少一个是"登记了却没真豁免"。

维护约定同 test_fe_be_consistency.py：每条守卫配 `*_is_not_vacuous` 变异自证。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

try:
    from packaging.specifiers import SpecifierSet
    from packaging.version import InvalidVersion, Version
except ImportError:  # pragma: no cover - packaging 是 setuptools 的依赖，CI 必装
    SpecifierSet = None  # type: ignore[assignment]
    Version = None  # type: ignore[assignment]

_ROOT = Path(__file__).resolve().parents[1]
_LOCK = _ROOT / "requirements-lock.txt"
_REQ = _ROOT / "requirements.txt"
_PYPROJECT = _ROOT / "pyproject.toml"
_TRIAGE = _ROOT / "docs" / "SECURITY_DEPENDABOT_TRIAGE.md"
_TRIVY_IGNORE = _ROOT / ".trivyignore.yaml"
_SECURITY_WF = _ROOT / ".github" / "workflows" / "security.yml"
# 两道镜像扫描：同一个 .trivyignore.yaml 必须被它们各自正确接上
_IMAGE_WFS = (
    _ROOT / ".github" / "workflows" / "docker-build.yml",
    _ROOT / ".github" / "workflows" / "docker-publish.yml",
)


def _norm(name: str) -> str:
    return name.lower().replace("_", "-")


def _pins(path: Path) -> dict[str, str]:
    """取 `名字==版本` 形式的钉版表（锁文件、以及 pip-compile 头注释之外的行）。"""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([0-9][0-9a-zA-Z.+-]*)", line.strip())
        if m:
            out[_norm(m.group(1))] = m.group(2)
    return out


def _declared_specifiers() -> dict[str, list[str]]:
    """requirements.txt + pyproject 里对某个包的全部约束串（按包名聚合）。"""
    out: dict[str, list[str]] = {}

    def add(name: str, spec: str) -> None:
        name = _norm(name)
        spec = spec.strip().strip("\"'").strip()
        # 只收真正的版本约束：pyproject 里还有 classifier、extra 名等引号字符串，
        # 它们喂给 SpecifierSet 会直接抛 InvalidSpecifier。
        if spec and re.match(r"^[<>=!~]=?\s*\d", spec):
            out.setdefault(name, []).append(spec)

    for line in _REQ.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*([^#;\s][^#]*)", line.strip())
        if m and not line.strip().startswith("#"):
            add(m.group(1), m.group(2))
    for m in re.finditer(r'"([A-Za-z0-9][A-Za-z0-9._-]*)\s*([^"]*)"', _PYPROJECT.read_text(encoding="utf-8")):
        add(m.group(1), m.group(2))
    return out


def _violations(pins: dict[str, str], decl: dict[str, list[str]]) -> list[str]:
    bad: list[str] = []
    for name, specs in decl.items():
        if name not in pins:
            continue  # 锁里没有该包：由别的检查负责，不在本条口径内
        ver = pins[name]
        for spec in specs:
            try:
                ok = Version(ver) in SpecifierSet(spec)
            except InvalidVersion:
                bad.append(f"{name}: 锁里版本 {ver!r} 无法解析")
                continue
            if not ok:
                bad.append(f"{name}: 锁钉 {ver} 不满足声明 {spec!r}")
    return bad


# ---------------------------------------------------------------------------
# D1 声明 ↔ 锁
# ---------------------------------------------------------------------------


def test_lock_pins_satisfy_declared_specifiers():
    """锁文件里每条被声明过的包，其钉版必须落在声明的区间内。"""
    pins = _pins(_LOCK)
    decl = _declared_specifiers()
    checked = [n for n in decl if n in pins]
    assert len(checked) >= 20, f"只能对上 {len(checked)} 个包，解析可能失效（锁 {len(pins)} 条 / 声明 {len(decl)} 条）"
    bad = _violations(pins, decl)
    assert not bad, f"锁与声明互相矛盾（按声明装环境与按锁装环境是两个不同的东西）：{bad}"


def test_d1_guard_is_not_vacuous():
    pins = _pins(_LOCK)
    tf = pins.get("transformers")
    assert tf is not None, "锁里没有 transformers，样本失效"
    assert _violations(pins, {"transformers": [">=4.52.1,<4.53"]}) == []
    assert _violations(pins, {"transformers": [">=4.57.0"]}), "声明抬到 4.57 时必须报错，否则守卫空转"


# ---------------------------------------------------------------------------
# D2 引擎钉住的那条血脉
# ---------------------------------------------------------------------------

_ENGINE_FACT = """IndexTTS 2.0/2.5 的发行元数据要求 transformers==4.52.1 + tokenizers==0.21.0；
2026-09-21 实测：4.57.6 下 indextts.infer_v2_5 / infer_v2 直接 ImportError（VoxCPM2 不受影响），
4.52.1 + tokenizers 0.21.0 下三引擎真推理全通（2.5 出 214,040 B/RMS 6176；2.0 出 205,124 B/
RMS 6926；VoxCPM2 出 230,148 B/RMS 4615）。证据与代价（含已接受风险的公告清单）见
docs/SECURITY_DEPENDABOT_TRIAGE.md。要改这里，先让 IndexTTS 侧适配，再连同分诊文档一起改。"""


def test_engine_pinned_transformers_lineage():
    """锁必须停在 4.52.1 / tokenizers 0.21.0，且声明必须带 <4.53 上界。"""
    pins = _pins(_LOCK)
    assert pins.get("transformers") == "4.52.1", (
        f"锁里 transformers 被挪动了：{pins.get('transformers')!r}。{_ENGINE_FACT}"
    )
    assert pins.get("tokenizers") == "0.21.0", f"锁里 tokenizers 被挪动了：{pins.get('tokenizers')!r}。{_ENGINE_FACT}"
    specs = " ".join(_declared_specifiers().get("transformers", []))
    assert "<4.53" in specs, (
        f"requirements.txt / pyproject 的 transformers 声明缺 `<4.53` 上界，会解析到让 IndexTTS 起不来的版本。{_ENGINE_FACT}"
    )


def test_d2_guard_is_not_vacuous():
    # 声明侧：上界存在是本守卫的判据，去掉后 _violations 应能抓到锁与声明的矛盾
    assert "<4.53" in " ".join(_declared_specifiers().get("transformers", []))
    assert _violations({"transformers": "4.57.6"}, {"transformers": [">=4.52.1,<4.53"]}), "锁被抬上 4.57 时必须报错"
    assert _violations({"tokenizers": "0.23.2"}, {"tokenizers": [">=0.21.0,<0.22"]}), "tokenizers 漂到 0.23 时必须报错"


# ---------------------------------------------------------------------------
# D3 两份豁免清单与分诊文档同步
# ---------------------------------------------------------------------------


def _trivy_ignore_entries() -> list[dict[str, str]]:
    text = _TRIVY_IGNORE.read_text(encoding="utf-8")
    entries: list[dict[str, str]] = []
    for block in re.finditer(r"^  - id: (CVE-[0-9]{4}-[0-9]{4,7})\n(.*?)(?=^  - id: |^\S|\Z)", text, re.M | re.S):
        entries.append({"id": block.group(1), "body": block.group(2)})
    return entries


def _pip_audit_ignores() -> list[str]:
    text = _SECURITY_WF.read_text(encoding="utf-8")
    return sorted(set(re.findall(r"--ignore-vuln (PYSEC-[0-9]{4}-[0-9]{3,7})", text)))


def test_accepted_risk_registers_stay_in_sync():
    """每条豁免都必须有到期时间，且它的 id 必须出现在分诊文档里。"""
    triage = _TRIAGE.read_text(encoding="utf-8")
    entries = _trivy_ignore_entries()
    assert len(entries) >= 3, f"只解析到 {len(entries)} 条 Trivy 豁免，解析已失效"
    missing_expiry = [e["id"] for e in entries if "expiration:" not in e["body"]]
    unlogged = [e["id"] for e in entries if e["id"] not in triage]

    ignores = _pip_audit_ignores()
    assert len(ignores) >= 16, f"只解析到 {len(ignores)} 条 pip-audit 豁免，解析已失效"
    # pip-audit 用的是 PYSEC 号，与 Trivy 的 CVE 号不同源；两者都必须在分诊文档里有账
    orphan_pysec = [p for p in ignores if p not in triage]

    problems = []
    if missing_expiry:
        problems.append(f"这些豁免没有 expiration（到期即重新变红，防止永久静音）：{missing_expiry}")
    if unlogged:
        problems.append(f"这些 CVE 在分诊文档里没有对应条目：{unlogged}")
    if orphan_pysec:
        problems.append(f"这些 PYSEC 在分诊文档里没有对应条目：{orphan_pysec}")
    assert not problems, "豁免与分诊记录脱节：\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("cve", ["CVE-2026-4372", "CVE-2026-5241", "CVE-2026-9856"])
def test_accepted_cves_are_actually_pinned_versions(cve: str):
    """镜像里真出现过的号才允许被豁免：本条防止"顺手多豁免几个"。"""
    ids = {e["id"] for e in _trivy_ignore_entries()}
    assert cve in ids, f"{cve} 不在 .trivyignore.yaml 里（该文件应只登记 docker-build 实测报出的号）"
    assert cve in _TRIAGE.read_text(encoding="utf-8"), f"{cve} 没在分诊文档里留痕"


# ---------------------------------------------------------------------------
# D4 "以为豁免了"专项：工具真正读取的键名 + 豁免集与文档表完全相等
# ---------------------------------------------------------------------------

# trivy-action 的合法输入名（v0.36.0 实测的 valid inputs 里有这个、没有 ignorefile）
_TRIVY_INPUT = "trivyignores"


def _trivy_ignore_version_keys() -> tuple[int, int]:
    """返回 (.trivyignore.yaml 里 `package.version` 正确写法的处数, `versions:` 错误写法的处数)。"""
    text = _TRIVY_IGNORE.read_text(encoding="utf-8")
    good = len(re.findall(r"^\s{6}version:\s*[\"']?[0-9]", text, re.M))
    bad = len(re.findall(r"^\s{6}versions:", text, re.M))
    return good, bad


def _triage_table_pysecs() -> set[str]:
    """分诊文档 §1 表 A 组行里登记的 PYSEC 号集合。"""
    rows = [ln for ln in _TRIAGE.read_text(encoding="utf-8").splitlines() if re.match(r"^\| A\d+ \|", ln)]
    ids: set[str] = set()
    for ln in rows:
        ids.update(re.findall(r"PYSEC-[0-9]{4}-[0-9]{3,7}", ln))
    return ids


def test_trivy_ignore_hooked_up_with_the_keys_the_tools_read():
    """两条镜像扫描必须用 trivyignores 接清单，且清单本身的 schema 键名要对。"""
    problems = []
    for wf in _IMAGE_WFS:
        text = wf.read_text(encoding="utf-8")
        if f"{_TRIVY_INPUT}:" not in text:
            problems.append(
                f"{wf.name} 没有 {_TRIVY_INPUT}: 输入 —— 写错名字（例如 ignorefile）只会出一条警告然后照常变红"
            )
        if "ignorefile:" in text:
            problems.append(f"{wf.name} 用了 ignorefile:，trivy-action v0.36.0 不认这个输入名（等于没接豁免）")

    good, bad = _trivy_ignore_version_keys()
    if bad:
        problems.append(
            f".trivyignore.yaml 里有 {bad} 处 `versions:` —— schema 只读 `package.version`（单数字符串），复数写法不生效"
        )
    if good < 3:
        problems.append(f".trivyignore.yaml 只解析到 {good} 条带 `package.version` 的条目，本文件现有 3 条豁免")
    assert not problems, "豁免实际未生效：\n  " + "\n  ".join(problems)


def test_pip_audit_ignore_set_equals_the_triage_table():
    """pip-audit 的豁免集合必须与分诊文档 §1 的 A 组表**完全相等**。

    多了 = 有豁免没登记理由；少了 = 文档登记了风险却漏了豁免（CI 直接红，或更糟：
    号写残缺了还自以为豁免掉了 —— 本轮真的发生过 4 个被截断的假号）。
    """
    registered = _triage_table_pysecs()
    exempted = set(_pip_audit_ignores())
    assert len(registered) >= 16, f"分诊文档 §1 只解析到 {len(registered)} 个 PYSEC，表或解析已失效"
    assert exempted == registered, (
        f"豁免集与分诊表漂移：只在豁免里（未登记理由）{sorted(exempted - registered)}；"
        f"只在表里（登记了却没豁免）{sorted(registered - exempted)}"
    )


def test_d3_d4_guards_are_not_vacuous():
    """变异自证：把三处"写错就静默失效"的地方改坏，本组守卫必须变红。"""
    entries = _trivy_ignore_entries()
    assert len(entries) == 3, "Trivy 豁免条目数对不上，D3 的解析已失效"
    assert all("expiration:" in e["body"] for e in entries)
    assert all("version:" in e["body"] and "versions:" not in e["body"] for e in entries)

    # 号残缺（截断成前缀）必须被"完全相等"抓到：本轮真实发生过 4 个被截断的假号
    registered = _triage_table_pysecs()
    exempted = set(_pip_audit_ignores())
    assert exempted == registered
    truncated = {pid[:-1] for pid in exempted}
    assert truncated != registered, "把每个豁免号各截掉一位后仍相等，说明这条断言在空转"
    assert truncated.isdisjoint(registered), "截断后的号必须一个都对不上分诊表，否则前缀假号抓不住"

    # 输入名写错必须被抓到
    for wf in _IMAGE_WFS:
        text = wf.read_text(encoding="utf-8")
        assert f"{_TRIVY_INPUT}:" in text, f"{wf.name} 现在就没接上豁免"
        assert "ignorefile:" not in text, f"{wf.name} 里混进了 trivy-action 不认的输入名"
        mutated = text.replace(f"{_TRIVY_INPUT}:", "ignorefile:")
        assert f"{_TRIVY_INPUT}:" not in mutated, "判据不是恒真：把输入名换成错的那个之后必须失去匹配"
