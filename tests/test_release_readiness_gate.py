"""发布条件闸的两半：脚本能判"这条 release PR 现在能不能合"，工作流真的在跑它。

背景（都有账）：release-please 自己开的 PR **拿不到任何 CI**（GitHub 固定行为：
`GITHUB_TOKEN` 产生的提交不再级联触发 workflow；实测 #120 与 #132 的 `gh pr checks` 都是空数组），
而它有 5 类手工同步的版本位 RP 不会碰。于是"红在 release PR 上是预期行为"这个假设是错的 ——
不补齐就会红在**已经发版之后**的 main 上。这道闸把判据提前，并回写成 commit status。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "check_release_readiness.py"
_WF = _ROOT / ".github" / "workflows" / "release-please.yml"

# 判据会读到的所有版本承载文件；少搬一个，fixture 就会以"取不到版本号"而不是"判出漂移"失败，
# 那等于白测 —— 所以这个清单与 _CARRIERS 的断言不能省。
_CARRIERS = (
    "pyproject.toml",
    "version.json",
    "config.yaml",
    "CHANGELOG.md",
    ".release-please-manifest.json",
    "desktop/package.json",
    "desktop/src-tauri/tauri.conf.json",
    "desktop/src-tauri/Cargo.toml",
    "desktop/src-tauri/Cargo.lock",
    "deploy/kubernetes/deployment.yaml",
    "scripts/installer/setup.nsi",
    "tests/test_version_consistency.py",
    # 判据里有两条要读它（extra-files 的类型白名单与标注一致性），漏搬就会以 FileNotFoundError
    # 崩在"判据未能执行"上 —— 那正是脚本设计上要区分开的那一档（崩 != 漂移，但同样不能放行）。
    "release-please-config.json",
    "docs/release-governance.md",
)


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    # 子进程的 stdout 在 Windows 上默认走 GBK：只给父进程设 encoding="utf-8" 会把中文输出
    # 解成一堆 \ufffd，断言消息就没法读（#126 在门禁测试上栽过同一个坑）。
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        check=False,
    )


def _current_version() -> str:
    """从 pyproject 现读，别把版本号硬编码进测试（否则每次发版都要来改这里）。"""
    import re

    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    assert m, "读不到 pyproject 的 version"
    return m.group(1)


CUR = _current_version()  # fixture 搬过来时树里就是它
NEW = "9.9.9"


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """把版本承载文件按原样搬进 tmp_path，造一棵"可以改一处看它响不响"的假工作树。"""
    for rel in _CARRIERS:
        src = _ROOT / rel
        assert src.is_file(), f"仓库里少了 {rel}，这个 fixture 的假设要更新"
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    return tmp_path


def _set(root: Path, rel: str, new: str = NEW) -> None:
    p = root / rel
    p.write_text(p.read_text(encoding="utf-8").replace(f'"{CUR}"', f'"{new}"'), encoding="utf-8")


def _bump_rp_managed_sites(root: Path) -> None:
    """只抬 release-please 会自动改的那几处 —— 正是一条没补齐手工位的 release PR 的形状。"""
    for rel in (
        "pyproject.toml",
        "version.json",
        "desktop/package.json",
        "desktop/src-tauri/tauri.conf.json",
        "desktop/src-tauri/Cargo.toml",
        ".release-please-manifest.json",
    ):
        _set(root, rel)
    (root / "CHANGELOG.md").write_text(f"## [{NEW}] - 2026-09-22\n\n* 测试用\n", encoding="utf-8")


def _complete_hand_sites(root: Path) -> None:
    (root / "config.yaml").write_text(f'version: "{NEW}"\n', encoding="utf-8")
    # 包名必须写对：判据是按 `name = "tts-multimodel-desktop"` 定位锁里自身版本的
    (root / "desktop" / "src-tauri" / "Cargo.lock").write_text(
        f'[[package]]\nname = "tts-multimodel-desktop"\nversion = "{NEW}"\n', encoding="utf-8"
    )
    # 镜像名要照判据的正则来（它认 `ghcr.io/<owner>/tts-multimodel:x.y.z`），否则测的是"读取器失效"而不是"版本漂移"
    (root / "deploy" / "kubernetes" / "deployment.yaml").write_text(
        f"          image: ghcr.io/reserendipity/tts-multimodel:{NEW}\n", encoding="utf-8"
    )
    (root / "scripts" / "installer" / "setup.nsi").write_text(
        f'OutFile "TTSMultiModel-Setup-v{NEW}.exe"\n!define APP_VERSION "{NEW}"\nVIProductVersion "{NEW}.0"\n',
        encoding="utf-8",
    )
    gov = root / "docs" / "release-governance.md"
    gov.write_text(
        gov.read_text(encoding="utf-8").replace("已发布最新 = v" + CUR, "已发布最新 = v" + NEW), encoding="utf-8"
    )
    (root / "version.json").write_text(
        json.dumps(
            {
                "version": NEW,
                "release_date": "2026-09-22",
                "minimum_shell_version": "2.2.2",
                "changelog": f"{NEW}：测试",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_script_passes_on_a_consistent_tree(tree: Path) -> None:
    res = _run(tree)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "发版条件满足" in res.stdout


def test_script_fails_when_only_the_rp_managed_sites_are_bumped(tree: Path) -> None:
    _bump_rp_managed_sites(tree)
    res = _run(tree)
    out = res.stdout + res.stderr
    assert res.returncode == 1, f"未补齐手工位的 release PR 被放行了：{out[-400:]}"
    for who in ("config.yaml", "Cargo.lock", "deployment.yaml", "OutFile"):
        assert who in out, f"没点出 {who} 在拖：{out[-500:]}"


def test_script_still_fails_on_a_stale_changelog_section(tree: Path) -> None:
    """手工位全补齐、只有 CHANGELOG 段名与目标版本不符 —— 也要拦住（发版说明是给人看的）。"""
    _bump_rp_managed_sites(tree)
    _complete_hand_sites(tree)
    (tree / "CHANGELOG.md").write_text(f"## [{CUR}] - 2026-09-22\n\n* 旧段\n", encoding="utf-8")
    res = _run(tree)
    out = res.stdout + res.stderr
    assert res.returncode == 1, f"CHANGELOG 段名过期却被放行：{out[-400:]}"
    assert f"## [{NEW}]" in out


def test_workflow_runs_the_gate_and_can_write_the_status() -> None:
    doc = yaml.safe_load(_WF.read_text(encoding="utf-8"))
    job = doc["jobs"]["release-please"]
    # permissions 在这个文件里写在顶层（对整个工作流生效），不是按 job 覆写；任一层给了都算够。
    layers = [(doc.get("permissions") or {}), (job.get("permissions") or {})]
    assert any(p.get("statuses") == "write" for p in layers), (
        "没有任何一层给 permissions.statuses: write —— 回写 commit status 会被 API 拒，这道闸等于没装"
    )
    steps = job["steps"]
    gate = next((s for s in steps if "发布条件检查" in str(s.get("name") or "")), None)
    assert gate, "找不到发布条件检查步骤（release PR 拿不到 CI，这是合并前唯一的机器判据）"
    body = str(gate["run"])
    assert "check_release_readiness.py" in body, "这一步没真调脚本，只是摆样子"
    assert "statuses/" in body and 'context="release-gate"' in body
    rp = next(i for i, s in enumerate(steps) if "release-please-action" in str(s.get("uses", "")))
    assert any("发布条件检查" in str(s.get("name") or "") for s in steps[rp + 1 :]), (
        "闸必须在 RP 步骤之后跑 —— 它检的是刚被 groom 过的那个 head"
    )
