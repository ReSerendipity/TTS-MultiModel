#!/usr/bin/env python3
"""为核心模块完整性清单生成/校验签名（桌面分发与安全加固 P0：Ed25519 优先，HMAC 回退）。

背景：integrity_manifest.json 与被校验代码同目录，攻击者若能改代码就能
同步改清单，"启动自检"即被绕过。签名把信任根外移：
- 发布版（推荐）：构建机用 Ed25519 私钥（data/.manifest_signing_key）签发，
  发布包内置公钥验证（用户端可验签、不持有私钥，enforce 才能成立）；
- 开发机：无 Ed25519 私钥时回退 HMAC-SHA256（data/.integrity_hmac_secret）。

用法：
    # 首次（签发机）：生成密钥对（私钥不进包，公钥内置）
    python scripts/generate_manifest_signing_key.py

    # 代码更新后：重新生成清单 → 签名
    python scripts/generate_integrity_manifest.py
    python scripts/sign_integrity_manifest.py

    # 校验（CI / 运维巡检）
    python scripts/sign_integrity_manifest.py --verify

退出码：
    0 成功；1 校验失败或文件缺失。

所属项目：TTS_MultiModel (TTS_MultiModel 多引擎 TTS 工具)
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.integrated_app.security.secret_key import (  # noqa: E402
    get_hmac_fallback_key,
    manifest_private_key_path,
    sign_file,
    sign_manifest_ed25519,
    signature_path_for,
    verify_file_signature,
    verify_manifest_signature_ed25519,
)

MANIFEST_PATH = os.path.join("app", "integrated_app", "security", "integrity_manifest.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="完整性清单签名 / 校验")
    parser.add_argument("--manifest", default=MANIFEST_PATH, help="清单文件路径")
    parser.add_argument("--verify", action="store_true", help="校验模式（默认签名模式）")
    args = parser.parse_args()

    if not os.path.exists(args.manifest):
        print(f"[FAIL] 清单文件不存在: {args.manifest}")
        print("       先运行 `python scripts/generate_integrity_manifest.py` 生成")
        return 1

    if args.verify:
        if verify_manifest_signature_ed25519(args.manifest):
            print(f"[PASS] 清单 Ed25519 签名有效: {args.manifest}")
            return 0
        if verify_file_signature(args.manifest):
            print(f"[PASS] 清单 HMAC 签名有效: {args.manifest}")
            return 0
        sig_ed = f"{args.manifest}.sig.ed25519"
        sig_hmac = signature_path_for(args.manifest)
        if not os.path.exists(sig_ed) and not os.path.exists(sig_hmac):
            print(f"[FAIL] 缺少签名文件（{sig_ed} 或 {sig_hmac}），请先执行签名")
        else:
            print(f"[FAIL] 清单签名无效（内容已变更或密钥不匹配）: {args.manifest}")
        return 1

    # 签名模式：Ed25519 优先（发布版），无私钥回退 HMAC（开发机）
    if manifest_private_key_path().exists():
        sig_path = sign_manifest_ed25519(args.manifest)
        if sig_path is None:
            print("[FAIL] Ed25519 签名写入失败")
            return 1
        # 签名后立即用内置公钥回验——密钥错配（私钥与内置公钥不配套）或签名
        # 写入异常在构建期立刻暴露，而不是等完整构建完成后在冒烟门禁才失败。
        if not verify_manifest_signature_ed25519(args.manifest):
            print("[FAIL] Ed25519 签名回验失败：私钥与内置公钥不匹配或签名文件异常")
            print("       请检查 data/.manifest_signing_key 与 security/manifest_signing_public_key.pem 是否配套")
            return 1
        print(f"[OK] 已签名(Ed25519): {args.manifest} -> {sig_path}")
        return 0

    key = get_hmac_fallback_key()
    if not key:
        print("[FAIL] 无法获取签名密钥（Ed25519 私钥或 HMAC 密钥均不可用）")
        return 1

    sig_path = sign_file(args.manifest, key)
    if not sig_path:
        print("[FAIL] 签名写入失败")
        return 1
    print(f"[OK] 已签名(HMAC，开发模式): {args.manifest} -> {sig_path}")
    print("    提示：发布构建请先生成 Ed25519 密钥对（scripts/generate_manifest_signing_key.py）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
