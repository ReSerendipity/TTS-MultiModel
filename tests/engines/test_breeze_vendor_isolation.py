# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Breeze vendor 链依赖隔离守卫。

背景：Breeze TTS 2 接入时（2.8.0）刻意**不安装** PyPI ``qwen-tts``——其依赖
闭包含 gradio / onnxruntime / sox / descript-audiotools 等约 200MB 与本引擎
无关的重依赖（主要被 25Hz V1 tokenizer 的 vq 链顶层硬导入拖入）。我们改为
vendor 12Hz V2 裁剪副本（``engines/breeze_tts2/vendor/qwen_tts_min``）。

本守卫防止两类回归：
    1. 有人把 ``from qwen_tts import ...`` 改回直接依赖（requirements 里没有
       qwen-tts，装了包的环境才暴露，守卫必须在干净子进程里抓）；
    2. vendor 副本自身意外引入重依赖（升级 qwen-tts 上游重放裁剪时漏改）。

实现：**独立子进程**里导入完整 vendor 链后断言 ``sys.modules`` 不含任何
禁入重依赖——子进程隔离保证不受 pytest 进程里其他用例已导入的模块污染。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_APP_DIR = str(Path(__file__).resolve().parents[2] / "app")

_PROBE = r"""
import sys

sys.path.insert(0, sys.argv[1])

from integrated_app.engines.breeze_tts2.vendor.breeze_infer.runtime import load_runtime  # noqa: F401,E402
from integrated_app.engines.breeze_tts2.vendor.breeze_infer.templates import prepare_inputs  # noqa: F401,E402
from integrated_app.engines.breeze_tts2.vendor.models.fast_streaming import (  # noqa: F401,E402
    FastBreezeStreamingRuntime,
)
from integrated_app.engines.breeze_tts2.vendor.models.breeze import (  # noqa: F401,E402
    BreezeForConditionalGeneration,
)
from integrated_app.engines.breeze_tts2.vendor.qwen_tts_min import (  # noqa: F401,E402
    Qwen3TTSTokenizer,
)

FORBIDDEN = ("gradio", "onnxruntime", "sox", "descript_audiotools", "argbind")
leaked = sorted(m for m in FORBIDDEN if m in sys.modules)
if leaked:
    print("LEAKED:" + ",".join(leaked))
    sys.exit(1)
print("CLEAN")
"""

# 禁入模块 → 它们属于哪条与本引擎无关的依赖链（报错时给行动指引）
_FORBIDDEN_HINTS = {
    "gradio": "qwen-tts 的 demo/UI 链（12Hz V2 tokenizer 不需要）",
    "onnxruntime": "qwen-tts 25Hz V1 的 vq 链（Breeze 仅用 12Hz V2）",
    "sox": "qwen-tts 25Hz V1 的 vq 链（Breeze 仅用 12Hz V2）",
    "descript_audiotools": "indextts DAC 声码器链（与 Breeze 无关）",
    "argbind": "descript-audiotools 的配置链（与 Breeze 无关）",
}


def test_breeze_vendor_chain_isolation() -> None:
    """vendor 全链导入后，禁入重依赖不得出现在 sys.modules。"""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, _APP_DIR],
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "TRANSFORMERS_OFFLINE": "1", "HF_HUB_OFFLINE": "1"},
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    if proc.returncode == 0:
        assert "CLEAN" in proc.stdout, f"探针输出异常: {proc.stdout!r} / {proc.stderr[-300:]!r}"
        return

    detail = (proc.stdout + proc.stderr)[-500:]
    hints = "; ".join(f"{m}（{_FORBIDDEN_HINTS.get(m, '未知来源')}）" for m in _FORBIDDEN_HINTS if m in detail)
    raise AssertionError(
        f"Breeze vendor 链引入了禁入重依赖（exit={proc.returncode}）。"
        f"{('疑似来源：' + hints) if hints else ''} "
        f"请检查 vendor/qwen_tts_min 裁剪副本与 breeze_infer 引用是否被改回直接依赖。尾部输出：{detail}"
    )
