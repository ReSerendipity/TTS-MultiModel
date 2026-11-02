# O4 备份链路可修半边 · 阶段 9 留痕（2026-11-02）

> 工单：整改阶段 9——修掉文档推荐入口死路径（F3）、把「备份集覆盖哪些活体数据资产」变成可判定、
> 交付调度接线的可验证实现。本轮**不处理**异地（O4 阻塞③）与周期性到期（阻塞④），不碰
> `scripts/rollback_release.py`、`requirements-lock.txt`、O3/E7/P25。
> 执行分支 `fix/desktop-shell-csrf-and-commands`（起测 HEAD `dc8061d`，起测 `git status --porcelain` 空）。
> 证据目录：`C:/Users/Doro/Desktop/评估报告_2026-10-29/_演练/O4-wiring/`（下文以 `_演/` 指代）。

## 1. 结论速览

- **F3：已闭环 `[x]`（退役）**——`scripts/backup_db.py` 删除，SRE_RUNBOOK §3.3 推荐入口改指
  `scripts/backup_history_db.py`；文档命令冷 shell 原样跑 **exit 0**。
- **覆盖表：可判定**——逐资产实测见 §2；默认备份集覆盖 `data/history.db`，`config.yaml` 由 git
  覆盖实证（blob==HEAD），其余资产覆盖状态逐项写明。
- **调度接线：已交付未注册**——`scripts/install_backup_schedule.py` 默认 `--dry-run`（七要素契约 +
  幂等判据 + 重复运行逐字节一致），11 项测试全绿；**`--apply` 本轮未调用**（待所有者授权）。
- **O4 自判：保持 `[?]`**——判据①可复核通过、②活体通过、③周期性未满足（调度未注册 + 2027-01
  到期轮次未到），另异地为资源决策硬前提。详见 §8。
- 全量测试套两口径 exit 0（2614/0/41 与 2522/0/10，cov 52.39%≥45%），见 §6。
- 新发现 **F6**（测试套件直写生产库噪声行）按证据登记，未修，见 §7。

## 2. 数据资产覆盖表（本机运行态，2026-11-02 实测，字节数与 mtime 精确到亚秒）

| 资产 | 实测路径 | 字节数 | mtime（+0800） | 是否被备份集覆盖 | 由谁覆盖 / 定性 |
|---|---|---|---|---|---|
| 主业务历史库 | `data/history.db` | 2,408,448 | 2026-11-02 11:29:43.624601300（本轮对平窗口内未变，测试窗后推前，见 F6） | **✅ 覆盖** | `scripts/backup_history_db.py` 默认集（§4 真跑对平） |
| 历史库 WAL 旁路 | `data/history.db-wal` | 5,401,352 | 2026-11-02 11:29:43.619064500 | ✅ **语义覆盖** | backup API 经连接读取，快照含已提交 WAL 内容（§4 行数对平为证）；旁路文件本身不单独复制 |
| 历史库 SHM 旁路 | `data/history.db-shm` | 32,768 | 2026-11-02 11:28:44.908181400 | ➖ 无需覆盖 | 可重建索引，非数据 |
| 治理配置 | `config.yaml`（仓库根） | 15,704 | 2026-10-27 10:53:58.910741200 | **✅ 覆盖（非备份集）** | git 跟踪，实证 `git hash-object` == `HEAD` blob `d45ed956ee8fdc7a691cfe1cfe518d718fd89c86`；内容版本可 `git restore` 还原 |
| PII 加密密钥 | `data/.pii_key` | 44 | 2026-09-05 15:43:17.311650300 | ❌ **无覆盖** | 丢失＝历史 PII 字段永久不可解密；密钥侧管理属所有者决策（§7） |
| 历史链 HMAC 密钥 | `data/.history_hmac_key` | 64 | 2026-08-07 19:30:45.345382000 | ❌ **无覆盖** | 丢失＝HMAC 链校验断裂（SRE §3.1 告警路径失效） |
| 水印密钥 | `data/.watermark_key` | 32 | 2026-09-05 16:08:06.365222100 | ❌ 无覆盖 | 同上定性 |
| CSRF secret | `data/.csrf_secret` | 64 | 2026-08-07 19:00:53.046308700 | ❌ 无覆盖 | 可重生成（后果=会话失效），仍登记 |
| 清单签名密钥 | `data/.manifest_signing_key` | 119 | 2026-09-10 14:33:12.048309600 | ❌ 无覆盖 | 丢失＝已签清单不可复核 |
| 版本谱系库（活体指向） | `outputs/generation_versions.db` | 65,536 | 2026-11-02 11:29:21.422459200 | ❌ 不在默认备份集 | `config.yaml:161` 指向此库但 `generation_versioning.enabled=false`（治理决策：保留模块、停用功能；95 行历史测试数据已清理）；如启用可 `backup_history_db.py --db-path` 显式覆盖 |
| 版本谱系旧副本 | `data/generation_versions.db` | 32,768 | 2026-09-05 17:46:22.588289100 | ❌ 无覆盖 | 工单锚列它为资产；**实测定性为旧副本**（config/代码 `generation_versioning.py:107` 均指向 outputs 侧） |
| 审计日志 | `data/audit.log` | **不存在**（`ls` 实测 No such file，11:5x 与 12:1x 两次） | — | ➖ **无此对象** | 代码实体在（`app/integrated_app/security/audit.py`，多路由接线）且 `config.yaml:214 audit_enabled: true`，但截至实测未落盘；`%APPDATA%/tts-multimodel/` 空目录（审计 HMAC 密钥亦未生成），与之一致。退役的 `backup_db.py` 对它的备份承诺是**假承诺**，已随 F3 移除 |
| 时延估计训练样本 | `data/generation_times.json` | 4,313 | 2026-11-01 17:33:41.783618500 | ❌ 无覆盖 | `estimator.py`/`model_manager_core/state.py` 消费，≤200 条缓存，可再生定性；仍登记 |
| push 订阅库 | `data/push_subscriptions.db` | **主库不存在**；残骸 `-shm` 32,768（2026-08-06 22:54:39.873362000）、`-wal` 20,632（2026-08-02 14:05:04.790155300） | — | ➖ 非备份对象 | 全仓 `git grep push_subscriptions` **零命中**（无代码实体）；孤儿残留定性，处置（是否删除）待所有者 |
| 一次性手工旧备份 | `data/history.db.bak-20261006` | 1,437,696 | 2026-10-06 18:58:18.128909300 | ➖ 备份物而非活体 | 保留原位 |
| 既有备份物 ×6 | `data/backups/`（清单 `_演/backups-before.txt`） | 32,768～2,175,934,464 | 各见清单 | — | **本轮零改动**（事后 diff 仅新增，见 §4） |
| checkpoints | `data/checkpoints/` | 空目录 | — | ➖ 无对象 | `ls` 实测 |

**可判定结论**：默认备份集（调度将执行的命令行）覆盖且仅覆盖 `data/history.db` 的完整性快照；
`config.yaml` 的还原面由 git 承担（已实证等价）。4 个密钥类 + 2 个 generation_versions 副本 +
`generation_times.json` **不在任何备份面内**——这是备份集的事实边界，逐项列入 §7 未覆盖清单。

## 3. F3 决策：退役（附取证）

**取证（本轮全部亲自重跑）**：

- `git grep -l backup_db` → 仅 `docs/SRE_RUNBOOK.md`、`docs/runbooks/2026-10-31-restore-drill.md`；
  `app/`、`tests/`、`.github/` 零引用（`tests/` 备份相关只有 `test_backup_history_db.py`）。
- `ls data/`（含 `-a`）→ 无 `tts_history.db`、无 `audit.log`；repo 根无 `backups/` 目录。
- `git grep -n tts_history` 现网命中 → 仅 `backup_db.py` 自身 4 处（文档字符串/目标/恢复目标/恢复前快照名）。
- **根因推测复核——推翻工单锚**：工单推测 `tts_history.db` 之死源于 `routes/generate/utils.py`
  记载的 S-R4「两个 SQLite 路径合并」。**实测推翻**：S-R4 合并的是 `outputs/history.db`→
  `data/history.db`（`utils.py:64-67,277-281`），两路径均非 `tts_history.db`；真实因果链由
  `git show` 钉死——`90e8512` 时 `config.yaml:58` 确有 `db_path: data/tts_history.db`，
  `64a753f`（2026-08-16 15:36:54 UTC，"prune legacy sections"）清理了该配置段，而
  `backup_db.py` 是 **15 天后** `80c13b5`（2026-08-31 11:29:37 UTC，运维能力补齐）新增的，
  照抄了已被清理的死命名。即：**脚本生下来就指向死路径**，与 S-R4 无关。
- `backup_history_db.py` 侧：路径经 `get_history_db_path()`（优先级成文于 `history_db.py:2745-2746`、
  `config.py:230-254`，`ROOT_DIR` 解析不依赖 CWD）；备份目录解析同样 `__file__` 锚定（`main()` 237-238）。

**决策：退役删除**（而非修活）。理由（均可复核）：①退役判据「零代码/测试引用」实测成立；②修活将与
`backup_history_db.py` 形成两个同源入口（一有测试一无测试、一写 `data/backups/` 一写 repo 根
`backups/`），制造 Bus-Factor 陷阱与目录约定分叉；③其 `--restore` 目标写死幽灵库 `data/tts_history.db`，
修活须连恢复面一起改，改动面与风险大于收益；④2026-10-31 演练台账与 `2026-10-31-restore-drill.md:77`
的 F3 建议本就指向「改指真实链路」。假承诺清理：`audit.log` 对象不存在（§2 定性），退役即消灭
「一并备份审计日志与 config.yaml」的书面谎言。

**实施**：`git rm scripts/backup_db.py`；SRE_RUNBOOK §3.3 推荐入口/命令块/覆盖口径三段重写；
`2026-10-31-restore-drill.md` §5.1 F3 行状态 `未处理（待决策）→已闭环（阶段 9）`（不删行）；
TODO.md 新增 F3 条目。文档不留任何指向死路径的推荐入口（复核：`git grep -n "backup_db"` 除留痕
叙述外无推荐命令——留痕中以「已退役」语境提及不构成推荐入口）。

## 4. 真跑备份与恢复对平（数字全部实测）

**备份**（最终留存入口，即文档推荐命令原文）：

```
命令：.venv/Scripts/python.exe scripts/backup_history_db.py --keep 14
起始 2026-11-02 12:17:03 → 结束 12:17:04，退出码 0
stdout：[history-backup] 备份完成: …\data\backups\history_backup_20261102_121704.db (2.30 MB)   （_演/backup-run.log）
产物：data/backups/history_backup_20261102_121704.db，2,408,448 B
      sha256 874d8447e9bfe9f3b138041f96a2a9ac019ec37b43107457a8e4f8e08d164f41   （_演/backup-artifact.txt）
轮转安全：keep=14 仅作用于 history_backup_* 前缀；事前清单 _演/backups-before.txt（6 项），
      事后 diff 仅新增（backups-after.txt），既有备份物零改动。
```

**冷 shell 复跑**（新起 `bash -c` 进程，文档命令一字不差）：2026-11-02 12:18:44 起止同秒，
**exit 0**，产物 `history_backup_20261102_121844.db` 2,408,448 B，**sha256 与第一份逐字节同**
（`874d8447…164f41`；库在 12:17:04–12:18:44 间静止，两份即同一逻辑态，`_演/cold-shell-run.log`）。

**恢复（目标仅仓外临时目录，不指向生产路径）**：

```
命令：.venv/Scripts/python.exe scripts/backup_history_db.py --restore data/backups/history_backup_20261102_121704.db
      --target "C:/Users/Doro/Desktop/评估报告_2026-10-29/_演练/O4-wiring/restore_tmp/restored_history.db"
起始 12:17:50 → 结束 12:17:51，退出码 0（内置恢复后 PRAGMA integrity_check 通过，否则脚本抛错退非 0）
产物：restore_tmp/restored_history.db，2,408,448 B   （_演/restore-run.log）
```

**对平**（`_演/verify_reconcile.py`，活体库 `mode=ro` URI 只读打开，可正确读取未 checkpoint 的 WAL；
报告落盘 12:18:32.542，`_演/reconcile-report.txt`；期间活体主库 mtime 恒为 11:29:43.624601300）：

| 表 | 字段一致 | 行数（活体/恢复） | 主键前 5 行抽样 sha256 |
|---|---|---|---|
| `_schema_migrations` | ✅ | 8 / 8 ✅ | 一致（前 5 行，pk=version） |
| `action_logs` | ✅ | 2,666 / 2,666 ✅ | 一致（pk=id） |
| `generation_history` | ✅ | 468 / 468 ✅ | 一致（pk=id） |
| `generation_history_fts` | ✅ | 468 / 468 ✅ | 一致（rowid） |
| `generation_history_fts_config` | ✅ | 1 / 1 ✅ | 一致（前 1 行，pk=k） |
| `generation_history_fts_data` | ✅ | 170 / 170 ✅ | 一致（pk=id） |
| `generation_history_fts_docsize` | ✅ | 468 / 468 ✅ | 一致（pk=id） |
| `generation_history_fts_idx` | ✅ | 168 / 168 ✅ | 一致（pk=segid） |
| `meta_kv` | ✅ | 7 / 7 ✅ | 一致（pk=key） |

**9/9 表全对平 PASS**。**结论（按备份集覆盖口径）**：备份集覆盖的每个资产——`data/history.db`
（含 WAL 内已提交内容）——取得到且可恢复（字节级产物 + 表数/行数/主键抽样三层对平 + integrity_check）；
`config.yaml` 的「取得到且可恢复」由 git blob 等价实证（§2）。12:25:43 对平重放退出码 1 系 F6 测试
写入漂移（活体 2,666→2,686 行）所致，恰证对平须限定静态窗口，非备份缺陷。

## 5. 调度接线产物（交付未注册）

`scripts/install_backup_schedule.py`——Windows 任务计划程序登记器，GitHub 托管 runner cron 摸不到
本机 `data/`（`.github/workflows` 实测 **9** 个文件带 `schedule:`——benchmark `7 0 * * 0`、codeql
`17 3 * * 1`、docker-smoke `47 5 * * 1`、e2e `13 2 * * *`、gitleaks `47 0 * * 0`、security `23 1 * * 0`、
self-purify `47 2 * * 1`、stale `37 4 * * *`、stress `47 3 * * *`，**无一与备份相关**；工单锚「6 个」
为少数口径，以实测为准，结论不变），故调度落本机。

- **频率自洽**：注册每日任务，对齐 §3.3 节奏句「每日全量备份」；触发 03:00 与既有台账示例
  `history-20260831-030000` 备份时刻同例；§3.2「每季度一次」为全流程演练**人工 SOP**、
  `--drill` 周频为脚本自述建议，均不混入本登记器（避免造出第二套成文节奏）。
- **命令行即文档命令**：与 §3.3 推荐入口逐字一致（venv 解释器绝对路径 + `--keep 14`；两路径均
  `__file__` 锚定，不依赖任务计划程序的工作目录）。
- **幂等**：固定任务名 `TTS-MultiModel-Backup-Daily`（`/tn`）+ `/f` 就地更新——同名注册不产生
  第二个任务；重复 `--dry-run` 输出逐字节一致（`_ev/schedule-dry-run.txt` vs `-2.txt`，cmp 通过）。
- **dry-run 样例**（2026-11-02 12:19 实测，退出码 0；`_ev/schedule-dry-run.txt`）：

```
[dry-run] 备份调度登记计划（Windows 任务计划程序），未做任何系统写入：
  任务名        : TTS-MultiModel-Backup-Daily
  频率          : 每日（对齐 docs/SRE_RUNBOOK.md §3.3「每日全量备份」节奏句）
  触发时刻      : 03:00（每日，本机时区）
  完整命令行    : "C:\Users\Doro\APP\repository\TTS-MultiModel\.venv\Scripts\python.exe" "C:\Users\Doro\APP\repository\TTS-MultiModel\scripts\backup_history_db.py" --keep 14
  工作目录      : C:\Users\Doro\APP\repository\TTS-MultiModel
  运行身份      : Doro（当前登录用户，交互式运行；不配置服务账号、不存储密码，用户未登录时不执行——补注册授权后如需无人值守，属所有者决策项）
  下一次应执行  : 2026-11-03 03:00:00
  同名任务探测  : 不存在 → --apply 时将新建唯一任务
  注册命令(供授权后使用，本 dry-run 未执行):
    schtasks /create /f /tn TTS-MultiModel-Backup-Daily /tr "…完整命令行…" /sc daily /st 03:00
[dry-run] 结束。未调用 schtasks /create /change /delete，未写注册表/文件。
```

- **下一次应执行时刻：2026-11-03 03:00:00**（本机时区；实测时点 12:19 已过当日 03:00）。
- **测试**：`tests/test_install_backup_schedule.py` **11 项全绿**（覆盖 dry-run 七要素契约、
  幂等判据〔`/f` 在 argv、同名探测话术、重复运行逐字节一致、apply 仅单次 `/create`〕、`--dry-run`
  与 `--apply` 互斥、非法时刻退 2、schtasks 不可用降级、跨槽时刻计算）。**测试不触碰系统**：全部
  经注入的 `FakeRunner` 断言 argv，从不调真实 `subprocess`，不读写任务计划程序（文件头自述）。
- 偏差登记：修复了首版 dry-run 中 schtasks 中文 GBK 报错触发的 `UnicodeDecodeError`
  （`errors="replace"`），修复后 stderr 0 字节。

## 6. 全量测试套复验（两口径，日志/计时 `_演/testrun-*`）

| 口径 | 命令要点 | passed | failed | skipped | 退出码 | 计时 |
|---|---|---|---|---|---|---|
| A（对齐基线 2603/0/41） | `pytest tests/ -v --tb=short --ignore=tests/e2e` + ci 离线 env | **2,614** | **0** | **41** | **0** | 12:33:17→12:34:49（pytest 内部 88.85s） |
| B（ci.yml L233 选集+cov 门禁） | A + `-m "not integration and not gpu and not cuda and not vram and not benchmark and not stress"` + `--cov=app/integrated_app --cov-branch --cov-fail-under=45`（html/xml 重定向仓外） | **2,522** | **0** | **10**（另 deselected 123） | **0** | 12:34:58→12:37:53（内部 171.00s）；覆盖率 **52.39% ≥ 45%** 达标 |

- **终跑口径**：首跑曾于 12:19:28（A）/12:21:18（B）执行，其后 dry-run 解码偏差修复与 ruff 整改
  改动了 `scripts/install_backup_schedule.py` 与测试文件本体，为保证「提交的树==被测的树」，
  上表为整改后**终跑**数字；两次运行 passed/failed/skipped/deselected/覆盖率逐项一致
  （2,614/0/41 与 2,522/0/10/123、52.39%），首跑日志已被终跑覆盖（`_演/testrun-*.log` 为终跑）。

- 对账：A 2,614 = 基线 2,603 + 本轮新增 11（`test_install_backup_schedule.py`）；B 2,522 = 上轮 2,511 + 11。
  **零失败、零丢失**，skipped/deselected 与基线逐项一致。
- 覆盖率 52.39% vs 上轮 52.41%（−0.02pt）：本轮新增代码在 `scripts/`（不在 `--cov=app/integrated_app`
  分子内），微降系运行态行数抖动，如实登记。
- 偏差登记（沿用工单口径）：venv 缺 `pytest-timeout` 且禁装，ci 命令的 `--timeout=180` **未执行**；
  cov 产物全部落 `_演/`（`coverage.xml`、`htmlcov/`），`git status --porcelain` 复核生产工作树
  零 cov 残留（上轮 htmlcov 污染不再犯）。

## 7. 未覆盖清单（本轮没做什么、为什么、需要什么）

1. **调度真实注册未做**——工单硬约束 4：本轮只交付实现 + dry-run 产物；注册需所有者单独授权
   （执行 `--apply` 一条命令即可，运行身份/命令行已全文可见）。
2. **异地副本未解决**——同机备份的事实边界：`data/backups/` 与活体同盘同机，盘毁则备份同毁。
   需要属主给**第二个存储位置**（NAS/第二机/对象存储任一）后才能接线，**登记待办≠已解决**。
3. **周期性待 2027-01**——2026-10-31 首轮周期点已建；调度未注册即无机器周期证据，到期轮次佐证前
   O4 判据③不满足。
4. 5 个运行密钥（`.pii_key`/`.history_hmac_key`/`.watermark_key`/`.csrf_secret`/`.manifest_signing_key`）
   无任何备份覆盖——纳入备份面涉及密钥入盘副本的安全折衷，属所有者决策（§3.3 已成文暴露）。
5. `generation_versioning` 库与 `data/generation_versions.db` 旧副本、`generation_times.json` 不在
   备份集（定性见表）；`data/audit.log` 当前无对象可备（代码在、未落盘）。
6. `data/push_subscriptions.db` 孤儿残骸处置（删除与否）未做——涉及删数据，属主决策。
7. **F6（新登记，未修）**：测试套件直写生产 `data/history.db.action_logs`。实测链：对平 PASS
   （12:18:32，2,666 行）→ 口径 A/B 窗写入 20 行测试噪声（id 2,675–2,686，行时间戳
   12:20:38.983–12:23:01.831，内容含 `test_operation`/`测试操作`/`test_hard_timeout_engine`）→
   12:25:43 重放因活体漂移 FAIL；阶段 8 窗（11:26:37–11:29:35，id 2,647–2,666）同型——工单锚
   「11:29 活跃在写」实为测试写。**终跑复验又写入 20 行**（id 2,687–2,706，行时间戳
   12:33:53.650–12:36:44.837，表行数最终 2,706）：本轮累计 +40 行噪声，因「禁改活体数据」
   未回删。修复（fixture 指向临时库）超出阶段 9 范围，登记属主决策。
8. 本轮不做的事（工单「明确不做」照单）：`rollback_release.py`、`requirements-lock.txt`、O3/E7/P25、
   其他仓库、服务启停、联网装依赖——均未触碰。

## 8. O4 四态自判（按三判据逐项）

| 判据 | 结论 | 依据 |
|---|---|---|
| ① 可复核 | **通过** | 本 runbook + `_演/` 顶层 20 项证据（18 个文件 + `htmlcov/`、`restore_tmp/` 两目录：事前/事后备份清单、stdout、退出码、计时、产物 sha、对平报告与漂移重放、冷 shell 日志、dry-run 两次逐字节一致样、两口径全量日志+计时文件、coverage.xml、对平脚本本体）；文档命令冷 shell exit 0；测试 11 项可重放 |
| ② 对象是活体 | **通过** | 执行对象为活体生产库 `data/history.db`（2,408,448 B，mtime 11:29:43 实测在写窗口后静止）；备份经真实链路真跑，非合成最小库；恢复回读对平 9/9 表（§4 数字） |
| ③ 周期性 | **不满足** | 调度登记产物已交付但**未注册**（待授权）→ 本机无机器周期；2026-10-31 首轮人工周期点在案，**待 2027-01 到期轮次佐证**。异地（备份链的存储多样性前提）仍未解决，属资源决策 |

**O4 自判：`[?]`（保持）**。本轮真实关掉了四阻塞中的两个（F3 死路径、调度无接线之「实现缺失」半边），
并令覆盖口径可判定；但③周期性、④异地两个硬前提未动，**不翻 `[x]`**，也不以「登记待办」伪装解决。

## 9. 并发文件登记（约束 3）

起测 `git status --porcelain` 空；实施完毕待提交时复核，全部 6 条（`M docs/SRE_RUNBOOK.md`、
`M docs/TODO.md`、`M docs/runbooks/2026-10-31-restore-drill.md`、`D scripts/backup_db.py`、
`?? scripts/install_backup_schedule.py`、`?? tests/test_install_backup_schedule.py`）均可归属本轮，
**并发外部改动 0 条**，未出现需隔离的他人文件。活体侧变化（`data/` 全 gitignore，不入库）：
`data/backups/` 新增 2 份本轮备份物、`data/history.db(+wal/shm)` 因 F6 测试窗被推前 mtime——
均已如实登记，非本方直接改写。
