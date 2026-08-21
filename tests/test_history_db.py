"""Tests for SQLite-based history database."""
import os
import sys

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")


class TestHistoryDatabase:
    """Test HistoryDatabase CRUD operations."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create a temporary HistoryDatabase for testing."""
        from integrated_app.history_db import HistoryDatabase
        db_path = str(tmp_path / "test_history.db")
        database = HistoryDatabase(db_path=db_path)
        return database

    def test_database_creation(self, db):
        """Database is created successfully."""
        assert db is not None

    def test_add_record(self, db):
        """Can add a record to the database."""
        db.add_record(
            filename="test_output.wav",
            filepath="/outputs/test_output.wav",
            created_at="2026-01-01T00:00:00",
            file_size=1024,
            duration_seconds=5.0,
            engine="voxcpm2",
            text_preview="测试文本",
        )
        records = db.get_paginated_records(limit=10, offset=0)
        assert records["total"] == 1

    def test_add_multiple_records(self, db):
        """Can add multiple records."""
        for i in range(5):
            db.add_record(
                filename=f"test_{i}.wav",
                filepath=f"/outputs/test_{i}.wav",
                created_at="2026-01-01T00:00:00",
                file_size=1024 * (i + 1),
                duration_seconds=float(i + 1),
                engine="voxcpm2",
                text_preview=f"测试文本{i}",
            )
        records = db.get_paginated_records(limit=10, offset=0)
        assert records["total"] == 5

    def test_pagination(self, db):
        """Pagination works correctly."""
        for i in range(10):
            db.add_record(
                filename=f"test_{i}.wav",
                filepath=f"/outputs/test_{i}.wav",
                created_at="2026-01-01T00:00:00",
                file_size=1024,
                duration_seconds=1.0,
                engine="voxcpm2",
                text_preview=f"文本{i}",
            )
        page1 = db.get_paginated_records(limit=5, offset=0)
        assert len(page1["items"]) == 5
        assert page1["total"] == 10
        page2 = db.get_paginated_records(limit=5, offset=5)
        assert len(page2["items"]) == 5

    def test_search(self, db):
        """Search filters records by keyword."""
        db.add_record(filename="hello.wav", filepath="/outputs/hello.wav",
                      created_at="2026-01-01T00:00:00",
                      file_size=1024, duration_seconds=1.0,
                      engine="voxcpm2", text_preview="你好世界")
        db.add_record(filename="test.wav", filepath="/outputs/test.wav",
                      created_at="2026-01-01T00:00:00",
                      file_size=1024, duration_seconds=1.0,
                      engine="voxcpm2", text_preview="测试文本")
        results = db.get_paginated_records(limit=10, offset=0, search_keyword="你好")
        assert results["total"] == 1

    def test_delete_records(self, db):
        """Can delete records."""
        db.add_record(filename="delete_me.wav", filepath="/outputs/delete_me.wav",
                      created_at="2026-01-01T00:00:00",
                      file_size=1024, duration_seconds=1.0,
                      engine="voxcpm2", text_preview="删除我")
        records = db.get_paginated_records(limit=10, offset=0)
        assert records["total"] == 1
        db.delete_multiple_records(["delete_me.wav"], delete_files=False)
        records = db.get_paginated_records(limit=10, offset=0)
        assert records["total"] == 0

    def test_hide_and_show_records(self, db):
        """Can hide and show records."""
        db.add_record(filename="hide_me.wav", filepath="/outputs/hide_me.wav",
                      created_at="2026-01-01T00:00:00",
                      file_size=1024, duration_seconds=1.0,
                      engine="voxcpm2", text_preview="隐藏我")
        db.hide_multiple_records(["hide_me.wav"])
        visible = db.get_paginated_records(limit=10, offset=0)
        assert visible["total"] == 0
        db.show_multiple_records(["hide_me.wav"])
        visible = db.get_paginated_records(limit=10, offset=0)
        assert visible["total"] == 1

    def test_get_total_count(self, db):
        """Get total count of records."""
        for i in range(3):
            db.add_record(filename=f"count_{i}.wav", filepath=f"/outputs/count_{i}.wav",
                          created_at="2026-01-01T00:00:00",
                          file_size=1024, duration_seconds=1.0,
                          engine="voxcpm2", text_preview=f"计数{i}")
        assert db.get_total_count() == 3

    def test_batch_delete_100_records(self, db):
        """Batch delete 100 records with a single IN (...) query."""
        filenames = []
        for i in range(100):
            name = f"batch_{i}.wav"
            filenames.append(name)
            db.add_record(
                filename=name,
                filepath=f"/outputs/{name}",
                created_at="2026-01-01T00:00:00",
                file_size=1024,
                duration_seconds=1.0,
                engine="voxcpm2",
                text_preview=f"批量{i}",
            )
        assert db.get_total_count() == 100
        deleted = db.delete_multiple_records(filenames, delete_files=False)
        assert deleted == 100
        assert db.get_total_count() == 0

    def test_batch_hide_and_show_records(self, db):
        """Batch hide and show multiple records via IN (...) query."""
        filenames = [f"batch_hide_{i}.wav" for i in range(10)]
        for name in filenames:
            db.add_record(
                filename=name,
                filepath=f"/outputs/{name}",
                created_at="2026-01-01T00:00:00",
                file_size=1024,
                duration_seconds=1.0,
                engine="voxcpm2",
                text_preview="批量隐藏",
            )
        hidden = db.hide_multiple_records(filenames)
        assert hidden == 10
        assert db.get_total_count() == 0
        shown = db.show_multiple_records(filenames)
        assert shown == 10
        assert db.get_total_count() == 10

    def test_insert_and_query(self, db):
        """insert() stores a full record and query() retrieves it."""
        record_id = db.insert({
            "filename": "inserted.wav",
            "filepath": "/outputs/inserted.wav",
            "created_at": "2026-01-01T00:00:00",
            "file_size_bytes": 4096,
            "duration_seconds": 3.5,
            "text_preview": "插入测试",
            "engine": "indextts2",
            "persona_name": "test_persona",
        })
        assert record_id > 0
        rows = db.query(engine="indextts2", search_text="插入")
        assert len(rows) == 1
        assert rows[0]["filename"] == "inserted.wav"

    def test_count_and_stats(self, db):
        """count() and get_stats() return aggregate information."""
        for i in range(3):
            db.add_record(
                filename=f"stat_{i}.wav",
                filepath=f"/outputs/stat_{i}.wav",
                created_at="2026-01-01T00:00:00",
                file_size=1024,
                duration_seconds=2.0,
                engine="voxcpm2",
                text_preview=f"统计{i}",
            )
        assert db.count(engine="voxcpm2") == 3
        stats = db.get_stats()
        assert stats["total_records"] == 3
        assert stats["unique_engines"] == 1

    def test_validate_integrity(self, db):
        """PRAGMA integrity_check reports the database as ok."""
        is_ok, message = db.validate_integrity()
        assert is_ok is True
        assert message == "ok"


class TestHistoryDatabaseMigration:
    """Test JSON to SQLite migration."""

    def test_migration_from_json(self, tmp_path):
        """Can migrate records from JSON file via insert_batch."""
        import json
        json_path = str(tmp_path / "history_records.json")
        # Create a fake JSON history file
        data = [
            {
                "filename": "migrated.wav",
                "filepath": "/outputs/migrated.wav",
                "file_size_bytes": 2048,
                "duration_seconds": 2.0,
                "engine": "voxcpm2",
                "text_preview": "迁移测试",
                "created_at": "2026-01-01T00:00:00",
            }
        ]
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        from integrated_app.history_db import HistoryDatabase
        db_path = str(tmp_path / "test_migrate.db")
        database = HistoryDatabase(db_path=db_path)
        # Simulate JSON migration by loading and inserting records
        with open(json_path, encoding="utf-8") as f:
            records = json.load(f)
        database.insert_batch(records)
        # Migration should have happened
        result = database.get_paginated_records(limit=10, offset=0)
        assert result["total"] == 1


class TestHistoryDatabaseFTS:
    """[H-R6] Test FTS5 full-text search acceleration and LIKE fallback parity."""

    @pytest.fixture
    def db(self, tmp_path):
        from integrated_app.history_db import HistoryDatabase
        return HistoryDatabase(db_path=str(tmp_path / "fts.db"))

    def _seed(self, db, n=60):
        for i in range(n):
            db.add_record(
                filename=f"clip_{i}.wav",
                filepath=f"/outputs/clip_{i}.wav",
                created_at="2026-01-01T00:00:00",
                file_size=1024,
                duration_seconds=1.0,
                engine="voxcpm2",
                text_preview=("你好世界编号%d" % i) if i % 3 == 0 else ("hello world number %d" % i),
            )

    def test_fts_enabled_on_modern_sqlite(self, db):
        """FTS5 + trigram should be available on the bundled SQLite build."""
        # We do not hard-assert True (older SQLite may lack FTS5); just ensure the
        # attribute exists and search works either way.
        assert hasattr(db, "_fts_enabled")

    def test_fts_search_ascii_parity(self, db):
        """>=3 char ASCII search returns the same set as substring match."""
        self._seed(db)
        res = db.get_paginated_records(limit=200, offset=0, search_keyword="hello world")
        # 40 records are 'hello world number' (i % 3 != 0 over 60)
        assert res["total"] == 40

    def test_fts_search_cjk(self, db):
        """>=3 char CJK substring search matches via trigram or LIKE fallback."""
        self._seed(db)
        res = db.get_paginated_records(limit=200, offset=0, search_keyword="你好世界")
        assert res["total"] == 20  # i % 3 == 0 over 60

    def test_short_keyword_fallback(self, db):
        """1-2 char keywords fall back to LIKE and still match substrings."""
        db.add_record(filename="a.wav", filepath="/outputs/a.wav",
                      created_at="2026-01-01T00:00:00", file_size=1, duration_seconds=1.0,
                      engine="voxcpm2", text_preview="甲乙")
        res = db.get_paginated_records(limit=10, offset=0, search_keyword="甲")
        assert res["total"] == 1

    def test_fts_replace_resync(self, db):
        """INSERT OR REPLACE keeps FTS index consistent (old text unfindable)."""
        db.add_record(filename="r.wav", filepath="/outputs/r.wav",
                      created_at="2026-01-01T00:00:00", file_size=1, duration_seconds=1.0,
                      engine="voxcpm2", text_preview="原始文本内容甲")
        assert db.get_paginated_records(limit=10, search_keyword="原始文本")["total"] == 1
        # Overwrite same filepath with new content
        db.add_record(filename="r.wav", filepath="/outputs/r.wav",
                      created_at="2026-01-01T00:00:00", file_size=1, duration_seconds=1.0,
                      engine="voxcpm2", text_preview="替换后的新文本内容")
        assert db.get_paginated_records(limit=10, search_keyword="原始文本")["total"] == 0
        assert db.get_paginated_records(limit=10, search_keyword="替换后的新")["total"] == 1

    def test_fts_delete_resync(self, db):
        """Deleting a record removes it from full-text search."""
        db.add_record(filename="d.wav", filepath="/outputs/d.wav",
                      created_at="2026-01-01T00:00:00", file_size=1, duration_seconds=1.0,
                      engine="voxcpm2", text_preview="待删除的独特文本")
        rec = db.get_paginated_records(limit=10, search_keyword="待删除的独特")
        assert rec["total"] == 1
        rid = rec["items"][0]["id"]
        db.delete_record(rid, delete_file=False)
        assert db.get_paginated_records(limit=10, search_keyword="待删除的独特")["total"] == 0

    def test_integrity_after_fts(self, db):
        """integrity_check remains ok with the FTS virtual table present."""
        self._seed(db, n=10)
        is_ok, message = db.validate_integrity()
        assert is_ok is True


class TestHistoryDatabaseKeyset:
    """[H-R6] Test keyset (cursor) pagination equivalence with offset paging."""

    @pytest.fixture
    def db(self, tmp_path):
        from integrated_app.history_db import HistoryDatabase
        return HistoryDatabase(db_path=str(tmp_path / "keyset.db"))

    def _seed(self, db, n=120):
        for i in range(n):
            db.add_record(
                filename=f"k_{i}.wav",
                filepath=f"/outputs/k_{i}.wav",
                created_at="2026-01-01T00:00:00",
                file_size=1024,
                duration_seconds=1.0,
                engine="voxcpm2",
                text_preview=f"记录{i}",
            )

    def test_keyset_matches_offset_full_walk(self, db):
        """Walking all pages via keyset yields the same id set as offset paging."""
        self._seed(db, 120)
        # offset walk
        off_ids, off = [], 0
        while True:
            page = db.get_paginated_records(limit=25, offset=off)
            off_ids.extend(it["id"] for it in page["items"])
            if not page["hasMore"]:
                break
            off += 25
        # keyset walk
        ks_ids, cur_ts, cur_id = [], None, None
        while True:
            page = db.query_records_keyset(limit=25, cursor_timestamp=cur_ts, cursor_id=cur_id)
            ks_ids.extend(it["id"] for it in page["items"])
            if not page["hasMore"]:
                break
            cur_ts = page["next_cursor"]["timestamp"]
            cur_id = page["next_cursor"]["id"]
        assert len(ks_ids) == 120
        assert len(ks_ids) == len(set(ks_ids))  # no duplicates
        assert set(off_ids) == set(ks_ids)

    def test_keyset_first_page_no_cursor(self, db):
        """First page (no cursor) returns newest records and a next_cursor."""
        self._seed(db, 30)
        page = db.query_records_keyset(limit=10)
        assert len(page["items"]) == 10
        assert page["hasMore"] is True
        assert page["next_cursor"] is not None
        assert "timestamp" in page["next_cursor"] and "id" in page["next_cursor"]

    def test_keyset_last_page_cursor_none(self, db):
        """Final page reports hasMore False and next_cursor None."""
        self._seed(db, 15)
        page = db.query_records_keyset(limit=20)
        assert len(page["items"]) == 15
        assert page["hasMore"] is False
        assert page["next_cursor"] is None

    def test_keyset_limit_clamped(self, db):
        """Out-of-range limit is clamped to the default (50)."""
        self._seed(db, 60)
        page = db.query_records_keyset(limit=0)
        assert len(page["items"]) == 50
        page2 = db.query_records_keyset(limit=99999)
        assert len(page2["items"]) == 50

    def test_keyset_with_filter_and_search(self, db):
        """Keyset honors filters and search_text."""
        self._seed(db, 20)
        page = db.query_records_keyset(limit=100, filters={"engine": "voxcpm2"}, search_text="记录1")
        # '记录1', '记录10'..'记录19' -> 11 records contain substring '记录1'
        assert len(page["items"]) == 11


class TestHistoryDatabaseMigrationHelper:
    """[H-R6] Test the unified _migrate_add_column helper."""

    @pytest.fixture
    def db(self, tmp_path):
        from integrated_app.history_db import HistoryDatabase
        return HistoryDatabase(db_path=str(tmp_path / "mig.db"))

    def test_add_new_column_and_idempotent(self, db):
        """Helper adds a missing column and is a no-op when it already exists."""
        db._migrate_add_column("probe_col", "TEXT DEFAULT ''")
        # Column now exists: SELECT should not raise
        cur = db._execute("SELECT probe_col FROM generation_history LIMIT 1")
        cur.fetchall()
        # Second call is a no-op (must not raise)
        db._migrate_add_column("probe_col", "TEXT DEFAULT ''")

    def test_existing_migrations_idempotent(self, db):
        """Re-running the built-in add-column migrations is safe."""
        db._migrate_add_hidden_column()
        db._migrate_add_created_timestamp_column()
        db._migrate_add_file_missing_column()
        is_ok, _ = db.validate_integrity()
        assert is_ok is True
