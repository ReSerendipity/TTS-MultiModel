#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Persona .pt 嵌入文件来源验证工具。

P2 安全修复：扫描 personas 目录下所有 .pt 文件，检查其 origin 元数据字段，
验证嵌入文件来源是否为 TTS_MultiModel 生成。用于防止嵌入被批量导出后无法追溯。

使用方法:
    python scripts/verify_persona_pt_origin.py [--dir PATH]

退出码:
    0 — 所有 .pt 文件来源验证通过
    1 — 发现来源不匹配或无法读取的 .pt 文件
"""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_PERSONA_DIR = _PROJECT_ROOT / "personas"
EXPECTED_ORIGIN = "TTS_MultiModel v2.1.0"


def verify_pt_file(pt_path: Path) -> tuple[bool, str]:
    """验证单个 .pt 文件的来源元数据。

    Args:
        pt_path: .pt 文件路径。

    Returns:
        Tuple[bool, str]: (是否验证通过, 详细信息)。
    """
    try:
        import torch  # noqa: PLC0415

        raw = torch.load(pt_path, map_location="cpu")
    except Exception as e:
        return False, f"加载失败: {type(e).__name__}: {e}"

    # 新格式: {"data": payload, "_meta": {"origin": ..., "format_version": ..., "created_at": ...}}
    if isinstance(raw, dict) and "_meta" in raw:
        meta = raw.get("_meta", {})
        origin = meta.get("origin", "")
        version = meta.get("format_version", "?")
        created = meta.get("created_at", "?")

        if not origin:
            return False, "缺少 origin 字段（元数据不完整）"

        if origin != EXPECTED_ORIGIN:
            return False, f"origin 不匹配: 期望 '{EXPECTED_ORIGIN}'，实际 '{origin}'"

        return True, f"origin={origin}, version={version}, created={created}"

    # 旧格式: 直接存储原始数据，无 _meta 键
    return False, ("旧格式 .pt 文件（无 origin 元数据）—— 建议重新加载该音色以自动生成带来源标识的新格式文件")


def verify_directory(persona_dir: Path) -> int:
    """验证目录下所有 .pt 文件的来源。

    Args:
        persona_dir: Persona 目录路径。

    Returns:
        验证失败的数量。
    """
    if not persona_dir.exists():
        logger.warning("目录不存在: %s", persona_dir)
        return 0

    pt_files = sorted(persona_dir.glob("*.pt"))
    if not pt_files:
        logger.info("目录下无 .pt 文件: %s", persona_dir)
        return 0

    total = len(pt_files)
    passed = 0
    failed = 0

    print(f"扫描目录: {persona_dir}")
    print(f"发现 {total} 个 .pt 文件")
    print("-" * 60)

    for pt_file in pt_files:
        ok, detail = verify_pt_file(pt_file)
        if ok:
            passed += 1
            print(f"  [OK]   {pt_file.name} — {detail}")
        else:
            failed += 1
            print(f"  [FAIL] {pt_file.name} — {detail}")

    print("-" * 60)
    print(f"总计: {total} | 通过: {passed} | 失败: {failed}")

    return failed


def main() -> int:
    """脚本主入口。

    Returns:
        0 表示所有验证通过，1 表示有验证失败。
    """
    parser = argparse.ArgumentParser(description="Persona .pt 嵌入文件来源验证工具")
    parser.add_argument(
        "--dir",
        type=str,
        default=str(_DEFAULT_PERSONA_DIR),
        help=f"Persona 目录路径 (默认: {_DEFAULT_PERSONA_DIR})",
    )
    args = parser.parse_args()

    persona_dir = Path(args.dir)

    print("=" * 60)
    print("TTS_MultiModel Persona .pt 来源验证工具")
    print(f"预期 origin: {EXPECTED_ORIGIN}")
    print("=" * 60)
    print()

    failed = verify_directory(persona_dir)

    print()
    if failed == 0:
        print("All .pt files verified successfully.")
        return 0
    else:
        print(f"{failed} file(s) failed verification.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
