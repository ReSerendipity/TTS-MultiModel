"""torch 交付轨一致性闸：`requirements-lock.txt` == `build_portable_bundle.ps1` 的钉版。

为什么有这条：2026-09-20 dependabot 的 `minor-and-patch` 组（`patterns: ["*"]`）把**手维护的锁**
单独顶到 `torch==2.14.0` / `torchvision==0.29.0`，而便携轨的构建脚本仍钉 `2.13.0` / `0.28.0`，
脚本自己的注释还写着"保持与 requirements-lock.txt 一致"。三件事同时坏：

  - 锁说的版本与发出去的便携包里跑的版本不是一个，且没人会察觉（没有闸，锁也不参与构建）；
  - docker 镜像从锁装 → 镜像 2.14.0、便携包 2.13.0、开发 `.venv` 2.13.0+cu132，三份不同轨；
  - 显存行为只在 2.13.0+cu132 上实测过（v2.2.4 的四次连续切换、9489→3344 MiB 回落），
    2.14.0 那组合**从未安装、从未跑过**。

所以这里钉的是"三处一起动"这个流程约束：要升 torch，先改脚本里的版本+哈希，再复算锁，
否则本条红。owner 2026-09-23 定的轨是 **2.13.0**（"不在发版关口上改版本"）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCK = PROJECT_ROOT / "requirements-lock.txt"
BUNDLE = PROJECT_ROOT / "scripts" / "build_portable_bundle.ps1"
DEPENDABOT = PROJECT_ROOT / ".github" / "dependabot.yml"

#: 锁里的包名 → 构建脚本里的参数名
TRACK = {
    "torch": "TorchVersion",
    "torchvision": "TorchvisionVersion",
    "torchaudio": "TorchaudioVersion",
}
#: 版本参数 → 哈希参数（cu132 index 不发布哈希，权威值只能钉在仓里）
HASH_PARAM = {
    "TorchVersion": "TorchSha256",
    "TorchvisionVersion": "TorchvisionSha256",
    "TorchaudioVersion": "TorchaudioSha256",
}


def _locked_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(torch|torchvision|torchaudio)==([0-9][^#\s]*)\s*$", line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _script_params() -> dict[str, str]:
    body = BUNDLE.read_text(encoding="utf-8")
    return dict(re.findall(r"\[string\]\$(Torch\w*Version|Torch\w*Sha256|TorchaudioVersion)\s*=\s*'([^']*)'", body))


def test_track_reads_both_sides() -> None:
    """两侧都真读到了东西 —— 读不到的闸等于没有闸。"""
    locked = _locked_versions()
    params = _script_params()
    assert set(locked) == set(TRACK), f"锁里没把三件套都钉住：{sorted(locked)}"
    missing = [p for p in list(TRACK.values()) + list(HASH_PARAM.values()) if p not in params]
    assert not missing, f"构建脚本里找不到参数 {missing} —— 本闸的正则失配了，去修它而不是删测试"


def test_lock_and_bundle_pin_the_same_torch_track() -> None:
    locked = _locked_versions()
    params = _script_params()
    drift = [(pkg, locked[pkg], params[param]) for pkg, param in TRACK.items() if locked[pkg] != params[param]]
    assert not drift, (
        "torch 交付轨分叉：requirements-lock.txt 与 build_portable_bundle.ps1 钉的不是同一版本 "
        f"{drift}。\n  三处（锁 / 便携脚本 / docker 镜像）必须一起动：先改脚本里的版本与 SHA256，"
        "再复算锁。显存行为实测过的轨是 2.13.0+cu132（owner 2026-09-23 定：不在发版关口改版本）。"
    )


def test_every_pinned_wheel_has_a_sha256_anchor() -> None:
    """三个 wheel 都要有 64 位十六进制哈希：cu132 index 不发布哈希，这里是唯一权威值。"""
    params = _script_params()
    bad = [HASH_PARAM[v] for v in TRACK.values() if not re.fullmatch(r"[0-9a-f]{64}", params.get(HASH_PARAM[v], ""))]
    assert not bad, f"这些哈希参数为空或形状不对（要 64 位小写十六进制）：{bad} —— 空值等于关掉门禁"


def test_dependabot_cannot_silently_re_bump_the_track() -> None:
    """锁回钉之后，还得保证下个月不被 `patterns: ["*"]` 那组再顶开。"""
    doc = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
    pip = [u for u in doc.get("updates", []) if u.get("package-ecosystem") == "pip"]
    assert pip, "dependabot 里没有 pip 生态，本闸要跟着改"
    ignored = {i.get("dependency-name") for i in (pip[0].get("ignore") or [])}
    need = {pkg for pkg in TRACK if pkg != "torchaudio"}  # 忽略的是这次分叉的两个；torchaudio 未漂移
    missing = sorted(need - ignored)
    assert not missing, (
        f"torch 轨的这几个包没有 dependabot ignore：{missing}。"
        "手维护的锁被自动 bump 就是这次分叉的成因（2026-09-20 的 #87）。"
    )
