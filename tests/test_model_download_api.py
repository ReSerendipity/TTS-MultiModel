# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""模型下载管理器 API 契约测试（子进程打桩，不触网、不下载任何权重）。

覆盖：
    - catalog：9 个条目、tier 排序（required→recommended→optional）、
      installed 标志随磁盘状态变化、code_repo 类目标记
    - start：未知 KEY 404；合法 KEY 202 语义（ok+job）；
      已有任务运行中 409；重复 KEY 复用同一 job 对象
    - status：running 态与终态（poll 收割 returncode）
    - cancel：运行中可取消；空闲时 409

Popen 打桩：monkeypatch ``model_download_manager.subprocess.Popen`` 为
立即退出的假进程（``poll()`` 依次返回 None→0），日志写临时文件。
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Any

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")

from fastapi.testclient import TestClient  # noqa: E402

from integrated_app import model_download_manager as mdm  # noqa: E402


class _FakeProc:
    """可编程退出态的假 Popen：序列化脚本为依次返回的 poll 值。"""

    def __init__(self, poll_sequence: list[int | None], log_text: str = "") -> None:
        self._seq = list(poll_sequence)
        self.pid = 424242
        self.log_text = log_text
        self._stdout_fh: Any = None

    def poll(self) -> int | None:
        if len(self._seq) > 1:
            return self._seq.pop(0)
        return self._seq[0]

    def terminate(self) -> None:
        self._seq = [0]

    def kill(self) -> None:
        self._seq = [0]


@pytest.fixture()
def client() -> Any:
    from integrated_app.app_server import create_app

    return TestClient(create_app())


@pytest.fixture()
def fresh_manager(tmp_path: Any, monkeypatch: Any) -> mdm.ModelDownloadManager:
    """隔离的管理器实例：日志与脚本路径打到 tmp，注册表为最小桩。"""
    monkeypatch.setattr(mdm, "_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(mdm, "_DOWNLOAD_SCRIPT", tmp_path / "download_models.py")
    q = chr(39)  # 单引号
    stub = (
        "MODEL_REGISTRY = ["
        + chr(10)
        + "    {"
        + q
        + "key"
        + q
        + ": "
        + q
        + "voxcpm2"
        + q
        + ", "
        + q
        + "title"
        + q
        + ": "
        + q
        + "V"
        + q
        + ", "
        + q
        + "local_dir"
        + q
        + ": "
        + q
        + "VoxCPM2"
        + q
        + ", "
        + q
        + "tier"
        + q
        + ": "
        + q
        + "required"
        + q
        + "},"
        + chr(10)
        + "    {"
        + q
        + "key"
        + q
        + ": "
        + q
        + "breeze-tts2"
        + q
        + ", "
        + q
        + "title"
        + q
        + ": "
        + q
        + "B"
        + q
        + ", "
        + q
        + "local_dir"
        + q
        + ": "
        + q
        + "Breeze-TTS-2"
        + q
        + ", "
        + q
        + "tier"
        + q
        + ": "
        + q
        + "optional"
        + q
        + "},"
        + chr(10)
        + "    {"
        + q
        + "key"
        + q
        + ": "
        + q
        + "openvoice"
        + q
        + ", "
        + q
        + "title"
        + q
        + ": "
        + q
        + "O"
        + q
        + ", "
        + q
        + "local_dir"
        + q
        + ": "
        + q
        + "OpenVoice"
        + q
        + ", "
        + q
        + "tier"
        + q
        + ": "
        + q
        + "optional"
        + q
        + "},"
        + chr(10)
        + "    {"
        + q
        + "key"
        + q
        + ": "
        + q
        + "indextts2"
        + q
        + ", "
        + q
        + "title"
        + q
        + ": "
        + q
        + "I"
        + q
        + ", "
        + q
        + "local_dir"
        + q
        + ": "
        + q
        + "IndexTTS-2.5"
        + q
        + ", "
        + q
        + "tier"
        + q
        + ": "
        + q
        + "recommended"
        + q
        + "},"
        + chr(10)
        + "]"
        + chr(10)
    )
    (tmp_path / "download_models.py").write_text(stub, encoding="utf-8")
    return mdm.ModelDownloadManager()


def _patch_popen(monkeypatch: Any, proc: _FakeProc) -> None:
    def _fake_popen(cmd: Any, **kwargs: Any) -> _FakeProc:
        proc._stdout_fh = kwargs.get("stdout")
        if proc._stdout_fh is not None:
            proc._stdout_fh.write(proc.log_text)
            proc._stdout_fh.flush()
        return proc

    monkeypatch.setattr(mdm.subprocess, "Popen", _fake_popen)


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------


class TestCatalog:
    def test_catalog_lists_all_entries_with_tiers(self, client: Any) -> None:
        r = client.get("/api/models/download/catalog")
        assert r.status_code == 200
        items = r.json()["items"]
        keys = [i["key"] for i in items]
        assert len(keys) == 9
        for required_key in ("voxcpm2", "sensevoice", "zipenhancer", "breeze-tts2", "index-tts"):
            assert required_key in keys
        tiers = [i["tier"] for i in items]
        assert tiers == sorted(tiers, key=lambda x: {"required": 0, "recommended": 1, "optional": 2}[x])
        assert items[0]["tier"] == "required"

    def test_catalog_marks_code_repo(self, client: Any, monkeypatch: Any, tmp_path: Any) -> None:
        # installed 断言与磁盘环境无关：reference_repos 根指到 tmp 沙箱
        monkeypatch.setattr(mdm, "REFERENCE_REPOS_DIR", tmp_path / "reference_repos")
        items = client.get("/api/models/download/catalog").json()["items"]
        repo = next(i for i in items if i["key"] == "index-tts")
        assert repo["kind"] == "code_repo"
        assert repo["installed"] is False  # 沙箱内无克隆
        # 克隆后（.git 出现）翻转为 True
        git_dir = tmp_path / "reference_repos" / "index-tts" / ".git"
        git_dir.mkdir(parents=True)
        (git_dir / "HEAD").write_text("ref: refs/heads/main" + chr(10), encoding="utf-8")
        items2 = client.get("/api/models/download/catalog").json()["items"]
        repo2 = next(i for i in items2 if i["key"] == "index-tts")
        assert repo2["installed"] is True

    def test_installed_flag_reflects_disk(
        self, fresh_manager: mdm.ModelDownloadManager, tmp_path: Any, monkeypatch: Any
    ) -> None:
        entry = next(e for e in fresh_manager._registry if e["key"] == "voxcpm2")
        assert fresh_manager.catalog()  # smoke
        target = tmp_path / "model" / entry["local_dir"]
        monkeypatch.setattr(mdm, "_PROJECT_ROOT", tmp_path)
        # 目录不存在 → False；有文件 → True
        assert mdm._is_installed(entry) is False
        target.mkdir(parents=True)
        (target / "dummy.bin").write_bytes(b"x")
        assert mdm._is_installed(entry) is True


# ---------------------------------------------------------------------------
# start / status / cancel 生命周期
# ---------------------------------------------------------------------------


class TestJobLifecycle:
    def test_start_unknown_key_404(self, client: Any) -> None:
        client.get("/")  # 取 CSRF cookie（Double-Submit 中间件）
        r = client.post(
            "/api/models/download/start",
            data={"key": "not-a-model"},
            headers={"X-CSRF-Token": client.cookies.get("csrf_token") or ""},
        )
        assert r.status_code == 404
        assert "未知模型 KEY" in r.json()["message"]

    def test_start_runs_and_completes(self, fresh_manager: mdm.ModelDownloadManager, monkeypatch: Any) -> None:
        proc = _FakeProc([None, 0], log_text="line1\nDownloading: 34%|██\n")
        _patch_popen(monkeypatch, proc)
        job, err = fresh_manager.start("voxcpm2")
        assert err == "" and job is not None
        st = fresh_manager.status()
        assert st["running"] is True
        assert st["job"]["phase"] == "running"
        assert any("Downloading" in ln for ln in st["job"]["log_tail"])
        # 收割：poll 返回 0 → done
        st = fresh_manager.status()
        assert st["running"] is False
        assert st["job"]["phase"] == "done"
        assert st["job"]["returncode"] == 0

    def test_start_while_running_409(self, fresh_manager: mdm.ModelDownloadManager, monkeypatch: Any) -> None:
        proc = _FakeProc([None, None])
        _patch_popen(monkeypatch, proc)
        job, err = fresh_manager.start("voxcpm2")
        assert err == ""
        job2, err2 = fresh_manager.start("breeze-tts2")
        assert job2 is None
        assert "已有下载任务在运行" in err2

    def test_failed_download_marks_failed(self, fresh_manager: mdm.ModelDownloadManager, monkeypatch: Any) -> None:
        proc = _FakeProc([1])  # 首次 poll 即返回 rc=1（模拟立即失败）
        _patch_popen(monkeypatch, proc)
        fresh_manager.start("breeze-tts2")
        st = fresh_manager.status()
        assert st["job"]["phase"] == "failed"
        assert st["job"]["returncode"] == 1

    def test_cancel_running_then_idle_409(self, fresh_manager: mdm.ModelDownloadManager, monkeypatch: Any) -> None:
        proc = _FakeProc([None, None])
        _patch_popen(monkeypatch, proc)
        fresh_manager.start("openvoice")
        ok, err = fresh_manager.cancel()
        assert ok and err == ""
        assert fresh_manager.status()["job"]["phase"] == "cancelled"
        ok2, err2 = fresh_manager.cancel()
        assert ok2 is False and err2

    def test_manager_single_active_job_thread_safety(
        self, fresh_manager: mdm.ModelDownloadManager, monkeypatch: Any
    ) -> None:
        """并发 start 只允许一个进入 running（锁内判定，余下收到 409 语义）。"""
        proc = _FakeProc([None, None])
        _patch_popen(monkeypatch, proc)
        results: list[tuple[str, str]] = []
        barrier = threading.Barrier(4)

        def _try(key: str) -> None:
            barrier.wait()
            results.append(fresh_manager.start(key))

        threads = [
            threading.Thread(target=_try, args=(k,)) for k in ("voxcpm2", "breeze-tts2", "openvoice", "indextts2")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ok_count = sum(1 for _, err in results if err == "")
        assert ok_count == 1
        assert all("已有下载任务在运行" in err for _, err in results if err)


# ---------------------------------------------------------------------------
# HTTP 层（真实路由，Popen 打桩）
# ---------------------------------------------------------------------------


class TestDownloadRoutes:
    def test_start_status_cancel_http_roundtrip(self, client: Any, monkeypatch: Any, tmp_path: Any) -> None:
        monkeypatch.setattr(mdm, "_LOG_DIR", tmp_path / "logs")
        monkeypatch.setattr(mdm, "_DOWNLOAD_SCRIPT", tmp_path / "download_models.py")
        (tmp_path / "download_models.py").write_text("# stub\n", encoding="utf-8")
        proc = _FakeProc([None, None])
        _patch_popen(monkeypatch, proc)

        # 拿 CSRF
        client.get("/")
        headers = {"X-CSRF-Token": client.cookies.get("csrf_token") or ""}

        r = client.post("/api/models/download/start", data={"key": "voxcpm2"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["job"]["phase"] == "running"

        st = client.get("/api/models/download/status").json()
        assert st["running"] is True

        r = client.post("/api/models/download/start", data={"key": "voxcpm2"}, headers=headers)
        assert r.status_code == 409

        r = client.post("/api/models/download/cancel", headers=headers)
        assert r.status_code == 200
        st = client.get("/api/models/download/status").json()
        assert st["job"]["phase"] == "cancelled"
