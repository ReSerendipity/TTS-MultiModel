#!/usr/bin/env python3
"""生成模型权重期望 SHA256 清单（C1 整改）。

扫描 ``model/`` 下所有模型文件（排除目录/缓存/临时文件），输出 JSON：
    {相对 model/ 的路径: sha256}

用法：
    python scripts/generate_model_checksums.py [--root .] [--out model_checksums.json]

生成的清单应提交到仓库（或安全存储），并在 config.yaml 的
``runtime.integrity.expected_model_hashes`` 指向它。
注意：model/ 为权重禁区，本脚本只读扫描，不修改任何权重。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

_SKIP_DIRS = {".git", "__pycache__", "cache"}
_SKIP_EXT = {".tmp", ".lock"}


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="生成模型权重 SHA256 清单")
    parser.add_argument("--root", default=_project_root(), help="项目根目录")
    parser.add_argument("--out", default="model_checksums.json", help="输出 JSON 路径")
    args = parser.parse_args()

    model_dir = os.path.join(args.root, "model")
    if not os.path.isdir(model_dir):
        print(f"[WARN] 未找到 model/ 目录: {model_dir}", file=sys.stderr)
        return 0

    manifest: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(model_dir):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in _SKIP_EXT:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, model_dir).replace(os.sep, "/")
            try:
                manifest[rel] = sha256_file(full)
            except OSError as exc:
                print(f"[WARN] 跳过 {rel}: {exc}", file=sys.stderr)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"[OK] 已写入 {len(manifest)} 条权重哈希到 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
