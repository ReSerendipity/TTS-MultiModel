# 发布/回滚/SLA 总纲（Release Governance）

> **本仓为家族发布/回滚/SLA 关键源仓之一**：`docs/SRE_RUNBOOK.md`（SLA/SLO/回滚/DR 演练）与 `docs/rollback_sop.md`（一键回滚 SOP + `scripts/rollback_release.py`）即为权威实体版。
> **来源**：家族通用 `.spec_audit/family_release_governance.md`（泛化自本仓 + DraftPeek VERSIONING.md），此处为总览索引。
> **适用范围**：TTS_MultiModel 全项目发布、回滚与运行稳定性。

---

## 1. 版本号规范

- 遵循 SemVer `MAJOR.MINOR.PATCH`。**已发布最新 = v2.2.2（2026-09-22）**。
  版本号出现在 11 处（`version.json`/`pyproject.toml`/`config.yaml`/`desktop/*`/`scripts/installer/setup.nsi`），
  但只有 tag + GitHub Release 同时存在才算发出去；核对：`gh release view v<版本>`。
  > 其中 `desktop/package-lock.json` 在 `.gitignore` 里（不是仓库内的版本位）；仓库内跟踪的 9 处
  > 由 `tests/test_version_consistency.py::test_all_version_sites_agree` 逐条比对，漂一处就红
  > （v2.2.2 发版前 `deploy/kubernetes/deployment.yaml` 的镜像 tag 就停在 2.2.1，红得下来）。
- 版本位：`pyproject.toml` + `config.yaml`（release-please 驱动前端缓存参数需人工补齐，见本地 AGENTS.md #9（AGENTS.md 为本地维护、不随仓库分发））+ `CHANGELOG.md`。
- 发布**目前**由人工 `git tag -a` + `gh release create` 完成：**`release-please.yml` 是结构性空转**，
  不会替你发版 —— 它设了 `skip-github-pull-request: true` 而仓库从未产生过 release PR，
  于是每次 main push 都输出 `found 0 possible releases` 后成功；它还传了 5 个 v4 不认的入参
  （`package-name`/`changelog-path`/`draft`/`label`/`prerelease` 全被忽略），且没有
  `release-please-config.json` / `.release-please-manifest.json`。
  挂在它下面的 `build-release`（sdist/wheel + SHA256SUMS）因 `release_created != true` 同样从不执行。
  （v2.2.2 曾在工作流全绿的情况下既没 tag 迁移也没 Release，原因即此；修法另见待落的 RP 修复 PR。）

## 2. 发布流程

1. 确认本批内容已进 CHANGELOG（`[Unreleased]` 收敛进 `[<新版本>]` 并改日期）；
   `tests/test_version_consistency.py` 必须绿（它就是"§5 版本位全部同步"那格闸）
2. 同步 `config.yaml` 顶层 `version` 与 `deploy/kubernetes/deployment.yaml` 的镜像 tag
   （都无人自动改；后者红在版本位一致性测试里）
3. 手工发版（RP 目前是空转，见 §1）：
   `git tag -a v<版本> -m "..." <SHA>` → `git push origin v<版本>` →
   `python -m build` + `twine check dist/*` + `SHA256SUMS.txt` →
   `gh release create v<版本> --notes-file ... <资产>`；核对 `gh release view v<版本>`
4. CI 盯到终态；容器镜像**发布走 `docker-publish.yml`**，上线时按 digest 钉，禁止 `:latest`

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
- [ ] CHANGELOG `[Unreleased]` 已改版本 + 日期
- [ ] 全量 pytest 通过（门禁实测：非 GPU 回归 0 failed）
- [ ] `ruff` 全绿；mypy 遵守 `.ci/mypy_baseline.txt` 棘轮
- [ ] `python scripts/check_spec_refs.py` 退出码 0
- [ ] 镜像 digest 钉版 + Trivy 关键/高危扫描绿
- [ ] 完整性自检 16/16 通过
- [ ] tag 已推送，且 `gh release view v<版本>` 能看到 Release（**推 tag 不会触发自动发版**：
      `release-please.yml` 目前结构性空转，见 §1；Release 需手工建 + 手工挂资产）