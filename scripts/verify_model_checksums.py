#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""模型权重 SHA256 校验工具。

P1 安全修复：下载模型后自动比对 SHA256 哈希值，防止权重被篡改。

使用方法:
    python scripts/verify_model_checksums.py

退出码:
    0 — 所有校验通过（或无校验清单可用）
    1 — 校验失败（文件不匹配或缺失）
"""

import hashlib
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_CHECKSUM_FILE = _PROJECT_ROOT / "docs" / "SHA256SUMS.models"
_MODELS_DIR = _PROJECT_ROOT / "pretrained_models"


def compute_sha256(file_path: Path, chunk_size: int = 8192) -> str:
    """计算文件的 SHA256 哈希值。

    Args:
        file_path: 文件路径。
        chunk_size: 分块读取大小（字节）。

    Returns:
        十六进制 SHA256 哈希字符串。
    """
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def verify_checksums() -> bool:
    """校验 pretrained_models 目录下所有模型的 SHA256 哈希值。

    Returns:
        True 表示所有校验通过，False 表示有校验失败。
    """
    if not _CHECKSUM_FILE.exists():
        logger.warning("校验清单不存在: %s，跳过校验", _CHECKSUM_FILE)
        logger.info("提示: 首次下载后请运行生成校验值并填入清单")
        return True

    # 解析校验清单
    entries: list[tuple[str, str]] = []
    with open(_CHECKSUM_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                expected_hash, rel_path = parts
                entries.append((expected_hash.lower().strip(), rel_path.strip()))

    if not entries:
        logger.info("校验清单为空（无实际哈希值），跳过校验")
        logger.info("提示: 请运行 sha256sum 生成实际值并填入 docs/SHA256SUMS.models")
        return True

    all_ok = True
    for expected_hash, rel_path in entries:
        file_path = _MODELS_DIR / rel_path
        if not file_path.exists():
            logger.error("[FAIL] 文件缺失: %s", rel_path)
            all_ok = False
            continue

        logger.info("校验中: %s ...", rel_path)
        actual_hash = compute_sha256(file_path)
        if actual_hash == expected_hash:
            logger.info("[OK]   %s", rel_path)
        else:
            logger.error("[FAIL] %s — 哈希不匹配", rel_path)
            logger.error("  期望: %s", expected_hash)
            logger.error("  实际: %s", actual_hash)
            all_ok = False

    return all_ok


def verify_model_dir(model_dir: Path) -> bool:
    """校验指定模型目录的完整性（非空 + 文件大小预检）。

    Args:
        model_dir: 模型目录路径。

    Returns:
        True 表示目录非空且包含有效文件。
    """
    if not model_dir.exists():
        logger.warning("模型目录不存在: %s", model_dir)
        return False

    if not any(model_dir.iterdir()):
        logger.warning("模型目录为空: %s", model_dir)
        return False

    # 检查是否包含权重文件
    weight_exts = {".pth", ".pt", ".safetensors", ".bin"}
    weight_files = [f for f in model_dir.rglob("*") if f.suffix in weight_exts]

    if not weight_files:
        logger.warning("模型目录中未找到权重文件 (%s): %s", ", ".join(weight_exts), model_dir)
        return False

    for wf in weight_files:
        size_mb = wf.stat().st_size / (1024 * 1024)
        if size_mb < 1.0:
            logger.warning("[WARN] 权重文件过小 (<1MB)，可能已损坏: %s (%.1f MB)", wf, size_mb)
        else:
            logger.info("[OK] %s (%.1f MB)", wf.name, size_mb)

    return True


def main() -> int:
    """脚本主入口。

    Returns:
        0 表示校验通过，1 表示校验失败。
    """
    print("=" * 60)
    print("TTS_MultiModel 模型权重 SHA256 校验工具")
    print("=" * 60)
    print()

    # 1. 目录完整性检查
    print("[1/2] 目录完整性检查...")
    model_dirs = [
        ("VoxCPM2", _MODELS_DIR / "VoxCPM2"),
        ("IndexTTS2", _MODELS_DIR / "IndexTTS2"),
        ("dots.tts", _MODELS_DIR / "dots.tts"),
        ("SenseVoiceSmall", _MODELS_DIR / "SenseVoiceSmall"),
        ("speech_zipenhancer", _MODELS_DIR / "speech_zipenhancer"),
    ]

    dir_ok = True
    for name, path in model_dirs:
        if path.exists():
            if verify_model_dir(path):
                print(f"  ✓ {name}")
            else:
                print(f"  ✗ {name} — 目录不完整")
                dir_ok = False
        else:
            print(f"  - {name} — 未下载（跳过）")

    print()

    # 2. SHA256 校验
    print("[2/2] SHA256 哈希校验...")
    hash_ok = verify_checksums()

    print()
    if dir_ok and hash_ok:
        print("✅ 所有校验通过！")
        return 0
    else:
        print("❌ 校验失败，请检查上方日志")
        return 1


if __name__ == "__main__":
    sys.exit(main())
