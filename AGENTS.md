# AGENTS.md — TTS MultiModel AI 辅助开发指南

> 本文件为 AI 编程助手（CatPaw / Claude / Cursor / Copilot 等）提供项目开发约定，
> 帮助 AI 理解代码风格、模块边界、测试规范和发布流程，减少返工。

---

## 1. 项目概览

- **技术栈**：Python 3.10+ / FastAPI / PyTorch / Jinja2 / Alpine.js / htmx
- **三引擎**：VoxCPM2（核心）+ IndexTTS 2.0（情感控制）+ dots.tts（48kHz 克隆）
- **代码入口**：`bin/clean_launch.py` → `bin/integrated_app/app_server.py`
- **默认端口**：7869
- **包管理**：pip + `requirements.txt` + `pyproject.toml`

---

## 2. 代码风格

### 2.1 格式化与 Lint（ruff）

配置位于 `pyproject.toml`：

```toml
[tool.ruff]
target-version = "py310"
line-length = 120

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
line-ending = "auto"

[tool.ruff.lint]
select = ["E", "W", "F", "I", "UP", "B", "SIM"]
ignore = ["E501", "B008", "B905"]
```

**关键约定**：
- 双引号字符串
- 4 空格缩进
- 行宽上限 120 字符（E501 已忽略，但应保持合理）
- import 排序：`known-first-party = ["integrated_app"]`
- 每文件豁免：`bin/clean_launch.py` 豁免 E402/I001（启动脚本需延迟导入）

### 2.2 类型检查（mypy）

```toml
[tool.mypy]
python_version = "3.10"
strict = false           # 非严格模式，但关键模块应有类型注解
warn_unused_ignores = true
```

- 公共 API 函数和 Protocol 接口必须有类型注解
- 内部辅助函数可省略返回类型（mypy 可推断）
- `# type: ignore` 必须附带具体 error code（warn_unused_ignores 启用）

### 2.3 命名约定

| 类别 | 约定 | 示例 |
|------|------|------|
| 类 | PascalCase | `IndexTTS2Engine` |
| 函数 | snake_case | `fn_voxcpm_clone` |
| 私有函数 | 前缀 `_` | `_save_output` |
| 常量 | UPPER_SNAKE | `SAVE_DIR` |
| 模块级单例 | 小写 | `registry` |
| Protocol | PascalCase + 后缀 | `TTSEngine`, `EngineRegistry` |

### 2.4 文档字符串

- 所有公共类和函数使用三引号 docstring
- 包含功能说明、Args、Returns、Raises 段
- 中文注释用于架构说明，英文用于 docstring（两者均可，保持文件内一致）

---

## 3. 模块边界

```
bin/integrated_app/
├── engines/              ← 引擎接口抽象层（TTSEngine Protocol + 具体引擎实现）
│   ├── voxcpm2/          ← VoxCPM2 子包（design/clone/ultimate/script/streaming/prompt）
│   ├── indextts2_engine.py  ← IndexTTS2 引擎适配器
│   └── dotstts_engine.py    ← dots.tts 引擎适配器
├── routes/               ← API 路由层（只放路由逻辑，不写业务）
│   ├── generate/         ← TTS 生成路由（按引擎分子目录）
│   ├── system/           ← 系统路由（health/gpu/settings/logs）
│   └── ...
├── training/             ← LoRA 微调训练独立模块（accelerator/data/packers/state/tracker）
├── middleware/           ← HTTP 中间件（CSRF/错误处理/限流/RequestID）
├── security/             ← 完整性检查 + integrity_manifest.json
├── templates/            ← Jinja2 HTML 模板
├── static/               ← 静态资源（CSS/JS/字体）
└── *.py                  ← 50+ 核心模块（config/gpu/history/i18n/mcp/monitor/queue 等）
```

### 3.1 硬约束

| 规则 | 说明 |
|------|------|
| `routes/` 不写业务逻辑 | 路由层只做参数校验 → 调用引擎/服务层 → 返回响应。业务逻辑放 `engines/` 或专用模块 |
| `engines/` 是接口抽象层 | 新引擎必须实现 `TTSEngine` Protocol（`engine_interface.py`），通过 `engine_registry.register()` 注册 |
| `training/` 独立于推理 | 训练模块不直接 import 推理引擎运行时状态，通过 checkpoint 文件解耦 |
| 单 Worker 串行 | 所有模型写操作（load/unload/switch/LoRA）通过 `model_manager` 的 RLock 串行化，防止并发 OOM |
| 离线优先 | 引擎 `load()` 不自动联网下载模型，缺失时抛 `EngineLoadError` 并引导用户手动下载 |

### 3.2 关键单例

| 单例 | 位置 | 作用 |
|------|------|------|
| `registry` | `model_registry.py` | 全局引擎状态（当前引擎/模型引用/加载标志） |
| `engine_registry` | `engine_interface.py` | 引擎类注册表（InMemoryEngineRegistry） |
| `_gen_tracker` | `model_manager.py` | 生成耗时追踪器 |
| `_progress_mgr` | `model_manager.py` | 进度条管理器（SSE 推送） |

---

## 4. 测试约定

### 4.1 测试分层

| 层级 | 位置 | 标记 | 说明 |
|------|------|------|------|
| 单元测试 | `tests/test_*.py` | 无特殊标记 | 90+ 文件，覆盖核心模块 |
| 集成测试 | `tests/integration/` | `@pytest.mark.integration` | GPU 加载/VRAM 切换/离线推理 |
| E2E 测试 | `tests/e2e/` | 无 | 视觉回归测试（截图对比） |
| 基准测试 | `tests/benchmarks/` | `@pytest.mark.benchmark` | pytest-benchmark 格式 |
| 性能脚本 | `perf/` | 无 | 独立 Python 脚本，非 pytest |
| 训练测试 | `tests/training/` | `@pytest.mark.gpu` | LoRA 微调训练 |

### 4.2 新功能测试要求

- **新功能必须补 `test_*.py`**：新增公共函数/类必须同时新增或更新对应测试文件
- **集成测试**放 `tests/integration/`，用 `@pytest.mark.integration` 标记
- **E2E 测试**放 `tests/e2e/`，涉及 UI 变更时更新截图基线
- **性能脚本**：涉及 API 性能变化时更新 `perf/` 脚本

### 4.3 覆盖率目标（分阶段）

```
v1: 20% → v2: 30% → v3: 40%（当前）→ v4: 50% → v5: 60%
```

`pyproject.toml` 中 `fail_under = 40`。新增代码应保持或提升覆盖率。

### 4.4 运行测试

```bash
# 全量测试
pytest

# 仅单元测试（快速）
pytest tests/ -m "not integration and not benchmark and not gpu"

# 集成测试（需 GPU）
pytest tests/integration/ -m integration

# 性能脚本（独立运行）
python perf/cold-start.py
python perf/stress-test.py
```

---

## 5. 发布流程

### 5.1 Conventional Commits

PR 标题和 commit message 必须使用 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

| 类型 | 用途 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat: 添加 dots.tts 流式生成支持` |
| `fix` | Bug 修复 | `fix: 修复引擎切换时显存泄漏` |
| `docs` | 文档变更 | `docs: 更新 ARCHITECTURE.md 引擎流程图` |
| `refactor` | 重构 | `refactor: 提取 generate_with_template 公共模板` |
| `perf` | 性能优化 | `perf: 优化文本分割减少 30% 耗时` |
| `test` | 测试相关 | `test: 补充 IndexTTS2 情感向量控制测试` |
| `chore` | 构建/工具 | `chore: 升级 ruff 到 v0.12.0` |

### 5.2 release-please 自动发版

- GitHub workflow `.github/workflows/release-please.yml` 自动根据合并到 `main` 的 Conventional Commits 生成：
  1. 更新 `CHANGELOG.md`
  2. 提升 `pyproject.toml` 版本号
  3. 创建 GitHub Release
- **不需要手动改版本号**——由 release-please 自动处理

### 5.3 Pre-commit 钩子

```bash
pip install pre-commit
pre-commit install
```

钩子配置在 `.pre-commit-config.yaml`，提交时自动执行：
- ruff lint + format
- isort import 排序
- trailing-whitespace / end-of-file-fixer
- check-yaml / check-json / check-toml
- 3-engine 兼容性检查（local hook）

---

## 6. 常见 AI 辅助开发场景

### 6.1 添加新 TTS 引擎

1. 在 `engines/` 创建 `xxx_engine.py`，实现 `TTSEngine` Protocol
2. 在 `engines/__init__.py` 中 `engine_registry.register("xxx", XXXEngine, ...)`
3. 在 `routes/generate/` 创建对应路由子目录
4. 在 `model_manager.py` 添加 `load_xxx()` 函数
5. 补测试：`tests/test_xxx_engine.py`
6. 更新 `docs/MODEL_DOWNLOADS.md`

### 6.2 修改现有引擎生成逻辑

1. 编辑 `engines/voxcpm2/` 下对应模块（如 `clone.py`）
2. 公共逻辑提取到 `_base.py` 的 `generate_with_template`
3. 路由层 `routes/generate/voxcpm2/` 无需改动（除非参数变化）
4. 补/更新测试

### 6.3 添加 API 端点

1. 在 `routes/` 对应子目录创建路由函数
2. 路由函数只做：参数校验 → 调用引擎 → 返回响应
3. CSRF 自动校验（middleware 统一处理）
4. 补 OpenAPI 文档（FastAPI 自动生成，但需写 docstring）
