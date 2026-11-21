# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""创建 Breeze TTS 2 的隔离运行环境（transformers 4.57.3 + qwen-tts）。

本项目全局锁 transformers 5.x，但 Breeze 上游要求 4.57.3；在 5.x 上跑
Breeze 的产物经人工试听确认是噪音。本脚本创建一个独立 venv 供 Breeze 专用，
不改动项目主环境的依赖锁。

用法::

    python scripts/setup_breeze_venv.py [目标目录]

默认目标目录（按优先级）：

1. 环境变量 ``TTS_BREEZE_VENV``
2. Windows: ``D:/breeze-venv``（大体积落 D 盘）
3. 其它平台: 项目根 ``.venv-breeze``

创建后用 ``--no-deps`` 让 venv 继承主环境已有的 torch/CUDA，只覆盖
transformers 与 qwen-tts，避免重复下载数 GB 的 torch。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def default_venv_dir() -> Path:
    env = os.environ.get("TTS_BREEZE_VENV", "").strip()
    if env:
        return Path(env)
    if sys.platform == "win32":
        # 大体积落 D 盘（用户环境约定）；D 盘不存在时回退到用户目录
        if Path("D:/").is_dir():
            return Path("D:/breeze-venv")
        if os.environ.get("USERPROFILE"):
            return Path(os.environ["USERPROFILE"]) / "breeze-venv"
    return PROJECT_ROOT / ".venv-breeze"


def main() -> int:
    target = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else default_venv_dir()
    target = target.resolve()
    print(f"[Breeze] 隔离环境目录: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    # 用当前解释器建 venv 并继承系统 site-packages（复用主环境的 torch）
    print("[Breeze] 创建 venv（--system-site-packages）...")
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(target)], check=True)

    if sys.platform == "win32":
        python = target / "Scripts" / "python.exe"
    else:
        python = target / "bin" / "python"
    if not python.is_file():
        print(f"[Breeze][ERROR] 解释器未找到: {python}", file=sys.stderr)
        return 1

    # 只覆盖 transformers / qwen-tts，其余依赖从主环境继承
    print("[Breeze] 安装 transformers==4.57.3 + qwen-tts==0.1.1 ...")
    subprocess.run(
        [str(python), "-m", "pip", "install", "transformers==4.57.3", "qwen-tts==0.1.1"],
        check=True,
    )

    print("[Breeze] 验证版本 ...")
    check = subprocess.run(
        [
            str(python),
            "-c",
            "import transformers, torch, qwen_tts; "
            "print('transformers', transformers.__version__, '| torch', torch.__version__, "
            "'| cuda', torch.cuda.is_available())",
        ],
        capture_output=True,
        text=True,
    )
    print(" ".join(line.strip() for line in (check.stdout or "").splitlines() if line.strip()))
    if check.returncode != 0:
        print(f"[Breeze][ERROR] 验证失败: {(check.stderr or '')[-500:]}", file=sys.stderr)
        return 1

    print(f"[Breeze] 完成。后续 Breeze 生成将自动使用: {python}")
    print(f"[Breeze] 如需指定其它位置，设置环境变量 TTS_BREEZE_VENV={target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
