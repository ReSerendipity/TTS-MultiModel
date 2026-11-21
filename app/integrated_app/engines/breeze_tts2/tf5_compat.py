# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Breeze TTS 2 的 transformers 5.x 兼容层。

两处 4.x → 5.x 断点，均在 vendor 模块导入前生效（由 `breeze_tts2/__init__.py`
在包加载时调用 `apply()`）：

1. `vendor/qwen_tts_min` 的 tokenizer 按 4.x 写法使用 `@check_model_inputs()`；
   5.20.2 把 `transformers.utils.generic.check_model_inputs` 改成了直接装饰器
   （签名只剩 `(func)`，无参工厂语义删除）→ 模块导入期即
   `TypeError: check_model_inputs() missing 1 required positional argument: 'func'`。
   这里把两种形态都接住：无参调用返回装饰器（4.x），直接传函数则立即装饰（5.x）。

2. `vendor/models/breeze.py` 按 4.x 写法
   `from transformers.modeling_utils import no_init_weights`；5.x 把该符号挪到了
   新模块 `transformers.initialization`（原实现仍在），旧路径导入即 ImportError。
   这里把 5.x 原生实现重新导出到旧路径，不做语义重建。

3. `vendor/qwen_tts_min` 的音频 tokenizer 按 4.x 写法
   `ROPE_INIT_FUNCTIONS[self.rope_type]`（无 `rope_scaling` 时 `rope_type="default"`）；
   5.20.2 的 `ROPE_INIT_FUNCTIONS` 只剩 linear/dynamic/yarn/longrope/llama3，
   `"default"` 键被删除 → 模型构建期即 `KeyError: 'default'`。
   这里按 4.x 语义重建标准 RoPE 逆频率函数并补回该键。

4. `vendor/models/cudagraph` 按 4.x 写法调用
   `layer.lazy_initialization(dummy_k)`（单参）；5.20.2 的 cache layer 签名是
   `(key_states, value_states)` 双参 → 推理期即 `TypeError`。
   这里给三个 cache layer 基类包一层缺省适配：value_states 未传时复用
   key_states（5.20.2 实现只取其 `shape[-1]`，与 vendor 传入同一 dummy 的
   意图一致）。

5. vendor 多处按 4.x 写法调用 `create_causal_mask(input_embeds=…,
   cache_position=…)`；5.20.2 已改名 `inputs_embeds` 并删除 `cache_position`
   （带 cache 时 mask 尺寸改由 `past_key_values` 推导）→ 推理期即 `TypeError`。
   这里包装新函数做 kwarg 翻译：改名透传，`cache_position` 缺 `position_ids`
   时升维映射过去（5.x 仅在无 cache 时消费该参，vendor 各调用点均带 cache）。

6. `vendor/models/cudagraph/backbone_graph.py` 按 4.x 写法
   `k, v = past_key_values[li]` 取 prefill 后的 KV；5.20.2 的 `Cache` 已删除
   `__getitem__`（per-layer KV 改放 `cache.layers[i].keys/.values`）→
   推理期即 `TypeError: object is not subscriptable`。
   这里给 `Cache` 基类补回下标读取（`(layers[i].keys, layers[i].values)`），
   写路径（`update`）5.x 签名兼容且忽略多余 cache_kwargs，无需处理。

7. vendor 的代码书权重 tie（`_tied_weights_keys` 为 4.x list 形态 + 模型自定义
   `_tie_weights()` 钩子）在 5.x 整体失效：5.20.2 把 `_tied_weights_keys` 改为
   `{target: source}` dict 契约，且仅当 `config.tie_word_embeddings` 为真时才
   生成映射（Breeze 顶层 config 无该字段 → `all_tied_weights_keys = {}`）；
   `_tie_weights` / `_tie_or_clone_weights` 钩子在 5.x 被整体删除。
   后果：`backbone_model.embed_tokens.embed_audio_tokens.weight`（checkpoint
   中省略、依赖 tie 共享 depth decoder 的 [32816, 2048] 表）保持随机初始化 →
   prefill 产出垃圾 logits → 下一步 embedding 索引越界（CUDA device assert）。
   这里补回 4.x 语义：`_tie_or_clone_weights`（模块级 weight 重绑）并在
   `tie_weights()` 末尾追加调用模型自定义 `_tie_weights()` 钩子。

8. 5.20.2 加载模型时，`_finalize_model_loading` 会**无条件**用
   `torch.empty_like` 物化所有非持久 buffer（不论模型是否在 meta device 上
   构造）——4.x 时代在 `__init__` 里真实计算的 buffer（rotary 的 `inv_freq`、
   `audio_tokens_offsets`、`embed_scale`）加载后全是垃圾值；vendor 的
   `_init_weights` 只管参数、不重建 buffer → 推理期 embedding 索引越界 /
   RoPE 错乱（实测 `audio_tokens_offsets[0]`=4.18e12，应为 0）。
   修复：`fix_non_persistent_buffers()`（本模块底部）在 `load_runtime` 之后
   按各类存储字段确定性重算，由 `engine.py` 在加载后显式调用。
"""

from __future__ import annotations

import logging
from functools import wraps

logger = logging.getLogger("tts_multimodel")


def apply() -> None:
    """安装上述七处兼容补丁；幂等，无缺失符号时自动 no-op。

    **仅作用于 transformers 5.x**：Breeze 上游要求 4.57.3，该版本原生代码正常，
    任何补丁都可能改变权重绑定/采样语义（实测 4.x 下强装 5.x 的
    ``_install_no_init_weights`` 会直接 ``ModuleNotFoundError``）。
    在 4.x 下整体 no-op，保证官方 4.57.3 路径与上游零偏差。
    """
    import transformers

    major = int(transformers.__version__.split(".", 1)[0])
    if major < 5:
        logger.debug(
            "[breeze_tf5_compat] transformers %s 无需 5.x 兼容补丁，跳过",
            transformers.__version__,
        )
        return

    _install_check_model_inputs()
    _install_no_init_weights()
    _install_default_rope_fn()
    _install_cache_lazy_init_compat()
    _install_create_causal_mask_compat()
    _install_cache_getitem_compat()
    _install_tie_weights_hook_compat()


def _install_check_model_inputs() -> None:
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


def _install_no_init_weights() -> None:
    """把 5.x 移位后的 `no_init_weights` 重新导出到 `modeling_utils` 旧路径。

    5.20.2 删除了 `transformers.modeling_utils.no_init_weights`（符号整体迁往
    `transformers.initialization`，实现未变：临时 patch torch init 函数与
    `PreTrainedModel.init_weights`，finally 恢复），而 vendor 的
    `vendor/models/breeze.py` 仍按 4.x 写法从旧路径导入。

    这里不做语义重建（如 swap `torch.nn.init`），直接把 5.x 原生实现重新导出，
    与 5.x 的 meta device / from_config 加载流程保持零偏差。
    """
    import transformers.initialization
    import transformers.modeling_utils as mu

    if hasattr(mu, "no_init_weights"):
        return  # 4.x 原生存在，无需补
    native = getattr(transformers.initialization, "no_init_weights", None)
    if native is None:
        logger.debug("[breeze_tf5_compat] initialization 模块无 no_init_weights，跳过")
        return
    mu.no_init_weights = native  # type: ignore[attr-defined]
    logger.debug("[breeze_tf5_compat] no_init_weights 已从 initialization 重新导出")


def _install_default_rope_fn() -> None:
    """补回 transformers 5.x 从 `ROPE_INIT_FUNCTIONS` 删除的 `"default"` 项。

    5.20.2 的 `ROPE_INIT_FUNCTIONS` 只保留 linear/dynamic/yarn/longrope/llama3
    五种 rope_type（default 场景改由新配置 mixin 内部处理），而 vendor 的
    `qwen_tts_min` 音频 tokenizer 仍是 4.x 写法：无 `rope_scaling` 时取
    `rope_type="default"`，再从该字典查函数 → 构建期 `KeyError: 'default'`。

    这里按 4.x `_compute_default_rope_parameters` 语义重建（标准 RoPE 逆频率，
    `attention_scaling=1.0`）并原地补键；theta 读取兼容 5.x 新语法
    （`config.rope_parameters["rope_theta"]`，5.x Qwen3Config 已无
    `rope_theta` 属性）与 4.x 属性兜底。vendor 以引用方式导入该字典，
    原地补键与导入顺序无关。
    """
    import torch
    from transformers.modeling_rope_utils import ROPE_INIT_FUNCTIONS

    if "default" in ROPE_INIT_FUNCTIONS:
        return  # 原生存在（4.x 或未来版本恢复），无需补

    def _compute_default_rope_parameters(config, device=None, **rope_kwargs):
        # 5.x 起 rope 参数收进 config.rope_parameters（需先 standardize），
        # 4.x 及 vendor 自有 config 仍是 config.rope_theta 属性——两条路都兜住。
        try:
            config.standardize_rope_params()
        except Exception:  # noqa: BLE001 - 旧式 config 不支持时走属性兜底
            logger.debug("[breeze_tf5_compat] standardize_rope_params 失败，走属性兜底")

        theta = None
        rope_parameters = getattr(config, "rope_parameters", None)
        if isinstance(rope_parameters, dict):
            theta = rope_parameters.get("rope_theta")
        if theta is None:
            rope_scaling = getattr(config, "rope_scaling", None)
            if isinstance(rope_scaling, dict):
                theta = rope_scaling.get("rope_theta")
        if theta is None:
            theta = getattr(config, "rope_theta", None)
        if theta is None:
            raise ValueError(f"config {type(config).__name__} 无可用的 rope_theta")

        partial_rotary_factor = getattr(config, "partial_rotary_factor", 1.0)
        head_dim = getattr(config, "head_dim", None) or (config.hidden_size // config.num_attention_heads)
        rotary_dim = int(head_dim * partial_rotary_factor)
        inv_freq = 1.0 / (theta ** (torch.arange(0, rotary_dim, 2, dtype=torch.int64).float().to(device) / rotary_dim))
        return inv_freq, 1.0

    ROPE_INIT_FUNCTIONS["default"] = _compute_default_rope_parameters
    logger.debug("[breeze_tf5_compat] ROPE_INIT_FUNCTIONS['default'] 已补回（5.x shim）")


def _install_cache_lazy_init_compat() -> None:
    """让 cache layer 的 `lazy_initialization` 兼容 4.x 单参调用。

    5.20.2 签名为 `lazy_initialization(key_states, value_states)`，而 vendor 的
    cudagraph 预热代码只传 `dummy_k`。5.20.2 的实现里 `value_states` 仅取
    `shape[-1]`（v_head_dim），未传时复用 key_states 与 vendor 意图等价
    （kv 同维度）。仅改写三个基类，子类经继承自动生效； Transformers 原生
    双参调用不受影响（包装层透传）。
    """
    import transformers.cache_utils as cu

    def _make_single_arg_wrapper(orig):
        # 工厂固化 orig，避免循环变量晚绑定
        @wraps(orig)
        def lazy_initialization(self, key_states, value_states=None, **kwargs):
            if value_states is None:
                value_states = key_states
            return orig(self, key_states, value_states, **kwargs)

        lazy_initialization._breeze_kv_default = True
        return lazy_initialization

    for name in ("DynamicLayer", "DynamicSlidingWindowLayer", "StaticLayer"):
        cls = getattr(cu, name, None)
        orig = getattr(cls, "lazy_initialization", None) if cls is not None else None
        if cls is None or orig is None or getattr(orig, "_breeze_kv_default", False):
            continue
        cls.lazy_initialization = _make_single_arg_wrapper(orig)
        logger.debug("[breeze_tf5_compat] %s.lazy_initialization 已兼容单参调用", name)


def _install_create_causal_mask_compat() -> None:
    """让 `create_causal_mask` / `create_sliding_window_causal_mask` 接受 4.x kwarg。

    4.x 签名 `(config, input_embeds, attention_mask, cache_position, …)` 在
    5.20.2 中：参数改名 `inputs_embeds`、`cache_position` 被删除（带 cache 的
    调用点 mask 尺寸改由 `past_key_values` 推导，`position_ids` 仅无 cache 时
    参与 packed-sequence 检测）。翻译规则：

    - `input_embeds` → `inputs_embeds`（纯改名透传）；
    - `cache_position`：总是弹出；若调用点未给 `position_ids`，则升维成
      `(batch, q_len)` 后映射过去，与 5.x 形状约定对齐。

    仅翻译 kwarg（vendor 全部调用点均为 kwarg 调用），位置参数透传不改。
    """
    import transformers.masking_utils as mku

    def _translate_kwargs(kwargs: dict) -> None:
        if "input_embeds" in kwargs:
            kwargs.setdefault("inputs_embeds", kwargs.pop("input_embeds"))
        cache_position = kwargs.pop("cache_position", None)
        if cache_position is not None and "position_ids" not in kwargs:
            pos = cache_position
            embeds = kwargs.get("inputs_embeds")
            if pos.ndim == 1 and embeds is not None:
                pos = pos.unsqueeze(0).expand(embeds.shape[0], -1)
            kwargs["position_ids"] = pos

    def _wrap_mask_fn(orig):
        # 工厂固化 orig，避免循环变量晚绑定
        @wraps(orig)
        def mask_fn(*args, **kwargs):
            _translate_kwargs(kwargs)
            return orig(*args, **kwargs)

        mask_fn._breeze_kw_translate = True
        return mask_fn

    for fn_name in ("create_causal_mask", "create_sliding_window_causal_mask"):
        orig = getattr(mku, fn_name, None)
        if orig is None or getattr(orig, "_breeze_kw_translate", False):
            continue
        setattr(mku, fn_name, _wrap_mask_fn(orig))
        logger.debug("[breeze_tf5_compat] %s 已兼容 4.x kwarg", fn_name)


def _install_cache_getitem_compat() -> None:
    """给 5.x `Cache` 基类补回 4.x 的下标读取 `cache[i] -> (k, v)`。

    4.x `DynamicCache.__getitem__(i)` 返回第 i 层累计的 `(key_cache, value_cache)`；
    5.20.2 删除了该接口（per-layer KV 移至 `cache.layers[i].keys/.values`）。
    仅补读路径；`update(k, v, layer_idx, cache_kwargs)` 第 4 参进 `*args`
    被 5.x 层实现忽略，语义等价（cache 位置改由内部 `cumulative_length` 推导）。
    """
    import transformers.cache_utils as cu

    Cache = getattr(cu, "Cache", None)
    if Cache is None or hasattr(Cache, "__getitem__"):
        return  # 4.x 原生存在，无需补

    def __getitem__(self, idx):
        layer = self.layers[idx]
        return layer.keys, layer.values

    Cache.__getitem__ = __getitem__
    logger.debug("[breeze_tf5_compat] Cache.__getitem__ 已补回（5.x shim）")


def _install_tie_weights_hook_compat() -> None:
    """恢复 4.x 的模型自定义 `_tie_weights()` 钩子调用链。

    5.20.2 的权重 tie 收敛为「`config.tie_word_embeddings` 为真 + `_tied_weights_keys`
    为 `{target: source}` dict」的映射机制；4.x 的两个扩展点均被删除：

    - `_tie_weights()`（模型自定义钩子）—— 5.x 不再调用，整个 transformers
      代码库已无任何定义或调用；
    - `_tie_or_clone_weights(output_module, input_module)`（模块级 weight 重绑）。

    Breeze 的 `BreezeForConditionalGeneration` 正是 4.x 写法：list 形态
    `_tied_weights_keys` + 钩子把 `embed_audio_tokens.weight` 重绑到 depth
    decoder 的共享表（checkpoint 只存后者）。5.x 下映射为空 → 该表保持随机
    初始化 → 推理时 embedding 索引越界。

    这里：① 给 `PreTrainedModel` 补回 `_tie_or_clone_weights`（仅缺省时，
    语义与 4.57 一致）；② 包装 `tie_weights()`，在 5.x 映射 tie 之后追加
    调用模型自定义钩子。5.x 原生模型无钩子定义 → 自动 no-op，零影响。
    """
    import torch.nn as nn
    import transformers.modeling_utils as mu

    PTM = mu.PreTrainedModel

    if not hasattr(PTM, "_tie_or_clone_weights"):

        def _tie_or_clone_weights(self, output_embeddings, input_embeddings):
            # 4.57 语义：非 torchscript 时直接共享 storage（引用赋值）；
            # 输出侧若有 bias，按新权重行数右侧补零。
            if getattr(self.config, "torchscript", False):
                output_embeddings.weight = nn.Parameter(input_embeddings.weight.clone())
            else:
                output_embeddings.weight = input_embeddings.weight
            bias = getattr(output_embeddings, "bias", None)
            if bias is not None:
                output_embeddings.bias.data = nn.functional.pad(
                    output_embeddings.bias.data,
                    (
                        0,
                        output_embeddings.weight.shape[0] - output_embeddings.bias.data.shape[0],
                    ),
                    "constant",
                    0,
                )

        PTM._tie_or_clone_weights = _tie_or_clone_weights  # type: ignore[attr-defined]
        logger.debug("[breeze_tf5_compat] PreTrainedModel._tie_or_clone_weights 已补回")

    orig_tie_weights = PTM.tie_weights
    if getattr(orig_tie_weights, "_breeze_hook_call", False):
        return

    @wraps(orig_tie_weights)
    def tie_weights(self, *args, **kwargs):
        result = orig_tie_weights(self, *args, **kwargs)
        hook = getattr(self, "_tie_weights", None)
        if callable(hook) and not getattr(hook, "_breeze_hook_disabled", False):
            hook()
        return result

    tie_weights._breeze_hook_call = True  # type: ignore[attr-defined]
    PTM.tie_weights = tie_weights  # type: ignore[method-assign]
    logger.debug("[breeze_tf5_compat] tie_weights 已追加 _tie_weights 钩子调用")


def fix_non_persistent_buffers(*roots) -> None:
    """from_pretrained 加载后重算 vendor 模型的非持久 buffer（5.x 必调）。

    5.20.2 在 `_finalize_model_loading` → `_move_missing_keys_from_meta_to_device`
    中**无条件**用 `torch.empty_like` 物化全部非持久 buffer（不论模型是否在
    meta device 上构造），而 `_initialize_missing_keys` 只对参数调
    `_init_weights`、不重建 buffer → 4.x 时代在 `__init__` 里真实计算的
    buffer 加载后全是垃圾值（实测 `audio_tokens_offsets[0]=4.18e12`、
    `inv_freq` 全为 denormal）。

    这里按 vendor 各类的存储字段做确定性重算（均与 `__init__` 逻辑一致）：
    - 存 `rope_init_fn` + `config` 的 rotary（breeze.py / qwen3_tts_tokenizer_v2.py
      同款）：重跑 `rope_init_fn` 重置 inv_freq / original_inv_freq / attention_scaling；
    - 存 `layer_types` 且带 `{lt}_inv_freq` buffer 的（t5gemma2_compat）：
      逐层调其 `_compute_inv_freq`；
    - 存 `scalar_embed_scale` 的（t5gemma2_compat scaled embedding）：重置标量；
    - 存 `audio_tokens_offsets` 的（BreezeBackboneModelEmbeddings）：由
      embedding 行数 // offsets 长度推出 vocab_size 重算。

    须在 `load_runtime` 之后、任何推理之前调用。
    """
    for root in roots:
        if root is None:
            continue
        # Qwen3TTSTokenizer 包装器不是 nn.Module，真正的模型树在其 .model
        if not hasattr(root, "modules") and hasattr(root, "model"):
            root = root.model
        if not hasattr(root, "modules"):
            continue
        for module in root.modules():
            _fix_module_buffers(module)


def _fix_module_buffers(module) -> None:
    """按 duck-typing 匹配 vendor buffer 模式并重算（无匹配则 no-op）。"""
    import torch

    with torch.no_grad():
        # 1) 标准 vendor rotary：存 rope_init_fn / config / inv_freq
        rope_init_fn = getattr(module, "rope_init_fn", None)
        if callable(rope_init_fn) and hasattr(module, "config") and hasattr(module, "inv_freq"):
            device = module.inv_freq.device
            inv_freq, attention_scaling = rope_init_fn(module.config, device)
            module.attention_scaling = attention_scaling
            module.inv_freq = inv_freq.to(device)
            if hasattr(module, "original_inv_freq"):
                module.original_inv_freq = module.inv_freq
            return

        # 2) t5gemma2_compat 多 rope 层：{layer_type}_inv_freq
        layer_types = getattr(module, "layer_types", None)
        compute_inv_freq = getattr(module, "_compute_inv_freq", None)
        if layer_types is not None and callable(compute_inv_freq):
            for lt in layer_types:
                buf = getattr(module, f"{lt}_inv_freq", None)
                if buf is None:
                    continue
                inv_freq, attention_scaling = compute_inv_freq(module.config, lt, device=buf.device)
                setattr(module, f"{lt}_inv_freq", inv_freq.to(buf.device))
                setattr(module, f"{lt}_attention_scaling", attention_scaling)
            return

        # 3) t5gemma2_compat scaled embedding：embed_scale 标量
        scalar_embed_scale = getattr(module, "scalar_embed_scale", None)
        if scalar_embed_scale is not None and hasattr(module, "embed_scale"):
            module.embed_scale = torch.tensor(scalar_embed_scale, device=module.embed_scale.device)
            return

        # 4) BreezeBackboneModelEmbeddings：audio_tokens_offsets
        offsets = getattr(module, "audio_tokens_offsets", None)
        embed_audio_tokens = getattr(module, "embed_audio_tokens", None)
        if offsets is not None and embed_audio_tokens is not None:
            num_codebooks = offsets.numel()
            rows = embed_audio_tokens.weight.shape[0]
            if num_codebooks > 0 and rows % num_codebooks == 0:
                vocab_size = rows // num_codebooks
                module.audio_tokens_offsets = torch.arange(num_codebooks, device=offsets.device) * vocab_size
