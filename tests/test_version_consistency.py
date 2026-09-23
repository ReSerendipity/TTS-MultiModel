"""Version consistency basic test (family simplified).

Verify:
- Version number follows SemVer (x.y.z)
- No hardcoded old version (0.1.0 / 0.0.1)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _find_version_file():
    candidates = [
        "package.json",
        "pyproject.toml",
        "version.json",
        "gradle.properties",
    ]
    for c in candidates:
        if (PROJECT_ROOT / c).exists():
            return c
    return None


def test_version_file_exists():
    assert _find_version_file() is not None


def test_version_is_semver():
    vfile = _find_version_file()
    assert vfile

    content = (PROJECT_ROOT / vfile).read_text(encoding="utf-8", errors="ignore")

    if vfile == "package.json":
        data = json.loads(content)
        version = data.get("version", "")
    elif vfile == "pyproject.toml":
        m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
        version = m.group(1) if m else ""
    elif vfile == "version.json":
        data = json.loads(content)
        version = data.get("version", "")
    elif vfile == "gradle.properties":
        m = re.search(r"versionName\s*=\s*([^\n]+)", content)
        version = m.group(1).strip() if m else ""
    else:
        version = ""

    assert re.fullmatch(r"\d+\.\d+\.\d+", version)


def test_no_hardcoded_old_version():
    old_patterns = [r'version\s*=\s*["\']0\.1\.0["\']', r'version\s*=\s*["\']0\.0\.1["\']']

    offenders = []
    for pyfile in PROJECT_ROOT.rglob("*.py"):
        if any(skip in str(pyfile) for skip in [".venv", "__pycache__", "node_modules"]):
            continue
        try:
            content = pyfile.read_text(encoding="utf-8", errors="ignore")
            for pat in old_patterns:
                if re.search(pat, content):
                    offenders.append(str(pyfile.relative_to(PROJECT_ROOT)))
                    break
        except Exception:
            pass

    assert offenders == []


# ---------------------------------------------------------------------------
# 版本位一致性：release-governance §1 说"版本号出现在 11 处"，但 §5 的那格
# "[ ] 版本位全部同步" 一直是**手工勾选**。v2.2.2 发版前实测就有真漂移（`deploy/kubernetes
# /deployment.yaml` 的镜像 tag 还停在 2.2.1）。手工勾选的清单项在无人复看时会漂，故上闸。
# 注：`desktop/package-lock.json` 也在 .gitignore 里（第 196 行），不是仓库内的版本位，故不列。
# ---------------------------------------------------------------------------

_SEMVER = re.compile(r"\d+\.\d+\.\d+")

#: release-please 通过 `release-please-config.json` 的 extra-files 会自动抬的版本位。
#: 键与本文件 `_site_versions()` 的键一致；改 config 时同步改这里，否则失败信息会指错方向。
#:
#: `config.yaml` **不在**这里，而且不是遗漏：release-please 的 `yaml` 写入器会整份重排
#: 文档，第一次真跑（PR #120，2026-09-22）就把 232 行配置改成了 160 增/160 删，
#: 注释行从 96 行变成 0 行（`model_source_mode` 的选型说明、SSL 开关怎么打开、
#: `vram_safety_margin_gb` 的算式），并把 `"127.0.0.1"` 的引号去掉。
#: 同一条 PR 上 json/toml 那 4 条各只动 1 行 —— 破坏性是 `yaml` 类型特有的。
#: 见 `test_extra_files_entries_are_all_actionable` 里的类型白名单。
_RP_MANAGED = {
    "pyproject.toml",
    "version.json",
    "desktop/package.json",
    "desktop/src-tauri/tauri.conf.json",
    "desktop/src-tauri/Cargo.toml",
}


def _ver_tuple(s: str) -> tuple[int, ...]:
    """x.y.z → 可比较的整数元组（避免字典序把 2.9 判成比 2.10 大）。"""
    return tuple(int(p) for p in s.split("."))


def _site_versions() -> dict[str, str]:
    """从各版本位精确取值；取不到就抛，避免"少一个站点"被当成"一致"。"""

    def grab(rel: str, pattern: str, label: str) -> str:
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        m = re.search(pattern, text, re.M)
        if not m:
            raise AssertionError(f"{label}（{rel}）取不到版本号，正则或文件已失效：{pattern!r}")
        return m.group(1)

    def grab_json(rel: str, label: str) -> str:
        data = json.loads((PROJECT_ROOT / rel).read_text(encoding="utf-8"))
        if "version" not in data:
            raise AssertionError(f"{label}（{rel}）没有 version 字段")
        return str(data["version"])

    sites = {
        "pyproject.toml": grab("pyproject.toml", r'^version\s*=\s*"([^"]+)"', "Python 包版本"),
        "version.json": grab_json("version.json", "更新契约版本（唯一版本源）"),
        "config.yaml": grab("config.yaml", r'^version:\s*"?([^"\n]+)"?', "前端缓存参数版本"),
        "desktop/package.json": grab_json("desktop/package.json", "桌面壳 npm 版本"),
        "desktop/src-tauri/tauri.conf.json": grab_json("desktop/src-tauri/tauri.conf.json", "Tauri 壳版本"),
        "desktop/src-tauri/Cargo.toml": grab(
            "desktop/src-tauri/Cargo.toml",
            r"\[package\][\s\S]*?^version\s*=\s*\"([^\"]+)\"",
            "Rust 包版本",
        ),
        "desktop/src-tauri/Cargo.lock": grab(
            "desktop/src-tauri/Cargo.lock",
            r'name = "tts-multimodel-desktop"\nversion = "([^"]+)"',
            "Rust 锁内自身版本",
        ),
        "scripts/installer/setup.nsi": grab(
            "scripts/installer/setup.nsi", r'!define APP_VERSION "([^"]+)"', "安装器版本"
        ),
        "deploy/kubernetes/deployment.yaml": grab(
            "deploy/kubernetes/deployment.yaml",
            r"image:\s*ghcr\.io/[A-Za-z0-9._/-]+:([0-9][^\s\"]*)",
            "k8s 镜像 tag",
        ),
        # 第 10 处：文档里那句"已发布最新"。它以前只是散文，于是会这样漂 ——
        # v2.2.5 由 release-please 发出去之后，这行还停在 v2.2.4（RP 不碰散文，手工补齐时也没带上它）。
        "docs/release-governance.md": grab(
            "docs/release-governance.md",
            r"\*\*已发布最新 = v(\d+\.\d+\.\d+)",
            "治理文档声称的最新已发布版本",
        ),
    }
    # 安装器**内嵌**的那份版本：`setup.nsi` 的 `File "version.json"` 取的是
    # `scripts/installer/version.json`，而它是 .gitignore:436 明写的"装配中间物"
    # （与 TTSMultiModel.exe 同批手工放进去，makensis 不在仓库里跑）—— 真版本源只有根
    # version.json。所以不变式是**若在场则必须等于源**：干净检出里没有这个文件（CI 就没有），
    # 可一旦装进安装器的那份与源不同步，装出来的壳就拿一份旧版本号做本地识别
    # （assemble_installer_data.ps1:59 的注释写着"缺失会静默跳过更新"）。
    installer_copy = PROJECT_ROOT / "scripts" / "installer" / "version.json"
    if installer_copy.exists():
        sites["scripts/installer/version.json（装配中间物）"] = grab_json(
            "scripts/installer/version.json", "安装器内嵌版本（壳本地识别）"
        )
    return sites


def test_all_version_sites_agree() -> None:
    sites = _site_versions()
    assert len(sites) >= 9, f"只核到 {len(sites)} 个版本位，本条已失去意义"
    bad = {k: v for k, v in sites.items() if not _SEMVER.fullmatch(v)}
    assert not bad, f"这些版本位不是 x.y.z 形态：{bad}"
    distinct = set(sites.values())
    assert len(distinct) == 1, (
        "版本位互相矛盾（release-governance §5 的『版本位全部同步』没做到）：\n  "
        + "\n  ".join(
            f"{k} = {v}{'  ← RP 自动' if k in _RP_MANAGED else '  ← 手工同步'}" for k, v in sorted(sites.items())
        )
        + "\n  release-please 只会改上面标『RP 自动』的那些（它只支持 json/toml/yaml/xml/pom/generic，"
        "没有 regex，且 yaml 写入器会整份重排、把注释洗掉，所以 config.yaml 也不在里面）；"
        "标『手工同步』的必须在 release PR 上补一个 commit —— "
        "Cargo.lock 归 cargo 生成、setup.nsi 的注释符是 `;` 用不了 generic 的 `# x-release-please-version` 标记、"
        "k8s 镜像 tag 要跟 ghcr 上真存在的标签走、安装器那份是 gitignore 的装配中间物。"
    )


def test_installer_artifact_names_track_the_version_site() -> None:
    """OutFile / VIProductVersion 与 APP_VERSION 必须同源，否则装出来的包自称一个版本、
    文件名叫另一个版本（`version.json` 的 min_shell_version 比对就失去意义）。"""
    sites = _site_versions()
    # 基准取 pyproject，别用 next(iter(set(sites.values())))：版本位互相矛盾时那是在**随机挑一个**
    # 当真值，setup.nsi 可能恰好撞上被挑中的那个而判过（2026-09-22 用假工作树撞通过一次）。
    ver = sites["pyproject.toml"]
    text = (PROJECT_ROOT / "scripts" / "installer" / "setup.nsi").read_text(encoding="utf-8", errors="ignore")
    assert f"TTSMultiModel-Setup-v{ver}.exe" in text, f"OutFile 还没跟到 v{ver}"
    assert f'VIProductVersion "{ver}.0"' in text, f"VIProductVersion 还没跟到 {ver}.0"
    data = json.loads((PROJECT_ROOT / "version.json").read_text(encoding="utf-8"))
    minimum = str(data.get("minimum_shell_version", ""))
    assert _SEMVER.fullmatch(minimum), f"minimum_shell_version 不是 x.y.z 形态：{minimum!r}"
    # 它是**下界**，不是"必须等于当前版本"：语义是"低于它的壳不接受了"。
    # 强令相等等于每次发版都把上一版壳判死（而目前代码里还没人真的读它：
    # updater.rs 只在 AppVersion 结构里解析它，shell-update.json 契约里没有这个字段），
    # 所以这里只钉住唯一站得住的关系：下界不得高于本次版本。
    assert _ver_tuple(minimum) <= _ver_tuple(ver), f"minimum_shell_version={minimum} 高于本次版本 {ver}"


def _extra_files_entries() -> list[dict]:
    cfg = json.loads((PROJECT_ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    return list(cfg.get("extra-files") or [])


def test_rp_managed_annotation_matches_the_actual_config() -> None:
    """`_RP_MANAGED` 只是给失败信息指路用的，它自己不能漂：必须与
    release-please-config.json 的 extra-files + pyproject（python release-type 自带）一致。"""
    listed = {str(e.get("path")) for e in _extra_files_entries()}
    assert listed, "config 里一个 extra-files 都没有，那这条闸就没意义了"
    assert listed | {"pyproject.toml"} == _RP_MANAGED, (
        f"RP 自动位与测试里的标注不一致：config 有 {sorted(listed)}，标注多/少的部分是"
        f" {sorted((listed | {'pyproject.toml'}) ^ _RP_MANAGED)}"
    )


def test_extra_files_entries_are_all_actionable() -> None:
    """每一条 extra-files 都必须"真的能命中"，否则 release-please 会在无人察觉时少抬一处版本位。

    三件事都在这里钉住：
      1. 类型白名单只放 `json|toml|generic`。`yaml` 被排除是有账的：它在 PR #120（2.2.4）
         上把 `config.yaml` 的 232 行改写成 160 增/160 删，注释行 96 → 0 —— 静默、且每次都发生。
      2. `json|toml`：按 jsonpath 走进目标文件，取到的值必须**当前就是那个版本号**
         （写错 key、文件搬家、字段改名都会在这里红，而不是在发版当天发现）。
      3. `generic`：那一行必须带着 `x-release-please-version` 标记，它是行内匹配 ——
         标记一旦丢（`routes/system/settings.py` 的 `_save_yaml_raw` 用 safe_dump 整体重写
         config.yaml，注释必然消失），版本位就再也不动。
    """
    entries = _extra_files_entries()
    assert entries, "config 里没有 extra-files，本条闸没有对象"
    ver = _site_versions()["pyproject.toml"]

    unsupported = [e for e in entries if str(e.get("type")) not in {"json", "toml", "generic"}]
    assert not unsupported, (
        "extra-files 用了没在 PR 上验过的类型："
        + str([(e.get("path"), e.get("type")) for e in unsupported])
        + " —— release-please 的 yaml 写入器会整份重排文档并删掉注释（实测见本文件 "
        "_RP_MANAGED 上方的账），只允许 json/toml/generic。"
    )

    pending: list[str] = []
    sites = _site_versions()
    for entry in entries:
        rel, type_, path = str(entry["path"]), str(entry["type"]), str(entry.get("jsonpath") or "")
        target = PROJECT_ROOT / rel
        if not target.exists():
            pending.append(f"{rel}：文件不存在")
            continue
        if type_ == "generic":
            body = target.read_text(encoding="utf-8", errors="ignore")
            hit = [ln for ln in body.splitlines() if "x-release-please-version" in ln and _SEMVER.search(ln)]
            if not hit:
                pending.append(
                    f"{rel}：generic 要求同一行内既有 `x-release-please-version` 标记又有一个 x.y.z，两者都没找到"
                )
            continue
        value = _jsonpath_value(target, path, type_)
        if value == _NO_PARSER:
            # Python 3.10 没有 tomllib（CI 矩阵里就有 3.10），这一位只能交给
            # `test_all_version_sites_agree` 的读取器去核 —— 它对 Cargo.toml 是覆盖到的；
            # 连站点读取器都没有（下表没这一行）就真的没人管了，那种情况要响。
            if rel not in sites:
                pending.append(
                    f"{rel}：本解释器读不了 {type_}（无 tomllib），且 {rel} 不在 _site_versions() 里 —— 没人核对这一位"
                )
            continue
        if isinstance(value, str) and value.startswith("__unread__"):
            pending.append(f"{rel}：jsonpath {path} 走不到（{value}）")
        elif str(value) != ver:
            pending.append(
                f"{rel}：jsonpath {path} 现在是 {value!r}，与 canonical {ver!r} 不等 —— 它不会被抬到本次版本"
            )
        if rel in sites and sites[rel] != ver:
            pending.append(f"{rel}：本文件另一个读取器看到的是 {sites[rel]!r}，与 canonical 不一致")
    assert not pending, "extra-files 有命中不了的条目：\n  " + "\n  ".join(pending)


def _exclude_paths_entries() -> list[str]:
    """`packages["."].exclude-paths` —— 故意读包内那一层，不读顶层同名键。

    RP 的 `CommitExclude` 构造器吃的是 `Record<packagePath, {excludePaths}>`，
    包内那层一定是生效的；顶层那层是否被合并进包配置我没在 RP 源码里读到确证，
    写在那里就会变成"看起来配了、其实没生效"。
    """
    cfg = json.loads((PROJECT_ROOT / "release-please-config.json").read_text(encoding="utf-8"))
    pkg = (cfg.get("packages") or {}).get(".") or {}
    return [str(p) for p in (pkg.get("exclude-paths") or [])]


#: RP 匹配排除目录的方式（`src/util/commit-exclude.ts`，2026-09-22 读源码确认）：
#: `file.indexOf(`${path}/`) === 0` —— 前缀比对，**不是 glob**。
_EXCLUDE_GLOB_CHARS = re.compile(r"[*?\[\]]")


def test_exclude_paths_entries_are_all_actionable() -> None:
    """每一条 exclude-paths 都必须"今天真的会排掉提交"，否则它就是一行装饰。

    起因是实测：#136（两个 `docs(release):` 提交、零代码改动）合并后 25 秒，
    RP 就开出了 `chore(main): release 2.2.6`（CHANGELOG 段是 3 条 `### Documentation`）——
    纯文档改动会被当成一次可发布的版本，而每条这种版本都要人补 5 类手工同步位。
    于是加了 `exclude-paths: ["docs", "tests"]`。这条闸钉住它别写错，因为这里最容易写错：

      1. **不是 glob**。写 `docs/**` 或 `docs/**/*.md` 一点都不会生效 ——
         匹配式是 `file.startsWith(entry + "/")`，`docs/**/` 永远不是任何文件路径的前缀。
         RP 自己的测试用的也是裸目录名（`test/util/commit-exclude.ts` 里是 `['pkg3','pkg1']`）。
      2. **排不掉仓库根上的单个文件**：同样的 `+"/"` 规则让 `README.md`、`CHANGELOG.md`、
         `release-please-config.json` 这类根文件做不成条目（所以下面要求条目必须是真实目录）。
      3. **"." 会把所有提交都排掉** → RP 永远不开 release PR，而且**不会报错**，
         正是本仓踩过的"永远绿却什么都不做"那个形状（见 §1 的 v2.2.2 旧账）。
      4. 排掉的目录里不能有任何 RP 要写的版本位或随包分发的代码：那才是真会漏东西的地方。
    """
    entries = _exclude_paths_entries()
    assert entries, (
        "exclude-paths 空了。今天的行为已经实测过：纯文档合并会在 25 秒内开出一条 "
        "release PR（#136 → #137 / 2.2.6），所以要么把它加回来，要么连同 "
        "docs/release-governance.md §1 第 5 条一起改口径 —— 别只删配置。"
    )

    dead: list[str] = []
    for entry in entries:
        if entry in ("", ".", "/"):
            dead.append(f"{entry!r}：会把仓库根当目录，等于排掉所有提交（RP 不报错，只是再也不发版）")
            continue
        if _EXCLUDE_GLOB_CHARS.search(entry) or entry.endswith("/") or entry.startswith("/"):
            dead.append(
                f"{entry!r}：带 glob/斜杠。RP 的匹配是 `file.startsWith(entry + '/')`，"
                "`docs/**` 这种写法永远命中不了 —— 要用裸目录名 `docs`"
            )
            continue
        target = PROJECT_ROOT / entry
        if not target.is_dir():
            dead.append(f"{entry}：不是仓库里的目录（根级单文件排不掉，见 `isRelevant` 的 `+'/')")
            continue
        files = [p for p in target.rglob("*") if p.is_file() and ".git" not in p.parts]
        if not files:
            dead.append(f"{entry}：目录是空的，没有任何提交会只落在这里 → 排了等于没排")

    covered = [
        f"{rel}（RP 自动位）"
        for rel in sorted(_RP_MANAGED | {"CHANGELOG.md"})
        if any(e and rel.startswith(f"{e}/") for e in entries)
    ]
    assert not covered, f"exclude-paths 覆盖到了 RP 要写的版本位：{covered} —— 这些位一旦落在被排的目录里，发版时没人抬"

    shipped = [
        str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")
        for p in (PROJECT_ROOT / "app").rglob("*.py")
        if any(e and str(p.relative_to(PROJECT_ROOT)).replace("\\", "/").startswith(f"{e}/") for e in entries)
    ]
    assert not shipped, (
        "exclude-paths 覆盖到了随包分发的代码（pyproject 的 packages.find where=['app']）："
        f"{shipped[:5]} —— 排掉这种目录会让真改动不发版"
    )
    assert not dead, "exclude-paths 有排不掉任何提交的条目：\n  " + "\n  ".join(dead)


#: 当前解释器没有 TOML 解析器时的哨兵（区别于"有解析器但走不到 key"）。
_NO_PARSER = "__no-toml-parser__"


def _jsonpath_value(target: Path, path: str, type_: str) -> object:
    """按 RP 的 jsonpath 取当前值；取不到返回 `__unread__:原因` 字符串（交给调用方汇总）。"""
    keys = path.removeprefix("$").strip(".").split(".")
    try:
        if type_ == "json":
            doc: object = json.loads(target.read_text(encoding="utf-8"))
        else:
            try:
                import tomllib
            except ImportError:  # CI 矩阵有 3.10，那里没有 tomllib
                return _NO_PARSER
            doc = tomllib.loads(target.read_text(encoding="utf-8"))
        for key in keys:
            if not isinstance(doc, dict) or key not in doc:
                return f"__unread__:缺 key {key!r}"
            doc = doc[key]
        return doc
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        return f"__unread__:{type(exc).__name__}: {exc}"


def test_bundled_changelog_describes_its_own_version() -> None:
    """`version.json` 的 `$.version` 由 RP 自动抬，但 `changelog`/`release_date` 不会 ——
    而 `desktop/src-tauri/src/updater.rs` 会读本地 `version.json` 的 changelog 展示给用户。
    只抬版本号就会发出"自称 2.2.4、说明写着 2.2.3"的壳，所以这条必须红在 release PR 上。"""
    data = json.loads((PROJECT_ROOT / "version.json").read_text(encoding="utf-8"))
    ver = str(data["version"])
    changelog = str(data.get("changelog", ""))
    assert changelog.strip(), "version.json 没有 changelog 字段，壳里那栏是空的"
    assert changelog.lstrip().startswith(ver), (
        f"version.json 自称 {ver}，changelog 却在讲另一个版本：{changelog.splitlines()[0][:40]!r}..."
        " —— RP 只改 $.version，发版时把这段说明（和 release_date）一起补上。"
    )
