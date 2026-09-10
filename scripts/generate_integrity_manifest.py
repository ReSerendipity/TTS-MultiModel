#!/usr/bin/env python3
"""生成核心模块完整性清单 (integrity_manifest.json)。

遍历 ``integrity_selfcheck.py`` 中定义的核心模块列表，
计算每个文件的 SHA-256 哈希值，输出为 JSON 格式保存到
``app/integrated_app/security/integrity_manifest.json``。

每次修改核心模块代码后，重新运行此脚本更新清单：
    python scripts/generate_integrity_manifest.py

便携分卷构建（A-6，build_portable_bundle.ps1）需要针对 staging 里的
``app/integrated_app`` 重新生成清单（闭源注入/去注释后哈希已变），
此时传入 ``--app-dir`` 指定应用根目录（``integrated_app`` 的上级）：
    python scripts/generate_integrity_manifest.py --app-dir <path>
"""

import argparse
import hashlib
import json
import os
import sys

# 确保可以导入 integrated_app 包
_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from integrated_app.security.integrity_selfcheck import _CORE_MODULES  # noqa: E402

# app/integrated_app/ 目录
_APP_DIR = os.path.join(_APP_DIR, "integrated_app")
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
    parser = argparse.ArgumentParser(description="生成完整性清单")
    parser.add_argument(
        "--app-dir",
        default=None,
        help="应用根目录（integrated_app 的上级，其下须有 security/integrity_selfcheck.py 引用的核心模块），默认仓库 app/",
    )
    args = parser.parse_args()

    app_dir = _APP_DIR
    if args.app_dir:
        app_dir = os.path.abspath(args.app_dir)
        manifest_path = os.path.join(app_dir, "security", "integrity_manifest.json")
    else:
        manifest_path = _MANIFEST_PATH

    files: dict[str, str] = {}
    skipped: list[str] = []

    for module_rel in _CORE_MODULES:
        module_path = os.path.join(app_dir, module_rel)
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

    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        # EOF 尾换行规范化（pre-commit end-of-file-fixer 会补 \n；签名依赖文件字节，
        # 若此处不写尾换行，提交前再补一次换行会导致签名失配——见 P1-2 冒烟教训）
        f.write("\n")

    print(f"\n清单已生成: {manifest_path}")
    print(f"  已哈希: {len(files)} 个文件")
    if skipped:
        print(f"  已跳过: {len(skipped)} 个文件 ({', '.join(skipped)})")


if __name__ == "__main__":
    main()
