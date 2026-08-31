"""history_db 边界值与负向路径测试 —— P1：提升参数化与负向断言密度。

背景：评估报告指出全仓 ``parametrize`` 仅 9 处 / 1674 用例（0.5%），
边界值与等价类驱动几乎缺失，负向断言（``pytest.raises``）密度仅 5.2%。
本模块针对 torch-free 的 ``HistoryDatabase`` 补齐参数化边界与负向路径，
重点锁死已有防御逻辑（limit/offset 钳制、幂等清理、缺失键返回）不被回归。

所有断言均基于实测行为（临时探针验证），非凭空假设。
"""

import os
import sys

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from integrated_app.history_db import create_history_db  # noqa: E402

# 记录总数刻意取 > 20：20 是 get_paginated_records 对非法 limit 的钳制值，
# 只有数据量超过 20 才能区分「被钳制到 20」与「返回全部」。
_TOTAL_RECORDS = 25


@pytest.fixture
def db(tmp_path):
    """创建隔离的历史库并预置 _TOTAL_RECORDS 条记录。"""
    handle = create_history_db(str(tmp_path))
    for i in range(_TOTAL_RECORDS):
        handle.add_record(
            f"rec_{i:03d}.wav",
            str(tmp_path / f"rec_{i:03d}.wav"),
            f"2026-01-01 00:00:{i:02d}",
            1024 + i,
            text_preview=f"预览 {i}",
            engine="voxcpm2",
        )
    yield handle
    handle.close()


class TestPaginationBoundary:
    """分页参数边界：非法 limit/offset 必须被钳制，不得回退为全表拉取。"""

    @pytest.mark.parametrize(
        "limit,expected_items",
        [
            (0, 20),  # 0 → 钳制为 20
            (-1, 20),  # 负值 → 钳制为 20（SQLite 中 LIMIT -1 等同不限，必须拦截）
            (-100, 20),
            (1001, 20),  # 超过上限 1000 → 钳制为 20
            (10**9, 20),
            (5, 5),  # 合法值原样生效
            (20, 20),
            (1000, 25),  # 上限内 → 返回全部 25 条
        ],
    )
    def test_invalid_limit_is_clamped(self, db, limit, expected_items):
        """非法 limit 一律钳制为 20，合法 limit 按原值生效。"""
        result = db.get_paginated_records(limit=limit, offset=0)
        assert len(result["items"]) == expected_items

    @pytest.mark.parametrize("offset", [-1, -100, -(10**6)])
    def test_negative_offset_is_clamped_to_zero(self, db, offset):
        """负 offset 钳制为 0，应从首条开始返回。"""
        result = db.get_paginated_records(limit=10, offset=offset)
        assert len(result["items"]) == 10
        assert result["loaded"] == 10

    def test_offset_beyond_end_returns_empty(self, db):
        """offset 越过末尾应返回空列表且 hasMore=False，不得越界报错。"""
        result = db.get_paginated_records(limit=10, offset=10**6)
        assert result["items"] == []
        assert result["hasMore"] is False

    @pytest.mark.parametrize("time_filter", ["bogus", "", "ALL"])
    def test_unknown_time_filter_falls_back_to_all(self, db, time_filter):
        """未知时间过滤值按「全部」处理，不得抛异常或静默返回空。"""
        result = db.get_paginated_records(limit=1000, offset=0, time_filter=time_filter)
        assert result["total"] == _TOTAL_RECORDS

    @pytest.mark.parametrize("duration_filter", ["bogus", "", "LT5"])
    def test_unknown_duration_filter_falls_back_to_all(self, db, duration_filter):
        """未知时长过滤值按「全部」处理。"""
        result = db.get_paginated_records(limit=1000, offset=0, duration_filter=duration_filter)
        assert result["total"] == _TOTAL_RECORDS


class TestNegativePaths:
    """负向路径：非法输入必须优雅失败（返回失败语义），不得抛异常或破坏数据。"""

    @pytest.mark.parametrize("record_id", [0, -1, 999999])
    def test_delete_missing_record_returns_failure(self, db, record_id):
        """删除不存在的记录应返回 (False, 原因)，且不误删任何数据。"""
        ok, msg = db.delete_record(record_id)
        assert ok is False
        assert isinstance(msg, str) and msg, "失败时必须给出人类可读原因"
        assert db.get_total_count() == _TOTAL_RECORDS, "失败的删除不得影响既有记录"

    @pytest.mark.parametrize("retention_days", [0, -1, -1000])
    def test_purge_expired_non_positive_is_noop(self, db, retention_days):
        """retention_days <= 0 表示永久保留，必须是 no-op。"""
        assert db.purge_expired(retention_days) == 0
        assert db.get_total_count() == _TOTAL_RECORDS

    def test_insert_batch_empty_list_returns_zero(self, db):
        """空批量插入应返回 0 且不影响既有数据。"""
        assert db.insert_batch([]) == 0
        assert db.get_total_count() == _TOTAL_RECORDS

    def test_load_kv_missing_key_returns_none(self, db):
        """读取不存在的 KV 键应返回 None（而非抛异常）。"""
        assert db.load_kv("definitely-not-exists") is None


class TestKeyValueEdgeValues:
    """KV 存储边界值：空键、空值、超长值、Unicode 均需可往返。"""

    @pytest.mark.parametrize(
        "key,value",
        [
            ("", "empty-key"),  # 空键
            ("k", ""),  # 空值
            ("unicode", "中文🎉混排"),  # Unicode / emoji
            ("long", "x" * 5000),  # 超长值
        ],
    )
    def test_save_load_roundtrip(self, db, key, value):
        """边界值写入后必须原样读回。"""
        db.save_kv(key, value)
        assert db.load_kv(key) == value


class TestClearSemantics:
    """清空语义：隐藏式清理可恢复且幂等。"""

    def test_hide_only_clear_is_recoverable(self, db):
        """hide_only=True 仅隐藏，记录仍可通过 include_hidden 统计到。"""
        affected = db.clear_all_records(hide_only=True)
        assert affected == _TOTAL_RECORDS
        assert db.get_total_count() == 0
        assert db.get_total_count(include_hidden=True) == _TOTAL_RECORDS

    def test_clear_is_idempotent(self, db):
        """重复清空第二次应返回 0（幂等），不得报错。"""
        db.clear_all_records(hide_only=True)
        assert db.clear_all_records(hide_only=True) == 0


class TestEncryptionRoundtrip:
    """存储层加密往返：写入的明文必须能被解密还原。"""

    def test_text_preview_survives_encrypt_decrypt(self, db):
        """记录经加密落库后，读取时必须还原为原始明文。"""
        result = db.get_paginated_records(limit=1000, offset=0)
        previews = {item["text_preview"] for item in result["items"]}
        assert "预览 7" in previews
        assert len(previews) == _TOTAL_RECORDS
