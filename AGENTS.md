# AGENTS.md - TTS_MultiModel 代理工作规范

本文件规定了 AI 代理在处理 TTS_MultiModel 项目时的核心行为准则、标准工作流及项目特定约束。

- 详细项目背景请看 `docs/ISSUE_ANALYSIS.md`
- 项目架构全景请看 `docs/PROJECT_ARCHITECTURE.md`
- 优化规格请看 `docs/SPEC_optimization.md`
- IndexTTS2 集成指南请看 `docs/INDEXTTS2_INTEGRATION_GUIDE.md`
- ADR 索引请看 `docs/adr/README.md`
- 当文件冲突时，优先级为：本文件 > `docs/SPEC_optimization.md` > 其他项目文档

---

## 1. 最高优先级规则

1. **任务未真正完成前，禁止主动结束。** 严禁以"我准备好了"或"请告诉我你的选择"作为普通消息结尾并结束 turn。
2. **决策与确认必须使用工具。** 需要用户选择、确认或提供缺失信息时，必须调用 `AskUserQuestion`。
3. **手动步骤必须闭环验证。** 涉及用户手动操作时，必须给出步骤，使用 `AskUserQuestion` 确认完成后，由代理主动执行验证操作（如检测进程、检查文件或运行测试）。
4. **权限不足时优先自动化。** 遇到 UAC 或权限报错，先尝试提权方案（如 PowerShell `-Verb RunAs`），只有确实无法自动完成时才转为手动步骤。
5. **禁止编造信息。** 对不确定的技术细节、模型参数或 API 行为，必须明确说明不确定性或进行联网搜索。
6. **回复语言统一为中文。** 无论用户输入何种语言，面向用户的说明和建议必须使用中文。
7. **文档与实际不符时必须更新。** 在工作过程中，如果发现 AGENTS.md 中的描述（如模块路径、功能说明、配置项等）与项目实际情况不匹配，必须及时更新 AGENTS.md 文件，确保文档始终反映真实状态。

---

## 2. 标准工作流

### 2.1 先澄清，再执行
- **场景**：需求模糊（如"优化生成"）、规格缺失（如新增 API 字段不全）、指令冲突。
- **要求**：提问要具体，尽量提供可选项，并说明不同选择对性能、显存或质量的影响。

### 2.2 先搜索，再下结论
- **场景**：遇到特定引擎报错、CUDA 版本兼容性、Transformers 库更新、未文档化的音效参数。
- **要求**：优先参考官方文档和 GitHub Issues。引用事实时尽量附带来源链接。

### 2.3 先读取，再修改
- **场景**：修改任何代码文件、配置文件或模型定义。
- **要求**：禁止盲改。必须先用 `Read` 或 `Grep` 理解上下文。变更后必须进行最小充分验证。

### 2.4 先验证，再交付
- **场景**：代码修复、重构或功能添加。
- **要求**：至少运行相关单元测试（pytest）或集成测试。如果是 WebUI 修改，应使用浏览器工具验证页面表现。

### 2.5 本地验证命令（与 CI 门禁对齐）

以下命令与 `.github/workflows/ci.yml` 逐步对应，**ci.yml 为命令真值来源**，两者不一致时以 ci.yml 为准并同步更新本节：

```bash
# Lint（对应 ci.yml lint job）
ruff check bin/integrated_app/ scripts/
ruff format bin/integrated_app/ scripts/ --check --diff

# 依赖一致性检查（对应 ci.yml test job 的阻断步骤）
python scripts/sync_requirements.py --check

# 3引擎兼容性检查（对应 ci.yml compat job）
# 需先设置离线环境变量
# Linux/macOS: export TRANSFORMERS_OFFLINE=1 HF_HUB_OFFLINE=1 MODELSCOPE_OFFLINE=1 CUDA_VISIBLE_DEVICES=""
# Windows PowerShell: $env:TRANSFORMERS_OFFLINE="1"; $env:HF_HUB_OFFLINE="1"; $env:MODELSCOPE_OFFLINE="1"; $env:CUDA_VISIBLE_DEVICES=""
python scripts/check_3engine_compat.py

# CPU-only 单元测试 + 覆盖率门槛（对应 ci.yml test job，需先设置离线环境变量）
pytest tests/ -v --tb=short --cov=bin/integrated_app --cov-branch --cov-report=term-missing --cov-report=xml:coverage.xml --cov-fail-under=40 -m "not integration and not gpu and not cuda and not vram"
```

注意：`-m` 过滤器与离线环境变量不可省略，否则本地结果与 CI 门禁不可比（会尝试运行需要 GPU/模型的用例）。Windows 本地可用内置解释器 `.\WPy64-312101\python\python.exe -m pytest ...` 执行。

覆盖率配置说明：
- `pyproject.toml` 中 `[tool.coverage.run] branch = true` 启用分支覆盖
- `pyproject.toml` 中 `[tool.pytest.ini_options] asyncio_mode = "auto"` 自动处理异步测试
- markers 已注册 `integration`、`benchmark`、`gpu`、`cuda`、`vram` 五类标记
- CI 和本地均使用 `-m "not integration and not gpu and not cuda and not vram"` 基于 marker 过滤（非 `-k`）

---

## 3. 工具使用规范

- **AskUserQuestion**：用于决策和确认，禁止用于简单通知。
- **Read / Edit / SearchCodebase**：优先于 `RunCommand` 处理文件。
- **Task (子代理)**：用于大型任务拆解（如同时重构多个路由）、并行搜索或多模块探索。
- **WebSearch / WebFetch**：用于解决时效性信息或专业领域知识缺口。
- **GetDiagnostics**：在修改后检查是否存在语法错误或 Lint 警告。

---

## 4. 编码与脚本约定

### 4.1 命名规范
- **Python**：变量、函数、文件名用 `snake_case`（如 `generate_voice.py`）。类名用 `PascalCase`。
- **JS/TS**：变量用 `camelCase`，类名用 `PascalCase`。
- **常量**：全大写加下划线（如 `MAX_VRAM_LIMIT`）。
- **i18n**：遵循 `namespace.sub.key` 格式（如 `voxcpm2.ui.generate_btn`）。

### 4.2 脚本编码
- **.bat**：仅限 ASCII 英文，避免中文乱码。
- **.ps1**：使用 UTF-8 with BOM。
- **.py**：使用 UTF-8，头部保留编码声明。
- **JSON/YAML**：统一使用 UTF-8 无 BOM。

### 4.3 依赖管理
- **主依赖**：`requirements.txt` 包含所有运行时依赖，`pyproject.toml` 定义项目元数据
- **依赖同步**：使用 `scripts/sync_requirements.py` 检查依赖一致性
- **离线安装**：先执行 `pip download -r requirements.txt -d ./offline_packages`，再用 `pip install --no-index --find-links=./offline_packages -r requirements.txt`

---

## 5. TTS_MultiModel 项目速查

### 5.1 运行与入口
- **版本**：以 `config.yaml` 中 `version` 字段为单一真值来源（`pyproject.toml` 需与其保持同步）
- **Windows 推荐入口**：`start.bat`（使用内置 WinPython）
- **备选入口**：`bin/start_app.bat`（使用系统 Python）
- **启动链路**：`start.bat` -> `bin/clean_launch.py` -> `bin/integrated_app/app_server.py`
- **默认地址**：`127.0.0.1:7869`
- **环境变量**：离线模式必须设置 `TRANSFORMERS_OFFLINE=1`、`HF_HUB_OFFLINE=1` 和 `MODELSCOPE_OFFLINE=1`
- **自动加载**：`config.yaml` 中 `server.auto_load_model: true` 可启用启动时自动加载模型
- **Docker 部署**：支持 NVIDIA CUDA 12.1 容器化部署，使用 `docker-compose.yml` 或 `Dockerfile` 构建

### 5.2 核心模块

所有模块位于 `bin/integrated_app/` 下：

| 模块 | 职责 |
|------|------|
| [app_server.py](bin/integrated_app/app_server.py) | FastAPI 应用创建、生命周期管理、中间件注册、路由自动发现 |
| [config.py](bin/integrated_app/config.py) | 路径配置、YAML 解析、`AppConfig` 单例（`get_config()`） |
| [config_models.py](bin/integrated_app/config_models.py) | Pydantic 配置模型（`AppConfig`、`ServerConfig`、`GenerationConfig`、`SSEConfig` 等） |
| [model_manager.py](bin/integrated_app/model_manager.py) | 模型加载/卸载、引擎切换、LRU 缓存、进度追踪、显存监控 |
| [model_registry.py](bin/integrated_app/model_registry.py) | 线程安全的模型状态单例（`registry`），管理 `voxcpm_model`、`indextts2_engine`、`current_engine` |
| [engine_interface.py](bin/integrated_app/engine_interface.py) | `TTSEngine`、`ControllableTTSEngine` 协议定义 + `InMemoryEngineRegistry` 引擎注册表 |
| [gpu_backend.py](bin/integrated_app/gpu_backend.py) | CUDA/MPS/CPU 后端抽象与显存管理 |
| [gpu_utils.py](bin/integrated_app/gpu_utils.py) | GPU 工具函数：OOM 检测、显存释放、`GPUMemoryMonitor` |
| [persona_manager.py](bin/integrated_app/persona_manager.py) | 音色角色管理与嵌入缓存 |
| [persona_metadata.py](bin/integrated_app/persona_metadata.py) | Persona 扩展元数据：标签、分类、导入/导出 |
| [generation.py](bin/integrated_app/generation.py) | 生成辅助：音频保存、文本分割、音频合并、预处理 |
| [audio_processing.py](bin/integrated_app/audio_processing.py) | 音频后处理：响度归一化、VAD 静音裁切 |
| [cache.py](bin/integrated_app/cache.py) | `LRUCache`、`AdaptiveLRUCache`（GPU 感知自适应容量） |
| [estimator.py](bin/integrated_app/estimator.py) | 生成时间估算器（线性回归预测） |
| [exceptions.py](bin/integrated_app/exceptions.py) | 统一异常层次：`TTSError`、`ModelLoadError`、`InsufficientVRAMError`、`GenerationError` 等 |
| [history_db.py](bin/integrated_app/history_db.py) | SQLite 历史记录索引（WAL 模式、线程本地连接池） |
| [progress.py](bin/integrated_app/progress.py) | `ProgressManager`：生成进度追踪与 HTML 进度条渲染 |
| [prompt_cache.py](bin/integrated_app/prompt_cache.py) | 参考音频 prompt 缓存持久化（JSON+binary 格式，LRU+TTL） |
| [tracker.py](bin/integrated_app/tracker.py) | `GenerationTracker`：生成任务状态追踪 |
| [monitor.py](bin/integrated_app/monitor.py) | `HealthMonitor`：GPU 泄漏检测、模型自检、健康指标 |
| [auth.py](bin/integrated_app/auth.py) | Bearer Token API 认证中间件（恒定时间比较） |
| [i18n.py](bin/integrated_app/i18n.py) | 国际化：支持 zh/en/ja/ko 四种语言 |
| [utils.py](bin/integrated_app/utils.py) | 通用工具函数（临时文件清理、角色颜色、标签处理） |
| [cli.py](bin/integrated_app/cli.py) | 命令行接口（VoxCPM 多引擎 CLI） |
| [emotion_control.py](bin/integrated_app/emotion_control.py) | 情感标签解析与控制 |
| [emotion_tags.py](bin/integrated_app/emotion_tags.py) | 情感标签系统 |
| [text_frontend.py](bin/integrated_app/text_frontend.py) | 文本前端处理（分词、规范化） |
| [service_layer.py](bin/integrated_app/service_layer.py) | 服务层抽象 |
| [task_queue.py](bin/integrated_app/task_queue.py) | 任务队列管理 |
| [signal_handlers.py](bin/integrated_app/signal_handlers.py) | 信号处理器 |
| [vllm_backend.py](bin/integrated_app/vllm_backend.py) | vLLM 加速后端 |
| [openai_api.py](bin/integrated_app/openai_api.py) | OpenAI 兼容 API |
| [mcp_server.py](bin/integrated_app/mcp_server.py) | MCP 服务器 |

### 5.3 TTS 引擎

引擎通过 `InMemoryEngineRegistry` 注册，支持运行时动态发现和切换。引擎配置在 `config.yaml` 的 `models.engines` 中声明。

#### VoxCPM2（核心引擎）
位于 `bin/integrated_app/engines/voxcpm2/`，子模块结构：

| 子模块 | 功能 |
|--------|------|
| `engine.py` | `VoxCPM2Engine` 类，实现 `TTSEngine` + `ControllableTTSEngine` 协议 |
| `design.py` | 语音设计生成（文本描述 -> 语音） |
| `clone.py` | 语音克隆（参考音频 + 文本 -> 克隆语音） |
| `ultimate.py` | 终极克隆模式（完整参数控制：cfg/denoise/steps/seed） |
| `script.py` | 剧本工坊（多角色对话生成） |
| `streaming.py` | 流式生成（长文本分段流式输出） |
| `prompt.py` | Prompt 延续模式（参考音频续写） |
| `lora.py` | LoRA 微调权重加载/卸载/启用/禁用 |
| `decorators.py` | 生成上下文装饰器（`@with_generation_context`） |
| `_base.py` | 共享工具函数和高级参数构建 |

#### IndexTTS2（情感控制引擎）
位于 `bin/integrated_app/engines/indextts2_engine.py`，支持：
- 零样本语音克隆
- 8 维情感向量控制（happy/angry/sad/afraid/disgusted/melancholic/surprised/calm）
- 时长控制
- 多后端 GPU 支持（CUDA/MPS/CPU）
- 最低 6GB 显存 + 16GB 内存

#### dots.tts（克隆引擎）
位于 `bin/integrated_app/engines/dotstts_engine.py`，支持：
- 零样本语音克隆
- 中英文双语支持
- 高质量语音合成（48kHz 采样率）
- 最低 8GB 显存 + 16GB 内存

#### 角色存储
位于 `personas/`，每个角色包含：
- `.wav` — 参考音频
- `.txt` — 文本描述/信息
- `.pt` — 预计算嵌入

### 5.4 路由结构

路由通过 `pkgutil` 自动发现，位于 `bin/integrated_app/routes/` 下：

| 路由模块 | 前缀 | 职责 |
|----------|------|------|
| `pages.py` | `/` | 首页、favicon |
| `tabs.py` | `/tabs` | HTMX 标签页加载（voice_design/clone/ultimate/script/indextts2 等） |
| `model.py` | `/api/model` | 模型状态、加载/卸载、预加载、引擎切换、LoRA 管理 |
| `audio.py` | `/api/audio` | 音频文件服务（生成结果、Persona 音频、说话人样本） |
| `persona.py` | `/api/persona` | 音色管理（增删改查、参考音频下发） |
| `sse.py` | `/api/sse` | 统一 SSE 事件流端点 `/api/sse/events` |
| `training.py` | `/api/training` | LoRA 微调训练管理 |
| `generate/voxcpm2/` | `/api/generate/voxcpm2` | VoxCPM2 生成路由（design/clone/script/streaming） |
| `generate/indextts2/` | `/api/generate/indextts2` | IndexTTS2 生成路由（synthesize） |
| `generate/generic/` | `/api/generate/generic` | 通用生成路由 |
| `system/health.py` | `/api/system` | 健康检查、生成统计 |
| `system/gpu.py` | `/api/system` | GPU 状态与显存信息 |
| `system/logs.py` | `/api/system` | 操作日志查询 |
| `system/settings.py` | `/api/system` | 运行时设置 |

### 5.5 中间件

| 中间件 | 文件 | 职责 |
|--------|------|------|
| `RequestIDMiddleware` | `middleware/request_id.py` | 为每个请求分配唯一 ID，注入日志上下文 |
| `CORSMiddleware` | 内置 | 跨域资源共享 |
| `CSRFMiddleware` | `middleware/csrf.py` | Double-Submit Cookie CSRF 防护 |
| `APIAuthMiddleware` | `auth.py` | Bearer Token 认证（可选，`config.yaml` 中配置） |
| `error_handler` | `middleware/error_handler.py` | 全局异常处理（TTSError/ValidationError/Generic） |

### 5.6 国际化

位于 `bin/integrated_app/locales/`，支持 4 种语言：
- `zh.json` — 中文（默认）
- `en.json` — 英文
- `ja.json` — 日文
- `ko.json` — 韩文

通过 `i18n.py` 的 `t()` 函数和 Jinja2 模板过滤器使用。

### 5.7 训练模块

位于 `bin/integrated_app/training/`，用于 VoxCPM LoRA 微调：

| 子模块 | 职责 |
|--------|------|
| `accelerator.py` | 训练加速器 |
| `config.py` | 训练配置 |
| `data.py` | `HFVoxCPMDataset`、`BatchProcessor`、数据加载 |
| `packers.py` | 数据打包器 |
| `state.py` | `TrainingState` 训练状态管理 |
| `tracker.py` | `TrainingTracker` 训练进度追踪 |

### 5.8 配置系统

`config.yaml`（根目录）管理所有运行时配置：

```yaml
version: "x.y.z"   # 以 config.yaml 实际值为准，勿硬编码
server:        # 主机、端口、自动加载、SSL
models:        # 模型路径（VoxCPM2 + IndexTTS2 + dots.tts）
generation:    # 生成默认参数（cfg、timesteps、重试策略等）
cache:         # Persona 缓存策略
environment:   # 离线环境变量
api_auth:      # API 认证（Bearer Token）
sse:           # SSE 事件流参数
audio_player:  # 音频播放器配置
ui:            # UI 布局（侧边栏宽度等）
```

通过 `config.py` 的 `get_config()` 获取 `AppConfig` 单例，或通过 `config_models.py` 中的 Pydantic 模型进行类型安全的访问。

### 5.9 测试

测试文件位于 `tests/`，使用 pytest：

- `test_app.py` / `test_app_server.py` — 应用启动与路由
- `test_config.py` / `test_config_models.py` — 配置解析
- `test_generation.py` — 生成流程
- `test_engine_interface.py` / `test_engine_switch.py` — 引擎接口与切换
- `test_dotstts_engine.py` — dots.tts 引擎测试
- `test_routes_htmx.py` — HTMX 路由
- `test_gpu_utils.py` / `test_gpu_utilization_routes.py` — GPU 工具与路由
- `test_history_db.py` — 历史记录数据库
- `test_i18n.py` — 国际化
- `test_security.py` / `test_path_traversal.py` — 安全测试
- `test_auth.py` / `test_auth_integration.py` — 认证
- `test_csrf_integration.py` — CSRF 防护
- `test_cache.py` / `test_prompt_cache.py` — 缓存
- `test_exceptions.py` — 异常处理
- `test_progress.py` — 进度追踪
- `test_sse.py` — SSE 事件流
- `test_audio_processing.py` — 音频处理
- `test_utils.py` — 通用工具
- `test_bin_integration.py` / `test_bin_system_enhancements.py` — 集成测试
- `test_hard_constraints.py` — 硬约束测试
- `test_service_layer.py` / `test_service_layer_signal_taskqueue.py` — 服务层测试
- `test_signal_handlers.py` — 信号处理器测试
- `test_task_queue.py` — 任务队列测试
- `test_page_switch.py` — 页面切换测试
- `benchmarks/test_generation_bench.py` — 生成性能基准

---

## 6. 项目硬约束

1. **显存预检**：模型加载前必须预检，可用显存需为模型大小的 1.5 倍以上。
2. **内存熔断**：显存占用超过 90% 时必须立即终止推理并清理缓存。
3. **离线优先**：禁止在推理过程中自动下载模型，必须引导用户使用内置脚本。
4. **单 Worker 串行**：生成任务通过 `model_manager.py` 串行处理，避免并发显存爆炸。
5. **SSE 状态推送**：进度更新通过统一端点 `/api/sse/events` 推送，支持 progress/complete/status/engine_switch/cancelled/time_estimate 事件类型，前端不应使用轮询。

---

## 6.1. 实现陷阱（代码不可直接推断）

- **静态文件缓存陷阱**：自定义 StaticFiles 子类（如 `CachedStaticFiles`）曾为 CSS/JS 设置 `immutable` + 长期 `max-age`（1年/7天等），导致修改 CSS/JS 后刷新页面仍显示旧版。修改此类文件后务必：(1) 检查 `Cache-Control` 头是否含 `immutable`；(2) 检查 `base.html` 中 `?v=` 版本号是否需要递增；(3) 最稳妥方案是将 CSS/JS 的 Cache-Control 改为 `no-cache, must-revalidate`，使每次刷新都向服务器验证
- GPU 后端支持 NVIDIA CUDA、Apple MPS 和 CPU，启动时自动检测
- 模型状态通过 `model_registry` 监听器桥接到 SSE 事件总线，模块间解耦，不要直接 import event_bus
- 测试场景中应避免真实模型自动加载，优先使用 mock 或现有测试夹具

---

## 7. 安全与变更边界

- **敏感信息**：严禁读取或修改 `.env`、密钥或证书文件。
- **Git 操作**：禁止使用 `git reset --hard` 等破坏性回滚。
- **联网搜索**：查询中不得包含用户的私有代码片段或 Persona 数据。
- **CSRF 防护**：所有 state-changing 请求必须通过 `X-CSRF-Token` 头携带 token。
- **API 认证**：Bearer Token 认证使用恒定时间比较（`hmac.compare_digest`）防止定时攻击。
- **异常处理**：所有异常通过 `middleware/error_handler.py` 统一捕获，返回标准化 JSON 格式。

---

## 8. 交付前自检

- [ ] 我是否完成了完整任务，而不是停在建议阶段？
- [ ] 我是否对所有代码修改进行了语法和逻辑验证？
- [ ] 我是否使用了 `AskUserQuestion` 处理所有未决决策？
- [ ] 我是否在修改后检查了显存占用和性能影响？
- [ ] 我是否遵守了项目命名规范（特别是 i18n 命名空间）？
