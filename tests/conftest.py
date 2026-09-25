"""Shared pytest fixtures for the TTS MultiModel test suite."""

import contextlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

# Keep tests offline and avoid auto-loading models during test discovery/client creation.
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("TTS_AUTO_LOAD_MODEL", "0")


@pytest.fixture(autouse=True, scope="session")
def _no_subprocess_coverage():
    """关掉子进程的 coverage 自动记录，避免 `pytest --cov` 的 combine 阶段随机崩。

    `--cov` 时 pytest-cov 会设 `COVERAGE_PROCESS_START`，而 coverage 自带的
    ``a1_coverage.pth`` 见到它就对**每个 Python 进程**调用 ``process_startup()``。
    本套测试里有若干用例要起子进程跑真实脚本（``python -c`` / ``check_release_readiness.py``
    等），这些子进程 cwd 不在仓库根，读不到 ``pyproject.toml`` 的 ``[tool.coverage.run]
    branch = true``，于是写出 ``has_arcs='0'`` 的数据文件混进仓库根，
    与主进程的 ``'1'`` 合并时抛 ``DataError: Can't combine statement coverage data
    with branch data`` —— 用例全绿但覆盖率闸失败，且是否触发取决于子进程当时的 cwd。

    子进程覆盖率本来就不是这里想要的度量（被测的是 CLI/脚本自身，不是 app/integrated_app），
    所以从父进程环境里摘掉开关，让子进程不再记录。
    """
    saved = {k: os.environ.pop(k) for k in ("COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG") if k in os.environ}
    try:
        yield saved
    finally:
        os.environ.update(saved)


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
    assert tmp_path.exists(), "tmp_path fixture should provide an existing directory"
    assert tmp_path.is_dir(), "tmp_path should be a directory"
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
        with contextlib.suppress(PermissionError):
            db_path.unlink()  # Windows may hold locks on DB files


@pytest.fixture(scope="session")
def temp_root_for_tests(tmp_path_factory):
    """Session-scoped temporary root directory for tests that need shared state.

    Use this when you need a persistent temp dir across multiple test functions
    within the same session (e.g., for caching tests).

    Note: This is NOT automatically cleaned up between individual tests.
    Use tmp_path (function-scoped) instead for automatic cleanup.
    """
    return tmp_path_factory.mktemp("tts_test_root")
