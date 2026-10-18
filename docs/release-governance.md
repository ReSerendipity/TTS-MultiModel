# 发布 / 回滚 规范（本仓本地化版）

> **来源与权威**：公共条款见 `ReSerendipity/.github` 的 `REPOSITORY_GUIDELINES.md` §10（发布 / 分发统一规范）。
> 本文件按 `RELEASE_GOVERNANCE_TEMPLATE.md` 收敛，只填**本仓特有**内容，**不重写、不抵触 §10 的公共条款**。
> 未确定项一律写「待定（负责人 + 触发条件）」，不留空占位符。
> 配套实体：`docs/rollback_sop.md`（回滚 SOP + `scripts/rollback_release.py`）、`docs/SRE_RUNBOOK.md`（SLA/SLO/DR）。

---

## 0. 远端优先（根原则，不可改写）

发布链的构建、打包、扫描、上传、推送**全部在 GitHub Actions 远端完成**；本地只做三件事：改版本位、落 CHANGELOG、`git tag` + `git push`。本地不执行打包 / 镜像构建 / 发布上传。

## 1. 版本号规范

- 遵循 **SemVer** `MAJOR.MINOR.PATCH`；预发布用 `-alpha.N` / `-beta.N` / `-rc.N`。
- **已发布最新 = v2.7.0**（2026-10-17）。
- **唯一权威版本位**：`pyproject.toml` `[project].version`，由 release-please manifest `.release-please-manifest.json` 的 `"."` 驱动（当前 `2.7.0`）。
- 必须同步的其它版本位（仓库内跟踪共 11 处，数目以 `tests/test_version_consistency.py::_site_versions()` 实际返回为准）：
  `version.json` `$.version`、`config.yaml` 顶层 `version`、`desktop/package.json` `$.version`、
  `desktop/src-tauri/tauri.conf.json` `$.version`、`desktop/src-tauri/Cargo.toml` `$.package.version`、
  `desktop/src-tauri/Cargo.lock`、`scripts/installer/setup.nsi` 的 `!define APP_VERSION`（及其 `OutFile` / `VIProductVersion`）、
  `deploy/kubernetes/deployment.yaml` 的镜像 tag、`CHANGELOG.md`、本文本句、`scripts/installer/version.json`（装配中间物）。
  （`desktop/package-lock.json` 在 `.gitignore` 里，不是仓库内版本位。）
- 版本位漂移由 `tests/test_version_consistency.py::test_all_version_sites_agree` 逐条比对钉住——**漂一处即判红**；失败信息逐条标注哪处「RP 自动」、哪处「手工同步」。
- 自动化方式：**release-please（自动位 + 人工补齐）**，备用手工 tag 路径见 §3。
  - **自动位（release-please 直接改）**：
    - `pyproject.toml`（python release-type 自带）；
    - `release-please-config.json` 的 `extra-files` 共 4 条：`version.json` `$.version`、`desktop/package.json` `$.version`、`desktop/src-tauri/tauri.conf.json` `$.version`、`desktop/src-tauri/Cargo.toml` `$.package.version`（实测在 PR #120 上每条只动 1 行）。
  - **人工位（必须人补）**：
    - `config.yaml`——**绝不交给 release-please**：它的 YAML 写入器不是「改那一个字段」，而是解析后整份重排；同一条 PR 上实测把 232 行配置改成 160 增 / 160 删，注释行从 96 行清零（`model_source_mode` 选型说明、SSL 怎么打开、`vram_safety_margin_gb` 的算式全丢），还会去掉 `"127.0.0.1"` 这类引号。已从 extra-files 摘除，归手工同步。
    - `desktop/src-tauri/Cargo.lock`——归 cargo 生成。
    - `scripts/installer/setup.nsi` 的 `OutFile` / `APP_VERSION` / `VIProductVersion` 三处——NSIS 注释符是 `;`，用不了 release-please `generic` 类型要求的 `# x-release-please-version` 行内标记。
    - `deploy/kubernetes/deployment.yaml` 的镜像 tag——必须跟 ghcr 上真存在的标签走。
    - `version.json` 的 `changelog` / `release_date`——RP 只抬 `$.version`；桌面壳 `updater.rs` 会把 changelog 显示给用户，只抬版本号会发出「自称新版本、说明写着旧版」的包。
  - 人工补齐提交要加在 release 分支上、**并且紧接着就合**：任何一次 main push 都会让 RP 重写那条分支、冲掉人工提交（2026-09-22 真被冲掉过 `1c2597a`）。
  - 走手工 tag 路径发版后，要把 `.release-please-manifest.json` 的 `"."` 抬到刚发的版本，否则 RP 会在下一条 release PR 里把版本号再抬一格、两边互踩。
  - 配套闸：`test_extra_files_entries_are_all_actionable` 钉住「每条 extra-files 今天确实能命中」；`test_bundled_changelog_describes_its_own_version` 拦「版本号新、说明旧」。

## 2. CHANGELOG 管理

- 遵循 **Keep a Changelog**：`Added / Changed / Fixed / Deprecated / Removed / Security`。
- 每次合并 `main` 在 `[Unreleased]` 追加；type 映射 `feat→Added`、`fix→Fixed`、`refactor→Changed`。
- 发版时 `[Unreleased]` → `[VERSION] - YYYY-MM-DD`。
- **本仓口径**：走 release-please 自动路径时 CHANGELOG 由 RP 按提交信息生成，**不要手写 `[Unreleased]`**——RP 不消费它（实测 #140 手写条目被留在 `[2.2.5]` 与 `[2.2.4]` 之间，成永久假账，已删）；手写收敛 `[Unreleased]` 只适用于 §3 手工 tag 路径。

## 3. 发布流程（远端）

1. 确认本批内容就绪：自动路径由 RP 生成 CHANGELOG；手工路径才把 `[Unreleased]` 收敛进 `[<新版本>]` 并改日期。`tests/test_version_consistency.py` 必须绿。
2. 同步 §1 全部人工版本位。
3. 二选一发版，不要同时走：
   - **自动路径（默认）**：合入 release-please 在 main push 后自动开 / 刷新的 release PR；RP 随后打 tag、建 Release 并挂 wheel / sdist / `SHA256SUMS` 资产。
     - release PR 由 bot 用 `GITHUB_TOKEN` 推 commits，GitHub 不级联触发 workflow，故 PR 上看不到 CI；合并前**本地跑** `python scripts/check_release_readiness.py --root <检出>`（`release-gate` 会把结论以 commit status `release-gate` 打在 release 分支 head 上；加成必需检查是仓库所有者的一键决定）。
     - RP 用 `GITHUB_TOKEN` 打的 tag 不级联，`docker-publish.yml` 的 tag 腿不会自动跑，semver 镜像 tag（`:X.Y.Z`）缺失；**合完立刻** `gh workflow run docker-publish.yml --ref v<X.Y.Z>`，再 `gh run list --workflow docker-publish.yml --branch v<X.Y.Z>` 确认 completed/success，最后回头核 `deployment.yaml` 指的 tag 真存在——顺序反了就是「清单自称能上线、实际拉不到镜像」。
   - **手工路径**：本地 `git tag v<X.Y.Z>` → `git push origin v<X.Y.Z>` → 远端建 **draft Release** 并挂资产 → 人工审核后 `published`；发完按 §1 抬 manifest。
4. 发布后按 §10.5 留可复核记录 `docs/execution/RELEASE_VERIFICATION.md`（固定流程尚未建立：待定（仓库所有者 + 下次发版时触发））。
5. 盯到终态：`gh run list` / `gh pr checks` 确认绿；红了当场修或 revert 止损。

> 便携分卷（core/torch/model，约 26 GB）与桌面 NSIS 增量包不在默认 Release 资产里：需要 `scripts/release_gate.ps1 -ModelDir ... -RuntimeDir ... -TorchWheelDir ...` 真构建 + 单独点头（权重未变时重传逐字节浪费）；`-OutDir` / `-StagingDir` 必须显式指到 `outputs/` 之下（漏传会被禁区护栏直接 fail）。`SHA256SUMS.txt` 必须是 LF 清单（Windows 上写成 CRLF 会让 `sha256sum -c` 整份假红）。

## 4. 分发产物与完整性

| 产物 | 构建位置 | 标签 / 命名 | 完整性手段 |
|---|---|---|---|
| Python wheel / sdist（`tts_multimodel-*.whl` / `.tar.gz`） | **远端** | 不可变 semver `vX.Y.Z` | `SHA256SUMS.txt` |
| ghcr 容器镜像 `ghcr.io/reserendipity/tts_multimodel` | **远端** | semver（`:X.Y.Z` / `:X.Y`）+ `:sha-<commit>`；**无 floating `latest`** | Trivy `CRITICAL,HIGH` 阻断；上线按 digest 钉版 |
| 桌面安装包（Tauri v2 壳 / NSIS）与便携分卷 | **远端** | semver | `SHA256SUMS.txt` + Tauri ed25519 更新器签名 |

- 容器镜像：推 `ghcr.io`，`permissions: packages: write`，用 `GITHUB_TOKEN` 登录；tag 取 semver + 构建 `sha`，**不得以 `latest` 作发布基线**（本仓现状：`type=raw,value=latest,enable={{is_default_branch}}` 已于 2026-10-18 移除，§10.3 差异清零；历史 `:latest` tag 仍留在 GHCR 上，但不再更新、不作下游引用基线）。
- 镜像引用必须全小写（镜像名 `tts_multimodel` 下划线由 `${{ github.repository }}` 小写得来；镜像名与 tag 形状由 `tests/test_image_name_consistency.py` 钉）；镜像内不含模型权重（`.dockerignore` 排除 `model/`），运行时挂载。
- 镜像发布前须经 Trivy `CRITICAL,HIGH`（`ignore-unfixed`）**阻断**（本仓 `exit-code: '1'`，符合口径）；豁免逐条登记在 `.trivyignore.yaml`（理由 + expiration）。
- 镜像链 `provenance` / `sbom`：本仓 `provenance: false`、未开 `sbom: true`。两个原因并记：①镜像是单架构（未声明 `platforms`），attestation 会以额外 manifest 进 index，与单架构消费方冲突；②本仓发布链是「build（`push: false`）→ Trivy 扫 → 手动 `docker push`」，而 attestation 只能由 buildx 在其自身 push 时附加，当前结构下即便写 `provenance: true` 也不会真正产出 attestation。属 §10.4 推荐口径的 WARN（未采纳、理由已记录）；是否改结构：待定（仓库所有者）。
- **不使用 GPG 分离签名**（2026-10-18 裁决）；`gpg-signed-release.yml` 已于 2026-10-18 删除（§10.6 欠账清零）。

## 5. 回滚

- **判定**（满足任一即触发）：成功率跌破 SLO / readiness degraded / P0-P1 安全事件 / 契约断裂（阈值与演练见 `docs/rollback_sop.md`、`docs/SRE_RUNBOOK.md`）。
- **执行**：`git revert <bad-commit>`（保历史、可再 forward）；本仓用 `python scripts/rollback_release.py --target v<tag>` 反向 revert + 切镜像 tag / `kubectl rollout undo`；不可变产物按上一版 tag 重推。权重为外部挂载、与回滚无关；SQLite 不随代码回滚（迁移兼容）。
- **破坏性动作**：重指向 / 删除 tag、移动任何远端 ref → **须仓库所有者单独授权**。
- **回滚后验证**：liveness `GET /api/health/ping`（内存级）；readiness `GET /api/health/ready`（`/readyz`，模型就绪 / 流量门禁）。

## 6. SLA / 错误预算

- 目标可用性 **≥99.5%/月** ⇒ 错误预算 **≈3.6h/月**；超出即冻结非紧急发布并复盘（指标口径见 `docs/SRE_RUNBOOK.md` §1）。

## 7. 发布前检查清单

- [ ] 版本位全部同步（见 §1 清单；`tests/test_version_consistency.py::test_all_version_sites_agree` 绿）
- [ ] `version.json` 的 `changelog` / `release_date` 已改本次版本（`test_bundled_changelog_describes_its_own_version` 拦旧说明）
- [ ] CHANGELOG 已收敛（自动路径由 RP 生成；手工路径 `[Unreleased]` → `[VERSION] - YYYY-MM-DD`）
- [ ] 全量 pytest 通过（非 GPU 回归 0 failed）
- [ ] `ruff` 全绿；mypy 遵守 `.ci/mypy_baseline.txt` 棘轮
- [ ] `python scripts/check_spec_refs.py` 输出 `SPEC_REFS STATE=CHECKED`（SKIPPED 也退 0，见 `.github/scripts/spec_refs_gate.sh`）
- [ ] 自动路径合并前本地跑 `python scripts/check_release_readiness.py --root <检出>`
- [ ] 镜像 digest 钉版 + Trivy `CRITICAL,HIGH` 阻断通过，豁免已在 `.trivyignore.yaml` 登记理由 + expiration
- [ ] 完整性自检 16/16 通过
- [ ] tag 已推送且 `gh release view v<版本>` 可见 Release；自动路径合 PR 后已 `gh workflow run docker-publish.yml --ref v<版本>` 补 semver 镜像 tag
- [ ] **本地未产出任何发布交付物**（全部远端构建）

---

## 差异点采集清单（本仓取值）

| 占位符 | 本仓取值 |
|---|---|
| 版本位文件与字段 | `pyproject.toml` `[project].version`（当前 `2.7.0`），manifest `.release-please-manifest.json` 的 `"."` 驱动 |
| 其它需同步版本位 | 见 §1 清单（共 11 处，以测试实际返回为准） |
| 版本一致性校验入口 | `tests/test_version_consistency.py::test_all_version_sites_agree` |
| 自动化方式 | release-please（自动位 + 人工补齐），手工 tag 备用路径 |
| 发布 workflow 文件名 | `release-please.yml` / `release-gate.yml` / `docker-publish.yml`；Release 资产由 RP 建 Release 时挂（build-release 腿） |
| 产物形态与标签规则 | wheel / sdist；ghcr semver + sha；Tauri / NSIS 安装包（见 §4 表） |
| 完整性手段 | `SHA256SUMS.txt` + Tauri ed25519 更新器签名 + Trivy 阻断 |
| 回滚判定阈值 | 成功率跌破 SLO / readiness degraded / P0-P1 / 契约断裂（`docs/rollback_sop.md`） |
| 回滚后验证命令 | `curl /api/health/ping`、`curl /api/health/ready`（`docs/SRE_RUNBOOK.md`） |
| 技术栈 | Python（FastAPI）+ 前端 + Tauri v2 桌面壳 + K8s 部署 |
| 部署目标 | ghcr 镜像 + `deploy/kubernetes`（远端）；桌面安装包走 Release 资产 |

## 禁止事项

- 不得重写 §10 的公共条款（远端优先、tag 驱动、版本位一致、不可变标签、扫描阻断、无 GPG）。
- 不得引入 `latest` 作发布基线；不得保留或新建 GPG 签名流程。
- 不得留空占位符，不得编造阈值。
