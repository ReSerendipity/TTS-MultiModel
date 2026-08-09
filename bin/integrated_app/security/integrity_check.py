# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""文件 SHA-256 完整性校验工具模块。

提供文件哈希计算与校验功能，用于：
1. 启动时核心模块完整性自检（配合 ``integrity_selfcheck.py``）
2. 模型权重文件哈希校验（防止权重投毒，CWE-353）

来源：Seedvr2 的 ``security/integrity_check.py``，适配 TTS_MultiModel 项目结构。
"""

import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# 大文件分块读取大小 (8MB chunks, balances memory vs speed)
_CHUNK_SIZE = 8 * 1024 * 1024


def compute_sha256(filepath: str | os.PathLike) -> str:
    """计算文件的 SHA-256 哈希值。

    使用分块读取策略，支持大文件（GB 级模型权重）而不占用过多内存。

    Args:
        filepath: 文件路径。

    Returns:
        十六进制 SHA-256 哈希字符串（64 字符）。

    Raises:
        FileNotFoundError: 文件不存在时抛出。
        OSError: 文件读取失败时抛出。
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")

    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def verify_file_integrity(
    filepath: str | os.PathLike,
    expected_hash: str | None,
    *,
    purpose: str = "file",
    skip_if_empty: bool = True,
) -> bool:
    """验证文件 SHA-256 哈希值，防止文件被篡改。

    安全策略:
        1. 如果 expected_hash 为空或 None，且 skip_if_empty=True，跳过校验并记录调试日志
        2. 如果 expected_hash 非空，计算文件实际哈希并比对
        3. 哈希不匹配时记录严重安全警告并返回 False
        4. 哈希匹配时记录信息日志

    Args:
        filepath: 文件路径。
        expected_hash: 期望的 SHA-256 哈希值（64 字符十六进制字符串）。
        purpose: 描述性标签（如 "app_server"），用于日志消息。
        skip_if_empty: expected_hash 为空时是否跳过校验（默认 True，向后兼容）。

    Returns:
        True 表示校验通过（或跳过），False 表示校验失败。
    """
    if not expected_hash or not expected_hash.strip():
        if skip_if_empty:
            logger.debug(
                f"[INTEGRITY] {purpose}: 未配置 SHA-256 哈希，跳过校验 ({filepath}). "
                f"建议在 integrity_manifest.json 中配置哈希以启用完整性校验。"
            )
            return True
        else:
            logger.error(f"[INTEGRITY] {purpose}: 期望哈希为空但 skip_if_empty=False，拒绝加载")
            return False

    expected_hash = expected_hash.strip().lower()

    if not Path(filepath).exists():
        logger.error(f"[INTEGRITY] {purpose}: 文件不存在: {filepath}")
        return False

    logger.debug(f"[INTEGRITY] 正在校验 {purpose} SHA-256 完整性: {filepath}")
    actual_hash = compute_sha256(filepath)

    if actual_hash == expected_hash:
        logger.debug(f"[INTEGRITY] {purpose}: SHA-256 校验通过 ✓")
        return True
    else:
        logger.error(
            f"[SECURITY CRITICAL] {purpose}: SHA-256 校验失败！\n"
            f"    文件: {filepath}\n"
            f"    期望: {expected_hash}\n"
            f"    实际: {actual_hash}\n"
            f"    该文件可能已被篡改 (CWE-353)。"
        )
        return False
