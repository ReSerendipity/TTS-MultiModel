# Bus Factor 登记与观察判据（2026-11-01 · E7/P25 带项上线）

> 对应评估门禁：**E7**（关键知识无单点 Bus Factor>1）、**P25**（核心系统知识单点有登记与备份），均为 ⛔P0（条件），评估口径「触发场景未发生，按基线 P1 带项上线并登记观察」。
> 本文即该「登记」本体：现状量化 + 知识单点载体点名 + 补偿措施 + **可判定**的升级/解除判据。落点说明：`docs/execution/` 在本仓属禁止入库面（AGENTS.md 硬约束，例外仅 RELEASE_VERIFICATION.md 且当前并未跟踪），故按仓规落 `docs/runbooks/`（tracked 运维记录位）。

## 1. 现状量化（实测数字，非估算；2026-11-01 11:10:37 +0800 实测）

- 全仓 **744** commits（`git rev-list --count HEAD`，2026-11-01 11:10:37）
- **全仓历史始于 2026-07-19**（`git log --reverse --format='%ad' --date=short | head -1`）——距今约 3 个半月，**「近 12 个月」窗口等于全量、该框架无意义**，本节全部比例以全量 744 为分母
- 全仓 **7 个不同邮箱**（`git log --format='%ae' | sort -u | wc -l` = 7）

**表 1 · 按邮箱聚合**（`git log --format='%ae' | sort | uniq -c | sort -rn`）：

| 邮箱 | commits | 占比（分母=744） | 归类 |
|---|---|---|---|
| `259081123+ReSerendipity@users.noreply.github.com`（GitHub noreply 公开标识） | 558 | 75.0% | 人类（所有者） |
| `dev@tts-multimodel.local` | 135 | 18.1% | 人类（所有者） |
| `…49699333+dependabot[bot]@users.noreply.github.com` | 32 | 4.3% | bot |
| `…41898282+github-actions[bot]@users.noreply.github.com` | 10 | 1.3% | bot |
| `doro@rewq.cn` | 4 | 0.5% | 人类（所有者） |
| `zengyangc@outlook.com` | 4 | 0.5% | 人类（所有者） |
| `zwork@local` | 1 | 0.1% | 人类（所有者） |

**表 2 · 署名 × 邮箱交叉**（`git log --format='%an%x1f%ae' | sort | uniq -c | sort -rn`；署名含本机用户名者按仓规脱敏为署名A/B，原始映射见 git log）：

| 署名 | 邮箱 | commits |
|---|---|---|
| ReSerendipity | noreply（同上） | 284 |
| 署名A | noreply（同上） | 160 |
| TTS MultiModel Dev | `dev@tts-multimodel.local` | 135 |
| TTS MultiModel Dev | noreply（同上） | 114 |
| dependabot[bot] | dependabot 邮箱 | 32 |
| github-actions[bot] | github-actions 邮箱 | 10 |
| 署名A | `doro@rewq.cn` | 4 |
| ReSerendipity | `zengyangc@outlook.com` | 3 |
| zwork | `zwork@local` | 1 |
| 署名A | `zengyangc@outlook.com` | 1 |

**三种多样性的区分与判定**：署名多样性（10 种署名）≠ 邮箱多样性（7 个邮箱）≠ 贡献者多样性——**7 个邮箱中 5 个人类邮箱均指向同一所有者**（noreply + `dev@…local` 为其惯用别名，其余 3 个为个人邮箱别名），bot 邮箱 2 个为自动化。三者各自都不构成 Bus Factor 冗余：署名可随意填写、别名邮箱同属一人、bot 提交无维护判断。**人类贡献者数 = 1，Bus Factor = 1**（与评估报告 E7「736 commits 同一邮箱三种署名」的结论方向一致，但其「同一邮箱」表述同样应读作「同一人的多个署名/邮箱」）。

> **更正记录（2026-11-01）**：本文首版 §1 把「4 种署名变体之和 692」误标为「同一邮箱 692（95.6%）」——该和实际跨两个邮箱（noreply 558 + `dev@tts-multimodel.local` 135）；并误用「近 12 个月 724」分母（全仓历史仅始于 2026-07-19，12 个月窗口不成立）。派单方独立复算指出后，以本节两表重做证据；**结论不变（Bus Factor=1）**，修正经新提交落库（不 amend）。

## 2. 核心模块与「除所有者外无人能改」判定

| 模块/资产 | 单点判定依据 |
|---|---|
| `app/integrated_app/app_server.py` + `routes/**`（路由/中间件组装） | 全部历史提交均为单维护者；无第二人 review 记录 |
| `app/integrated_app/engines/**`（7 引擎接入） | 引擎注册显式实参、采样率单一事实源等约定**只记载于 AGENTS.md**（不随仓分发） |
| `app/integrated_app/security/**`（完整性自检/清单签名） | 清单再生成+重签流程只存在于 AGENTS.md/本地 SOP |
| `scripts/backup_history_db.py`、`scripts/rollback_release.py` 等 DR/发布链 | 修复与演练记录 2026-10-31 起由本会话建立，仍单维护者 |
| `config.yaml` 人工版本位 / release 手工补齐位 | release-governance §1 记载 RP 不碰的手工位清单——其操作知识依赖维护者记忆+本地文档 |
| `.github/workflows/**`（21 workflows） | 门禁层位口径（提交钩子/推送钩子/CI job 之辨）只记载于 AGENTS.md |

## 3. 知识单点载体点名：AGENTS.md（本地维护、不随仓库分发）

- 事实：`AGENTS.md` **33,767 B，untracked**，被 `.gitignore:383` 忽略（`git ls-files --error-unmatch` 报 not known；`git check-ignore -v` 命中 `.gitignore:383`）——实测 2026-11-01。
- 承载的知识类型：①Agent 行为契约（6 条铁律、自检清单、命令执行形态）；②代码事实源与红线（引擎注册、采样率、i18n、版本位）；③门禁层位口径（哪道闸在提交钩子/推送钩子/CI job）；④本机独有资产地图（`local/`、`docs/agents/`、`.qoder/` 及「探测不到≠缺失」判定）。
- clone 者拿不到的具体后果：本地三层硬门禁（ruff/mypy 棘轮/pytest+覆盖率）在干净克隆必缺，**不得读作预检全绿**；被跟踪文档引用 `docs/agents/**` 链接全部 404 属预期，但新协作者无从知晓这是设计意图；完整性清单再生成/重签、版本位手工补齐等流程无 clone 内替代文档。

## 4. 补偿措施（已在位 + 本轮新增）

- 已在位：`CHANGELOG.md` 头注原文——「> 说明：以下历史条目中提及的 `AGENTS.md` 为**本地维护、不随仓库分发**的资产（`.gitignore` 已忽略）；条目仅为变更发生时的历史记录，clone 读者无需在仓库中查找该文件。」（`DOD.md`、`deploy/kubernetes/README.md` 同款标注）
- 本轮新增：①本文（登记本体+可判定判据）；②`docs/SRE_RUNBOOK.md` §3.4 门禁段将 E7/P25 记为 **[?]**（带项上线，非通过）；③阶段 1–2b 建立的演练/缺口台账（restore-drill、o3-failover-drill、TODO 登记）使关键流程至少在 tracked 面有替代记载。

## 5. 升级/解除判据（可判定条件，不写「视情况」）

**从「带项上线」升级为「阻断」**——满足任一条即触发：
1. 所有者连续 **14 个自然日**不可达（无 commit/issue/review 活动可查）且窗口内出现需要所有者独有知识的操作（tag/手工版本位/完整性清单重签）无法执行；
2. 出现**第二个人类贡献者**的提交触及 §2 清单中任一核心模块，且无所有者成对 review（`git log --format='%an <%ae>'` 中出现新邮箱 + 触及清单路径即判定）;
3. AGENTS.md 引用的任一流程被写入 CI/branch-protection，但 clone 中 `git ls-files` 查不到其引用的非豁免路径（探测不到写成存在的口径违规，机械可查）;
4. release 手工版本位补齐因知识缺失连续 **2 次**失败（版本位一致性测试红）。

**解除/降级条件**：可分发副本（如 `AGENTS.public.md`）入库且 CI 复现本地三层门禁的主要检查，则 E7/P25 的观察理由消失，可重评。

## 6. 待所有者决策（只列利弊与建议，不自行改 .gitignore / 不提交 AGENTS.md）

| 方案 | 利 | 弊 | 建议 |
|---|---|---|---|
| a. AGENTS.md 直接随仓分发 | 单一来源，零漂移 | 文件含本机路径/本地资产约定，直接分发违反仓规（禁止入库本机绝对路径与用户名）并泄露本机布局 | 不建议 |
| b. 拆出 `AGENTS.public.md`（策划版：契约+红线+门禁层位，剥离本机路径） | clone 者拿到可执行约定；本机版保留全量 | 双文件漂移风险——需同步闸（如 spec_refs 类校验） | **建议**，配同步检查 |
| c. 维持本地独占 + 接受 Bus Factor=1 | 零成本 | E7/P25 永久带项；协作者/CI 拿不到约定 | 仅在明确单人项目定位时接受 |
