"""三份依赖清单的同步闸（纯静态，不联网、不装包）。

为什么这个闸要写在 pytest 里而不是只靠 `scripts/sync_requirements.py`：
那个脚本**被 `.gitignore` 排除**（`.gitignore:293`），干净 clone 与 CI runner 上都没有它，
于是 `--check` / `--check-small` 从来没在 CI 跑过。2026-11-01 核对时记下的「既有红」
（`requirements.txt` 多 `munch>=4.0.0`、`--check-small` 报 filelock / pillow 与 lock 不一致）
就是这个缺位的后果：本地偶尔有人手工跑一次才看得见，红了也没人拦。
本文件把两条规则搬进 pytest，让 CI 的常规测试 job 真跑，脚本仍可本地用。

两条规则（与脚本语义对齐）：
S1 ``test_requirements_txt_matches_pyproject_dependencies`` ——
   `requirements.txt`（声明范围，供 install.bat/install.sh 用）必须与
   `pyproject.toml` 的 `[project].dependencies` **双向相等**：缺一条是漏装，
   多一条是「只写进清单、没进 pyproject」的野生依赖（`munch` 就是这么漂了半年）。
S2 ``test_portable_pins_align_with_lock`` ——
   `launcher/requirements-small.txt`（便携包钉装）与 `requirements-lock.txt`（开发锁定）：
   每行必须 `name==version` 精确钉装；每个包必须在 lock 里且版本相同；
   lock 里除 torch 族与 setuptools 外**不得有** small 漏掉的包（漏了就便携包运行期 ImportError）。

维护约定：两条守卫各配一条 `*_is_not_vacuous` 变异自证 —— 用合成坏输入喂同一套判定函数，
断言它真的报错；否则「全绿」可能只是函数里 return [] 的另一种形状。
"""

from __future__ import annotations

from pathlib import Path

import tomllib

_ROOT = Path(__file__).resolve().parents[1]
_REQ = _ROOT / "requirements.txt"
_PYPROJECT = _ROOT / "pyproject.toml"
_SMALL = _ROOT / "launcher" / "requirements-small.txt"
_LOCK = _ROOT / "requirements-lock.txt"

# 便携清单显式排除的包：torch 族由构建脚本单独安装（CUDA wheel 不走 PyPI 默认索引）
_SMALL_EXCLUDE = {"torch", "torchaudio", "torchvision", "setuptools"}


def _pyproject_deps() -> list[str]:
    with _PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    return [d.strip() for d in data.get("project", {}).get("dependencies", []) if d.strip()]


def _requirements_deps() -> list[str]:
    deps: list[str] = []
    for line in _REQ.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            deps.append(stripped)
    return deps


def _find_requirements_drift(req_deps: list[str], py_deps: list[str]) -> tuple[list[str], list[str]]:
    """返回 (requirements 缺少的 pyproject 依赖, requirements 多出的依赖)。"""
    missing = [d for d in py_deps if d not in req_deps]
    extra = [d for d in req_deps if d not in py_deps]
    return missing, extra


def _read_pins(path: Path) -> tuple[dict[str, str], list[str]]:
    """读 `name==version` 钉装文件，返回 (pins, 非法行)。"""
    pins: dict[str, str] = {}
    bad: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "==" not in line:
            bad.append(line)
            continue
        name, _, ver = line.partition("==")
        pins[name.strip()] = ver.strip()
    return pins, bad


def _find_small_drift(
    small_map: dict[str, str],
    small_bad: list[str],
    lock_map: dict[str, str],
) -> list[str]:
    """返回便携清单与 lock 的不一致项（空列表 = 对齐）。"""
    errors: list[str] = list(small_bad)
    for name, ver in small_map.items():
        if name in _SMALL_EXCLUDE:
            continue
        if name not in lock_map:
            errors.append(f"{name}=={ver}（lock 中不存在）")
        elif lock_map[name] != ver:
            errors.append(f"{name}=={ver}（lock 为 {lock_map[name]}）")
    for name, ver in lock_map.items():
        if name in _SMALL_EXCLUDE:
            continue
        if name not in small_map:
            errors.append(f"lock 有 {name}=={ver} 但便携清单缺失")
    return errors


def test_requirements_txt_matches_pyproject_dependencies() -> None:
    missing, extra = _find_requirements_drift(_requirements_deps(), _pyproject_deps())
    assert not missing, f"requirements.txt 缺少 pyproject 依赖: {missing}"
    assert not extra, f"requirements.txt 多出 pyproject 中不存在的依赖: {extra}"


def test_requirements_sync_guard_is_not_vacuous() -> None:
    """变异自证：同一判定函数在缺一条 / 多一条时都必须报出来。"""
    good_req = ["torch>=2.5.0", "munch>=4.0.0"]
    good_py = ["torch>=2.5.0", "munch>=4.0.0"]
    assert _find_requirements_drift(good_req, good_py) == ([], [])

    missing, extra = _find_requirements_drift(["torch>=2.5.0"], good_py)
    assert missing == ["munch>=4.0.0"] and extra == []

    missing, extra = _find_requirements_drift(["torch>=2.5.0", "munch>=4.0.0", "numpy>=2.0"], good_py)
    assert extra == ["numpy>=2.0"] and missing == []


def test_portable_pins_align_with_lock() -> None:
    small_map, small_bad = _read_pins(_SMALL)
    assert not small_bad, f"便携清单存在未精确钉装的行: {small_bad}"
    lock_map, _ = _read_pins(_LOCK)
    drift = _find_small_drift(small_map, small_bad, lock_map)
    assert not drift, "便携清单与 lock 不一致:\n" + "\n".join(drift)


def test_portable_pins_guard_is_not_vacuous() -> None:
    """变异自证：版本不一致 / lock 有而便携缺 / 未精确钉装，三种都要报。"""
    lock = {"filelock": "4.1.0", "pillow": "12.3.0", "torch": "2.13.0"}
    ok = _find_small_drift({"filelock": "4.1.0", "pillow": "12.3.0"}, [], lock)
    assert ok == []

    assert _find_small_drift({"filelock": "4.2.1", "pillow": "12.3.0"}, [], lock)
    assert _find_small_drift({"filelock": "4.1.0"}, [], lock)  # lock 有 pillow 但便携缺
    assert _find_small_drift({"filelock": "4.1.0", "pillow": "12.3.0"}, ["pillow>=12"], lock)
    # torch 族被排除，lock 里有也不算漏
    assert _find_small_drift({"filelock": "4.1.0", "pillow": "12.3.0"}, [], lock) == []
