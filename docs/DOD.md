# 完成定义（Definition of Done）

> **来源**：家族通用 DOD 模板 `.spec_audit/family_DOD.md`（源自 SpiritPal definition-of-done.md 泛化；该家族模板已归档，2026-09-17 起按本仓本地化内容维护），本仓本地化。
> **适用范围**：TTS_MultiModel 全项目所有功能开发任务。
> **关于 `AGENTS.md`**：下文多处引用的 `AGENTS.md` 为**本地维护、不随仓库分发**的资产（`.gitignore` 已忽略），clone 本仓的读者无需查找该文件，相关约束以本文条目正文为准。

---

## 0. DoD 等级

| 等级 | 适用场景 | 要求 |
|------|---------|------|
| **Full DoD** | 正式功能开发 / 大型 PR | 全部检查类 |
| **Lite DoD** | Bug 修复 / 小优化 | 代码完成 + 测试覆盖 + 构建验证 |
| **Hotfix DoD** | 紧急线上修复 | 代码完成 + 构建验证（事后补齐其余） |

> 判定「完成」必须先跑对应等级清单；不满足不得标注完成、不得提交 main。

## 1. 代码完成

- [ ] 功能已实现，覆盖 PRD / Spec 定义的所有验收标准（AC）
- [ ] `ruff` / `ruff-format` 通过（0 error）；mypy 命中 `.ci/mypy_baseline` 无新增
- [ ] 无调试残留（`print()` / `breakpoint()` / `pdb`，诊断日志除外需标记 `[diag]`）
- [ ] 未引入跨层违规引用（遵守本地 AGENTS.md §3 模块边界 + 禁区表）
- [ ] 涉及引擎改动遵守本地 AGENTS.md SOP / `docs/project/MULTI_ENGINE_DESIGN.md` + `DI_SINGLETONS.md`
- [ ] 新增路由遵守 routes 分层约定（generate / system）

## 2. 测试覆盖

- [ ] 新增/修改函数有对应 pytest 单测（`tests/`）
- [ ] 覆盖正常路径 + 边界条件 + 异常场景
- [ ] 全量 pytest 通过（不新增失败用例）
- [ ] 涉及引擎切换/显存遵守 `test_vram_switch` 等集成约束
- [ ] 前端模板改动跑 smoke / 视觉回归（含 i18n 5 语言）

## 3. 文档同步

- [ ] `AGENTS.md` 已同步（本地维护、不随仓库分发；目录结构 / 模块边界 / 配置 / 环境变量）
- [ ] 新增模块在本地 AGENTS.md §3 / `docs/project/ARCHITECTURE.md` 有对应条目
- [ ] 踩坑已追加到 `docs/project/KNOWN_GOTCHAS.md`（触发/现象/做法/日期）
- [ ] `CHANGELOG.md` 已记录变更（type 对应 Added/Fixed/…）
- [ ] `python scripts/check_spec_refs.py` 退出码 0（无幻影/死链/假门禁）

## 4. 公共服务 / SRE

- [ ] 涉及部署时遵守 `docs/SRE_RUNBOOK.md`（SLO / 探针 / 错误预算）与 `docs/rollback_sop.md`
- [ ] 涉及镜像发布遵守 docker / k8s 钉版（digest）约定，不用 `:latest`

## 5. 构建 & 验证

- [ ] 完整启动通过（`app/clean_launch.py`；`app_server.py` / `openai_api.py` 契约不破坏）
- [ ] 手动验证功能按预期工作（不仅是测试通过）→ 走下面的 §5.1 清单
- [ ] 涉及引擎/权重改动时遵守模型校验（`generate_model_checksums.py` / `verify_model_weights.py`）

### 5.1 发布前人工点验清单（约 30 分钟，必须用真实鼠标键盘 + 真听音频）

自动化测不出"看得见/听得着"的东西：遮罩是否真的出现、音频有没有爆音嘶声、
字体是否塌成宋体、拖拽与键盘可达性。以下每项都在浏览器里真做一遍，不是看代码。

**准备（2 分钟）**：`python app/clean_launch.py` → 打开 `http://127.0.0.1:7869` → 侧栏切到
「语音设计」；准备一段 3–10 秒清晰人声做上传素材。

| # | 动作 | 通过判据（不满足即阻断发布） |
|---|---|---|
| 1 | 三个引擎各点一次切换（voxcpm2 → indextts2 → indextts20 → voxcpm2） | 每次都出现遮罩且**阶段文本会推进**（卸载→清理→加载→就绪），不是停在同一句 30 秒；切完顶部引擎徽标随之变化 |
| 2 | 切换期间观察控制台与 `logs/app.log` | 无 `疑似仍有强引用未释放` / 无 `可用 -0.xxGB` / 出现 `本次回收 X.XXGB`（这是显存泄漏回归的哨兵） |
| 3 | 每个引擎各生成一次，**戴耳机听完整个片段** | 无爆音、无断续、无明显电子嘶声（尤其 IndexTTS 上采样到 48k 后的高频水印）；播放器时长与实际听感一致。水印可懂度另用同源对照判读：`python scripts/make_watermark_ab.py` → 按 `docs/reports/watermark_ab/README.md` 的 A/B 来回切，**听不出哪段是 B 为通过** |
| 4 | 中文文本 + 一段英文 + 数字/日期各一次 | 数字按语言正确读法展开；「切换标题字体」菜单里**未安装的家族要显示「未安装」且点了不写 `localStorage`**，Console 不出现 CSP violation（`python scripts/check_font_menu_availability.py` 可机器化核对数量，见 §5.2） |
| 5 | 故意制造 6 类误操作：空文本 / 超上限文本 / 未勾授权 / 传 `.txt` / 传内容是文本但命名 `.wav` / 音色未加载就点生成 | 每类都是**红色错误块 + 可读中文 + 有「立即加载」或「重试」按钮**，结果区绝不出现 `{"detail":[{...` 这类原始 JSON 或 `Traceback`/绝对路径。**并且要验按钮做事**：点「重试」必须看到一次新的提交（Network 里有新请求或表单被重新提交），点「立即加载」必须看到加载遮罩 —— 只确认"按钮存在"不算通过（GOTCHAS #133） |
| 6 | 长文本生成到一半点「取消」 | 立即停下并给出取消态提示；**取消后再生成一次能正常排队**（不是卡住/需刷新）；`logs/app.log` 里取消**不应**出现 ERROR 级记录 |
| 7 | 用 Tab/回车走完三个工作台，并开一次屏幕阅读器（NVDA/VoiceOver） | 全键盘可完成"选音色→输入→生成→播放"；遮罩朗读出阶段变化（`role="status" aria-live`） |
| 8 | 断网后重启服务并打开首屏 | 页面正常渲染（外部字体被 CSP 拦或加载失败都不应阻塞），仅样式退化，无空白页 |
| 9 | 连续切换 6 次后查 `/api/system/health` 与 `nvidia-smi` | 空闲显存回到切换前水平（±0.5GB），无单调递增趋势（长时泄漏哨兵） |
| 10 | `curl` 直连 `/v1/audio/speech`（不带 Cookie/授权声明） | 返回机器可读 JSON 错误（**不是** HTML 片段）—— 验证错误渲染分流没把 API 客户端带进 HTML 分支 |

**收尾**：把发现的任何"只在真机上暴露"的问题按铁律 #2 记进 `docs/agents/GOTCHAS.md`，
并在本表下方补一行该次验证的版本号与结论。

### 5.2 能自动化掉的"看得见/听得着"（先跑这三条，再决定哪些还要人做）

| 命令 | 覆盖清单项 | 通过判据 |
|---|---|---|
| `pytest tests/test_a11y_static.py tests/test_fe_be_consistency.py -q` | 4/5/7 的结构面 | 五条可感知性守卫 + **十条**前后端一致性守卫全绿：图标按钮有名称 / 模板无外链资源 / id 不重复 / 对话框有名称 / 播报通道带 `aria-live`；URL 与字段与 name 三层 / 生成后接线 / **手写 fetch 带 `X-CSRF-Token`**（#130）/ **功能页表单与引擎归属 + 程序化跳转必须用 `gotoTab`**（#131）/ **内联 JS 过 `node --check`** / **`onclick` 调的函数必须存在（防死按钮）**（#133）。每条都带变异自证 |
| `python scripts/check_font_menu_availability.py`（需服务在跑） | 4 的字体面 | 菜单每条的"可用/未安装"结论与同浏览器实测一致，且加载期 0 条 CSP 报错。2026-09-19 起字体已自托管（776 文件 / 25.5 MB + 14 份 OFL 全文），实测 **14/14 可用** |
| `python scripts/make_watermark_ab.py` + 人耳 | 3 的水印面 | 生成同源 A/B 与量化表（落在 `docs/reports/watermark_ab/`，**该目录被 `.gitignore` 忽略**，属可重生成的本地产物）；**能否听出 B 仍需人耳**，脚本只保证差异唯一 |

- 最近一次执行：2026-09-19，v2.2.2 工作树。机器侧佐证：全量 `pytest`（含 e2e、服务在线，
  `--cov=app/integrated_app`）**2084 passed / 40 skipped / 0 failed，8m14s**，覆盖率 51.94%
  （门禁 45%）；mypy 103 = 基线；`tests/e2e/` 68 passed + 5 skipped（跳过的 5 条是视觉回归，
  基线由 Linux CI 生成，Windows 本机按平台守卫 skip）。同一套用 `--cov=integrated_app`
  口径统计约 50.0%，差异只在统计范围，两者都过门禁。
  第 1/2/3/4/5/6/9/10 项已取证——其中第 4 项由 §5.2 的字体菜单核对转为机器判定，
  第 3 项的水印可懂度已于 2026-09-19 由人工听 A/B 判定**通过**（三对同源 A/B 均达标），
  第 5 项的"按钮要做事"已真机验过：400 错误块带 `role="alert"` + 「立即加载」「重试」两个按钮，
  点重试 XHR 计数 +1 且错误块重新渲染（此前该按钮调的是从未定义的函数，纯死键，#133），
  第 9 项 6 轮切换实测空闲显存回到切换前水平（spread 416MB，无单调递增）。
  **仍需人工：第 7 项真开一次屏幕阅读器、第 8 项断网首屏。**

## 6. 安全 & 隐私

- [ ] 无硬编码密钥 / API Key / 敏感常量（走 `.env.sway` / Secret 管理）
- [ ] 新增路由遵守安全中间件（auth / csrf / rate_limit / content_safety / integrity_check）
- [ ] 未新增静默吞错（`except: pass` 等）
- [ ] 涉及禁区目录（model/ 权重、integrity、安全模块）走人工确认流程

## 7. 可追溯性

- [ ] 变更有影响评估（破坏性 / 非破坏性）
- [ ] 涉及配置变更时：`config.yaml` 结构同步 config_models + `check_config_refs.py`
- [ ] 涉及版本：`pyproject.toml` / `CHANGELOG.md` / release-please tag 一致
- [ ] 涉及 CI 变更时与 `.github/workflows/*.yml` 实际文件一致（证据绑定）