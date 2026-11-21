# GOTCHAS — 踩坑记录（按日期追加）

> 格式：触发场景 / 现象 / 正确做法 / 日期
>
> 记录的是**实际验证过的**坑，不是推测。未能在本仓复现的现象请标注环境。

---

## 2026-11-21

### 1. transformers 5.x meta 构造会垃圾化非持久 buffer（本轮最深的坑）

- **触发场景**：任何 vendored 4.x 模型代码经 transformers 5.20.2 的
  `from_pretrained` 加载（Breeze / qwen_tts_min 全家）。
- **现象**：模型能加载、LOAD REPORT 只有少量 MISSING，但推理期出现
  CUDA device-side assert；blocking 模式下首个断言点在 embedding/RoPE。
  实测 `BreezeBackboneModelEmbeddings.audio_tokens_offsets[0]=4.18e12`
  （应为 0），各 rotary 的 `inv_freq` 全为 denormal。
- **根因**：5.20.2 `_finalize_model_loading` →
  `_move_missing_keys_from_meta_to_device` **无条件**用 `torch.empty_like`
  物化所有非持久 buffer（不论模型是否在 meta device 上构造），
  `_initialize_missing_keys` 只对参数跑 `_init_weights`、不重建 buffer。
- **正确做法**：加载后按 vendor 各类的存储字段显式重算——见
  `app/integrated_app/engines/breeze_tts2/tf5_compat.py::fix_non_persistent_buffers`，
  由 `engine.py` 在 `load_runtime` 之后调用。不要再尝试"改 CPU 构造绕过"
  （实测 `empty_like` 覆盖与构造设备无关，无效）。

### 2. transformers 5.x 的 tie 机制对 4.x vendor 模型整体失效

- **触发场景**：vendor 模型用 4.x 三件套：list 形态 `_tied_weights_keys` +
  自定义 `_tie_weights()` 钩子 + `_tie_or_clone_weights()`。
- **现象**：`all_tied_weights_keys == {}`，依赖 tie 共享的权重保持随机
  初始化（checkpoint 只存一侧），产出垃圾 logits → 下一步 embedding 越界。
- **根因**：5.x 契约改为 `{target: source}` dict + 仅当
  `config.tie_word_embeddings` 为真才生成映射（Breeze 顶层 config 无此字段）；
  `_tie_weights` / `_tie_or_clone_weights` 在 5.20.2 已被整体删除。
- **正确做法**：`tf5_compat._install_tie_weights_hook_compat`——补回
  `_tie_or_clone_weights` 并包装 `tie_weights()` 在映射 tie 之后追加钩子调用。
  排查此类问题先用探针比对两侧权重的 `data_ptr()` 是否一致。

### 3. transformers 5.20.2 相对 4.57 的其它已接住断点（均在 tf5_compat）

`no_init_weights` 迁往 `transformers.initialization`（重新导出即可，勿重建语义）；
`ROPE_INIT_FUNCTIONS` 删除 `"default"` 键（按 4.x 公式重建，theta 读取兼容
5.x `config.rope_parameters`）；cache layer `lazy_initialization` 变双参
（value_states 仅取 shape[-1]，可复用 key_states）；`create_causal_mask`
改名 `inputs_embeds` + 删 `cache_position`（kwarg 翻译包装）；
`Cache.__getitem__` 下标被删（读路径补回即可，`update` 的多余 dict 进
`*args` 被 5.x 忽略）。

### 4. mypy 棘轮存在环境性漂移（判据要修正）

- **触发场景**：`scripts/check_mypy_ratchet.py`。
- **现象**：基线 89（configs/mypy_baseline.json），但在干净 `4800c52` 上
  实测跑出 94（+5，全部位于 voxcpm2/g2p/auth 等未改动文件）——棘轮在
  干净树上即失败。该漂移与 mypy / 依赖版本或平台相关，非代码引入。
- **正确做法**：判据应为「改动文件 mypy 净增 0」：跑
  `mypy app/integrated_app --no-error-summary` 对比改动前后全量错误数。
  本轮 3 个新错误（monkeypatch 模式）已用 `# type: ignore[attr-defined]` /
  `[method-assign]` 归零。要恢复棘轮绿灯需 owner 刷新基线。

### 5. pytest 基线既有 2 个恒红（与引擎改动无关）

- **现象**：`test_text_pipeline_integration.py::test_pipeline_chinese_number_normalization`
  与 `test_g2p_processor.py::test_process_returns_text` 在干净 `4800c52`
  上同样失败（G2P/文本管线，疑似平台数据/依赖漂移）。
- **正确做法**：验收以「改动相关测试全过 + 失败集与干净基线一致」为准，
  不要顺手去修无关红测（超任务范围）。

### 6. OpenVoice 构造期会联网拉水印模型

- **触发场景**：`ToneColorConverter.__init__` → `wavmark.load_model()` →
  `hf_hub_download("M4869/WavMark")`（直连 HF 在本机必失败）。
- **现象**：`LocalEntryNotFoundError: ... missing the 'X-Repo-Commit' header`。
- **正确做法**：`HF_ENDPOINT=https://hf-mirror.com` 下载（文件仅 10MB，
  已落入默认缓存 `~/.cache/huggingface/hub`，之后离线可自动回退本地缓存）。
  注意 hf-mirror 已提示「近 30 天出口流量超限」，大文件下载有被限流风险。
  另：此坑解释了为何不要给引擎传 `enable_watermark=False` 来回避——
  引擎 `voice_conversion(message=...)` 设计上带水印，改默认值属行为变更，
  须 owner 裁决。

### 7. `generate design` 回归命令需要 `--control`

- **触发场景**：分派文档验收矩阵里 `generate design --text "回归" --json`。
- **现象**：`ok:false`「音色描述不能为空」（EXIT=1，BUSINESS_ERROR）。
- **正确做法**：补 `--control "<音色描述>"`，如
  `generate design --text "回归测试" --control "warm gentle female voice" --json`。
