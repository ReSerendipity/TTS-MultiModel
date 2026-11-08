# TTS-MultiModel 归档索引（_archive/）

> 归档区按治理规范 §12.1 使用仓根 `_archive/`（下划线前缀＝非活动区，§10 审计自动豁免）。
> 归档只移动文件、不改写正文；`docs/README.md`、`docs/SRE_RUNBOOK.md`、`docs/TODO.md`、`docs/runbooks/BUS-FACTOR-2026-11-01.md` 及 2 个测试 / 1 个路由注释中的引用已改新路径。
> 说明：这批 runbook 是门禁（D6 / O4 / R3 / R4 / F6 / F7 / F8 / O3）的执行证据链，归档后**仍在仓库内可检索**，只是不再位于活动目录 `docs/`。

| 原路径 | 归档后路径 | 归档日 | 类别 | 一句话结论 | 是否仍被引用 |
|---|---|---|---|---|---|
| `docs/runbooks/2026-09-agent-memory-archive.md` | `_archive/执行记录/2026-11/2026-09-agent-memory-archive.md` | 2026-11-08 | 执行记录（记忆归档） | 把 `.workbuddy` 等即将清理的会话记忆里仍有长期价值的事实按主题固化入库 | 是：`docs/README.md`、已归档 runbook 内部（已改新路径） |
| `docs/runbooks/2026-10-31-restore-drill.md` | `_archive/执行记录/2026-11/2026-10-31-restore-drill.md` | 2026-11-08 | 执行记录（演练） | 目标库级备份恢复 + 发布回滚演练，为 R4 / D6 / O4 门禁补证（阶段 1） | 是：`docs/README.md`、`docs/SRE_RUNBOOK.md`、`docs/TODO.md`（已改新路径） |
| `docs/runbooks/2026-11-01-o3-failover-drill.md` | `_archive/执行记录/2026-11/2026-11-01-o3-failover-drill.md` | 2026-11-08 | 执行记录（演练） | O3 单点与切换演练（阶段 2 · 降格执行），记录人工恢复耗时与围栏 | 是：`edge_tts.py`、`test_edge_tts_fallback.py`、`docs/SRE_RUNBOOK.md`、`docs/TODO.md` |
| `docs/runbooks/2026-11-02-f6-batch-b-and-fastfail.md` | `_archive/执行记录/2026-11/2026-11-02-f6-batch-b-and-fastfail.md` | 2026-11-08 | 执行记录（整改阶段 11） | F6 B 批：把 8192 / streaming 两判据钉到测试代码，22 行「0.0s 失败」先证伪后诊断 | 是：`docs/README.md`、`docs/SRE_RUNBOOK.md`、`docs/TODO.md` |
| `docs/runbooks/2026-11-02-f6-test-db-isolation.md` | `_archive/执行记录/2026-11/2026-11-02-f6-test-db-isolation.md` | 2026-11-08 | 执行记录（整改阶段 10） | 测试套写污染生产库的止血、零增长证明与可逆隔离（只隔离不删除） | 是：`docs/SRE_RUNBOOK.md`、`docs/TODO.md` |
| `docs/runbooks/2026-11-02-o4-backup-wiring.md` | `_archive/执行记录/2026-11/2026-11-02-o4-backup-wiring.md` | 2026-11-08 | 执行记录（整改阶段 9） | 备份链路可修半边：F3 死路径退役 + 备份集覆盖可判定 + 调度接线（未注册） | 是：`docs/SRE_RUNBOOK.md`、`docs/TODO.md` |
| `docs/runbooks/2026-11-02-r4-real-history-rollback-drill.md` | `_archive/执行记录/2026-11/2026-11-02-r4-real-history-rollback-drill.md` | 2026-11-08 | 执行记录（整改阶段 8） | R4 真仓 756 提交 / 22 tag 完整历史重跑的回滚演练 | 是：`docs/TODO.md`、`docs/SRE_RUNBOOK.md` |
| `docs/runbooks/2026-11-03-f7-venv-entrypoint-and-f8-hooks-drift.md` | `_archive/执行记录/2026-11/2026-11-03-f7-venv-entrypoint-and-f8-hooks-drift.md` | 2026-11-08 | 执行记录（整改阶段 13） | F7 文档解释器入口失效的 23 个站点分类处置 + F8 钩子漂移登记（只登记不修） | 是：`docs/TODO.md`、`docs/SRE_RUNBOOK.md`、`test_run_python_entry.py` |
| `docs/runbooks/2026-11-03-f8-funasr-install.md` | `_archive/执行记录/2026-11/2026-11-03-f8-funasr-install.md` | 2026-11-08 | 执行记录（整改阶段 14） | 系统 Python 补装 `funasr==1.4.16`，解除 F8 前置条件红灯 | 是：`docs/TODO.md`、`docs/SRE_RUNBOOK.md` |
| `docs/runbooks/2026-11-06-f8-hooks-wiring.md` | `_archive/执行记录/2026-11/2026-11-06-f8-hooks-wiring.md` | 2026-11-08 | 执行记录（F8 收口） | `core.hooksPath` 从 `local/hooks` 改回 `.githooks` 并做 fresh clone 双向验证 | 是：`docs/TODO.md`、`docs/README.md` |
| `docs/runbooks/2026-11-06-o3-multipath-failover-drill.md` | `_archive/执行记录/2026-11/2026-11-06-o3-multipath-failover-drill.md` | 2026-11-08 | 执行记录（O3 扩展） | 把 O3 切换演练从单路径扩到多路径（进程内离线扩展），属主裁决单点本体接受为既定形态 | 是：`docs/README.md` |
| `docs/runbooks/2026-11-06-o4-schedule-registration.md` | `_archive/执行记录/2026-11/2026-11-06-o4-schedule-registration.md` | 2026-11-08 | 执行记录（O4 注册） | 备份调度注册进 Windows 任务计划 + 回读证明 + 下次时间与触发人成文 | 是：`docs/README.md`、`docs/SRE_RUNBOOK.md`、`docs/TODO.md` |

## 未归档（活动文档，判定理由）

| 文档 | 保留理由 |
|---|---|
| `docs/runbooks/BUS-FACTOR-2026-11-01.md` | Bus Factor「登记本体」：含现状量化、补偿措施与**可判定的升级/解除判据**，属长期台账而非单次留痕 |
| `docs/runbooks/key-rotation-SOP-19.md` | 完整性签名密钥轮换 SOP，仍为现行操作手册（并被 `configs/spec_refs_baseline.json` 列为引用基线） |
| `docs/qoder-snapshot.md` | `.qoder` 本地资产层机制说明（脚本 + 测试），现行文档 |
| `docs/SECURITY_AUDIT_TTS_MultiModel.md` | docs 索引标「现行」，安全审计结论与后续 triage 台账配套 |
| `docs/SRE_RUNBOOK.md`、`docs/DOD.md`、`docs/CODING_STANDARDS.md`、`docs/release-governance.md`、`docs/rollback_sop.md` | 现行手册 / 规范 |
| `docs/TODO.md`、`docs/EXCEPTION_SUPPRESSION_LEDGER.md`、`docs/TRAINING_LEDGER.md`、`docs/SECURITY_CODEQL_TRIAGE.md`、`docs/SECURITY_DEPENDABOT_TRIAGE.md`、`docs/SECURITY_REMEDIATION_TRACKER.md` | 台账（长期维护；TODO 的超大体量属批 6 分卷议题） |
| `docs/SECURITY_MATRIX.md`、`docs/SECURITY.md`、`docs/PRIVACY_POLICY.md`、`docs/USER_AGREEMENT.md`、`docs/THIRD_PARTY_NOTICES.md`、`docs/agent-contract.md` | 安全矩阵 / 契约 / 法律文本 |
| `docs/发布与分发-单文件安装包.md`、`docs/README.md`、`docs/adr/*`、`docs/model-cards/*` | 发布形态规范、索引、ADR（不迁移）、模型卡 |
