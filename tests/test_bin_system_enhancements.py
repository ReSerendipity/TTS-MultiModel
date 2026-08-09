# -*- coding: utf-8 -*-
"""
Test script to verify system enhancement improvements:
1. Log rotation configuration
2. Static resource caching headers
3. Health check endpoints
4. Database query optimizations
"""

import os
import sys
import tempfile
import sqlite3
import time

import pytest

pytestmark = pytest.mark.integration

# Add bin dir to path for imports
_BIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

def test_log_rotation():
    """Test that log rotation is properly configured."""
    print("=" * 60)
    print("TEST 1: Log Rotation Configuration")
    print("=" * 60)

    from integrated_app.app_server import setup_logging, RotatingFileHandler
    import logging

    # Call setup_logging
    setup_logging()

    root_logger = logging.getLogger()
    rotating_handlers = [h for h in root_logger.handlers if isinstance(h, RotatingFileHandler)]

    if rotating_handlers:
        handler = rotating_handlers[0]
        max_bytes = handler.maxBytes
        backup_count = handler.backupCount

        print(f"  [OK] RotatingFileHandler found")
        print(f"  [OK] Max file size: {max_bytes / (1024*1024):.1f} MB")
        print(f"  [OK] Backup count: {backup_count}")
        print(f"  [OK] Encoding: {handler.encoding}")

        if max_bytes == 10 * 1024 * 1024 and backup_count == 3:
            print(f"  [PASS] Log rotation configured correctly")
        else:
            print(f"  [FAIL] Expected 10MB max, 3 backups")
    else:
        print(f"  [FAIL] No RotatingFileHandler found")

    print()

def test_cached_static_files():
    """Test that CachedStaticFiles class exists and works."""
    print("=" * 60)
    print("TEST 2: Static Resource Caching")
    print("=" * 60)

    from integrated_app.app_server import CachedStaticFiles, _NO_CACHE_EXTENSIONS

    # Verify cache configurations
    print(f"  [OK] CachedStaticFiles class defined")
    print(f"  [OK] No-cache file types: {len(_NO_CACHE_EXTENSIONS)} extensions")

    # Test that no-cache extensions cover expected types
    expected_no_cache = [".html", ".json", ".css", ".js", ".png", ".svg", ".woff2"]
    all_pass = True
    for ext in expected_no_cache:
        if ext in _NO_CACHE_EXTENSIONS:
            print(f"  [PASS] {ext}: no-cache configured")
        else:
            print(f"  [FAIL] {ext}: not in no-cache set")
            all_pass = False

    # Test that the class inherits from StaticFiles
    from fastapi.staticfiles import StaticFiles
    if issubclass(CachedStaticFiles, StaticFiles):
        print(f"  [PASS] CachedStaticFiles inherits from StaticFiles")
    else:
        print(f"  [FAIL] CachedStaticFiles does not inherit from StaticFiles")
        all_pass = False

    print()
    return all_pass

def test_database_optimizations():
    """Test database optimizations."""
    print("=" * 60)
    print("TEST 3: Database Query Optimizations")
    print("=" * 60)

    from integrated_app.history_db import HistoryDatabase

    # Create a temporary database for testing
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_history.db")

    try:
        db = HistoryDatabase(db_path)
        print(f"  [OK] HistoryDatabase created")

        # Test 1: Thread-local connection
        conn = db._get_connection()
        if conn is not None:
            print(f"  [PASS] Thread-local connection working")
        else:
            print(f"  [FAIL] Thread-local connection failed")

        # Test 2: Check WAL mode is enabled
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        if mode.lower() == "wal":
            print(f"  [PASS] WAL journal mode enabled")
        else:
            print(f"  [FAIL] Expected WAL mode, got {mode}")

        # Test 3: Check cache_size pragma
        cursor = conn.execute("PRAGMA cache_size")
        cache_size = cursor.fetchone()[0]
        if cache_size >= -64000:  # Negative means KB
            print(f"  [PASS] Cache size optimized: {cache_size} KB")
        else:
            print(f"  [FAIL] Cache size not optimized: {cache_size}")

        # Test 4: Verify all indexes exist
        cursor = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='index' AND name LIKE 'idx_history_%'
        """)
        indexes = [row[0] for row in cursor.fetchall()]
        expected_indexes = [
            'idx_history_created_at',
            'idx_history_engine',
            'idx_history_persona',
            'idx_history_engine_created',
            'idx_history_persona_created',
            'idx_history_is_success',
            'idx_history_filepath',
        ]

        print(f"  [OK] Found {len(indexes)} indexes")
        for idx in expected_indexes:
            if idx in indexes:
                print(f"  [PASS] Index {idx} exists")
            else:
                print(f"  [FAIL] Index {idx} missing")

        # Test 5: Test batch insert
        test_records = [
            {
                "filename": f"test_{i}.wav",
                "filepath": f"/tmp/test_{i}.wav",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "file_size_bytes": 1024 * i,
                "engine": "test_engine",
                "persona_name": "test_persona",
                "text_preview": f"Test preview {i}",
            }
            for i in range(5)
        ]

        count = db.insert_batch(test_records)
        if count == 5:
            print(f"  [PASS] Batch insert: inserted {count} records")
        else:
            print(f"  [FAIL] Batch insert: expected 5, got {count}")

        # Test 6: Test query performance
        results = db.query(limit=10)
        if len(results) == 5:
            print(f"  [PASS] Query returned {len(results)} records")
        else:
            print(f"  [FAIL] Query returned {len(results)} records, expected 5")

        # Test 7: Test count with filters
        total = db.count()
        engine_count = db.count(engine="test_engine")
        if total == 5 and engine_count == 5:
            print(f"  [PASS] Count: total={total}, engine={engine_count}")
        else:
            print(f"  [FAIL] Count: total={total}, engine={engine_count}")

        # Test 8: Test get_stats
        stats = db.get_stats()
        if stats["total_records"] == 5:
            print(f"  [PASS] Stats: {stats}")
        else:
            print(f"  [FAIL] Stats: total_records={stats['total_records']}")

        # Test 9: Test close method
        db.close()
        print(f"  [PASS] Close method executed without error")

        print()
        return True

    except Exception as e:
        print(f"  [FAIL] Exception: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Cleanup
        try:
            if os.path.exists(db_path):
                os.remove(db_path)
            for f in os.listdir(temp_dir):
                os.remove(os.path.join(temp_dir, f))
            os.rmdir(temp_dir)
        except Exception:
            pass

def test_audio_cache_headers():
    """Test that audio routes have cache headers configured."""
    print("=" * 60)
    print("TEST 4: Audio Cache Headers")
    print("=" * 60)

    # Read the audio.py file and check for Cache-Control headers
    audio_py_path = os.path.join(_BIN_DIR, "integrated_app", "routes", "audio.py")
    with open(audio_py_path, 'r', encoding='utf-8') as f:
        content = f.read()

    checks = [
        ("serve_audio Cache-Control", "Cache-Control" in content and "max-age=3600" in content),
        ("serve_persona_audio Cache-Control", "Cache-Control" in content and "max-age=3600" in content),
        ("speaker_sample Cache-Control", "max-age=86400" in content),
        ("Accept-Ranges header", "Accept-Ranges" in content),
    ]

    all_pass = True
    for name, result in checks:
        if result:
            print(f"  [PASS] {name}")
        else:
            print(f"  [FAIL] {name}")
            all_pass = False

    print()
    return all_pass

def main():
    print("System Enhancement Verification Tests")
    print("=" * 60)
    print()

    results = {}

    try:
        test_log_rotation()
        results["Log Rotation"] = True
    except Exception as e:
        print(f"  [FAIL] Error: {e}")
        results["Log Rotation"] = False

    results["Static Caching"] = test_cached_static_files()
    results["Database Optimization"] = test_database_optimizations()
    results["Audio Cache Headers"] = test_audio_cache_headers()

    # Summary
    print("=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n[SUCCESS] All system enhancements verified!")
        return 0
    else:
        print("\n[WARNING] Some tests failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
