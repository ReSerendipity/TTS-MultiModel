"""Shared pytest fixtures for the TTS MultiModel test suite."""

import contextlib
import os
import re
import sqlite3
import sys
import tempfile
import types
from pathlib import Path
from urllib.parse import unquote

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

# =============================================================================
# F6 全局测试库隔离（2026-11-02 整改阶段 10）
# =============================================================================
# 背景（实测复核，勿凭记忆改动）：阶段 9 之前 `TTS_HISTORY_DB_PATH` 在本目录出现 0 次，
# 全测试套只有 tests/test_history_pruning.py 自行隔离了库路径——即默认**所有**测试经
# `get_history_db()` 单例 / `log_operation()` 双写生产 `data/history.db` 的
# `action_logs`（生产库实测 2,706 行中 2,167 行为测试签名噪声），备份链因此永久带噪。
#
# 本块的三层机制（conftest 导入即生效，早于任何测试模块 import，无需各测试"记得设"）：
# 1. **重定向**：强制 `TTS_HISTORY_DB_PATH` 指向本次会话独占的系统临时目录库，
#    走应用自己承认的优先级入口（history_db.py::get_history_db 首查该 env，
#    config.py::get_history_db_path 同口径），`action_logs` 与 `generation_history`
#    同库、一并覆盖；子进程（tests 里 `python -c` 起脚本）经 env 继承同样落隔离库。
# 2. **哨兵**：包装 `sqlite3.connect`——任何测试**打开生产 `data/history.db` 即抛
#    ProductionDatabaseAccessError 并指名当前测试 nodeid**（含只读 URI 打开），把
#    "以后不会再犯"变成机器可判；配套 tests/test_f6_db_isolation_sentinel.py 自证。
# 3. **旁路重定向**：`VersionManager(db_path=None)` 默认写 `outputs/generation_versions.db`
#    （实测 mtime 落在阶段 9 测试窗内；应用对该库**没有** env/config 优先入口，SAVE_DIR 为
#    硬编码常量），哨兵对该精确路径做透明重定向到会话临时目录，其余路径一律放行。
#    `data/push_subscriptions.db` 实测无任何代码引用（孤儿 -wal/-shm 残骸）→ 不处置、只登记。

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _f6_norm(path_str: str) -> str:
    """绝对化 + 规范化大小写/分隔符，供跨进程路径比对。"""
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(path_str))))


def _f6_uri_to_body(s: str) -> str:
    """`file:/C:/x?mode=ro` / `file:///C:/x` → 文件系统路径串。"""
    body = s[len("file:") :]
    body = body.split("?", 1)[0].split("#", 1)[0]
    body = unquote(body)
    # Windows 盘符前的冗余斜杠：/C:/... 与 ///C:/...
    while body.startswith("/") and re.match(r"^/[A-Za-z]:", body):
        body = body[1:]
    return body


# 生产保护集：默认 data/history.db、config.yaml history.db_path（若有）、调用方 env 原值
_F6_PROTECTED_DB_PATHS: set[str] = {
    _f6_norm(os.path.join(_REPO_ROOT, "data", "history.db")),
}
try:  # 直读 yaml，不在 conftest 期 import integrated_app（避免拉重依赖）
    import yaml as _yaml

    with open(os.path.join(_REPO_ROOT, "config.yaml"), encoding="utf-8") as _f:
        _raw = ((_yaml.safe_load(_f) or {}).get("history") or {}).get("db_path")
    if _raw:
        _F6_PROTECTED_DB_PATHS.add(_f6_norm(_raw if os.path.isabs(_raw) else os.path.join(_REPO_ROOT, _raw)))
except Exception:  # nosec B110 - config 解析失败时仍保护默认路径
    pass
_pre_env = os.environ.get("TTS_HISTORY_DB_PATH", "").strip()
if _pre_env:
    _F6_PROTECTED_DB_PATHS.add(_f6_norm(_pre_env))

# 旁路重定向：generation_versions 默认路径
_F6_PROD_VERSIONS_DB = _f6_norm(os.path.join(_REPO_ROOT, "outputs", "generation_versions.db"))

_F6_TMP_DIR = tempfile.mkdtemp(prefix="tts-f6-history-iso-")
_F6_ISOLATED_HISTORY_DB = os.path.join(_F6_TMP_DIR, "data", "history.db")
_F6_ISOLATED_VERSIONS_DB = os.path.join(_F6_TMP_DIR, "outputs", "generation_versions.db")
os.makedirs(os.path.dirname(_F6_ISOLATED_VERSIONS_DB), exist_ok=True)  # 重定向目标目录须先存在
os.environ["TTS_HISTORY_DB_PATH"] = _F6_ISOLATED_HISTORY_DB  # 强制，非 setdefault

_F6_ORIG_SQLITE_CONNECT = sqlite3.connect
_F6_CURRENT_TEST = {"nodeid": "(非用例阶段：collect/import/fixture)"}
_F6_REDIRECT_LOG: list[dict] = []  # 旁路重定向留痕：{from, to, nodeid}


class ProductionDatabaseAccessError(RuntimeError):
    """F6 哨兵：测试试图打开生产历史库时抛出。"""


def _f6_connect_target(args, kwargs):
    """从 connect() 参数解析目标库的规范化绝对路径；非字符串/内存库返回 None。"""
    db = kwargs.get("database", args[0] if args else None)
    if isinstance(db, bytes):
        db = db.decode("utf-8", "replace")
    if not isinstance(db, str) or db in ("", ":memory:"):
        return None, db
    raw = _f6_uri_to_body(db) if db.lower().startswith("file:") else db
    if not raw:
        return None, db
    return _f6_norm(raw), db


def _f6_sentinel_connect(*args, **kwargs):
    target, original = _f6_connect_target(args, kwargs)
    if target is not None:
        if target in _F6_PROTECTED_DB_PATHS:
            raise ProductionDatabaseAccessError(
                f"F6 哨兵：测试 {_F6_CURRENT_TEST['nodeid']} 试图打开生产历史库 "
                f"{original!r}（规范化 {target}）。测试套必须只写会话隔离库"
                f"（TTS_HISTORY_DB_PATH={_F6_ISOLATED_HISTORY_DB}）；"
                f"确需落盘请指向 tmp_path，勿指向 data/history.db。"
            )
        if target == _F6_PROD_VERSIONS_DB:
            # 透明重定向，保持参数形态（plain/uri）不变，并留痕供自证测试对账
            _F6_REDIRECT_LOG.append(
                {"from": target, "to": _f6_norm(_F6_ISOLATED_VERSIONS_DB), "nodeid": _F6_CURRENT_TEST["nodeid"]}
            )
            new_db = _F6_ISOLATED_VERSIONS_DB
            if original.lower().startswith("file:"):
                new_db = "file:" + _F6_ISOLATED_VERSIONS_DB.replace("\\", "/")
            if kwargs.get("database") is not None:
                kwargs["database"] = new_db
            else:
                args = (new_db,) + tuple(args[1:])
    return _F6_ORIG_SQLITE_CONNECT(*args, **kwargs)


sqlite3.connect = _f6_sentinel_connect  # 全局包装：早于任何测试模块 import


def pytest_runtest_setup(item):
    """记录当前用例 nodeid，供哨兵报错指名来源测试文件。"""
    _F6_CURRENT_TEST["nodeid"] = item.nodeid


@pytest.fixture(autouse=True, scope="session")
def _f6_isolation_active():
    """会话闸：隔离与哨兵若被环境篡改，任何用例起跑前先炸。"""
    assert os.environ.get("TTS_HISTORY_DB_PATH") == _F6_ISOLATED_HISTORY_DB, (
        "F6 隔离失效：TTS_HISTORY_DB_PATH 未指向会话临时库"
    )
    assert sqlite3.connect is _f6_sentinel_connect, "F6 哨兵失效：sqlite3.connect 未被包装"
    yield


@pytest.fixture
def f6_guard():
    """暴露哨兵内件，供 tests/test_f6_db_isolation_sentinel.py 自证。"""
    return types.SimpleNamespace(
        protected_db_paths=set(_F6_PROTECTED_DB_PATHS),
        isolated_history_db=_F6_ISOLATED_HISTORY_DB,
        prod_versions_db=_F6_PROD_VERSIONS_DB,
        isolated_versions_db=_F6_ISOLATED_VERSIONS_DB,
        error=ProductionDatabaseAccessError,
        norm=_f6_norm,
        redirect_log=_F6_REDIRECT_LOG,
        connect_is_wrapped=sqlite3.connect is _f6_sentinel_connect,
    )


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
