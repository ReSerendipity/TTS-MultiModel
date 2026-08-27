# TTS_MultiModel 任务清单与长期规划蓝图

> 本文件汇总项目所有 **已完成 / 进行中 / 待执行 / 未来规划** 任务，是项目维护的总入口。
>
> **最后更新**：2026-08-01
> **关联分支**：master
> **维护者**：项目维护者（执行）+ AI 指挥（规划 / 验收）
> **状态**：3 引擎治理完成，依赖已升级到 dots.tts 最低要求，进入"稳定维护 + 长期演进"阶段

---

## 0. 阅读说明

### 0.1 状态图例

| 状态 | 含义 |
|------|------|
| ✅ **Done** | 已完成并入库 commit |
| 🟡 **In Progress** | 正在执行 |
| ⏳ **Pending** | 已规划未启动 |
| 🔴 **Blocked** | 依赖前置任务未完成 |
| ❌ **Cancelled** | 已决策不做 |

### 0.2 优先级图例

| 优先级 | 含义 |
|--------|------|
| 🔴 **P0** | 阻塞型 —— 不解决将导致关键路径失败或运行时报错 |
| 🟠 **P1** | 高优 —— 影响核心功能、生产稳定性或 CI 通过率 |
| 🟡 **P2** | 中优 —— 影响开发体验、非核心路径或长期可维护性 |
| 🟢 **P3** | 低优 —— 改进项、Nice-to-have 或文档完备性 |

### 0.3 文档来源

- `docs/PENDING_ISSUES.md` — 历史问题清单
- `docs/MULTI_ENGINE_DESIGN.md` — 多引擎架构设计
- `docs/INDEXTTS2_INTEGRATION_GUIDE.md` — IndexTTS2 集成指南
- `docs/INTEGRATION_DECISIONS.md` — 引擎接入决策
- `docs/INSTALLATION_FALLBACKS.md` — 安装兜底方案
- `docs/adr/0001-remove-gptsovits.md` — ADR 索引
- `docs/adr/README.md` — ADR 总览

---

## 1. 协作规范（v2 模式）

### 1.1 角色分工

| 角色 | 职责 | 产出 |
|------|------|------|
| **AI 指挥** | 分析 / 决策 / 拆任务 / 验收 / 架构 | 任务书、决策记录、风险标注、验收报告 |
| **执行者（人类）** | 写代码 / 改文件 / 跑命令 / 报告 | diff、运行日志、截图、回执 |

### 1.2 沟通协议

- **任务完成回报**：`任务 N 完成 | commit <hash> | <影响行数>`
- **任务打回**：一句话给原因 + 修正方向
- **停顿规则**：指挥不主动停顿；如需等待，下一项任务先派发
- **决策工具**：分歧时使用结构化问题收集选择

### 1.3 任务单元粒度

- 一个原子 commit 一次
- 每个 commit 可独立回滚
- 每个 commit 配最小验证（lint / pytest / 语法体检）

---

## 2. 已完成任务总览 ✅

### 2.1 GPT-SoVITS + dots.tts 双引擎集成阶段（4 个 commit）

| 任务 | Commit | 体量 | 状态 |
|------|--------|------|------|
| 引擎接入（声明式 schema） | `718f59b` | +1517/-77 | ✅ |
| OPTIMIZATION spec（history FTS5 + keyset pagination + audio streaming） | `3c26eab` | +1169/-125 | ✅ |
| Tab 高级参数折叠面板 | `615d780` | +207/-2 | ✅ |
| .gitignore 排除 pynini 源码 | `d950a69` | +4/-0 | ✅ |

### 2.2 兜底与健壮性阶段（7 个 commit）

| 任务 | Commit | 关键内容 | 状态 |
|------|--------|----------|------|
| vendor tn stubs + opencc fallback + 安装文档 | `3e22f9e` | vendor/tn/ 6 文件 + pyproject + 2 份 docs | ✅ |
| dots.tts 健壮性 + ja/ko i18n | `b87faac` | load() try/except + ja/ko +24 键 | ✅ |
| 集成 + 端到端 + 服务层测试 | `073a3ef` | 31 tests collected | ✅ |
| 脚本 + 工具 + 示例 | `3530368` | 3 scripts + 2 utils + 2 examples | ✅ |
| AGENTS.md + sse 路由 hotfix | `750de57` | 2 文件各 ±2 行 | ✅ |
| 文档入库（PENDING/SECURITY/TRAINING） | `89c8de7` | 3 份 docs | ✅ |
| 额外：gptsovits 健壮性 + en/zh 文案 | `a8b6008` | gptsovits_engine +28 行 | ✅ |

### 2.3 3 引擎治理阶段（5 个 commit）

| 任务 | Commit | 体量 | 状态 |
|------|--------|------|------|
| ADR-0001 + 3-engine 兼容性检测脚本 | `c4a4f4c` | +530 | ✅ |
| 删除 GPT-SoVITS 引擎核心 | `a9e4d07` | -1101 | ✅ |
| 清理注册表/路由/配置/文本前端 | `86d98af` | +14/-85 | ✅ |
| i18n + 文档 + 测试用例收尾 | `4a1678c` | +39/-249 | ✅ |
| TASKS.md 入库（v2 协作规范） | `4a6d5b1` | +332 | ✅ |

### 2.4 关键里程碑数据

| 指标 | 接入前 | 当前 |
|------|--------|------|
| 引擎数 | 2 | 3 |
| 编译失败包 | 0 | 0（vendor stub 兜底） |
| 版本锁冲突 | 0 | 0（升级到 dots.tts 最低要求） |
| 依赖 transformers | 4.43 | **5.14.1** |
| 依赖 numpy | 1.26.4 | **2.4.6**（<2.5 兼容 numba） |
| 依赖 pydantic | 2.10.6 | **2.13.4** |
| 兼容性检测 | 无 | **9/9 通过** |
| ADR 数量 | 0 | 1 |
| 最近 17 个 commit 新增 | — | 约 4500 行净增 |

---

## 3. 当前待执行任务 🟡

### 3.1 任务 15：更新 PENDING_ISSUES.md 反映已解决项

- **目标**：把已完成项从 PENDING 移出，新增"已解决附录"
- **修订要点**：
  - 关闭 §1.1（tn stub vendor 化）
  - 关闭 §1.2（opencc fallback）
  - 关闭 §1.3（pyopenjtalk 随 GPT-SoVITS 删除而消失）
  - 关闭 §2.1（依赖升级到 dots.tts 最低要求）
  - 关闭 §2.2（dotstts load() try/except）
  - 关闭 §3.1（CI 门禁降至 20）
  - §5.2（GPT-SoVITS 字段冗余）→ 随引擎整体删除
- **保留活跃项**：§3.2（0% 覆盖）/ §3.3（GPU 端到端）/ §3.4（Playwright）/ §4.1（VRAM 切换）/ §4.2（SSE 心跳）/ §4.3（多引擎并发）/ §5.1（移动端）/ §5.3（examples 缺）
- **新增 §6**：已解决项附录（ADR + commit 索引 + 升级版本号）
- **验收**：grep 不到"未解决"标记的 P0 项

### 3.2 任务 16：更新 AGENTS.md 同步引擎列表

- **目标**：让项目入口文档反映当前真实状态
- **修订要点**：
  - §5.3 TTS 引擎：删除 GPT-SoVITS 行
  - §5.4 路由结构：删除 gptsovits 路由
  - §5.5 引用：清理 gptsovits 相关
  - 顶部"详细项目背景"链接：新增 `docs/adr/README.md`
  - 关联文档引用：`docs/INDEXTTS2_INTEGRATION_GUIDE.md` 保留
- **验收**：grep "gptsovits\|GPT-SoVITS" 在 AGENTS.md 命中 = 0

### 3.3 任务 17：更新 README.md + CHANGELOG.md

- **目标**：对外文档与用户视角的更新
- **README.md 修订**：
  - 引擎特性对比表：移除 GPT-SoVITS 行
  - 多语言支持矩阵：保留 zh/en/ko/yue（dots.tts 支持）
  - "最近更新"区块：新增 GPT-SoVITS 移除 + ADR-0001 条目
- **CHANGELOG.md 修订**：
  - Unreleased 段新增：
    - **Removed**：GPT-SoVITS 引擎（ADR-0001）
    - **Changed**：依赖升级（transformers / numpy / pydantic）
    - **Added**：`docs/adr/` + `scripts/check_3engine_compat.py`
- **验收**：README 引擎表只有 3 行；CHANGELOG Unreleased 段完整

**回报模板**：`任务 15/16/17 全部完成 | commit <hash1/hash2/hash3> | <行数>`

---

## 4. PENDING_ISSUES 残留项（按优先级）

### 4.1 🔴 P0 阻塞型

**全部已解决**（详见任务 15）。当前无活跃 P0。

### 4.2 🟠 P1 高优

| 项 | 描述 | 影响 | 阶段 |
|----|------|------|------|
| §3.2 关键模块 0% 覆盖率 | service_layer / signal_handlers / task_queue / training 子模块无单元测试 | 重构无回归保护 | 阶段 A |
| 3 引擎真实推理烟雾测试 | 当前仅验证 import，未做端到端推理 | 升级风险难量化 | 阶段 B |

### 4.3 🟡 P2 中优

| 项 | 描述 | 影响 | 阶段 |
|----|------|------|------|
| §3.3 GPU 端到端测试 | CI 离线 CPU 环境不能加载模型 | 显卡/驱动兼容性靠人肉 | 阶段 B |
| §3.4 Playwright UI 测试 | 当前仅 HTTP 层 | 折叠面板交互失效无保护 | 阶段 B |
| §4.1 VRAM 切换彻底性 | 引擎切换时 VRAM 累积膨胀风险 | 违反硬约束 #2 | 阶段 B |
| §4.2 SSE 长文本断连 | 反向代理 60s timeout | 长文本生成卡死 | 阶段 B |
| §2.3 inflect 文档说明 | 决策未文档化 | 新人困惑 | 阶段 A（轻量） |

### 4.4 🟢 P3 低优

| 项 | 描述 | 影响 | 阶段 |
|----|------|------|------|
| §4.3 多引擎并发设计 | MULTI_ENGINE_DESIGN.md 已写但未实施 | 未来扩展受限 | 阶段 C |
| §5.1 移动端响应式 | 折叠面板字段在小屏挤在一行 | 移动端体验差 | 阶段 C |
| §5.3 examples 补充 | 仅 dotstts 有，gptsovits 已删 | 文档完整性 | 阶段 A（轻量） |

---

## 5. 长期规划蓝图（阶段 A-E）

### 5.1 阶段 A：技术债清偿（工期 1-2 周）

**目标**：补齐核心模块单元测试，消除"重构无回归保护"风险

**子任务清单**：

| 编号 | 任务 | 验收 | 工期 |
|------|------|------|------|
| A.1 | `tests/test_service_layer.py`：覆盖请求生命周期编排 | ≥70% 覆盖 | 2 天 |
| A.2 | `tests/test_signal_handlers.py`：覆盖信号注册/清理 | ≥70% 覆盖 | 1 天 |
| A.3 | `tests/test_task_queue.py`：覆盖单 worker 串行逻辑 | ≥70% 覆盖 | 2 天 |
| A.4 | `tests/training/test_data.py`：覆盖 `HFVoxCPMDataset` 边界 | ≥60% 覆盖 | 2 天 |
| A.5 | `tests/training/test_packers.py`：覆盖数据打包 | ≥60% 覆盖 | 1 天 |
| A.6 | 文档：inflect 决策纳入 `docs/DEPENDENCIES_DECISIONS.md` | 文件存在 | 0.5 天 |
| A.7 | examples：补 `examples/dotstts_clone_quick.py` + `examples/batch_clone_all_personas.py` | 文件存在 | 1 天 |

**依赖**：无前置

**验收标准**：
- `pytest --cov-fail-under=40` 通过（从 20 升到 40）
- service_layer / signal_handlers / task_queue 三个模块覆盖率 ≥70%
- 全部 7 个子任务 commit 入库

**价值**：中（技术债，但避免未来重构踩坑）

---

### 5.2 阶段 B：稳定性与可观测性升级（工期 2-3 周）

**目标**：让生产路径有自动化兜底，让长文本生成可靠

**子任务清单**：

| 编号 | 任务 | 验收 | 工期 |
|------|------|------|------|
| B.1 | `scripts/check_3engine_compat.py` 集成到 CI | lint job 后增加 compat job | 0.5 天 |
| B.2 | `tests/integration/test_real_inference_smoke.py`：3 引擎各跑 1 个空推理（CPU） | pytest -m smoke 通过 | 2 天 |
| B.3 | `tests/integration/test_vram_switch.py`：连续切换 5 次验证 VRAM 不累积 | `torch.cuda.memory_allocated()` 前后差 ≤100MB | 2 天 |
| B.4 | `routes/sse.py` 加 `retry: 1000` + 前端 EventSource 断线重连 | UI 长文本 5 分钟不卡 | 2 天 |
| B.5 | 反向代理 timeout 配置文档 | `docs/DEPLOYMENT.md` 新增章节 | 1 天 |
| B.6 | `tests/e2e/test_tab_collapse_interaction.py` 接入 Playwright | CI 浏览器环境可选跑 | 3 天 |
| B.7 | self-hosted GPU runner 配置（GitHub Actions） | 矩阵增加 gpu-runner job | 2 天 |

**依赖**：A 完成（先有单元测试保护再重构）

**验收标准**：
- CI 流水线 6 个 job：lint / compat / unit / integration / e2e / gpu-smoke
- 3 引擎真实推理烟雾测试通过
- SSE 断线重连演示视频 5 分钟无卡顿

**价值**：高（直接降低生产事故率）

---

### 5.3 阶段 C：架构演进（工期 4+ 周）

**目标**：把 MULTI_ENGINE_DESIGN.md 落地，让 3 引擎可并发加载

**子任务清单**：

| 编号 | 任务 | 验收 | 工期 |
|------|------|------|------|
| C.1 | 重构 `model_registry.py` 为 `MultiEngineRegistry` | 向后兼容 `current_engine` | 1 周 |
| C.2 | 扩展 `task_queue.py` 为每引擎独立队列 | SSE 事件携带 engine 字段 | 1 周 |
| C.3 | UI 适配：多引擎状态显示 + 引擎指定 | 切换引擎无需等待 | 1 周 |
| C.4 | 移动端响应式 CSS 断点 | `@media (max-width: 600px)` 生效 | 0.5 周 |
| C.5 | 文档：更新 `docs/PROJECT_ARCHITECTURE.md` | 引用 MULTI_ENGINE_DESIGN 实施状态 | 0.5 周 |

**依赖**：B 完成（先有 CI 保护再做架构大改）

**验收标准**：
- 2 引擎同时加载到 GPU 不 OOM（多卡环境）
- 引擎切换延迟 < 5 秒（含模型预热）
- 移动端访问 WebUI 折叠面板字段单列显示

**价值**：中（未来扩展，但当前 3 引擎用不上并发）

---

### 5.4 阶段 D：CI/CD 体系化（持续）

**目标**：建立完善的 CI matrix，让所有 PR 都有充分验证

**子任务清单**：

| 编号 | 任务 | 验收 | 工期 |
|------|------|------|------|
| D.1 | CI matrix 多 Python 版本（3.10 / 3.11 / 3.12 / 3.13） | 4 个 Python 版本全通过 | 2 天 |
| D.2 | CI matrix 多操作系统（Windows / Linux / macOS） | 3 个 OS 全通过 | 3 天 |
| D.3 | CI cache 优化：vendor / modelscope 缓存 | lint+unit 时间 < 5 分钟 | 1 天 |
| D.4 | pre-commit hook：ruff + format + check_3engine | 本地提交前自动检查 | 1 天 |
| D.5 | release-please 自动 CHANGELOG + version bump | 标签推送自动发布 | 2 天 |
| D.6 | 性能基准 CI：每次 PR 跑 `benchmarks/test_generation_bench.py` | 性能回归自动告警 | 2 天 |

**依赖**：无强依赖（与 A/B/C 并行）

**验收标准**：
- CI 总时长 ≤ 15 分钟
- 4 Python × 3 OS = 12 个矩阵全绿
- 性能回归阈值 ±10% 内自动通过

**价值**：高（CI 信心 + 性能可见性）

---

### 5.5 阶段 E：下一代架构与产品化（工期 4-8 周）

**目标**：从"多引擎 TTS 工具"演进为"产品化 TTS 平台"，覆盖实时性、智能化、工程化、生态化

**子任务清单**：

| 编号 | 任务 | 验收 | 工期 | 前置 |
|------|------|------|------|------|
| E.1 | Streaming 实时 TTS | FastAPI `StreamingResponse` 端点 + 4 语言 i18n 键齐 | 1 周 | 无 |
| E.2 | LLM-driven 提示词编排 | `LLMProvider` 抽象 + 3 个内置模板 + 1 个 OpenAI 兼容实现 | 2 周 | 无 |
| E.3 | TypeScript 类型化（渐进式） | `tsconfig.json` + 28 JS 全部加 `.d.ts` + `tsc --noEmit` 0 错误 | 3 周 | 无 |
| E.4 | 插件化架构 | `engine_registry` 加 hook + 第三方插件示例 + `plugin.yaml` schema | 2 周 | C 完成 |

**依赖**：
- E.1 无前置（独立）
- E.2 无前置（独立）
- E.3 无前置（独立）
- E.4 依赖 C 完成（`MultiEngineRegistry` 已支持多引擎）

**验收标准**：
- 4 语言 i18n 包含 `streaming.*` / `llm.*` / `plugin.*` 相关键
- LLM provider 可配置：`config.yaml` 增加 `llm.provider: openai|anthropic|ollama`
- `npx tsc --noEmit` 0 错误
- 第三方插件可独立注册到 `engine_registry`（`pip install tts-plugin-foo` 即生效）

**价值**：
- E.1 高（实时性是 TTS 核心体验，FastAPI 已有 SSE 但缺 chunked audio）
- E.2 高（LLM 编排降低用户门槛，是差异化功能）
- E.3 中（工程化收益渐进，3 周投入获得 IDE 提示 + 重构安全）
- E.4 中（生态扩展，但当前 3 引擎够用）

**关键风险**：
- E.3 TS 与现有 28 个原生 JS 不兼容（需渐进改造，保留 .js 兼容）
- E.4 插件化破坏性大（必须保持向后兼容 `current_engine`）

**质量基线（2026-08-01 快照）**：
- 3 引擎兼容性检测：`scripts/check_3engine_compat.py` **9/9 通过**（WinPython 解释器）
- pytest 离线：**353 passed, 76 skipped, 0 failed, 44.30s**
- 测试覆盖率：**26.05%**（超 CI 门禁 20% 6 个百分点）
- 详见 [`docs/STAGE_E_QUALITY_REPORT.md`](STAGE_E_QUALITY_REPORT.md)

---

### 5.6 阶段依赖图

```
阶段 A (技术债清偿)
  ↓
阶段 B (稳定性升级)
  ↓
阶段 C (架构演进)
  ↑
阶段 D (CI/CD 体系化) ─── 并行于 A/B/C
```

**关键路径**：A → B → C（不可乱序）
**并行机会**：D 可从阶段 A 完成后并行启动

---

## 6. 全局依赖关系图

```
当前（任务 15/16/17）
  ↓
阶段 A: 技术债清偿
  ↓
阶段 B: 稳定性升级
  ↓
阶段 C: 架构演进
  ↓ ↑
阶段 D: CI/CD 体系化 ── 并行
```

**关键约束**：
- 阶段 A 必须先于 B（先有单元测试再重构）
- 阶段 B 必须先于 C（先稳定再架构大改）
- 阶段 D 可与 A/B/C 并行（CI 改进是横向能力）

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
4. 详见 `docs/adr/0001-remove-gptsovits.md` 决策记录

### 7.2 当前 hotfix 应急

| 故障 | 修复 |
|------|------|
| vendor/tn stub 丢失 | 从 `app/integrated_app/vendor/tn/` 复制回 `site-packages/tn/` |
| opencc 兜底丢失 | `pip install opencc-python-reimplemented` |
| pytest 找不到 | 使用 `.\WPy64-312101\python\python.exe -m pytest` 而非系统 Python |
| 兼容性回归 | 跑 `scripts/check_3engine_compat.py` 定位失败项 |
| 依赖升级失败 | 检查 `pyproject.toml` 版本约束，参考 `docs/INSTALLATION_FALLBACKS.md` |

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

### 8.4 阶段完成报告

```
阶段 X 完成 | <子任务完成情况> | <关键指标变化> | <下一步建议>
```

---

## 附录 A：完整 commit 列表（master 分支最近 17 个）

```
4a6d5b1 docs: tasks index and v2 workflow
4a1678c refactor(i18n,docs,tests): clean GPT-SoVITS residue
86d98af refactor(engines): remove GPT-SoVITS references from core
a9e4d07 refactor(engines): remove GPT-SoVITS engine
c4a4f4c docs(adr): ADR-0001 remove GPT-SoVITS + 3-engine compat checker
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
```

---

## 附录 B：项目硬约束（来自 AGENTS.md §6，必须遵守）

1. **显存预检**：模型加载前必须预检，可用显存需为模型大小的 1.5 倍以上
2. **内存熔断**：显存占用超过 90% 时必须立即终止推理并清理缓存
3. **离线优先**：禁止在推理过程中自动下载模型
4. **单 Worker 串行**：生成任务通过 `model_manager.py` 串行处理
5. **SSE 状态推送**：进度更新通过统一端点 `/api/sse/events` 推送

---

## 附录 C：依赖版本基线（2026-08-01 锁定）

| 包 | 版本 | 来源 |
|----|------|------|
| torch | ≥ 2.5.1 | pyproject.toml |
| transformers | **5.14.1** | 升级后 |
| numpy | **2.4.6**（< 2.5 兼容 numba） | 升级后 |
| pydantic | **2.13.4** | 升级后 |
| funasr | ≥ 1.0.0 | VoxCPM2 |
| opencc-python-reimplemented | ≥ 0.1.0 | 历史 GPT-SoVITS 兜底（已无关） |
| Python | 3.10 - 3.13 | 分类器 |

**升级历史**：
- 2026-07-XX：transformers 4.43 → 4.50（接入 GPT-SoVITS）
- 2026-08-01：transformers 4.50 → **5.14.1** / numpy 1.26.4 → **2.4.6** / pydantic 2.10.6 → **2.13.4**

---

**维护说明**：本文件由 AI 指挥维护，每次任务完成/打回/重新规划时更新。阶段 A-D 任务的具体派发见 `docs/TASKS.md`（如已建立）或本文件新增子章节。
