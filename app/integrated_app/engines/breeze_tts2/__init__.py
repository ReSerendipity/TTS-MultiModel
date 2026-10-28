# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Breeze TTS 2 引擎包。

- engine.py: BreezeTTSEngine —— BaseControllableTTSEngine 实现
- vendor/: breezeblue-ai/breeze-tts 官方推理代码固化副本（Apache-2.0），见 vendor/README.md
- tf5_compat.py: transformers 5.x 兼容层（须先于 vendor 导入生效）
"""

from . import tf5_compat

tf5_compat.apply()
