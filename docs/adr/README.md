# Architecture Decision Records (ADR)

> 本目录是 TTS_MultiModel 项目的架构决策记录库。
> 每个 ADR 编号唯一、只追加不删除（被推翻的标记为 Superseded）。

## 索引

| 编号 | 标题 | 状态 | 日期 |
|------|------|------|------|
| [0001](0001-remove-gptsovits.md) | 删除 GPT-SoVITS 引擎 | Implemented | 2026-08-01 |
| [0002](0002-consolidate-into-app-integrated-app.md) | 顶层分层并入 `app/integrated_app/` | Implemented | 2026-08-27 |
| [0003](0003-single-engine-voxcpm2-json-i18n.md) | 引擎收缩为 voxcpm2 + i18n 改 JSON 词表 | Implemented | 2026-08-27 |
| [0004](0004-engine-registry-expansion.md) | 引擎注册表扩张至 5 引擎（indextts2/indextts20/voicebox/step-audio-editx 正式登记） | Implemented | 2026-09-16 |

## 何时必须写 ADR（满足任一即写）

- 引入或移除一个**运行时依赖**（模型引擎、数据库、前端框架）
- 改变**目录结构或分层边界**（如 TTS 的 bin→app、api/core→app/integrated_app）
- 做出**有长期代价的技术选择**（串行队列 vs 并发、Tauri vs Electron）
- 撤销此前的 ADR → 新写一条并把旧条状态改为 `Superseded by NNNN`

## ADR 格式规范

每个 ADR 文件应包含以下一级标题：

1. **背景与问题** — 为什么需要做这个决策
2. **评估的备选方案** — 列出所有考虑过的方案及其利弊
3. **决策** — 最终选择了什么方案，以及选择理由
4. **实施影响** — 对代码、依赖、文档的具体影响清单
5. **可回滚路径与待验证项** — 如何回滚、需要验证什么

## 如何新增 ADR

1. 复制编号（如 `0002-xxx.md`）
2. 按上述格式填写
3. 在本索引表中追加一行
4. 提交 commit
