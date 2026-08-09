#!/usr/bin/env python3
"""生成核心模块完整性清单 (integrity_manifest.json)。

遍历 ``integrity_selfcheck.py`` 中定义的核心模块列表，
计算每个文件的 SHA-256 哈希值，输出为 JSON 格式保存到
``bin/integrated_app/security/integrity_manifest.json``。

每次修改核心模块代码后，重新运行此脚本更新清单：
    python scripts/generate_integrity_manifest.py
"""

import hashlib
import json
import os
import sys

# 确保可以导入 integrated_app 包
_BIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

from integrated_app.security.integrity_selfcheck import _CORE_MODULES  # noqa: E402

# bin/integrated_app/ 目录
_APP_DIR = os.path.join(_BIN_DIR, "integrated_app")
# 清单输出路径
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


def main() -> None:
    """生成完整性清单。"""
    files: dict[str, str] = {}
    skipped: list[str] = []

    for module_rel in _CORE_MODULES:
        module_path = os.path.join(_APP_DIR, module_rel)
        if not os.path.exists(module_path):
            print(f"  [SKIP] {module_rel} (文件不存在)")
            skipped.append(module_rel)
            continue
        sha = compute_sha256(module_path)
        files[module_rel] = sha
        print(f"  [OK]   {module_rel}  {sha[:16]}...")

    manifest = {
        "description": "TTS_MultiModel 核心模块完整性清单 (自动生成)",
        "algorithm": "sha256",
        "files": files,
    }

    os.makedirs(os.path.dirname(_MANIFEST_PATH), exist_ok=True)
    with open(_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n清单已生成: {_MANIFEST_PATH}")
    print(f"  已哈希: {len(files)} 个文件")
    if skipped:
        print(f"  已跳过: {len(skipped)} 个文件 ({', '.join(skipped)})")


if __name__ == "__main__":
    main()
