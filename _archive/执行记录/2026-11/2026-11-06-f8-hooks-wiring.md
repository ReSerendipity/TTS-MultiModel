# F8 本地 git 钩子接线（2026-11-06 · 补接线 + fresh clone 验证）

> 对应评估门禁：**E8**（本地环境一致且提交前有检查，P2）+ 评估发现 **F8**（本地钩子断链，P1）
> 本单动作：属主授权的「补接线」——把 `core.hooksPath` 从漂移值 `local/hooks` 改回成文口径 `.githooks`，并验证 fresh clone 上钩子真实生效。**不重写历史**：断链窗口内的既有提交不做 rebase / 重签（rebase 属禁改动作，且窗口内提交实测全部已带 `Signed-off-by`，见 §1）。
> 既有登记：`docs/TODO.md` F8 节、`docs/runbooks/2026-11-03-f7-venv-entrypoint-and-f8-hooks-drift.md` §6、`docs/runbooks/2026-11-03-f8-funasr-install.md`、`docs/SRE_RUNBOOK.md` 治理与勘误速查 F8 条。

---

## 1. 动手前复核（当前 HEAD 实测，评估报告口径的更新）

| 项 | 实测 | 与评估报告口径的关系 |
|---|---|---|
| `git config core.hooksPath` | **`local/hooks`**（接线前） | 与报告一致（断链未闭环，本轮动手） |
| `local/hooks/` 内容 | `pre-push`（转发到 `.githooks/pre-push` + 父工作区 ledger 检查）+ `.origin`（内容 `.githooks`） | 与报告一致 |
| 断链窗口提交数 | `git rev-list --count --since="2026-10-31T00:00:19+08:00" HEAD` = **31** | 报告为 29（b49297e 时点），其后 +2 笔新提交 |
| 窗口内缺 SOB 的提交 | **0 笔**（31/31 全带 `Signed-off-by`，逐笔 trailers 实测） | **较报告更新**：报告点名的 ea27533/b49297e 两笔无 SOB——两笔在当前分支以同主题重建为 34ef17f/043f200（内容逐字节一致，`git diff` 空），重建后**均带 SOB**；缺 SOB 的事实已被重建修复，剩余缺陷只有钩子休眠本身 |

## 2. 接线动作（2026-11-06 00:5x）

```text
$ sh .githooks/install.sh        → exit 0，输出「已启用 .githooks：core.hooksPath=.githooks」
$ git config core.hooksPath      → .githooks（回读证明）
```

- 采用 `.githooks/install.sh`（成文口径的唯一安装入口）；**未使用** `scripts/install-hooks.ps1`（已退役墓碑，指向不存在的 `scripts/git-hooks`，按 TODO F8 警告勿用）。
- `local/hooks/` 目录**原样保留未删除**（`.gitignore` 忽略的本地层资产，含父工作区 ledger 检查的转发件）；接线后它不再被 git 读取（inert）。回滚路径：`git config core.hooksPath local/hooks`。
- 接线即生效的钩子：`pre-commit`（pre-commit 框架 → 失败退 pre-commit-lite）、`pre-push`、`prepare-commit-msg`（自动补 DCO）、`commit-msg`（DCO 硬校验）、`post-merge` / `post-checkout`（依赖清单变更提醒）。

## 3. 主仓验证（接线后、本轮提交前实测）

```text
$ sh .githooks/pre-commit
    → [pre-commit] 使用 pre-commit 框架（.pre-commit-config.yaml）
    → Engine Compatibility Check / Engine Specs / Training Isolation AST Gate /
      Declared Capabilities / Integrity Manifest Sync / Upstream Vendor Sync (offline) /
      Structure Guard / Detect hardcoded secrets   全部 Passed
    → exit 0（无暂存文件时 always_run 钩子全跑，框架环境全部命中本机缓存，离线无下载）
```

注：框架运行时自动 stash 未暂存改动并恢复——本轮 3 项在途脏文件（`launcher/requirements-small.txt`、`pyproject.toml`、`requirements-lock.txt`）经此路径原样保留，未入本次提交。

## 4. fresh clone 验证（本机临时克隆 `%TEMP%\tts-f8-fresh-clone-verify`，全程离线、零新装依赖）

| 步骤 | 实测 | 结论 |
|---|---|---|
| ① out-of-box | `git config core.hooksPath` 空（默认 `.git/hooks` 样例） | 钩子默认**不生效**——`core.hooksPath` 是本地配置不入库，属 git 机制，需一步引导 |
| ② 引导 | `sh .githooks/install.sh` → exit 0，回读 `.githooks` | 文档化的一步引导即生效 |
| ③ DCO 门禁双向 | 无 SOB 消息 → `sh .githooks/commit-msg` **exit 1**（输出修复指引）；带 SOB → **exit 0** | DCO 硬校验真实拦截 |
| ④ 全链真实提交（第一次） | `git commit -s` → **exit 1**，红在 `Engine Compatibility Check` 8/9：`funasr import 失败` | 钩子**按设计拦截**：funasr 当前仅存在于主仓已重建的 `.venv`（见 §5），fresh clone 无 venv → 环境不兼容被正确拦下，接线本身无缺陷 |
| ⑤ 全链真实提交（第二次，等价「克隆方按文档建好 venv」完成态） | 临时克隆内 `.venv` junction 指向主仓已重建 venv（零安装、零联网），`git commit -s` → **exit 0**，12 项钩子全 Passed，落提交 `96215e5` 带 `Signed-off-by`（作者=签署者） | **fresh clone 生效得证**：引导后钩子全链生效且 DCO 自动签署 |

④⑤ 的口径说明：⑤ 用 junction 复现的是仓库自身文档要求的 onboarding 终态（项目 venv 在位），不是对环境缺口的掩盖；不建 venv 的 fresh clone 会被 ④ 的门禁如实拦住——这正是 pre-commit 应有的行为。

## 5. 环境演进登记（影响判读，实测）

- **funasr 位置已变**：阶段 14（2026-11-03）把 `funasr==1.4.16` 装进系统 Python `C:\Python312`；2026-11-05 17:37 属主把 `.venv` 重建为**真实目录**（`pyvenv.cfg`：home=C:\Python312、include-system-site-packages=false），本轮实测 `C:\Python312\python.exe -c "import funasr"` → **ModuleNotFoundError**、`.venv\Scripts\python.exe` → **funasr 1.4.16 OK**——funasr 现仅存于 `.venv`。
- 连带效应：`scripts/run_python.py` 解析序「venv 优先」重新命中活体 `.venv`（F7 期间 venv 死链时走 PATH 兜底）；`check_engine_compat.py` 的 venv 重跑逻辑（`_reexec_with_venv_python`）在主仓命中、在 fresh clone 落空（无 venv）——§4④ 现象的机制解释。
- 评估报告「`.venv` 仍为悬空联接、不可执行」的口径**已被属主 2026-11-05 重建动作超越**（本轮实测可执行），G4（venv 重建决策）事实上已由属主动作落地，但 lock 同步（`requirements-lock.txt` torch 版本口径）仍待属主。

## 6. 断链窗口的处置口径

- 窗口 31 笔提交**不重写、不补钩**（rebase 禁改 + 全部已带 SOB，无签署事实缺口）；本轮提交起，本地提交重新全程经过 pre-commit / commit-msg 门禁——断链状态就此闭合。
- 本轮主仓提交本身即为活体验证：pre-commit 框架全链 + `commit-msg` DCO 硬校验 + `prepare-commit-msg`（-s 已签则幂等跳过）。

## 7. 未覆盖项

- GitHub 侧 DCO app / 分支保护的远端态：不联网，未能判断（沿用评估报告盲区口径，本地镜像证据 `dco.yml`、`docs/ci/branch-protection.json` 不变）。
- fresh clone 上 `pre-push` 链（precheck.ps1 为本地层资产、父工作区 ledger 检查在仓外）：push 类动作本轮禁止，未验证。
