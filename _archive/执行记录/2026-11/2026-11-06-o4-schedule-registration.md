# O4 备份调度注册（2026-11-06 · 注册 + 回读证明 + 下次时间与触发人成文）

> 对应评估门禁：**O4**（备份恢复定期实测，⛔P0 绝对）
> 本单动作：属主工单授权的「注册」——把已交付未注册的备份调度（`scripts/install_backup_schedule.py`，阶段 9 产物）真正注册进 Windows 任务计划程序，回读证明，并把下次执行时间与触发人成文。**不伪造成功**：全程命令与退出码如实记录。
> 既有登记：`docs/TODO.md` O4 节、`docs/runbooks/2026-11-02-o4-backup-wiring.md`、`docs/SRE_RUNBOOK.md` §3.3。

---

## 1. 动手前复核（当前 HEAD 实测）

| 项 | 实测 | 与评估报告口径的关系 |
|---|---|---|
| 指定查询 | `schtasks /query /tn "TTS-MultiModel-Backup-Daily"` → 「错误: 系统找不到指定的文件。」exit 1 | 与报告一致（未注册，未闭环，本轮动手） |
| 全量查询 | `schtasks /query /fo csv` 全量按名称模式 grep `tts|multi|backup` → TTS-MultiModel 命中 **0**（其余命中均为系统自带 Backup 任务） | 与报告一致 |
| 注册脚本 | `scripts/install_backup_schedule.py` 在场（阶段 9 交付 + 阶段 13 F7 接线改造），默认 `--dry-run` | 与报告一致；本单授权 = `--apply` 的首次真实调用 |

## 2. 注册前命令行验证（注册的必须是「活命令行」）

**环境演进登记（实测）**：属主于 2026-11-05 17:37 把项目 `.venv` 重建为**真实目录**（`pyvenv.cfg`：home=C:\Python312、3.12.10），F7 期间的悬空联接已不存在——`scripts/run_python.py` 解析序（venv 优先）现在命中**活解释器**，dry-run 打印的注册命令行不再是死路径（阶段 13 曾登记旧写法会把死解释器注册成每日任务）。

```text
$ python scripts/run_python.py scripts/install_backup_schedule.py          # dry-run，exit 0
    完整命令行    : "<仓库根>\.venv\Scripts\python.exe" "<仓库根>\scripts\backup_history_db.py" --keep 14
    运行身份      : Doro（当前登录用户，交互式运行；不配置服务账号、不存储密码，用户未登录时不执行）
    下一次应执行  : 2026-11-06 03:00:00
    同名任务探测  : 不存在 → --apply 时将新建唯一任务
$ python scripts/run_python.py scripts/backup_history_db.py --keep 14      # 注册命令行实跑一次，exit 0
    → [history-backup] 备份完成: data\backups\history_backup_20261106_003611.db (2.36 MB)
    产物实测 2,469,888 B；history_backup_*.db 系列 6 份 → 7 份，--keep 14 轮转零删除（backups 目录对象 12 → 13）
```

## 3. 注册动作（--apply，2026-11-06 00:4x）

```text
$ python scripts/run_python.py scripts/install_backup_schedule.py --apply   # exit 0
    [apply] 执行真实注册（写系统任务计划）...
    成功: 成功创建计划任务 "TTS-MultiModel-Backup-Daily"。        （schtasks 退出码: 0）
```

- 无需管理员提权：任务以当前登录用户身份注册（无 `/ru`/`/rp`），`schtasks /create` 直接成功。
- 幂等性沿用脚本设计：固定 `/tn` + `/f` 就地更新，重复注册不产生第二个同名任务。

## 4. 回读证明（注册后 verbose 查询原文关键字段）

```text
$ schtasks /query /tn TTS-MultiModel-Backup-Daily /fo LIST /v
    主机名:            DESKTOP-R3BNNVQ
    任务名:            \TTS-MultiModel-Backup-Daily
    下次运行时间:      2026/11/6 3:00:00
    模式:              就绪        （计划任务状态: 已启用）
    登录状态:          只使用交互方式
    作为用户运行:      Doro
    要运行的任务:      "<仓库根>\.venv\Scripts\python.exe" "<仓库根>\scripts\backup_history_db.py" --keep 14
    开始时间:          3:00:00
```

全量查询交叉验证：`schtasks /query /fo csv` 中出现 `"\TTS-MultiModel-Backup-Daily","2026/11/6 3:00:00","就绪"`（此前同查询命中 0）。

## 5. 下次时间与触发人（成文口径）

| 项 | 值 | 出处 |
|---|---|---|
| 频率 | 每日 03:00（本机时区，`/sc daily /st 03:00`） | 注册命令行 + 回读「开始时间: 3:00:00」 |
| 下次执行 | **2026-11-06 03:00:00**（注册时刻 2026-11-06 00:4x，今日未到 03:00 → 当日触发） | dry-run `compute_next_run` 与回读「下次运行时间」两口径一致 |
| 触发人/运行身份 | **Doro**（当前登录 Windows 用户，交互式 token） | 回读「作为用户运行」+「登录状态: 只使用交互方式」 |
| 无人值守边界 | **用户未登录时不执行**（交互式运行、不存储密码） | 脚本 plan 自述 + 回读登录状态——如需无人值守（服务账号/密码存储）属属主决策项，本轮未配置 |

## 6. 门禁 O4 判定的口径登记（不擅自改判）

- 本轮闭合的是 O4 阻塞结构中的「调度未注册」半边：**已注册 = 有机器周期排程**。周期性**证据**仍需首燃回读（2026-11-06 03:00 后查 `data/backups/` 新增产物与任务上次运行时间）——首燃在本 runbook 成文时尚未发生，如实登记，不预支结论。
- 「同机无异地」半边**未动**：`data/backups/` 全部对象仍同机同盘，第二存储位置属属主资源决策（O4 保持 `[?]` 的另一半原因，本轮不代决）。

## 7. 需属主决策项（本单只陈述，不代答）

1. 是否配置无人值守运行（服务账号 / `/ru`+`/rp` 密码存储 / 仅登录时触发维持现状）。
2. 第二存储位置（异地副本）——O4 剩余阻塞，属资源决策。
3. 备份集覆盖边界沿用 O4 节既有登记（运行密钥 5 件、`generation_versioning` 库、`audit.log` 未落盘），本轮未扩。
