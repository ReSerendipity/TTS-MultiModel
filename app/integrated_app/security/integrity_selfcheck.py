# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""启动时核心模块完整性自检。

在应用启动时计算核心安全模块的 SHA-256 哈希值并与预期值比对，
检测文件是否被篡改或注入后门 (CWE-912 供应链投毒防御)。
桌面分发与安全加固 P0 新增：清单签名校验（Ed25519 主签名 + HMAC 回退）+ enforce 阻断。

来源：SeedVR2 的 ``security/integrity_selfcheck.py``，适配 TTS_MultiModel 项目结构。

覆盖的核心模块:
    - app_server.py              (应用入口)
    - config.py                  (配置加载)
    - config_models.py           (Pydantic 配置模型)
    - engine_interface.py        (引擎接口抽象)
    - model_manager.py           (模型管理)
    - model_registry.py          (引擎注册表)
    - history_db.py              (历史记录数据库)
    - i18n.py                    (国际化)
    - auth.py                    (认证中间件)
    - middleware/csrf.py         (CSRF 中间件)
    - middleware/error_handler.py (错误处理中间件)
    - middleware/rate_limit.py   (速率限制中间件)
    - middleware/request_id.py   (请求 ID 中间件)
    - security/integrity_check.py (完整性校验)
    - security/integrity_selfcheck.py (自校验本身)
    - security/secret_key.py     (密钥管理与清单签名/验签, P0 新增)

使用方式:
    from .security.integrity_selfcheck import run_startup_selfcheck

    results = run_startup_selfcheck(enforce=True)  # enforce: 失败即抛 RuntimeError
    if results["failed"]:
        print("WARNING: 核心模块完整性校验失败！")

哈希清单文件:
    哈希值存储在 ``app/integrated_app/security/integrity_manifest.json`` 中。
    首次运行或代码更新后，运行 ``python scripts/generate_integrity_manifest.py`` 重新生成，
    再运行 ``python scripts/sign_integrity_manifest.py`` 签发（Ed25519 优先 / HMAC 回退）。
    若清单文件不存在，自检跳过并提示生成命令。
"""

import hashlib
import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# 核心安全模块清单 (相对于 app/integrated_app/)
_CORE_MODULES = [
    "app_server.py",
    "config.py",
    "config_models.py",
    "engine_interface.py",
    "model_manager.py",
    "model_registry.py",
    "history_db.py",
    "i18n.py",
    "auth.py",
    "middleware/csrf.py",
    "middleware/error_handler.py",
    "middleware/rate_limit.py",
    "middleware/request_id.py",
    "security/integrity_check.py",
    "security/integrity_selfcheck.py",
    "security/secret_key.py",
]

# 清单文件路径
_MANIFEST_FILENAME = "integrity_manifest.json"

# 最近一次自检结果（供 /api/system/health 等只读暴露，线程安全）
_last_result: dict = {}
_last_result_lock = threading.Lock()


def get_last_selfcheck() -> dict:
    """返回最近一次自检结果（未运行过时返回空 dict）。"""
    with _last_result_lock:
        return dict(_last_result)


def _remember(result: dict) -> dict:
    with _last_result_lock:
        _last_result.clear()
        _last_result.update(result)
    return result


def _get_manifest_path() -> Path:
    """获取清单文件路径。"""
    return Path(__file__).parent / _MANIFEST_FILENAME


def _compute_file_sha256(filepath: Path) -> str:
    """计算文件 SHA-256。"""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def verify_manifest_signature(manifest_path: Path | str) -> bool:
    """校验完整性清单签名（P0：Ed25519 优先，HMAC 回退）。

    发布版：清单由构建机用私钥签发，包内内置公钥验证 → 用户端可验签、
    无需持有私钥，enforce 模式下正常启动且防篡改。
    开发机：无 Ed25519 私钥/公钥时回退 HMAC（data/.integrity_hmac_secret，
    本机运行 scripts/sign_integrity_manifest.py 后即可验签）。

    Args:
        manifest_path: 清单文件路径。

    Returns:
        任一签名有效返回 True；否则 False。
    """
    # 1) Ed25519 公钥验证（发布版主路径；公钥内置、私钥不出构建机）
    try:
        from .secret_key import verify_manifest_signature_ed25519

        if verify_manifest_signature_ed25519(manifest_path):
            return True
    except Exception as e:  # noqa: BLE001 — 模块不可用时按未签名处理
        logger.debug("[SELF-CHECK] Ed25519 验签不可用: %s", e)

    # 2) HMAC 回退（开发机/历史清单）
    try:
        from .secret_key import verify_file_signature
    except Exception as e:  # noqa: BLE001
        logger.debug("[SELF-CHECK] 签名校验模块不可用: %s", e)
        return False
    return verify_file_signature(manifest_path)


def run_startup_selfcheck(enforce: bool = False) -> dict:
    """执行启动时核心模块完整性自检。

    流程:
        1. 读取 integrity_manifest.json 清单文件
        2. 若清单不存在，跳过自检并提示生成命令
        3. 校验清单签名（Ed25519 优先，HMAC 回退）；enforce 时签名无效 → RuntimeError
        4. 对每个核心模块计算当前 SHA-256
        5. 与清单中的预期哈希比对；enforce 时不一致 → RuntimeError
        6. 不一致的文件记录为失败

    Args:
        enforce: True 时校验失败抛出 RuntimeError 阻断启动（fail-fast）；
            清单缺失仍跳过不阻断（避免误伤首次部署）。

    Returns:
        dict: 包含 total/passed/failed/skipped/failed_files/manifest_signed 字段。

    Raises:
        RuntimeError: enforce=True 且存在校验失败的文件或清单签名无效。
    """
    manifest_path = _get_manifest_path()
    app_dir = Path(__file__).parent.parent  # app/integrated_app/

    # 读取清单
    if not manifest_path.exists():
        logger.info(
            "[SELF-CHECK] 完整性清单不存在，跳过自检。"
            " 运行 `python scripts/generate_integrity_manifest.py` 生成清单以启用启动自检。"
        )
        return _remember(
            {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "skipped": len(_CORE_MODULES),
                "failed_files": [],
                "manifest_signed": False,
            }
        )

    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"[SELF-CHECK] 清单文件读取失败: {e}")
        return _remember(
            {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "skipped": len(_CORE_MODULES),
                "failed_files": [],
                "manifest_signed": False,
            }
        )

    # P0：校验清单本身的签名。清单与被校验代码同目录，"能改代码就能同步改清单"
    # 是原方案的结构性弱点；签名把信任根外移到密钥（data/ 下 0600，或构建机私钥）。
    signature_ok = verify_manifest_signature(manifest_path)
    if not signature_ok:
        message = (
            "[SELF-CHECK] 完整性清单缺少有效签名"
            "（运行 `python scripts/sign_integrity_manifest.py` 生成）。"
            "未签名清单无法防御'同步篡改代码与清单'的投毒路径"
        )
        logger.warning(message)
        if enforce:
            raise RuntimeError(message + "；已开启 enforce，拒绝启动")

    expected_hashes = manifest.get("files", {})
    total = 0
    passed = 0
    failed = 0
    skipped = 0
    failed_files: list[str] = []

    for module_rel in _CORE_MODULES:
        module_path = app_dir / module_rel
        if not module_path.exists():
            logger.warning(f"[SELF-CHECK] 核心模块不存在: {module_path}")
            skipped += 1
            continue

        expected = expected_hashes.get(module_rel, "")

        if not expected:
            skipped += 1
            continue

        total += 1

        try:
            actual = _compute_file_sha256(module_path)
        except OSError as e:
            logger.error(f"[SELF-CHECK] 无法读取 {module_path}: {e}")
            failed += 1
            failed_files.append(module_rel)
            continue

        if actual == expected:
            passed += 1
            logger.debug(f"[SELF-CHECK] ✓ {module_rel}")
        else:
            failed += 1
            failed_files.append(module_rel)
            logger.error(
                f"[SECURITY WARNING] 核心模块完整性校验失败: {module_rel}\n"
                f"    期望 SHA-256: {expected}\n"
                f"    实际 SHA-256: {actual}\n"
                f"    该文件可能已被篡改！请检查代码完整性。"
            )

    # 输出汇总
    if failed > 0:
        logger.error(
            "=" * 60 + "\n"
            "[SECURITY] ⚠️  核心模块完整性自检失败！\n"
            f"    通过: {passed}/{total}, 失败: {failed}, 跳过: {skipped}\n"
            f"    失败文件: {', '.join(failed_files)}\n"
            "    请检查上述文件是否被篡改，或运行 "
            "`python scripts/generate_integrity_manifest.py` 更新清单。\n" + "=" * 60
        )
    elif passed > 0:
        logger.info(f"[SELF-CHECK] 核心模块完整性自检通过: {passed}/{total} ✓")

    if enforce and failed > 0:
        raise RuntimeError(f"核心模块完整性校验失败（enforce 模式，拒绝启动）: {', '.join(failed_files)}")

    return _remember(
        {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "failed_files": failed_files,
            "manifest_signed": signature_ok,
        }
    )
