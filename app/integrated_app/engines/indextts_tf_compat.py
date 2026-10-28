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
    """回填 indextts / breeze vendored 代码依赖的 transformers 旧符号（幂等）。

    覆盖 4.53+ 的 5 处断裂与 5.x 的 2 处新断裂（cache_utils 具体类收编、
    no_init_weights 搬家），并挂载 `transformers_452_backfill`——indextts
    vendored 副本依赖的 18 个 4.52.1 符号（AST 全量扫描实证的完整差集）。
    所有注入都以 ``hasattr`` 守卫——在符号尚存的旧版本或未来上游自行适配
    后，本函数自动变为 no-op，无需版本分支。
    """
    # 4.52 符号库先装（vendored 副本顶层导入依赖它，见 transformers_452_backfill 模块文档）
    import torch.nn as nn

    # ---- ① QuantizedCacheConfig（4.53 从 cache_utils 移除）----
    # indextts 仅在 transformers_generation_utils.py:1748 把它当默认值构造，
    # 零参 stub 语义等价（量化缓存路径本项目从不启用）。
    import transformers.cache_utils as _cu

    from integrated_app.engines import transformers_452_backfill  # noqa: F401 - import 即安装

    if not hasattr(_cu, "QuantizedCacheConfig"):

        class QuantizedCacheConfig:  # noqa: N801 - 保持上游符号名
            """4.53+ 已删除；indextts 仅零参构造，不读取任何字段。"""

            def __init__(self, *args, **kwargs) -> None:  # noqa: D401
                pass

        setattr(_cu, "QuantizedCacheConfig", QuantizedCacheConfig)  # noqa: B010 - 动态符号注入
        logger.debug("[indextts_tf_compat] 已注入 cache_utils.QuantizedCacheConfig stub")

    # ---- ①b OffloadedCache（5.x 从 cache_utils 收编移除）----
    # indextts 的 transformers_generation_utils.py:32 顶层导入、:1766 实例化
    # （cache_implementation="offloaded" 路径）。5.x 移除该类（Layer 体系重组，
    # offload 由 accelerate 层处理）。别名指 DynamicCache：offloaded 语义退化为
    # 普通显存缓存（省显存优化失效、功能等价），TTS 短合成不触达该路径。
    if not hasattr(_cu, "OffloadedCache"):
        setattr(_cu, "OffloadedCache", _cu.DynamicCache)  # noqa: B010 - 动态符号注入
        logger.debug("[indextts_tf_compat] 已别名 cache_utils.OffloadedCache -> DynamicCache")

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

        setattr(_cg, "_crop_past_key_values", _crop_past_key_values)  # noqa: B010 - 动态符号注入
        logger.debug("[indextts_tf_compat] 已注入 candidate_generator._crop_past_key_values")

    # ---- ③ NEED_SETUP_CACHE_CLASSES_MAPPING / QUANT_BACKEND_CLASSES_MAPPING ----
    # （4.53 从 generation.configuration_utils 移除，字符串→Cache 类映射）
    # 5.x 起 cache_utils 进一步收编：OffloadedCache / SlidingWindowCache /
    # Quanto/HQQ 具体类整体移除（cache 走 Layer 体系重组），只留 DynamicCache /
    # StaticCache / QuantizedCache。故逐符号 hasattr 解析，缺失时回退
    # DynamicCache——映射的 key 集合才是 vendored 代码的查询面
    # （cache_implementation 配置路径），TTS 推理只用 full/dynamic，
    # 回退值仅保证 dict 构造不炸、对应路径永不触达。
    import transformers.generation.configuration_utils as _gu

    def _cache_cls_or_fallback(name: str):  # noqa: ANN001, ANN202
        cls = getattr(_cu, name, None)
        if cls is None:
            logger.debug("[indextts_tf_compat] cache_utils.%s 在当前版本不存在，回退 DynamicCache", name)
            cls = _cu.DynamicCache
        return cls

    if not hasattr(_gu, "NEED_SETUP_CACHE_CLASSES_MAPPING"):
        setattr(  # noqa: B010 - 动态符号注入
            _gu,
            "NEED_SETUP_CACHE_CLASSES_MAPPING",
            {
                "full": _cache_cls_or_fallback("DynamicCache"),
                "offloaded": _cache_cls_or_fallback("OffloadedCache"),
                "sliding_window": _cache_cls_or_fallback("SlidingWindowCache"),
                "static": _cache_cls_or_fallback("StaticCache"),
            },
        )
        logger.debug("[indextts_tf_compat] 已注入 configuration_utils.NEED_SETUP_CACHE_CLASSES_MAPPING")
    if not hasattr(_gu, "QUANT_BACKEND_CLASSES_MAPPING"):
        setattr(  # noqa: B010 - 动态符号注入
            _gu,
            "QUANT_BACKEND_CLASSES_MAPPING",
            {
                "quanto": _cache_cls_or_fallback("QuantoQuantizedCache"),
                "hqq": _cache_cls_or_fallback("HQQQuantizedCache"),
            },
        )
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

        setattr(_mu, "SequenceSummary", SequenceSummary)  # noqa: B010 - 动态符号注入
        logger.debug("[indextts_tf_compat] 已注入 modeling_utils.SequenceSummary stub")

    # ---- ⑤ GenerationConfig.forced_decoder_ids（4.57 属性清理移除）----
    # indextts 的 vendored 生成代码（transformers_generation_utils.py:1005）读取该
    # 属性以弹弃用警告；4.57 起 GenerationConfig 不再定义之 → 实例读取 AttributeError。
    # 类级默认 None 使读取回到"未设置"分支，与 <=4.52 行为完全等价（赋值路径由
    # 实例 __dict__ 自然遮蔽，不受影响）。
    if not hasattr(_gu.GenerationConfig, "forced_decoder_ids"):
        setattr(_gu.GenerationConfig, "forced_decoder_ids", None)  # noqa: B010 - 动态符号注入
        logger.debug("[indextts_tf_compat] 已注入 GenerationConfig.forced_decoder_ids 类级默认")

    # ---- ⑥ no_init_weights（5.x 从 modeling_utils 搬家到 initialization）----
    # breeze vendored 代码在 from_pretrained 路径里
    # `from transformers.modeling_utils import no_init_weights`（vendor/models/breeze.py），
    # 5.20.2 起该 contextmanager 搬家到 transformers.initialization（签名不变）。
    # 旧位别名指新家，hasattr 守卫幂等；<=4.57 环境自动 no-op。
    if not hasattr(_mu, "no_init_weights"):
        import transformers.initialization as _init

        if hasattr(_init, "no_init_weights"):
            setattr(_mu, "no_init_weights", _init.no_init_weights)  # noqa: B010 - 动态符号注入
            logger.debug("[indextts_tf_compat] 已别名 modeling_utils.no_init_weights -> initialization.no_init_weights")
