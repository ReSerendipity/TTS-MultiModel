"""security/audit.py 单元测试 — 结构化审计日志与轮转。

覆盖目标模块: app/integrated_app/security/audit.py
"""

from integrated_app.security.audit import (
    _AUDIT_MAX_BACKUPS,
    _AUDIT_MAX_BYTES,
    _rotate_audit_log_if_needed,
    get_recent_audit,
    log_audit,
)


class TestAuditLogRotation:
    """P1 安全整改：审计日志按大小轮转。"""

    def test_no_rotation_under_limit(self, tmp_path):
        """文件小于阈值时不轮转。"""
        log_file = tmp_path / "audit.log"
        log_file.write_text("small\n", encoding="utf-8")
        _rotate_audit_log_if_needed(str(log_file))
        assert log_file.exists()
        assert not (tmp_path / "audit.log.1").exists()

    def test_rotation_over_limit(self, tmp_path):
        """文件超过阈值时轮转（当前→.1）。"""
        log_file = tmp_path / "audit.log"
        log_file.write_bytes(b"x" * (_AUDIT_MAX_BYTES + 100))
        _rotate_audit_log_if_needed(str(log_file))
        assert not log_file.exists()
        assert (tmp_path / "audit.log.1").exists()

    def test_rotation_shifts_backups(self, tmp_path):
        """多次轮转时备份依次后移。"""
        log_file = tmp_path / "audit.log"
        # 预创建 .1 和 .2
        (tmp_path / "audit.log.1").write_text("backup1\n", encoding="utf-8")
        (tmp_path / "audit.log.2").write_text("backup2\n", encoding="utf-8")
        log_file.write_bytes(b"x" * (_AUDIT_MAX_BYTES + 100))
        _rotate_audit_log_if_needed(str(log_file))
        assert (tmp_path / "audit.log.1").exists()  # 原当前日志
        assert (tmp_path / "audit.log.2").exists()  # 原 .1
        assert (tmp_path / "audit.log.3").exists()  # 原 .2

    def test_rotation_deletes_oldest_backup(self, tmp_path):
        """超过最大备份数时删除最旧的。"""
        log_file = tmp_path / "audit.log"
        for i in range(1, _AUDIT_MAX_BACKUPS + 1):
            (tmp_path / f"audit.log.{i}").write_text(f"backup{i}\n", encoding="utf-8")
        log_file.write_bytes(b"x" * (_AUDIT_MAX_BYTES + 100))
        _rotate_audit_log_if_needed(str(log_file))
        # 最旧的 .{MAX_BACKUPS} 应被删除（原 .3 被删，原 .2→.3）
        assert not (tmp_path / f"audit.log.{_AUDIT_MAX_BACKUPS + 1}").exists()

    def test_rotation_nonexistent_file_noop(self, tmp_path):
        """文件不存在时不报错。"""
        _rotate_audit_log_if_needed(str(tmp_path / "nonexistent.log"))
        assert True


class TestLogAudit:
    """log_audit 基本行为测试。"""

    def test_log_audit_writes_to_ring(self):
        """log_audit 写入内存环形缓冲。"""
        before = len(get_recent_audit(limit=500))
        log_audit("test_action", actor="tester", detail="unit test", severity="info")
        after = get_recent_audit(limit=500)
        assert len(after) == before + 1
        assert after[-1]["action"] == "test_action"
        assert after[-1]["actor"] == "tester"

    def test_log_audit_entry_has_required_fields(self):
        """审计条目包含必要字段。"""
        log_audit("field_check", actor="sys", detail="d", severity="warning", outcome="blocked")
        entry = get_recent_audit(limit=1)[-1]
        for field in ("ts", "iso", "action", "actor", "severity", "outcome", "detail"):
            assert field in entry

    def test_log_audit_does_not_contain_pii_text(self):
        """detail 字段不自动包含完整文本（调用方责任，此处验证结构）。"""
        log_audit("content_blocked", actor="user", detail="category=violence", outcome="blocked")
        entry = get_recent_audit(limit=1)[-1]
        assert "category=violence" in entry["detail"]
