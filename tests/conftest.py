"""Shared pytest fixtures for the TTS MultiModel test suite."""

import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_BIN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

# Keep tests offline and avoid auto-loading models during test discovery/client creation.
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("TTS_AUTO_LOAD_MODEL", "0")


@pytest.fixture
def app():
    """Create the real FastAPI application with all routers discovered."""
    from integrated_app.app_server import create_app

    return create_app()


@pytest.fixture
def client(app):
    """Return a TestClient backed by the real application."""
    return TestClient(app)


@pytest.fixture
def tmp_persona_dir(tmp_path: Path):
    """Create a temporary directory for persona files that will be cleaned up after each test.

    Usage: Override the PERSONA_DIR environment variable before importing modules that use it.

    Example::

        def test_something(tmp_persona_dir):
            os.environ["PERSONA_DIR"] = str(tmp_persona_dir)
            # Now any module that reads PERSONA_DIR will use this isolated path
    """
    return tmp_path


@pytest.fixture
def isolated_history_db(tmp_path: Path):
    """Create a temporary SQLite database path for history storage isolation.

    Usage: Override HISTORY_DB_PATH or related config before running tests.

    Returns:
        Path to a temporary .db file that will be automatically removed after the test.
    """
    db_path = tmp_path / "test_history.db"
    yield db_path
    # Cleanup is handled by tmp_path fixture
    if db_path.exists():
        try:
            db_path.unlink()
        except PermissionError:
            pass  # Windows may hold locks on DB files


@pytest.fixture(scope="session")
def temp_root_for_tests(tmp_path_factory):
    """Session-scoped temporary root directory for tests that need shared state.

    Use this when you need a persistent temp dir across multiple test functions
    within the same session (e.g., for caching tests).

    Note: This is NOT automatically cleaned up between individual tests.
    Use tmp_path (function-scoped) instead for automatic cleanup.
    """
    return tmp_path_factory.mktemp("tts_test_root")
