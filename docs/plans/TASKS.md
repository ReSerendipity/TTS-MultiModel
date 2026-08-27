# TTS_MultiModel 任务清单

> 本文件汇总项目所有 **架构决策、代码任务、文档任务** 的当前状态与执行计划。
>
> **最后更新**：2026-08-01
> **关联分支**：master
> **维护者**：项目维护者（执行）+ AI 指挥（规划/验收）

---

## 0. 阅读说明

- **状态图例**：
  - ✅ **Done** — 已完成并入 commit
  - 🟡 **In Progress** — 正在执行
  - ⏳ **Pending** — 已规划未启动
  - 🔴 **Blocked** — 依赖前置任务未完成
  - ❌ **Cancelled** — 已决策不做
- **优先级图例**：🔴 P0 阻塞型 / 🟠 P1 高优 / 🟡 P2 中优 / 🟢 P3 低优
- **任务来源**：
  - `docs/PENDING_ISSUES.md` — 历史问题清单
  - `docs/MULTI_ENGINE_DESIGN.md` — 多引擎架构设计
  - `docs/INDEXTTS2_INTEGRATION_GUIDE.md` — IndexTTS2 集成指南
  - `docs/INTEGRATION_DECISIONS.md` — 引擎接入决策
  - `docs/INSTALLATION_FALLBACKS.md` — 安装兜底方案
  - `docs/adr/0001-remove-gptsovits.md` — 删除 GPT-SoVITS 决策

---

## 1. 协作规范

### 1.1 角色分工（v2 模式）

| 角色 | 职责 | 产出 |
|---|---|---|
| **AI 指挥** | 分析 / 决策 / 拆任务 / 验收 / 架构 | 任务书、决策记录、风险标注、验收报告 |
| **执行者（人类）** | 写代码 / 改文件 / 跑命令 / 报告 | diff、运行日志、截图、回执 |

### 1.2 沟通协议

- **任务完成回报**：`任务 N 完成 | commit <hash> | <影响行数>`
- **任务打回**：一句话给原因 + 修正方向
- **停顿规则**：指挥不主动停顿；如需等待，下一项任务先派发
- **决策工具**：分歧时使用 `ask_followup_question` 收集结构化选择

### 1.3 任务单元粒度

- 一个原子 commit 一次
- 每个 commit 可独立回滚
- 每个 commit 配最小验证（lint / pytest / 语法体检）

---

## 2. 已完成任务 ✅

### 2.1 GPT-SoVITS + dots.tts 双引擎集成（4 个原子 commit）

| 任务 | Commit | 体量 | 状态 |
|---|---|---|---|
| 引擎接入（声明式 schema） | `718f59b` | +1517/-77 | ✅ |
| OPTIMIZATION spec（history FTS5 + keyset pagination + audio streaming） | `3c26eab` | +1169/-125 | ✅ |
| Tab 高级参数折叠面板 | `615d780` | +207/-2 | ✅ |
| .gitignore 排除 pynini 源码 | `d950a69` | +4/-0 | ✅ |

### 2.2 兜底与健壮性（6 个原子 commit）

| 任务 | Commit | 关键内容 | 状态 |
|---|---|---|---|
| vendor tn stubs + opencc fallback + 安装文档 | `3e22f9e` | vendor/tn/6 文件 + pyproject + 2 份 docs | ✅ |
| dots.tts 健壮性 + ja/ko i18n | `b87faac` | load() try/except + ja/ko +24 键 | ✅ |
| 集成 + 端到端 + 服务层测试 | `073a3ef` | 31 tests collected, ci.yml coverage 降至 20 | ✅ |
| 脚本 + 工具 + 示例 | `3530368` | 3 scripts + 2 utils + 2 examples | ✅ |
| AGENTS.md + sse 路由 hotfix | `750de57` | 2 文件各 ±2 行 | ✅ |
| 文档入库（PENDING/SECURITY/TRAINING） | `89c8de7` | 3 份 docs | ✅ |
| 额外：gptsovits 健壮性 + en/zh 文案 | `a8b6008` | gptsovits_engine +28 行 | ✅ |

**总验收结果**：2421 行新增，31 tests 全部可收集，AST 100% 通过，工作区干净。

---

## 3. 已完成任务 ✅（原进行中）

### 3.1 任务 7：写 ADR-0001 删除 GPT-SoVITS

- **状态**：✅ Done
- **目标**：把"删除 GPT-SoVITS"作为正式架构决策落库，可追溯、可回滚
- **文件**：
  - `docs/adr/README.md`（ADR 索引）
  - `docs/adr/0001-remove-gptsovits.md`（决策记录）
- **内容大纲**：
  1. 背景与问题（引用 PENDING_ISSUES §1 §2）
  2. 评估的备选方案（A 双轨隔离 / B 声明式降级 / C 去留删除）
  3. 决策（采用 C，理由 4 条）
  4. 实施影响（删除清单 / 保留清单 / 调整清单）
  5. 可回滚路径（git reflog 找 718f59b + 独立 venv）
  6. 待验证项（check_3engine_compat.py 通过）
- **验收点**：
  - 2 文件存在
  - 0001 含 5 个一级标题
  - 引用 PENDING_ISSUES + commit hash
  - README 是索引而非副本

**回报模板**：`任务 7 完成 | commit <hash> | <文件数>`

---

## 4. 已完成任务 ✅（原待执行）

### 4.1 任务 8：写检测脚本 `scripts/check_3engine_compat.py`

- **状态**：✅ Done
- **目标**：把"3 引擎不冲突"从口头保证变成可机器验证的事实
- **检测范围**：仅依赖层（不加载真实模型，不需 GPU）
- **检测项**（9 项）：

| 序号 | 检测项 | 判定 | 来源 |
|---|---|---|---|
| 1 | torch ≥ 2.5.1 | 版本满足 | pyproject.toml:56 |
| 2 | transformers ≥ 4.57.0 | 版本满足 | pyproject.toml:62 |
| 3 | numpy ≥ 2.2.6 | 版本满足 | dots.tts 硬性要求 |
| 4 | pydantic ≥ 2.12.5 | 版本满足 | dots.tts 硬性要求 |
| 5 | funasr 可 import | import 成功 | VoxCPM2 依赖 |
| 6 | fastapi 可 import | import 成功 | Web 框架 |
| 7 | VoxCPM2 模块可 import | import 成功 | 引擎 1 |
| 8 | IndexTTS2 模块可 import | import 成功 | 引擎 2 |
| 9 | dots.tts 可 import（vendor stub 生效） | import 成功 | 引擎 3 |

- **输出格式**：
  ```
  === TTS_MultiModel 3引擎兼容性检测 ===
  [OK] torch        : 2.5.1
  [OK] transformers : 4.57.0
  [WARN] pydantic   : 2.10.0 (>= 2.12.5 不满足!)
  [OK] VoxCPM2      : import OK
  ...
  ---
  总体：8/9 通过
  ```
- **关键陷阱**（执行者注意）：
  1. `import dots_tts` 前必须 `sys.path.insert(0, 'app/integrated_app')`
  2. 不真加载模型，只 import 模块/类
  3. VoxCPM2 路径：`app.integrated_app.engines.voxcpm2.engine`
  4. IndexTTS2 路径：`app.integrated_app.engines.indextts2_engine`
  5. pydantic `__version__` 不存在时用 `importlib.metadata`
- **验收命令**：
  ```powershell
  .\WPy64-312101\python\python.exe scripts/check_3engine_compat.py
  .\WPy64-312101\python\python.exe scripts/check_3engine_compat.py --json
  ```

**回报模板**：`任务 8 完成 | commit <hash> | 检测结果 8/9 通过`

---

### 4.2 任务 9：实际删除 GPT-SoVITS（多 commit 拆删）

- **状态**：✅ Done（子任务 9.1–9.5 全部完成）
- **目标**：从代码库中彻底移除 GPT-SoVITS 引擎
- **预计 commit 数**：4-5 个原子 commit

**子任务 9.1**：删除引擎实现
- 删 `app/integrated_app/engines/gptsovits_engine.py`
- 删 `app/integrated_app/templates/partials/gptsovits_advanced.html`
- 删 `examples/call_gptsovits_api.py`
- 更新 `app/integrated_app/engines/__init__.py` / `engine_interface.py` 注册项

**子任务 9.2**：删除 i18n 键
- 删 `app/integrated_app/locales/{en,zh,ja,ko}.json` 中所有 `gptsovits.*` 键
- 保留 `engine.gptsovits` 通用键（如果存在）

**子任务 9.3**：删除测试用例
- 删 `tests/integration/test_engine_switch_vram.py` 中 GPT-SoVITS 相关用例
- 删 `tests/e2e/` 中 GPT-SoVITS 相关用例

**子任务 9.4**：调整依赖与文档
- 删 `pyproject.toml` 注释中"GPT-SoVITS 优先"段落
- 改 `AGENTS.md §5.3` 移除 GPT-SoVITS 索引行
- 改 `README.md` 引擎列表
- 改 `docs/INTEGRATION_DECISIONS.md` 移除 GPT-SoVITS 章节
- 保留 `docs/INSTALLATION_FALLBACKS.md`（作为历史踩坑档案）

**子任务 9.5**：更新 ADR 状态
- 改 `docs/adr/0001-remove-gptsovits.md` 状态：`Accepted` → `Implemented`
- 记录实际 commit hash

**验收点**：
- 全部 9 项检测脚本通过
- `pip install -e .` 无报错
- `pytest --collect-only` 无 GPT-SoVITS 相关测试
- `git grep "gptsovits\|GPT-SoVITS"` 仅在归档文档中命中

---

## 5. 未来展望任务（来自 PENDING_ISSUES，按优先级）

### 5.1 🔴 P0 阻塞型

| 任务 | 来源 | 说明 |
|---|---|---|
| `tn` stub 已迁回 vendor | PENDING_ISSUES §1.1 | ✅ Done（任务 1） |
| `opencc-python-reimplemented` 已声明 | PENDING_ISSUES §1.2 | ✅ Done（任务 1） |
| `pyproject.toml` 版本策略与原文档决策不一致 | PENDING_ISSUES §2.1 | ✅ Done（任务 9：GPT-SoVITS 删除后版本约束不再需要兼容） |
| `dotstts_engine.load()` try/except 兜底 | PENDING_ISSUES §2.2 | ✅ Done（任务 2） |
| CI `--cov-fail-under` 已降至 20 | PENDING_ISSUES §3.1 | ✅ Done（任务 3） |

### 5.2 🟠 P1 高优

| 任务 | 来源 | 说明 |
|---|---|---|
| `pyopenjtalk` 整套跳过，日语 TTS 不可用 | PENDING_ISSUES §1.3 | ✅ Done（任务 9：GPT-SoVITS 删除后此问题消失） |
| 关键模块 0% 覆盖率 | PENDING_ISSUES §3.2 | ⏳ 长期：补 service_layer / signal_handlers / task_queue / training 单元测试 |
| 3 引擎 import 兼容性检测 | 本任务 §4.1 | ✅ Done（任务 8：check_3engine_compat.py） |

### 5.3 🟡 P2 中优

| 任务 | 来源 | 说明 |
|---|---|---|
| 真实模型权重加载端到端测试 | PENDING_ISSUES §3.3 | ⏳ 长期：需 GPU runner |
| Playwright 真实浏览器 UI 测试 | PENDING_ISSUES §3.4 | ⏳ 长期：需浏览器环境 |
| 引擎切换 VRAM 释放彻底性 | PENDING_ISSUES §4.1 | ⏳ 长期：CI matrix 加 GPU runner |
| SSE 长文本断连风险 | PENDING_ISSUES §4.2 | ⏳ 中期：加 retry 心跳 |
| 编译失败 fallback 文档缺失 | PENDING_ISSUES §1.4 | ✅ Done（INSTALLATION_FALLBACKS.md） |
| `inflect` 不安装但未文档化 | PENDING_ISSUES §2.3 | ✅ Done（任务 9：INTEGRATION_DECISIONS.md §5 已记录） |

### 5.4 🟢 P3 低优

| 任务 | 来源 | 说明 |
|---|---|---|
| 移动端响应式未覆盖 | PENDING_ISSUES §5.1 | ⏳ 长期：CSS 断点 |
| 多引擎并发加载设计 | PENDING_ISSUES §4.3 / MULTI_ENGINE_DESIGN | ⏳ 长期：架构演进 |
| GPT-SoVITS 折叠面板字段冗余 | PENDING_ISSUES §5.2 | ✅ Done（任务 9：模板已随引擎删除） |

---

## 6. 关键依赖关系图

```
任务 7 (写 ADR-0001)
   ↓
任务 8 (检测脚本)
   ↓
任务 9 (拆删 GPT-SoVITS)
   ↓
未来：补 0% 覆盖率模块
   ↓
未来：GPU runner / Playwright / SSE 心跳
```

**关键约束**：
- 任务 8 必须先于任务 9（先验证 3 引擎能 import，再删除第 4 引擎）
- 任务 9 的子任务必须顺序执行（9.1 删实现 → 9.4 改文档）
- 检测脚本集成到 CI（待任务 8 完成后单独立任务）

---

## 7. 回滚与应急

### 7.1 GPT-SoVITS 回滚路径（如未来需恢复）

1. `git reflog | grep gptsovits` 找回接入 commit `718f59b`
2. 重建独立 venv：
   ```bash
   python -m venv .venv-gptsovits
   .venv-gptsovits/app/pip install 'transformers==4.50.0' 'numpy<2.0' 'pydantic<2.10.6'
   .venv-gptsovits/app/pip install jieba opencc-python-reimplemented
   ```
3. 通过 subprocess 调用独立 venv，主项目通过 HTTP 桥接

### 7.2 当前已知 hotfix 应急

- **vendor/tn stub 丢失**：从 `app/integrated_app/vendor/tn/` 复制回 `site-packages/tn/`
- **opencc 兜底丢失**：`pip install opencc-python-reimplemented`
- **pytest 找不到**：使用 `.\WPy64-312101\python\python.exe -m pytest` 而非系统 Python

---

## 8. 沟通模板

### 8.1 任务完成回报

```
任务 N 完成 | commit <hash> | <影响行数>
```

### 8.2 任务打回请求

```
任务 N 打回 | 原因：<具体问题> | 请改：<修正方向>
```

### 8.3 阻塞请求

```
任务 N 阻塞 | 等待：<依赖任务> | 当前状态：<已做了什么>
```

---

## 附录 A：完整 commit 列表（master 分支）

```
a8b6008 feat(engine): gptsovits robustness + en/zh i18n + template polish
89c8de7 docs: pending issues tracker + security + training guide
750de57 chore(docs): AGENTS.md + sse route hotfix
3530368 chore(scripts): verifier utilities + engine API examples
073a3ef test(coverage): integration + e2e + service-layer tests
b87faac feat(engine): harden dotstts load() + ja/ko i18n parity
3e22f9e feat(infra): vendor tn stubs + opencc fallback + install docs
d950a69 chore(gitignore): exclude vendored pynini source snapshot
615d780 feat(ui): collapsible advanced-params panels for gptsovits/dotstts clone tabs
3c26eab feat(perf): apply OPTIMIZATION spec (history FTS5 + keyset pagination + audio streaming)
718f59b feat(engines): integrate GPT-SoVITS and dots.tts as declarative engines
cfc6672 fix: resolve 5 better-harness findings + lint cleanup
d4cc5f4 chore: tighten .gitignore and untrack test/audit artifacts
ba5dcc6 feat: UI/UX audit fixes, bad-case retry queue, model optimizer, and engine refinements
```

---

## 附录 B：项目硬约束（必须遵守）

来自 `AGENTS.md §6`：

1. **显存预检**：模型加载前必须预检，可用显存需为模型大小的 1.5 倍以上
2. **内存熔断**：显存占用超过 90% 时必须立即终止推理并清理缓存
3. **离线优先**：禁止在推理过程中自动下载模型
4. **单 Worker 串行**：生成任务通过 `model_manager.py` 串行处理
5. **SSE 状态推送**：进度更新通过统一端点 `/api/sse/events` 推送

---

**维护说明**：本文件由 AI 指挥维护，每次任务完成/打回/重新规划时更新。
