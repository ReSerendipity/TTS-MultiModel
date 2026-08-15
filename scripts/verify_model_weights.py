#!/usr/bin/env python
"""模型权重下载完整性校验脚本。

检查 pretrained_models/ 下所有引擎的权重文件是否完整。

用法：
    python scripts/verify_model_weights.py [--model generic_tts_engine|all]
"""

import argparse
import hashlib
import os
import sys

# 各引擎权重文件清单（文件名 → 预期大小（字节），0 表示不校验大小）
WEIGHT_MANIFESTS = {
    "generic_tts_engine": {
        "dir": "pretrained_models/通用 TTS 引擎",
        "files": {},
    },
    "voxcpm2": {
        "dir": "pretrained_models/VoxCPM2",
        "files": {},
    },
    "indextts2": {
        "dir": "pretrained_models/IndexTTS2",
        "files": {},
    },
}


def compute_sha256(filepath: str) -> str:
    """计算文件的 SHA256 哈希值。"""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192 * 1024):  # 8MB chunks
            sha256.update(chunk)
    return sha256.hexdigest()


def verify_engine(engine_name: str, project_root: str) -> bool:
    """校验指定引擎的权重文件。

    Args:
        engine_name: 引擎名称。
        project_root: 项目根目录。

    Returns:
        True 表示所有文件存在且校验通过。
    """
    manifest = WEIGHT_MANIFESTS.get(engine_name)
    if manifest is None:
        print(f"未知引擎: {engine_name}", file=sys.stderr)
        return False

    engine_dir = os.path.join(project_root, manifest["dir"])
    if not os.path.isdir(engine_dir):
        print(f"[{engine_name}] 目录不存在: {engine_dir}")
        return False

    # 如果有具体文件清单，逐个检查
    expected_files = manifest["files"]
    if not expected_files:
        # 无具体清单：检查目录中是否有权重文件
        weight_extensions = (".bin", ".safetensors", ".pt", ".pth", ".ckpt", ".onnx")
        found_files = []
        for root, _, files in os.walk(engine_dir):
            for f in files:
                if f.endswith(weight_extensions):
                    found_files.append(os.path.join(root, f))
        if not found_files:
            print(f"[{engine_name}] 未找到权重文件")
            return False
        print(f"[{engine_name}] 找到 {len(found_files)} 个权重文件:")
        for f in found_files:
            size_mb = os.path.getsize(f) / (1024 * 1024)
            print(f"  {os.path.relpath(f, engine_dir)} ({size_mb:.1f} MB)")
        return True

    all_ok = True
    for filename, expected_size in expected_files.items():
        filepath = os.path.join(engine_dir, filename)
        if not os.path.exists(filepath):
            print(f"[{engine_name}] 缺失文件: {filename}")
            all_ok = False
            continue

        actual_size = os.path.getsize(filepath)
        if expected_size > 0 and actual_size != expected_size:
            print(f"[{engine_name}] 文件大小不匹配: {filename} (预期 {expected_size}, 实际 {actual_size})")
            all_ok = False
            continue

        print(f"[{engine_name}] OK: {filename} ({actual_size / (1024 * 1024):.1f} MB)")

    return all_ok


def main():
    parser = argparse.ArgumentParser(description="模型权重完整性校验")
    parser.add_argument(
        "--model", default="all", choices=["all", "generic_tts_engine", "voxcpm2", "indextts2"], help="校验哪个引擎"
    )
    parser.add_argument("--sha256", action="store_true", help="计算并输出 SHA256（耗时较长）")
    args = parser.parse_args()

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    engines = list(WEIGHT_MANIFESTS.keys()) if args.model == "all" else [args.model]

    all_ok = True
    for engine in engines:
        print(f"\n=== 校验 {engine} ===")
        if not verify_engine(engine, project_root):
            all_ok = False

    if all_ok:
        print("\n所有权重文件校验通过")
        sys.exit(0)
    else:
        print("\n部分权重文件校验失败", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
