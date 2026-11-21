# ADR-0005: CLI 全功能扩展（AI-agent 优先）

- 状态： Implemented（P0–P4 全部完成，2026-11-21）
- 日期： 2026-11-21
- 决策者： owner（本轮范围选择：架构重构先行，后续按轮扩展）

## 背景与目标

当前 `app/integrated_app/cli.py`（1130 行）只提供 `design` / `clone` / `batch` 三个 VoxCPM2 子命令，且**直调 vendor `VoxCPM`**，绕过项目统一服务层（`service_layer.py`）、registry、历史库、内容安全与后处理。副作用：`--engine indextts2` 是假开关（只打印日志，实际仍走 VoxCPM）。

目标：让 CLI 提供的功能**覆盖项目全部能力**，使 AI agent 能通过 CLI 完成过去必须在 Web UI 上做的所有操作。整体设计决策：

1. **实现路径 = 双模式混合**：CLI 直接调 Python 函数覆盖绝大多数功能；同时提供 `cli serve` 子命令拉起内置 FastAPI，覆盖 `/v1` OpenAI 兼容、SSE 等必须走 HTTP 的面。
2. **交互形态 = 结构化 JSON 输出**：全局 `--json` 开关，输出机器可读结果（audio_path/duration/engine/params/error_code），退出码语义化；默认仍为人类可读文本，不破坏现有体验。
3. **本轮范围 = 架构重构先行**：先把 design/clone/batch 从直调 vendor 迁到统一服务层，搭建可扩展命令框架与 JSON 输出层，为后续功能扩展打基础。**本轮不实现全部功能**。

## 功能盘点结论（已实测）

项目当前能力面（源码盘点，约 100 个 API 端点）：

| 类别 | 数量/内容 |
|---|---|
| 引擎 | 7 个：voxcpm2 / indextts2 / indextts20 / voicebox / step-audio-editx / edge-tts / breeze-tts2 |
| 核心合成 | design / clone / ultimate / prompt 续写 / script / streaming / post-process，跨 5+ 引擎 |
| 音色管理 | persona save/list/table/delete、授权状态、说话人样本 |
| 剧本批处理 | 多角色映射、SRT/ZIP 导出、batch checkpoint 续跑 |
| 模型管理 | load/unload/switch/preload/status、LoRA load/unload/toggle/list |
| 下载管理 | runtime + 模型 catalog/start/progress/cancel/uninstall |
| 历史记录 | 分页查询/隐藏/显示/清空/回收站/导出 ZIP/同步/反馈/统计 |
| 训练 | LoRA 微调 start/stop/log |
| 系统运维 | GPU 状态/清理、健康/队列/SLO/容量、日志、设置、metrics、shutdown |
| OpenAI 兼容 | `/v1/audio/speech`、`/v1/audio/speech/batch`、`/v1/models` |

CLI 现状：仅 3 个子命令、单引擎、无历史/授权/后处理/缓存。

## 可行性实证（2026-11-21 本机实测）

在**无 FastAPI server 的纯 CLI 环境**下：

1. `get_model_service().load_model("voxcpm2")` → `LoadResult(success=True, engine=voxcpm2, load_time≈36s)`，完整加载 VoxCPM2 + SenseVoiceSmall 并预热，EXIT=0。
2. `get_generation_service().generate_voice_design(...)` → 生成成功，`GenerationResult.to_dict()` 输出：
   ```json
   {"audio_path": "outputs/voxcpm_design_*.wav", "message": "生成成功！音频时长 4.0 秒...",
    "duration": 4.0, "engine": "voxcpm2", "params": {...}}
   ```
   且自动执行内容安全检测。

结论：`service_layer` 是干净的、可直接复用的业务层，**重构可行**。

> 注意 `service_layer.py:229-237` 的既有说明：HTTP 路由仍走 `routes/generate/utils.py` 管线，service_layer 尚未吸收信号量并发、OOM 降级、后处理、幂等缓存、HTML 渲染等横切关注点。**CLI 场景是单进程单任务，这些大多非必需**；后处理/缓存可在 CLI 层按需补。这是本 ADR 选择 service_layer 作为 CLI 基座的核心理由。

## 决策：目标命令树（分阶段实现）

```
tts-multimodel [全局选项: --json --config --quiet]
├── generate                      # 语音生成（统一入口，--engine 真正生效）
│   ├── design  --text --control [-o]
│   ├── clone   --text --reference-audio [-o]
│   ├── ultimate --text --reference-audio --ref-text [-o]
│   ├── prompt-continue --text --prompt-audio --prompt-text [-o]
│   ├── script  --input --output-dir [--persona-map]
│   ├── emotion --text --reference-audio --emotion-text [--engine indextts2] [-o]
│   ├── convert --source --target   # voicebox 音色转换
│   ├── edit    --source --edit-type --edit-info  # step-audio-editx
│   └── edge    --text --lang [-o]  # edge-tts 在线兜底
├── batch       --input --output-dir [--resume] [--engine]
├── postprocess --audio [--tempo] [--lufs] [--enhance] [-o]
├── persona     list|save|show|delete
├── model       status|load|unload|switch|preload
├── lora        load|unload|toggle|state|list
├── download    status|catalog|start|cancel|uninstall
├── history     list|show|hide|clear|recycle|export|sync|stats
├── train       start|stop|log
├── system      health|gpu|logs|settings|metrics
├── engines     list|info
└── serve       [--host --port]      # 拉起内置 FastAPI，覆盖 /v1 + SSE
```

兼容承诺：`design` / `clone` / `batch` 三个顶层命令保留为 `generate xxx` 的别名，旧参数与退出码不变（保护桌面 shim、README 示例、已有脚本）。

## 架构设计（双模式混合）

```
cli.py                     # 入口：全局选项解析 + 子命令分发 + 输出层
├── cli_core.py (新)       # 命令注册表：命令 → handler 映射，元数据（名称/参数/能力标签）
├── cli_output.py (新)     # 输出层：HumanRenderer / JsonRenderer，统一 Result 信封
├── cli_commands/ (新包)
│   ├── generate.py        # 调 service_layer／fn_voxcpm_*／各引擎
│   ├── persona.py         # 调 persona_manager / PersonaService
│   ├── history.py         # 调 history_db
│   ├── model.py           # 调 ModelService / model_manager_core
│   ├── download.py        # 调 download_service（callback 打印进度）
│   ├── training.py        # spawn scripts/train_voxcpcm_finetune.py
│   └── system.py          # 调纯函数；HTTP 专属面委托 serve
└── serve.py (新)          # 内置 uvicorn + create_app()，供 /v1 与 SSE
```

关键原则：

- **直调优先**：凡有独立 Python 函数的功能（绝大多数），CLI 直接 import 调用，不启 server。
- **serve 兜底**：OpenAI `/v1` 兼容、SSE、settings/GPU/日志/shutdown 等强绑 `Request`/`app.state` 的面，由 `cli serve` 启服务后走 HTTP。
- **不重复造轮子**：现有 design/clone/batch 停止直调 vendor，统一走 `service_layer` / `fn_voxcpm_*`，让 `--engine` 真正生效。
- **JSON 契约**：所有 handler 返回统一 `Result` 信封（`ok` / `data` / `error` / `error_code` / `warnings`）；`--json` 时 `cli_output` 输出单行 JSON 到 stdout，日志走 stderr（避免污染解析）；退出码 0 成功 / 1 业务失败 / 2 参数错误 / 3 环境未就绪。
- **合规对齐**：CLI 生成走 service_layer 自带的内容安全检测；克隆类命令要求 `--consent` 显式授权标记（与 Web 的 `has_consent` 对齐）。

## 分阶段路线图

| 阶段 | 内容 | 交付物 | 验证 |
|---|---|---|---|
| **P0（本轮）** | 架构重构：design/clone/batch 迁到 service_layer；建 `cli_core`/`cli_output` 骨架；`--json` 支持；顶层别名兼容 | 重构后的 cli.py + 2 个新模块 + ADR | 三条命令回归生成；JSON 输出解析测试；旧参数兼容测试 |
| P1 | 核心合成扩展：ultimate / prompt-continue / script / emotion(IndexTTS) / breeze / edge / convert / edit / postprocess | generate 子命令族 | 每引擎冒烟（有模型的真机，无模型的 --help 与参数校验） |
| P2 | 音色 + 历史 + 批量断点续跑 | persona / history / batch --resume | DB CRUD 回归、导出 ZIP 校验 |
| P3 | 模型 + 下载 + LoRA + 训练 | model / download / lora / train | 加载/切换、下载进度、训练子进程 |
| P4 | 系统运维 + serve 兜底 | system / engines / serve | `/v1/audio/speech` 端到端、SSE 订阅 |

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| service_layer 缺横切关注点（并发/OOM/缓存/后处理）导致功能倒退 | CLI 单任务场景影响有限；P0 回归测试守住现有行为；后处理用 `audio_processing.enhance_audio` 在 CLI 层补 |
| 重构改变现有音频产物（用户已验证可用） | P0 前后各生成一条对比；保留旧实现开关 `--legacy-engine` 应急回退 |
| 桌面 shim 依赖 `cli.py` 路径与参数 | 顶层 design/clone/batch 别名 + 参数不变；shim 无需改动 |
| 模型加载耗时（≈36s）让每次 CLI 调用变慢 | service_layer 单例 + registry 复用；同进程多命令批量场景用 `batch` 一次性加载 |
| 训练/系统运维强绑 HTTP | 归入 `serve` 兜底，不强行直调 |

## 决策依据

- 用户目标：AI agent 通过 CLI 免去 GUI 繁琐操作，功能完全覆盖项目。
- 用户选择：双模式混合 + JSON 输出 + 本轮架构重构先行。
- 实测证据：`service_layer` 在无 server 环境加载与生成均成功，输出可 JSON 化。
- 项目约束：`AGENTS.md` / `LOCAL_RULES.md`；坑点沉淀到 `GOTCHAS.md`。

## P0 实施记录（2026-11-21 已完成）

### 交付物

| 文件 | 类型 | 内容 |
|---|---|---|
| `app/integrated_app/cli_output.py` | 新增 | `Result` 信封、`emit()`、Human/JSON 双渲染、`begin_json_mode()`、语义化退出码（0/1/2/3） |
| `app/integrated_app/cli_service.py` | 新增 | CLI→service_layer 适配层：`run_design` / `run_clone`、引擎解析与就绪检查、`-o` 结果复制 |
| `app/integrated_app/cli.py` | 修改 | 全局 `--json`（任意位置）、`generate design|clone` 命令族、顶层 `--service` 开关、脚本直跑导入兜底 |
| `tests/test_cli_json.py` | 新增 | 13 个用例：信封序列化、JSON 纯净性、stdout 隔离、引擎解析、Mock 防御 |
| README.md | 修改 | 桌面包 CLI 说明 |

### 兼容策略（实测确认）

- 顶层 `design` / `clone` / `batch` **默认仍走 legacy**（vendor 直调），行为与退出码不变；加 `--service` 才走统一服务层。
- 新的 `generate design|clone` **默认走 service_layer**，`--engine` 真正生效。
- `--json` 从 argv 预提取，允许写在子命令之后（AI agent 自然写法）。

### 实测结果（本机 RTX 5070 Ti，全部通过）

| 场景 | 命令 | 结果 |
|---|---|---|
| 新路径 + JSON | `generate design --json` | EXIT=0，stdout 单行 334B，`json.loads` 严格通过 |
| 别名 + service + `-o` | `design --service -o ... --json` | EXIT=0，音频复制到指定路径，保留 `service_audio_path` |
| legacy 兼容 | `design --model-path model/VoxCPM2` | EXIT=0，276KB 音频正常 |
| batch 兼容 | `batch --input ... --model-path ...` | EXIT=0，2/2 音频生成 |
| 既有测试 | `pytest tests/test_cli.py` | 56 passed |
| 新增测试 | `pytest tests/test_cli_json.py` | 13 passed |
| 静态检查 | `ruff` / `mypy`（新文件） | 全部通过 |

### 实施中发现的坑（须写入项目经验文档）

1. **脚本直跑没有包上下文**：桌面 shim 用 `python cli.py` 直跑时，`from .cli_output import ...` 抛
   `attempted relative import with no known parent package`。解决：文件顶部把 `integrated_app` 父目录
   注入 `sys.path`，并统一使用绝对导入 `from integrated_app.xxx import ...`（开发布局与桌面 `app/app` 两层布局都成立）。
2. **第三方库污染 stdout**：`funasr` 在 import 时 `print("funasr version: ...")`，会破坏单行 JSON。
   解决：`begin_json_mode()` 把 `sys.stdout` 重定向到 `stderr`，`emit()` 写回进入前的原始 stdout。
3. **`--json` 位置限制**：argparse 全局选项只能放在子命令前。解决：`main()` 先从 `sys.argv` 提取并剔除 `--json`。
4. **MagicMock 让 `getattr` 误判**：`getattr(args, "service", False)` 在 Mock 上返回 truthy 对象，导致既有单测
   误入 service 分支。解决：用 `is True` 严格判断；`_resolve_engine` 增加 `isinstance(..., str)` 防御。
5. **legacy 不带 `--model-path` 会联网下载**（既有行为，非本次引入）：离线环境会卡 3+ 分钟后失败。
   文档需提示离线场景显式传 `--model-path` 或改用 `generate`（service 路径走 config.yaml 本地路径）。
6. **E402**：在 `sys.path` 注入块之后 `import soundfile` 会触发 E402；把第三方 import 上移到顶部即可。

### 下一步（P1）

按路线图实现 `generate` 子命令族的其余动作：ultimate / prompt-continue / script / emotion(IndexTTS) /
breeze / edge / convert / edit / postprocess。每个动作遵循同一模式：`cli_service.py` 加 adapter →
`cli.py` 注册子命令 → `cli_output` 统一输出 → 冒烟测试。

## P1 实施记录（2026-11-21 已完成）

### 交付物

| 文件 | 类型 | 内容 |
|---|---|---|
| `app/integrated_app/cli_service_ext.py` | 新增 | 9 个动作适配：ultimate / prompt-continue / script / emotion / breeze(design/clone/direction) / edge / convert / edit / postprocess |
| `app/integrated_app/cli.py` | 修改 | 注册 12 个 generate 子命令 + 顶层 postprocess；`--persona-map`；`sys.exit(main())` 修复退出码 |
| `tests/test_cli_service_ext.py` | 新增 | 13 个用例：两种元组顺序、路径解析、默认输出、真机后处理、参数校验 |
| README.md / ADR | 修改 | P1 用法与实施记录 |

### 命令总览（本轮新增 10 个动作）

```bash
tts-multimodel generate ultimate        --text T --reference-audio R [--ref-text RT] [--denoise-strength S]
tts-multimodel generate prompt-continue --text T --prompt-audio P --prompt-text PT
tts-multimodel generate script          --input SCRIPT.txt [--persona-map 角色=音频 ...]
tts-multimodel generate emotion         --text T --reference-audio R (--emotion-text ET | --emotion-audio EA | --emotion-vector 8floats)
tts-multimodel generate breeze-design   --text T [--control C] [--cfg-scale N]
tts-multimodel generate breeze-clone    --text T --reference-audio R --ref-text RT
tts-multimodel generate breeze-direction --text T --reference-audio R --control C
tts-multimodel generate edge            --text T [--lang zh]
tts-multimodel generate convert         --source-audio S --target-audio G [--tau 0.3]
tts-multimodel generate edit            --source-audio S --source-text ST --edit-type emotion --edit-info happy
tts-multimodel postprocess              --audio A [--tempo 1.1] [--enhance] [--trim-silence]
```

### 实测结果（本机 RTX 5070 Ti）

| 动作 | 结果 | 证据 |
|---|---|---|
| postprocess | ✅ 通过 | 3.68s → 1.1x → 3.345s |
| ultimate | ✅ 通过 | 3.88s 音频；自动 ASR 转写参考音频 |
| prompt-continue | ✅ 通过 | 1.92s 音频 |
| script（音色库角色） | ✅ 通过 | 3/3 段，11.48s，2 角色 |
| script（--persona-map） | ✅ 通过 | 3/3 段，9.88s（"旁白"库里无，显式映射成功） |
| emotion（IndexTTS2） | ✅ 通过 | 3.31s / 1.75s；情感文本生效 |
| edge（Edge-TTS） | ✅ 通过 | 18144 字节 MP3 |
| breeze-design | ⛔ 阻塞 | vendored 代码 `no_init_weights` 在 transformers 5.20 已移除（**项目级既有问题**） |
| convert（OpenVoice） | ⛔ 阻塞 | `from openvoice import ToneColorConverter` 与新版权威路径 `openvoice.api` 不符；PyPI 无包（**项目级既有问题**） |
| edit（Step-Audio） | ⛔ 无权重 | 正确返回 `NOT_READY`(3) + 下载指引 |

> 6/10 真机出音频；4/10 因底层依赖/权重缺失不可用，但 CLI 代码路径与错误码语义均已验证。
> 两个项目级阻塞建议单独立项（不属 CLI 范围）：① Breeze 与 transformers 5.x 的兼容；
> ② voicebox 的 openvoice 依赖声明与 API 路径。

### P1 期间修复的真实缺陷

1. **退出码丢失**：`if __name__ == "__main__": main()` 丢弃返回值，导致失败场景 EXIT=0，
   agent 无法判断成败。改为 `sys.exit(main())`（桌面 shim 直跑同样受益）。
2. **临时目录产物**：IndexTTS2 / Voicebox / Step-Audio 在 `output_path=None` 时写系统临时目录，
   易被清理。改为默认落到 `outputs/`（`_default_output`）。
3. **剧本角色缺音色**：原本只能依赖音色库；新增 `--persona-map ROLE=AUDIO` 让 agent 直接给映射，
   并在部分段失败时输出可操作 warnings。

### 新增坑点

1. **引擎返回元组顺序不一致**：VoxCPM2/IndexTTS 是 `(sample_rate, audio, ref)`，Breeze 是
   `(audio, sample_rate, ref)`。`_audio_tuple_to_result(audio_first=...)` 必须显式区分，否则采样率与
   数组错位。
2. **引擎不读配置**：`VoiceboxEngine()` / `StepAudioEditXEngine()` 不自行解析 `config.yaml` 的
   `model_dir`，必须由调用方传绝对路径。正确做法是复用路由层单例工厂
   （`_get_voicebox_engine` / `_get_editx_engine`，其 `request` 形参未使用，可传 None），
   避免 CLI 另写一套配置逻辑漂移。
3. **Edge-TTS 产物是 MP3 且无时长**：`_synthesize_edge_tts_wav` 返回 `(filename, size)`，
   duration 暂记 0（后续可用 ffprobe/mutagen 补）。

## P2 实施记录（2026-11-21 已完成）

### 交付物

| 文件 | 类型 | 内容 |
|---|---|---|
| `app/integrated_app/cli_service_data.py` | 新增 | 数据域适配：persona（list/show/save/delete）+ history（list/stats/hide/clear/recycle/restore/purge/export/sync） |
| `app/integrated_app/cli.py` | 修改 | 注册 `persona` / `history` 命令组；`batch --resume`；补 logging 配置 |
| `tests/test_cli_service_data.py` | 新增 | 16 例（含 ZIP 路径遍历防护） |

### 新增命令

```bash
tts-multimodel persona list [--keyword K]
tts-multimodel persona show --name N
tts-multimodel persona save --name N --audio A [--ref-text T] [--overwrite] [--consent self|granted|unverified]
tts-multimodel persona delete --name N

tts-multimodel history list [--limit 20] [--offset 0] [--keyword K] [--include-hidden]
tts-multimodel history stats
tts-multimodel history hide --files a.wav,b.wav
tts-multimodel history clear [--hard]
tts-multimodel history recycle [--limit 50]
tts-multimodel history restore --ids 1,2,3
tts-multimodel history purge [--keep-days 30]
tts-multimodel history export --files a.wav,b.wav -o out.zip
tts-multimodel history sync [--output-dir DIR]

tts-multimodel batch --input texts.txt --output-dir ./outs --resume
```

### 实测结果

| 动作 | 结果 | 证据 |
|---|---|---|
| persona list | ✅ | 6 个音色，字段固定（name/status/file_size/created_at/description） |
| persona show | ✅ | 含 `consent_state=unverified` 与 ref_text 全文 |
| history stats | ✅ | 709 条记录、4 个引擎 |
| history list | ✅ | 分页 + 完整字段（含 HMAC/feedback 等） |
| history export | ✅ | 生成合法 ZIP，1 个文件 |
| history hide（不存在文件） | ✅ | hidden=0，不误伤 |
| history recycle | ✅ | 回收站列表 |
| history sync | ✅ | 返回最新 mtime |
| history purge（keep 99999） | ✅ | purged=0，安全 |
| batch --resume | ✅ | 2 个已存在输出全部 SKIP，0.0s |

### P2 期间发现并处理的问题

1. **`get_persona_detail_table` 列序与直觉不同**：实际为
   `name / status / file_size / created_at / description`（不是 名称/描述/路径/大小/时间）。
   直接按直觉映射会导致字段错位，已按其 docstring 修正。
2. **`PersonaService.get_persona` 存在既有解包 bug**：`wav_path, ref_text = embedding`
   抛 `ValueError: not enough values to unpack`。CLI 绕开该服务，直接读
   `persona_manager` 表格 + 磁盘文件（不改动既有代码，避免超出本轮范围）。
3. **CLI 完全没有 logging 配置**：`logger.info` 全被丢弃，batch 的进度/跳过/汇总用户看不见。
   在 `main()` 补 `logging.basicConfig(..., stream=stderr)`；有 handler 时是 no-op，
   不影响引擎自身配置，也不污染 `--json` 的 stdout。
4. **`sync_from_filesystem` 返回的是最新 mtime 而非条目数**：字段名从 `synced`
   改为 `latest_mtime`，避免误导 agent。

## P3 实施记录（2026-11-21 已完成）

### 交付物

| 文件 | 类型 | 内容 |
|---|---|---|
| `app/integrated_app/cli_service_ops.py` | 新增 | 运维域适配：model（status/load/unload/switch）、engines（list/info）、download（catalog/status/start/uninstall）、lora（list/load/unload/toggle/state）、train（start/stop/log） |
| `app/integrated_app/cli.py` | 修改 | 注册 5 个命令组；新增 `_dispatch_result` 统一异常收口 |
| `tests/test_cli_service_ops.py` | 新增 | 15 例 |

### 新增命令

```bash
tts-multimodel model status | load --engine E | unload | switch --engine E
tts-multimodel engines list | info --engine-id ID
tts-multimodel download catalog | status | start --kind runtime|model [--target K] | uninstall --kind ... 
tts-multimodel lora list | load --path P | unload | toggle --enable|--disable | state
tts-multimodel train start --train-manifest M [--save-path DIR] [...] | stop | log [--tail N]
```

### 实测结果

| 动作 | 结果 | 证据 |
|---|---|---|
| engines list / info | ✅ | 7 个引擎及 capabilities |
| model status | ✅ | 未加载态正确 |
| model load voxcpm2 | ✅ | 36.27s 加载成功 |
| download catalog / status | ✅ | 环境包 + 模型清单与 present 状态 |
| lora list | ✅ | 空库返回 count=0 |
| lora state（未加载） | ✅ | `NOT_READY`(3)，不再抛栈 |
| train log（无日志） | ✅ | 明确报「日志目录不存在」 |

### 重要使用约束：CLI 进程隔离

每个 CLI 调用是**独立进程**，因此：

- `model load` 加载的显存会在进程退出时释放，**对后续独立的 CLI 命令无效**；
- 生产用法是让 `generate` 系列命令自行加载（它们内部已处理），
- 需要跨命令保持模型状态时使用 P4 的 `serve`（同一服务进程内 model 命令才有持续意义）。

这一点必须写进用户文档，否则 agent 会误以为 `model load` 能"预热"后续调用。

### P3 期间修复的缺陷

1. **`abspath("")` 陷阱**：`os.path.abspath(getattr(args, "save_path", "") or "")` 在未传参时得到
   **当前工作目录**（不是空串），导致 `train log` 误读 `<cwd>/logs/app.log`（5.7 万行应用日志）。
   改为先判 None/空再取默认（`_resolve_save_path`）。
2. **引擎未加载时抛栈**：`fn_voxcpm_get_lora_state()` 等直接抛 `EngineSwitchError`，CLI 打印
   Python traceback 而非结构化错误。新增 `_dispatch_result` 统一收口：`EngineSwitchError` /
   `EngineNotLoadedError` → `NOT_READY`(3)，其他 `TTSError` → `BUSINESS_ERROR`(1)，
   未预期异常也转 Result（不再崩栈）。

## P4 实施记录（2026-11-21 已完成）

### 交付物

| 文件 | 类型 | 内容 |
|---|---|---|
| `app/integrated_app/cli_service_system.py` | 新增 | `serve`（完整 FastAPI）+ system health/gpu/logs/settings；配置递归脱敏 |
| `app/integrated_app/cli.py` | 修改 | 注册 `serve` 与 `system` 命令组 |
| `tests/test_cli_service_system.py` | 新增 | 9 例（含脱敏与尾读） |

### 新增命令

```bash
tts-multimodel serve [--host 127.0.0.1] [--port 7869]   # 完整 HTTP：/v1 + SSE + Web UI
tts-multimodel system health | gpu | logs [--tail N] | settings
```

### 实测结果

| 动作 | 结果 | 证据 |
|---|---|---|
| system health | ✅ | 平台/CPU/内存/GPU 11.91GB/模型全就位 |
| system gpu | ✅ | 显存四元组 |
| system logs --tail 3 | ✅ | 正确读取 logs/app.log |
| system settings | ✅ | 完整配置快照，敏感字段脱敏 |
| **serve** | ✅ | 启动后自检 16/16；`/v1/models` 200（tts-1/tts-1-hd）、`/api/system/health/ping` 200、`/api/engines/list` 200 |

### 重要说明

- `serve` 是"双模式混合"的 HTTP 侧：OpenAI `/v1/audio/speech`、SSE `/api/sse/events`、
  Web UI 页面这些强绑 `Request`/`app.state` 的能力，通过 `serve` 实现 100% 覆盖。
- **启动耗时约 20-30 秒**（import torch + create_app + 完整性自检），期间端口尚未监听；
  脚本轮询时应留足等待窗口。
- `serve` 与 `model load` 的关系：只有同一 `serve` 进程内，`model load` 的状态才对后续
  请求持续有效；独立 CLI 命令之间不共享显存（见 P3 说明）。

## 最终命令总览（P0–P4 完成态）

```
tts-multimodel
├── generate design|clone|ultimate|prompt-continue|script|emotion
│           |breeze-design|breeze-clone|breeze-direction|edge|convert|edit
├── design|clone|batch（legacy 兼容；design/clone 支持 --service）
├── postprocess
├── persona list|show|save|delete
├── history list|stats|hide|clear|recycle|restore|purge|export|sync
├── model status|load|unload|switch
├── engines list|info
├── download catalog|status|start|uninstall
├── lora list|load|unload|toggle|state
├── train start|stop|log
├── system health|gpu|logs|settings
└── serve
```

覆盖度对照（约 100 个 HTTP 端点 / 7 个引擎 / 37 类功能）：

- **直调覆盖**：全部合成动作、音色管理、历史管理、模型管理、下载管理、LoRA、训练、只读运维；
- **serve 覆盖**：OpenAI `/v1` 兼容面、SSE、HTMX 页面、settings 写入、shutdown 等强绑 HTTP 的能力；
- **明确不支持**：需要交互式 Web UI 的操作（如页面内拖拽、实时表单预览）——本就不属于 CLI 语义。
