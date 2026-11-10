# 文档索引（docs/INDEX）

> 最后核对：2026-11-10　｜　维护：文档整理工单 WO-DOC-B01
> 本文件是 `docs/` 与全仓文档的**机器可读总索引**；[`README.md`](README.md) 保留为人类阅读的分层导读（安全/许可/规范/运维分组）。
> 面向最终用户的使用说明见根 [`README.md`](../README.md)。

## 状态口径

| 状态 | 含义 |
|---|---|
| 现行 | 仍在指导当前工作，或仍是某项规则、SOP、口径的有效来源 |
| 历史 | 时点记录（演练留痕、审计快照、已完成的批次报告），只作追溯 |
| 废弃 | 已被后继文档取代，或文件自身声明作废 |

索引范围：`docs/` 全部 Markdown 逐个列出（22 篇根 + 5 篇 ADR + 2 篇 runbooks = 29 篇 tracked）；仓根入口文档逐个列出；`local/`、`_archive/`、`output/`、`.qoder/`、`model/`、`reference_repos/` 为本地归档 / 运行产物 / 第三方副本，只到目录级。

## 一、入口文档（仓根）

| 文档 | 用途 | 状态 | 核对日期 |
|---|---|---|---|
| [`README.md`](../README.md) | 面向用户的项目说明：六引擎能力、界面截图、安装、模型许可与 FAQ（中英双语） | 现行 | 2026-11-10 |
| [`AGENTS.md`](../AGENTS.md) | 编码代理在本仓的执行契约 | 现行 | 2026-11-10 |
| [`CHANGELOG.md`](../CHANGELOG.md) | 版本变更记录（Keep-a-Changelog 体例） | 现行 | 2026-11-10 |
| [`FIX_LOG.md`](../FIX_LOG.md) | 缺陷修复流水（按编号记录现象、根因、改动与验证） | 现行 | 2026-11-10 |
| [`LOCAL_RULES.md`](../LOCAL_RULES.md) | 本机环境相关的本地约定（路径、启动、模型放置） | 现行 | 2026-11-10 |
| [`docs/README.md`](README.md) | docs 分层导读：安全 / 用户许可 / 工程规范 / 运维 / 发布治理分组的常用入口 | 现行 | 2026-11-10 |
| [`docs/agent-contract.md`](agent-contract.md) | Agent 契约（v2），与 `.github` 模板一致 | 现行 | 2026-11-10 |
| [`docs/TODO.md`](TODO.md) | TODO / 缺陷登记（含 owner 决策项） | 现行 | 2026-11-10 |

## 二、docs/ 根（安全、规范、运维、发布）

| [`CODING_STANDARDS.md`](CODING_STANDARDS.md) | 入库内容的可移植性、依赖与 Git 卫生约束 | 现行 | 2026-11-10 |
| [`DOD.md`](DOD.md) | 完成定义（Definition of Done） | 现行 | 2026-11-10 |
| [`EXCEPTION_SUPPRESSION_LEDGER.md`](EXCEPTION_SUPPRESSION_LEDGER.md) | 异常吞没（except…: pass）分级台账与治理进度 | 现行 | 2026-11-10 |
| [`PRIVACY_POLICY.md`](PRIVACY_POLICY.md) | TTS MultiModel 隐私政策 | 现行 | 2026-11-10 |
| [`README.md`](README.md) | docs 分层导读（安全 / 许可 / 规范 / 运维 / 发布治理分组） | 现行 | 2026-11-10 |
| [`SECURITY.md`](SECURITY.md) | 安全政策与漏洞披露渠道（GitHub 只认仓根 SECURITY.md / .github 策略入口，本文件为规范正文） | 现行 | 2026-11-10 |
| [`SECURITY_AUDIT_TTS_MultiModel.md`](SECURITY_AUDIT_TTS_MultiModel.md) | 安全审计 — TTSMultiModel | 现行 | 2026-11-10 |
| [`SECURITY_CODEQL_TRIAGE.md`](SECURITY_CODEQL_TRIAGE.md) | CodeQL 存量 110 条告警的可处置分诊清单 | 现行 | 2026-11-10 |
| [`SECURITY_DEPENDABOT_TRIAGE.md`](SECURITY_DEPENDABOT_TRIAGE.md) | 依赖漏洞告警分诊：20 条 open 告警已 dismiss，并登记 16+2 条 transformers 风险（含 PYSEC-only 的 ZDI 系列） | 现行 | 2026-11-10 |
| [`SECURITY_MATRIX.md`](SECURITY_MATRIX.md) | 安全控制矩阵（供审计与合规检查对照） | 现行 | 2026-11-10 |
| [`SECURITY_REMEDIATION_TRACKER.md`](SECURITY_REMEDIATION_TRACKER.md) | TTSMultiModel v2.2.1 — 安全评估整改追踪表（家族复用 ImageMultiModel 流程） | 现行 | 2026-11-10 |
| [`SRE_RUNBOOK.md`](SRE_RUNBOOK.md) | SRE 运行手册（SLA / SLO / 回滚 / 灾难恢复演练） | 现行 | 2026-11-10 |
| [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) | Third-Party Notices（第三方组件声明） | 现行 | 2026-11-10 |
| [`TODO.md`](TODO.md) | TODO / 缺陷登记（含 owner 决策项，持续追加） | 现行 | 2026-11-10 |
| [`TRAINING_LEDGER.md`](TRAINING_LEDGER.md) | LoRA 训练实验台账（每跑一次留一条可回滚记录） | 现行 | 2026-11-10 |
| [`USER_AGREEMENT.md`](USER_AGREEMENT.md) | TTS MultiModel 用户协议（User Agreement） | 现行 | 2026-11-10 |
| [`agent-contract.md`](agent-contract.md) | 本文件由 ReSerendipity/.github 统一维护，仓内副本必须与模板逐字节一致； | 现行 | 2026-11-10 |
| [`qoder-snapshot.md`](qoder-snapshot.md) | .qoder 本地资产层：快照 / 回退点机制 | 现行 | 2026-11-10 |
| [`release-governance.md`](release-governance.md) | 发布 / 回滚 规范（本仓本地化版） | 现行 | 2026-11-10 |
| [`rollback_sop.md`](rollback_sop.md) | 回滚 SOP（自动化回滚 / 版本化回滚） | 现行 | 2026-11-10 |
| [`发布与分发-单文件安装包.md`](发布与分发-单文件安装包.md) | 发布与分发：单文件安装包 + 环境/模型自助下载 | 现行 | 2026-11-10 |
| [`README.md`](model-cards/README.md) | 模型卡目录（六卡已入库；OpenVoice / Step-Audio-EditX 待补） | 现行 | 2026-11-10 |

## 三、docs/adr（架构决策记录）

| 文档 | 用途 | 状态 | 核对日期 |
|---|---|---|---|
| [`0001-remove-gptsovits.md`](adr/0001-remove-gptsovits.md) | ADR-0001：删除 GPT-SoVITS 引擎（Implemented，2026-08-01） | 现行 | 2026-11-10 |
| [`0002-consolidate-into-app-integrated-app.md`](adr/0002-consolidate-into-app-integrated-app.md) | ADR-0002：顶层分层并入 `app/integrated_app/`（Implemented，2026-08-27） | 现行 | 2026-11-10 |
| [`0003-single-engine-voxcpm2-json-i18n.md`](adr/0003-single-engine-voxcpm2-json-i18n.md) | ADR-0003：引擎收缩为 voxcpm2 + i18n 由 gettext 改 JSON 词表（Implemented，2026-08-27） | 现行 | 2026-11-10 |
| [`0004-engine-registry-expansion.md`](adr/0004-engine-registry-expansion.md) | ADR-0004：引擎注册表扩张至 5 引擎（Implemented，2026-09-16） | 现行 | 2026-11-10 |
| [`README.md`](adr/README.md) | ADR 索引（编号 / 标题 / 状态 / 日期；只追加不删除，被推翻标 Superseded） | 现行 | 2026-11-10 |

## 四、docs/runbooks（运行手册与演练）

| 文档 | 用途 | 状态 | 核对日期 |
|---|---|---|---|
| [`BUS-FACTOR-2026-11-01.md`](runbooks/BUS-FACTOR-2026-11-01.md) | Bus Factor 单点登记与观察判据（E7/P25 带项上线） | 现行 | 2026-11-10 |
| [`key-rotation-SOP-19.md`](runbooks/key-rotation-SOP-19.md) | 完整性签名密钥轮换 Runbook（SOP-19） | 现行 | 2026-11-10 |

## 五、本地归档 / 运行产物 / 第三方副本（目录级）

| 目录 | 用途 | 状态 | 核对日期 |
|---|---|---|---|
| [`local/`](../local/) | 本地维护层：`agents/` 台账 11 件、`hooks/`、`docs-archive/` 183 个被忽略文件的本地归档（2026-11-04 由 `docs/` 迁出） | 现行 | 2026-11-10 |
| [`_archive/`](../_archive/) | 历史归档：`执行记录/2026-11/` 13 篇演练与批次留痕 + `INDEX.md` | 历史 | 2026-11-10 |
| [`output/`](../output/) | PR / Issue / 发布核对的过程记录（17 篇），非正式文档 | 历史 | 2026-11-10 |
| [`.qoder/`](../.qoder/) | 本地 AI 工具资产（commands / rules / skills），17 篇；另有 `.qoder-snapshots/` 快照副本 | 现行 | 2026-11-10 |
| [`model/`](../model/) | 各引擎模型卡与权重占位（`*/README.md` 为上游模型卡，随权重分发） | 现行 | 2026-11-10 |
| [`reference_repos/`](../reference_repos/) | 上游开源仓副本（index-tts / index-tts-main），文档随上游，本仓不维护 | 历史 | 2026-11-10 |
| [`pytest_tmp/`](../pytest_tmp/) | 测试夹具的落盘样本（临时文件，非文档） | 历史 | 2026-11-10 |

> 说明：`reference_repos/` 与 `model/*/README.md` 是第三方原文，按"不改上游文本"原则整体不列入逐个索引，也不做任何修改。

## 六、索引维护规则

1. **新增文档**：归入 `docs/` 对应目录，并在本文件第二节补一行；`状态` 按上文口径判定。
2. **迁往本地归档**：本仓惯例是把过期但需留痕的文档移入 `local/docs-archive/<类别>/`（不随仓库发布），移动后同步本文件与 `docs/README.md` 的链接。
3. **废弃文档**：移入 `_archive/docs-archive-<YYYYMMDD>/`（保留原相对路径），本文件对应行改 `状态=废弃` 并写新路径。
4. **核对日期**：整仓复核时统一刷新。
