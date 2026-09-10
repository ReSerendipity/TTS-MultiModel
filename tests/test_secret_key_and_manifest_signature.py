"""密钥管理与清单签名/验签测试（桌面分发与安全加固 P0）。

覆盖目标模块: app/integrated_app/security/secret_key.py + integrity_selfcheck.py
"""

import os
from pathlib import Path

import pytest

from integrated_app.security.integrity_selfcheck import (
    run_startup_selfcheck,
    verify_manifest_signature,
)
from integrated_app.security.secret_key import (
    get_hmac_fallback_key,
    harden_secret_file_permissions,
    reset_cached_key,
    sign_file,
    sign_manifest_ed25519,
    verify_file_signature,
    verify_manifest_signature_ed25519,
)

# 仓库内真实清单（已由构建机签发，随代码提交）
_REAL_MANIFEST = (
    Path(__file__).resolve().parent.parent / "app" / "integrated_app" / "security" / "integrity_manifest.json"
)


class TestHmacFallbackKey:
    def test_generates_64_bytes_and_persists(self, tmp_path):
        key_file = tmp_path / ".integrity_hmac_secret"
        key = get_hmac_fallback_key(key_file)
        assert len(key) == 64
        assert key_file.exists()
        # 重新读取应返回同一密钥
        reset_cached_key()
        assert get_hmac_fallback_key(key_file) == key

    def test_env_override_takes_priority(self, tmp_path, monkeypatch):
        key_file = tmp_path / ".k"
        env_key = os.urandom(64).hex()
        monkeypatch.setenv("TTS_INTEGRITY_HMAC_SECRET", env_key)
        assert get_hmac_fallback_key(key_file) == bytes.fromhex(env_key)

    def test_harden_permissions_no_raise(self, tmp_path):
        p = tmp_path / "k.txt"
        p.write_bytes(b"x" * 64)
        # 只要求不抛异常且返回 True（Windows ACL 尽力收紧，POSIX 0600）
        assert harden_secret_file_permissions(p) is True


class TestFileSignature:
    def test_sign_and_verify_roundtrip(self, tmp_path):
        key_file = tmp_path / ".k"
        target = tmp_path / "integrity_manifest.json"
        target.write_text('{"files": {}}', encoding="utf-8")
        sig = sign_file(target, get_hmac_fallback_key(key_file))
        assert sig is not None
        assert sig.exists()
        assert verify_file_signature(target, get_hmac_fallback_key(key_file)) is True

    def test_tamper_detected(self, tmp_path):
        key_file = tmp_path / ".k"
        target = tmp_path / "m.json"
        target.write_text("ORIGINAL", encoding="utf-8")
        sign_file(target, get_hmac_fallback_key(key_file))
        target.write_text("TAMPERED", encoding="utf-8")
        assert verify_file_signature(target, get_hmac_fallback_key(key_file)) is False

    def test_missing_signature_fails(self, tmp_path):
        key_file = tmp_path / ".k"
        target = tmp_path / "m.json"
        target.write_text("x", encoding="utf-8")
        assert verify_file_signature(target, get_hmac_fallback_key(key_file)) is False


class TestEd25519ManifestSignature:
    def test_keypair_sign_verify_roundtrip(self, tmp_path):
        priv = tmp_path / ".manifest_signing_key"
        pub = tmp_path / "manifest_signing_public_key.pem"
        manifest = tmp_path / "integrity_manifest.json"
        manifest.write_text('{"files": {}}', encoding="utf-8")

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        private_key = ed25519.Ed25519PrivateKey.generate()
        priv.write_bytes(
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        pub.write_text(
            private_key.public_key()
            .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
            .decode("utf-8"),
            encoding="utf-8",
        )

        sig = sign_manifest_ed25519(manifest, priv)
        assert sig is not None
        assert verify_manifest_signature_ed25519(manifest, pub) is True

        # 篡改后验签失败
        manifest.write_text('{"files": {"x": "y"}}', encoding="utf-8")
        assert verify_manifest_signature_ed25519(manifest, pub) is False

    def test_repo_manifest_is_signed_and_valid(self):
        """真实仓库清单必须带有效 Ed25519 签名（密钥轮换后需重签，防回归）。"""
        if not _REAL_MANIFEST.exists():
            pytest.skip("仓库清单不存在")
        assert verify_manifest_signature_ed25519(_REAL_MANIFEST) is True

    def test_verify_manifest_signature_ed25519_first(self):
        if not _REAL_MANIFEST.exists():
            pytest.skip("仓库清单不存在")
        assert verify_manifest_signature(_REAL_MANIFEST) is True


class TestStartupSelfcheckEnforce:
    def test_enforce_passes_with_signed_manifest(self):
        """真实仓库：已签名 + 哈希一致 → enforce=True 不抛异常。"""
        if not _REAL_MANIFEST.exists():
            pytest.skip("仓库清单不存在")
        result = run_startup_selfcheck(enforce=True)
        assert result["failed"] == 0
        assert result["manifest_signed"] is True
        assert result["total"] > 0

    def test_enforce_blocks_on_invalid_signature(self, monkeypatch, tmp_path):
        """篡改清单内容（哈希对不上）→ enforce=True 抛 RuntimeError。"""
        if not _REAL_MANIFEST.exists():
            pytest.skip("仓库清单不存在")
        from integrated_app.security import integrity_selfcheck as mod

        tampered = tmp_path / "integrity_manifest.json"
        tampered.write_text('{"files": {"app_server.py": "deadbeef"}}', encoding="utf-8")

        monkeypatch.setattr(mod, "_get_manifest_path", lambda: tampered)
        with pytest.raises(RuntimeError, match="enforce"):
            run_startup_selfcheck(enforce=True)

    def test_no_enforce_reports_failure_without_raising(self, monkeypatch, tmp_path):
        if not _REAL_MANIFEST.exists():
            pytest.skip("仓库清单不存在")
        from integrated_app.security import integrity_selfcheck as mod

        tampered = tmp_path / "integrity_manifest.json"
        tampered.write_text('{"files": {"app_server.py": "deadbeef"}}', encoding="utf-8")
        monkeypatch.setattr(mod, "_get_manifest_path", lambda: tampered)
        result = run_startup_selfcheck(enforce=False)
        assert result["failed"] >= 1
        assert "app_server.py" in result["failed_files"]
