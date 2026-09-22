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
        "version.json": grab_json("version.json", "更新契约版本"),
        # 安装器**内嵌**的那份：`setup.nsi` 的 `File "version.json"` 取的是本目录里的这个文件，
        # 而仓库里没有任何脚本重新生成它（makensis 是手工跑的）—— 不跟着抬，
        # 装出来的壳就会拿一份旧版本号做本地识别（那份注释自己写着"缺失会静默跳过更新"）。
        "scripts/installer/version.json": grab_json("scripts/installer/version.json", "安装器内嵌版本（壳本地识别）"),
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
            r"image:\s*ghcr\.io/\S+/tts-multimodel:([0-9][^\s\"]*)",
            "k8s 镜像 tag",
        ),
    }
    return sites


def test_all_version_sites_agree() -> None:
    sites = _site_versions()
    assert len(sites) >= 10, f"只核到 {len(sites)} 个版本位，本条已失去意义"
    bad = {k: v for k, v in sites.items() if not _SEMVER.fullmatch(v)}
    assert not bad, f"这些版本位不是 x.y.z 形态：{bad}"
    distinct = set(sites.values())
    assert len(distinct) == 1, (
        "版本位互相矛盾（release-governance §5 的『版本位全部同步』没做到）："
        + "\n  "
        + "\n  ".join(f"{k} = {v}" for k, v in sorted(sites.items()))
    )


def test_installer_artifact_names_track_the_version_site() -> None:
    """OutFile / VIProductVersion 与 APP_VERSION 必须同源，否则装出来的包自称一个版本、
    文件名叫另一个版本（`version.json` 的 min_shell_version 比对就失去意义）。"""
    sites = _site_versions()
    ver = next(iter(set(sites.values())))
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
