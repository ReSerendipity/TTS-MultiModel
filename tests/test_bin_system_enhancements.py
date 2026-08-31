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
import time

import pytest

pytestmark = pytest.mark.integration

# Add bin dir to path for imports
_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)


def test_log_rotation():
    """Test that log rotation is properly configured."""
    import logging

    from integrated_app.app_server import RotatingFileHandler, setup_logging

    # Call setup_logging
    setup_logging()

    root_logger = logging.getLogger()
    rotating_handlers = [h for h in root_logger.handlers if isinstance(h, RotatingFileHandler)]

    assert len(rotating_handlers) > 0, "Should have at least one RotatingFileHandler"
    handler = rotating_handlers[0]
    assert handler.maxBytes == 10 * 1024 * 1024, f"Expected 10MB max, got {handler.maxBytes}"
    assert handler.backupCount == 3, f"Expected 3 backups, got {handler.backupCount}"
    assert handler.encoding is not None, "Encoding should be set"


def test_cached_static_files():
    """Test that CachedStaticFiles class exists and works.

    Note (2026-08-31 同步): 实现采用「差异化缓存策略」（app_server.py P1-1，
    来源 Seedvr2 VersionedStaticFiles）——CSS/JS/HTML/JSON no-cache；字体缓存 30 天；
    图片缓存 1 天。旧断言把 .png/.svg/.woff2 也放进 no-cache 与该文档化决策相悖，
    已按实现修正：分别断言三类扩展名落在各自正确的集合中。
    """
    from integrated_app.app_server import (
        _FONT_EXTENSIONS,
        _IMAGE_EXTENSIONS,
        _NO_CACHE_EXTENSIONS,
        CachedStaticFiles,
    )

    # Verify cache configurations
    assert CachedStaticFiles is not None, "CachedStaticFiles class should be defined"
    assert len(_NO_CACHE_EXTENSIONS) > 0, "Should have no-cache extensions configured"

    # 经常改动的文本资源：no-cache, must-revalidate
    for ext in [".html", ".json", ".css", ".js", ".map"]:
        assert ext in _NO_CACHE_EXTENSIONS, f"{ext} should be in no-cache set"

    # 字体：缓存 30 天（内容稳定、体积大）
    for ext in [".woff2", ".woff", ".ttf"]:
        assert ext in _FONT_EXTENSIONS, f"{ext} should be in font cache set"

    # 图片：缓存 1 天（配合 ?v= 版本参数做失效）
    for ext in [".png", ".svg", ".jpg", ".ico"]:
        assert ext in _IMAGE_EXTENSIONS, f"{ext} should be in image cache set"

    # 三类集合互斥，避免同扩展名命中歧义分支
    assert not (_NO_CACHE_EXTENSIONS & _FONT_EXTENSIONS), "no-cache 与 font 集合不应重叠"
    assert not (_NO_CACHE_EXTENSIONS & _IMAGE_EXTENSIONS), "no-cache 与 image 集合不应重叠"

    # Test that the class inherits from StaticFiles
    from fastapi.staticfiles import StaticFiles

    assert issubclass(CachedStaticFiles, StaticFiles), "CachedStaticFiles should inherit from StaticFiles"


def test_database_optimizations():
    """Test database optimizations."""
    from integrated_app.history_db import HistoryDatabase

    # Create a temporary database for testing
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_history.db")

    try:
        db = HistoryDatabase(db_path)

        # Test 1: Thread-local connection
        conn = db._get_connection()
        assert conn is not None, "Thread-local connection should work"

        # Test 2: Check WAL mode is enabled
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal", f"Expected WAL mode, got {mode}"

        # Test 3: Check cache_size pragma
        cursor = conn.execute("PRAGMA cache_size")
        cache_size = cursor.fetchone()[0]
        assert cache_size >= -64000, f"Cache size not optimized: {cache_size}"

        # Test 4: Verify all indexes exist
        cursor = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='index' AND name LIKE 'idx_history_%'
        """)
        indexes = [row[0] for row in cursor.fetchall()]
        expected_indexes = [
            "idx_history_created_at",
            "idx_history_engine",
            "idx_history_persona",
            "idx_history_engine_created",
            "idx_history_persona_created",
            "idx_history_is_success",
            # 注（2026-08-31 同步）：idx_history_filepath 已在重构 I2-1 中有意移除——
            # filepath 列的 UNIQUE 约束自带隐式索引（sqlite_autoindex_*），再建显式索引纯属冗余，
            # 徒增写入开销。此处改为断言「filepath 的 UNIQUE 隐式索引存在」以保留同等覆盖。
        ]
        for idx in expected_indexes:
            assert idx in indexes, f"Index {idx} missing"

        # Test 4b: filepath UNIQUE 约束必须自带隐式索引（I2-1 移除显式索引后的等价覆盖）
        cursor = conn.execute("PRAGMA index_list(generation_history)")
        index_info = {row[1]: row[2] for row in cursor.fetchall()}  # name -> unique(0/1)
        unique_indexes = [name for name, is_unique in index_info.items() if is_unique]
        assert unique_indexes, "filepath UNIQUE 约束应产生至少一个唯一索引"

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
        assert count == 5, f"Batch insert: expected 5, got {count}"

        # Test 6: Test query performance
        results = db.query(limit=10)
        assert len(results) == 5, f"Query returned {len(results)} records, expected 5"

        # Test 7: Test count with filters
        total = db.count()
        engine_count = db.count(engine="test_engine")
        assert total == 5, f"Count total={total}, expected 5"
        assert engine_count == 5, f"Count engine={engine_count}, expected 5"

        # Test 8: Test get_stats
        stats = db.get_stats()
        assert stats["total_records"] == 5, f"Stats total_records={stats['total_records']}, expected 5"

        # Test 9: Test close method
        db.close()

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
    # Read the audio.py file and check for Cache-Control headers
    audio_py_path = os.path.join(_APP_DIR, "integrated_app", "routes", "audio.py")
    with open(audio_py_path, encoding="utf-8") as f:
        content = f.read()

    assert "Cache-Control" in content, "Should have Cache-Control header"
    assert "max-age=3600" in content, "serve_audio should have max-age=3600"
    assert "max-age=86400" in content, "speaker_sample should have max-age=86400"
    assert "Accept-Ranges" in content, "Should have Accept-Ranges header"


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
