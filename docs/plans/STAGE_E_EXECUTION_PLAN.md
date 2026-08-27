# 阶段 E 任务执行手册

> 编制日期：2026-08-01
> 编制者：AI 代理
> 目标执行者：项目维护者（你）
> 关联文档：[`docs/ROADMAP.md`](ROADMAP.md) §5.5 阶段 E / [`docs/STAGE_E_QUALITY_REPORT.md`](STAGE_E_QUALITY_REPORT.md)
> 健康度基线：3 引擎兼容 9/9 ✅ / pytest 353 passed ✅ / 覆盖率 26.05% ✅

---

## 0. 任务包总览

### 0.1 5 个原子任务包

| 编号 | 任务 | 工期 | 前置 | ROI | 推荐顺序 | 状态 |
|------|------|------|------|-----|---------|------|
| **TB.1** | Playwright 端到端 3 引擎冒烟 + 截图 | 1 天 | 无 | 高（视觉基线） | 1️⃣ 第一周 | ⏳ 待执行 |
| **TB.2** | E.1 Streaming 实时 TTS | 1 周 | TB.1 | 高（核心体验） | 2️⃣ 第一周 | ⏳ 待执行 |
| **TB.3** | E.2 LLM-driven 提示词编排 | 2 周 | 无 | 高（差异化） | 3️⃣ 第二周起 | ⏳ 待执行 |
| **TB.4** | E.3 TypeScript 类型化（渐进式） | 3 周 | 无 | 中（工程化） | 4️⃣ 可与 TB.2/3 并行 | ⏳ 待执行 |
| **TB.5** | E.4 插件化架构 | 2 周 | 阶段 C 完成 | 中（生态） | 5️⃣ 后期 | ⏳ 待执行 |

**总工期**：8 周
**总 commit 数**：预估 15-20 个原子 commit
**质量门禁**：每个 commit 前必跑 `scripts/stage_e_quality_gate.bat`

### 0.2 状态图例

| 符号 | 含义 |
|------|------|
| ⏳ | 待执行 |
| 🟡 | 进行中 |
| ✅ | 完成 + 入库 |
| ❌ | 决策不做 |

---

## 1. 启动前准备（必做）

### 1.1 必读文档

按重要性顺序：
1. **本文档**（你正在读的）
2. [`docs/ROADMAP.md`](ROADMAP.md) §5.5 阶段 E 详细规划
3. [`docs/STAGE_E_QUALITY_REPORT.md`](STAGE_E_QUALITY_REPORT.md) 健康度基线
4. [`AGENTS.md`](../AGENTS.md) 全文（项目硬约束、编码规范、CI 门禁）

### 1.2 通用约束（AGENTS.md §6 硬约束）

每个任务包必须遵守：

1. **显存预检**：模型加载前预检，可用显存需为模型大小的 1.5 倍以上
2. **内存熔断**：显存占用超过 90% 时立即终止推理并清理缓存
3. **离线优先**：禁止推理过程中自动下载模型
4. **单 Worker 串行**：生成任务通过 `model_manager.py` 串行处理
5. **SSE 状态推送**：进度更新通过统一端点 `/api/sse/events` 推送

### 1.3 通用工作流（每个子项必走）

```
1. 启动任务前：跑 scripts/stage_e_quality_gate.bat 确认基线健康
2. 编码：按子项实施步骤，遵守 AGENTS.md §4 编码规范
3. 单测：每个子项 ≥ 70% 覆盖（CI 门禁 20%，目标 30%）
4. 本地验证：跑 scripts/stage_e_quality_gate.bat 确认未回归
5. 提交：原子化 commit（一个 commit 一个可独立回滚的主题）
6. 回报：任务 N 完成 | commit <hash> | <影响行数>
```

### 1.4 复现质量基线

```bash
# 3 引擎兼容性检测（应输出 9/9 通过）
.\WPy64-312101\python\python.exe scripts\check_3engine_compat.py

# pytest 离线（应输出 353 passed / 26.05% 覆盖）
cmd /c scripts\stage_e_quality_gate.bat
```

期望结果已记录在 `docs/STAGE_E_QUALITY_REPORT.md`。

---

## 2. TB.1 Playwright 端到端 3 引擎冒烟 + 截图（1 天）

### 2.1 目标

启动 server，用 Playwright 自动化测试 3 引擎 WebUI，捕获 LCP/可访问性截图作为视觉基线。

### 2.2 前置

- 无（独立任务）

### 2.3 关键文件

| 操作 | 路径 |
|------|------|
| 新建 | `tests/e2e/test_3engine_smoke.py` |
| 新建 | `docs/e2e_screenshots/voxcpm2_{home,tab,form}.png` |
| 新建 | `docs/e2e_screenshots/indextts2_{home,tab,form}.png` |
| 新建 | `docs/e2e_screenshots/dotstts_{home,tab,form}.png` |
| 新建 | `docs/e2e_screenshots/lcp_a11y_report.md` |
| 修改 | `.github/workflows/e2e.yml`（本地路径过滤 + artifact 上传） |

### 2.4 实施步骤

1. **启动 server**：
   ```bash
   cd "c:/Users/Doro/TTS_MultiModel"
   cmd /c start.bat
   # 等待 127.0.0.1:7869 可访问（约 30s）
   ```

2. **写 Playwright 脚本**（`tests/e2e/test_3engine_smoke.py`）：
   - 复用现有 `tests/e2e/test_tab_collapse_interaction.py` 的 setup
   - 3 引擎 tab 各访问一次
   - 每个 tab 抓 3 张截图（home / tab loaded / form filled）
   - 收集 Web Vitals（LCP / FCP / CLS）
   - 跑 axe-core 基础可访问性扫描

3. **3 引擎各抓 9 张** = 3 张 × 3 引擎

4. **收集 LCP / a11y**：
   - LCP < 2.5s 为通过
   - a11y 无 critical 错误
   - 失败状态截图也保留（按你之前决策"允许记录所有状态"）

5. **更新 `.github/workflows/e2e.yml`**：本地路径过滤 + artifact 上传 9 张截图

### 2.5 验收标准

- [ ] 9 张截图入库（3 引擎 × 3 张）
- [ ] LCP < 2.5s
- [ ] axe-core 无 critical 错误
- [ ] 失败状态（如有）作为"环境受限"证据保留
- [ ] `.github/workflows/e2e.yml` 支持路径过滤
- [ ] `docs/e2e_screenshots/lcp_a11y_report.md` 含数据表

### 2.6 风险

| 风险 | 缓解 |
|------|------|
| CPU 上真实推理 OOM/超时 | 失败时截图 + 日志保留为证据，不阻塞 |
| VoxCPM2 / IndexTTS2 启动慢 | 用 `--lazy-load` 标志，跳过预加载 |
| Web Vitals 测量不准确 | 用 Playwright Performance API，不依赖外部服务 |

### 2.7 单测要求

- `tests/e2e/test_3engine_smoke.py` 标记 `@pytest.mark.e2e`
- 与 CI gate 一致：`-m "not integration"` 不应包含 e2e（e2e 单独跑）
- 复用现有 `e2e.yml` 的 Playwright 镜像（已配置）

### 2.8 提交模板

```bash
git add tests/e2e/test_3engine_smoke.py docs/e2e_screenshots/ .github/workflows/e2e.yml
git commit -m "test(e2e,stage-e): playwright 3-engine smoke + LCP/a11y screenshots

Captures visual baseline for VoxCPM2 / IndexTTS2 / dots.tts:
- 9 screenshots (3 engines x 3 states)
- Web Vitals (LCP/FCP/CLS) measurements
- axe-core a11y scan results
- Failure states preserved as 'environment-limited' evidence

Refs: ROADMAP.md TB.1, STAGE_E_QUALITY_REPORT.md section 4.2"
```

---

## 3. TB.2 E.1 Streaming 实时 TTS（1 周）— 详细启动指南

### 3.1 目标

FastAPI 增加 `StreamingResponse` 端点，前端流式接收 chunked audio，4 语言 i18n 键齐。

### 3.2 前置

- TB.1 完成（视觉基线对比）

### 3.3 关键文件

| 操作 | 路径 | 行数预估 |
|------|------|----------|
| 新建 | `app/integrated_app/routes/streaming_audio.py` | +120 |
| 修改 | `app/integrated_app/app_server.py` | +8（注册路由） |
| 修改 | `app/integrated_app/static/js/tts_form.js` | +60（流式消费） |
| 修改 | `app/integrated_app/static/js/api_cache.js` | +30（流式响应缓存） |
| 修改 | `locales/zh.json` | +6（streaming.* 键） |
| 修改 | `locales/en.json` | +6 |
| 修改 | `locales/ja.json` | +6 |
| 修改 | `locales/ko.json` | +6 |
| 新建 | `tests/test_streaming_audio.py` | +150 |
| 新建 | `tests/integration/test_streaming_inference.py` | +80 |

### 3.4 实施步骤（详细）

#### Day 1-2：后端 StreamingResponse 端点

1. **创建 `routes/streaming_audio.py`**：
   ```python
   from fastapi.responses import StreamingResponse
   
   @router.post("/api/generate/stream")
   async def stream_generate(request: GenerateRequest):
       """SSE 风格的流式 TTS 生成端点。
       
       事件类型：
         - chunk: {audio: base64, segment_id: int}
         - progress: {percentage: float, eta_seconds: int}
         - complete: {audio_url: str, duration: float}
         - error: {message: str, code: str}
       """
       async def event_stream():
           async for segment in engine.stream_generate(...):
               yield f"data: {json.dumps({...})}\n\n"
       return StreamingResponse(event_stream(), media_type="text/event-stream")
   ```

2. **复用现有 VoxCPM2 流式函数**（`engines/voxcpm2_engine.py` 的 `fn_voxcpm_streaming`）和 dots.tts 的 `generate_streaming`（`engines/dotstts_engine.py:29`）

3. **注册路由**到 `app_server.py`：
   ```python
   from .routes.streaming_audio import router as streaming_router
   app.include_router(streaming_router)
   ```

4. **错误处理**：复用 `middleware/error_handler.py` 的统一异常捕获

#### Day 3：i18n 键添加

5. **4 语言添加 6 个键**：
   - `streaming.start`（开始流式生成）
   - `streaming.chunk_received`（已接收第 N 块）
   - `streaming.complete`（流式生成完成）
   - `streaming.cancelled`（流式生成已取消）
   - `streaming.error`（流式生成失败）
   - `streaming.playback_buffering`（缓冲中…）

   （已存在 `stream_generate` / `streaming_gen` / `received_chunk` 等键，参考 `docs/STAGE_E_QUALITY_REPORT.md` §3.3）

#### Day 4：前端消费流

6. **修改 `tts_form.js`**：
   ```js
   async function streamGenerate(formData) {
       const response = await fetch('/api/generate/stream', {
           method: 'POST',
           headers: { 'X-CSRF-Token': getCsrfToken() },
           body: formData
       });
       const reader = response.body.getReader();
       const decoder = new TextDecoder();
       while (true) {
           const { done, value } = await reader.read();
           if (done) break;
           const chunk = decoder.decode(value);
           // 解析 SSE 事件
           handleStreamEvent(chunk);
       }
   }
   ```

7. **使用 MediaSource API** 边下边播（如不支持则降级到完整下载）

#### Day 5：测试

8. **`tests/test_streaming_audio.py`**：覆盖路由、SSE 格式、错误处理（≥ 80%）
9. **`tests/integration/test_streaming_inference.py`**：mock 引擎跑 1 次完整流

### 3.5 验收标准

- [ ] `POST /api/generate/stream` 端点可用
- [ ] 4 语言 i18n 键齐（zh/en/ja/ko 各 6 个新键）
- [ ] 前端能边下边播（MediaSource API 优先，否则 buffer 模式）
- [ ] 错误处理：SSE 错误事件 + HTTP 状态码双通道
- [ ] 与现有 SSE 进度事件兼容（不重复推送）
- [ ] 单元测试 ≥ 80% 覆盖
- [ ] 集成测试通过

### 3.6 风险

| 风险 | 缓解 |
|------|------|
| 现有 `fn_voxcpm_streaming` 实现细节未知 | 启动前先 `read_file engines/voxcpm2/streaming.py`（如存在）理解接口 |
| FastAPI StreamingResponse 异步处理 | 用 `async def` 路由 + `asyncio.Queue` |
| 前端 MediaSource API 兼容 | 检测浏览器支持，降级到 fetch + ArrayBuffer |
| 跨域/CSRF 冲突 | 复用现有 `CSRFMiddleware` + 现有 SSE 端点模式 |

### 3.7 提交模板

```bash
git add app/integrated_app/routes/streaming_audio.py app/integrated_app/app_server.py
git commit -m "feat(streaming,stage-e): FastAPI StreamingResponse endpoint for chunked audio

- POST /api/generate/stream returns text/event-stream
- Reuses fn_voxcpm_streaming + dots.tts generate_streaming
- 4-lang i18n keys (streaming.start/chunk_received/complete/...)"
```

### 3.8 配套 commit（建议拆 3-4 个原子 commit）

- `feat(streaming,stage-e): backend StreamingResponse endpoint`（+128）
- `feat(i18n,streaming): 4-lang streaming keys`（+24）
- `feat(frontend,streaming): stream consumption + MediaSource`（+90）
- `test(streaming,stage-e): unit + integration coverage ≥80%`（+230）

---

## 4. TB.3 E.2 LLM-driven 提示词编排（2 周）

### 4.1 目标

`LLMProvider` 抽象 + 3 个内置模板 + 1 个 OpenAI 兼容实现。

### 4.2 前置

- 无

### 4.3 关键文件

| 操作 | 路径 | 行数预估 |
|------|------|----------|
| 新建 | `app/integrated_app/llm/__init__.py` | +5 |
| 新建 | `app/integrated_app/llm/provider.py`（`LLMProvider` 抽象基类） | +80 |
| 新建 | `app/integrated_app/llm/openai_provider.py` | +120 |
| 新建 | `app/integrated_app/llm/templates.py`（3 个内置模板） | +200 |
| 新建 | `app/integrated_app/llm/orchestrator.py` | +150 |
| 新建 | `app/integrated_app/routes/llm.py` | +60 |
| 修改 | `app/integrated_app/app_server.py` | +5 |
| 修改 | `app/integrated_app/config_models.py` | +30（`LLMConfig`） |
| 修改 | `config.yaml` | +10（`llm:` 段） |
| 修改 | `locales/{zh,en,ja,ko}.json` | +12（每语言 3 个新键） |
| 新建 | `tests/test_llm_provider.py` | +150 |
| 新建 | `tests/test_llm_templates.py` | +120 |
| 新建 | `examples/llm_orchestrate_demo.py` | +60 |

### 4.4 实施步骤

#### Week 1：抽象 + 实现

1. **Day 1**：`LLMProvider` 抽象（参考 `engine_interface.py` 风格）
   ```python
   class LLMProvider(Protocol):
       async def complete(self, prompt: str, **kwargs) -> str: ...
       async def stream_complete(self, prompt: str, **kwargs) -> AsyncIterator[str]: ...
   ```

2. **Day 2-3**：`OpenAIProvider` 实现（兼容 OpenAI / Anthropic / Ollama openai-compatible 端点）
   - 使用 `httpx.AsyncClient` + 环境变量
   - 离线优先：本地 Ollama 优先，云端 API 需 API key

3. **Day 4-5**：3 个内置模板
   - `voice_design_enhance`：基础描述 → 详细 voice design prompt
   - `clone_refine`：参考音频描述 → 优化提示词
   - `script_split`：长文本 → 角色对话拆分

#### Week 2：编排 + 集成

4. **Day 6-7**：`Orchestrator` 串联 LLM + 引擎
5. **Day 8**：`/api/llm/orchestrate` 路由
6. **Day 9**：i18n + 文档
7. **Day 10**：测试 + 例子

### 4.5 验收标准

- [ ] `LLMProvider` Protocol 定义清晰
- [ ] 3 个内置模板可工作（手工测试 5 个用例）
- [ ] `Orchestrator` 可串联 3 引擎
- [ ] `config.yaml` 增加 `llm.provider: openai|ollama` 配置
- [ ] 4 语言 i18n 键齐
- [ ] 单元测试 ≥ 75% 覆盖
- [ ] 离线模式：本地 Ollama 可用，无 API key 也能跑

### 4.6 风险

| 风险 | 缓解 |
|------|------|
| LLM API 调用费用 | 离线 Ollama 优先；提供 mock provider |
| 模板质量参差 | 3 个模板先小范围测试，不公开 |
| 增加依赖 | `httpx` 已存在；不引新依赖 |
| LLM 输出不稳定 | 加 retry + 限流 + temperature=0.3 |

### 4.7 提交模板

```bash
git add app/integrated_app/llm/ app/integrated_app/routes/llm.py config.yaml
git commit -m "feat(llm,stage-e): LLM-driven prompt orchestration

- LLMProvider Protocol (openai-compatible)
- 3 built-in templates: voice_design_enhance / clone_refine / script_split
- Orchestrator wires LLM -> 3 TTS engines
- Offline-first: local Ollama preferred"
```

---

## 5. TB.4 E.3 TypeScript 类型化（3 周，渐进式）

### 5.1 目标

`tsconfig.json` + 28 JS 全部加 `.d.ts` 声明 + `tsc --noEmit` 0 错误。

### 5.2 前置

- 无（独立任务，可与 TB.2/TB.3 并行）

### 5.3 关键文件

| 操作 | 路径 | 行数预估 |
|------|------|----------|
| 新建 | `tsconfig.json` | +30 |
| 新建 | `app/integrated_app/static/js/types/global.d.ts` | +200 |
| 新建 | `app/integrated_app/static/js/types/window.d.ts` | +100 |
| 新建 | `app/integrated_app/static/js/types/api.d.ts` | +150 |
| 新建 | `app/integrated_app/static/js/types/i18n.d.ts` | +50 |
| 修改 | `app/integrated_app/static/js/*.js` | +500（类型注释） |
| 新建 | `package.json` | +25 |
| 新建 | `.github/workflows/tsc.yml` | +40 |
| 新建 | `docs/TYPESCRIPT_MIGRATION_GUIDE.md` | +150 |

### 5.4 实施步骤

#### Week 1：基础设施

1. **Day 1**：`package.json` + `tsconfig.json`（strict mode + checkJs: false）
2. **Day 2-3**：全局类型声明（window.d.ts / api.d.ts / i18n.d.ts）
3. **Day 4-5**：CI 集成 `.github/workflows/tsc.yml`（`tsc --noEmit`）

#### Week 2-3：渐进式类型化

4. **Week 2**：核心文件先类型化
   - `tts_form.js`（最大，约 800 行）
   - `api_cache.js`
   - `prototype_v4.js`
5. **Week 3**：剩余 25 个 JS 文件
6. **每个文件**：保留 `.js` 扩展名（不强制 .ts 迁移），加 `@type` JSDoc

### 5.5 验收标准

- [ ] `tsc --noEmit` 0 错误
- [ ] 28 JS 全部有类型注释
- [ ] CI 集成（`.github/workflows/tsc.yml`）
- [ ] 不强制 .ts 迁移（保留 .js 兼容）
- [ ] IDE（VSCode）智能提示可用

### 5.6 风险

| 风险 | 缓解 |
|------|------|
| 现有 28 JS 无类型 | 渐进式，每个文件独立提交 |
| 与 HTMX/Alpine.js 集成 | 全局 window.d.ts 声明 |
| 团队学习成本 | JSDoc 风格，不强制 .ts 迁移 |
| 跨文件类型依赖 | 用 `// @ts-check` 局部启用 |

### 5.7 提交模板

```bash
git add package.json tsconfig.json app/integrated_app/static/js/types/ .github/workflows/tsc.yml
git commit -m "chore(types,stage-e): TypeScript infrastructure + global .d.ts

- tsconfig.json (strict mode, allowJs)
- package.json (typescript dev dep)
- 4 global .d.ts (window/api/i18n/global)
- CI: tsc --noEmit gate"
```

---

## 6. TB.5 E.4 插件化架构（2 周）

### 6.1 目标

`engine_registry` 加 hook + 第三方插件示例 + `plugin.yaml` schema。

### 6.2 前置

- **阶段 C 完成**（`MultiEngineRegistry` 已支持多引擎）
- 当前 C.1 / C.2 / C.3 已 100% 完成（见 ROADMAP.md §5.3）✅

### 6.3 关键文件

| 操作 | 路径 | 行数预估 |
|------|------|----------|
| 修改 | `app/integrated_app/engine_interface.py` | +50（hook 接口） |
| 修改 | `app/integrated_app/model_registry.py` | +100（plugin 加载） |
| 新建 | `app/integrated_app/plugins/__init__.py` | +10 |
| 新建 | `app/integrated_app/plugins/discovery.py` | +120 |
| 新建 | `app/integrated_app/plugins/schema.py`（`plugin.yaml` 校验） | +80 |
| 新建 | `app/integrated_app/plugins/hooks.py` | +60 |
| 新建 | `app/integrated_app/plugins/example/__init__.py` | +20 |
| 新建 | `app/integrated_app/plugins/example/plugin.yaml` | +15 |
| 新建 | `docs/PLUGIN_DEVELOPMENT_GUIDE.md` | +300 |
| 新建 | `tests/test_plugins_discovery.py` | +150 |

### 6.4 实施步骤

#### Week 1：核心 hook

1. **Day 1-2**：`engine_interface.py` 加 hook 协议
   ```python
   class EngineHook(Protocol):
       async def pre_generate(self, request: GenerateRequest) -> GenerateRequest: ...
       async def post_generate(self, result: GenerateResult) -> GenerateResult: ...
       async def on_engine_switch(self, old: str, new: str) -> None: ...
   ```

2. **Day 3-4**：`model_registry.py` 加插件加载
   - 从 `~/.tts_multimodel/plugins/` 或 `TTS_PLUGINS_PATH` env 扫描
   - 解析 `plugin.yaml`
   - 注册 hook 到现有引擎

3. **Day 5**：单元测试

#### Week 2：示例 + 文档

4. **Day 6-7**：示例插件 `app/integrated_app/plugins/example/`
5. **Day 8-9**：`docs/PLUGIN_DEVELOPMENT_GUIDE.md`（hook 类型、plugin.yaml schema、调试）
6. **Day 10**：测试 + 集成

### 6.5 验收标准

- [ ] `EngineHook` Protocol 定义
- [ ] 插件自动发现（从 `plugin.yaml` 加载）
- [ ] 示例插件可注册并被现有引擎调用
- [ ] 第三方可 `pip install tts-plugin-foo` 即生效
- [ ] 向后兼容：现有 3 引擎不需修改
- [ ] 单元测试 ≥ 75% 覆盖

### 6.6 风险

| 风险 | 缓解 |
|------|------|
| 破坏性大 | 严格保持向后兼容 `current_engine` |
| 第三方插件安全 | plugin.yaml schema 校验，签名验证（未来） |
| 插件冲突 | hook 优先级 + 显式注册顺序 |
| 调试困难 | 详细日志 + 插件隔离运行（v2 考虑） |

### 6.7 提交模板

```bash
git add app/integrated_app/engine_interface.py app/integrated_app/model_registry.py app/integrated_app/plugins/
git commit -m "feat(plugins,stage-e): plugin architecture with hook + discovery

- EngineHook Protocol (pre_generate/post_generate/on_engine_switch)
- Auto-discovery via plugin.yaml from TTS_PLUGINS_PATH
- Example plugin in app/integrated_app/plugins/example/
- Backward compatible with current 3 engines"
```

---

## 7. 推进节奏建议

### 8.1 8 周时间线

```
W1: TB.1 Playwright (1d) + TB.2 E.1 Streaming 启动 (4d)
W2: TB.2 E.1 Streaming 完成
W3-W4: TB.3 E.2 LLM-driven
W5-W7: TB.4 E.3 TypeScript（可与 W3-W4 并行）
W8: TB.5 E.4 插件化（需等阶段 C 完成）
```

### 8.2 每周检查点

- **每周一上午**：跑 `scripts/stage_e_quality_gate.bat` 确认无回归
- **每周五下午**：本周 commit 入库 + 进度回报

### 8.3 进度回报模板

```bash
# 按 ROADMAP.md §8.1 模板
任务 N 完成 | commit <hash> | <影响行数>
```

例如：
```
TB.1 完成 | commit a1b2c3d | +850/-0（9 截图 + 1 脚本）
TB.2 完成 | commit e4f5g6h + i7j8k9l + m0n1o2p | +520/-0
```

### 8.4 质量门禁

每个 commit 前必跑：
```bash
cmd /c scripts\stage_e_quality_gate.bat
```

期望：
- 353 passed（基线 2026-08-01）
- 覆盖率 ≥ 26.05%（基线）
- 0 failed

如有任何 commit 后**回归**，立即 revert + 排查。

---

## 9. 紧急回滚

### 9.1 单 commit 回滚

```bash
git revert <commit_hash>
git push
```

### 9.2 子任务包回滚

```bash
# 找到第一个 commit
git log --oneline | grep "stage-e" | tail -1
# revert 范围
git revert <first_commit>..<last_commit>
```

### 9.3 整阶段回滚

```bash
git revert <stage_e_first_commit>..HEAD
# 或回退到 ROADMAP A-D 末态
git reset --hard 9ce23eb  # 危险，需用户确认
```

**警告**：按 AGENTS.md §7，**禁止使用 `git reset --hard`**，除非用户明确请求。

---

## 10. 复现质量基线（速查）

### 10.1 3 引擎兼容性

```bash
.\WPy64-312101\python\python.exe scripts\check_3engine_compat.py
```

期望：9/9 通过

### 10.2 pytest 离线

```bash
cmd /c scripts\stage_e_quality_gate.bat
```

期望：353 passed / 76 skipped / 0 failed / 26.05% 覆盖

### 10.3 当前基线（2026-08-01）

| 指标 | 数值 |
|------|------|
| torch | 2.8.0+cu128 |
| transformers | 5.14.1 |
| numpy | 2.4.6 |
| pydantic | 2.13.4 |
| VoxCPM2 / IndexTTS2 / dots.tts import | OK |
| pytest passed | 353 |
| pytest skipped | 76 |
| 覆盖率 | 26.05% |
| HEAD | `0267492` |

---

## 附录 A：原子 commit 估算

总计 15-20 个原子 commit：

| 任务包 | commit 数 | 关键 commit hash 模式 |
|--------|----------|---------------------|
| TB.1 | 2 | `test(e2e,stage-e)`, `docs(screenshots,stage-e)` |
| TB.2 | 4 | `feat(streaming,backend)`, `feat(i18n,streaming)`, `feat(frontend,streaming)`, `test(streaming)` |
| TB.3 | 5 | `feat(llm,provider)`, `feat(llm,templates)`, `feat(llm,orchestrator)`, `feat(llm,routes)`, `test(llm)` |
| TB.4 | 4 | `chore(types,infra)`, `chore(types,global)`, `chore(types,core)`, `chore(types,remaining)` |
| TB.5 | 3 | `feat(plugins,hooks)`, `feat(plugins,discovery)`, `docs(plugins,guide)` |

## 附录 B：跨任务包依赖图

```
TB.1 ──> TB.2 ──> [完成核心体验]
         TB.3 ──> [完成差异化]
         TB.4 ──> [完成工程化]
         TB.5 ──> [完成生态化]

注：TB.3 / TB.4 / TB.5 可与 TB.2 并行（独立任务）
```

## 附录 C：风险登记表

| 风险 | 等级 | 缓解 |
|------|------|------|
| Playwright 真实推理 OOM | 中 | 失败状态截图保留 |
| VoxCPM2 streaming 内部细节 | 中 | Day 1 先 read 现有 `fn_voxcpm_streaming` |
| LLM API 成本 | 中 | 离线 Ollama 优先 |
| TS 类型化工作量低估 | 中 | 渐进式，每个 JS 独立提交 |
| 插件化破坏性 | 高 | 严格向后兼容 + 完整测试 |

---

**手册结束**。预计 8 周完成 5 个子任务包 + 15-20 个原子 commit。

执行过程中遇到问题随时调用 `scripts/stage_e_quality_gate.bat` 确认基线无回归。
