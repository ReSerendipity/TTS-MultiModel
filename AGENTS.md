# TTS_MultiModel AGENTS.md — AI 辅助开发指南

> 🧬 **自进化协议版本**：v1.11  
> 📅 **最后更新日期**：2026-08-27  
> 🎯 **对应项目版本**：v2.2.1（`pyproject.toml` 与 `CHANGELOG.md` 一致）  
> ⚠️ **实测版本漂移待处理**：`config.yaml` 顶层 `version` 仍为 `"2.2.0"`，落后一个 patch。
> release-please 不会自动同步该文件，需人工补齐（它会驱动前端 `?v=app_version` 缓存参数，见陷阱 #9）。

---

## ⚠️ 🤖 Agent 行为契约（自进化协议 · 必须严格遵守）

AI Agent 打开本文件后的 **第一件事** 是执行下面的「🧪 自进化自检清单」，并遵守以下 6 条铁律：

### 🔴 6 条自进化铁律
1. **🔄 同步规则（Synchronize First）**：如果发现项目实际情况（目录结构、依赖版本、技术栈、配置文件名等）与本文件描述 **不一致** → **立即更新本文件**，不要只改代码不改 AGENTS.md。这是最高优先级的规则。
2. **📝 坑点累积（Gotchas Accumulation）**：每次修复 Bug / 踩坑后（哪怕是很小的坑），**必须** 追加一条到第 13 节「常见陷阱（Known Gotchas）」，写清楚：触发场景、现象/报错、正确做法、首次发现日期。
3. **📚 SOP 累积（SOP Accumulation）**：每次完成一个「本文件现有 SOP 没覆盖」的典型开发任务后，**必须** 把步骤整理成新 SOP 追加到第 12 节「典型 AI 开发场景 SOP」。
4. **✅ 自检流程（Self-Check on Startup）**：每次打开本文件准备工作前，**必须** 先运行下面的「🧪 自进化自检清单」，逐项核对，有任何一项不符先修正 AGENTS.md 再干活。
5. **🏷️ 版本递增（Version Increment）**：每次更新本文件内容后，**必须** 做三件事：① 文件顶部「自进化协议版本号」+0.1（小改）或 +1.0（大改/框架调整）；② 更新「最后更新日期」；③ 在文件末尾「📋 自进化修订记录表」追加一行记录。
6. **🔬 证据绑定（Evidence Binding）**：本文件中每出现一个**可执行文件路径**（脚本、配置、workflow、源码），它必须是**当时可验证存在**的。引用前跑一次 `python scripts/check_spec_refs.py`；若确实想描述尚未实现的东西，必须显式加 `（计划，未实现）` 前缀。禁止把"CI 会阻断 X"写成一个 CI 里不存在的门禁。

### 🧪 自进化自检清单（每次启动工作前必跑）
- [ ] 目录结构（顶层 `app/` + `app/integrated_app/` 下的 `engines/`、`routes/`、`security/`、`training/` 等）是否和第 3 节模块边界 + 5 条硬约束描述一致？
- [ ] 现役引擎（VoxCPM2 / IndexTTS 2.5 / 通用引擎）的 Registry 注册名是否和 `engine_interface._register_builtin_engines()` 里的**显式** `engine_registry.register(...)` 实参一致？（本仓无目录扫描式自动注册）
- [ ] 上次工作是否踩了新坑？如果是，是否已追加到第 13 节 Known Gotchas？
- [ ] 是否新增了路由？如果是，是否定义了**模块级 `router` 变量**（`app_server.py` 靠 `hasattr(mod, "router")` 发现，无文件名后缀要求）？
- [ ] 新增的翻译 key 是否已完成 5 种语言 JSON 词表同步（见第 8 节 i18n 规范）？
- [ ] 本文引用的 scripts/ configs/ workflows/ 路径是否全部真实存在？（跑 `python scripts/check_spec_refs.py`，要求退出码 0）
- [ ] §pre-commit 表格是否与 `.pre-commit-config.yaml` **双向**一致？（既无虚构钩子，也无漏记实际钩子）
- [ ] 上次更新是否正确递增了自进化协议版本号 + 追加了修订记录表？

---

## 1. 项目概览

> **TTS_MultiModel**：多引擎统一文本转语音（TTS）后端服务。
> 核心特色：**多引擎热插拔** + 统一 API + 单 Worker 串行调度防 GPU OOM。
> 当前注册的引擎（2026-08-27 实测 `engine_interface.py` 的 `_register_builtin_engines()`）：
> **VoxCPM2**（核心，7 种生成模式，24kHz）+ **IndexTTS 2.5**（可选情感控制，22.05kHz）+ **通用引擎接口**；
> **dots.tts 已于 2026-08-15 停用**（硬依赖 pynini 在原生 Windows 无预编译包），注册代码保留在注释中，
> 详见 §12 SOP-1 与 §13 陷阱。历史文档提到的 CosyVoice2 / ChatTTS / F5-TTS 三引擎**均已下线**。
> 开源协议：**Apache-2.0**
> 技术栈：**Python 3.11+ + FastAPI 0.115+ + Uvicorn + Pydantic v2 + PyYAML + NumPy + SoundFile + Torch 2.x（CUDA）**
> （历史版本此处列的 `AioSQLite` 不存在，历史记录实际用**标准库 `sqlite3`**，见 `history_db.py`；
> `structlog` 也不在依赖清单中，日志用标准库 `logging`）
> 代码入口：`app/clean_launch.py`（推荐，含引擎健康预热）
> 默认端口：**`http://127.0.0.1:7869`**（禁止 0.0.0.0 监听，见第 11.4 条与第 13 节陷阱）
> 默认路由前缀：**`/api/...`**，按域划分
> （`/api/generate`、`/api/system`、`/api/model`、`/api/persona`、`/api/audio`、`/api/training`；
> **历史文档里的 `/api/v1/tts/...` 前缀全仓零命中，不存在**）
> 依赖管理：**`pyproject.toml` 为唯一声明源**（`[project] dependencies` 生产依赖 +
> `[project.optional-dependencies] dev` 开发依赖）；`requirements.txt` 由
> `scripts/sync_requirements.py` 从 pyproject 生成；`requirements-lock.txt` 为锁定版本。
> **不存在 `requirements-dev.txt`**（开发依赖请 `pip install -e ".[dev]"`）。

---

## 2. 代码风格约定

### 2.1 Lint / 格式化 / 类型检查
| 工具 | 配置说明 | 关键规则 |
|------|---------|---------|
| **Ruff** | `pyproject.toml → [tool.ruff]` | `target-version = "py311"`，`line-length = 100` |
| Ruff select | `select = ["E", "F", "I", "W", "UP", "B", "A"]` | UP（Python 3.11 现代化语法）、B（flake8-bugbear）、A（flake8-builtins） |
| Ruff ignore（⚠️ 重要，不要擅自移除） | `ignore = ["E402", "B008", "B017"]` | **为什么有这三个 ignore？每条都有理由**<br>`E402`：引擎 `__init__.py` 需要先 `sys.path.insert(0, engines_dir)` 再 import 第三方模型代码<br>`B008`：Pydantic `Field(default_factory=list)` 广泛使用可变默认值，这是框架官方推荐用法，不是 bug（注：此前解释里还提到 `Depends(engine_registry.get())`，但本仓当前未使用 FastAPI `Depends`，见 §7）<br>`B017`：安全测试代码用 `pytest.raises(Exception)` 故意抓所有异常测回退，这是正确的测试策略 |
| **Mypy** | `[tool.mypy] strict = false` | 渐进式策略。**⚠️ 但 mypy 当前不在 pre-commit 门禁内**（`.pre-commit-config.yaml` 无 mypy 钩子，见 §10），仅可手动执行；历史文档所列的 `common/`、`core/`、`api/main.py` 路径不存在，实际类型检查目标为 `app/integrated_app/` |
| **命名规则** | 全局 | 类/异常 `PascalCase`，函数/方法/变量 `snake_case`，常量 `UPPER_SNAKE_CASE`，模块 `snake_case.py` |
| 协议/接口名（Protocol） | 补充规则 | 允许 `AbstractXxx`、`XxxProtocol` 两种命名。**引擎契约的 Protocol 定义在 `app/integrated_app/engine_interface.py`（含 `EngineRegistry` 等，`@runtime_checkable`）**——不存在 `engines/base.py`，也不存在 `BaseTTSProtocol` 这个名字 |

### 2.2 Import 顺序（Ruff `isort` 强制执行）
```python
# 1. Stdlib（import sys / os / asyncio / typing / logging）
# 2. Third-party（import torch / fastapi / numpy）
# 3. Local project（from .config import get_project_root / from .engine_interface import engine_registry）
```
> 禁止 `from fastapi import FastAPI, APIRouter, Depends, HTTPException` 一行多 import（Ruff `I` 规则会自动拆成 4 行）。

### 2.3 Docstring
- public 类 / 函数用 **Google 风格** docstring：
  ```python
  def synthesize(text: str, voice: str = "default") -> bytes:
      """调用 TTS 引擎合成音频。

      Args:
          text: 要合成的文本（语言自动检测）
          voice: 音色名，可用值见 GET /api/persona/table

      Returns:
          bytes: WAV 格式音频数据（16-bit PCM，24kHz 采样率）

      Raises:
          EngineNotLoadedError: 目标引擎未加载（HTTP 503，引导用户去 Settings 加载）
          InsufficientVRAMError: CUDA 显存耗尽（HTTP 503，由 OOM retry 捕获后降级）
      """
  ```

---

## 3. 模块边界 & 5 条硬约束（🚫 绝对不能违反）

```
TTS_MultiModel/
├── app/
│   ├── clean_launch.py        ← 推荐入口（含 CUDA 检测 + VRAM 预估 + 预热一轮合成）
│   └── integrated_app/        ← 应用主体（以下缩进内容全部相对此目录）
│       ├── app_server.py          ← FastAPI 组装 + pkgutil 路由自动发现 + 静态/模板挂载
│       ├── config.py              ← 配置加载，读仓库根级 config.yaml（唯一权威配置源）
│       ├── i18n.py                ← JSON 驱动 i18n，5 语言 + 两层 fallback，规范见第 8 节
│       ├── engine_interface.py    ← EngineRegistry Protocol + engine_registry 单例 + _register_builtin_engines()
│       ├── engine_ui_data.py      ← 引擎 UI 元数据（展示名/特性/表单字段）
│       ├── model_manager.py       ← 权重定位、加载与显存预估
│       ├── history_db.py          ← 历史记录持久化（标准库 sqlite3）
│       ├── generation_versioning.py ← 生成参数版本化（sqlite3）
│       ├── batch_inference.py  spec.py  watermark.py
│       ├── engines/               ← 模型引擎层（接口 + 实现；**显式注册**，见 §12 SOP-1）
│       │   ├── voxcpm2_engine.py      ← 顶层入口/兼容层
│       │   ├── indextts2_engine.py    ← IndexTTS2 引擎（单文件实现）
│       │   └── voxcpm2/               ← VoxCPM2 引擎（包形式，7 种生成模式）
│       │       └── engine.py  _base.py  design.py  clone.py  script.py
│       │           ultimate.py  prompt.py  streaming.py  lora.py  decorators.py
│       ├── routes/                ← FastAPI 路由（pkgutil 递归自动发现；
│       │   │                         契约 = 模块内定义 `router` 变量，**不约束文件名**）
│       │   ├── pages.py  tabs.py  model.py  audio.py  sse.py  persona.py  training.py
│       │   ├── generate/          ← 生成路由子包
│       │   │   ├── utils.py       ← per-engine asyncio.Semaphore（默认容量 1 = 串行）
│       │   │   ├── voxcpm2/       ← design / clone / script / streaming
│       │   │   ├── indextts2/     ← synthesize
│       │   │   └── generic/       ← 通用引擎接口
│       │   └── system/            ← health.py（健康/统计） gpu.py（显存）
│       ├── security/              ← content_safety.py  integrity_check.py
│       │   └── integrity_selfcheck.py  integrity_manifest.json（权重 SHA-256 清单）
│       └── locales/               ← zh.json zh-tw.json en.json ja.json ko.json（**JSON，非 gettext**）
├── model/                   ← 模型权重（🚫 禁区，AI 不允许自动修改）
├── personas/  lora/  prompt_cache/   ← 音色库 / LoRA 权重 / prompt 缓存
├── tests/                   ← 测试体系（6 层，第 4 节详细说明）
├── perf/                    ← cold-start.py  vram-usage.py  generation-benchmark.py
│                              stress-test.py  report_generator.py
├── scripts/                 ← 辅助脚本（下载 / 校验 / 兼容性 / 水印密钥 / 依赖同步，见 §5、§11）
├── docs/                    ← 项目文档（索引见 docs/README.md，分类归档见 §10.2）
├── install.bat / start.bat        ← Windows 一键
├── install.sh  / start.sh         ← Linux/macOS 一键
├── config.yaml              ← 唯一权威配置源（顶层键：version / server / models / ui /
│                                 history / logging / runtime / watermark；
│                                 引擎声明在 models.engines.<key>；**不存在 synthesis: 顶层键**）
├── requirements.txt               ← 生产依赖（由 scripts/sync_requirements.py 从 pyproject 生成）
├── requirements-lock.txt          ← 锁定版本（由 pip-compile 生成，非本仓脚本）
└── pyproject.toml            ← 唯一依赖声明源 + 工具配置 + dev extras + pre-commit 元数据
```

> **⚠️ 路径前缀勘误说明（2026-08-27）**：本节此前的版本把入口写成顶层 `api/` `common/` `core/`
> `engines/` `routes/` `training/`，那是项目早期规划结构。实际代码已在历次重构中全部收敛到
> `app/integrated_app/` 之下（顶层不存在这六个目录）。本文件其余章节若出现 `common/xxx`、
> `core/xxx`、顶层 `engines/xxx` 的简写，一律读作 `app/integrated_app/` 下的对应路径。

> **🚫 已删除的不存在实现**：顶层 `training/` 目录（实际在 `app/integrated_app/training/`）、
> `core/prompt_templates/` 模板目录、`engines/auto_register.py`、`engines/base.py`、
> `common/logger.py`、`common/db.py`（AioSQLite）均不存在。
> prompt 逻辑内联在 `app/integrated_app/engines/voxcpm2/prompt.py`；
> 历史库实际为标准库 sqlite3（`history_db.py`）；
> **日志为Python 标准库 `logging.getLogger("tts_multimodel")`，`structlog` 不在依赖清单中**，
> 各模块自行 `import logging` 取同名 logger，不存在统一 logger 封装文件。

### 🔴 5 条硬约束（违反一条直接导致生产事故）

> 本节此前的版本用 `core.services.*` / `core.scheduler.TTSScheduler` / `common/` 指代实现，
> 这些顶层包不存在（见 §3 勘误说明）。约束本身全部有效，以下是按实际代码修正后的表述。

1. **`routes/` 目录永远不写业务逻辑**：路由只能做：参数校验（Pydantic）+ 调用 `app/integrated_app/` 下的能力模块（如 `batch_inference.py`、`model_manager.py`、`audio_processing.py`、`persona_manager.py`、`history_db.py`）+ 返回响应。**路由文件里不允许出现 `torch.*` / `numpy.*` / 任何推理相关代码**。
2. **`engines/` 只是接口适配层**：不做业务编排、不写 DB、不写日志（只抛异常给上层）。引擎实现只做一件事：接收输入 → 调模型 → 返回音频 bytes。
3. **`training/` 完全独立**：API 启动路径（`app/clean_launch.py` / `app/integrated_app/app_server.py`）**绝对不 import `app/integrated_app/training/`** 任何模块。如果 training 和业务能力需要共享代码 → 抽到 `app/integrated_app/` 顶层模块（如已有的 `audio_processing.py`）。
4. **所有推理任务单 Worker 串行执行**：实际机制是 `app/integrated_app/routes/generate/utils.py` 维护 `_generation_semaphores: dict[str, asyncio.Semaphore]`，**per-engine 信号量默认容量 1**，所有生成路由经 `_execute_generation()` 取用。严禁绕过它直接并发 `await engine.synthesize()`——哪怕 GPU 空闲也不行。多引擎 + 大 batch 并发 GPU VRAM 直接爆 OOM。`--workers` 必须 = 1（见 §6.2）。
5. **所有外部资源（模型权重 / 音频文件 / 缓存文件）必须能离线工作**：不允许运行时请求外部 API 下载模型 / tokenizer / 音色 embedding。所有资源必须在 install.sh / install.bat 阶段一次性拉好。

---

## 4. 测试约定（测试体系 = 6 层 + 分阶段覆盖率路线图）

### 4.1 测试分层表（2026-08-27 按实际目录结构核实）

| 层级 | 测试类型 | 框架 | 实际位置 | 说明 |
|:----:|---------|------|------|------|
| L0 | **Smoke Tests** | pytest + marker | 根目录 `tests/test_*.py` 中打 `@pytest.mark.smoke` 者 | CI 独立触发 `-m smoke`，最小集快速验证构建是否损坏 |
| L1 | 单元测试 | pytest + pytest-asyncio | `tests/*.py`（**根目录 98 个扁平文件**） | 纯函数、utils、Registry（不加载 GPU）。*项目不用 `unit/` 子目录分包，按文件名查找* |
| L2 | 引擎接口测试 | pytest（`@pytest.mark.engine`） | `tests/engines/`（2 个文件） | 引擎契约合规性。**默认跳过，需 GPU + 权重 + `--run-engine`** |
| L3 | Service 层集成测试 | pytest + TestClient | `tests/integration/`（8 个文件） | 生成全流程走历史库 + 串行信号量（用 mock engine，不加载 GPU）。历史库为标准库 sqlite3，非 AioSQLite |
| L4 | API 端点测试 | pytest + httpx.AsyncClient | 根目录扁平文件：`tests/test_api_contract.py`、`tests/test_openai_api.py`、`tests/test_auth.py`、`tests/test_auth_integration.py` | **不存在 `tests/api/` 子目录**。`/health`、`/synthesize`、`/voices`、`/history` HTTP 层 |
| L5 | 安全测试（路径/注入/DoS） | pytest 手工攻击用例 | 根目录扁平文件：`tests/test_path_traversal.py`、`tests/test_security.py`、`tests/test_security_expanded.py`、`tests/test_csrf_integration.py` | **不存在 `tests/security/` 子目录**。path traversal / prompt injection / CSRF / 认证绕过 |
| L6 | E2E / UI 测试 | Playwright | `tests/e2e/`（5 个文件） | 视觉回归 + mock 引擎流 + 截图工具 |
| — | 基准 / 训练 / 前端 | pytest / vitest | `tests/benchmarks/`（3）、`tests/training/`（3）、`tests/frontend/`（JS） | 性能基准、训练流程回归、前端单测；不计入 Python `fail_under` |

**实际资产分布（2026-08-17）**：
- 总测试文件：107 个（92 个扁平 + 5 个子目录）
- 测试函数总数：~1,560 个
- 代码行数：~17,600 行（含注释）
- 覆盖率：**40.11%**（目标分阶段：v1:20% → v2:30% → v3:40% → v4:50% → v5:60%）
- 覆盖率范围：仅统计 `app/integrated_app/`（omit: tests/, templates/, static/）

### 4.2 覆盖率分阶段路线图（诚实设定，逐步提升）
| 阶段 | 目标 fail_under | 说明 |
|------|:---------------:|------|
| 当前 | **40%** ✅ | 已达成！覆盖范围仅 `app/integrated_app/`（见 §4 末尾的 omit 说明），以配置、路由、utils、进度与队列相关模块为主 |
| M1 里程碑 | 50% | 补上所有引擎的**契约合规性测试**（用 mock，契约 Protocol 见 `app/integrated_app/engine_interface.py`）、auth / middleware 行为级验证 |
| M2 里程碑 | 60% | `routes/` 层测试覆盖 70%+ 核心路径、安全回归用例完整化 |
| M3 里程碑 | 70% | 加 SSE 流式响应的时序测试（pytest-asyncio + anyio） |
| 最终目标 | 80% | 加上完整攻击测试回归用例（path traversal / DoS vector / SQL injection / XSS / SSRF），GPU 功能全矩阵覆盖 |

### 4.3 测试命名规范
```python
# 类名：Test + 被测类名（PascalCase）
class TestHistoryDatabase:          # 对应 app/integrated_app/history_db.py 的 HistoryDatabase
    # 方法名：test_<行为>_when_<条件>（snake_case）
    async def test_returns_empty_list_when_db_missing(self):
        ...
```

### 4.4 常用测试命令
```bash
# Smoke tests (fastest, <30s) - verify build is not broken
pytest tests/test_smoke.py -m smoke -v

# Full suite (unit + integration + api + security, exclude e2e/GPU)
pytest tests/ -q --ignore=tests/e2e

# With coverage report (CI default)
pytest tests/ --cov=app/integrated_app --cov-branch --cov-report=term-missing --cov-fail-under=40 -q --ignore=tests/e2e \
  -m "not integration and not gpu and not cuda and not vram"

# Integration tests only (Linux only, no GPU models)
pytest tests/integration/ -v --tb=short -q

# E2E visual regression (requires Playwright + real server)
pytest tests/e2e/test_visual_regression.py -v

# Engine interface tests (need GPU + model weights loaded)
pytest tests/engines/ -v --run-engine

# Performance benchmarks
pytest tests/benchmarks/ -m benchmark

# Run all non-GPU tests with timeout protection
pytest tests/ --timeout=180 -v --tb=short
```

---

## 5. 依赖管理

> **⚠️ 2026-08-27 更正**：本节此前列有 `requirements-dev.txt`（开发依赖）与
> `scripts/generate_lock.py`（生成 lock 文件）。**实测两者均不存在**：
> 仓库根只有 `requirements.txt` 与 `requirements-lock.txt` 两个 requirements 类文件，
> 开发依赖实际声明在 `pyproject.toml` L88-L91 的 `[project.optional-dependencies]`，
> 而 `requirements-lock.txt` 文件头写明由 **pip-compile** 生成，与本仓脚本无关。
> 照旧表操作会新建一套与既有声明源并行的"影子依赖清单"，直接导致版本漂移。

| 文件 | 作用 | 更新方式（实测） |
|------|------|---------|
| `pyproject.toml` | **唯一依赖声明源**：`[project]` 元数据（name `tts-multimodel` / version `2.2.1` / `requires-python >=3.10`）、`dependencies`、`[project.optional-dependencies]`、`[project.scripts] tts-multimodel = "integrated_app.cli:main"`、以及 Ruff / Mypy / Pytest / Coverage 工具配置 | 加/改依赖时**只改这里** |
| `requirements.txt` | 生产依赖清单，**由 pyproject 自动同步生成**（文件头有 `# 由 pyproject.toml 同步生成` 标记），供 `install.bat` / `install.sh` / CI 直接 `pip install -r` 使用 | `python scripts/sync_requirements.py` 重新生成；加 `--check` 只校验一致性，不一致以非零退出 |
| `requirements-lock.txt` | 完整锁定版本（含传递依赖） | 由 `pip-compile --no-annotate --output-file=requirements-lock.txt requirements.txt` 生成（非本仓脚本）；CI 仅断言文件存在（`.github/workflows/ci.yml` L192） |
| **不存在** `requirements-dev.txt` | 开发依赖请走 extras | `pip install -e ".[dev]"` → `pytest` / `pytest-asyncio` / `pytest-cov` / `ruff` / `mypy`；另有 `.[vllm]`、`.[watermark]` |

**改依赖的正确顺序**：编辑 `pyproject.toml` → `python scripts/sync_requirements.py` → 需要锁定时再跑 `pip-compile` → 跑 `python scripts/sync_requirements.py --check` 自检。

---

## 6. 构建 / 启动命令

### 6.1 一键脚本（推荐）
| 平台 | 安装依赖 | 启动服务 |
|------|:--------:|---------|
| **Windows** | `install.bat`（自动装 CUDA版 torch + 剩余依赖） | `start.bat` → 自动打开 `http://127.0.0.1:7869/docs` |
| **Linux/macOS** | `chmod +x install.sh && ./install.sh` | `chmod +x start.sh && ./start.sh` |

### 6.2 手动启动命令
```bash
# 推荐方式（环境初始化 → 配置加载 → 模型完整性检查 → 端口选择 → 启动服务）
python app/clean_launch.py
# → 默认监听 http://127.0.0.1:7869
# → 端口被占用时自动递增尝试 7870、7871…（最多 10 个，见 clean_launch.py L23/L316）
# → 后台线程轮询端口连通性，就绪后额外等 2 秒再自动打开浏览器（auto_open_browser，超时 300s）
#
# ⚠️ 旧文档写的「成功标志：日志最后出现 "All engines loaded. Health endpoint: …"」
#    是虚构文案——该字符串在全仓 .py 中出现 0 次，据此判等会永远误判为"启动失败"。
#    真实的可用性判据只有下条 §6.3 的 HTTP 探针。
# 另一处真实行为：模型文件不完整时 clean_launch 打印
#    "WARNING: Some model files incomplete or missing" 后**仍会继续启动**（仅加载已就绪的引擎），
#    不要把这条 warning 当成启动失败。

# 方式 B（纯 Uvicorn 前台调试，需在 app/ 目录下）
cd app
uvicorn integrated_app.app_server:create_app --factory --host 127.0.0.1 --port 7869 --workers 1
# ⚠️ --workers 只能 = 1！
#    WHY：推理串行靠 routes/generate/utils.py 的 `_generation_semaphores`
#    （per-engine `asyncio.Semaphore`，默认容量 1）。它是**进程内**字典，
#    多 worker 各持一份 → 每个进程独立放行 1 个请求 → 实际并发 = worker 数 → 显存叠加 → OOM。
#    （旧文档归因为"Scheduler 全局单例"，但本仓不存在 TTSScheduler / core.scheduler，
#     结论"必须单 worker"没错，理由要按上面的进程内信号量来理解。）
```

### 6.3 启动后验证

按由浅入深三个端点验证，全部路径已实测（前缀 `/api/system` 与 `/api/model`，**不存在 `/api/v1/tts/*`**）：

```bash
# 1) Liveness —— 极快内存级，不访问 DB/GPU
curl http://127.0.0.1:7869/api/system/health/ping
# → {"status":"ok","ts":1770000000000,"attribution":"TTS_MultiModel © ReSerendipity, Apache 2.0"}

# 2) Readiness —— 模型加载 + DB 连通 + GPU 可用
curl http://127.0.0.1:7869/api/system/health/ready
# → {"status":"ready","model_loaded":true,"db_connected":true,"gpu_available":true,
#    "current_engine":"voxcpm2","uptime_seconds":12.3}
# status 可为 "degraded"：生成仍可用但 history 不入库（DB 异常），此时是黄色告警而非启动失败

# 3) 引擎状态 —— 多引擎的实际证据
curl http://127.0.0.1:7869/api/model/status
# → {"loaded":true,"engine":"voxcpm2","voxcpm2_loaded":true,"indextts2_loaded":false,
#    "queue":"空闲","model_status":"loaded","current_engine":"voxcpm2",
#    "vram_used_mb":6500,"persona_count":12,"lora_enabled":false}
```

**启动成功的判据**：`/api/model/status` 里 `loaded` 为 `true`，且 `voxcpm2_loaded` / `indextts2_loaded`
中**至少一个**为 `true`。

> **多引擎语义的正确理解（2026-08-27 依实际代码澄清，这是本仓终态）**：
> `model_registry.ModelRegistry.EngineName` 枚举当前只有 `VOXCPM2 = "voxcpm2"` 与 `INDEXTTS2 = "indextts2"` 两个值。
> 「多引擎」指**能力注册表与切换契约是多引擎的**——`engine_registry` 可同时登记多个引擎的声明式规格，
> `registry` 同时持有 `voxcpm_model` / `indextts2_engine` 两个独立加载位，可经 `POST /api/model/switch` 切换；
> 但**同一时刻只有一个 `current_engine` 处于激活态**（受显存与串行信号量约束）。
> 旧文档写的 `"engines_loaded": ["cosyvoice2","chattts","f5tts"]` 与「3 个都在即成功」判据均无效：
> 该响应字段在全仓不存在，且 cosyvoice2 / chattts / f5tts 三个引擎已下线。
> 新增第 N 个引擎时，判据不变（"至少一个已加载"），不要改成硬编码引擎个数。

---

## 7. 依赖注入 & 单例获取方式清单

> **⚠️ 2026-08-27 重大更正**：本节此前规定"所有跨层访问必须通过 FastAPI `Depends` 或 `get_xxx()`
> 工厂，禁止直接从模块 import 全局变量实例"，并列出了 `get_settings` / `get_engine_registry` /
> `get_scheduler` / `get_db_pool` / `get_synthesis_service` / `get_history_service` 六个工厂。
> **实测：`Depends(` 在全仓 `app/integrated_app/` 下出现 0 次，上述六个函数有五个不存在**
> （仅 `get_history_db()` 真实存在）。也就是说，旧规则与本仓实际架构方向相反——
> 项目实际采用的正是"模块级单例 + 直接 import"。照旧规则编码会找不到工厂函数，
> 并自行发明一套 DI，与既有 55 个顶层模块的风格冲突。
> 以下按实际代码重写。**若未来真的要引入 DI，请先写一条 ADR 再改本节。**

| 共享状态 | 真实获取方式 | 定义位置 | 作用域 |
|------|--------------------------------|--------|--------|
| 引擎注册表（**能力声明**） | `from .engine_interface import engine_registry` | `app/integrated_app/engine_interface.py:669`<br>`engine_registry: InMemoryEngineRegistry = InMemoryEngineRegistry()` | 模块级单例，`_register_builtin_engines()` 于导入时填充 |
| 模型注册表（**运行时加载态**） | `from .model_registry import registry` | `app/integrated_app/model_registry.py`（`class ModelRegistry` L164） | 模块级单例 + `RLock`；持 `voxcpm_model` / `indextts2_engine` / `current_engine`，批量原子更新走 `set_voxcpm_loaded()` / `set_indextts2_loaded()` |
| 引擎声明式规格 | `app/integrated_app/config_models.py`（与上述注册表协作） | `config_models.py` | 只读声明源 |
| 配置项 | 函数式访问器：`get_project_root()` / `get_pretrained_dir()` / `get_voxcpm2_model_path()` / `get_voxcpm2_asr_path()` / `get_voxcpm2_denoiser_path()` / `get_indextts2_model_path()` | `app/integrated_app/config.py` L94-L169 | 每次调用读取；底层为 YAML + Pydantic 双重加载 |
| 历史库 | `get_history_db() -> HistoryDatabase`；建库 `create_history_db(output_dir)`；释放 `close_all_connections()` | `app/integrated_app/history_db.py` L1984 / L2009 / L2024 | 连接按路径缓存（标准库 sqlite3，`check_same_thread=False`） |
| 推理串行 | `_generation_semaphores`（per-engine `asyncio.Semaphore`，默认容量 1），经 `_execute_generation()` 取用 | `app/integrated_app/routes/generate/utils.py` | 进程内字典，按引擎 key 分桶 |
| 权重完整性 | `integrity_check.py` / `integrity_selfcheck.py` + 清单 `security/integrity_manifest.json` | `app/integrated_app/security/` | 只读 |

**测试中替换共享状态的正确姿势**（因无 DI，不能依赖 `app.dependency_overrides`）：
```python
# 引擎注册表：monkeypatch 模块属性，而不是覆盖 FastAPI 依赖
monkeypatch.setattr("integrated_app.engine_interface.engine_registry", FakeRegistry())

# 历史库：monkeypatch 工厂函数本身
monkeypatch.setattr("integrated_app.history_db.get_history_db", lambda: fake_db)

# 配置路径：patch 访问器
monkeypatch.setattr("integrated_app.config.get_voxcpm2_model_path", lambda: str(tmp_path))
```

> 新增共享状态时，请沿用「模块级单例 + `get_xxx()` 访问器」的既有约定，不要混用 `Depends`，
> 也不要在函数内部重复 `import` 后即时构造实例。

---

## 8. i18n 多语言规范（5 种语言：中 / 繁 / 英 / 日 / 韩）

> **⚠️ 2026-08-27 重大更正**：本节此前的版本描述为「基于 `gettext` + `babel`，翻译文件在
> `common/locale/<lang>/LC_MESSAGES/messages.{po,mo}`，6 步流程含 `update_pot.py` /
> `msgmerge` / `msgfmt` / `check_i18n_keys.py`，且「漏翻译会导致 CI 阻断 PR」」。
> **实测：本仓库没有任何 `.po` / `.mo` / `.pot` 文件，`common/` 目录不存在，
> 两个脚本都不存在，`.pre-commit-config.yaml` 里也没有 i18n 钩子——所谓「CI 阻断」是虚构的门禁。**
> 真实实现是 **JSON 文件驱动**，见下。

### 8.1 翻译机制
- 实现文件：`app/integrated_app/i18n.py`（**纯标准库 + JSON，不用 gettext / 不用 babel**）
- 翻译词表：`app/integrated_app/locales/` 下 **5 个 JSON 文件**
  `zh.json` / `zh-tw.json` / `en.json` / `ja.json` / `ko.json`
- 语言代码 → 文件 的映射由 `i18n.py` 的 `_LANG_FILE_MAP` 维护，含别名归一：
  `zh` / `zh-Hans` → `zh.json`，`zh-TW` / `zh-Hant` → `zh-tw.json`，
  `en` → `en.json`，`ja` → `ja.json`，`ko` → `ko.json`
- 用户可见文本必须走翻译函数，**函数名是 `t()` 而不是 `_()`**：
  ```python
  from .i18n import t
  msg = t("Synthesis completed successfully", lang)     # ✓ 本仓约定
  ```
- 加载结果缓存在模块级 `_I18N_TRANSLATIONS`，按键路径解析（`_resolve_key`）

### 8.2 两层回退机制（缺翻译时不会出现空串）
```
调用 t(key, lang)
    ↓ 在 lang 对应 JSON 里找不到 key（_resolve_key 返回 None）
default 参数（调用方给的兜底串）；未传 default 时回退英文原串
```
> 此前文档写的是「三层回退 + en-US 兜底 + 理论不会到第三步」，实际代码只有上述两层。

### 8.3 新增 / 修改翻译 Key 的标准步骤（4 步）
1. **在代码里用英文原串作 key**：`t("Voice clone failed: source audio too short", lang)`
   （不要写中文当 key，`en.json` 直接原样返回）
2. **手工把该 key 补进其余 4 个 JSON**：`locales/zh.json`、`zh-tw.json`、`ja.json`、`ko.json`
   （结构须与 `en.json` 的层级一致，`_resolve_key` 按点号路径下钻）
3. **自查 5 份词表 key 是否齐**（本仓**暂无**自动校验脚本，需人工比对；
   若要补脚本，请建 `scripts/check_i18n_keys.py` 并同步登记进 §10 钩子表，不要先写文档再写代码）
4. **模板侧取值**：Jinja2 用 `register_i18n_filters(env)` 注册的过滤器；
   前端批量取词用 `get_i18n_json(lang)`；请求语言解析用 `get_lang(request)`

**验证**：启动服务后切语言，确认目标文案随语言变化且控制台无 `_resolve_key` 相关 warning。

---

## 9. Git 提交规范 & 发布流程（release-please 自动发版 ⭐）

### 9.1 Conventional Commits
```
<type>(<scope>): <subject>

<body>

<footer>
```
Type：`feat` / `fix` / `docs` / `style` / `refactor` / `perf` / `test` / `chore` / `ci` / `security`  
Scope 建议：`voxcpm2` / `indextts2` / `engines` / `routes` / `i18n` / `perf` / `security` / `ui`
（历史 scope `cosyvoice2` / `chattts` / `f5tts` / `scheduler` 已随对应实现下线，勿再使用）

### 9.2 ⭐ release-please 自动发版（最重要的一点：**不要手动改版本号！**）
CI workflow（`.github/workflows/release-please.yml`）会自动做所有版本号相关工作：

1. PR 合 main 时，release-please bot 根据 Conventional Commits 自动计算下一个语义化版本
   - 出现 `feat` → +minor（1.0.0 → 1.1.0）
   - 出现 `BREAKING CHANGE:` footer → +major（1.x → 2.0.0）
   - 其他 → +patch（1.0.0 → 1.0.1）
2. release-please 自动开一个 PR（标题如 `release-please--branches--main--components--tts-multimodel`）：
   - 修改 `pyproject.toml` 的 `version` 字段
   - 根据 commit 自动生成 `CHANGELOG.md` 条目（feat → Features、fix → Bug Fixes 自动分类）
3. **你只要做一件事**：review 这个 release-please PR，没问题 Approve + Merge
4. PR 一合 main，CI 自动打 Git Tag（`v1.x.x`）+ 创建 GitHub Release + 上传构建产物

> ⚠️ 严禁做的事：
> - ❌ 不要手动改 `pyproject.toml` 的 `version`
> - ❌ 不要手动写 CHANGELOG（release-please 自动生成，你可以手动补充细节但不要自己写结构）
> - ❌ 不要自己 `git tag`，会和 release-please 的 tag 冲突导致重复版本

### 9.3 本地需要知道的版本号同步位置（万不得已要手动改的话，一起改）
| # | 文件 | 字段 |
|---|------|------|
| 1 | `pyproject.toml` | `[project] version = "x.x.x"` |
| 2 | `config.yaml`（**当前唯一权威版本源**） | 顶层 `version: "x.x.x"`，由 `app/integrated_app/config.py` 读取（旧 `common/config.py` 的 `APP_VERSION` 常量已删除，勿再引用） |
| 3 | `CHANGELOG.md`（release-please 自动维护，手动改的话要对应 `## [x.x.x] - YYYY-MM-DD`） | |

---

## 10. Pre-commit 钩子（提交前自动跑的检查）

> **⚠️ 2026-08-27 更正**：本节此前只列 6 个钩子，且其中 **`black`、`mypy`、`check-i18n-coverage`
> 三个在本仓库 `.pre-commit-config.yaml` 里并不存在**（格式化实际由 `ruff-format` 承担）。
> 同时漏记了 9 个真实生效的钩子——其中 `check-3engine-compat` 是 `always_run` 且挂在
> `pre-push` 阶段的**硬门禁**，不知道它的开发者会误以为提交被卡是环境问题。
> 下表已与配置文件逐条核对（双向一致）。

`.pre-commit-config.yaml` 实际配置 **15 个钩子**，每个 commit 前自动执行：

| 钩子 | 作用 | 备注 |
|------|------|------|
| `ruff` | lint + 自动修可修问题 | 主规则源见 `pyproject.toml [tool.ruff]` |
| `ruff-format` | 代码格式化（**取代 black**） | 与 ruff 配置同源，不会互相打架 |
| `isort` | import 分段与排序 | 规范见 §2.2 |
| `check-ast` | Python 语法可解析 | 拦截写坏的半截文件 |
| `shellcheck` | `*.sh` 脚本静态检查 | 覆盖 `install.sh` / `start.sh` |
| `trailing-whitespace` | 去行尾空白 | — |
| `end-of-file-fixer` | 保证文件以换行结尾 | — |
| `check-yaml` | YAML 语法 | 含 `config.yaml`、workflows |
| `check-json` | JSON 语法 | 含 `locales/*.json`、`integrity_manifest.json` |
| `check-toml` | TOML 语法 | `pyproject.toml` |
| `check-merge-conflict` | 检查遗留 `<<<<<<< HEAD` 冲突标记 | — |
| `check-added-large-files` | 拦截误加大文件 | 权重走 `model/`，不进 git |
| `detect-private-key` | 防止误提交 `.pem` / `id_rsa` | — |
| **`check-3engine-compat`** | 跑 `scripts/check_3engine_compat.py`，检测 VoxCPM2 / IndexTTS2 / 通用引擎三者在当前 Python 环境可 import | ⚠️ **`always_run: true`，且同时挂在 `pre-commit` 与 `pre-push` 两个阶段**——环境装坏时 push 也会被挡 |
| **`forbid-private-key-in-env`** | 禁止把私钥写进 `.env` 类文件 | 与 `init_watermark_key.py` 生成的密钥文件配套约束 |

> **不存在**的钩子（勿再引用）：`black`、`mypy`、`check-i18n-coverage`。
> mypy 目前**不在提交门禁内**；若要恢复，先加进 `.pre-commit-config.yaml` 再回来更新本表。

### 安装（首次 clone 后必执行一次）
```bash
pip install pre-commit
pre-commit install      # 安装到 .git/hooks/pre-commit
# 可选：手动跑一遍所有钩子（确认环境 OK）
pre-commit run -a
```

---

## 11. 安全注意事项

> **⚠️ 2026-08-27 更正**：本节 1/2/3/5 条此前引用了 `common.path_guard.safe_join`、
> `config.yaml → synthesis.max_chars`、`scripts/verify_engine.py`、`configs/model_checksums.yaml`、
> `TTSScheduler 队列满返回 503` —— **这五个标识符/配置项在本仓库全部不存在**。
> 安全类幻影比其他类幻影更危险：它会让开发者误以为防护已由公共模块提供，
> 于是不复用真实实现、也不自建校验，直接裸写 `os.path.join`。以下按实际代码重写。

1. **路径安全（无公共封装，务必复用已有实现）**
   本仓库**没有** `common/path_guard.py`，也**没有** `safe_join()` 函数（全仓搜索零命中）。
   路径防护是**分散内联实现**的，共同手法为 `os.path.realpath()` 解析 + 基目录前缀比对
   （比对基目录时要带尾部 `os.sep`，否则 `/persona` 可被 `/personaxxx` 绕过）。现有实现：

   | 实现 | 位置 | 适用 |
   |---|---|---|
   | `_safe_file_path(root_dir, user_input)` | `app/integrated_app/routes/audio.py:137` | 音频/历史文件读取（含 symlink 攻击防护） |
   | `_validate_path(base_dir, user_path)` | `app/integrated_app/routes/training.py:62` | 训练数据目录 |
   | realpath + `startswith` 前缀比对 | `app/integrated_app/persona_manager.py:168` `:562`、`persona_metadata.py:290` | 音色 wav 与打包元数据 |
   | realpath 前缀比对（禁 symlink 逃出） | `app/integrated_app/routes/generate/voxcpm2/streaming.py:821` | 流式生成写盘 |

   **新增涉及用户输入路径的代码时**：优先复用上述函数；跨模块不便复用时按同一手法实现，
   并**禁止** `os.path.join(base, 用户输入)` 后直接 `open()`。
2. **文本长度与 prompt 注入防护**
   - **不存在** `config.yaml → synthesis.max_chars`。`config.yaml` 顶层键只有
     `version` / `server` / `models` / `ui` / `history` / `logging` / `runtime` / `watermark`。
   - 实际长度上限在 `app/integrated_app/routes/tabs.py` 按引擎给出（8192 / 4096 / 3072 量级），
     分段默认值取 `get_config().generation_defaults.split_max_chars`（读取失败回退 200）。
     **改上限要改这里，不要新增一个文档里的配置键。**
   - 控制 token 白名单在 `app/integrated_app/emotion_control.py:281`：
     `_CHAT_TTS_TAG_PATTERN = re.compile(r"\[(?P<tag>laugh|uv_break|oral_(?P<oral_idx>\d))\]")`
     ——注意是方括号形式 `[laugh]` / `[uv_break]` / `[oral_N]`（此前文档写的 `<laugh>` 是错的），
     且该 token 集源自已下线的 ChatTTS，现为兼容保留。
   - 内容风险审查在 `app/integrated_app/security/content_safety.py`，
     阈值经 Pydantic 配置项 `security.content_safety_threshold` 读取，未配置时回退内置默认值。
3. **模型完整性校验（时机是"下载后"，不是"启动时"）**
   - 校验脚本：`scripts/verify_model_checksums.py`（下载模型后比对 SHA-256，防权重被篡改）
   - 辅助脚本：`scripts/verify_model_weights.py`、`scripts/check_model_paths.py`（防目录移动导致路径漂移）
   - 清单文件：**`app/integrated_app/security/integrity_manifest.json`**（不是 `configs/model_checksums.yaml`，
     后者不存在；`configs/` 目录本身也不存在）
   - 清单生成器：`scripts/generate_integrity_manifest.py`
   - 运行期自检：`app/integrated_app/security/integrity_check.py`、`integrity_selfcheck.py`
   - 权重水印密钥初始化：`scripts/init_watermark_key.py`
   > 此前文档写的"启动时 `scripts/verify_engine.py` 对 3 个引擎权重跑 SHA-256，
   > 不匹配立即终止启动"——脚本名、清单路径、触发时机三者皆错，勿照此排查问题。
4. **网络安全**：生产环境 **绝对不能 `host="0.0.0.0"`**，只监听 `127.0.0.1`
   （`config.yaml → server.host` 已是该值，`server.port` = 7869）。
   外网访问必须套 Nginx（HTTPS + Basic Auth + IP 白名单 + 反向代理限频 `/synthesize`）。
   `config.yaml` 的 `server.ssl.certfile` 目前**未生效**（配置内注释已说明 server 跑 HTTP），
   要上 HTTPS 需在 `app/clean_launch.py` 的 uvicorn 启动处配 `ssl_certfile`/`ssl_keyfile`。
5. **并发与 DoS 防护（实际机制）**
   - **限流**：`app/integrated_app/middleware/rate_limit.py`，超限返回 **`429 Too Many Requests`**。
   - **串行**：per-engine `asyncio.Semaphore`（默认容量 1），见 §3 硬约束 4 与 §7。
   - **`503` 的真实来源不是"队列满"**，而是：
     `EngineNotLoadedError`（引擎未加载，引导用户去 Settings 加载）与
     `InsufficientVRAMError`（CUDA OOM，由 `_run_with_oom_retry` 捕获降级）。
   > 此前文档写的"`TTSScheduler` 队列满（默认 10）返回 503"没有对应实现，
   > 按它去排查 503 会找错方向——遇到 503 请先看是哪个异常类抛的。

---

## 12. 典型 AI 开发场景 SOP（照着做，少踩坑）

<!-- 📥 新SOP追加模板（AI 完成新类型任务后复制填好追加到这里）：
#### SOP-X: [场景名称]
**适用条件**：什么情况下走这个流程
**步骤**：
1. 第一步...
2. 第二步...
3. 第三步...
**验证**：怎么确认操作成功
**关联文件**：
- path/to/file1.py
- path/to/file2.py
-->

#### SOP-1: 添加新的 TTS 引擎（比如新增 XTTS v2）

> **⚠️ 2026-08-27 重大更正**：本节此前 7 步里有 5 步指向不存在的实现——
> `engines/auto_register.py`（**不存在，本仓没有任何目录扫描式自动注册**）、
> `BaseTTSProtocol`（**不存在这个名字**，真实 Protocol 是 `TTSEngine` / `ControllableTTSEngine`）、
> `configs/config.example.yaml`（**`configs/` 目录不存在**，唯一配置文件是根的 `config.yaml`）、
> `core/prompt_templates/`（**不存在**，prompt 逻辑内联在引擎包内）、
> `perf/engine-benchmark.py`（真实文件名是 `perf/generation-benchmark.py`）、
> pytest 参数 `--run-engine`（**不是本仓 marker**，真实 marker 见步骤 6）。
> 照旧步骤执行会在第 3 步就停下来找 `auto_register.py`，然后自行发明一套扫描机制。
>
> **本仓终态是多引擎，注册机制是「显式注册」而非「自动扫描」**——这是有意设计：
> 显式注册让每个引擎的导入策略（立即导入 / 懒导入）在代码里可读、可单独 try/except，
> 自动扫描会把某个引擎的 ImportError 直接升级为全站启动失败。新增引擎请沿用显式注册。

**适用条件**：需要新增一种 TTS 引擎实现，通过统一注册表与 `/api/model/*` 契约暴露

**步骤**：
1. 在 `app/integrated_app/engines/` 下新建引擎实现。两种既有形态任选：
   - 依赖多、需要分文件 → 建**包** `xttsv2/`（参照 `engines/voxcpm2/`：`engine.py` + `prompt.py` + `clone.py` + `design.py` + …）
   - 单文件可容纳 → 建模块 `xttsv2_engine.py`（参照 `engines/indextts2_engine.py` / `engines/voxcpm2_engine.py`）
2. 实现引擎类，满足 `app/integrated_app/engine_interface.py:35` 的 `TTSEngine` Protocol（**结构化子类型，不用显式继承**）；
   若需要情感/时长等可控能力，参照 `ControllableTTSEngine`（L208）。
   类标识用 `name`（Registry key，全局唯一）+ `display_name`。
3. **在 `_register_builtin_engines()`（`engine_interface.py:672`）里显式调用 `engine_registry.register(...)`**。
   按引擎重要性选导入策略（这是既有三引擎的真实分工）：

   | 策略 | 适用 | 写法 |
   |------|------|------|
   | 立即导入 + 懒导入回退 | **核心引擎**（现状：VoxCPM2） | `try: from .engines.xttsv2.engine import XTTSv2Engine; engine_registry.register("xttsv2", engine_class=XTTSv2Engine, ...) except ImportError: engine_registry.register("xttsv2", lazy_module="engines.xttsv2.engine:XTTSv2Engine", ...)` |
   | 纯懒导入 | **可选引擎**（现状：IndexTTS2） | `engine_registry.register("indextts2", lazy_module="...:IndexTTS2Engine", ...)` —— 启动期绝不 import，依赖缺失时不影响核心引擎 |
   | 注释掉注册 | **停用**（现状：dots.tts，见 Gotcha #12） | 保留注册代码但注释 + 写明原因，可逆 |

   `register()` 完整签名（L483）：
   `name, engine_class=None, display_name="", vram_requirement=6.0, lazy_module="", languages=None, supported_features=None, sample_rate=24000, requires_gpu=True, quality="high"`；
   `lazy_module` 格式必须是 `"package.module:ClassName"`。
4. 在 `model_registry.py` 的 `EngineName` 枚举（L70）加值（当前仅 `VOXCPM2="voxcpm2"` / `INDEXTTS2="indextts2"`），
   并补对应的 `_<engine>_loaded` 加载位与 `set_<engine>_loaded()` 原子更新方法；
   同步在 `config.yaml → models.engines.<key>` 加声明式配置（**不要新建 `configs/` 目录**）。
5. Prompt / 模板逻辑内联在引擎包内（参照 `engines/voxcpm2/prompt.py`），**不引入 `core/prompt_templates/` 这类新目录层级**。
6. **测试**：
   - 合规性：`pytest tests/engines/test_protocol_compliance.py -v`（L2 层，校验 Protocol 契约）
   - 新引擎用例放 `tests/engines/test_<engine>_engine.py`
   - 需要真 GPU 的用 marker 标注，**真实可用 marker**：`integration` / `benchmark` / `gpu` / `cuda` / `vram` / `smoke`
     （`pyproject.toml` 的 `markers`；**`--run-engine` 不存在**，不要臆造参数，pytest 会报 unrecognized）
   - 免 GPU 的依赖层兼容性：`python scripts/check_3engine_compat.py`（9 项检测，含 torch/transformers/numpy/pydantic 版本与各引擎可 import 性；该钩子已挂 pre-commit + pre-push，见 §10）
7. （可选）性能基准：`python perf/generation-benchmark.py`（对比既有引擎 RTF），基线产物落 `perf/results/`。

**验证**：启动服务 → `GET /api/model/status` 的 `voxcpm2_loaded` / `indextts2_loaded` 不受新引擎影响且 `loaded` 为 `true`
→ `POST /api/model/switch` 切到新引擎返回 `{"status":"ok","engine":"xttsv2"}`
→ `GET /api/persona/table` 能返回该引擎可见音色（旧文档的 `GET /api/v1/tts/voices` 端点不存在）。

**关联文件**（下例以占位符 `<new_engine>` 表示待新增引擎的注册名，实际请替换）：
- `app/integrated_app/engines/<new_engine>/engine.py`（或 `engines/<new_engine>_engine.py`）
- `app/integrated_app/engine_interface.py`（`_register_builtin_engines()` L672）
- `app/integrated_app/model_registry.py`（`EngineName` L70 / `ModelRegistry` L164）
- `config.yaml`（`models.engines.<key>`）
- `app/integrated_app/config_models.py`（声明式规格）
- `tests/engines/test_<new_engine>_engine.py`
- `scripts/check_3engine_compat.py`

#### SOP-2: 修改现有引擎的生成逻辑（比如调整 IndexTTS2 的情感向量默认值）
**适用条件**：不新增引擎，只调参数 / prompt 逻辑 / 后处理

**步骤**：
1. 改对应引擎的实现与 prompt 模块：`engines/voxcpm2/prompt.py`、`engines/voxcpm2/engine.py`、
   `engines/indextts2_engine.py`，或路由层参数默认值 `routes/generate/{voxcpm2,indextts2,generic}/`
   （**不存在 `core/prompt_templates/<engine>/*.txt`**，模板不是独立 txt 资产）
2. 跑回归：`pytest tests/engines/ -v` + `pytest tests/ -m "not gpu and not cuda and not benchmark" -v`，
   确认接口兼容（`TTSEngine` Protocol 的返回结构字段一个没少）
3. 跑性能对比：改前改后各跑一次 `python perf/generation-benchmark.py`，确认 RTF 劣化不超过 10%
4. **改了契约就要同步全部实现方**：若动了 `engine_interface.py` 的 `TTSEngine` / `ControllableTTSEngine` Protocol
   或 `SynthesisResult` 字段 → **必须同步另一现役引擎**（voxcpm2 ↔ indextts2）与通用引擎 vendor stub，
   并跑 `python scripts/check_3engine_compat.py` 确认三个实现都仍可 import
   （旧文档写"更新 `engines/base.py` 的 Protocol"——**不存在 `engines/base.py`**，Protocol 就在 `engine_interface.py`）

#### SOP-3: 添加新的 API 端点
**适用条件**：在既有 `/api/*` 前缀体系下加新路由

**步骤**：
1. 在 `app/integrated_app/routes/`（或其子包 `routes/generate/`、`routes/system/`）下新建模块。
   **文件名无任何约束**——真实发现契约是**模块级 `router` 变量**：
   `app_server.py` 的 `_discover_routes()`（L179）+ `_auto_discover_routers()`（L220）
   用 `pkgutil.iter_modules` 递归遍历 `routes` 包，凡 `hasattr(mod, "router")` 即收集并挂载。
   （旧文档要求文件名必须以 `*_router.py` 结尾且由 `auto_register` 扫描——**两条都不成立**；
   现有真实文件如 `routes/persona.py`、`routes/model.py`、`routes/system/health.py` 均无 `_router` 后缀。）
2. 文件内定义（**prefix 与 tag 都写在 `APIRouter(...)` 上，本仓未使用 `openapi_tags`，全仓零命中**）：
   ```python
   from fastapi import APIRouter, Request
   from .generate.utils import _generation_semaphores   # 模块级单例直接 import，本仓不用 Depends

   router = APIRouter(prefix="/api/xxx", tags=["xxx"])   # 变量名必须是 router！

   @router.get("/table")
   async def list_xxx(request: Request) -> dict:
       ...
   ```
   既有 prefix 只有：`/api/generate`、`/api/system`、`/api/model`、`/api/persona`、`/api/training`、`/api`（audio）；
   `pages` / `sse` / `tabs` 三个 router 无 prefix。**`/api/v1/tts/*` 前缀全仓零命中，不要新开 v1 前缀**。
3. **不允许**在路由模块里写具体业务逻辑，逻辑下沉到同层能力模块（`history_db.py`、`persona_manager.py`、
   `model_manager.py`、`gpu_backend.py` 等）。
   注意：旧文档写的 `core.services.*` 分层**不存在**，本仓是 `app/integrated_app/` 下的扁平能力模块。
4. 测试放**扁平** `tests/test_xxx_api.py`（用 `TestClient` 或 `httpx.AsyncClient` 发真实请求，
   覆盖状态码、响应字段、错误场景）。
   **`tests/api/` 目录不存在**（见 §4.1）；且本仓无 DI，不能靠 `app.dependency_overrides`，
   共享状态用 `monkeypatch.setattr` 打模块属性（见 §7）。
5. 若需 Swagger 分组说明：tag 描述只能靠 `APIRouter(tags=[...])` + 各端点的 `summary` / `description`
   自行写清（既有做法），**不要去 `create_app()` 里找 `openapi_tags` 列表加描述——那里没有**。

---

## 13. 常见陷阱（Known Gotchas）— 血泪教训汇总

<!-- 📥 新坑追加模板（AI 踩坑后复制填好追加到表格最后）：
| # | 坑点标题 | 触发场景 | 现象/报错 | 正确做法 | 首次发现日期 |
|---|---------|---------|---------|---------|------------|
| X | 简短标题 | 什么操作会触发 | 具体报错信息或现象 | 正确代码/配置/步骤 | YYYY-MM-DD |
-->

| # | 坑点标题 | 触发场景 | 现象/报错 | 正确做法 | 首次发现日期 |
|---|---------|---------|---------|---------|------------|
| 1 | **引擎注册/模型加载不得在 import 阶段触碰 GPU** | 在模块导入时就构造模型实例并加载权重（旧文档误记为 `engines/__init__.py` 里 `EngineRegistry()` + `load_all()`） | import 阶段 CUDA 初始化失败、fork 子进程时 CUDA context 泄漏、测试 import 时也加载 GPU → 本地跑单测 10GB VRAM 先占满 | **2026-08-27 按实际代码更正**：本仓不存在 `api/main.py`、也不存在 `engine_registry.load_all()`。真实的防泄漏机制是：① `engine_interface._register_builtin_engines()` 于导入时**只登记类引用或 `lazy_module` 字符串路径**，不实例化、不加载权重；② 真正的权重加载由 `model_manager` 在 `POST /api/model/load` 或 lifespan 预加载时执行；③ `app_server.py:255` 的 `async def lifespan(app)` 负责生命周期，且仅当 `config.yaml → server.auto_load_model=true` 时才在启动阶段后台预加载（**当前配置为 false**）。新引擎请沿用「懒导入路径注册」，不要在注册时构造模型 | 2026-05-05 |
| 2 | **Uvicorn workers 只能 1，多 worker 必 OOM** | 为了提升并发，`uvicorn ... --workers 4` 或 Gunicorn 多 worker | 每个 worker 都独立初始化引擎注册表 + 各自加载一份模型到 GPU，VRAM 占用 ×worker 数 → 直接 OOM 崩溃 | workers 永远 = 1。**2026-08-27 更正串行机制的表述**：本仓不存在 `TTSScheduler`，真实串行靠 `routes/generate/utils.py` 的 `_generation_semaphores`（per-engine `asyncio.Semaphore`，默认容量 1），它是**进程内**字典，多 worker 各持一份 → 并发度 = worker 数 → 显存叠加。真要水平扩展 → 多实例 + 前面 Nginx 负载均衡（每台机器 GPU 1 份模型） | 2026-05-15 |
| 3 | **SSE StreamingResponse 的生成器不能是 async def** | 在 StreamingResponse(content=xxx) 里传 `async def generate(): async for chunk in ...: yield chunk` | Uvicorn/Lifespan 的事件循环不一致 → `RuntimeError: async generator ignored StopAsyncIteration`，进度流推到 30% 左右就卡死 | content 用普通 `def generate()`，内部 `loop = asyncio.new_event_loop(); loop.run_until_complete(coro)` 或用 `asyncio.run_coroutine_threadsafe(...).result(timeout=30)` 同步取 chunk | 2026-06-01 |
| 4 | **不要在引擎层调 logger 静默兜底**（必须抛异常给上层） | 可选引擎内部 `logger.warning("voice not found, fallback to default")` 然后返回空音频 bytes | 上层路由不知道这次是 fallback 还是正常合成，`result.success` 永远 = True，metrics 和监控都废了 | 引擎层只抛异常。**2026-08-27 更正异常名与出处**：本仓不存在 `VoiceNotFoundError`，异常统一在 `app/integrated_app/exceptions.py`（基类 `TTSError` L49）。音色/Persona 找不到请用 `PersonaNotFoundError`（L139，继承 `PersonaError`），引擎未加载用 `EngineNotLoadedError`（L244），显存不足用 `InsufficientVRAMError`（L99）；由路由层统一 catch → 记日志 + 决定 fallback + 写 metrics | 2026-06-10 |
| 5 | **NumPy / Torch Tensor 不要直接当 FastAPI Response 返回** | `return wav_numpy_array`（shape=(samples,) dtype=np.int16）期望前端能直接当 WAV 播 | FastAPI 的 JSONResponse 会把 numpy 数组尝试序列化成 List[float]，1 秒音频（24000 samples）→ 24000 个 JSON number，10 秒就 24 万 → 响应体 10MB+ 且序列化 2-5 秒 | 先转 WAV 字节：`buf = io.BytesIO(); soundfile.write(buf, wav_np, samplerate=24000, format="WAV"); buf.seek(0); return Response(content=buf.read(), media_type="audio/wav")` | 2026-06-20 |
| 6 | **`training/` 目录绝对不能被 API 启动路径 import** | 在启动链路的模块里 `from training.prepare_dataset import ...`（想复用一些音频切片工具） | API 启动时 import 到 `datasets`、`librosa`、`torchaudio` 等训练专用大依赖 → 冷启动时间 +45 秒 + 多占 2GB RAM，更惨的是训练代码可能改全局 torch dtype 导致推理精度错 | **2026-08-27 更正共享代码归属**：本仓不存在 `common/` 目录（旧写的 `common/audio_utils.py`、`core/services/dataset_helper.py` 均不存在），也没有 `core/` 分层。共享工具的真实归属是 `app/integrated_app/utils.py`，训练侧在 `app/integrated_app/training/`（`data.py` / `accelerator.py` / `config.py` …）。把共享代码抽到 `utils.py`，API 侧与训练侧都从它 import。**该约束当前成立**：实测 `app/integrated_app/` 下（除 `training/` 自身）对 `training` 的顶层 import 为 0 处 | 2026-07-02 |
| 7 | **长文本一次性推理会内存爆 32GB** | 用户提交 5000 字一次性交给单个引擎推理 | 超长中间张量 + attention matrix → 32GB 内存被吃完，OOM 被系统 kill | 路由/引擎层**先按标点与段落分句**再逐句推理，最后拼接。**2026-08-27 更正实现细节**：分句上限取自 `config.yaml → generation_defaults.split_max_chars`（默认 200，允许区间 50–500，见 `routes/system/settings.py` L80）；拼接用 **`numpy.concatenate`**（`soundfile` 根本没有 `concat` 函数，旧写法照抄会 AttributeError），见 `engines/voxcpm2/_base.py` L593、`routes/generate/indextts2/synthesize.py` L354、`routes/generate/voxcpm2/streaming.py` L209。超时预算按「句数 × 单句超时」估算，且要按 §7 的 per-engine 信号量理解排队（不存在 Scheduler 层） | 2026-07-10 |
| 8 | **release-please：绝对不要手动改版本号** | 图省事直接改 `pyproject.toml` 的 `version = "1.1.0"`，合了 main → release-please 的 PR 里版本号冲突 | CI 创建 Release 失败：`tag v1.1.0 already exists`，CHANGELOG 条目重复 | **完全放手给 release-please**：版本号和 CHANGELOG 一律它生成，你只需要 Approve release-please 自动开的 PR。真要手动改就先让 release-please 生成了，再改 release-please PR 里面的内容（合之前改 PR 就好） | 2026-07-20 |
| 9 | **全局底部播放器不显示的根因=前端资源缓存** | 页面加载后生成音频，底部 `global-audio-player` 悬浮条（含波形+可拖动进度条）从未出现，结果卡也没有任何可见播放器 | `base.html` 的 JS/CSS 均带 `?v={{ app_version }}` 缓存参数；若版本号未变，浏览器复用旧版 JS，`window.globalAudioPlayer` 为 undefined，`tts_form.js` 的 `initAutoPlay` 里 `if (audioSrc && window.globalAudioPlayer)` 直接跳过 → 播放器永不弹出 | ① 发布新前端资源时务必递增 `app_version`（或改用内容 hash 命名）并硬刷新测试；② 播放器组件应**自包含**：不依赖全局单例。已落地：`routes/generate/utils.py` 的 `_EMBEDDED_PLAYER_HTML` + `static/js/embedded_player.js` + `static/css/embedded_player.css`，结果卡内嵌波形播放器，全部路由（design/clone/script/streaming/post-process/indextts2）自动生效 | 2026-08-15 |
| 10 | **生成后自动播放 = 隐形播放 + 双音源叠加** | 生成成功自动调 `window.globalAudioPlayer.play()`（`tts_form.js` initAutoPlay / 各页面的 SSE done 分支 / `reprocess.js` / `prompt_continue.html`），同时结果卡内嵌播放器又是独立 Audio 实例 | ① 底部播放器 UI 未显示但音频在播（浏览器标签页喇叭亮），用户"看不见播放器却听见声音"；② 用户再点内嵌播放器 → 两路音频同时播放 | **统一约定：生成/流式/后处理成功后一律不自动播放**，由用户手动点结果卡内嵌播放器（`EmbeddedPlayer.html()`）试听；全局底部播放器仅保留给历史记录/音色库等**用户主动点击**的试听。`showPlayer()` 增加 `playerEl.style.display='flex'` 内联兜底，防止 CSS 未命中时 UI 不可见 | 2026-08-15 |
| 11 | **引擎切换显存预检漏算"卸载当前引擎可释放的显存"** | 当前引擎已占显存时切到另一个引擎（如 VoxCPM2 → dotstts），`_check_vram_prereq` 在卸载前检查"当前空闲显存" | 日志 `[引擎切换] VRAM 检查: 需要 6.0GB, 可用 5.72GB` → `InsufficientVRAMError` 503，明明卸载旧引擎后显存足够，却永远走不到"先卸载再加载"路径；且 `_can_hot_standby` 用 `target*0.8`（乘反低估）会误判热待机 → 不卸载直接加载新引擎 → OOM | `_check_vram_prereq` 把 `registry.current_engine` 的基线 VRAM 计入有效可用（有效可用 = 当前空闲 + 当前引擎占用），只有"卸载后仍装不下"才硬失败；`_can_hot_standby` 改为 `target*1.2`（完整需求+余量），显存不充裕时自然回退到先卸载再加载的传统路径。见 `app/integrated_app/model_manager.py` | 2026-08-15 |
| 12 | **dots.tts 在原生 Windows 上无法安装（pynini 无 Windows 包）** | Windows + 纯 pip（无 conda）环境切换/加载 dotstts 引擎 | `switch_engine` 报 `ENGINE_LOAD_ERROR` 503：`No module named 'dots_tts'`；`pip install dots.tts` 在 `pynini` 步骤源码编译失败（无 Cython/OpenFst，Windows 无官方预编译 wheel） | dots.tts 硬依赖 `WeTextProcessing → pynini`，pynini 仅 Linux/macOS（conda-forge 或 WSL）。Windows 要在用，需装社区 wheel（`SystemPanic/pynini-windows`）或 conda/WSL，且有 transformers 版本冲突风险。本项目已**停用 dotstts**：注释掉 `engine_interface._register_builtin_engines()` 里的注册，引擎不再出现在切换列表，切换以"不支持的引擎"失败而非 503 | 2026-08-15 |
| 13 | **E2E 测试文件被截断损坏导致 CI 门禁必然失败** | 编辑/合并 PR 时文件意外截断（如 `test_screenshot_capture_extended.py` 从 487 行只剩 25 字节） | pytest 收集阶段报 `IndentationError` / `SyntaxError`，e2e.yml workflow 的 required gate 直接失败，所有 PR 合不进 main | ① 提交前本地跑 `pytest tests/e2e/ --collect-only` 验证语法；② 使用 IDE 的 lint-on-save；③ CI 加 pre-commit hook 跑 `python -m py_compile tests/**/*.py`；**2026-08-17 已恢复该文件并修复** | 2026-08-17 |
| 14 | **永真断言与零断言测试制造虚假安全感** | 测试写成 `assert task in set or task not in set`（集合论恒真）、`pass` 函数体、只调用不验证结果 | CI 全部 green 但代码有 bug，因为这些测试**根本不验证任何行为**；覆盖率虚高到 40%+ 仍可能漏核心功能 | ① 审查 assert 语句是否真正验证预期结果；② 用 `pytest --assert=always` 看详细断言输出；③ CI 加 `--strict-markers` 和 ruff flake8-assertive；**2026-08-17 已修复 4 处永真 +4 处零断言** | 2026-08-17 |
| 15 | **认证/安全测试只是构造对象而从未发起真实请求** | `test_auth.py` 只写 `middleware = APIAuthMiddleware(...)` + `assert middleware is not None` | 文档声称 "should reject all authenticated requests" 但**没有一条 HTTP 请求验证**，中间件逻辑是否正确完全未测 | 安全相关测试必须用 `TestClient` 发起真实 HTTP 请求，验证 status_code + response body；禁用 auth/有效 token/无效 token/缺少 header/错误 scheme 全覆盖；**2026-08-17 test_auth.py 重写为 8 个行为级测试** | 2026-08-17 |

---

## 📋 自进化修订记录表（AGENTS.md 进化史）

| 自进化版本 | 日期 | 触发原因 | 更新内容摘要 | 对应项目版本 | 已校验 |
|:---------:|------|---------|------------|:------------:|:-----:|
| v1.0 | 2026-08-10 | 初始建立自进化协议 | 从 TTS_MultiModel 项目健康度评估报告建议补齐：建立自进化协议（5 条铁律 + 自检清单）+ 启动命令章节 + i18n 多语言规范章节（5 种语言 6 步流程 + check_i18n_keys.py）+ 版本号同步清单（万不得已手动改的 3 处）+ 集中化 8 条 Known Gotchas 表格 | v1.0.0  | — |
| v1.1 | 2026-08-15 | 结果音频播放器需求（用户反馈：底部悬浮播放器完全未出现） | 排查根因（`window.globalAudioPlayer` 依赖 + 前端资源缓存 `?v=app_version`）并落地**结果卡内嵌播放器**：新增 `static/js/embedded_player.js` + `static/css/embedded_player.css`，在 `routes/generate/utils.py` 的 `_success_html`/`_partial_success_html` 注入 `_EMBEDDED_PLAYER_HTML`（波形+可拖动进度条+时间，自包含不依赖全局单例），streaming.py 的 SSE 完成片段与 post-process 片段同步注入；新增 Known Gotchas #9 | v1.0.0  | — |
| v1.2 | 2026-08-15 | 用户反馈：生成后自动播放但看不见播放器（tab 喇叭亮）+ 点内嵌播放器后双音源叠加 | 定位：生成成功自动调 `globalAudioPlayer.play()`（多个路径）+ 内嵌播放器独立 Audio 实例 → 隐形播放 + 双音源。修复：**统一不自动播放**（`tts_form.js` initAutoPlay、voice_design/voice_clone 的 SSE done 分支、`reprocess.js`、`prompt_continue.html` 全部移除自动播放，改由内嵌播放器点播；voice_design 的 `EmbeddedPlayer.html()` 工厂方法统一换入逻辑）；`showPlayer()` 加 `display:flex` 内联兜底防 UI 不可见；删除 voice_design 死代码 createWavBlob/playStreamingAudio；新增 Known Gotchas #10 | v1.0.0  | — |
| v1.3 | 2026-08-15 | 用户反馈：切换引擎报 `InsufficientVRAMError` 503，需先卸载旧引擎再加载 | 定位根因：`model_manager._check_vram_prereq` 在卸载前只查"当前空闲显存"，漏算卸载当前引擎可释放的显存；`_can_hot_standby` 用 `target*0.8` 乘反低估会误判热待机。修复：预检把当前引擎基线 VRAM 计入有效可用（仅"卸载后仍装不下"才硬失败）；热待机改为 `target*1.2`（完整需求+余量），显存不充裕时回退到"先卸载再加载"路径避免 OOM；新增 Known Gotchas #11 | v1.0.0  | — |
| v1.4 | 2026-08-15 | 用户切换 dotstts 报 `ENGINE_LOAD_ERROR` 503（`No module named 'dots_tts'`），确认后决定暂不启用 | 确认 dots.tts 在原生 Windows 无法安装（硬依赖 WeTextProcessing → pynini 无 Windows 官方包）。应约在 `engine_interface._register_builtin_engines()` 注释掉 dotstts 注册（停用，可逆），同步更新 `test_dotstts_interface.test_registered_engines`（断言 `"dotstts" not in names`）；新增 Known Gotchas #12 | v1.0.0  | — |
| v1.5 | 2026-08-17 | **测试体系完整性修复**（基于评估报告 P0/P1 级任务全量执行） | ①恢复截断损坏的 test_screenshot_capture_extended.py (486 行)；②重构 test_auth.py 为完整行为级测试（8 个 HTTP 认证场景）；③修复 4 处永真断言 +4+ 处零断言测试；④pytest.raises(Exception)→ValidationError（5 处）；⑤test_progress.py 改用公共接口 get_state()；⑥conftest.py 新增隔离 fixture；⑦CI: ruff 覆盖 tests/、integration 过滤修正、benchmark 回归实化、update-baselines 改 PR；⑧新增 smoke marker 与 test_smoke.py；⑨Known Gotchas #13~#15；AGENTS.md 第 4 节测试章节同步实际结构 + 覆盖率提升至 40% | v1.0.0  | — |
| v1.6 | 2026-08-17 | **安全测试补盲与 M1 里程碑达成** | ①新增 test_security_expanded.py（SQL/XSS/SSRF 盲区补测，8 用例）；②新增 tests/engines/test_protocol_compliance.py（L2 引擎协议合规性测试，7 用例）；③e2e.yml PR 触发补 routes/**；④Security Scan 已纳入 PR 门禁（exit-code:1）。M1 里程碑：覆盖率 40%→目标 50%，L2 引擎测试已实现 | v1.0.0  | — |
| v1.7 | 2026-08-17 | **AGENTS.md 自检：修正陈旧入口引用** | 自检发现第 1 节「代码入口」、硬约束 #3、第 6 节「手动启动命令」仍引用不存在的旧入口 `api/clean_launch.py` / `uvicorn api.main:app`（项目实际结构为 `app/` + `app/integrated_app/`）。修正：入口统一为 `app/clean_launch.py`（推荐）与 `uvicorn integrated_app.app_server:create_app --factory`（app/ 下手动调试）；硬约束 #3 启动路径同步为 `app/clean_launch.py` / `app/integrated_app/app_server.py` | v1.0.0  | — |
| v1.8 | 2026-08-17 | **目录重命名 bin→app** | 将项目目录 `bin` 重命名为 `app` 并同步全部引用（start.bat、pyproject.toml、.github/workflows、AGENTS.md、README.md、Dockerfile、config.yaml、scripts、tests、docs 等），同时将模型目录 token `pretrained_models` 全部替换为 `model`（config.yaml / install.bat / README.md / docker-compose.yml / .gitignore / .pre-commit-config.yaml / scripts / app 内部 / start.sh 等）；同步更新代码入口、覆盖率路径、CI 命令与文档引用 | v1.0.0  | — |

| v1.9 | 2026-08-17 | **统一权重目录 model/** | 解决 `model/` 与 `models/` 重名歧义：`config_models.py` 的 `ModelConfig.base_dir` 默认值 `models` → `model`（运行时实际路径为 `config.py` 的 `PRETRAINED_DIR=ROOT/model`）；5 种语言 locales 的 `download_guide_note` 由 `models/` 目录改为 `model/` 目录；docs/MODEL_DOWNLOADS.md 与 docs/INDEXTTS2_INTEGRATION_GUIDE.md 的权重路径 `models/` → `model/`；第 3 节目录树 `models/` → `model/`；删除遗留的 `models/` 运行时缓存目录 | v1.0.0  | — |
| v1.10 | 2026-08-27 | **家族规范完整性审计（Phase A · T5）：按实测代码修正主干事实** | 依「多引擎为终态」的确认，对 §1/§2/§3/§4/§7/§8/§9.1/§10/§11/§12/§13/自检清单逐节与文件系统、代码实测对账：① 目录树以 `app/integrated_app/` 为根重写，删除顶层 `api/`、`common/`、`core/` 等不存在分层的引用；② 引擎叙述改为实测的显式注册机制（核心引擎立即导入 + 懒导入回退、可选引擎纯懒导入），并说明「多引擎 = 能力注册与切换契约多引擎、同一时刻单一激活引擎」的准确语义；③ §7 撤销「必须用 `Depends` 工厂」规则——本仓 DI 零命中，旧规则与实现方向相反，改为「模块级单例 + `get_xxx()` 访问器 + `monkeypatch` 替换」；④ §8 撤销 gettext/babel 六步流程与「CI 阻断」虚构门禁，改为 JSON 词表 + `t()` + 两层回退 + 4 步流程；⑤ §11 撤销集中式路径守卫、`synthesis.max_chars`、启动时权重校验、队列满返回 503 四项无对应实现的声明，逐条给出真实分散实现与真实异常来源；⑥ §10 钩子表由 6 条补至与配置双向一致的 15 条（含 `always_run` 且挂 push 阶段的兼容性硬门禁），并移除 `black`/`mypy`/i18n 三个假钩子；⑦ §12 三条 SOP 全量重写（自动扫描、`*_router.py` 命名约束、`core.services` 分层、`--run-engine` 参数、示例端点均无实现）；⑧ §13 陷阱 #1/#2/#4/#6/#7 的「正确做法」列改指向真实文件与真实异常类；⑨ 依赖表删除两个不存在的清单/脚本并补正确同步顺序；⑩ README 9 处文档链接重定向至实际所在子目录（含一处文件名拼写错误），CONTRIBUTING 删除重复的 DCO 标题；顶部「对应项目版本」校正，并记录 `config.yaml` 落后一个 patch 的实测版本漂移。**以上各项均只更正与事实不符的表述，未新增任何未实现的承诺** | v2.2.1  | — |
| v1.11 | 2026-08-27 | **家族规范完整性审计（Phase B · B4）：自进化协议打补丁（第 6 条铁律 + 修订表已校验列）** | ① 新增第 6 条铁律「证据绑定（Evidence Binding）」：可执行路径必须当时可验证存在、未实现项须显式标注、禁止虚构 CI 门禁；② 自检清单追加两项：路径真实存在校验（跑 `python scripts/check_spec_refs.py`）与 pre-commit 双向一致校验；③ 修订记录表增加「已校验」列，历史行统一填 `—`（未校验），新条目须填 `✓ (check_spec_refs)` 或 `✗`；④ 本仓新增 `scripts/check_spec_refs.py` 家族审计 wrapper 与 `.github/workflows/docs-consistency.yml`（本地/含审计器环境强校验，纯 CI 环境找不到审计器时降级跳过保持绿）。本行即首个填写「已校验」的条目 | v2.2.1| ✓ (check_spec_refs) |

<!-- 🔄 下次更新 AGENTS.md 时，在上面表格末尾追加新一行，不要删除历史记录 -->


## 路线图落地新增模块（2026-08-18，未提交）
- app/integrated_app/watermark.py — 音频域数字水印（移植自 Image_MultiModel）
- app/integrated_app/spec.py — 领域公式契约层
- app/integrated_app/batch_inference.py — 新增 register_resume_inference_fn / make_checkpoint_resume_handler（断点续跑引擎注册表 + 默认 handler）
- app/integrated_app/app_server.py — lifespan 注册默认 checkpoint_resume_handler
- scripts/init_watermark_key.py、scripts/verify_watermark.py、scripts/render_pages.py
- tests/test_watermark.py、tests/test_spec.py、tests/test_checkpoint_resume.py、tests/test_resume_handler.py、tests/frontend/
- config.yaml 新增 watermark、runtime.task 节

## 📂 文件归档与放置规范（重要：新增文件必须遵守）

> 本仓库目录已于 2026-08-23 系统整理（见 `docs/整理记录_20260823.md`）。后续任何新增/生成文件，**先判断类型再放置**，不要随意丢在仓库根目录或其他位置。

**docs/ 分类（项目文档）**
- `docs/project/`：需求(PRD)、架构、API、技术选型、设计上下文
- `docs/plans/`：实施计划、路线图、指南(Guide)、待办(TASKS)
- `docs/reports/`：评估/审计/安全/测试/优化报告、Lessons
- `docs/repo-analysis/`：仓库学习报告（命名 `{仓库名}_技术学习报告.md`）
- `docs/_devarchive/`：历史/一次性开发产物、交接方案、旧版本文档（**归档而非删除**）

**根目录只允许放置**
- 标准仓库文件：README、LICENSE、NOTICE、CONTRIBUTING、CHANGELOG、AGENTS、SECURITY、USER_AGREEMENT
- 构建与配置：build/gradle、pyproject.toml、config.yaml、requirements*.txt、Dockerfile、docker-compose.yml、.gitignore、.env(.example)、启动脚本(start/install)
- 明确被 build/CI 或文档要求从根目录运行的工具

**禁止事项（防止回归混乱）**
- ❌ 一次性调试脚本/截图/日志/草稿 → 放 `scripts/` 或 `docs/_devarchive/`，绝不堆在根目录
- ❌ 文档散落到 app/tests/perf/personas 等业务目录 → 归入 `docs/` 对应分类
- ❌ 移动/删除 gitignored 运行时/密钥产物（`.env`、`.coverage`、`.server_port`、`perf/monitoring_plan.md`）
- ❌ 删除旧版本文档 → 需要留档移入 `docs/_devarchive/`

> 新增文件前若不确定归属，先询问，不要自作主张放置。
