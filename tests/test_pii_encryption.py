"""PII 字段级加密与迁移单元测试（P1 安全整改）。

覆盖目标模块:
  - app/integrated_app/history_db.py（_encrypt_pii / _decrypt_pii / _get_pii_cipher）
  - scripts/migrate_pii_encryption.py（迁移逻辑）
"""

import sqlite3
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "app"))
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

from integrated_app.history_db import (  # noqa: E402
    _PII_PREFIX,
    _decrypt_pii,
    _encrypt_pii,
    _get_pii_cipher,
)


class TestPIIEncryption:
    """_encrypt_pii / _decrypt_pii 往返与向后兼容。"""

    def test_encrypt_decrypt_roundtrip(self):
        """加密后解密还原原文。"""
        original = "这是一段包含 PII 的文本预览"
        encrypted = _encrypt_pii(original)
        assert encrypted.startswith(_PII_PREFIX)
        assert encrypted != original
        decrypted = _decrypt_pii(encrypted)
        assert decrypted == original

    def test_empty_string_passthrough(self):
        """空字符串不加密。"""
        assert _encrypt_pii("") == ""
        assert _decrypt_pii("") == ""

    def test_plaintext_backward_compatible(self):
        """旧明文行（无 enc: 前缀）解密时原样返回。"""
        plain = "旧版明文记录"
        assert _decrypt_pii(plain) == plain

    def test_encrypted_value_has_prefix(self):
        """加密值以 enc: 前缀标识。"""
        encrypted = _encrypt_pii("hello")
        assert encrypted.startswith("enc:")

    def test_cipher_is_valid_fernet_key(self):
        """自动生成的密钥必须是合法 Fernet 密钥（32 字节 base64，44 字符）。

        回归测试：旧版用 secrets.token_urlsafe(44) 生成 59 字符无效密钥，
        导致加密静默降级为明文。
        """
        cipher = _get_pii_cipher()
        assert cipher is not None
        # Fernet 实例可正常加密解密即证明密钥合法
        token = cipher.encrypt(b"test")
        assert cipher.decrypt(token) == b"test"


class TestPIIMigration:
    """迁移脚本逻辑测试（使用临时 DB）。"""

    def _make_history_db(self, tmp_path: Path, records: list[tuple[int, str]]) -> Path:
        """创建含完整表结构的临时 DB，用原始 SQL 插入明文记录（绕过 add_record 的自动加密）。"""
        from integrated_app.history_db import HistoryDatabase

        db_path = tmp_path / "history.db"
        HistoryDatabase(str(db_path))  # 建表
        conn = sqlite3.connect(str(db_path))
        conn.executemany(
            "INSERT INTO generation_history "
            "(id, filename, filepath, created_at, file_size_bytes, text_preview, engine, "
            "is_success, is_degraded, hidden, created_timestamp, file_missing) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, 0, 1700000000.0, 0)",
            [
                (
                    rid,
                    f"file_{rid}.wav",
                    str(tmp_path / f"file_{rid}.wav"),
                    "2026-01-01T00:00:00",
                    1024,
                    text,
                    "voxcpm2",
                )
                for rid, text in records
            ],
        )
        conn.commit()
        conn.close()
        return db_path

    def test_migration_encrypts_plaintext(self, tmp_path, monkeypatch):
        """迁移将明文行加密为 enc: 前缀。"""
        db_path = self._make_history_db(tmp_path, [(1, "明文记录一"), (2, "明文记录二")])

        # 模拟 get_history_db 返回指向临时 DB 的实例
        from integrated_app.history_db import HistoryDatabase

        fake_db = HistoryDatabase(str(db_path))
        monkeypatch.setattr("migrate_pii_encryption.get_history_db", lambda: fake_db)

        from migrate_pii_encryption import migrate

        rc = migrate(dry_run=False, batch_size=10)
        assert rc == 0

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT text_preview FROM generation_history ORDER BY id").fetchall()
        conn.close()
        for (text,) in rows:
            assert text.startswith(_PII_PREFIX)
            assert _decrypt_pii(text) in ("明文记录一", "明文记录二")

    def test_migration_idempotent(self, tmp_path, monkeypatch):
        """重复运行不双重加密。"""
        db_path = self._make_history_db(tmp_path, [(1, "唯一记录")])

        from integrated_app.history_db import HistoryDatabase

        fake_db = HistoryDatabase(str(db_path))
        monkeypatch.setattr("migrate_pii_encryption.get_history_db", lambda: fake_db)

        from migrate_pii_encryption import migrate

        assert migrate(dry_run=False) == 0
        assert migrate(dry_run=False) == 0  # 第二次应无操作

        conn = sqlite3.connect(str(db_path))
        (text,) = conn.execute("SELECT text_preview FROM generation_history WHERE id=1").fetchone()
        conn.close()
        # 只应出现一个 enc: 前缀（未双重加密）
        assert text.startswith(_PII_PREFIX)
        assert not text.startswith(_PII_PREFIX + _PII_PREFIX)
        assert _decrypt_pii(text) == "唯一记录"

    def test_migration_dry_run_no_write(self, tmp_path, monkeypatch):
        """dry-run 不修改数据库。"""
        db_path = self._make_history_db(tmp_path, [(1, "不应被修改")])

        from integrated_app.history_db import HistoryDatabase

        fake_db = HistoryDatabase(str(db_path))
        monkeypatch.setattr("migrate_pii_encryption.get_history_db", lambda: fake_db)

        from migrate_pii_encryption import migrate

        assert migrate(dry_run=True) == 0

        conn = sqlite3.connect(str(db_path))
        (text,) = conn.execute("SELECT text_preview FROM generation_history WHERE id=1").fetchone()
        conn.close()
        assert text == "不应被修改"  # 未加密


class TestPIIEncryptionSearchFallback:
    """PII 加密开启时搜索降级为 filename-only。"""

    def test_search_by_filename_works_when_encrypted(self, tmp_path):
        """加密开启时，按 filename 搜索仍可命中。"""
        from integrated_app.history_db import HistoryDatabase

        db = HistoryDatabase(str(tmp_path / "h.db"))
        db.add_record(
            filename="special_report.wav",
            filepath=str(tmp_path / "special_report.wav"),
            created_at="2026-01-01T00:00:00",
            file_size=1024,
            text_preview="这是一段加密的文本预览内容",
        )
        results = db.query_records(search_text="special_report")
        assert len(results) == 1

    def test_search_by_text_content_fails_when_encrypted(self, tmp_path):
        """加密开启时，按 text_preview 内容搜索无法命中（密文不可搜索）。"""
        from integrated_app.history_db import HistoryDatabase

        db = HistoryDatabase(str(tmp_path / "h.db"))
        db.add_record(
            filename="test.wav",
            filepath=str(tmp_path / "test.wav"),
            created_at="2026-01-01T00:00:00",
            file_size=1024,
            text_preview="独有的关键词xyz123",
        )
        # 按 text_preview 内容搜索应返回 0（密文）
        results = db.query_records(search_text="独有的关键词xyz123")
        assert len(results) == 0
