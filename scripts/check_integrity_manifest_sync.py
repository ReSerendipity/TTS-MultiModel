#!/usr/bin/env python3
"""检查核心模块完整性清单与当前代码是否同步（提交/推送/CI 门禁）。

每次改动 ``app/integrated_app/`` 下任一核心模块（_CORE_MODULES）后，
``security/integrity_manifest.json`` 中的期望哈希就会过期——若未重新生成，
运行时 enforce 自检会拒绝启动（见 docs/agents/GOTCHAS.md #77）。
本脚本在提交/推送/CI 阶段提前发现「代码已改、清单未同步」的状态，
避免把问题留到启动时。

用法:
    python scripts/check_integrity_manifest_sync.py

退出码:
    0  清单与当前核心模块哈希一致（或清单缺失——与运行时语义一致：缺失跳过不阻断）
    1  存在不一致（提示重新生成清单并重签）
"""

import hashlib
import json
import os
import sys

# 确保可以导入 integrated_app 包
_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from integrated_app.security.integrity_selfcheck import _CORE_MODULES  # noqa: E402

# app/integrated_app/ 目录与清单路径
_APP_DIR = os.path.join(_APP_DIR, "integrated_app")
_MANIFEST_PATH = os.path.join(_APP_DIR, "security", "integrity_manifest.json")


def compute_sha256(filepath: str) -> str:
    """计算文件 SHA-256。"""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def main() -> int:
    """执行清单同步检查。"""
    # 清单缺失：与运行时 selfcheck 语义一致（缺失跳过、不阻断），仅提示。
    if not os.path.exists(_MANIFEST_PATH):
        print(f"[WARN] 完整性清单不存在: {_MANIFEST_PATH}")
        print(
            "       运行 `python scripts/generate_integrity_manifest.py` 生成后，"
            "再运行 `python scripts/sign_integrity_manifest.py --verify` 签发。"
        )
        return 0

    with open(_MANIFEST_PATH, encoding="utf-8") as f:
        manifest = json.load(f)
    expected_hashes = manifest.get("files", {})

    stale: list[str] = []
    for module_rel in _CORE_MODULES:
        module_path = os.path.join(_APP_DIR, module_rel)
        if not os.path.exists(module_path):
            print(f"  [WARN] 核心模块不存在: {module_rel}（跳过）")
            continue
        expected = expected_hashes.get(module_rel, "")
        if not expected:
            print(f"  [WARN] 清单未记录: {module_rel}（跳过）")
            continue
        actual = compute_sha256(module_path)
        if actual == expected:
            print(f"  [OK]   {module_rel}")
        else:
            stale.append(module_rel)
            print(f"  [FAIL] {module_rel}\n          期望: {expected}\n          实际: {actual}")

    if stale:
        print(
            "\n" + "=" * 60 + "\n"
            "[FAIL] 完整性清单与当前核心模块不同步（GOTCHAS #77）\n"
            f"    过期文件: {', '.join(stale)}\n"
            "    若改动来自正常代码变更，请运行：\n"
            "      python scripts/generate_integrity_manifest.py\n"
            "      python scripts/sign_integrity_manifest.py --verify\n"
            "    若你并未改动这些文件，请立即检查是否被篡改。\n" + "=" * 60
        )
        return 1

    print(f"\n[PASS] 完整性清单同步: {len(_CORE_MODULES)} 个核心模块全部一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
