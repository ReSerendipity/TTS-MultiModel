# TTS-MultiModel — docs 索引

TTS_MultiModel（多引擎语音合成）的开发 / 安全 / 运维 / 治理文档入口；面向最终用户的使用说明见根 `README.md`。文档总数 **320（`git ls-files` 口径）**；不含 `.qoder-snapshots/`（17）与 `local/docs-archive/`（本地归档）。下表覆盖 `docs/` 根与子目录共 24 篇 tracked Markdown（`docs/adr/` 5 篇 ADR 另计，合计 29 篇 tracked，`git ls-files 'docs/*.md'` 实测 2026-11-10）；全量逐文件清单与状态见 [`INDEX.md`](INDEX.md)。链接均为相对路径；「状态/截至」摘自各文自述定位（2026-11-04 复核）。

## 安全与合规
| 文件 | 管什么 | 状态/截至 |
|---|---|---|
| [SECURITY.md](SECURITY.md) | 安全政策（漏洞报告渠道） | 现行 |
| [SECURITY_MATRIX.md](SECURITY_MATRIX.md) | 安全矩阵 | 现行 |
| [SECURITY_AUDIT_TTS_MultiModel.md](SECURITY_AUDIT_TTS_MultiModel.md) | 安全审计报告 | 现行 |
| [SECURITY_CODEQL_TRIAGE.md](SECURITY_CODEQL_TRIAGE.md) | CodeQL 存量告警分诊（main 分支） | 现行 |
| [SECURITY_DEPENDABOT_TRIAGE.md](SECURITY_DEPENDABOT_TRIAGE.md) | 依赖漏洞告警分诊 | 2026-09-21（20 条 open 告警已 dismiss，详见 §1） |
| [SECURITY_REMEDIATION_TRACKER.md](SECURITY_REMEDIATION_TRACKER.md) | v2.2.1 安全评估整改追踪表 | 现行 |
| [EXCEPTION_SUPPRESSION_LEDGER.md](EXCEPTION_SUPPRESSION_LEDGER.md) | 异常吞没（except…: pass）分级台账 | 现行 |

## 用户与许可
| 文件 | 管什么 | 状态/截至 |
|---|---|---|
| [USER_AGREEMENT.md](USER_AGREEMENT.md) | 用户协议 | v1.0（生效 2026-08-12） |
| [PRIVACY_POLICY.md](PRIVACY_POLICY.md) | 隐私政策 | 现行 |
| [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | 第三方组件声明 | 更新 2026-09-02（非穷尽） |

## 工程规范
| 文件 | 管什么 | 状态/截至 |
|---|---|---|
| [DOD.md](DOD.md) | 完成定义（Definition of Done） | 现行 |
| [CODING_STANDARDS.md](CODING_STANDARDS.md) | 编码与 Git 规范 | 现行 |
| [TRAINING_LEDGER.md](TRAINING_LEDGER.md) | LoRA 训练实验台账（NEXT 评估 P0-3） | 现行（持续追加） |

## 运维与运行手册
| 文件 | 管什么 | 状态/截至 |
|---|---|---|
| [TODO.md](TODO.md) | TODO / 缺陷登记（owner 决策项） | 现行（持续追加） |
| [SRE_RUNBOOK.md](SRE_RUNBOOK.md) | SRE 运行手册（SLA / SLO / 回滚 / 灾难恢复演练） | 现行（演练表带 2026-10-29→11-03 时间戳） |
| [rollback_sop.md](rollback_sop.md) | 回滚 SOP（自动化回滚 / 版本化回滚） | 现行 |
| [_archive/执行记录/2026-11/2026-09-agent-memory-archive.md](../_archive/执行记录/2026-11/2026-09-agent-memory-archive.md) | 2026-09 Agent 工作记忆归档（环境/打包/发版/Git 坑点，去重后入库） | 留痕 2026-09-14→09-19 + 09-30 计划 |
| [_archive/执行记录/2026-11/2026-10-31-restore-drill.md](../_archive/执行记录/2026-11/2026-10-31-restore-drill.md) | 目标库级备份恢复与发布回滚演练（阶段 1） | 留痕 2026-10-31 |
| [_archive/执行记录/2026-11/2026-11-01-o3-failover-drill.md](../_archive/执行记录/2026-11/2026-11-01-o3-failover-drill.md) | O3 单点与切换演练（阶段 2 · 降格执行） | 留痕 2026-11-01 |
| [_archive/执行记录/2026-11/2026-11-02-f6-test-db-isolation.md](../_archive/执行记录/2026-11/2026-11-02-f6-test-db-isolation.md) | F6 测试套写污染生产库——止血与可逆隔离（整改阶段 10） | 留痕 2026-11-02 |
| [_archive/执行记录/2026-11/2026-11-02-o4-backup-wiring.md](../_archive/执行记录/2026-11/2026-11-02-o4-backup-wiring.md) | O4 备份链路可修半边（阶段 9 留痕） | 留痕 2026-11-02 |
| [_archive/执行记录/2026-11/2026-11-02-r4-real-history-rollback-drill.md](../_archive/执行记录/2026-11/2026-11-02-r4-real-history-rollback-drill.md) | R4 发布回滚演练——真仓 756 条历史重跑（整改阶段 8） | 留痕 2026-11-02 |
| [_archive/执行记录/2026-11/2026-11-02-f6-batch-b-and-fastfail.md](../_archive/执行记录/2026-11/2026-11-02-f6-batch-b-and-fastfail.md) | F6 B 批闸门取证与 fastfail（阶段 11） | 留痕 2026-11-02→11-03 |
| [_archive/执行记录/2026-11/2026-11-03-f7-venv-entrypoint-and-f8-hooks-drift.md](../_archive/执行记录/2026-11/2026-11-03-f7-venv-entrypoint-and-f8-hooks-drift.md) | F7 文档解释器入口失效 · F8 本地钩子接线漂移（阶段 13 留痕） | 留痕 2026-11-03 |
| [_archive/执行记录/2026-11/2026-11-03-f8-funasr-install.md](../_archive/执行记录/2026-11/2026-11-03-f8-funasr-install.md) | F8 前置条件——系统 Python 补装 funasr 1.4.16（阶段 14 留痕） | 留痕 2026-11-03 |
| [runbooks/BUS-FACTOR-2026-11-01.md](runbooks/BUS-FACTOR-2026-11-01.md) | Bus Factor 登记与观察判据（E7/P25 带项上线） | 留痕 2026-11-01 |
| [runbooks/key-rotation-SOP-19.md](runbooks/key-rotation-SOP-19.md) | 完整性签名密钥轮换 Runbook | 现行 SOP（SOP-19） |

## 发布与治理
| 文件 | 管什么 | 状态/截至 |
|---|---|---|
| [release-governance.md](release-governance.md) | 发布 / 回滚 规范（本仓本地化版） | 现行 |
| [agent-contract.md](agent-contract.md) | Agent 契约（v2） | 现行 |
| [model-cards/README.md](model-cards/README.md) | 模型卡目录（六卡已入库；OpenVoice / Step-Audio-EditX 待补） | 2026-10-21 核对 |
| [发布与分发-单文件安装包.md](发布与分发-单文件安装包.md) | 单文件安装包 + 环境/模型自助下载 | 现行（v2.9.0 口径） |

## 工具与技能
| 文件 | 管什么 | 状态/截至 |
|---|---|---|
| [qoder-snapshot.md](qoder-snapshot.md) | .qoder 本地资产层：快照 / 回退点机制 | 现行 |
| bilibili-repo-harvest | 已收敛至 Agent-Knowledge/skills/bilibili-repo-harvest/（外部归档仓，2026-11-04） | 现行 |

## 边界说明
- `docs/` 下 183 个被忽略文件已于 2026-11-04 迁至 `local/docs-archive/`（本地归档，未随仓库发布）；`docs/adr/` 5 篇已解除忽略并入库。
- `docs/agents/` 11 篇台账随迁至 `local/docs-archive/agents/`（2026-11-04）；后续追加写本地归档路径。
