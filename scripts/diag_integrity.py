#!/usr/bin/env python3
"""scripts/diag_integrity.py — 完整性门禁逐环节诊断（发布门禁五步的②⑤支撑）。

报告2 §七-3：CI 只调脚本路径，不内联 ``python -c``（YAML 块标量易碎）。
本脚本对「给定 app 目录」逐环节打印 True/False，任一环节 False → 退出码 1，
输出可直接二分定位环境问题（import 失败 / 清单缺失 / 签名文件缺失 / 公钥缺失 /
验签失败 / 自检失败）。

用法：
    python scripts/diag_integrity.py
        --app-dir app/integrated_app          # 被测应用目录（默认仓库 app/integrated_app）
        --enforce                             # 追加 run_startup_selfcheck(enforce=True) 探测
        --manifest <路径>                     # 覆盖清单路径（默认 <app-dir>/security/integrity_manifest.json）

输出示例：
    [diag] import_ok=True manifest_exists=True sig_exists=True pub_exists=True VERIFY=True selfcheck=True
"""

import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="完整性门禁逐环节诊断")
    parser.add_argument("--app-dir", default=None, help="被测应用目录（integrated_app），默认仓库 app/integrated_app")
    parser.add_argument("--manifest", default=None, help="清单路径（默认 <app-dir>/security/integrity_manifest.json）")
    parser.add_argument("--enforce", action="store_true", help="追加 enforce=True 自检探测")
    args = parser.parse_args()

    results: list[tuple[str, bool, str]] = []

    # ① import OK：被测目录可导入 integrated_app.security 模块
    if args.app_dir:
        app_dir = os.path.abspath(args.app_dir)
        sys.path.insert(0, os.path.dirname(app_dir))
        sys.path.insert(0, app_dir)
    else:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        app_dir = os.path.join(repo, "app", "integrated_app")
        sys.path.insert(0, os.path.join(repo, "app"))
        sys.path.insert(0, app_dir)

    try:
        from integrated_app.security import integrity_selfcheck  # noqa: PLC0415

        results.append(("import_ok", True, f"module={integrity_selfcheck.__name__}"))
    except Exception as exc:  # noqa: BLE001
        results.append(("import_ok", False, f"exc={exc!r}"))
        _report(results)
        return 1

    manifest_path = args.manifest or os.path.join(app_dir, "security", "integrity_manifest.json")
    sig_path = manifest_path + ".sig.ed25519"
    pub_path = os.path.join(os.path.dirname(manifest_path), "manifest_signing_public_key.pem")

    results.append(("manifest_exists", os.path.isfile(manifest_path), manifest_path))
    results.append(("sig_exists", os.path.isfile(sig_path), sig_path))
    results.append(("pub_exists", os.path.isfile(pub_path), pub_path))

    if os.path.isfile(manifest_path):
        try:
            verify = bool(integrity_selfcheck.verify_manifest_signature(manifest_path))
            results.append(("VERIFY", verify, f"manifest={manifest_path}"))
        except Exception as exc:  # noqa: BLE001
            results.append(("VERIFY", False, f"exc={exc!r}"))
    else:
        results.append(("VERIFY", False, "manifest missing"))

    try:
        result = integrity_selfcheck.run_startup_selfcheck(enforce=False)
        ok = bool(result.get("failed", 1) == 0)
        results.append(("selfcheck", ok, f"result={result}"))
    except Exception as exc:  # noqa: BLE001
        results.append(("selfcheck", False, f"exc={exc!r}"))

    if args.enforce:
        try:
            integrity_selfcheck.run_startup_selfcheck(enforce=True)
            results.append(("selfcheck_enforce", True, "no exception"))
        except RuntimeError:
            results.append(("selfcheck_enforce", False, "RuntimeError（enforce 阻断，符合预期）"))
        except Exception as exc:  # noqa: BLE001
            results.append(("selfcheck_enforce", False, f"exc={exc!r}"))

    _report(results)
    return 0 if all(ok for _, ok, _ in results) else 1


def _report(results: list[tuple[str, bool, str]]) -> None:
    parts = []
    for name, ok, detail in results:
        parts.append(f"{name}={'True' if ok else 'False'}")
        print(f"[diag] {name}={ok}  {detail}")
    print("[diag] " + " ".join(parts))


if __name__ == "__main__":
    raise SystemExit(main())
