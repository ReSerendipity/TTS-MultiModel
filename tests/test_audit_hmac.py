"""security/audit.py HMAC 链测试（安全合规评估 P1-1）。

覆盖：
- log_audit 落内存环的条目带 prev_hash/record_hmac 链字段，且连续两条链头连续
- verify_audit_chain 对干净文件全绿、对篡改行检出、对 legacy（无链字段）行跳过
- 环境变量 TTS_AUDIT_HMAC_KEY 控制密钥（换密钥后链校验失败）

覆盖目标模块: app/integrated_app/security/audit.py
"""

import json

import pytest

from integrated_app.security.audit import (
    _chain_entry,
    _get_audit_hmac_secret,
    get_recent_audit,
    log_audit,
    verify_audit_chain,
)


def _write_chain_file(path, entries: list[dict]) -> None:
    """按 log_audit 落盘格式（ensure_ascii=False JSONL）写链文件。"""
    secret = _get_audit_hmac_secret()
    prev = ""
    lines = []
    for e in entries:
        chained = _chain_entry(e, prev, secret)
        prev = chained["record_hmac"]
        lines.append(json.dumps(chained, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _base_entry(**overrides) -> dict:
    e = {
        "ts": 1770000000.0,
        "iso": "2026-01-01T00:00:00Z",
        "action": "generation",
        "actor": "system",
        "severity": "info",
        "outcome": "success",
        "detail": "engine=voxcpm2",
        "request_id": "req-1",
    }
    e.update(overrides)
    return e


class TestAuditHmacChain:
    def test_log_audit_entry_has_chain_fields(self):
        log_audit("hmac_field_check", actor="sys", detail="d", severity="info")
        entry = get_recent_audit(limit=1)[-1]
        assert "prev_hash" in entry
        assert "record_hmac" in entry
        assert isinstance(entry["record_hmac"], str) and len(entry["record_hmac"]) == 64

    def test_chain_continuity_between_consecutive_entries(self):
        log_audit("hmac_chain_a", actor="sys", detail="a", severity="info")
        log_audit("hmac_chain_b", actor="sys", detail="b", severity="info")
        recent = get_recent_audit(limit=2)
        a, b = recent[-2], recent[-1]
        assert a["action"] == "hmac_chain_a"
        assert b["action"] == "hmac_chain_b"
        assert b["prev_hash"] == a["record_hmac"]

    def test_verify_ok_on_clean_file(self, tmp_path):
        f = tmp_path / "audit.log"
        _write_chain_file(f, [_base_entry(), _base_entry(actor="user", request_id="req-2")])
        result = verify_audit_chain(str(f))
        assert result["ok"] is True
        assert result["checked"] == 2
        assert result["tampered"] == 0
        assert result["legacy_skipped"] == 0

    def test_verify_detects_tampered_line(self, tmp_path):
        f = tmp_path / "audit.log"
        _write_chain_file(f, [_base_entry(), _base_entry(request_id="req-2")])
        lines = f.read_text(encoding="utf-8").splitlines()
        # 模拟"篡改内容后未重算链"的最常见形态：只改内容不动 hmac
        original = json.loads(lines[1])
        lines[1] = json.dumps({**original, "detail": "engine=evil-edit"}, ensure_ascii=False)
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = verify_audit_chain(str(f))
        assert result["ok"] is False
        assert result["tampered"] >= 1
        assert result["first_error"] is not None

    def test_verify_skips_legacy_lines(self, tmp_path):
        f = tmp_path / "audit.log"
        # legacy：无 record_hmac 字段的历史行
        legacy = json.dumps(_base_entry(action="legacy"), ensure_ascii=False)
        _write_chain_file(f, [_base_entry(request_id="req-2")])
        f.write_text(legacy + "\n" + f.read_text(encoding="utf-8"), encoding="utf-8")
        result = verify_audit_chain(str(f))
        assert result["ok"] is True
        assert result["legacy_skipped"] == 1
        assert result["checked"] == 1

    def test_key_change_breaks_chain(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TTS_AUDIT_HMAC_KEY", "test-key-a")
        f = tmp_path / "audit.log"
        _write_chain_file(f, [_base_entry()])
        monkeypatch.setenv("TTS_AUDIT_HMAC_KEY", "test-key-b")
        result = verify_audit_chain(str(f))
        assert result["ok"] is False
        assert result["tampered"] == 1

    def test_missing_file_ok(self, tmp_path):
        result = verify_audit_chain(str(tmp_path / "nope.log"))
        assert result["ok"] is True
        assert result["checked"] == 0

    def test_chain_entry_matches_history_db_pattern(self):
        """链模式与 history_db 一致：HMAC(secret, prev_hash + sha256(record))。"""
        import hashlib
        import hmac

        secret = b"k"
        entry = _base_entry()
        prev = "p" * 64
        chained = _chain_entry(entry, prev, secret)
        fields = ("ts", "iso", "action", "actor", "severity", "outcome", "detail", "request_id")
        record_str = "|".join(str(entry.get(f, "")) for f in fields)
        record_hash = hashlib.sha256(record_str.encode("utf-8")).hexdigest()
        expected = hmac.new(secret, f"{prev}{record_hash}".encode(), hashlib.sha256).hexdigest()
        assert chained["record_hmac"] == expected
