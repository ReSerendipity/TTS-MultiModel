# TTS_MultiModel AGENTS.md — AI 辅助开发指南

> 🧬 **自进化协议版本**：v1.5  
> 📅 **最后更新日期**：2026-08-17  
> 🎯 **对应项目版本**：v1.0.0（Apache-2.0 开源协议）

---

## ⚠️ 🤖 Agent 行为契约（自进化协议 · 必须严格遵守）

AI Agent 打开本文件后的 **第一件事** 是执行下面的「🧪 自进化自检清单」，并遵守以下 5 条铁律：

### 🔴 5 条自进化铁律
1. **🔄 同步规则（Synchronize First）**：如果发现项目实际情况（目录结构、依赖版本、技术栈、配置文件名等）与本文件描述 **不一致** → **立即更新本文件**，不要只改代码不改 AGENTS.md。这是最高优先级的规则。
2. **📝 坑点累积（Gotchas Accumulation）**：每次修复 Bug / 踩坑后（哪怕是很小的坑），**必须** 追加一条到第 13 节「常见陷阱（Known Gotchas）」，写清楚：触发场景、现象/报错、正确做法、首次发现日期。
3. **📚 SOP 累积（SOP Accumulation）**：每次完成一个「本文件现有 SOP 没覆盖」的典型开发任务后，**必须** 把步骤整理成新 SOP 追加到第 12 节「典型 AI 开发场景 SOP」。
4. **✅ 自检流程（Self-Check on Startup）**：每次打开本文件准备工作前，**必须** 先运行下面的「🧪 自进化自检清单」，逐项核对，有任何一项不符先修正 AGENTS.md 再干活。
5. **🏷️ 版本递增（Version Increment）**：每次更新本文件内容后，**必须** 做三件事：① 文件顶部「自进化协议版本号」+0.1（小改）或 +1.0（大改/框架调整）；② 更新「最后更新日期」；③ 在文件末尾「📋 自进化修订记录表」追加一行记录。

### 🧪 自进化自检清单（每次启动工作前必跑）
- [ ] 目录结构（`api/`、`common/`、`core/`、`engines/`、`routes/`、`training/`）是否和第 3 节模块边界 + 5 条硬约束描述一致？
- [ ] 3 个引擎（CosyVoice2 / ChatTTS / F5-TTS）的 Registry 注册名是否和 `engines/auto_register.py` 实际扫描结果一致？
- [ ] 上次工作是否踩了新坑？如果是，是否已追加到第 13 节 Known Gotchas？
- [ ] 是否新增了路由？如果是，命名是否遵循 `xxxx_router.py` 自动注册规则？
- [ ] 新增的翻译 key 是否已完成 5 种语言同步（见第 8 节 i18n 规范）？
- [ ] 上次更新是否正确递增了自进化协议版本号 + 追加了修订记录表？

---

## 1. 项目概览

> **TTS_MultiModel**：多引擎统一文本转语音（TTS）后端服务。  
> 核心特色：**三引擎热插拔**（CosyVoice2 情感 / ChatTTS 口语化 / F5-TTS 多说话人）+ 统一 API + 单 Worker 串行调度防 GPU OOM。  
> 开源协议：**Apache-2.0**  
> 技术栈：**Python 3.11+ + FastAPI 0.115+ + Uvicorn + Pydantic v2 + AioSQLite + PyYAML + NumPy + SoundFile + Torch 2.x（CUDA）**  
> 代码入口：`api/clean_launch.py`（推荐，含引擎健康预热）  
> 默认端口：**`http://127.0.0.1:7869`**（禁止 0.0.0.0 监听，见第 13 节陷阱）  
> 默认路由前缀：`/api/v1/tts/...`  
> 依赖管理：requirements.txt（生产）+ requirements-dev.txt（开发）+ requirements-lock.txt（锁定）+ pyproject.toml（工具配置）

---

## 2. 代码风格约定

### 2.1 Lint / 格式化 / 类型检查
| 工具 | 配置说明 | 关键规则 |
|------|---------|---------|
| **Ruff** | `pyproject.toml → [tool.ruff]` | `target-version = "py311"`，`line-length = 100` |
| Ruff select | `select = ["E", "F", "I", "W", "UP", "B", "A"]` | UP（Python 3.11 现代化语法）、B（flake8-bugbear）、A（flake8-builtins） |
| Ruff ignore（⚠️ 重要，不要擅自移除） | `ignore = ["E402", "B008", "B017"]` | **为什么有这三个 ignore？每条都有理由**<br>`E402`：引擎 `__init__.py` 需要先 `sys.path.insert(0, engines_dir)` 再 import 第三方模型代码<br>`B008`：Pydantic `Field(default_factory=list)` 和 FastAPI `Depends(engine_registry.get())` 广泛使用可变默认值，这是框架官方推荐用法，不是 bug<br>`B017`：安全测试代码用 `pytest.raises(Exception)` 故意抓所有异常测回退，这是正确的测试策略 |
| **Mypy** | `[tool.mypy] strict = false` | 渐进式策略：`common/`、`core/`、`api/main.py` 开启 `# mypy: strict`，`engines/`、`training/` 因大量第三方模型代码放宽 |
| **命名规则** | 全局 | 类/异常 `PascalCase`，函数/方法/变量 `snake_case`，常量 `UPPER_SNAKE_CASE`，模块 `snake_case.py` |
| 协议/接口名（Protocol） | 补充规则 | 允许 `AbstractXxx`、`XxxProtocol` 两种命名，`engines/base.py` 的 `BaseTTSProtocol` 是项目基准 |

### 2.2 Import 顺序（Ruff `isort` 强制执行）
```python
# 1. Stdlib（import sys / os / asyncio / typing）
# 2. Third-party（import torch / fastapi / numpy）
# 3. Local project（from common.config import settings / from engines.registry import EngineRegistry）
```
> 禁止 `from fastapi import FastAPI, APIRouter, Depends, HTTPException` 一行多 import（Ruff `I` 规则会自动拆成 4 行）。

### 2.3 Docstring
- public 类 / 函数用 **Google 风格** docstring：
  ```python
  def synthesize(text: str, voice: str = "default") -> bytes:
      """调用 TTS 引擎合成音频。

      Args:
          text: 要合成的文本（语言自动检测）
          voice: 音色名，可用值见 GET /api/v1/tts/voices

      Returns:
          bytes: WAV 格式音频数据（16-bit PCM，24kHz 采样率）

      Raises:
          EngineBusyError: 当前队列长度 >= MAX_QUEUE（默认 10）
          VoiceNotFoundError: 指定的 voice 不在引擎支持列表
      """
  ```

---

## 3. 模块边界 & 5 条硬约束（🚫 绝对不能违反）

```
TTS_MultiModel/
├── api/                 ← FastAPI 入口（只组装，不写业务逻辑，不写推理代码）
│   ├── main.py          ← create_app() + lifespan（预加载 3 个引擎到 GPU）
│   ├── clean_launch.py  ← 推荐入口（含 CUDA 检测 + VRAM 预估 + 预热一轮合成）
│   └── routes/          ← 路由：每个模块一个 xxx_router.py，auto_register 自动扫
├── common/              ← 公共基础设施（config / logger / i18n / db）
│   ├── config.py        ← Pydantic Settings + 单例 settings
│   ├── logger.py        ← structlog，所有模块用同一个 logger
│   ├── i18n.py          ← gettext，5 种语言，规范见第 8 节
│   └── db.py            ← AioSQLite 连接池（历史记录）
├── core/                ← 业务逻辑（services + workflows + scheduler）
│   ├── services/        ← SynthesisService / HistoryService / VoiceService
│   ├── scheduler.py     ← 单 Worker 串行调度队列（防止并发推理 GPU OOM）
│   └── prompt_templates/ ← 各个引擎的 prompt 模板（ChatTTS emo_embedding、CosyVoice spk 等）
├── engines/             ← 模型引擎层（接口 + 三个实现 + 自动注册）
│   ├── base.py          ← BaseTTSProtocol（Protocol，含 synthesize() / list_voices() / name ）
│   ├── auto_register.py ← 自动扫描 engines/ 下所有实现并注册到 EngineRegistry
│   ├── cosyvoice2/      ← CosyVoice2 引擎（情感丰富，24kHz WAV）
│   ├── chattts/         ← ChatTTS 引擎（口语化 + 笑声停顿，24kHz PCM）
│   └── f5tts/           ← F5-TTS 引擎（多说话人 + 跨语种克隆，24kHz）
├── routes/              ← FastAPI 路由（和 api/routes/ 联动，auto_register）
├── training/            ← 数据处理 & 微调脚本（独立，不参与 API 启动路径）
│   ├── prepare_dataset.py
│   └── finetune_cosyvoice.sh
├── models/              ← 模型权重（🚫 禁区，AI 不允许自动修改）
├── tests/               ← 测试体系（6 层，第 4 节详细说明）
├── perf/                ← 性能监控脚本（冷启动 / VRAM / 生成基准 / 压力测试 / 报告生成器）
├── scripts/             ← 辅助脚本（模型下载 / integrity check / i18n keys 校验）
├── install.bat / start.bat   ← Windows 一键
├── install.sh  / start.sh    ← Linux/macOS 一键
├── requirements.txt          ← 生产依赖
├── requirements-dev.txt      ← 开发依赖
├── requirements-lock.txt     ← 锁定版本
└── pyproject.toml            ← 元数据 + 工具配置 + pre-commit
```

### 🔴 5 条硬约束（违反一条直接导致生产事故）
1. **`routes/` 目录永远不写业务逻辑**：路由只能做：参数校验（Pydantic）+ 调 `core.services.*` + 返回响应。**路由文件里不允许出现 `torch.*` / `numpy.*` / 任何推理相关代码**。
2. **`engines/` 只是接口适配层**：不做业务编排、不写 DB、不写日志（只抛异常给上层）。引擎实现只做一件事：接收输入 → 调模型 → 返回音频 bytes。
3. **`training/` 完全独立**：API 启动路径（`clean_launch.py` / `api.main`）**绝对不 import training/** 任何模块。如果 training 和 core 共享代码 → 抽到 `common/`。
4. **所有推理任务单 Worker 串行执行**（`core.scheduler.TTSScheduler`，信号量=1）。严禁路由层直接并发 `await engine.synthesize()`——哪怕 GPU 空闲也不行。3 个引擎 + 大 batch 并发 GPU VRAM 直接爆 OOM。
5. **所有外部资源（模型权重 / 音频文件 / 缓存文件）必须能离线工作**：不允许运行时请求外部 API 下载模型 / tokenizer / 音色 embedding。所有资源必须在 install.sh / install.bat 阶段一次性拉好。

---

## 4. 测试约定（测试体系 = 6 层 + 分阶段覆盖率路线图）

### 4.1 6 层测试分层表
| 层级 | 测试类型 | 框架 | 目录 | 说明 |
|:----:|---------|------|------|------|
| L0 | **Smoke Tests** | pytest + marker | `tests/test_smoke.py` | 最小集快速验证（9 tests, <30s），CI 独立触发 `-m smoke` |
| L1 | 单元测试 | pytest + pytest-asyncio | `tests/*.py`（根目录 92 个扁平文件） | 纯函数、utils、Registry、Scheduler（不加载 GPU）。*注：项目未采用 unit/ 子目录分包，按文件名查找* |
| L2 | 引擎接口测试 | pytest（@pytest.mark.engine） | `tests/engines/` | BaseTTSProtocol 合规性（3 引擎都跑一遍），默认跳过 |
| L3 | Service 层集成测试 | pytest + TestClient | `tests/integration/`（6 个文件） | SynthesisService 走 DB + Scheduler 全流程（用 mock engine，不加载 GPU） |
| L4 | API 端点测试 | pytest + httpx.AsyncClient | `tests/api/`, `tests/test_auth*.py` | `/health`、`/synthesize`、`/voices`、`/history` HTTP 层 |
| L5 | 安全测试（路径/注入/DoS） | pytest 手工攻击用例 | `tests/security/`, `tests/test_path_traversal.py` 等 | path traversal / prompt injection / CSRF / 认证绕过 |
| L6 | E2E/UI 测试 | Playwright | `tests/e2e/`（4 个文件） | 视觉回归（5 baseline）、mock 引擎流、截图工具；*注意：test_screenshot_capture_extended.py 曾截断损坏于 2026-08-17 修复* |

**实际资产分布（2026-08-17）**：
- 总测试文件：107 个（92 个扁平 + 5 个子目录）
- 测试函数总数：~1,560 个
- 代码行数：~17,600 行（含注释）
- 覆盖率：**40.11%**（目标分阶段：v1:20% → v2:30% → v3:40% → v4:50% → v5:60%）
- 覆盖率范围：仅统计 `bin/integrated_app/`（omit: tests/, templates/, static/）

### 4.2 覆盖率分阶段路线图（诚实设定，逐步提升）
| 阶段 | 目标 fail_under | 说明 |
|------|:---------------:|------|
| 当前 | **40%** ✅ | 已达成！覆盖 common/、core/scheduler、utils、progress、task_queue 等基础模块 |
| M1 里程碑 | 50% | 补上所有 engines 的 BaseTTSProtocol 合规性测试（用 mock）、auth/middleware 行为级验证 |
| M2 里程碑 | 60% | services + routes 层测试覆盖 70%+ 核心路径、安全回归用例完整化 |
| M3 里程碑 | 70% | 加 SSE 流式响应的时序测试（pytest-asyncio + anyio） |
| 最终目标 | 80% | 加上完整攻击测试回归用例（path traversal / DoS vector / SQL injection / XSS / SSRF），GPU 功能全矩阵覆盖 |

### 4.3 测试命名规范
```python
# 类名：Test + 被测类名（PascalCase）
class TestTTSScheduler:
    # 方法名：test_<行为>_when_<条件>（snake_case）
    async def test_queue_blocks_when_full(self):
        ...
```

### 4.4 常用测试命令
```bash
# Smoke tests (fastest, <30s) - verify build is not broken
pytest tests/test_smoke.py -m smoke -v

# Full suite (unit + integration + api + security, exclude e2e/GPU)
pytest tests/ -q --ignore=tests/e2e

# With coverage report (CI default)
pytest tests/ --cov=bin/integrated_app --cov-branch --cov-report=term-missing --cov-fail-under=40 -q --ignore=tests/e2e \
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
| 文件 | 作用 | 更新频率 |
|------|------|---------|
| `requirements.txt` | 生产依赖（FastAPI / Torch / transformers / numpy / soundfile 等） | 加新依赖时更新 |
| `requirements-dev.txt` | 开发依赖（pytest / ruff / mypy / coverage / pytest-asyncio / pre-commit / httpx） | 加新开发工具时更新 |
| `requirements-lock.txt` | 完整锁定版本（含传递依赖） | 每次改 requirements 后 `python scripts/generate_lock.py` 生成 |
| `pyproject.toml` | 工具配置（Ruff / Mypy / Pytest / pre-commit / 项目元数据） | 改工具参数时更新 |

---

## 6. 构建 / 启动命令

### 6.1 一键脚本（推荐）
| 平台 | 安装依赖 | 启动服务 |
|------|:--------:|---------|
| **Windows** | `install.bat`（自动装 CUDA版 torch + 剩余依赖） | `start.bat` → 自动打开 `http://127.0.0.1:7869/docs` |
| **Linux/macOS** | `chmod +x install.sh && ./install.sh` | `chmod +x start.sh && ./start.sh` |

### 6.2 手动启动命令
```bash
# 推荐方式（含 CUDA 检测 + VRAM 预估 + 引擎预热）
python api/clean_launch.py
# → 监听 http://127.0.0.1:7869
# 成功标志：日志最后出现 "All 3 engines loaded. Health endpoint: GET /api/v1/tts/health"

# 纯 Uvicorn 前台调试
uvicorn api.main:app --host 127.0.0.1 --port 7869 --reload
# ⚠️ --reload 仅限开发！生产禁用（会重复加载 3 个引擎，VRAM 直接翻倍 → OOM）

# 生产守护进程（建议 systemd）
uvicorn api.main:app --host 127.0.0.1 --port 7869 --workers 1
# ⚠️ workers 只能 = 1！Scheduler 是全局单例，多 worker 会绕过串行队列并发推理 → OOM
```

### 6.3 启动后验证
打开 `http://127.0.0.1:7869/docs` → Swagger UI → `GET /api/v1/tts/health` → 返回：
```json
{
  "status": "ok",
  "engines_loaded": ["cosyvoice2", "chattts", "f5tts"],
  "queue_length": 0,
  "vram_used_mb": 12345
}
```
只要 `engines_loaded` 是 3 个都在就表示启动成功。

---

## 7. 依赖注入 & 单例注册表清单

> 所有跨层访问必须通过 FastAPI Depends 或对应的 Registry 单例，**禁止直接从模块 import 全局变量实例**。

| 单例 | 获取方式（Depends / get_xxx()） | 作用域 |
|------|--------------------------------|--------|
| `settings: Settings` | `Depends(get_settings)`（common.config） | app 生命周期全局单例 |
| `registry: EngineRegistry` | `Depends(get_engine_registry)`（engines.registry） | lifespan 启动时实例化，之后只读 |
| `scheduler: TTSScheduler` | `Depends(get_scheduler)`（core.scheduler） | 全局单例，信号量=1 保证串行 |
| `db: AioSQLite Pool` | `Depends(get_db_pool)`（common.db） | 全局单例连接池 |

> 测试中替换单例：`app.dependency_overrides[get_settings] = lambda: MockSettings(...)`

---

## 8. i18n 多语言规范（5 种语言：中 / 繁 / 英 / 日 / 韩）

### 8.1 翻译机制
- 基于标准库 `gettext` + `babel`，用户可见的所有异常 message、日志中可能展示给用户的部分 **必须走 `_()` 包装**
- 翻译文件位置：`common/locale/<lang>/LC_MESSAGES/messages.{po,mo}`

### 8.2 三层回退机制（任何一层缺翻译不会出现英文原串）
```
用户选择语言（如 ko-KR 韩语）
    ↓ 找不到翻译 →
en-US 英文（最后兜底，原串本身就是英文）
    ↓ 还找不到 →
（理论不会到这一步，所有 key 都是英文写的）
```

### 8.3 新增翻译 Key 的标准步骤（6 步，1-6 一步不能落）
1. 在代码里写 `_("Synthesis completed successfully")`（**英文原串 = key**，不要写中文）
2. `python scripts/update_pot.py` → 生成/更新 `common/locale/messages.pot` 模板
3. 为 5 种语言各执行一次 merge：
   ```bash
   for LANG in zh_CN zh_TW en_US ja_JP ko_KR; do
       msgmerge -U common/locale/$LANG/LC_MESSAGES/messages.po common/locale/messages.pot
   done
   ```
4. 每个 `.po` 文件里 `msgstr ""` 填好对应语言的翻译
5. 编译成二进制 `.mo`：
   ```bash
   for LANG in zh_CN zh_TW en_US ja_JP ko_KR; do
       msgfmt common/locale/$LANG/LC_MESSAGES/messages.po -o common/locale/$LANG/LC_MESSAGES/messages.mo
   done
   ```
6. **完整性校验（防止漏翻译）**：`python scripts/check_i18n_keys.py`  
   → 输出：`zh_CN: 128/128 ✓  zh_TW: 128/128 ✓  en_US: 128/128 ✓  ja_JP: 128/128 ✓  ko_KR: 128/128 ✓`  
   → 任何一种语言缺 1 条翻译，脚本退出码非 0，CI 阻断 PR。

---

## 9. Git 提交规范 & 发布流程（release-please 自动发版 ⭐）

### 9.1 Conventional Commits
```
<type>(<scope>): <subject>

<body>

<footer>
```
Type：`feat` / `fix` / `docs` / `style` / `refactor` / `perf` / `test` / `chore` / `ci` / `security`  
Scope 建议：`cosyvoice2` / `chattts` / `f5tts` / `scheduler` / `api` / `i18n` / `perf`

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

### 9.3 本地需要知道的版本号同步位置（万不得已要手动改的话，3 处一起改）
| # | 文件 | 字段 |
|---|------|------|
| 1 | `pyproject.toml` | `[project] version = "x.x.x"` |
| 2 | `common/config.py` | `APP_VERSION: Final[str] = "x.x.x"` |
| 3 | `CHANGELOG.md`（release-please 自动维护，手动改的话要对应 `## [x.x.x] - YYYY-MM-DD`） | |

---

## 10. Pre-commit 钩子（提交前自动跑的检查）

`.pre-commit-config.yaml` 配置了 6 个钩子，每个 commit 前自动执行：
| 钩子 | 作用 |
|------|------|
| `ruff` | 自动 fix 简单 lint 问题 + import 排序 |
| `black` | 自动格式化（确保和 Ruff 配置一致） |
| `mypy`（只跑 common/ + core/） | 类型检查，报错就阻断 commit |
| `check-merge-conflict` | 检查有没有遗留 `<<<<<<< HEAD` 冲突标记 |
| `detect-private-key` | 防止误提交 `.pem` / `id_rsa` 等私钥文件 |
| `check-i18n-coverage`（自定义脚本） | 跑 `scripts/check_i18n_keys.py` 确认翻译全齐 |

### 安装（首次 clone 后必执行一次）
```bash
pip install pre-commit
pre-commit install      # 安装到 .git/hooks/pre-commit
# 可选：手动跑一遍所有钩子（确认环境 OK）
pre-commit run -a
```

---

## 11. 安全注意事项
1. **路径安全**：所有用户输入参与文件路径拼接（上传音频、读取缓存 wav、写临时文件）→ 必须过 `common.path_guard.safe_join(base_dir, user_input)`（已在 common 中提供），**禁止 `os.path.join` + 用户输入的组合**
2. **prompt 注入防护**：TTS 文本最长限制在 `config.yaml → synthesis.max_chars`（默认 5000），超长直接 400。ChatTTS 的控制 token（如 `[uv_break]`、`<laugh>`）必须走白名单过滤，防止用户通过 prompt 控制语速/音色/情感
3. **模型完整性校验**：启动时 `scripts/verify_engine.py` 对 3 个引擎的核心权重跑 SHA-256，和 `configs/model_checksums.yaml` 比对，不匹配立即终止启动
4. **网络安全**：生产环境 **绝对不能 `host="0.0.0.0"`**，只监听 `127.0.0.1`，外网访问必须套 Nginx（HTTPS + Basic Auth + IP 白名单 + WAF 限频 `/synthesize` 接口）
5. **队列防 DoS**：`TTSScheduler` 队列满（默认 10）时立即返回 `503 Service Unavailable`，不要把请求积压在 OS 层（会导致内存爆）

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
**适用条件**：需要新增一种 TTS 引擎实现到 EngineRegistry，通过统一 API 暴露

**步骤**：
1. `engines/` 下新建 `xttsv2/` 目录 + `xttsv2_engine.py`
2. `xttsv2_engine.py` 里实现 `BaseTTSProtocol`（Protocol，不用显式继承），**必须**有这 3 个成员：
   ```python
   class XTTSv2Engine:
       name: ClassVar[str] = "xttsv2"                 # Registry key，全局唯一
       version: ClassVar[str] = "2.0.2"
       async def synthesize(self, req: SynthesisRequest) -> SynthesisResult: ...
       def list_voices(self) -> list[VoiceInfo]: ...
   ```
3. `engines/auto_register.py` 不需要改 → **启动时自动扫描 `engines/*/` 下所有 `*_engine.py` 并注册**
4. 新增配置：`config.yaml` → `engines.xttsv2.model_path` / `device` / `torch_dtype` 等，同步更新 `configs/config.example.yaml`
5. `core/prompt_templates/` 下加 `xttsv2/` 子目录放默认 prompt 模板（如 speaker reference 路径）
6. **测试**：`pytest tests/engines/test_xttsv2_engine.py -v --run-engine`，跑通 `BaseTTSProtocol` 合规性检查
7. （可选）写一个性能基准：`perf/engine-benchmark.py --engine xttsv2` 对比 cosyvoice2 的 RTF

**验证**：启动服务 → `GET /api/v1/tts/health` 的 `engines_loaded` 里出现 `"xttsv2"`，`GET /api/v1/tts/voices` 返回 XTTS 的音色列表

**关联文件**：
- `engines/xttsv2/xttsv2_engine.py`
- `core/prompt_templates/xttsv2/*.txt`
- `configs/config.example.yaml`
- `tests/engines/test_xttsv2_engine.py`

#### SOP-2: 修改现有引擎的生成逻辑（比如调整 ChatTTS emo_embedding）
**适用条件**：不新增引擎，只调参数 / prompt 模板 / 后处理

**步骤**：
1. 改 `core/prompt_templates/chattts/emo_embedding_default.txt`（或对应引擎 py 文件中实现）
2. 跑回归测试：`pytest tests/engines/test_chattts_engine.py -v --run-engine`，确认接口兼容（SynthesisResult 字段一个没少）
3. 跑性能对比：前后各跑一次 `python perf/generation-benchmark.py --engine chattts --runs 5`，确认 RTF 没有劣化超过 10%
4. 如果改了 `BaseTTSProtocol.synthesize()` 的返回结构（比如新增字段）→ **必须同步改另外 2 个引擎**（CosyVoice2 / F5-TTS），并在 `engines/base.py` 更新 Protocol

#### SOP-3: 添加新的 API 端点
**适用条件**：在 `/api/v1/tts/...` 前缀下加新路由

**步骤**：
1. `routes/` 下新建 `xxx_router.py`（文件名 **必须** `*_router.py` 结尾，否则 auto_register 扫不到）
2. 文件内定义：
   ```python
   from fastapi import APIRouter, Depends

   router = APIRouter(prefix="/api/v1/tts/xxx", tags=["tts-xxx"])  # 变量名必须是 router！

   @router.get("/list")
   async def list_xxx(service: SynthesisService = Depends(get_synthesis_service)):
       ...
   ```
3. **不允许** 在 xxx_router.py 里写具体业务逻辑，所有逻辑调 `core.services.*`
4. `tests/api/` 下加 `test_xxx_router.py`：httpx.AsyncClient 跑 GET/POST，状态码、响应体字段、错误场景全覆盖
5. `openapi_tags` 列表（`api/main.py` 的 `create_app()` 里）加对应 tag 描述（如果需要 Swagger 分组显示）

---

## 13. 常见陷阱（Known Gotchas）— 血泪教训汇总

<!-- 📥 新坑追加模板（AI 踩坑后复制填好追加到表格最后）：
| # | 坑点标题 | 触发场景 | 现象/报错 | 正确做法 | 首次发现日期 |
|---|---------|---------|---------|---------|------------|
| X | 简短标题 | 什么操作会触发 | 具体报错信息或现象 | 正确代码/配置/步骤 | YYYY-MM-DD |
-->

| # | 坑点标题 | 触发场景 | 现象/报错 | 正确做法 | 首次发现日期 |
|---|---------|---------|---------|---------|------------|
| 1 | **EngineRegistry 必须在 FastAPI lifespan 中初始化** | 在模块导入时（`engines/__init__.py`）就 `registry = EngineRegistry()` + `load_all()` | import 阶段 CUDA 初始化失败、fork 子进程时 CUDA context 泄漏、测试 import 时也加载 GPU → 本地跑单测 10GB VRAM 先占满 | 所有引擎加载必须在 `api/main.py` → `lifespan(app)` 回调中调用 `await engine_registry.load_all()`，模块级只允许 `registry = EngineRegistry()`（空初始化，不碰 GPU） | 2026-05-05 |
| 2 | **Uvicorn workers 只能 1，多 worker 必 OOM** | 为了提升并发，`uvicorn ... --workers 4` 或 Gunicorn 多 worker | 每个 worker 都独立初始化 EngineRegistry + 各加载一次 3 个引擎到 GPU，VRAM 占用 ×4 → 直接 OOM 崩溃 | workers 永远 = 1，并发通过 `TTSScheduler` 队列串行化。真要水平扩展 → 多实例 + 前面 Nginx 负载均衡（每台机器 GPU 1 份模型） | 2026-05-15 |
| 3 | **SSE StreamingResponse 的生成器不能是 async def** | 在 StreamingResponse(content=xxx) 里传 `async def generate(): async for chunk in ...: yield chunk` | Uvicorn/Lifespan 的事件循环不一致 → `RuntimeError: async generator ignored StopAsyncIteration`，进度流推到 30% 左右就卡死 | content 用普通 `def generate()`，内部 `loop = asyncio.new_event_loop(); loop.run_until_complete(coro)` 或用 `asyncio.run_coroutine_threadsafe(...).result(timeout=30)` 同步取 chunk | 2026-06-01 |
| 4 | **不要在引擎层调 logger**（必须抛异常给上层） | ChatTTS / F5-TTS 引擎内部 `logger.warning("voice not found, fallback to default")` 然后返回空音频 bytes | 上层（routes/services）不知道这次是 fallback 还是正常合成，`result.success` 永远 = True，metrics 和监控都废了 | 引擎层只抛异常：`raise VoiceNotFoundError(f"voice {voice} not supported")`，service 层统一 catch → 记日志 + 决定 fallback + 写 metrics | 2026-06-10 |
| 5 | **NumPy / Torch Tensor 不要直接当 FastAPI Response 返回** | `return wav_numpy_array`（shape=(samples,) dtype=np.int16）期望前端能直接当 WAV 播 | FastAPI 的 JSONResponse 会把 numpy 数组尝试序列化成 List[float]，1 秒音频（24000 samples）→ 24000 个 JSON number，10 秒就 24 万 → 响应体 10MB+ 且序列化 2-5 秒 | 先转 WAV 字节：`buf = io.BytesIO(); soundfile.write(buf, wav_np, samplerate=24000, format="WAV"); buf.seek(0); return Response(content=buf.read(), media_type="audio/wav")` | 2026-06-20 |
| 6 | **`training/` 目录绝对不能被 API 启动路径 import** | `core/services/dataset_helper.py` 里写了 `from training.prepare_dataset import ...`（想复用一些音频切片工具） | API 启动时 import 到 `datasets`、`librosa`、`torchaudio` 等训练专用大依赖 → 冷启动时间 +45 秒 + 多占 2GB RAM，更惨的是训练代码可能改全局 torch dtype 导致推理精度错 | 把共享代码抽到 `common/audio_utils.py`，`training/` 和 `core/` 都从 common import，**API 启动链路的任何模块都不允许出现 `import training.*`** | 2026-07-02 |
| 7 | **ChatTTS 长文本一次性推理会 CPU 内存爆 32GB** | 用户提交 5000 字一次性调 `chattts.synthesize(text)` | `torch.tensor(5000, mel_bins)` 中间张量 + attention matrix → 32GB 内存被吃完，OOM 被系统 kill | service 层自动按标点/段落分句（最长 500 chars / 句）→ 逐句调引擎 → 最后 `soundfile.concat(wav_parts)` 拼接成完整 WAV。Scheduler 层的 timeout 也要按句数 × 单句超时估算 | 2026-07-10 |
| 8 | **release-please：绝对不要手动改版本号** | 图省事直接改 `pyproject.toml` 的 `version = "1.1.0"`，合了 main → release-please 的 PR 里版本号冲突 | CI 创建 Release 失败：`tag v1.1.0 already exists`，CHANGELOG 条目重复 | **完全放手给 release-please**：版本号和 CHANGELOG 一律它生成，你只需要 Approve release-please 自动开的 PR。真要手动改就先让 release-please 生成了，再改 release-please PR 里面的内容（合之前改 PR 就好） | 2026-07-20 |
| 9 | **全局底部播放器不显示的根因=前端资源缓存** | 页面加载后生成音频，底部 `global-audio-player` 悬浮条（含波形+可拖动进度条）从未出现，结果卡也没有任何可见播放器 | `base.html` 的 JS/CSS 均带 `?v={{ app_version }}` 缓存参数；若版本号未变，浏览器复用旧版 JS，`window.globalAudioPlayer` 为 undefined，`tts_form.js` 的 `initAutoPlay` 里 `if (audioSrc && window.globalAudioPlayer)` 直接跳过 → 播放器永不弹出 | ① 发布新前端资源时务必递增 `app_version`（或改用内容 hash 命名）并硬刷新测试；② 播放器组件应**自包含**：不依赖全局单例。已落地：`routes/generate/utils.py` 的 `_EMBEDDED_PLAYER_HTML` + `static/js/embedded_player.js` + `static/css/embedded_player.css`，结果卡内嵌波形播放器，全部路由（design/clone/script/streaming/post-process/indextts2）自动生效 | 2026-08-15 |
| 10 | **生成后自动播放 = 隐形播放 + 双音源叠加** | 生成成功自动调 `window.globalAudioPlayer.play()`（`tts_form.js` initAutoPlay / 各页面的 SSE done 分支 / `reprocess.js` / `prompt_continue.html`），同时结果卡内嵌播放器又是独立 Audio 实例 | ① 底部播放器 UI 未显示但音频在播（浏览器标签页喇叭亮），用户"看不见播放器却听见声音"；② 用户再点内嵌播放器 → 两路音频同时播放 | **统一约定：生成/流式/后处理成功后一律不自动播放**，由用户手动点结果卡内嵌播放器（`EmbeddedPlayer.html()`）试听；全局底部播放器仅保留给历史记录/音色库等**用户主动点击**的试听。`showPlayer()` 增加 `playerEl.style.display='flex'` 内联兜底，防止 CSS 未命中时 UI 不可见 | 2026-08-15 |
| 11 | **引擎切换显存预检漏算"卸载当前引擎可释放的显存"** | 当前引擎已占显存时切到另一个引擎（如 VoxCPM2 → dotstts），`_check_vram_prereq` 在卸载前检查"当前空闲显存" | 日志 `[引擎切换] VRAM 检查: 需要 6.0GB, 可用 5.72GB` → `InsufficientVRAMError` 503，明明卸载旧引擎后显存足够，却永远走不到"先卸载再加载"路径；且 `_can_hot_standby` 用 `target*0.8`（乘反低估）会误判热待机 → 不卸载直接加载新引擎 → OOM | `_check_vram_prereq` 把 `registry.current_engine` 的基线 VRAM 计入有效可用（有效可用 = 当前空闲 + 当前引擎占用），只有"卸载后仍装不下"才硬失败；`_can_hot_standby` 改为 `target*1.2`（完整需求+余量），显存不充裕时自然回退到先卸载再加载的传统路径。见 `bin/integrated_app/model_manager.py` | 2026-08-15 |
| 12 | **dots.tts 在原生 Windows 上无法安装（pynini 无 Windows 包）** | Windows + 纯 pip（无 conda）环境切换/加载 dotstts 引擎 | `switch_engine` 报 `ENGINE_LOAD_ERROR` 503：`No module named 'dots_tts'`；`pip install dots.tts` 在 `pynini` 步骤源码编译失败（无 Cython/OpenFst，Windows 无官方预编译 wheel） | dots.tts 硬依赖 `WeTextProcessing → pynini`，pynini 仅 Linux/macOS（conda-forge 或 WSL）。Windows 要在用，需装社区 wheel（`SystemPanic/pynini-windows`）或 conda/WSL，且有 transformers 版本冲突风险。本项目已**停用 dotstts**：注释掉 `engine_interface._register_builtin_engines()` 里的注册，引擎不再出现在切换列表，切换以"不支持的引擎"失败而非 503 | 2026-08-15 |
| 13 | **E2E 测试文件被截断损坏导致 CI 门禁必然失败** | 编辑/合并 PR 时文件意外截断（如 `test_screenshot_capture_extended.py` 从 487 行只剩 25 字节） | pytest 收集阶段报 `IndentationError` / `SyntaxError`，e2e.yml workflow 的 required gate 直接失败，所有 PR 合不进 main | ① 提交前本地跑 `pytest tests/e2e/ --collect-only` 验证语法；② 使用 IDE 的 lint-on-save；③ CI 加 pre-commit hook 跑 `python -m py_compile tests/**/*.py`；**2026-08-17 已恢复该文件并修复** | 2026-08-17 |
| 14 | **永真断言与零断言测试制造虚假安全感** | 测试写成 `assert task in set or task not in set`（集合论恒真）、`pass` 函数体、只调用不验证结果 | CI 全部 green 但代码有 bug，因为这些测试**根本不验证任何行为**；覆盖率虚高到 40%+ 仍可能漏核心功能 | ① 审查 assert 语句是否真正验证预期结果；② 用 `pytest --assert=always` 看详细断言输出；③ CI 加 `--strict-markers` 和 ruff flake8-assertive；**2026-08-17 已修复 4 处永真 +4 处零断言** | 2026-08-17 |
| 15 | **认证/安全测试只是构造对象而从未发起真实请求** | `test_auth.py` 只写 `middleware = APIAuthMiddleware(...)` + `assert middleware is not None` | 文档声称 "should reject all authenticated requests" 但**没有一条 HTTP 请求验证**，中间件逻辑是否正确完全未测 | 安全相关测试必须用 `TestClient` 发起真实 HTTP 请求，验证 status_code + response body；禁用 auth/有效 token/无效 token/缺少 header/错误 scheme 全覆盖；**2026-08-17 test_auth.py 重写为 8 个行为级测试** | 2026-08-17 |

---

## 📋 自进化修订记录表（AGENTS.md 进化史）

| 自进化版本 | 日期 | 触发原因 | 更新内容摘要 | 对应项目版本 |
|:---------:|------|---------|------------|:------------:|
| v1.0 | 2026-08-10 | 初始建立自进化协议 | 从 TTS_MultiModel 项目健康度评估报告建议补齐：建立自进化协议（5 条铁律 + 自检清单）+ 启动命令章节 + i18n 多语言规范章节（5 种语言 6 步流程 + check_i18n_keys.py）+ 版本号同步清单（万不得已手动改的 3 处）+ 集中化 8 条 Known Gotchas 表格 | v1.0.0 |
| v1.1 | 2026-08-15 | 结果音频播放器需求（用户反馈：底部悬浮播放器完全未出现） | 排查根因（`window.globalAudioPlayer` 依赖 + 前端资源缓存 `?v=app_version`）并落地**结果卡内嵌播放器**：新增 `static/js/embedded_player.js` + `static/css/embedded_player.css`，在 `routes/generate/utils.py` 的 `_success_html`/`_partial_success_html` 注入 `_EMBEDDED_PLAYER_HTML`（波形+可拖动进度条+时间，自包含不依赖全局单例），streaming.py 的 SSE 完成片段与 post-process 片段同步注入；新增 Known Gotchas #9 | v1.0.0 |
| v1.2 | 2026-08-15 | 用户反馈：生成后自动播放但看不见播放器（tab 喇叭亮）+ 点内嵌播放器后双音源叠加 | 定位：生成成功自动调 `globalAudioPlayer.play()`（多个路径）+ 内嵌播放器独立 Audio 实例 → 隐形播放 + 双音源。修复：**统一不自动播放**（`tts_form.js` initAutoPlay、voice_design/voice_clone 的 SSE done 分支、`reprocess.js`、`prompt_continue.html` 全部移除自动播放，改由内嵌播放器点播；voice_design 的 `EmbeddedPlayer.html()` 工厂方法统一换入逻辑）；`showPlayer()` 加 `display:flex` 内联兜底防 UI 不可见；删除 voice_design 死代码 createWavBlob/playStreamingAudio；新增 Known Gotchas #10 | v1.0.0 |
| v1.3 | 2026-08-15 | 用户反馈：切换引擎报 `InsufficientVRAMError` 503，需先卸载旧引擎再加载 | 定位根因：`model_manager._check_vram_prereq` 在卸载前只查"当前空闲显存"，漏算卸载当前引擎可释放的显存；`_can_hot_standby` 用 `target*0.8` 乘反低估会误判热待机。修复：预检把当前引擎基线 VRAM 计入有效可用（仅"卸载后仍装不下"才硬失败）；热待机改为 `target*1.2`（完整需求+余量），显存不充裕时回退到"先卸载再加载"路径避免 OOM；新增 Known Gotchas #11 | v1.0.0 |
| v1.4 | 2026-08-15 | 用户切换 dotstts 报 `ENGINE_LOAD_ERROR` 503（`No module named 'dots_tts'`），确认后决定暂不启用 | 确认 dots.tts 在原生 Windows 无法安装（硬依赖 WeTextProcessing → pynini 无 Windows 官方包）。应约在 `engine_interface._register_builtin_engines()` 注释掉 dotstts 注册（停用，可逆），同步更新 `test_dotstts_interface.test_registered_engines`（断言 `"dotstts" not in names`）；新增 Known Gotchas #12 | v1.0.0 |
| v1.5 | 2026-08-17 | **测试体系完整性修复**（基于评估报告 P0/P1 级任务全量执行） | ①恢复截断损坏的 test_screenshot_capture_extended.py (486 行)；②重构 test_auth.py 为完整行为级测试（8 个 HTTP 认证场景）；③修复 4 处永真断言 +4+ 处零断言测试；④pytest.raises(Exception)→ValidationError（5 处）；⑤test_progress.py 改用公共接口 get_state()；⑥conftest.py 新增隔离 fixture；⑦CI: ruff 覆盖 tests/、integration 过滤修正、benchmark 回归实化、update-baselines 改 PR；⑧新增 smoke marker 与 test_smoke.py；⑨Known Gotchas #13~#15；AGENTS.md 第 4 节测试章节同步实际结构 + 覆盖率提升至 40% | v1.0.0 |

<!-- 🔄 下次更新 AGENTS.md 时，在上面表格末尾追加新一行，不要删除历史记录 -->
