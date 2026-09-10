#!/usr/bin/env python3
"""生成完整性清单签名密钥对（桌面分发与安全加固 P0，签发机/构建机运行一次）。

用法：
    python scripts/generate_manifest_signing_key.py            # 生成（已存在则报错）
    python scripts/generate_manifest_signing_key.py --force    # 覆盖轮换（需重签历史清单）

产物：
- 私钥：data/.manifest_signing_key（Ed25519 PKCS8 PEM，0600，**绝不进发布包/仓库**）
- 公钥：app/integrated_app/security/manifest_signing_public_key.pem（随代码分发，仅用于验签）
- CI Secret：脚本输出私钥 base64，用于设置 GitHub Actions Secret MANIFEST_SIGNING_KEY_B64

密钥 SOP（SOP-19）：
1. 生成后立即把私钥 base64 注入 CI Secret（本脚本输出）。
2. 离线备份私钥到独立介质；备份后本机副本保留于 data/（gitignore），
   不删除——删除会导致无法本地重签与回验（决策留痕：可逆性优先，见执行对照表）。
3. 私钥泄漏 = 信任根丢失，需 --force 轮换并重签历史发布清单。

退出码：0 成功；1 失败。
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.integrated_app.security.secret_key import (  # noqa: E402
    generate_manifest_signing_keypair,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成完整性清单签名密钥对")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的私钥（轮换签发身份）")
    args = parser.parse_args()

    try:
        priv_path, pub_path = generate_manifest_signing_keypair(force=args.force)
    except FileExistsError as e:
        print(f"[FAIL] {e}")
        return 1

    priv_b64 = base64.b64encode(priv_path.read_bytes()).decode("ascii")
    print(f"[OK] 私钥已生成: {priv_path}（0600，gitignore，绝不入包）")
    print(f"[OK] 公钥已生成: {pub_path}（随代码分发）")
    print()
    print("=" * 68)
    print(" 下一步（SOP-19 密钥分发）：")
    print("  1) 设置 GitHub Actions Secret（本仓库 -> Settings -> Secrets）：")
    print("     gh secret set MANIFEST_SIGNING_KEY_B64 --repo ReSerendipity/TTS_MultiModel")
    print(f"     值（私钥 base64，共 {len(priv_b64)} 字符）：")
    print(f"     {priv_b64}")
    print("  2) 离线备份私钥到独立介质（如密码管理器/离线 U 盘）。")
    print("  3) 运行签名：python scripts/sign_integrity_manifest.py")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
