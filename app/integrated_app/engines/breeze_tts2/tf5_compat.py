# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Breeze TTS 2 的 transformers 5.x 兼容层。

`vendor/qwen_tts_min` 的 tokenizer 按 4.x 写法使用 `@check_model_inputs()`；
5.20.2 把 `transformers.utils.generic.check_model_inputs` 改成了直接装饰器
（签名只剩 `(func)`，无参工厂语义删除）→ 模块导入期即
`TypeError: check_model_inputs() missing 1 required positional argument: 'func'`。

这里把两种形态都接住：无参调用返回装饰器（4.x），直接传函数则立即装饰（5.x）。
必须在 vendor 模块导入之前生效，故由 `breeze_tts2/__init__.py` 在包加载时调用。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("tts_multimodel")


def apply() -> None:
    """安装双形态 `check_model_inputs`；幂等，旧版本 transformers 下自动 no-op。"""
    from transformers.utils import generic

    current = getattr(generic, "check_model_inputs", None)
    if current is None or getattr(current, "_breeze_dual_mode", False):
        return

    orig = current

    def check_model_inputs(*args, **kwargs):
        # 5.x 形态：@check_model_inputs
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return orig(args[0])

        # 4.x 形态：@check_model_inputs(...) —— 旧参数在 5.x 已内化，此处丢弃
        def decorator(func):
            return orig(func)

        return decorator

    check_model_inputs._breeze_dual_mode = True
    generic.check_model_inputs = check_model_inputs
    logger.debug("[breeze_tf5_compat] check_model_inputs 已切换为双形态兼容")
