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
  * **已知缺口**：CI 冒烟 `scripts/gpu_smoke_minimal.py` 只覆盖 voxcpm2 + indextts2（走 OpenAI 口，
    而 `tts-1` / `tts-1-hd` 两个模型名里没有 IndexTTS **2.0** 的位置）；2.0 的真推理今天人工验过，
    要接进冒烟需改用 `/api/generate/indextts2` 形态并在 GPU runner 上复验，另案。
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