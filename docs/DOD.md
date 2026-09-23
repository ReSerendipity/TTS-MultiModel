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
| 8 | 断网后重启服务并打开首屏 | 页面正常渲染（外部字体被 CSP 拦或加载失败都不应阻塞），仅样式退化，无空白页。**已可机器化**：`python scripts/check_offline_first_paint.py` 掐掉所有非本机请求后核对（见 §5.2）；真拔网线仍建议顺手做一次，但不再是大头 |
| 9 | 连续切换 6 次后查 `/api/system/health` 与 `nvidia-smi` | 空闲显存回到切换前水平（±0.5GB），无单调递增趋势（长时泄漏哨兵） |
| 10 | `curl` 直连 `/v1/audio/speech`（不带 Cookie/授权声明） | 返回机器可读 JSON 错误（**不是** HTML 片段）—— 验证错误渲染分流没把 API 客户端带进 HTML 分支 |

**收尾**：把发现的任何"只在真机上暴露"的问题按铁律 #2 记进 `docs/agents/GOTCHAS.md`，
并在本表下方补一行该次验证的版本号与结论。

### 5.2 能自动化掉的"看得见/听得着"（先跑这三条，再决定哪些还要人做）

| 命令 | 覆盖清单项 | 通过判据 |
|---|---|---|
| `pytest tests/test_a11y_static.py tests/test_fe_be_consistency.py -q` | 4/5/7 的结构面 | 七条可感知性守卫 + **十一条**前后端一致性守卫全绿：图标按钮有名称 / 模板无外链资源 / id 不重复 / 对话框有名称 / 播报通道带 `aria-live` / 客户端拼的错误块也要 `role="alert"` / **正文栈不得含随包标题字体**；URL 与字段与 name 三层 / 生成后接线 / **手写 fetch 带 `X-CSRF-Token`**（#130）/ **功能页表单与引擎归属 + 程序化跳转必须用 `gotoTab`**（#131）/ **内联 JS 过 `node --check`** / **`onclick` 调的函数必须存在（防死按钮）**（#133）/ **共用换页容器的触发器必须带 `hx-sync`**（#136）。每条都带变异自证 |
| `python scripts/check_font_menu_availability.py`（需服务在跑） | 4 的字体面 | 菜单每条的"可用/未安装"结论与同浏览器实测一致，且加载期 0 条 CSP 报错。2026-09-19 起字体已自托管（776 文件 / 25.5 MB + 14 份 OFL 全文），实测 **14/14 可用** |
| `python scripts/check_offline_first_paint.py`（需服务在跑） | 8 的断网面 | 浏览器里掐掉**所有非本机请求**（localhost 放行）后：出网尝试 0 次、可见标签 >0、内容区有正文、无未渲染 Jinja/未翻译键、Console 零 error。2026-09-20 实测：放行 95 次本机请求、**0 次出网尝试**、12 个可见标签、`vd-form` 在位 → 通过。口径注意：它证明"根本不尝试出网"，不覆盖 DNS/代理层失败 |
| `python scripts/check_tab_switch_race.py`（需服务在跑） | 侧栏换页竞态 | 给第一个 `/tab/` 请求注入 1.5s 延迟、350 ms 后点第二个，要求**末态落在最后点的那一页**（结构签名比对，不看像素）。修前 **6/6 组被旧响应盖回先点那页**，带 `hx-sync="#tab-content:queue last"` 后 **0/6** → 通过（#136）。必须用 async Playwright：sync 的 route handler 会把请求串行化，测出来是假阴性 |
| `python scripts/make_watermark_ab.py` + 人耳 | 3 的水印面 | 生成同源 A/B 与量化表（落在 `docs/reports/watermark_ab/`，**该目录被 `.gitignore` 忽略**，属可重生成的本地产物）；**能否听出 B 仍需人耳**，脚本只保证差异唯一 |

- 最近一次执行：**2026-09-23，v2.2.6 + #144（issue #130 那 16 处）**。工作树 = **独立 `git worktree`
  检出的 `origin/main`**（主工作树当时挂着另一个写者的未提交改动，"跑测试期间冻结工作树"的前提在
  那边不成立，所以这轮全部证据都来自干净检出）。四组：
  - **机器侧全量**：`pytest --ignore=tests/e2e --timeout=180` → **#144 那轮 2147 passed / 38 skipped /
    0 failed，66.8s**；#143 那轮 2145 passed / 38 skipped / 0 failed，94.63s。
  - **CI 侧**：12 平台 `Test (pytest)` 矩阵 + `Lint (ruff)` + `Typecheck (mypy ratchet)` + `DCO` 全绿，
    并且 **`Playwright E2E Tests` pass（9m2s）** —— #144 改的就是服务端模板与事件绑定，这一格不能免
    （v2.2.4 那轮记的是"e2e 没在本机跑"，这次由 CI 补上，不是我自己跑的，别记成同源证据）。
  - **真机（浏览器）侧**：起 `python -m integrated_app.app_server --port 7899`，**不加载模型**，
    `nvidia-smi` 显存占用**全程 9638 MiB 恒定**；收工核实 **7899 无监听、PID 已退出**。
    口径写清楚：这一格证明的是"页面与事件链路在真浏览器里行为正确、且没有偷偷申请显存"，
    **不是**模型加载/切换的显存曲线（那格这轮零覆盖，与 v2.2.4 的"切换后 9489→3344 MiB"不是一类证据）。
    四组取证：① 对照组证明旧写法是真坏（属性里的 `&#39;` 解回 `'` → 整段 handler 编译期 SyntaxError，
    连前半句都不执行；同一探针不带撇号则正常执行）② Jinja 真渲染的字节交给浏览器解析 → 值逐字节相等、
    `attributes.length==3`、没生成 `onmouseover` ③ persona 页点注入的带撇号行 → 参数送达且行选中未触发
    ④ history 页四个动作分别核对（id 到处理器手里是 `number`）。详单在 #144 的 PR body 与
    issue #130 的关闭评论。
  - **产物侧**（RP 构建、挂在 v2.2.6 Release 上的那份，不是我本地重跑的构建）：wheel 28,381,933 B /
    **1065 条目** / `METADATA Version: 2.2.6`；sdist 28,341,730 B / **1209 文件**（比 2.2.5 的 1208 多的
    那 1 个正是新增的 `tests/test_image_name_consistency.py`，能对上就说明差异是真的）。
    两者 sha256 **三方一致**：本地 `sha256sum -c SHA256SUMS` rc=0 == Release 资产里的 `SHA256SUMS`
    == GitHub 自算的 `asset.digest`（`002231f2…` / `6bdd5bbf…`）；完整性三件套齐；解包后**按包导入**跑
    `run_startup_selfcheck(enforce=True)` = **`total=16 passed=16 failed=0 skipped=0
    manifest_signed=true`**。
  - **镜像侧**：`:2.2.6` / `:2.2` / `:latest` / `:sha-62af6f6` 四个标签推到同一 digest `dcedcf0d…`
    （run 35832045215，`completed/success`，Trivy HIGH/CRITICAL 门禁在推之后跑）。
    **集群里真拉一次仍未取证**：那个包匿名读不到，要先按 `deploy/kubernetes/README.md` 建
    `ghcr-pull` secret（要一个带 `read:packages` 的 PAT，属 owner 动作）。
  - **这一轮仍未覆盖的格子**（别当成已过）：字体菜单可用性、断网首屏、侧栏换页竞态三项**没重跑**
    （上次取证是 2026-09-19/20 的 v2.2.2 工作树）；模型加载与切换的显存曲线这轮没碰；
    `Setup.exe` 真机安装与 ~26 GB 便携分卷**仍未验收**。
- 上一次执行：**2026-09-22，v2.2.4 发版前**（工作树 = `chore/release-2.2.4` 合并后的 `d4d80b7`）。
  机器侧佐证：全量 `pytest --ignore=tests/e2e --timeout=180` **2131 passed / 38 skipped / 0 failed，77.7s**；
  真机（RTX 5070 Ti Laptop 12 GB，权重齐）跑 `#84` 那条路：**四次连续直切**
  `voxcpm2 → indextts2 → indextts20 → voxcpm2`，全程不手动 unload、load 前空闲最低 609 MiB，
  四步全 `HTTP 200 / status=ok`，**零次 503**；末了真合成一段并回读 `/api/audio/*.wav` =
  **206,580 B / `RIFF` 头**；收尾 `POST /api/model/unload` 后 `nvidia-smi used 9489 → 3344 MiB`，
  停服后 7869 无监听、无残留进程（`used 2790 MiB`）。产物侧：`twine check` 双 PASSED、
  wheel 1065 条、解包按包导入跑 `run_startup_selfcheck(enforce=True)` =
  **`16/16/0` + `manifest_signed=true`**。
  **这一轮没覆盖的格子**（别当成已过）：`tests/e2e/` 的 Playwright 没在本机跑（本轮改动不含
  模板/静态资源，CI 的 `Playwright E2E Tests` 在该改动合入的 `777bfe1` 上是 success）；
  字体菜单/断网首屏/换页竞态三项**没重跑**（上次取证是 2026-09-19/20 的 v2.2.2 工作树）；
  `Setup.exe` 真机安装与 26 GB 分卷仍未验收。
- **v2.2.5 只做产物侧回读，没重跑本清单**（2026-09-22 22:3xZ）。这一版的内容是发布链路本身
  （docker 层缓存 `mode=min`+scope、`release-gate` commit status、DCO 按作者豁免、治理文档），
  运行时代码零改动，而且主工作树当时有另一个写者在改 38 项 —— 全量门禁的前提"跑测试期间冻结
  工作树"不成立，所以**不拿局部绿灯冒充本清单执行过**。产物侧取证（RP 自己构建、挂上 Release 的那份）：
  wheel 28,381,932 B / **1065 条目** / `METADATA Version: 2.2.5`，sdist 28,337,605 B /
  **1208 文件**（`app/` 1064 + `tests/` 138 + 根 6），两者的 sha256 与 Release 的 `SHA256SUMS`
  以及 GitHub 自算 `asset.digest` **双向对上**（`4eba3c28…` / `04d012b6…`）；完整性三件套
  （清单 / `.sig.ed25519` / 公钥）在两个产物里都在；解包后**按包导入**跑
  `run_startup_selfcheck(enforce=True)` = `total=16 passed=16 failed=0 skipped=0
  manifest_signed=true`，rc=0（按文件路径加载会打断相对导入 → 假红，见 GOTCHAS #150）。
  顺带一条分发面事实：**`tests/` 随 sdist 分发**（138 个），wheel 里则没有 ——
  `release-please-config.json` 把 `tests` 放进 `exclude-paths` 的取舍依据就在这里（§1 第 5 条）。
- 上一次全量执行：2026-09-19，v2.2.2 工作树。机器侧佐证：全量 `pytest`（含 e2e、服务在线，
  `--cov=app/integrated_app`）**2084 passed / 40 skipped / 0 failed，8m14s**，覆盖率 51.94%
  （门禁 45%）；mypy 103 = 基线；`tests/e2e/` 68 passed + 5 skipped（跳过的 5 条是视觉回归，
  基线由 Linux CI 生成，Windows 本机按平台守卫 skip）。同一套用 `--cov=integrated_app`
  口径统计约 50.0%，差异只在统计范围，两者都过门禁。
  第 1/2/3/4/5/6/9/10 项已取证——其中第 4 项由 §5.2 的字体菜单核对转为机器判定，
  第 3 项的水印可懂度已于 2026-09-19 由人工听 A/B 判定**通过**（三对同源 A/B 均达标），
  第 5 项的"按钮要做事"已真机验过：400 错误块带 `role="alert"` + 「立即加载」「重试」两个按钮，
  点重试 XHR 计数 +1 且错误块重新渲染（此前该按钮调的是从未定义的函数，纯死键，#133），
  第 9 项 6 轮切换实测空闲显存回到切换前水平（spread 416MB，无单调递增）。
  **人工项已全部走完**：第 7 项屏幕阅读器由仓库所有者于 **2026-09-21 实机判定通过**
  （NVDA/VoiceOver 走"选音色→输入→生成→播放"，遮罩阶段变化有朗读）；第 8 项断网首屏已于
  2026-09-20 转为机器判定（`scripts/check_offline_first_paint.py`，见 §5.2）。
- 2026-09-21 本轮补记（分发产物与前端竞态，`fix/tab-race-and-dist-payload`，基线 = `origin/main` e104809）：
  * **门禁**：非 e2e **2078 passed / 35 skipped / 0 failed，2m33s，覆盖率 52.11%**；
    `tests/e2e`（服务在线）**68 passed / 5 skipped**；mypy **103 = 基线**；
    ruff check 全通过、`ruff format --check` 359 文件已格式化；完整性清单 **16/16 一致**；
    无硬编码路径 exit 0；`test_portable_bundle.ps1` **49 条断言全通过**；
    `release_gate.ps1` **六步全部 PASS**（新增第 ⑥ 步真跑 `assemble_release_staging`：
    staging 1069 文件 / woff2 777 / OFL 14 / 禁区命中 0）。
  * **对产物复验**（不是开发树）：`test_portable_bundle.ps1 -KeepArtifacts` 解包后从产物起服务，
    字体菜单 **14/14 可用**、页×引擎矩阵 **12/12 通过**；产物内含
    `LICENSE 11,515 B / NOTICE 1,419 B / THIRD_PARTY_NOTICES.md 3,280 B / SECURITY.md 7,579 B`
    与 777 woff2 + 1 ttf + 14 份 OFL（`fonts/` 30 MB，`app/` 36 MB）。
  * **抓到的静默漏发**：`SECURITY.md` 迁到 `.github/` 后便携包白名单仍写根路径，`Test-Path`
    不过就跳过 → 整个版本没进包且构建全绿。两条链路的白名单循环改为**缺项即 throw**，
    并新增 `tests/test_packaging_manifest.py`（25 条断言，含 4 条变异自证）常驻把关（#134）。
  * **新修的用户可见缺陷**：侧栏换页竞态 —— 注入 1.5s 慢响应后，**修前 6/6 组末态被先点那一页
    占住**（用户在错的页面上点生成 → 400），带 `hx-sync="#tab-content:queue last"` 后 **0/6**（#136）。
  * **依赖锁集自洽性**（比告警更要紧）：两份 lock 当时自相矛盾 —— `tokenizers==0.23.2` 同时违反
    `transformers 4.52.1` 要的 `<0.22` 与 `indextts` 要的 `==0.21.0`；`antlr4-python3-runtime` 4.13.2
    违反 hydra/omegaconf 的 `==4.9.*`；`mpmath` 1.4.1 违反 sympy 的 `<1.4`。后果不是 CI 红，而是
    **按 lock 装环境的人拿到破图**，开发机能跑只是因为比 lock 早（本机 30/73 条与 lock 不一致）。
    三行改到合法且与已验证环境一致后 `check_pin_crossconflicts.py` 从 **exit 1 → exit 0**（95 包
    冲突 0、未核验 0），并接进 `ci.yml` 每次 PR 跑 —— 它原先只挂在 pre-commit 的 `files:` 条件上，
    已经坏在 main 上的锁集它永远看不见。
  * **transformers 上界被实测钉死**：4.57.6 下 VoxCPM2 正常（243,164 B / RMS 5360）但
    **IndexTTS 2.5 / 2.0 双双 `infer_v2_5` / `infer_v2` 导入失败**；回到 4.52.1 + tokenizers 0.21.0
    后三引擎真推理全通（2.5：214,040 B / RMS 6176；2.0：205,124 B / RMS 6926；VoxCPM2：230,148 B /
    RMS 4615；每次卸载显存回到 ~3.5 GB）。故 `pyproject`/`requirements.txt` 里那句
    `transformers>=4.57.0`（9-14 搭在一条只讲 gpu-smoke 的提交里进来的）站不住，但改回 4.52.x 会让
    pip-audit 与 Trivy 两道 CI 安全门禁同时变红（4.52.x 实带 **16 个**公告：4 条要 4.53、4 条要
    5.x 大版本、8 条**上游根本没有修复版本**）——**下界与引擎可用性
    互斥**，2026-09-21 定为**出路①并落地**：下界回到 `>=4.52.1,<4.53`（`tokenizers>=0.21.0,<0.22`），
    两道扫描器改成**逐条带理由的已接受风险豁免**（号取自 CI 真实输出 + api.osv.dev 复核，见
    `docs/SECURITY_DEPENDABOT_TRIAGE.md` §1/§1a；pip-audit 侧 16 个 PYSEC 号，Trivy 侧因那两步带
    `severity:CRITICAL,HIGH` + `ignore-unfixed:true` 只剩 3 条 CVE，`.trivyignore.yaml` 于
    2026-12-31 到期、到期自动重新变红；pip-audit 没有到期机制，靠文档 §4 的同一日期人守），
    并新增 `tests/test_dependency_consistency.py`（11 条）核对"声明 ↔ 锁 ↔ 豁免清单"三者不互相漂移 —— 这正是原先缺位的那类"下界棘轮"，也是这条错误下界能在 main 上
    存活一周没人发现的原因；其中 D4 专门堵本轮自己踩出来的两个**静默失效**坑：
    `--ignore-vuln` 的号从 CI 表格里目抄被列宽截断成前缀假号（5 条里只生效 1 条）、
    Trivy 的输入名写成 `ignorefile`（v0.36.0 只认 `trivyignores`，写错仅警告、豁免等于没接）；
    同一步还暴露出**镜像装 4.52.4 而锁钉 4.52.1**（`Dockerfile:33/38` 按声明装而非按锁装），
    已作为已知缺口记在分诊文档 §3b，本轮未动 Dockerfile。
    引擎加载失败时的报错也不再断言"PyPI 无 indextts 包"，改为带上底层 ImportError 与版本不匹配提示。
    20 条 Dependabot 告警因此**没有一条能靠现在就升级消掉**，已于 2026-09-21 经所有者授权
    按 §5 映射逐条 dismiss（每条 `dismissed_comment` 自带绑定理由，open 现为 0 条），
    分诊见 `docs/SECURITY_DEPENDABOT_TRIAGE.md`；
    且那 20 条只覆盖有 GHSA 记录的 10 个公告，**8 条 PYSEC-only 的 Dependabot 从不开单**。
  * **GPU 冒烟覆盖面（本轮补齐 2.0，并发现"每周兜底"其实从没跑过）**：
    `scripts/gpu_smoke_minimal.py` 原先只覆盖 voxcpm2 + indextts2 —— `tts-1` / `tts-1-hd` 两个
    OpenAI 模型名里没有 IndexTTS **2.0** 的位置。现在第 0 步是引擎导入探针（`engine_imports`，
    用服务所在解释器 import `indextts.infer_v2` / `infer_v2_5`，失败即硬停并点名
    `transformers>=4.52.1,<4.53`），2.0 走 `/api/generate/indextts2` + `expected_engine` 真合成并
    从 `data-audio-filename` 回取 `/api/audio/<file>` 校验 RIFF，另加一条版本门负向
    （加载 2.0 却声明 `indextts2` 必须被点名拒绝）。
    **本机真机逐步验到**：`engine_imports` / `csrf_ticket` / `synth_tts-1`（310,470 B RIFF）/
    `switch_indextts2` / `openai_tts1hd_contract`（400）/ `synth_indextts2`（298,746 B RIFF）
    全部 OK；`switch_indextts20` OK，但其后的 `synth_indextts20` **没跑通**，
    撞在下面那条 CUDA 缺陷上（冒烟如实报 FAIL，没有粉饰成跳过）。
    过程中还修掉一个潜伏缺陷：脚本所有 POST 都不带 CSRF 双提交票，而 `/v1/audio/speech`
    并不在豁免路径里 —— 不带就 403 `CSRF_MISSING`，也就是说这条冒烟只要真跑就会红。
    更要紧的一条：**它从没真跑过**。`gpu-smoke.yml` 三次 schedule run（09-07 / 09-14 / 09-21）
    的 `gpu-smoke` job 全是 `skipped`，run 顶层却是 success —— secret 里根本没有
    `REPO_ADMIN_TOKEN`，且仓库**零个注册 runner**。本轮把跳过改成 `::warning` + 写进 job summary，
    并把每天真能跑的引擎导入兜底放到 `docker-smoke.yml`（CPU 托管 runner，只 import 不推理），
    同时给它的触发器补上 `requirements.txt` / `requirements-lock.txt` / `pyproject.toml`
    （以前依赖区间被改坏时这个作业压根不会触发）。
    **仍未消掉**：runner 不存在 → IndexTTS 2.5/2.0 的真推理与"两个 IndexTTS 变体的导入"在 CI 上
    依然零覆盖，只有本机能验；要让这条变成 CI 事实，需要注册一台带 `gpu` 标签的
    self-hosted runner 并配 `REPO_ADMIN_TOKEN`（归所有者）。
  * **真机跑冒烟时定位并修掉一个 CUDA 崩溃（原以为是"第二次切换"，其实是预热抢占）**：
    症状 `CUDA error: device-side assert triggered`，且**整个 CUDA context 被毒化** ——
    之后同进程所有推理连带失败，连切换的回滚重载都报 503。服务端时间线是决定性证据：
    `21:05:57 [req=bg-model-startup-load] 开始合成 text='你好'`（预热）→
    `21:05:59 [req=f663d3ae…] 开始合成`（用户请求）→ `21:06:01` **两条一起** assert。
    根因：预热走 `model_optimizer.warmup_indextts2`，从后台加载线程**直接调 `engine.infer`**，
    绕开了用户侧那把 per-engine `asyncio.Semaphore(1)`（`routes/generate/utils.py:439`），
    而 IndexTTS 推理不可重入。"第二次切换才挂"是巧合 —— 切换必然触发预热，而 `loaded`
    状态早于预热完成，脚本/用户就是会在预热那 2~5 秒里把请求挤进去。
    （先前记的三个"嫌疑"全被证伪：冷切换、参考音频素材、异步错位归因都排除过。）
    修法：在引擎这一层加按注册名分组的 `threading.RLock`（`_engine_infer_lock`），`infer`
    变成持锁薄包装并用 `functools.wraps` 保住签名（接口测试仍按 14 个参数检查），
    于是队列 / SSE / 预热 / OpenAI 口任何入口都被串行化。
    **真机验证**：① 原并发条件（预热进行中就压请求）从必崩变成 2.5 = 336,642 B、
    2.0 = 239,674 B，且与串行跑出来的**字节数完全一致**（锁只串行化，不改结果）；
    ② 完整冒烟 10 格全绿：`engine_imports` → `ready` → `csrf_ticket` → voxcpm2 321,962 B →
    切 2.5 → `openai_tts1hd_contract` 400 → 2.5 = 386,796 B → 切 2.0 → 2.0 = 402,400 B →
    版本门负向按预期拒绝；结束显存回落 2,666 MiB。
  * **镜像构建的间歇性失败已定位并修掉（PR #111）—— 记一次我自己的误判纠偏**：
    先前这里写的是"确定性失败、根因是 `update-alternatives: error: alternative path
    /usr/share/man/man7/bash-builtins.7.gz doesn't exist`、jammy 归档期 man-db/manpages 组合问题"。
    **那是错的**，两条都错：
    ① 那条 error 在**成功**的构建里同样出现（run 35607107888 13:42:31 `#11 97.72`），
      它是 `apt-get upgrade` 期间的良性噪声，与失败无因果；为验证它而开的探针 PR #110 五步全绿，
      等于把自己的前提证伪，故关闭。
    ② 也不是确定性基础设施故障：同一个 `Dockerfile`，12:19/12:28 红、13:40 绿。
      "重试一次仍一模一样"只说明那 9 分钟里 PPA 一直取不到，不说明它不是网络问题。
    真 fatal（红 run 的 `--log-failed`）：
    `Ign:7 https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu jammy/main amd64 Packages`
    → `W: Some index files failed to download. They have been ignored, or old ones used instead.`
    → **`apt-get update` 仍然返回 0** → 两条命令之后才炸
    `E: Unable to locate package python3.12` / `python3.12-venv`。
    绿色 run 同一行是 `Get:7 ... Packages [44.3 kB]`。
    性质上正是"警告后照旧继续"：报错文案（包不存在）与真原因（索引没抓下来）完全对不上，
    下一次抖动随时会再红一遍。修法与验收（含不等抖动的破坏态复现）见 #111 与 `Dockerfile` 注释。
  * **wheel 漏打完整性清单三件套已修（发版后核对产物发现，PR #117）**：v2.2.2 的 wheel 里
    `integrated_app/security/` 只有 `.py`，`integrity_manifest.json` / 其 `.sig.ed25519` /
    验签公钥 `.pem` **三件都没进包**；而 `config.yaml` 默认 `security.integrity_selfcheck.enforce: true`，
    自检在"清单不存在"分支只 `logger.info("跳过自检")` 就返回 → **纯 `pip install` 那条部署路径上
    P0 完整性保护一条都没跑，配置却声称它在强制运行**（Docker/便携另拷源码树，所以不受影响，
    这也是容器启动探测一直绿着的原因）。两处一起改：`package-data` 逐条点名三件；
    enforce 开着却没清单 → `RuntimeError` 并给出三条出路（生成 / 重打 wheel / 显式关 enforce），
    非强制模式保持原"跳过"语义。
    验收不靠"配置看起来对"：本机 `python -m build` 前后对比（包内条目 1062 → 1065，三件均 `OK`），
    再把 wheel 解到临时目录当成安装环境真跑一遍 —— 有清单时 `enforce=True` 返回
    `total/passed/failed/signed = 16/16/0/True`；把清单挪走则拒绝启动、`enforce=False` 仍返回 `skipped=16`。
    CI 侧在 `Build (sdist/wheel)` 作业里加了产物核对（只查 pyproject 不算数：setuptools 行为一变就谎报）。
    守卫 `tests/test_integrity_selfcheck_packaging.py`（8 条，含"仓库自带三件套所以 enforce 该通过"的
    反空验证，与"CI 不再核对 wheel 就红"的自证）；`integrity_selfcheck.py` 属 16 个被签模块，
    清单已重算并重签（`--verify` PASS、sync 16/16）。
  * **仍未覆盖**：桌面安装包链路（staging → data 7z → NSIS）**无任何 workflow 调用**、本机也无从安装
    （`scripts/installer/` 只有一个 4.3 MB `Setup.exe`、无同目录分卷），所以 `unpack_desktop.ps1`
    新加的许可/字体落地核对只过了语法层，`release_gate.ps1` 的第 ⑥ 步也只在发版/dispatch 时跑；
    htmx 1.9.10 在队列换页时自抛的一次 `insertBefore` TypeError（末态正确）未清，需另案升级 vendored 库。
    （原先记的"`docker build` 未实跑"已消掉：PR #100 的 `Boot hardened container & probe` 里，
    新加的"镜像内许可文本 + 字体计数"步骤在真构建产物上结论 success。）

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