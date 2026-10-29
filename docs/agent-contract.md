# Agent 契约（v2）

<!-- governance:agent-contract v2 -->

本文件由 `ReSerendipity/.github` 统一维护，仓内副本必须与模板逐字节一致；
权威治理规范见 [governance](https://github.com/ReSerendipity/.github/tree/main/governance)。

## 适用范围

所有在本仓工作的 AI agent（本地或云端），无论工具、模型与会话。新会话开头先读本文件。

## 开工前

1. 读本仓根 `README.md` 与根 `AGENTS.md`（若有）。
2. 查 `governance/desired-state.json` 中本仓的 `delivery` / `lifecycle`：
   `maintenance` 只允许 bug / 依赖 / 安全修复；`active` 才允许新功能。
3. 治理正文只在 `.github/governance`；不要凭记忆复述规则，以该仓当前 `main` 为准。

## 硬约束（违反即回退）

- 禁止入库：密钥 / token / 口令 / 连接串；本机绝对路径与用户名；`local/` 内容；
  嵌套或模块级 `AGENTS.md`；`docs/plans`、`docs/project`、`docs/_devarchive`、
  `docs/agents`、`docs/execution`。
  **例外**：`docs/execution/RELEASE_VERIFICATION.md`（§10.5 发布验证产物）与
  `GOVERNANCE-EXCLUSIONS.md` §七 已登记的仓/家族白名单路径，按登记口径放行；
  未登记的同类路径仍按违规处理。
- 提交必须带 DCO：`git commit -s`，身份用 noreply；不得使用个人邮箱。
- 不改平台设置（分支保护、ruleset、Secrets、仓库可见性），除非任务明确要求且所有者授权。
- 不删除、不重置、不 stash 用户未提交的改动；不 `git add -A`。
- 发布链只按 §10：远端优先、tag 驱动、资产附 `SHA256SUMS.txt`。

## 交付前

- `git status` 只含本次改动；`git diff` 能逐行解释。
- 跑本仓 `README.md` 里的测试 / 检查命令；失败不隐藏、不静默跳过。
- PR 描述写清：改了什么、为什么、怎么验证、哪些没做。

## 变更

本文件由 `.github` 的 `governance/templates/agent-contract.md` 生成；
仓内副本与模板不一致时，漂移审计会报红。

v2 起本文件只引用例外登记表，不复制具体仓名与清单；例外统一在
`GOVERNANCE-EXCLUSIONS.md` §七 登记，新增例外先改登记表，再同步模板与副本。
