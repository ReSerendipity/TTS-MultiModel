# AGENTS.md（公开精简版）

<!-- governance:agent-contract v1 -->

> 本文件允许入库，只写公开安全的最小契约；完整本地上下文仍放 `local/`，不入库。
> 完整契约：[docs/agent-contract.md](docs/agent-contract.md)
> 治理规范：[ReSerendipity/.github/governance](https://github.com/ReSerendipity/.github/tree/main/governance)

## 开工前

1. 读 `docs/agent-contract.md` 与根 `README.md`。
2. 确认本仓定位：`maintenance` 只修 bug / 依赖 / 安全，`active` 才允许新功能。

## 硬约束

- 密钥、token、口令、机器绝对路径不得入库。
- 公开仓不提交 `local/`、嵌套 `AGENTS.md`、`docs/plans|project|_devarchive|agents|execution`。
- 提交必须 `git commit -s`（DCO），身份用 noreply。
- 不改分支保护 / ruleset / Secrets / 仓库可见性。
- 不删他人 WIP，不 `git add -A`。

## 交付前

- `git status` 只含本次改动；跑 README 的测试命令；失败如实报告。
