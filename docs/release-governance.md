# 发布/回滚/SLA 总纲（Release Governance）

> **本仓为家族发布/回滚/SLA 关键源仓之一**：`docs/SRE_RUNBOOK.md`（SLA/SLO/回滚/DR 演练）与 `docs/rollback_sop.md`（一键回滚 SOP + `scripts/rollback_release.py`）即为权威实体版。
> **来源**：家族通用 `.spec_audit/family_release_governance.md`（泛化自本仓 + DraftPeek VERSIONING.md），此处为总览索引。
> **适用范围**：TTS_MultiModel 全项目发布、回滚与运行稳定性。

---

## 1. 版本号规范

- 遵循 SemVer `MAJOR.MINOR.PATCH`。**已发布最新 = v2.2.4（2026-09-22）**。
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
  > `version.json` 里 RP 只抬 `$.version`，**`changelog` 与 `release_date` 也是手工位**
  > （壳的 `updater.rs` 会把 changelog 显示给用户，只抬版本号会发出"自称 2.2.4、说明写着 2.2.3"的包）。
  > 所以下一条 release PR 上，红在这几处是**预期行为**，补一个 commit 即可 ——
  > 失败信息里会逐条标注哪处是『RP 自动』、哪处是『手工同步』，
  > 并由 `test_extra_files_entries_are_all_actionable` 钉住"每条 extra-files 今天确实能命中"。
- 版本位：`pyproject.toml` + `config.yaml`（release-please 驱动前端缓存参数需人工补齐，见本地 AGENTS.md #9（AGENTS.md 为本地维护、不随仓库分发））+ `CHANGELOG.md`。
- **发版有两条路，别同时走**：
  1. 自动：合入 release PR（RP 在 main push 后自动开/刷新，如 #120）并等它自己打 tag 发 Release；
  2. 手工：`git tag -a` + `gh release create`（v2.2.2/v2.2.3 走的就是这条）。
  手工发版之后 RP 会在下一条 release PR 里把版本号再抬一格（它按 manifest 算），
  所以手工发完要把 `.release-please-manifest.json` 一起抬到刚发的版本，否则两边在版本号上互踩。
  > **走第 1 条时，release PR 上看不到任何 CI 检查**（实测 #120：`gh pr checks 120` 是空的）。
  > 原因是 GitHub 的固定行为：由 `GITHUB_TOKEN` 产生的提交不再级联触发 workflow，
  > 而 release PR 的提交正是 bot 用 `GITHUB_TOKEN` 推的。
  > 后果很具体：§1 上面列的那些**手工同步位**在 release PR 上不会变红 ——
  > `test_all_version_sites_agree` 要到合并进 main 之后才红，那时 Release 已经发出去了。
  > 所以合 release PR 之前**必须本地跑**：`.venv/Scripts/python.exe -m pytest tests/test_version_consistency.py`
  > （外加 §2 第 1–2 步的手工位补齐）。要把它变成机器闸，就得给 release PR 配一个
  > 由 `workflow_dispatch`/`push` 触发、能对 release 分支的 head SHA 报 commit status 的作业 ——
  > 那是权限决策，不在本文档的"现状"里。
  历史上 `release-please.yml` 曾是**结构性空转**（`skip-github-pull-request: true` + 缺
  config/manifest + 5 个 v4 不认的入参 → 每次 main push 输出 `found 0 possible releases` 后绿，
  v2.2.2 因此三次绿 run 都没 Release）；已修，现在它真的会开 PR。挂在它下面的
  `build-release`（sdist/wheel + SHA256SUMS）同样只在 `release_created == true` 时执行。

## 2. 发布流程

1. 确认本批内容已进 CHANGELOG（`[Unreleased]` 收敛进 `[<新版本>]` 并改日期）；
   `tests/test_version_consistency.py` 必须绿（它就是"§5 版本位全部同步"那格闸）
2. 同步 `config.yaml` 顶层 `version` 与 `deploy/kubernetes/deployment.yaml` 的镜像 tag
   （都不在 extra-files 里：前者因为 RP 的 YAML 写入器会整份重排并洗掉注释，见 §1；
   后者要跟 ghcr 上真存在的标签走。两处都红在版本位一致性测试里）
3. 手工发版（v2.2.2/v2.2.3 走的就是这条；自动那条见 §1 末）：
   `git tag -a v<版本> -m "..." <SHA>` → `git push origin v<版本>` →
   `python -m build` + `twine check dist/*` + `SHA256SUMS.txt` →
   `gh release create v<版本> --notes-file ... <资产>`；核对 `gh release view v<版本>`
4. 手工发完把 `.release-please-manifest.json` 的 `"."` 抬到刚发的版本并推 main，
   否则 RP 会在下一条 release PR 里把版本号再抬一格（两边互踩）
5. CI 盯到终态；容器镜像**发布走 `docker-publish.yml`**，上线时按 digest 钉，禁止 `:latest`

> 便携分卷（core/torch/model，~26 GB）与桌面增量包**不在**第 3 步的默认资产里：
> 需要 `scripts/release_gate.ps1 -ModelDir ... -RuntimeDir ... -TorchWheelDir ...` 真构建 + 单独点头
> （权重未变时重传 13 个 model 分卷是逐字节浪费）。GPG 分离签名同理 ——
> `GPG_PRIVATE_KEY` 这个 secret 目前不存在，`gpg-signed-release.yml` 只会 notice 跳过。

## 3. 回滚（详见 `docs/rollback_sop.md`）

- 判定：成功率跌破 SLO / readiness degraded / P0-P1 安全 / 契约断裂。
- 执行：`python scripts/rollback_release.py --target v<tag>`（反向 revert，保历史）+ 切镜像 tag / `kubectl rollout undo`。
- 权重/DB：权重为外部挂载与回滚无关；SQLite 不随代码回滚（有迁移兼容）。

## 4. SLA / 错误预算（详见 `docs/SRE_RUNBOOK.md` §1）

- 可用性 ≥99.5%（月度）⇒ 错误预算 ≈ 3.6h/月。
- liveness `/api/health/ping`（内存级）；readiness `/api/health/ready` / `/readyz`（深度）。

## 5. 发布前检查清单

- [ ] 版本位全部同步（`pyproject.toml` + `config.yaml` + `CHANGELOG.md`；仓库内跟踪的 9 处已由
      `tests/test_version_consistency.py::test_all_version_sites_agree` 机器核对，这格是**复看**用）
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