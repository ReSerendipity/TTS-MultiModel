**ADR-0002: 顶层分层并入 `app/integrated_app/`**

- **状态**: Implemented
- **日期**: 2026-08-27
- **决策者**: 项目维护者 + AI 指挥（家族规范审计 Phase A 确认）

---

# 背景与问题

AGENTS.md v1.7 之前的 §3 声称顶层存在 `api/` `common/` `core/` `engines/` `routes/` `training/` 六个目录分层，
实测（2026-08-27）六个目录**全部不存在**；真实顶层为 `app/ cache/ data/ demo/ docs/ examples/ logs/ lora/ model/ outputs/ perf/ personas/ prompt_cache/ screenshots/ scripts/ tests/`。
文档与实现长期脱钩，源于历次"自检"只修正任务当时撞到的那一条（v1.7/v1.8/v1.9 均如此）。

# 评估的备选方案

- **方案 A：按文档建目录** —— 重建 api/common/core/... 把代码搬回去。代价高、与既有 `app/integrated_app/` 架构冲突，且无法自动化验证。
- **方案 B：文档对齐实现** —— 以 `app/integrated_app/` 为唯一应用根重写 §3 目录树与硬约束，删除不存在的分层。**采用**。

# 决策

文档以实测目录树为准：应用代码统一归于 `app/integrated_app/`（含 `engines/`、`routes/`、`middleware/`、`security/`、`training/` 等子层）。
「5 条硬约束」中的路径约束同步改为实际路径（`routes/` → `app/integrated_app/routes/`，`engines/` → `app/integrated_app/engines/`）。

# 实施影响

| 位置 | 影响 |
|---|---|
| AGENTS.md §3 | 目录树整体重写为实测结构，删除 `api/` `common/` `core/` 等条目 |
| AGENTS.md §3 硬约束 | 路径约束改为 `app/integrated_app/` 实际路径，删除 `training/` 相关约束 |
| AGENTS.md §12 SOP | `core/prompt_templates/` 等不存在路径改为各引擎包内 `prompt.py` |

# 可回滚路径与待验证项

- 纯文档决策：回滚即 `git revert` 对应 commit。
- 待验证：审计器对 AGENTS.md 的 `api/`、`core/`、`common/` 现存式引用零命中。