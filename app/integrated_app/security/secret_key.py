# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""服务端密钥管理模块 - 持久化 SECRET_KEY（桌面分发与安全加固 P0）。

来源：SeedVR2 的 ``security/secret_key.py``，适配 TTS_MultiModel 项目结构。

能力：
- ``get_hmac_fallback_key``：完整性清单 HMAC 回退签名密钥（64 字节，data/.integrity_hmac_secret）。
- ``harden_secret_file_permissions``：密钥文件权限收紧（POSIX 0600 / Windows ACL，读取时自愈）。
- ``sign_file`` / ``verify_file_signature``：任意文件的 HMAC-SHA256 签名与校验。
- ``generate_manifest_signing_keypair`` / ``sign_manifest_ed25519`` /
  ``verify_manifest_signature_ed25519``：清单 Ed25519 非对称签名（私钥只存构建机，
  公钥 PEM 内置 security/ 随代码分发，用户端可验签但不持有私钥）。

安全策略：
- 密钥使用 secrets.token_bytes 生成（256/512 位熵）
- 持久化文件权限限制为 0o600（POSIX）或 ACL 收紧（Windows）
- 支持环境变量覆盖（容器化/多实例部署场景）

使用方式:
    from app.integrated_app.security.secret_key import get_hmac_fallback_key

    key = get_hmac_fallback_key()  # 返回 bytes，64 字节
"""

import contextlib
import hashlib
import hmac
import logging
import os
import secrets
import stat
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# 默认 HMAC 回退密钥持久化路径（相对于项目根目录）。
# 决策（2026-09-10）：任务书引用参考实现的 data/.seedvr2_secret，属跨项目拷贝痕迹；
# 本仓既有密钥命名约定为 data/.{csrf_secret|history_hmac_key|pii_key|watermark_key}，
# 故采用项目一致命名 data/.integrity_hmac_secret（64 字节）。
_DEFAULT_KEY_FILE = "data/.integrity_hmac_secret"

# 密钥字节数（HMAC 回退签名用 64 字节）
_KEY_BYTES = 64

# 环境变量覆盖（优先级最高，容器化部署用）
_SECRET_KEY_ENV = "TTS_INTEGRITY_HMAC_SECRET"

# 签名文件后缀（manifest.json → manifest.json.sig）
SIGNATURE_SUFFIX = ".sig"

# 按密钥文件路径分键的缓存：不同 key_file 各自独立，绝不串用同一密钥。
# 锁保护"读文件/生成持久化"临界区，兑现模块文档的线程安全承诺。
_cache_lock = threading.Lock()
_cached_keys: dict[Path, bytes] = {}


def _default_key_file() -> Path:
    """默认密钥文件路径（项目根 data/.integrity_hmac_secret）。"""
    project_root = Path(__file__).resolve().parents[3]
    return project_root / _DEFAULT_KEY_FILE


def get_hmac_fallback_key(key_file: str | os.PathLike | None = None) -> bytes:
    """获取 HMAC 回退签名密钥。

    优先级：环境变量 TTS_INTEGRITY_HMAC_SECRET → 密钥文件 → 生成并持久化。
    按密钥文件路径分键缓存（线程安全），同一文件路径重复调用返回同一密钥；
    不同路径互不影响。

    Args:
        key_file: 密钥文件路径，为 None 时使用默认路径 data/.integrity_hmac_secret。

    Returns:
        64 字节随机密钥（bytes）。

    Raises:
        RuntimeError: 所有密钥来源均不可用时抛出（不应静默降级为弱密钥）。
    """
    env_key = os.environ.get(_SECRET_KEY_ENV, "").strip()
    if env_key:
        # 环境变量不进缓存：避免同一进程内环境变量变更被缓存掩盖
        try:
            return bytes.fromhex(env_key)
        except ValueError:
            return env_key.encode("utf-8")

    resolved = _default_key_file() if key_file is None else Path(key_file)
    cache_key = resolved.resolve()

    with _cache_lock:
        cached = _cached_keys.get(cache_key)
        if cached is not None:
            return cached

        if resolved.exists():
            try:
                hex_str = resolved.read_text(encoding="utf-8").strip()
                key = bytes.fromhex(hex_str)
                if len(key) != _KEY_BYTES:
                    logger.warning("密钥文件内容长度异常，重新生成密钥")
                    key = _generate_and_persist(resolved)
                # P0：读取时自愈历史部署的过宽权限
                harden_secret_file_permissions(resolved)
                logger.debug("从持久化文件加载 HMAC 回退签名密钥")
                _cached_keys[cache_key] = key
                return key
            except Exception as e:
                logger.warning(f"读取密钥文件失败，重新生成: {e}")

        key = _generate_and_persist(resolved)
        _cached_keys[cache_key] = key
        return key


def _generate_and_persist(key_file: Path) -> bytes:
    """生成新密钥并持久化到文件。

    Args:
        key_file: 密钥文件路径。

    Returns:
        新生成的密钥。
    """
    key = secrets.token_bytes(_KEY_BYTES)

    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key.hex(), encoding="utf-8")

    # P0：权限收紧（0o600，Windows 下尽力收紧 ACL）
    harden_secret_file_permissions(key_file)

    logger.info(f"已生成并持久化 HMAC 回退签名密钥: {key_file}")
    return key


def reset_cached_key() -> None:
    """清空全部密钥缓存（仅用于测试）。

    下次调用会重新从文件读取或生成。
    """
    with _cache_lock:
        _cached_keys.clear()


# ---------------------------------------------------------------------------
# P0：密钥文件权限收紧
# ---------------------------------------------------------------------------


def harden_secret_file_permissions(path: str | os.PathLike) -> bool:
    """收紧密钥文件权限（POSIX 0600；Windows 尽力收紧 ACL）。

    Windows 上 chmod 只能影响只读位，因此额外尝试用 icacls 移除继承并
    仅保留当前用户完全控制；icacls 不可用时仅记录 debug 日志。

    Args:
        path: 密钥文件路径。

    Returns:
        收紧成功返回 True；文件不存在或失败返回 False（不抛异常）。
    """
    p = Path(path)
    if not p.exists():
        return False

    try:
        if os.name == "nt":
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
            # 临时目录内不跑 icacls：修改 ACL 会破坏 pytest 临时目录回收（WinError 5）
            temp_root = os.path.realpath(os.environ.get("TEMP", "") or os.environ.get("TMP", "") or "")
            if temp_root and os.path.realpath(str(p)).startswith(temp_root):
                return True
            try:
                import subprocess

                # icacls 在中文 Windows 输出 GBK 文本，PYTHONUTF8=1 时 text=True 会按
                # UTF-8 解码崩溃线程。显式 encoding + errors=replace 保证解码永不中断。
                result = subprocess.run(
                    [
                        "icacls",
                        str(p),
                        "/inheritance:r",
                        "/grant:r",
                        f"{os.environ.get('USERNAME', '*')}:F",
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    check=False,
                )
                if result.returncode != 0:
                    logger.debug("icacls 收紧密钥权限未成功（可忽略）: %s", (result.stderr or "").strip()[:120])
            except Exception as e:  # noqa: BLE001 - 平台能力缺失不阻断
                logger.debug("Windows 密钥权限收紧跳过: %s", e)
            return True

        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        return True
    except Exception as e:  # noqa: BLE001 - 权限收紧失败不阻断业务
        logger.warning("密钥文件权限收紧失败: %s (%s)", p, e)
        return False


# ---------------------------------------------------------------------------
# P0：文件 HMAC-SHA256 签名 / 校验
# ---------------------------------------------------------------------------


def signature_path_for(file_path: str | os.PathLike) -> Path:
    """由被签名文件路径推导签名文件路径（追加 .sig）。"""
    return Path(f"{Path(file_path)}{SIGNATURE_SUFFIX}")


def sign_bytes(data: bytes, key: bytes) -> str:
    """对字节内容计算 HMAC-SHA256 十六进制摘要。"""
    return hmac.new(key, data, hashlib.sha256).hexdigest()


def sign_file(file_path: str | os.PathLike, key: bytes | None = None) -> Path | None:
    """为文件生成 HMAC-SHA256 签名（写入同名 .sig 文件）。

    Args:
        file_path: 待签名文件。
        key: 密钥；None 时自动获取（失败返回 None）。

    Returns:
        签名文件路径；文件不存在或无法获取密钥时返回 None。
    """
    p = Path(file_path)
    if not p.exists():
        return None
    try:
        key_bytes = key if key is not None else get_hmac_fallback_key()
    except Exception as e:  # noqa: BLE001 - 密钥不可用时降级为未签名
        logger.warning("获取签名密钥失败: %s", e)
        return None
    digest = sign_bytes(p.read_bytes(), key_bytes)
    sig_path = signature_path_for(p)
    sig_path.write_text(digest + "\n", encoding="utf-8")
    return sig_path


def verify_file_signature(file_path: str | os.PathLike, key: bytes | None = None) -> bool:
    """校验文件签名是否与当前内容匹配。

    Args:
        file_path: 待校验文件。
        key: 密钥；None 时自动获取。

    Returns:
        签名存在且匹配返回 True；无签名/无密钥/不匹配返回 False。
    """
    p = Path(file_path)
    sig_path = signature_path_for(p)
    if not p.exists() or not sig_path.exists():
        return False
    try:
        key_bytes = key if key is not None else get_hmac_fallback_key()
        expected = sig_path.read_text(encoding="utf-8").strip()
    except (OSError, RuntimeError) as e:
        logger.debug("签名校验读取失败: %s", e)
        return False
    if not expected:
        return False
    with contextlib.suppress(OSError):
        return hmac.compare_digest(sign_bytes(p.read_bytes(), key_bytes), expected)
    return False


# ---------------------------------------------------------------------------
# P0：清单签名非对称化（Ed25519：构建机私钥签发 / 发布包内置公钥验证）
# ---------------------------------------------------------------------------
# 背景：HMAC 是对称签名，用户端要验签就必须持有密钥；密钥进包=公开可伪造，
# 不进包=发布版 enforce 会把所有用户锁死（verify_file_signature 无密钥返回 False）。
# 非对称签名解决该矛盾：私钥只存构建机（data/.manifest_signing_key，随包三
# 重门禁排除），公钥 PEM 内置到 security/ 随代码分发（公开无害，仅用于验签）。

_MANIFEST_PRIVATE_KEY_NAME = ".manifest_signing_key"
_MANIFEST_PUBLIC_KEY_NAME = "manifest_signing_public_key.pem"
_MANIFEST_ED25519_SUFFIX = ".sig.ed25519"


def manifest_private_key_path() -> Path:
    """清单签名私钥路径（项目根 data/.manifest_signing_key，绝不进发布包）。"""
    return _default_key_file().parent / _MANIFEST_PRIVATE_KEY_NAME


def manifest_public_key_path() -> Path:
    """内置公钥路径（security/ 目录，随代码分发，仅用于验签）。"""
    return Path(__file__).parent / _MANIFEST_PUBLIC_KEY_NAME


def generate_manifest_signing_keypair(force: bool = False) -> tuple[Path, Path]:
    """生成 Ed25519 清单签名密钥对（构建机/签发机运行一次）。

    Args:
        force: True 时覆盖已存在的私钥（轮换签发身份，需同步重签所有发布清单）。

    Returns:
        (私钥路径, 公钥路径)。

    Raises:
        FileExistsError: 私钥已存在且 force=False。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    priv_path = manifest_private_key_path()
    if priv_path.exists() and not force:
        raise FileExistsError(f"清单签名私钥已存在: {priv_path}（如需轮换请用 --force，并重签历史发布清单）")

    private_key = ed25519.Ed25519PrivateKey.generate()
    priv_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path.parent.mkdir(parents=True, exist_ok=True)
    priv_path.write_bytes(priv_pem)
    harden_secret_file_permissions(priv_path)
    pub_path = manifest_public_key_path()
    pub_path.write_text(pub_pem.decode("utf-8"), encoding="utf-8")
    logger.info("清单签名密钥对已生成: 私钥=%s 公钥=%s", priv_path, pub_path)
    return priv_path, pub_path


def sign_manifest_ed25519(
    manifest_path: str | os.PathLike,
    private_key_path: str | os.PathLike | None = None,
) -> Path | None:
    """用 Ed25519 私钥为清单签名（写入 .sig.ed25519）。

    Args:
        manifest_path: 待签名清单文件。
        private_key_path: 私钥路径；None 时用默认 data/.manifest_signing_key。

    Returns:
        签名文件路径；文件/私钥缺失时返回 None。
    """
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except Exception as e:  # noqa: BLE001
        logger.warning("Ed25519 依赖不可用，无法非对称签名: %s", e)
        return None

    p = Path(manifest_path)
    if not p.exists():
        return None
    priv_path = manifest_private_key_path() if private_key_path is None else Path(private_key_path)
    if not priv_path.exists():
        logger.warning("清单签名私钥不存在: %s", priv_path)
        return None
    try:
        private_key = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
        if not isinstance(private_key, ed25519.Ed25519PrivateKey):
            logger.warning("清单签名私钥非 Ed25519，拒绝签名")
            return None
        sig = private_key.sign(p.read_bytes())
    except Exception as e:  # noqa: BLE001
        logger.warning("Ed25519 签名失败: %s", e)
        return None
    sig_path = Path(f"{p}{_MANIFEST_ED25519_SUFFIX}")
    sig_path.write_bytes(sig)
    return sig_path


def verify_manifest_signature_ed25519(
    manifest_path: str | os.PathLike,
    public_key_path: str | os.PathLike | None = None,
) -> bool:
    """用内置公钥验证清单的 Ed25519 签名。

    公钥公开无害（仅能验签、不能伪造），发布包内置它即可让用户端
    enforce 校验通过，同时私钥不出构建机。

    Args:
        manifest_path: 待校验清单文件。
        public_key_path: 公钥路径；None 时用内置 security/manifest_signing_public_key.pem。

    Returns:
        签名存在且有效返回 True；无公钥/无签名/不匹配返回 False。
    """
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except Exception as e:  # noqa: BLE001
        logger.debug("Ed25519 依赖不可用，跳过公钥验证: %s", e)
        return False

    p = Path(manifest_path)
    pub_path = manifest_public_key_path() if public_key_path is None else Path(public_key_path)
    sig_path = Path(f"{p}{_MANIFEST_ED25519_SUFFIX}")
    if not p.exists() or not pub_path.exists() or not sig_path.exists():
        return False
    try:
        public_key = serialization.load_pem_public_key(pub_path.read_bytes())
        if not isinstance(public_key, ed25519.Ed25519PublicKey):
            logger.debug("清单公钥非 Ed25519，拒绝验证")
            return False
        public_key.verify(sig_path.read_bytes(), p.read_bytes())
        return True
    except Exception as e:  # noqa: BLE001 — 任何验签失败都视为无效
        logger.debug("Ed25519 清单验签失败: %s", e)
        return False
