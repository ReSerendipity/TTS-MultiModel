# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""indextts 的 transformers >=4.53 兼容 shim。

背景：上游 indextts（github.com/index-tts/index-tts，按 transformers 4.52.1 开发）
在 ``indextts/gpt/transformers_generation_utils.py`` 等模块顶层导入了 4 个在
transformers 4.53+ 被移除/重组的符号，导致 ``indextts.infer_v2_5`` / ``infer_v2``
在 4.57.x 下 ImportError（pyproject.toml 依赖注释曾据此把上界钉在 <4.53）。

Breeze TTS 2 引擎（breeze_tts2）需要 transformers>=4.57.3，因此本模块在导入
indextts 之前把缺失符号按新 API 等价重建/回填，使两个引擎家族共存于同一进程。

已验证（2026-10-25，.venv transformers 4.57.3）：
    apply() 后 ``import indextts.infer_v2_5`` 与 ``import indextts.infer_v2``
    均可完整导入；IndexTTS 真机推理回归见 tests/ 与 CHANGELOG 对应条目。

用法（幂等，重复调用无副作用）::

    from integrated_app.engines.indextts_tf_compat import apply
    apply()                      # 在 import indextts 之前调用一次
    import indextts.infer_v2_5   # OK
"""

from __future__ import annotations

import logging

logger = logging.getLogger("tts_multimodel")


def apply() -> None:
    """回填 indextts 依赖的 4 个 transformers 旧符号（幂等）。

    所有注入都以 ``hasattr`` 守卫——在 transformers <=4.52（符号尚存）或未来
    上游 indextts 自行适配后，本函数自动变为 no-op，无需版本分支。
    """
    import torch.nn as nn

    # ---- ① QuantizedCacheConfig（4.53 从 cache_utils 移除）----
    # indextts 仅在 transformers_generation_utils.py:1748 把它当默认值构造，
    # 零参 stub 语义等价（量化缓存路径本项目从不启用）。
    import transformers.cache_utils as _cu

    if not hasattr(_cu, "QuantizedCacheConfig"):

        class QuantizedCacheConfig:  # noqa: N801 - 保持上游符号名
            """4.53+ 已删除；indextts 仅零参构造，不读取任何字段。"""

            def __init__(self, *args, **kwargs) -> None:  # noqa: D401
                pass

        _cu.QuantizedCacheConfig = QuantizedCacheConfig
        logger.debug("[indextts_tf_compat] 已注入 cache_utils.QuantizedCacheConfig stub")

    # ---- ② _crop_past_key_values（4.53 从 candidate_generator 移除）----
    # indextts 仅在 speculative decoding 裁剪 KV 缓存处调用（L4312）；
    # 4.5x 中 Cache 对象原生提供等价的 .crop()。
    import transformers.generation.candidate_generator as _cg

    if not hasattr(_cg, "_crop_past_key_values"):

        def _crop_past_key_values(model, past_key_values, new_cache_size):  # noqa: ANN001
            if past_key_values is None:
                return None
            if hasattr(past_key_values, "crop"):
                return past_key_values.crop(new_cache_size)
            # legacy 元组路径（仅 <=4.52 的 transformers 会走到，保留以兼容回退安装）
            if isinstance(past_key_values, (tuple, list)):
                return tuple((k[..., :new_cache_size, :], v[..., :new_cache_size, :]) for k, v in past_key_values)
            return past_key_values

        _cg._crop_past_key_values = _crop_past_key_values
        logger.debug("[indextts_tf_compat] 已注入 candidate_generator._crop_past_key_values")

    # ---- ③ NEED_SETUP_CACHE_CLASSES_MAPPING / QUANT_BACKEND_CLASSES_MAPPING ----
    # （4.53 从 generation.configuration_utils 移除，字符串→Cache 类映射）
    import transformers.generation.configuration_utils as _gu

    if not hasattr(_gu, "NEED_SETUP_CACHE_CLASSES_MAPPING"):
        _gu.NEED_SETUP_CACHE_CLASSES_MAPPING = {
            "full": _cu.DynamicCache,
            "offloaded": _cu.OffloadedCache,
            "sliding_window": _cu.SlidingWindowCache,
            "static": _cu.StaticCache,
        }
        logger.debug("[indextts_tf_compat] 已注入 configuration_utils.NEED_SETUP_CACHE_CLASSES_MAPPING")
    if not hasattr(_gu, "QUANT_BACKEND_CLASSES_MAPPING"):
        _gu.QUANT_BACKEND_CLASSES_MAPPING = {
            "quanto": _cu.QuantoQuantizedCache,
            "hqq": _cu.HQQQuantizedCache,
        }
        logger.debug("[indextts_tf_compat] 已注入 configuration_utils.QUANT_BACKEND_CLASSES_MAPPING")

    # ---- ④ SequenceSummary（4.53 从 modeling_utils 整体删除）----
    # indextts/transformers_gpt2.py:1357 仅用它构造 multiple_choice_head
    # （GPT-2 多选分类头，TTS 推理路径永不触达），恒等 stub 即可。
    import transformers.modeling_utils as _mu

    if not hasattr(_mu, "SequenceSummary"):

        class SequenceSummary(nn.Module):  # noqa: N801 - 保持上游符号名
            """4.53+ 已删除；indextts 仅用于 multiple_choice_head（TTS 不触达）。"""

            def __init__(self, config):  # noqa: ANN001, D401
                super().__init__()

            def forward(self, hidden_states, *args, **kwargs):  # noqa: ANN001, ANN003
                return hidden_states

        _mu.SequenceSummary = SequenceSummary
        logger.debug("[indextts_tf_compat] 已注入 modeling_utils.SequenceSummary stub")
