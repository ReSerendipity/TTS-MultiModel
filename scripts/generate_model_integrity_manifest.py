#!/usr/bin/env python3
"""生成模型权重完整性清单 (model_integrity_manifest.json)。

P2-3 安全整改：扫描 model/ 目录下的权重文件，计算 SHA-256 哈希，
输出为 JSON 清单，供启动时 verify_model_integrity() 校验（防权重投毒，CWE-353）。

每次更新/替换模型权重后，重新运行此脚本更新清单：
    python scripts/generate_model_integrity_manifest.py

清单格式：
    {
        "generated_at": "2026-09-05T12:00:00",
        "model_root": "model/",
        "files": {
            "voxcpm2/model.safetensors": "sha256:abc123...",
            ...
        }
    }

verify_model_integrity() 读取的是 {相对路径: 哈希} 扁平映射，
本脚本输出的 files 字段即为此格式（调用方可直接取 files 字段）。
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MODEL_DIR = _PROJECT_ROOT / "model"
_OUTPUT_PATH = _PROJECT_ROOT / "configs" / "model_integrity_manifest.json"

# 权重文件扩展名（排除配置/日志/临时文件）
_WEIGHT_EXTENSIONS = {
    ".safetensors",
    ".pt",
    ".pth",
    ".bin",
    ".ckpt",
    ".gguf",
    ".onnx",
    ".pkl",
    ".h5",
    ".tflite",
}

# 大文件分块读取（8MB）
_CHUNK_SIZE = 8 * 1024 * 1024


def compute_sha256(filepath: Path) -> str:
    """计算文件 SHA-256 哈希（分块读取，支持 GB 级权重）。"""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def scan_model_files(model_dir: Path) -> list[Path]:
    """递归扫描 model/ 目录下的权重文件。"""
    files: list[Path] = []
    if not model_dir.is_dir():
        print(f"[WARN] model 目录不存在: {model_dir}", file=sys.stderr)
        return files

    for root, _dirs, filenames in os.walk(model_dir):
        for fname in filenames:
            fpath = Path(root) / fname
            if fpath.suffix.lower() in _WEIGHT_EXTENSIONS:
                files.append(fpath)
    return sorted(files)


def main() -> int:
    if not _MODEL_DIR.is_dir():
        print(f"[ERROR] model 目录不存在: {_MODEL_DIR}", file=sys.stderr)
        return 1

    files = scan_model_files(_MODEL_DIR)
    if not files:
        print("[WARN] 未找到任何权重文件，清单将为空。", file=sys.stderr)

    manifest_files: dict[str, str] = {}
    total_size = 0
    for fpath in files:
        rel_path = str(fpath.relative_to(_MODEL_DIR)).replace("\\", "/")
        try:
            file_hash = compute_sha256(fpath)
            manifest_files[rel_path] = file_hash
            total_size += fpath.stat().st_size
            print(f"  [OK] {rel_path} ({fpath.stat().st_size / 1024 / 1024:.1f} MB)")
        except OSError as e:
            print(f"  [FAIL] {rel_path}: {e}", file=sys.stderr)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_root": "model/",
        "total_files": len(manifest_files),
        "total_size_bytes": total_size,
        "files": manifest_files,
    }

    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n[DONE] 清单已生成: {_OUTPUT_PATH}")
    print(f"  文件数: {len(manifest_files)}")
    print(f"  总大小: {total_size / 1024 / 1024 / 1024:.2f} GB")
    print("\n  下一步: 确认 config.yaml 中 runtime.integrity.expected_model_hashes")
    print("  指向此清单（默认 configs/model_integrity_manifest.json），启动时自动校验。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
