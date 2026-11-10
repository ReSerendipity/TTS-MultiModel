# O4 备份调度取消注册（2026-11-09 · 删除计划任务 + 导出 XML 备份 + 验证）

> 对应评估门禁：**O4**（备份恢复定期实测，⛔P0 绝对）
> 本单动作：属主决策下的「取消注册（删除）」——把已注册的备份调度 `TTS-MultiModel-Backup-Daily` 从 Windows 任务计划程序移除，删除前先导出 XML 备份（可一键重建），删除后验证查无此任务。**不伪造成功**：全程命令与退出码如实记录。
> 反向动作记录：`_archive/执行记录/2026-11/2026-11-06-o4-schedule-registration.md`（同任务 2026-11-06 的注册 + 回读证明）。
> 既有登记：`docs/TODO.md` O4 节、`docs/SRE_RUNBOOK.md` §3.3。

---

## 1. 动手前复核（当前系统实测）

| 项 | 实测 | 与注册记录的关系 |
|---|---|---|
| 指定查询 | `Get-ScheduledTask -TaskName 'TTS-MultiModel-Backup-Daily'` → `State=Ready`、`Author=DESKTOP-R3BNNVQ\Doro`、`TaskPath=\` | 与 2026-11-06 注册记录一致（任务在位） |
| 动作 | `"<仓库根>\.venv\Scripts\python.exe" "<仓库根>\scripts\backup_history_db.py" --keep 14` | 与注册记录「要运行的任务」一致 |
| 触发器 | `MSFT_TaskDailyTrigger`、`Enabled=True`、首次 `2026-11-06T03:00:00`、`ExecutionTimeLimit=PT72H`、`MultipleInstances=IgnoreNew`、`RunOnlyIfIdle=False` | 与注册记录一致（每日 03:00） |
| 历史运行（回读，引自 `docs/TODO.md` WO-S04） | `Last Run Time 2026/11/9 3:00:01`、`Last Result 0`；`data/backups/` 连续四个周期产物在位（各 2,469,888 B：1106/1107/1108/1109） | 注册后首燃及随后三个周期全部落盘、退出码 0，周期证据已闭环 |

## 2. 删除前导出 XML 备份（可一键重建）

```text
$ Export-ScheduledTask -TaskName 'TTS-MultiModel-Backup-Daily' | Out-File "<WorkBuddy会话工作区>\.workbuddy-ai\backups\scheduled-tasks\TTS-MultiModel-Backup-Daily.xml" -Encoding UTF8
    → 1426 字节
```

- 目的：保证「删除可逆」。该 XML 为任务完整定义，重注册时可直接 `Register-ScheduledTask -Xml (Get-Content ...)` 还原。
- 位置说明：XML 落于**仓库外**的 WorkBuddy 会话工作区（`C:\Users\Doro\WorkBuddy AI\2026-11-09-23-10-48\.workbuddy-ai\backups\scheduled-tasks\`），未纳入仓库版本管理；是否入库由属主决定。

## 3. 删除动作（Unregister-ScheduledTask，2026-11-09 23:18）

```text
$ Unregister-ScheduledTask -TaskName 'TTS-MultiModel-Backup-Daily' -Confirm:$false
    [结果] RESULT: DELETED OK
```

- 无需管理员提权：任务原以当前登录用户身份注册，移除同样以当前用户上下文执行成功。

## 4. 删除后验证（查无此任务）

```text
$ Get-ScheduledTask -TaskName 'TTS-MultiModel-Backup-Daily' -ErrorAction SilentlyContinue
    → 返回空（STILL EXISTS 判据不成立 → 已删除）
$ Get-ScheduledTask | Where-Object { $_.TaskPath -eq '\' -and $_.Author -match 'Doro' } | Select-Object TaskName, State
    → GamePlusPlus          Running
      image-scrapers-ww-state-backup   Ready
      PL-REGEN2-D9D14-Night           Ready
    （TTS-MultiModel-Backup-Daily 已不在列表）
```

- 全量交叉验证：`schtasks /query /fo csv` 中 `\TTS-MultiModel-Backup-Daily` 已不再出现（此前同查询命中 1）。

## 5. 动机与范围（不擅自扩判）

- **动机**：属主在「开机内存占用 37% 优化」会话中，判定每日 03:00 自动备份任务不再需要，为精简开机常驻决定取消注册。
- **范围（仅移除任务，不伤数据/脚本）**：
  - 备份脚本 `scripts/backup_history_db.py` **保留**——手动/按需备份照常可用（`python scripts/run_python.py scripts/backup_history_db.py --keep 14`）。
  - 注册产物 `scripts/install_backup_schedule.py` **保留**——重注册入口（`--apply` 固定 `/tn` + `/f` 幂等覆盖，不产生第二个同名任务）。
  - `data/backups/` 既有历史备份文件（含 2026-11-06~11-09 四份每日产物）**未删除**，仅停止后续自动生成。
- 结论：本机从此**无**每日自动备份（回到「未注册」状态），改为属主按需手动跑或择机重注册。

## 6. 门禁 O4 判定的口径登记（不擅自改判）

- 本动作闭环的是 O4 阻塞结构中的「调度已注册」半边：取消后本机无机器周期排程，回到未注册态。
- **文档同步已完成**（本单动作外，已另手改，非本记录范围但一并登记口径）：
  - `docs/TODO.md` O4 段标题改为「调度已于 2026-11-09 23:18 由属主取消注册（删除）」；新增一条删除记录 bullet（含删除手段/验证/重注册路径/O4 仍 `[?]` 不变）。
  - `docs/SRE_RUNBOOK.md` §3.3 加 ⚠️ 当前状态注释（2026-11-09 23:18 取消注册、本机无每日自动备份、重注册命令）；§门禁状态摘要改写为「2026-11-06 授权注册、2026-11-09 取消注册（当前未注册，重注册走 `--apply`）」。
  - `scripts/install_backup_schedule.py` docstring 修正「本轮从未调用」为真实注册史。
- **O4 整体仍 `[?]`**：唯一剩余阻断项仍是「③ 同机无异地」（源库与全部备份同机同盘，盘损即同丢），与本次删任务无关，不预支关闭。

## 7. 需属主决策项（本单只陈述，不代答）

1. 是否重建每日自动备份（如需：`python scripts/install_backup_schedule.py --apply`；建议同时评估调度命令行是否由 `--keep 14` 改为 `--full` 及备份目录访问控制——改 `--full` 会把 5 个运行密钥明文带进备份目录）。
2. 第二存储位置（异地副本）——O4 剩余阻塞，属资源决策。
3. 是否将本单导出的 XML 备份纳入仓库版本管理（当前在仓库外会话工作区）。
