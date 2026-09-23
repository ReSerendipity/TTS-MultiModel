"""镜像名同源闸：仓库里写出来的 ghcr 引用，必须等于工作流真的会推上去的那个名字。

起因（2026-09-23 实测）：`gh workflow run docker-publish.yml --ref v2.2.5` 的日志推的是

    pushing manifest for ghcr.io/reserendipity/tts_multimodel:2.2.5@sha256:ca6edebc…
    pushing manifest for ghcr.io/reserendipity/tts_multimodel:2.2@sha256:ca6edebc…
    pushing manifest for ghcr.io/reserendipity/tts_multimodel:sha-9125a3e6…@sha256:ca6edebc…
    pushing manifest for ghcr.io/reserendipity/tts_multimodel:latest@sha256:ca6edebc…

也就是 `IMAGE_NAME: ${{ github.repository }}` = `ReSerendipity/TTS_MultiModel` 经
docker/metadata-action 归一后**只变小写、下划线原样保留** → `reserendipity/tts_multimodel`。
而 `deploy/kubernetes/deployment.yaml` 两处、`deploy/kubernetes/README.md`、
`docs/rollback_sop.md` 写的是 `tts-multimodel`（连字符）—— 那个包在 ghcr 上从来不存在
（同一个 token 查两种拼写：下划线回 403「需要 read:packages」，连字符回 404「Package not found」）。
`README.md` 用的是对的那个。

原来的版本位闸抓不到这件事，因为它把名字**写死**在正则里
（`image:\\s*ghcr\\.io/\\S+/tts-multimodel:(...)`），于是它只核 tag、名字反倒在核之外 ——
这正是"配置只在源码树里对一遍、没跟产物对"的形状。所以这里改成从工作流推导，不许再硬编码。
"""

from __future__ import annotations

import inspect
import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 会写出 ghcr 引用的地方（只列具体文件，不做全仓 grep：`docs/_devarchive/` 是归档区）。
_REF_FILES = [
    "deploy/kubernetes/deployment.yaml",
    "deploy/kubernetes/README.md",
    "docs/rollback_sop.md",
    "README.md",
]

_REF_RE = re.compile(r"ghcr\.io/([A-Za-z0-9._/-]+)(?::([A-Za-z0-9._-]+))?")
_WORKFLOW = ".github/workflows/docker-publish.yml"


def _pushed_image_name() -> str:
    """从 docker-publish.yml 推导它真的会推的镜像名（不含 registry）。"""
    wf = (PROJECT_ROOT / _WORKFLOW).read_text(encoding="utf-8")
    env = dict(re.findall(r"^\s{2}([A-Z_]+):\s*(.+?)\s*$", wf, re.M))
    token = env.get("IMAGE_NAME")
    assert token, f"{_WORKFLOW} 里没有 env.IMAGE_NAME，本闸的推导依据变了，请同步改这里"
    registry = env.get("REGISTRY", "ghcr.io")
    assert registry == "ghcr.io", f"registry 变成 {registry} 了，本闸只核 ghcr 引用"

    if token.strip() == "${{ github.repository }}":
        remote = subprocess.run(  # noqa: S603
            ["git", "-C", str(PROJECT_ROOT), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        slug_url = remote.rstrip("/")
        if slug_url.endswith(".git"):
            slug_url = slug_url[: -len(".git")]
        slug = re.split(r"[:/]", slug_url)[-2:]
        assert len(slug) == 2, f"从 origin 推不出 owner/repo：{remote!r}"
        # metadata-action 的归一：整段小写，分隔符（`_`/`-`/`.`）原样保留 —— 上面日志的实测口径
        return "/".join(slug).lower()
    return token.strip().strip('"').lower()


def test_ghcr_references_use_the_name_the_workflow_pushes() -> None:
    """每一条写进仓库的 ghcr 引用，镜像名必须等于工作流推导出来的那一个。"""
    want = _pushed_image_name()
    bad = []
    for rel in _REF_FILES:
        path = PROJECT_ROOT / rel
        if not path.exists():
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name, _tag in _REF_RE.findall(line):
                if name != want:
                    bad.append(f"{rel}:{line_no} 写的是 {name!r}，真的推到 {want!r}")
    assert not bad, "ghcr 镜像名与工作流不同源：\n  " + "\n  ".join(bad) + f"\n（推导依据：{_WORKFLOW} 的 IMAGE_NAME）"


def test_ghcr_tags_do_not_carry_a_leading_v() -> None:
    """tag 的形状由 `type=semver,pattern={{version}}` 决定，是 `2.2.5` 而不是 `v2.2.5`。

    `docs/rollback_sop.md` 原先写的是 `:v2.2.0` —— 同一条命令里名字已经错了、tag 又多个 `v`，
    照着 SOP 敲的人会得到两次失败。**本闸只核形状，不核某个版本是否真推过**：ghcr 那个包是私有的，
    本机 token 读不到清单（403），2.2.2 之前的版本有没有镜像今天仍未取证。
    """
    bad = []
    for rel in _REF_FILES:
        path = PROJECT_ROOT / rel
        if not path.exists():
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for _name, tag in _REF_RE.findall(line):
                if tag.startswith("v") and tag[1:2].isdigit():
                    bad.append(f"{rel}:{line_no} 的 tag 是 {tag!r}，工作流的 semver 图案产出的形状是 {tag[1:]!r}")
    assert not bad, "ghcr 引用带了 `v` 前缀：\n  " + "\n  ".join(bad)


def test_derivation_is_not_hardcoded() -> None:
    """推导函数自己不许认识任何具体镜像名（认识名字 = 回到"只核 tag、核不出名字"的老状态）。"""
    body = inspect.getsource(_pushed_image_name)
    for literal in ("tts" + "_multimodel", "tts" + "-multimodel"):
        assert literal not in body, f"推导逻辑里出现了字面量 {literal!r} —— 名字必须只从工作流 + origin 来"
