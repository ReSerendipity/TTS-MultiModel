#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""下载管理：环境包（torch 运行时）与模型权重的安装、进度、取消、卸载。

瘦包（安装包只剩壳 + 应用代码 + 引导解释器）不含 torch 与权重，用户装完必须
在这里完成安装才能推理。本模块是 ``/api/downloads/*`` 与「下载管理」页面的执行层。

硬约束（同 ``routes/downloads.py``）：**禁止顶层 import torch**。本模块必须能在
引导解释器（无 torch）上运行，否则下载页打不开，用户就陷入「装了软件却装不上
环境」的死局——而这条链路恰恰是瘦包唯一的自救通道。torch 版本信息一律从
``RUNTIME.json`` 元信息文件读，不 import、不启动子进程探测。

两条下载链路：
    1. 环境包（torch/torchvision wheels，zip 分卷）：自己托管在 Release，
       下载 → 逐卷校验 SHA256 → 合并 → 解压 → ``pip install --no-index``
       装进**引导解释器自身**的 site-packages，装完写 ``RUNTIME.json``。
    2. 模型权重：不自己托管，复用 ``model_download_manager``（其内部再调
       ``scripts/download_models.py`` 的 hf-mirror → HF → ModelScope 多源回退），
       避免第二套下载逻辑漂移。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from typing import Any

from .config import PROJECT_ROOT

# ---------------------------------------------------------------- 目录 ----
RUNTIME_DIRNAME = "runtime"
# 瘦包内置的引导解释器目录名（无 torch）。环境装进去后目录名不变，只多出 torch。
BOOTSTRAP_DIRNAME = "bootstrap"
CATALOG_FILENAME = "downloads_catalog.json"
RUNTIME_META_NAME = "RUNTIME.json"

#: 环境包 Release 的默认位置（catalog 里没写 base_url 时的兜底）
DEFAULT_RELEASE_BASE = "https://github.com/ReSerendipity/TTS-MultiModel/releases/download"

#: 环境包里要离线安装的包（catalog.packages 缺省时使用）
DEFAULT_PACKAGES = ["torch", "torchvision"]

#: 卸载时按前缀清理的 torch 家族目录/包名（bootstrap 态不能整目录删）
_TORCH_FAMILY_PREFIXES = ("torch", "functorch", "nvidia_")
_TORCH_FAMILY_NAMES = frozenset({"torch", "torchvision", "torchaudio", "torchgen", "functorch"})

ProgressCb = Callable[[str, float, str], None]  # (stage, ratio 0..1, message)


def _exe_name() -> str:
    """当前平台解释器可执行文件名。"""
    return "python.exe" if os.name == "nt" else "python"


def _runtime_dir() -> str:
    """当前生效的运行时目录。

    瘦包里解释器叫 ``bootstrap/``，环境装进去后**目录名不变**（只多了 torch），
    所以不能只看 ``runtime/``；两个都可能有 python.exe，优先 ``runtime/``。
    """
    runtime = os.path.join(PROJECT_ROOT, RUNTIME_DIRNAME)
    if os.path.isfile(os.path.join(runtime, _exe_name())):
        return runtime
    bootstrap = os.path.join(PROJECT_ROOT, BOOTSTRAP_DIRNAME)
    if os.path.isfile(os.path.join(bootstrap, _exe_name())):
        return bootstrap
    return runtime


def _python_dir() -> str:
    """当前进程的解释器所在目录（环境就装进这里，不另起一份解释器）。"""
    return os.path.dirname(os.path.abspath(sys.executable or "")) or _runtime_dir()


def _catalog_path() -> str:
    """内置环境包清单的路径。"""
    return os.path.join(PROJECT_ROOT, CATALOG_FILENAME)


def _site_packages(runtime_dir: str) -> str:
    """解释器目录下的 site-packages（Windows 便携 Python 为 Lib/site-packages）。"""
    return os.path.join(runtime_dir, "Lib", "site-packages")


def _dir_stats(path: str) -> tuple[int, int]:
    """返回（文件数，总字节）。目录不存在或不可读时返回 (0, 0)。"""
    files = 0
    total = 0
    if not os.path.isdir(path):
        return 0, 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(root, name))
                files += 1
            except OSError:
                continue
    return files, total


def _read_runtime_meta() -> dict | None:
    """读取 ``<runtime>/RUNTIME.json``（环境安装成功后才会写入）。"""
    meta_path = os.path.join(_runtime_dir(), RUNTIME_META_NAME)
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _current_mode() -> str:
    """当前后端跑在哪个解释器上。

    - ``full``：打包态完整运行时 ``app/runtime/python.exe``（含 torch）
    - ``bootstrap``：瘦包内置引导解释器 ``app/bootstrap/python.exe``（无 torch）
    - ``system``：系统 Python（开发态或兜底）
    """
    exe = os.path.abspath(sys.executable or "")
    if not exe:
        return "system"
    for name, label in ((RUNTIME_DIRNAME, "full"), (BOOTSTRAP_DIRNAME, "bootstrap")):
        base = os.path.abspath(os.path.join(PROJECT_ROOT, name))
        if exe == base or exe.startswith(base + os.sep):
            return label
    return "system"


def _version_ge(actual: str | None, required: str | None) -> bool:
    """比较 x.y.z 版本：actual >= required 返回 True。缺值一律视为兼容（不阻断）。"""

    if not actual or not required:
        return True

    def parts(text: str) -> tuple[int, ...]:
        out: list[int] = []
        for chunk in str(text).split(".")[:3]:
            digits = "".join(ch for ch in chunk if ch.isdigit())
            out.append(int(digits) if digits else 0)
        return tuple(out)

    return parts(actual) >= parts(required)


def app_version() -> str:
    """当前应用版本（失败时返回 0.0.0，不影响状态端点可用性）。"""
    try:
        from .config import get_config

        return str(get_config().version or "0.0.0")
    except Exception:  # noqa: BLE001 - 状态端点不能因版本读取失败而 500
        return "0.0.0"


# ------------------------------------------------------------ 组件清单 ----
def load_catalog() -> dict:
    """读取内置清单 ``app/downloads_catalog.json``（随版本更新，离线也可展示）。"""
    path = _catalog_path()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {}


def runtime_catalog() -> dict | None:
    """环境包条目（含分卷、校验和、版本）；清单缺失时返回 None。"""
    entry = load_catalog().get("runtime")
    return entry if isinstance(entry, dict) else None


# -------------------------------------------------------------- 模型清单 ----
def _model_manager():
    """惰性取模型下载管理器单例。

    惰性导入的理由：它内部会按路径加载 ``scripts/download_models.py`` 取
    MODEL_REGISTRY，任何一侧缺失都只应让「模型」区不可用，不能把整个下载页
    （环境包那条自救链路）一起拖挂。
    """
    from .model_download_manager import model_download_manager

    return model_download_manager


def scan_models() -> list[dict]:
    """扫描权重就位情况（不加载权重、不 import torch）。

    复用 ``model_download_manager.catalog()`` 的 MODEL_REGISTRY（单一事实来源），
    额外把 ``installed`` 映射成 ``present`` 并保留 tier/size_hint，供页面直接渲染。
    """
    try:
        catalog = _model_manager().catalog()
    except Exception:  # noqa: BLE001 - 清单缺失时下载页仍须能装环境包
        return []
    out: list[dict] = []
    for item in catalog:
        out.append(
            {
                "name": str(item.get("key", "")),
                "display_name": str(item.get("title") or item.get("key", "")),
                "kind": str(item.get("kind", "weights")),
                "tier": str(item.get("tier", "optional")),
                "license": item.get("license", ""),
                "size_hint_gb": item.get("size_hint_gb"),
                "note": item.get("note", ""),
                "present": bool(item.get("installed")),
                "path": os.path.join(PROJECT_ROOT, "model", str(item.get("local_dir", ""))),
            }
        )
    return out


def build_runtime_status() -> dict:
    """运行时的就位情况。

    ``present``（有 python.exe）与 ``installed``（有 RUNTIME.json）必须分开报：
    引导态下 python.exe 本来就在，但 torch 还没装——「可用」只能看 RUNTIME.json。
    也不能看 ``site-packages/torch``：WinPython 完整版自带 CPU 版 torch，会把
    未装环境误判成已装，用户直接进首页、永远看不到下载页。
    """
    runtime_dir = _runtime_dir()
    exe = os.path.join(runtime_dir, _exe_name())
    raw_meta = _read_runtime_meta()
    meta = raw_meta or {}
    present = os.path.isfile(exe)
    files, size = _dir_stats(runtime_dir) if os.path.isdir(runtime_dir) else (0, 0)
    version = app_version()
    min_app = meta.get("min_app_version")
    return {
        "present": present,
        # 判「已装环境」只能看 RUNTIME.json 是否存在，不能用 bool(meta)：
        # 空对象也是合法元信息（构建期字段全缺），用真假值会把已装环境判成未装。
        "installed": raw_meta is not None,
        "mode": _current_mode(),
        "path": runtime_dir,
        "executable": exe if present else None,
        "python_version": meta.get("python"),
        "torch_version": meta.get("torch"),
        "cuda": meta.get("cuda"),
        "runtime_version": meta.get("runtime_version"),
        "built_at": meta.get("built_at"),
        "installed_at": meta.get("installed_at"),
        "file_count": files,
        "size_bytes": size,
        # 兼容判定：环境包声明的 min_app_version 必须 <= 当前应用版本
        "compatible": _version_ge(version, min_app) if present else True,
        "min_app_version": min_app,
    }


def build_status() -> dict:
    """下载页整体状态：运行模式、环境就位情况、各模型就位情况。"""
    runtime = build_runtime_status()
    models = scan_models()
    return {
        "app_version": app_version(),
        "mode": _current_mode(),
        # 「能跑推理」= 环境装好了（RUNTIME.json）+ 至少一个权重就位；
        # 只看 python.exe 会把引导态误判为就绪。
        "ready": bool(runtime["installed"] and runtime["compatible"] and any(m["present"] for m in models)),
        "runtime": runtime,
        "models": models,
        "model_root": os.path.join(PROJECT_ROOT, "model"),
    }


def catalog_view() -> dict:
    """给页面用的可下载清单：环境包（分卷/校验和）+ 各模型（含是否已就位）。"""
    return {
        "runtime": runtime_catalog(),
        "models": [
            {
                "name": item["name"],
                "display_name": item["display_name"],
                "present": item["present"],
                "tier": item["tier"],
                "size_hint_gb": item["size_hint_gb"],
                "is_default": False,
                # 模型走 HF/ModelScope，不占我们的带宽与 Release 配额
                "source": "huggingface / modelscope",
            }
            for item in scan_models()
        ],
        "catalog_present": bool(load_catalog()),
    }


# ------------------------------------------------------------ HTTP 下载 ----
def _sha256(path: str) -> str:
    """整文件 SHA256（分卷校验用；分卷 1.9 GB，按 1 MiB 分块读）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_file(
    url: str,
    target: str,
    on_progress: ProgressCb | None = None,
    stage: str = "download",
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    """带断点续传的下载：已有 .part 时用 Range 续传；服务端不支持则整文件重下。"""
    part = target + ".part"
    resume = os.path.getsize(part) if os.path.exists(part) else 0
    headers = {"User-Agent": "TTSMultiModel-Desktop"}
    if resume > 0:
        headers["Range"] = f"bytes={resume}-"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        if resume > 0 and response.status != 206:
            resume = 0  # 服务端不支持续传，从头开始
        total = int(response.headers.get("Content-Length") or 0) + resume
        written = resume
        with open(part, "ab" if resume > 0 else "wb") as handle:
            while True:
                if should_cancel and should_cancel():
                    raise InterruptedError("已取消")
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                if on_progress and total > 0:
                    on_progress(stage, min(written / total, 1.0), "")
    os.replace(part, target)


# ------------------------------------------------------------ 环境包安装 ----
def install_runtime(on_progress: ProgressCb | None = None, should_cancel: Callable[[], bool] | None = None) -> dict:
    """下载环境包并把 torch 离线装进当前解释器（bootstrap/ 自身），写 RUNTIME.json。"""
    catalog = runtime_catalog()
    if not catalog:
        raise RuntimeError("未找到环境包清单 downloads_catalog.json，无法确定下载地址（请更新到含清单的版本）")
    volumes = [v for v in catalog.get("volumes", []) if isinstance(v, dict)]
    if not volumes:
        raise RuntimeError("环境包清单缺少 volumes")

    base = str(catalog.get("base_url") or f"{DEFAULT_RELEASE_BASE}/{catalog.get('tag', 'runtime-v1')}")
    staging_parent = os.path.join(PROJECT_ROOT, ".downloads")
    work = os.path.join(staging_parent, "runtime")
    os.makedirs(work, exist_ok=True)

    def report(stage: str, ratio: float, message: str) -> None:
        if on_progress:
            on_progress(stage, ratio, message)

    # 1) 逐卷下载 + SHA256 校验
    parts: list[str] = []
    total_vols = len(volumes)
    for index, vol in enumerate(volumes):
        name = str(vol.get("name"))
        url = f"{base.rstrip('/')}/{name}"
        dest = os.path.join(work, name)
        expected = str(vol.get("sha256") or "").lower()

        if os.path.exists(dest) and expected and _sha256(dest) == expected:
            parts.append(dest)
            report("download", (index + 1) / total_vols, f"{name} 已存在，跳过")
            continue

        report("download", index / total_vols, f"下载 {name}")
        _download_file(
            url,
            dest,
            on_progress=lambda _s, r, _m, i=index, n=name: report(  # type: ignore[misc]
                "download", (i + r) / total_vols, f"下载 {n}"
            ),
            should_cancel=should_cancel,
        )
        if expected:
            actual = _sha256(dest)
            if actual != expected:
                raise RuntimeError(f"{name} 校验失败（期望 {expected[:12]}…，实际 {actual[:12]}…）")
        parts.append(dest)

    # 2) 合并分卷（顺序字节切片；单卷时直接复用）
    report("verify", 0.0, "合并分卷")
    archive = os.path.join(work, "runtime.zip")
    if len(parts) == 1:
        archive = parts[0]
    else:
        with open(archive, "wb") as out:
            for part in parts:
                with open(part, "rb") as handle:
                    shutil.copyfileobj(handle, out, 1024 * 1024)

    # 3) 解压 wheels 到临时目录
    report("extract", 0.0, "解压环境包")
    wheels = os.path.join(work, "wheels")
    if os.path.isdir(wheels):
        shutil.rmtree(wheels, ignore_errors=True)
    os.makedirs(wheels, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(wheels)

    # 4) 离线装入当前解释器（--no-index + --find-links，全程不联网）。
    #    装进 bootstrap/ 自身、不另起一份解释器：目录名不变，壳靠 RUNTIME.json
    #    判定它已经是完整运行时。
    report("swap", 0.5, "安装 torch（离线）")
    target = _python_dir()
    packages = [str(p) for p in (catalog.get("packages") or []) if p] or list(DEFAULT_PACKAGES)
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        wheels,
        "--no-warn-script-location",
        *packages,
    ]
    # PIP_CONFIG_FILE=NUL：屏蔽用户 pip 配置里的 extra-index-url（国内镜像常见）。
    # --no-index 本已禁索引，但显式隔离可避免配置注入引入意外来源。
    env = dict(os.environ, PIP_CONFIG_FILE="NUL" if os.name == "nt" else os.devnull)
    report("swap", 0.7, "安装 torch（离线）")
    result = subprocess.run(  # noqa: S603 - 固定 argv，无 shell
        command, capture_output=True, text=True, cwd=PROJECT_ROOT, env=env
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-400:]
        raise RuntimeError(f"torch 离线安装失败（退出码 {result.returncode}）：{tail}")

    # 5) 落地元信息（status/壳都读它判断「已装环境」，避免启动时 import torch）
    meta = {
        "runtime_version": str(catalog.get("version", "1")),
        "python": catalog.get("python"),
        "torch": catalog.get("torch"),
        "cuda": catalog.get("cuda"),
        "built_at": catalog.get("built_at"),
        "min_app_version": catalog.get("min_app_version"),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "installed_by_app_version": app_version(),
    }
    with open(os.path.join(target, RUNTIME_META_NAME), "w", encoding="utf-8") as meta_handle:
        json.dump(meta, meta_handle, ensure_ascii=False, indent=2)

    shutil.rmtree(work, ignore_errors=True)
    report("done", 1.0, "环境安装完成，请重启应用")
    return meta


# -------------------------------------------------------------- 模型安装 ----
def install_model(
    key: str,
    on_progress: ProgressCb | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """同步下载某个模型（供 ``DownloadManager`` 的任务线程调用）。

    真正的实现是 ``model_download_manager`` 的子进程任务；这里把它的
    phase/log 轮询转成 0..1 的进度回调，让前端只有一套进度模型。
    """
    manager = _model_manager()
    job, err = manager.start(key)
    if job is None or err:
        raise RuntimeError(err or f"无法启动模型下载任务: {key}")

    last_tail = ""
    while True:
        if should_cancel and should_cancel():
            manager.cancel()
            raise InterruptedError("已取消")
        status = manager.status()
        current = status.get("job") or {}
        tail = " | ".join((current.get("log_tail") or [])[-1:])
        if tail and tail != last_tail:
            last_tail = tail
            message = tail[:200]
        else:
            message = f"下载 {key}"
        if on_progress:
            # 子进程日志不提供总字节数，进度条给「进行中」的连续值即可
            on_progress("download", 0.5, message)
        if not current or job.phase not in ("running", "queued"):
            break
        time.sleep(0.5)

    if job.phase == "failed":
        raise RuntimeError(f"模型下载失败（退出码 {job.returncode}），详见日志 {job.log_file}")
    if job.phase == "cancelled":
        raise InterruptedError("已取消")
    if on_progress:
        on_progress("done", 1.0, f"{key} 下载完成")
    return {"component": key, "phase": job.phase}


def uninstall_runtime() -> None:
    """卸载运行环境。

    瘦包态（bootstrap/）不能整目录删——正在用这个解释器跑后端，删了应用再也起不来。
    此时只卸 torch 家族包与 RUNTIME.json，把 bootstrap 还原成「未装环境」。
    """
    target = _runtime_dir()
    if not os.path.isdir(target):
        return
    if os.path.basename(os.path.normpath(target)) == BOOTSTRAP_DIRNAME:
        site = _site_packages(target)
        if os.path.isdir(site):
            for entry in os.listdir(site):
                lowered = entry.lower()
                if lowered.startswith(_TORCH_FAMILY_PREFIXES) or entry in _TORCH_FAMILY_NAMES:
                    shutil.rmtree(os.path.join(site, entry), ignore_errors=True)
        meta = os.path.join(target, RUNTIME_META_NAME)
        if os.path.isfile(meta):
            os.remove(meta)
        return
    shutil.rmtree(target, ignore_errors=True)


def uninstall_model(key: str) -> None:
    """删除某个模型的权重目录（各模型 local_dir 独占，无共享资产问题）。"""
    entry = _model_manager().get_entry(key)
    if not entry:
        raise KeyError(f"未知模型 KEY: {key}")
    local_dir = str(entry.get("local_dir") or "")
    if not local_dir:
        return
    base = os.path.abspath(
        os.path.join(PROJECT_ROOT, "reference_repos" if entry.get("kind") == "code_repo" else "model")
    )
    target = os.path.abspath(os.path.join(base, local_dir))
    # 路径守卫：只允许删 base 下的子目录，防止 local_dir 里出现 .. 之类的越界
    if os.path.commonpath([base, target]) != base or target == base:
        raise ValueError(f"拒绝删除越界路径: {target}")
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)


# ------------------------------------------------------------ 任务管理 ----
class DownloadTask:
    """单个下载任务的状态（后台线程执行，前端轮询进度）。"""

    def __init__(self, component: str, kind: str, target: str) -> None:
        self.component = component
        self.kind = kind  # runtime | model
        self.target = target
        self.stage = "queued"
        self.ratio = 0.0
        self.message = ""
        self.error: str | None = None
        self.finished = False
        self.started_at = time.time()
        self._cancel = threading.Event()

    def to_dict(self) -> dict:
        """序列化为 API 响应（前端 1s 轮询用）。"""
        return {
            "component": self.component,
            "kind": self.kind,
            "target": self.target,
            "stage": self.stage,
            "ratio": round(self.ratio, 4),
            "message": self.message,
            "error": self.error,
            "finished": self.finished,
            "elapsed_seconds": round(time.time() - self.started_at, 1),
        }

    def request_cancel(self) -> None:
        """置取消标志；下载循环在下一次读块/轮询时退出。"""
        self._cancel.set()

    def _progress(self, stage: str, ratio: float, message: str) -> None:
        self.stage = stage
        self.ratio = ratio
        self.message = message

    def run(self) -> None:
        """任务主体：分派到环境包或模型安装，异常一律转成任务错误态。"""
        try:
            if self.kind == "runtime":
                install_runtime(self._progress, self._cancel.is_set)
            else:
                install_model(self.target, self._progress, self._cancel.is_set)
            self.stage = "done"
            self.ratio = 1.0
        except InterruptedError as exc:
            self.stage = "cancelled"
            self.error = str(exc)
        except (urllib.error.URLError, OSError) as exc:
            self.stage = "error"
            self.error = f"网络错误: {exc}"
        except Exception as exc:  # noqa: BLE001 - 错误需回传给用户，不能让线程静默死
            self.stage = "error"
            self.error = str(exc)
        finally:
            self.finished = True


class DownloadManager:
    """进程级单例：同一组件同时只允许一个任务在跑。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, DownloadTask] = {}

    def start(self, kind: str, target: str = "") -> DownloadTask:
        """启动（或复用）一个下载任务。"""
        component = "runtime" if kind == "runtime" else f"model:{target}"
        with self._lock:
            existing = self._tasks.get(component)
            if existing and not existing.finished:
                return existing
            task = DownloadTask(component, kind, target)
            self._tasks[component] = task
        thread = threading.Thread(target=task.run, name=f"download-{component}", daemon=True)
        thread.start()
        return task

    def progress(self) -> list[dict]:
        """全部任务的进度快照。"""
        with self._lock:
            return [task.to_dict() for task in self._tasks.values()]

    def cancel(self, component: str) -> bool:
        """请求取消某个任务；任务不存在或已结束时返回 False。"""
        task = self._tasks.get(component)
        if not task or task.finished:
            return False
        task.request_cancel()
        return True


_MANAGER: DownloadManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_download_manager() -> DownloadManager:
    """取进程级下载任务管理器（双检锁单例）。"""
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = DownloadManager()
    return _MANAGER


def _unused(*args: Any) -> None:  # 占位：保留扩展点
    return None
