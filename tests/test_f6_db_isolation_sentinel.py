"""F6 哨兵自证测试（2026-11-02 整改阶段 10）。

证明 conftest 的三层全局隔离真的生效：
1. `TTS_HISTORY_DB_PATH` 强制指向会话临时库，且 `get_history_db()` 单例落在其上；
2. 任何直接打开生产 `data/history.db` 的尝试（plain / 只读 URI）都会抛
   `ProductionDatabaseAccessError`，且错误信息点名本测试文件——即"以后谁再碰生产库，
   测试直接失败并指名"是机器可判的；
3. `VersionManager(db_path=None)` 的默认路径（`outputs/generation_versions.db`，
   应用侧无 env 入口的旁路库）被透明重定向，生产文件不被写；
4. `log_operation` 双写的 DB 行落在隔离库；
5. 测试起出的子进程继承隔离 env。
"""

import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROD_HISTORY_DB = os.path.join(_REPO_ROOT, "data", "history.db")
_PROD_VERSIONS_DB = os.path.join(_REPO_ROOT, "outputs", "generation_versions.db")


def test_f6_isolation_env_and_wrapper_active(f6_guard):
    assert f6_guard.connect_is_wrapped, "sqlite3.connect 哨兵包装未生效"
    # conftest 强制（而非 setdefault）设置，早于任何 app import
    assert os.environ["TTS_HISTORY_DB_PATH"] == f6_guard.isolated_history_db
    assert f6_guard.norm(_PROD_HISTORY_DB) in f6_guard.protected_db_paths
    assert _PROD_HISTORY_DB not in (f6_guard.isolated_history_db,)
    # 隔离库目录在生产树之外
    assert not f6_guard.norm(f6_guard.isolated_history_db).startswith(f6_guard.norm(_REPO_ROOT))


def test_f6_history_singleton_resolves_to_isolated_db(f6_guard):
    from integrated_app.history_db import get_history_db

    db = get_history_db()
    assert f6_guard.norm(db._db_path) == f6_guard.norm(f6_guard.isolated_history_db)
    assert f6_guard.norm(db._db_path) not in f6_guard.protected_db_paths


def test_f6_sentinel_raises_on_plain_prod_path(f6_guard):
    import sqlite3

    with pytest.raises(f6_guard.error) as exc_info:
        sqlite3.connect(_PROD_HISTORY_DB)
    msg = str(exc_info.value)
    assert "test_f6_db_isolation_sentinel" in msg, f"哨兵未点名来源测试：{msg}"


def test_f6_sentinel_raises_on_readonly_uri_form(f6_guard):
    import sqlite3

    uri = "file:" + _PROD_HISTORY_DB.replace("\\", "/") + "?mode=ro"
    with pytest.raises(f6_guard.error) as exc_info:
        sqlite3.connect(uri, uri=True)
    assert "test_f6_db_isolation_sentinel" in str(exc_info.value)


def test_f6_sentinel_raises_on_relative_prod_path(f6_guard):
    import sqlite3

    # 测试进程默认 cwd=仓库根时，相对路径同样应被识别为生产库
    old_cwd = os.getcwd()
    os.chdir(_REPO_ROOT)
    try:
        with pytest.raises(f6_guard.error):
            sqlite3.connect(os.path.join("data", "history.db"))
    finally:
        os.chdir(old_cwd)


def test_f6_versions_default_path_redirected_not_written(f6_guard):
    from integrated_app.generation_versioning import VersionManager, reset_version_manager

    before = f6_guard.norm(_PROD_VERSIONS_DB)
    redirect_count_before = len(f6_guard.redirect_log)
    reset_version_manager()
    vm = VersionManager(db_path=None)
    # 哨兵在 sqlite3.connect 层把默认生产路径重定向到会话临时库；调用方自身记录的
    # vm._db_path 仍是原始字符串，真正的落点以 redirect_log 为准。
    assert f6_guard.norm(vm._db_path) == before, "生产路径字符串不该被就地改写"
    assert len(f6_guard.redirect_log) > redirect_count_before, "未记录到 generation_versions 重定向"
    last = f6_guard.redirect_log[-1]
    assert last["from"] == before
    assert last["to"] == f6_guard.norm(f6_guard.isolated_versions_db)
    # 读写在重定向后的隔离库上正常工作（test_memory_store 语义不变）
    vid = vm.save_generation(audio_path="/tmp/f6.wav", text="F6 重定向验证", params={}, engine="f6")
    assert vm.get_version(vid) is not None
    reset_version_manager()


def test_f6_log_operation_lands_in_isolated_db(f6_guard):
    import sqlite3

    from integrated_app.routes.system.logs import log_operation

    log_operation("f6_sentinel_probe", "F6 隔离验证", {"probe": True})
    con = sqlite3.connect(f6_guard.isolated_history_db)
    try:
        n = con.execute("SELECT COUNT(*) FROM action_logs WHERE action='f6_sentinel_probe'").fetchone()[0]
    finally:
        con.close()
    assert n == 1, f"log_operation 未落隔离库（got {n}）"


def test_f6_subprocess_inherits_isolation_env(f6_guard):
    code = "import os; print(os.environ.get('TTS_HISTORY_DB_PATH', ''))"
    out = subprocess.run(  # nosec B607: sys.executable 固定，-c 仅回显 env
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert out.stdout.strip() == f6_guard.isolated_history_db
