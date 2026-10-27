# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""模型下载管理器（framework-only 分发口径的用户侧补全）。

背景：v2.8.0 起桌面发布包不再内置模型权重（docs/plans/MODEL_DISTRIBUTION_AUDIT.md
§六），用户首次安装后需要自行拉取权重。此前唯一的入口是命令行
``python scripts/download_models.py --model <key>``——对桌面版用户不够友好。

本模块把既有下载脚本产品化为**可管理的后台任务**：

    - 目录（catalog）：复用 ``scripts/download_models.py`` 的 ``MODEL_REGISTRY``
      （单一事实来源），补充 tier（required/recommended/optional）与安装状态；
    - 任务（job）：以**子进程**运行下载脚本（``--model <key>``），进程隔离保证
      网络库崩溃不拖垮 Web 服务，且可取消（terminate→kill）；
    - 进度：日志落盘 ``cache/model_downloads/<key>.log``，状态端点回传
      阶段 + 尾部日志行（快照下载器的 tqdm 进度行原样透出）。

设计约束：
    - 同一时刻只允许一个下载任务（磁盘与网络都是瓶颈，并发下载多模型
      只会让每个都变慢；队列化属于过度设计，前端按钮态已足够表达）；
    - 管理器自身不做下载实现——一切以脚本为准，避免两套下载逻辑漂移；
    - code_repo 类目（如 index-tts 代码仓库）与权重类目同一套任务模型，
      脚本内按 ``kind`` 分流实现。
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("tts_multimodel")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DOWNLOAD_SCRIPT = _PROJECT_ROOT / "scripts" / "download_models.py"
_LOG_DIR = _PROJECT_ROOT / "cache" / "model_downloads"

#: code_repo 类目的落盘根（引擎 sys.path 引导同源，见 indextts2_engine 引导注释）
REFERENCE_REPOS_DIR = _PROJECT_ROOT / "reference_repos"


def _load_registry() -> list[dict[str, Any]]:
    """从下载脚本导入 MODEL_REGISTRY（单一事实来源，避免两份清单漂移）。"""
    spec = importlib.util.spec_from_file_location("_tts_download_models", _DOWNLOAD_SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - 脚本必然存在
        raise RuntimeError(f"下载脚本缺失: {_DOWNLOAD_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.MODEL_REGISTRY)


def _is_installed(entry: dict[str, Any]) -> bool:
    """判断一个目录项是否已就位（权重：目录非空；code_repo：存在 .git）。"""
    if entry.get("kind") == "code_repo":
        git_dir = REFERENCE_REPOS_DIR / entry["local_dir"] / ".git"
        return git_dir.is_dir()
    target = _PROJECT_ROOT / "model" / entry["local_dir"]
    if not target.is_dir():
        return False
    return any(p.is_file() for p in target.rglob("*") if p.name != ".lock")


class DownloadJob:
    """单个下载任务的状态快照与子进程句柄。"""

    def __init__(self, key: str) -> None:
        self.key = key
        self.phase: str = "queued"  # queued/running/done/failed/cancelled
        self.returncode: int | None = None
        self.started_at: float = 0.0
        self.finished_at: float = 0.0
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_path: Path = _LOG_DIR / f"{key}.log"

    def to_dict(self, tail_lines: int = 30) -> dict[str, Any]:
        """序列化为 API 响应（含日志尾部）。"""
        return {
            "key": self.key,
            "phase": self.phase,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_s": round((self.finished_at or time.time()) - self.started_at, 1) if self.started_at else 0.0,
            "log_tail": self._read_tail(tail_lines),
            "log_file": str(self._log_path),
        }

    def _read_tail(self, n: int) -> list[str]:
        try:
            lines = self._log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            return lines[-n:]
        except OSError:
            return []

    def start(self) -> None:
        """拉起下载子进程（日志重定向到文件；调用方持有 manager 锁）。"""
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_fh = self._log_path.open("w", encoding="utf-8", errors="replace")
        log_fh.write(f"$ {sys.executable} {_DOWNLOAD_SCRIPT} --model {self.key}\n")
        log_fh.flush()
        self.started_at = time.time()
        self.phase = "running"
        self._proc = subprocess.Popen(  # noqa: S603 - 固定脚本路径 + 白名单 key
            [sys.executable, str(_DOWNLOAD_SCRIPT), "--model", self.key],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(_PROJECT_ROOT),
        )
        logger.info("[model-download] 任务启动: %s (pid=%s)", self.key, self._proc.pid)

    def poll(self) -> None:
        """非阻塞收割子进程退出态（由 status 端点顺带驱动）。"""
        if self.phase != "running" or self._proc is None:
            return
        rc = self._proc.poll()
        if rc is None:
            return
        self.returncode = rc
        self.finished_at = time.time()
        self.phase = "done" if rc == 0 else "failed"
        logger.info("[model-download] 任务结束: %s rc=%s (%.0fs)", self.key, rc, self.elapsed())

    def cancel(self) -> bool:
        """尽力终止子进程（terminate → 1.5s 后仍活则 kill）。返回是否发出了信号。"""
        if self.phase != "running" or self._proc is None:
            return False
        with contextlib.suppress(OSError):
            self._proc.terminate()

        def _escalate() -> None:
            time.sleep(1.5)
            if self._proc is not None and self._proc.poll() is None:
                with contextlib.suppress(OSError):
                    self._proc.kill()

        threading.Thread(target=_escalate, daemon=True).start()
        self.phase = "cancelled"
        self.finished_at = time.time()
        logger.info("[model-download] 任务已请求取消: %s", self.key)
        return True

    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        return time.time() - self.started_at


class ModelDownloadManager:
    """模型下载任务管理器（进程内单例；同一时刻至多一个活动任务）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: DownloadJob | None = None
        self._registry = _load_registry()
        self._history: dict[str, DownloadJob] = {}

    # ---- 目录 ----

    def catalog(self) -> list[dict[str, Any]]:
        """返回下载目录（含 tier / 安装状态 / 体积提示），按 tier 排序。"""
        order = {"required": 0, "recommended": 1, "optional": 2}
        items = []
        for entry in self._registry:
            items.append(
                {
                    "key": entry["key"],
                    "title": entry["title"],
                    "local_dir": entry["local_dir"],
                    "kind": entry.get("kind", "weights"),
                    "tier": entry.get("tier", "optional"),
                    "license": entry.get("license", ""),
                    "size_hint_gb": entry.get("size_hint_gb"),
                    "note": entry.get("note", ""),
                    "installed": _is_installed(entry),
                }
            )
        items.sort(key=lambda x: (order.get(x["tier"], 3), x["key"]))
        return items

    def get_entry(self, key: str) -> dict[str, Any] | None:
        for entry in self._registry:
            if entry["key"] == key:
                return entry
        return None

    # ---- 任务 ----

    def start(self, key: str) -> tuple[DownloadJob | None, str]:
        """启动下载任务。返回 (job, 错误消息)；成功时错误消息为空。"""
        entry = self.get_entry(key)
        if entry is None:
            return None, f"未知模型 KEY: {key}"
        with self._lock:
            if self._active is not None and self._active.phase == "running":
                return None, f"已有下载任务在运行（{self._active.key}），请等待完成或先取消"
            if _is_installed(entry) and entry.get("kind") != "code_repo":
                # 权重已就位：允许重下（断点续传/修复），但不主动拦截——
                # 由前端把按钮置为"重新下载"弱化误触；后端保持能力开放
                pass
            job = DownloadJob(key)
            try:
                job.start()
            except OSError as exc:
                logger.exception("[model-download] 任务启动失败: %s", exc)
                return None, f"下载进程启动失败: {exc}"
            self._active = job
            self._history[key] = job
            return job, ""

    def status(self) -> dict[str, Any]:
        """当前任务状态（顺带收割子进程退出态）。"""
        with self._lock:
            job = self._active
        if job is None:
            return {"running": False, "job": None}
        job.poll()
        return {"running": job.phase == "running", "job": job.to_dict()}

    def cancel(self) -> tuple[bool, str]:
        with self._lock:
            job = self._active
        if job is None or job.phase != "running":
            return False, "当前没有运行中的下载任务"
        ok = job.cancel()
        return ok, "" if ok else "任务已不在运行态"


#: 进程内单例（FastAPI 多 worker 部署形态下每进程各持一个；本项目单进程 uvicorn）
model_download_manager = ModelDownloadManager()
