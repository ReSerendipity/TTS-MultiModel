# 发布/回滚/SLA 总纲（Release Governance）

> **本仓为家族发布/回滚/SLA 关键源仓之一**：`docs/SRE_RUNBOOK.md`（SLA/SLO/回滚/DR 演练）与 `docs/rollback_sop.md`（一键回滚 SOP + `scripts/rollback_release.py`）即为权威实体版。
> **来源**：家族通用 `.spec_audit/family_release_governance.md`（泛化自本仓 + DraftPeek VERSIONING.md），此处为总览索引。
> **适用范围**：TTS_MultiModel 全项目发布、回滚与运行稳定性。

---

## 1. 版本号规范

- 遵循 SemVer `MAJOR.MINOR.PATCH`。**已发布最新 = v2.3.0（2026-09-25）**。
  版本号出现在 11 处（`version.json`/`pyproject.toml`/`config.yaml`/`desktop/*`/`scripts/installer/setup.nsi`），
  但只有 tag + GitHub Release 同时存在才算发出去；核对：`gh release view v<版本>`。
  > 其中 `desktop/package-lock.json` 在 `.gitignore` 里（不是仓库内的版本位）；仓库内跟踪的 9 处
  > 由 `tests/test_version_consistency.py::test_all_version_sites_agree` 逐条比对，漂一处就红
  > （v2.2.2 发版前 `deploy/kubernetes/deployment.yaml` 的镜像 tag 就停在 2.2.1，红得下来）。
  >
  > **哪几处 release-please 会自动改、哪几处必须人改**（它只支持 `json|toml|yaml|xml|pom|generic`，
  > 没有 regex）：自动的是 `pyproject.toml`（python release-type 自带）与
  > `release-please-config.json` 的 `extra-files` 里那 5 条 —— `version.json`、
  > `desktop/package.json`、`desktop/src-tauri/tauri.conf.json`、`desktop/src-tauri/Cargo.toml`
  > 的 jsonpath 站点，实测在 PR #120 上每条只动 1 行。
  > **`config.yaml` 曾经挂过 `yaml` 类型，已摘除**：release-please 的 YAML 写入器不是"改那一个字段"，
  > 而是解析后整份重排 —— 同一条 PR 上它把 232 行配置改成了 **160 增 / 160 删**，
  > 注释行从 **96 行变成 0 行**（`model_source_mode` 的选型说明、SSL 怎么打开、
  > `vram_safety_margin_gb` 的算式），并把 `"127.0.0.1"` 这类引号去掉。它现在归手工同步。
  > 另三处也**必须人补**：`desktop/src-tauri/Cargo.lock`（归 cargo 生成）、
  > `scripts/installer/setup.nsi` 的 `OutFile`/`APP_VERSION`/`VIProductVersion`
  > （NSIS 注释符是 `;`，用不了 `generic` 要求的 `# x-release-please-version` 行内标记）、
  > `deploy/kubernetes/deployment.yaml` 的镜像 tag（要跟 ghcr 上真存在的标签走）。
  > **还有一处是散文**（今天这张版本位表 11 行，它是最后收进来的一行）：本文开头那句
  > "已发布最新 = v<X.Y.Z>" —— RP 不碰它，而它真会漂
  > （v2.2.5 发出去之后这里还停在 v2.2.4），所以已被 `test_all_version_sites_agree`
  > 当版本位钉住，补齐时顺手改一行。
  > `version.json` 里 RP 只抬 `$.version`，**`changelog` 与 `release_date` 也是手工位**
  > （壳的 `updater.rs` 会把 changelog 显示给用户，只抬版本号会发出"自称 2.2.4、说明写着 2.2.3"的包）。
  > 补齐这几处的那条提交要加在 release 分支上、**并且紧接着就合**：任何一次 main push 都会让
  > RP 重写那条分支，把你补的提交冲掉（2026-09-22 真被冲掉过一次 `1c2597a`）。
  > 失败信息里会逐条标注哪处是『RP 自动』、哪处是『手工同步』，
  > 并由 `test_extra_files_entries_are_all_actionable` 钉住"每条 extra-files 今天确实能命中"。
- 版本位：`pyproject.toml` + `config.yaml`（release-please 驱动前端缓存参数需人工补齐，见本地 AGENTS.md #9（AGENTS.md 为本地维护、不随仓库分发））+ `CHANGELOG.md`。
- **发版有两条路，别同时走**：
  1. 自动：合入 release PR（RP 在 main push 后自动开/刷新）并等它自己打 tag 发 Release。
     **已于 2026-09-22 走通一次**：v2.2.5 的 tag `9125a3e`、Release 与 4 个资产
     （`SHA256SUMS`、`SHA256SUMS.scripts`、wheel、sdist）都是 RP 自己产出/挂上的。
     走这条路必须知道五件事：
     - **它只抬 5+1 处自动位**，剩下 5 类手工位（`config.yaml`、`Cargo.lock`、
       `setup.nsi` 三处、k8s 镜像 tag、`version.json` 的 `changelog`/`release_date`）
       要你在 release 分支上**另加一条提交**补齐；而**任何一次 main push 都会让 RP 重写那条分支**，
       把你补的提交冲掉（今天冲掉过一次：`1c2597a`）。所以顺序是"补齐 → 立刻合"，中间别合别的。
     - **DCO 曾经让它结构上不可合**：RP 的提交作者是 `github-actions[bot]`，永远签不出
       `Signed-off-by`，而分支保护要求 DCO —— 已按"提交作者"豁免（#135），并留下
       `tests/test_dco_bot_exemption.py` 钉住"豁免不是后门"。
     - **release PR 拿不到 CI**（见下面那段），所以合并前的判据是本地跑
       `python scripts/check_release_readiness.py --root <release 分支检出>`，
       结论也会以 commit status `release-gate` 打在 PR head 上（把它加成必需检查是 owner 的一键决定）。
     - **它不会替你发出 semver 镜像标签**（原先这条写成"不会替你发出镜像"，以偏概全，见下）：
       "bot 的 `GITHUB_TOKEN` 不级联"这条规则确实吃掉**它自己打的 tag 与它自己建的 Release**
       这两个下游事件。实测对照（2026-09-22）：v2.2.2 / v2.2.3 / v2.2.4 三次手工 `git tag`
       各留下 `docker-publish.yml` 一条 `ev=push br=vX.Y.Z` 和 `gpg-signed-release.yml`
       一条 `ev=release`；v2.2.5 起这两个**腿**就都没了。
       但 2026-09-25 复算要补一句口径：`docker-publish.yml` 的 `on.push` 里本来就有
       `branches: [main]`，所以合 release commit 那次 main push **会**触发它 —— 实测 v2.2.5
       当天 13:29:56Z 就有一条 `push/main` 且 success。所以"0 条"说的是 **tag 腿**，
       不等于"镜像没发"：那次发的是 `:latest` 与 `:sha-<long>`，而 `type=semver` 在分支
       push 上不出标签，因此 `:2.2.5` 当时确实不存在（14:12:36Z 那条
       `workflow_dispatch --ref v2.2.5` 就是去补它的；v2.2.6 同型，09-23T07:30:15Z）。
       于是合完 release PR 后要立刻补一手 `gh workflow run docker-publish.yml --ref vX.Y.Z` ——
       `metadata-action` 的 `type=semver` 在 tag ref 上就能出 `:2.2.5`，**不用改工作流**。
       不补的后果是具体的：`deploy/kubernetes/deployment.yaml` 指着 ghcr 上不存在的标签，
       而且这不能靠"把镜像 tag 退回上一版"消红 —— 那会让 `test_all_version_sites_agree`
       把版本位一致性一起判红（2026-09-22 的 v2.2.5 就卡在这一点上，见 §2 第 5 步）。
       GPG 签名那一格现在不痛（`GPG_PRIVATE_KEY` 未配，作业进 skip 分支只出 notice），
       但 owner 配上密钥后，RP 发的每个版本都要把 `gpg-signed-release.yml` 一起手工补跑。
     - **"什么算一次发版"默认宽到不实用**：实测 #136（两个 `docs(release):` 提交、零代码改动）
       合并 25 秒后 RP 就开出 **#137 `chore(main): release 2.2.6`**，CHANGELOG 段是 3 条
       `### Documentation`（它把 PR 的两个提交**和那条 merge commit 各算一条**）。
       一条纯文档改动要人补 5 类手工位 + 补跑镜像，这税不划算，所以
       `release-please-config.json` 加了 `packages["."].exclude-paths = ["docs", "tests"]`。
       这个字段有三个坑，都在 `src/util/commit-exclude.ts` 里（匹配式是
       `file.startsWith(entry + "/")`，**不是 glob**）：
       ① 写 `docs/**` 永不命中，只能用裸目录名 `docs`；RP 自己的测试用的也是 `['pkg3','pkg1']`；
       ② **仓库根上的单个文件排不掉**（那个 `+"/"` 让 `README.md`、`CHANGELOG.md`、
       `release-please-config.json` 都做不成条目）。**但"排不掉"不等于"会发版"** —— 我原先
       由这一点推"只改发版配置仍会 mint 一个版本"，#138 合入后被实测否掉：那条提交改的正是
       `release-please-config.json`，而 RP 的日志是
       `✔ No user facing commits found since 9125a3e… - skipping`。原因是类型侧还有一道闸：
       `ci:` 不算 user facing（`docs:` 算，这才是当初 2.2.6 被开出来的原因）。
       ③ 条目写成 `.` 会把**所有**提交排掉，RP 从此一声不响再也不发版（本仓踩过同形状的
       "永远绿却什么都不做"，见下面 v2.2.2 那段旧账）。
       闸是 `test_exclude_paths_entries_are_all_actionable`：glob / `.` / 不存在的目录 /
       清空列表 / 覆盖 `app/` 这 5 种写法都实测过会红。
       另外两条例外的账要记着：
       · **收窄口径会留下"死信 release PR"** —— RP 对已开出但内容不再算数的 PR 既不更新也
         **不关闭**（走 `skipping` 分支），#137 就是靠人关掉的。以后每次动 `exclude-paths`
         或提交类型口径，都要去 `gh pr list --author app/github-actions` 看一眼有没有旧的要清。
       · **排除 `tests` 不是零代价**：实测 v2.2.5 的 sdist 里有 **138 个 `tests/` 文件**
         （wheel 里 0 个，`docs/`/`scripts/`/`.github/` 也都是 0）。所以"只改测试"不发版这条
         是对** wheel（用户真正装的东西）**成立的判断，对 sdist 不成立 —— 是有意的取舍，别当成无影响。
  2. 手工：`git tag -a` + `gh release create`（v2.2.2 / v2.2.3 / v2.2.4 走的就是这条）。
     手工发版之后 RP 会在下一条 release PR 里把版本号再抬一格（它按 manifest 算），
     所以手工发完要把 `.release-please-manifest.json` 一起抬到刚发的版本，否则两边在版本号上互踩。
     —— 反过来也成立：走自动路径时别顺手再手工打同版本的 tag。
  > **走第 1 条时，release PR 上看不到任何 CI 检查**（实测 #120：`gh pr checks 120` 是空的）。
  > 原因是 GitHub 的固定行为：由 `GITHUB_TOKEN` 产生的提交不再级联触发 workflow，
  > 而 release PR 的提交正是 bot 用 `GITHUB_TOKEN` 推的。
  > 后果很具体：§1 上面列的那些**手工同步位**在 release PR 上不会变红 ——
  > `test_all_version_sites_agree` 要到合并进 main 之后才红，那时 Release 已经发出去了。
  > 所以合 release PR 之前**必须本地跑**：`python scripts/check_release_readiness.py --root <检出>`
  > （外加 §2 第 1–2 步的手工位补齐）。机器闸已经补上了一半（#134）：`release-please.yml` 会在
  > 开/刷新 PR 之后跑同一个脚本，并把结论以 commit status `release-gate` 报在 release 分支的
  > head SHA 上 —— 它是**可见信号不是拦截**，把它加成必需检查仍是 owner 的一键决定。
  > 而且要注意它只覆盖"版本位/可发布性"这一类判据：pytest 全量、真机验收这些在 release PR 上
  > 永远不会跑，那是本地路径的活。
  历史上 `release-please.yml` 曾是**结构性空转**（`skip-github-pull-request: true` + 缺
  config/manifest + 5 个 v4 不认的入参 → 每次 main push 输出 `found 0 possible releases` 后绿，
  v2.2.2 因此三次绿 run 都没 Release）；已修，现在它真的会开 PR。挂在它下面的
  `build-release`（sdist/wheel + SHA256SUMS）同样只在 `release_created == true` 时执行。
  > **`[Unreleased]` 不是中转站，RP 不读它**（2026-09-23 实测）：#140 里我手写了一条
  > `## [Unreleased]` → `### Bug Fixes` 的条目，RP 开 #141 时**没把它折进 2.2.6** —— 它照提交信息
  > 另生成两条（真实提交 + merge commit 各一条），把我那段原文留在原地，位置在 `[2.2.5]` 与
  > `[2.2.4]` 之间。于是"已经随 2.2.6 发出去的内容"会永久挂在 Unreleased 底下，是一份自相矛盾的
  > 假账（已删）。**走自动路径时别手写 `[Unreleased]`**：要留的说明写进提交信息与本文，
  > CHANGELOG 由提交信息生成。§2 第 1 步那句"`[Unreleased]` 收敛进 `[<新版本>]`"只对第 2 条
  > （手工发版）成立。

## 2. 发布流程

1. 确认本批内容已进 CHANGELOG（`[Unreleased]` 收敛进 `[<新版本>]` 并改日期）——
   **这一步只属于手工路径**：走 §1 第 1 条时 CHANGELOG 由 release-please 按提交信息生成，
   手写 `[Unreleased]` 不会被消费，只会在 `[2.2.5]` 与 `[2.2.4]` 之间留一份永久假账（见 §1 末）；
   `tests/test_version_consistency.py` 必须绿（它就是"§5 版本位全部同步"那格闸）
2. 同步 `config.yaml` 顶层 `version` 与 `deploy/kubernetes/deployment.yaml` 的镜像 tag
   （都不在 extra-files 里：前者因为 RP 的 YAML 写入器会整份重排并洗掉注释，见 §1；
   后者要跟 ghcr 上真存在的标签走，且**镜像名必须等于工作流真的推的那个**（`tts_multimodel` 下划线 —— 由 `${{ github.repository }}` 小写得来；名字与 tag 形状由 `tests/test_image_name_consistency.py` 钉，版本位由 `test_all_version_sites_agree` 钉）。两处都红在版本位一致性测试里）
3. 手工发版（v2.2.2/v2.2.3 走的就是这条；自动那条见 §1 末）：
   `git tag -a v<版本> -m "..." <SHA>` → `git push origin v<版本>` →
   `python -m build` + `twine check dist/*` + `SHA256SUMS.txt` →
   `gh release create v<版本> --notes-file ... <资产>`；核对 `gh release view v<版本>`
4. 手工发完把 `.release-please-manifest.json` 的 `"."` 抬到刚发的版本并推 main，
   否则 RP 会在下一条 release PR 里把版本号再抬一格（两边互踩）
5. CI 盯到终态；容器镜像**发布走 `docker-publish.yml`**，上线时按 digest 钉，禁止 `:latest`
   - **v2.2.5 起这条要自己踩**：走自动发版路径时 RP 用 `GITHUB_TOKEN` 打的 tag 不级联，
     `docker-publish.yml` 不会因此运行，`:2.2.5` 这个标签在 ghcr 上就不存在（见 §1 第 4 条）。
     补法：`gh workflow run docker-publish.yml --ref v<版本>`，跑完用
     `gh run list --workflow docker-publish.yml --branch v<版本>` 确认有了一条 completed/success，
     再回头核 `deployment.yaml` 指的那个 tag 真的存在 —— 顺序反了就是"清单自称能上线、实际拉不到镜像"。

> 便携分卷（core/torch/model，~26 GB）与桌面增量包**不在**第 3 步的默认资产里：
> 需要 `scripts/release_gate.ps1 -ModelDir ... -RuntimeDir ... -TorchWheelDir ...` 真构建 + 单独点头
> （权重未变时重传 13 个 model 分卷是逐字节浪费）。GPG 分离签名同理 ——
> `GPG_PRIVATE_KEY` 这个 secret 目前不存在，`gpg-signed-release.yml` 只会 notice 跳过。
> 构建本身还有两个口径要记（2026-09-23 都实测过）：
> **① `-OutDir`/`-StagingDir` 必须显式指到 `outputs/` 之下** —— 脚本默认值是
> `$Root\dist\bundles` 与 `$Root\dist\portable-staging`，也就是**忘传参数就会往禁区写 26 GB**。
> 现在漏传会被护栏直接 fail（`# 禁区护栏` 那段），真要写 `dist/` 得显式改脚本、而不是靠漏参数发生。
> **② `SHA256SUMS.txt` 必须是 LF 清单** —— 早先用 `File.WriteAllLines` 在 Windows 上写成 CRLF，
> `sha256sum -c` 于是把 `` 当成文件名的一部分，18 条全报 "No such file or directory"：
> **一整份全红的假失败**，真相是独立用 hashlib 复算 18/18 逐字节相符。Windows 上也可用
> `Get-FileHash` 逐卷核；用 `sha256sum` 的退出码时别把它接进管道 ——
> `sha256sum -c ... | tail` 之后 `$?` 是 `tail` 的（这条今天又踩了一次）。

### 2.1 v2.3.0 走自动路径的实测记录（2026-09-25）

- #145 被 RP 从 2.2.7 重写为 **2.3.0**（#142 带 `feat:` → minor，与"提交类型决定 bump"一致），
  head 上加了一笔人工同步提交 `dd6f7d6` 补齐五处手工位，随后 squash 合并 → main `cd80d735`
  （提交作者 `github-actions[bot]`），合并时刻 08:32:03Z。
- **tag `v2.3.0` 与 Release 由 RP 创建：`publishedAt = 2026-09-25T08:32:15Z`、`isDraft = false`**，
  4 个资产齐（`tts_multimodel-2.3.0-py3-none-any.whl`、`tts_multimodel-2.3.0.tar.gz`、
  `SHA256SUMS`、`SHA256SUMS.scripts`）。
- main push 侧：**30 条 check success + 1 skipping**（reusable 模板里的 `Test + Coverage`），零失败。
- **两个时间戳（tag 创建 → 镜像落库）**：`docker-publish` 被那次 main push 触发
  （run `36113445697`，08:32:05Z 起、**09:03:14Z success**，≈31 min），但它推上去的是
  `ghcr.io/reserendipity/tts_multimodel:latest` 与 `:sha-cd80d735c838d8744e0fdd7ed88efe1c5a64f137`
  （同 run 日志的 `Processing tags input` 可核：两条 `type=semver` 在分支 push 上不产出标签）。
  **`:2.3.0` 不在其中** —— 分支 push 拿不到 semver 标签，`deploy/kubernetes/deployment.yaml`
  里写的 `2.3.0` 当时只是目标值。补法沿用 v2.2.5 / v2.2.6 的先例，已执行：
  `gh workflow run docker-publish.yml --ref v2.3.0` → run `36131581847`
  （11:50:21Z 起、**12:03:46Z 四个 manifest 推完**、终态 **12:21:57Z success**（含 Trivy 腿）），
  落库 `:2.3.0`、`:2.3`、`:latest`、`:sha-cd80d735…`，digest `sha256:aa2494c4…`。
  证据取自该 run 日志的 `pushing manifest for …` 行；ghcr 的包版本列表这个 token 拿不到
  （`GET /users/reserendipity/packages/container/tts_multimodel/versions` 回 403，要 `read:packages`），
  所以外部不可复核这一点也一并记着。
  **于是 tag 创建 → 镜像落库 = `08:32:15Z` → `12:03:46Z`**；中间 3.5 小时是"等人补标签腿"的空窗，
  不是构建耗时 —— 下次发版要在合并后立刻 dispatch，别让 k8s 清单空指一个不存在的标签。
  顺带一条自我修正：我原以为 tag 腿上 `type=raw,value=latest,enable={{is_default_branch}}`
  会关掉 `:latest`，日志证明它照样重推了 `:latest`，且与 `:2.3.0` 同一 digest。
- `release-gate` 的 tag 腿与 `gpg-signed-release` 对 v2.3.0 **各 0 条 run**（这两条 workflow 的
  `head_branch` 全集只到 `v2.2.4`）；GPG 那一格另因 `GPG_PRIVATE_KEY` 未配置而本就走 skip + notice，
  记为已知状态，不算故障。
- 一条边界修正：上面说"release PR 拿不到任何 CI"，但**这次 #145 上 30 条 check 真跑了**。
  级联规则吃的是"**由 `GITHUB_TOKEN` 产生的提交**"，不是"bot 开的 PR" —— 人工往 release 分支补一笔
  再推，必需检查就能在 release PR 上完整跑一遍（本版第一次出现这个状态，也是它能带着
  `pytest 12 格全绿` 被合并的原因）。

## 3. 回滚（详见 `docs/rollback_sop.md`）

- 判定：成功率跌破 SLO / readiness degraded / P0-P1 安全 / 契约断裂。
- 执行：`python scripts/rollback_release.py --target v<tag>`（反向 revert，保历史）+ 切镜像 tag / `kubectl rollout undo`。
- 权重/DB：权重为外部挂载与回滚无关；SQLite 不随代码回滚（有迁移兼容）。

## 4. SLA / 错误预算（详见 `docs/SRE_RUNBOOK.md` §1）

- 可用性 ≥99.5%（月度）⇒ 错误预算 ≈ 3.6h/月。
- liveness `/api/health/ping`（内存级）；readiness `/api/health/ready` / `/readyz`（深度）。

## 5. 发布前检查清单

- [ ] 版本位全部同步（`pyproject.toml` + `config.yaml` + `CHANGELOG.md`；仓库内跟踪的 11 处
      由 `tests/test_version_consistency.py::test_all_version_sites_agree` 机器核对 ——
      数目以 `_site_versions()` 实际返回为准，含 `scripts/installer/version.json` 那个装配中间物
      和本文 §1 开头那句"已发布最新"，这格是**复看**用）
- [ ] `version.json` 的 `changelog`/`release_date` 已改成本次版本（RP 只抬 `$.version`；
      `test_bundled_changelog_describes_its_own_version` 会拦"版本号新、说明旧"）
- [ ] CHANGELOG `[Unreleased]` 已改版本 + 日期
- [ ] 全量 pytest 通过（门禁实测：非 GPU 回归 0 failed）
- [ ] `ruff` 全绿；mypy 遵守 `.ci/mypy_baseline.txt` 棘轮
- [ ] `python scripts/check_spec_refs.py` 退出码 0
- [ ] 镜像 digest 钉版 + Trivy 关键/高危扫描绿
- [ ] 完整性自检 16/16 通过
- [ ] tag 已推送，且 `gh release view v<版本>` 能看到 Release（**推 tag 本身不会建 Release**：
      走手工路径时要自己建 + 挂资产；走 RP 路径时由它合 PR 后打 tag 并建 Release，见 §1）