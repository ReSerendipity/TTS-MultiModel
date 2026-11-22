# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Breeze TTS 2 的依赖隔离桥接。

## 为什么需要它

Breeze 上游（breezeblue-ai/breeze-tts）把 transformers 固定在 **4.57.3**，
并要求 ``qwen-tts==0.1.1``。本项目全局锁 transformers 5.20.2（为其它引擎和
安全公告），此前用 ``tf5_compat`` 的七处兼容补丁在 5.x 上跑 Breeze。

**实测结论（2026-11-22，用户人工试听确认）**：5.x + 兼容层产出的中文/英文
音频**全部是噪音**；同一份权重在 4.57.3 + 官方 infer.py 下正常。数值证据：

==========================  ========  ========  =========
路径                        peak      rms       过零率/自相关
==========================  ========  ========  =========
纯官方 4.57.3               0.637     0.092     0.058 / 0.985
项目 5.x + tf5_compat       1.000     0.176     0.124 / 0.886（噪音）
隔离 venv 4.57.3 + 我们CLI  1.000*    0.100     0.062 / 0.987（正常）
==========================  ========  ========  =========
(* 仅 1 个削波样本，非整体削波)

## 方案

检测当前 transformers 主版本：

- **< 5**：原生 in-process 推理（上游原生支持，零偏差）。
- **>= 5**：把 Breeze 生成委托给**隔离 venv** 里的 CLI 子进程
  （该 venv 装 4.57.3 + qwen-tts），读回 JSON 结果与 WAV。

隔离 venv 路径优先级：

1. 环境变量 ``TTS_BREEZE_VENV``
2. ``D:/breeze-venv``（本机默认，Windows）
3. 项目根 ``.venv-breeze``（其它平台 / 便携部署）

未配置时给出明确的创建指引（见 ``scripts/setup_breeze_venv.py``），
绝不静默退回 5.x 噪音路径。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# 项目根 / app 目录（与本仓其它模块口径一致）
_APP_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _APP_DIR.parent


def transformers_major() -> int:
    """当前进程的 transformers 主版本号。"""
    try:
        import transformers

        return int(transformers.__version__.split(".", 1)[0])
    except Exception:  # noqa: BLE001 - 未安装时按未知（高版本）处理，走隔离
        return 99


def breeze_venv_python() -> Path | None:
    """返回隔离 venv 的解释器路径；不存在返回 None。"""
    candidates: list[Path] = []

    env = os.environ.get("TTS_BREEZE_VENV", "").strip()
    if env:
        candidates.append(Path(env))

    if sys.platform == "win32":
        candidates.append(Path("D:/breeze-venv"))
        if os.environ.get("USERPROFILE"):
            candidates.append(Path(os.environ["USERPROFILE"]) / "breeze-venv")
    candidates.append(_PROJECT_ROOT / ".venv-breeze")

    for root in candidates:
        if sys.platform == "win32":
            exe = root / "Scripts" / "python.exe"
        else:
            exe = root / "bin" / "python"
        if exe.is_file():
            return exe
    return None


def setup_hint() -> str:
    """隔离环境缺失时的可行动指引。"""
    return (
        "Breeze TTS 2 需要 transformers 4.57.3，但本项目运行环境是 5.x；"
        "5.x 兼容层产物已确认是噪音。请创建隔离环境：\n"
        "  python scripts/setup_breeze_venv.py\n"
        "或设置 TTS_BREEZE_VENV 指向已装好 transformers==4.57.3 + qwen-tts==0.1.1 的 venv。"
    )


def should_delegate() -> bool:
    """是否应把 Breeze 生成委托给隔离 venv。"""
    return transformers_major() >= 5


def run_isolated_breeze(args: Any, mode: str) -> dict[str, Any] | None:
    """在隔离 venv 里执行 breeze-* 子命令，返回解析后的 JSON data。

    Args:
        args: 当前 CLI 的命名空间对象（含 text / control / seed / output 等）。
        mode: ``design`` / ``clone`` / ``direction``。

    Returns:
        成功时返回 JSON 的 ``data`` 字典（含 audio_path / duration / params）；
        失败时返回 ``{"error": ..., "error_code": ...}``；子进程自身异常返回 None
        （由调用方回落到普通错误路径）。

    Raises:
        不抛异常：所有失败都编码进返回值，便于上层统一转 Result。
    """
    python = breeze_venv_python()
    if python is None:
        return {"error": setup_hint(), "error_code": "NOT_READY"}

    cli = _APP_DIR / "cli.py"
    if not cli.is_file():
        return {"error": f"CLI 入口不存在: {cli}", "error_code": "NOT_READY"}

    cmd = [str(python), str(cli), "generate", f"breeze-{mode}", "--text", str(args.text)]
    control = getattr(args, "control", None) or ""
    reference = getattr(args, "reference_audio", None)
    ref_text = getattr(args, "ref_text", None)
    cfg = getattr(args, "cfg_scale", None)
    seed = getattr(args, "seed", None)
    output = getattr(args, "output", None)

    if control:
        cmd += ["--control", str(control)]
    if reference:
        cmd += ["--reference-audio", str(reference)]
    if ref_text:
        cmd += ["--ref-text", str(ref_text)]
    if cfg is not None:
        cmd += ["--cfg-scale", str(cfg)]
    if seed is not None:
        cmd += ["--seed", str(seed)]
    if output:
        # 统一转绝对路径：子进程 cwd=项目根，父进程 cwd 可能不同（如 app/），
        # 相对路径会在两处各解析一次，导致重复产物（outputs/ 与 app/outputs/）。
        cmd += ["--output", str(Path(output).expanduser().resolve())]
    # 关键：委托子进程必须 --no-remote（进程内直跑 4.57.3）。
    # 否则 venv CLI 会自动发现常驻服务并把请求 HTTP 弹回本服务——本服务再委托
    # venv……形成递归，表现为信号量互等 / 「系统繁忙（等待超时）」。
    # （2026-11-22 安装版实测踩坑：首跑 90s 废音频 + 后续全部等待超时。）
    cmd.append("--no-remote")
    cmd.append("--json")

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=1800,  # 模型冷加载 + 长文本生成
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"error": "Breeze 隔离环境生成超时（>30 分钟）", "error_code": "TIMEOUT"}
    except OSError as exc:
        return {"error": f"无法启动 Breeze 隔离环境: {exc}", "error_code": "NOT_READY"}

    stdout = (completed.stdout or "").strip()
    # stdout 里 CLI 横幅在前，JSON 在最后一行（以 { 开头）
    payload: dict[str, Any] | None = None
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            break
    if payload is None:
        tail = (completed.stderr or "")[-500:] or stdout[-500:]
        return {
            "error": f"Breeze 隔离环境未返回 JSON（exit={completed.returncode}）: {tail}",
            "error_code": "INTERNAL_ERROR",
        }
    if not payload.get("ok"):
        return {
            "error": payload.get("error", "Breeze 隔离环境生成失败"),
            "error_code": payload.get("error_code", "BUSINESS_ERROR"),
        }
    return payload.get("data") or {}
