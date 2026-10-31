# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""下载管理（瘦包自救通道）的判据与 API 契约测试。

为什么这些测试重要：瘦包装完只有引导解释器（无 torch），「下载管理」页是用户
安装环境与权重的唯一入口。判据一旦写错（例如用 site-packages/torch 判「已装
环境」，而 WinPython 自带 CPU 版 torch），用户会直接进首页、永远看不到下载页。

覆盖：
    - ``build_runtime_status``：present（有 python.exe）与 installed（有
      RUNTIME.json）分开报；只有 python.exe 时 installed=False
    - ``build_status.ready``：必须 installed + 至少一个权重就位
    - ``uninstall_runtime`` 在 bootstrap 态只摘 torch 家族与 RUNTIME.json，
      绝不删目录（正在用它跑后端）
    - 路由：status/catalog 200、非法 kind 400、非本机来源 403、未知模型 400
"""

from __future__ import annotations

import json
import os
import sys

import pytest

_APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from integrated_app import download_service as ds  # noqa: E402
from integrated_app.routes import downloads as downloads_route  # noqa: E402


def _make_app_dir(tmp_path, runtime_name: str = "bootstrap") -> os.PathLike[str]:
    """造一个打包态目录：<root>/<runtime_name>/python.exe。"""
    runtime = tmp_path / runtime_name
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "python.exe").write_bytes(b"")
    return tmp_path


@pytest.fixture()
def boot_root(tmp_path, monkeypatch):
    """把 download_service 的 PROJECT_ROOT 指到临时打包态目录。"""
    root = _make_app_dir(tmp_path)
    monkeypatch.setattr(ds, "PROJECT_ROOT", str(root))
    return root


def test_present_without_meta_is_not_installed(boot_root):
    """引导态：有 python.exe 但没 RUNTIME.json → present=True / installed=False。"""
    status = ds.build_runtime_status()
    assert status["present"] is True
    assert status["installed"] is False
    assert status["torch_version"] is None


def test_installed_follows_runtime_meta(boot_root):
    """装完环境后写 RUNTIME.json → installed=True 且带出版本信息。"""
    meta = {"runtime_version": "1", "torch": "2.13.0+cu132", "cuda": "cu132", "python": "3.12.10"}
    (boot_root / "bootstrap" / ds.RUNTIME_META_NAME).write_text(json.dumps(meta), encoding="utf-8")
    status = ds.build_runtime_status()
    assert status["installed"] is True
    assert status["torch_version"] == "2.13.0+cu132"
    assert status["cuda"] == "cu132"


def test_bundled_torch_without_meta_is_still_not_installed(boot_root):
    """回归：WinPython 自带 CPU 版 torch 不得被当成「已装环境」。"""
    site_torch = boot_root / "bootstrap" / "Lib" / "site-packages" / "torch"
    site_torch.mkdir(parents=True)
    (site_torch / "__init__.py").write_text("", encoding="utf-8")
    assert ds.build_runtime_status()["installed"] is False


def test_ready_requires_runtime_meta_and_a_model(boot_root, monkeypatch):
    """ready 必须同时满足「环境已装」与「至少一个权重就位」。"""
    monkeypatch.setattr(ds, "scan_models", lambda: [{"name": "m", "present": True}])
    assert ds.build_status()["ready"] is False  # 只有权重、没装环境

    (boot_root / "bootstrap" / ds.RUNTIME_META_NAME).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ds, "scan_models", lambda: [{"name": "m", "present": False}])
    assert ds.build_status()["ready"] is False  # 装了环境、没权重

    monkeypatch.setattr(ds, "scan_models", lambda: [{"name": "m", "present": True}])
    assert ds.build_status()["ready"] is True


def test_uninstall_in_bootstrap_keeps_interpreter(boot_root):
    """bootstrap 态卸载：只摘 torch 家族与 RUNTIME.json，解释器目录必须留着。"""
    site = boot_root / "bootstrap" / "Lib" / "site-packages"
    (site / "torch").mkdir(parents=True)
    (site / "torchvision").mkdir()
    (site / "torch-2.13.0.dist-info").mkdir()
    (site / "fastapi").mkdir()
    (boot_root / "bootstrap" / ds.RUNTIME_META_NAME).write_text("{}", encoding="utf-8")

    ds.uninstall_runtime()

    assert (boot_root / "bootstrap" / "python.exe").exists(), "不能删引导解释器"
    assert not (site / "torch").exists()
    assert not (site / "torchvision").exists()
    assert not (site / "torch-2.13.0.dist-info").exists()
    assert (site / "fastapi").exists(), "非 torch 依赖必须保留"
    assert not (boot_root / "bootstrap" / ds.RUNTIME_META_NAME).exists()
    assert ds.build_runtime_status()["installed"] is False


def _client(config: dict | None = None) -> TestClient:
    """只挂下载路由的最小应用（不加载 torch / 引擎路由）。"""
    app = FastAPI()
    app.state.config = config or {}
    app.include_router(downloads_route.router)
    return TestClient(app)


@pytest.fixture()
def local_host(monkeypatch):
    """把回环白名单放宽到 TestClient 的伪主机，便于测「过了守卫之后」的分支。

    守卫本身另有 test_loopback_guard_blocks_foreign_host 用默认白名单覆盖。
    """
    monkeypatch.setattr(downloads_route, "LOOPBACK_HOSTS", frozenset({"testclient"}))


def test_status_and_catalog_are_200(boot_root):
    client = _client()
    assert client.get("/api/downloads/status").status_code == 200
    assert client.get("/api/downloads/catalog").status_code == 200
    body = client.get("/api/downloads/status").json()
    assert body["success"] is True
    assert body["data"]["mode"] in {"bootstrap", "full", "system"}
    assert body["data"]["runtime"]["installed"] is False


def test_start_rejects_unknown_kind(boot_root, local_host):
    resp = _client().post("/api/downloads/start", json={"kind": "nope", "target": ""})
    assert resp.status_code == 400


def test_start_runtime_without_catalog_reports_503(boot_root, local_host):
    """清单缺失时必须给可读错误，而不是启动一个注定失败的下载。"""
    assert ds.runtime_catalog() is None
    resp = _client().post("/api/downloads/start", json={"kind": "runtime", "target": ""})
    assert resp.status_code == 503


def test_uninstall_unknown_model_is_400(boot_root, local_host, monkeypatch):
    class _Stub:
        def get_entry(self, key):  # noqa: ANN001, ANN202
            return None

    monkeypatch.setattr(ds, "_model_manager", lambda: _Stub())
    resp = _client().post("/api/downloads/uninstall", json={"kind": "model", "target": "nope"})
    assert resp.status_code == 400


def test_loopback_guard_blocks_foreign_host(boot_root):
    """非本机来源调用重操作端点必须 403（TestClient 的 client.host 是 testclient）。"""
    client = _client()
    assert client.post("/api/downloads/cancel", json={"kind": "runtime"}).status_code == 403
    assert client.post("/api/downloads/uninstall", json={"kind": "runtime"}).status_code == 403
