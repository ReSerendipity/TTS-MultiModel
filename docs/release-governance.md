# 发布/回滚/SLA 总纲（Release Governance）

> **本仓为家族发布/回滚/SLA 关键源仓之一**：`docs/SRE_RUNBOOK.md`（SLA/SLO/回滚/DR 演练）与 `docs/rollback_sop.md`（一键回滚 SOP + `scripts/rollback_release.py`）即为权威实体版。
> **来源**：家族通用 `.spec_audit/family_release_governance.md`（泛化自本仓 + DraftPeek VERSIONING.md），此处为总览索引。
> **适用范围**：TTS_MultiModel 全项目发布、回滚与运行稳定性。

---

## 1. 版本号规范

- 遵循 SemVer `MAJOR.MINOR.PATCH`。当前 **v2.2.1**。
- 版本位：`pyproject.toml` + `config.yaml`（release-please 驱动前端缓存参数需人工补齐，见 AGENTS.md #9）+ `CHANGELOG.md`。
- 发布由 `release-please` 自动生成 GitHub Release 并打 semver tag。

## 2. 发布流程

1. 确认 CHANGELOG `[Unreleased]` 条目完整；release-please 提交后自动收敛版本
2. `git push` 触发 `release-please.yml`
3. 同步 `config.yaml` 顶层 `version`（release-please 不自动同步，需人工）
4. CI 盯到终态；容器镜像钉 digest 发布，禁止 `:latest`

## 3. 回滚（详见 `docs/rollback_sop.md`）

- 判定：成功率跌破 SLO / readiness degraded / P0-P1 安全 / 契约断裂。
- 执行：`python scripts/rollback_release.py --target v<tag>`（反向 revert，保历史）+ 切镜像 tag / `kubectl rollout undo`。
- 权重/DB：权重为外部挂载与回滚无关；SQLite 不随代码回滚（有迁移兼容）。

## 4. SLA / 错误预算（详见 `docs/SRE_RUNBOOK.md` §1）

- 可用性 ≥99.5%（月度）⇒ 错误预算 ≈ 3.6h/月。
- liveness `/api/health/ping`（内存级）；readiness `/api/health/ready` / `/readyz`（深度）。

## 5. 发布前检查清单

- [ ] 版本位全部同步（`pyproject.toml` + `config.yaml` + `CHANGELOG.md`）
- [ ] CHANGELOG `[Unreleased]` 已改版本 + 日期
- [ ] 全量 pytest 通过（门禁实测：非 GPU 回归 0 failed）
- [ ] `ruff` 全绿；mypy 遵守 `.ci/mypy_baseline.txt` 棘轮
- [ ] `python scripts/check_spec_refs.py` 退出码 0
- [ ] 镜像 digest 钉版 + Trivy 关键/高危扫描绿
- [ ] 完整性自检 16/16 通过
- [ ] tag 已推送触发 `release-please.yml`