# F6 测试套写污染生产库——止血、零增长证明与可逆隔离（整改阶段 10，2026-11-02）

> 工单：`wo_stage10.txt`。原则：**只隔离，不删除**——可证噪声与疑似噪声之间没有机械可判
> 边界（见 §3），任何 `DELETE` 都可能连真用户历史一起毁掉；移表在**同一事务**内完成、
> 隔离侧表逐行保留原值、回滚路径**实测**过（§5）。
> 全仓唯一改动面：`tests/conftest.py`（全局隔离+哨兵）、`tests/test_f6_db_isolation_sentinel.py`
> （自证 8 项）、本 runbook、台账两处（`docs/TODO.md` F6、`docs/SRE_RUNBOOK.md` §3.3/§3.4）。
> `local/docs-archive/agents/GOTCHAS.md` #196 为本地层（`.gitignore` 中 `docs/agents/` 与 `/local/` 均已忽略，本地归档、未随仓库发布），不入提交。
> 证据目录（仓外）：`C:/Users/Doro/Desktop/评估报告_2026-10-29/_演练/F6-isolation/`。

## 1. 处置前基线备份与验证（硬约束 2，未通过不得动手）

| 步骤 | 时刻（精确到秒） | 命令/产物 | 结果 |
|---|---|---|---|
| 备份（活链真跑） | 14:29:20→14:29:20 | `.venv/Scripts/python.exe scripts/backup_history_db.py --keep 0`（**显式 --keep 0**：默认 keep=5 会按 `history_backup_*` 裁剪既有备份，阶段 9 留证不可动） exit **0** | `data/backups/history_backup_20261102_142920.db` **2,408,448 B**，sha256 `a845230ce7d5a7cda0d4e01930918c6554d59cb9ede68981fdd46775005c5f83`（`backup-verify/backup-verification.log`） |
| 仓外恢复回读 | 14:29:36→14:29:36 | `--restore <上述备份> --target <演练目录>/backup-verify/restore_tmp/history_restored.db` exit **0**（`restore_backup` 内建 `PRAGMA integrity_check`≠ok 即抛，能 exit 0 即已过） | 回读 `action_logs=2,706` 行、`MAX(ts_ms)=1793594204837`、`generation_history=468`、表 11 张——与处置前活体（§2）**完全一致**。备份验证通过，方进入下一步 |

## 2. 处置前活体指标与处置后对照（零增长证明，硬约束核心）

处置前实测（`metrics/pre-treatment.json`，14:26:19）与两次全量复跑后对照
（`metrics/post-runA1.json` 14:40:37、`metrics/post-runA2.json` 14:42:40；跑测前基线
`metrics/pre-runA.json` 14:38:13 与 14:26:19 处置前完全一致）：

| 指标 | 处置前 14:26:19 | runA1 后（14:40:22 跑毕） | runA2 后（14:42:31 跑毕） | 结论 |
|---|---|---|---|---|
| `action_logs` 行数 | **2,706** | 2,706 | 2,706 | **零增长** |
| `MAX(ts_ms)` | 1793594204837（=2026-11-02 12:36:44.837） | 不变 | 不变 | 无新行 |
| `generation_history` 行数 | 468 | 468 | 468 | 同库另一表亦零增长 |
| `history.db` bytes/sha256 | 2,408,448 / `e19cb1822cd8634f…13ba04a` | 逐字节同 | 逐字节同 | 未触达 |
| `history.db-wal` bytes/sha256 | 5,401,352 / `f741c6f7f713d416…91ea6db0` | 逐字节同 | 逐字节同 | 未触达 |
| `history.db-shm` bytes/sha256 | 32,768 / `55c2951d9072bf68…80199` | 逐字节同 | 逐字节同 | 未触达 |
| `outputs/generation_versions.db` sha256 | `41ab2af72b3af97c…9fda401`（mtime 12:36:31.948，正落阶段 9 测试窗——**第二类中招文件**） | `sha256 -c` OK | OK | 重定向生效 |
| `data/push_subscriptions.db-wal/-shm` | `3de7276e…`/`4b6f6154…`（mtime 2026-08，孤儿） | OK | OK | 无代码引用、无写入 |

**时间维度互证**：本轮口径 A 两跑的窗口（14:38:23–14:40:22、14:40:44–14:42:31）在生产库
`ts_ms` 轴上是全新空段——`census/window-analysis.json` 实测该两段行数 **0 / 0**（对比阶段 8/9
同轴窗口各 10–20 行）。若隔离没兜住，这两窗必然带行；两窗全零 + 哈希不变 = 兜住。

阶段 9 基线（2614 passed / 0 failed / 41 skipped）与本轮口径 A 对齐关系：本轮新增哨兵自证
8 项 ⇒ 期望值 **2622**。两跑实测（`testlogs/runA1-exit.txt`、`runA2-exit.txt`）：

| 口径 | 时刻 | 结果 | exit |
|---|---|---|---|
| A（`pytest tests/ -v --tb=short --ignore=tests/e2e` + ci 离线 env） | 14:38:23→14:40:22（内部 114.84s） | **2622 passed / 0 failed / 41 skipped** | 0 |
| A 复跑（幂等确认） | 14:40:44→14:42:31（内部 103.11s） | **2622 passed / 0 failed / 41 skipped** | 0 |

## 3. 噪声普查（census，14:26:52 实测）与三档结论

判据 SQL（与 `census/census-pre.json` 同文可复放）：

```sql
-- 总量
SELECT COUNT(*) FROM action_logs;                                            -- 2,706
-- 桶1（可证）
SELECT COUNT(*) FROM action_logs WHERE action='test_operation';              -- 347
SELECT COUNT(*) FROM action_logs WHERE action='test_operation'
  AND message='测试操作' AND extra_json='{"key": "value"}';                  -- 347（签名 347/347 统一）
-- 桶2（可证）
SELECT COUNT(*) FROM action_logs WHERE action='unknown'
  AND message='' AND extra_json='{}';                                        -- 344
-- 桶3（可证）
SELECT COUNT(*) FROM action_logs WHERE action='generation'
  AND extra_json LIKE '%"engine": "test_%';                                  -- 1,476
SELECT COUNT(*) FROM action_logs WHERE action='generation'
  AND extra_json LIKE '%"endpoint": "ep"%';                                  -- 1,230（⊂ 桶3，交集 1,230）
-- 待判定桶
SELECT COUNT(*) FROM action_logs WHERE action='generation'
  AND extra_json NOT LIKE '%"engine": "test_%'
  AND extra_json NOT LIKE '%"endpoint": "ep"%';                              -- 451
SELECT COUNT(*) FROM action_logs WHERE action='model';                       -- 88
```

实测与工单锚的出入（**数字以本轮为准**，工单要求自测复核）：

| 项 | 工单（派单方 13:2x） | 本轮实测（14:26） | 差因 |
|---|---|---|---|
| `action_logs` 全量 | 2,696 | **2,706** | 派单方读数未含阶段 9 终跑落盘的尾部 10 行（WAL 内 12:36:24–12:36:44 行；本轮 mode=ro+wal 可见口径读取） |
| `test_operation` | 346 | **347** | 同上 |
| `unknown` 空签名 | 344（343） | **344** | 同上 |
| `generation`/`engine test_` | 1,919 / 1,470 | **1,927 / 1,476** | 同上 |
| 「剩下 449」 | 1,919−1,470=449 | 1,927−1,476=**451**；另工单未提的 `model` **88** 行 | — |
| 「时间戳逐对相同」 | 断言双写同 ts | 严格同值仅 **1/347**；±2s 内配对 **344/347**（典型相差 ~5ms，如 12:36:44.832/.837） | 双写机制成立，表述修正为「毫秒级相邻」 |
| 「449 行正落在阶段 9 窗口内、不可采信」 | 全量定性 | **451 行中仅 12 行（2.66%）**落在可考证窗口内；270 行耗时 ≤0.4s 但多在窗口外（7/31–11/1 无窗口记录期）；439 行窗口外（min 2026-07-31 13:34:18，max 2026-11-01 21:32:20） | **工单定性夸大，如实降级为「高度疑似 12 行 + 无法判定 439+88 行」**；不许把疑似写成可证，反向亦然 |

可考证测试窗口（出处：阶段 8/9 `testrun-*-exit.txt` 与两份 runbook 台账）及窗内行数
（`census/window-analysis.json`）：S8-A 11:26:03–11:27:32（10 行）、S8-B 11:27:43–11:31:00
（10）、S9-round1 12:19:57–12:23:08（20）、S9-finalA 12:33:17–12:34:49（10）、S9-finalB
12:34:58–12:37:53（10）；窗内 60 行 = 可证签名桶 48（6+6+36）+ 无签名疑似 12。三桶可证行
跨度 **47 天/47 天/35 天**（2026-08-08 00:31:20 → 2026-11-02 12:36:44），即长期逐轮测试累积，
非单轮事件。

**三档结论**：可证测试 **2,167 行**（347+344+1,476，机械判据、隔离对象）；高度疑似
**12 行**（无签名且 ts 落于可考证窗口）；无法判定 **439 + 88 = 527 行**（无签名、窗口无记录；
`model` 88 行全在窗外）——后两档**原地保留**，待属主判定。

## 4. 止血实现（机制层，禁「记得设」）

`tests/conftest.py`（导入期即生效，早于任何测试模块 import；三层）：

1. **重定向（应用承认的入口）**：`os.environ["TTS_HISTORY_DB_PATH"]`（**强制赋值，非
   setdefault**）指向 `tempfile.mkdtemp(prefix="tts-f6-history-iso-")/data/history.db`。
   该 env 正是 `history_db.py::get_history_db()`（L2745-2746 注释优先级）与
   `config.py::get_history_db_path()`（L240）认账的第一优先级入口——`action_logs` 与
   `generation_history` 同库一并覆盖；测试起的子进程（`python -c` 真跑脚本）经 env 继承
   同样落隔离库（哨兵自证第 8 项实测）。
2. **哨兵（机器可判的「以后不会再犯」）**：conftest 包装 `sqlite3.connect`，目标路径
   经 `normcase+realpath+abspath` 归一（含 `file:` URI、`?mode=ro`、相对路径）命中
   生产保护集（默认 `data/history.db` ∪ `config.yaml history.db_path` ∪ 调用方原 env 值）
   即抛 `ProductionDatabaseAccessError`，**错误信息点名当前测试 nodeid**
   （`pytest_runtest_setup` 钩子记录）——直接失败并指名测试文件，不静默。
   注意：`log_operation()` 会吞 `RuntimeError`（设计如此），所以验收口径是**生产文件
   字节级不变**（§2），而非日志有无报错；哨兵装在 `connect` 层，绕不开单例路径。
3. **旁路重定向（无 env 入口的第二类库）**：`VersionManager(db_path=None)` 默认路径
   `outputs/generation_versions.db`（实测 mtime 落在阶段 9 窗内，同型被污染）。应用对该库
   没有 env/config 优先入口（`SAVE_DIR` 硬编码），故哨兵对该**精确路径**做透明重定向
   并留 `redirect_log`（自证第 6 项对账 from/to）。`push_subscriptions.db`：全仓 `.py`
   零引用（`data/` 仅剩孤儿 `-wal/-shm`，mtime 2026-08），**无同类写入，不处置**，处置
   残骸与否沿阶段 9 属主决策项。

`tests/test_f6_db_isolation_sentinel.py`（8 项自证）：env+包装在位、单例落隔离库、plain/URI/
相对路径三种打开生产库都被拦且点名本文件、versions 默认路径重定向留痕、`log_operation`
双写实测落隔离库、子进程 env 继承。全部通过（§2 两跑 + 单跑 `19 passed`）。

## 5. 可逆隔离（仅可证三桶）与对账

隔离表：`action_logs_quarantine_20261102`（id/ts_ms/level/action/message/extra_json 六列
**原值** + `quarantine_reason` + `quarantine_batch='F6-20261102-A'`）。执行器：
`_演练/F6-isolation/scripts/quarantine_f6_action_logs.py`（isolate/rollback/verify 三态，
单事务 `BEGIN IMMEDIATE`，先 `INSERT…SELECT` 全列入隔离表、同事务内才按隔离表 id 删主表，
`integrity_check` 收口）。

| 时刻 | 操作 | exit | 数字 |
|---|---|---|---|
| 14:45:14 | **isolate** | 0 | 移入 347+344+1,476 = **2,167**；主表余 **539**；integrity ok；主表新 `MAX(ts_ms)`=1793594184555（12:36:24.555，即最高行的「VoxCPM design failed (0.3s)」——属高度疑似档，按工单原地保留） |
| 14:45:14 | verify | 0 | 等式一 **2,167 == 347+344+1,476 ✓**；等式二 **2,167 + 539 == 2,706 ✓**（处置前行数） |
| 14:45:29 | **rollback 实测** | 0 | 一条命令：`python -I _演练/F6-isolation/scripts/quarantine_f6_action_logs.py rollback`；主表回 **2,706**、`MAX(ts_ms)` 回 1793594204837、隔离表清 0；integrity ok |
| 14:45:30 | 回滚保真 | — | 行内容指纹（隔离表 2,167 行六列有序 sha256）**FP1 `13923865a2d64a70…22c4fb63` == 回滚后主表同判据 FP2** ✓——「原样并回」逐行验证 |
| 14:46:35 | **re-isolate**（终态） | 0 | VERIFY PASS：主表 539 / 隔离 2,167，两等式再次成立；post-isolation `history.db` sha256 `9652e5e78a679981…eb162f1` |
| 14:47:02→14:50:30 | 隔离态下口径 B 全量 | 0 | 生产库 **539→539**、db sha256 不变、wal bytes 不变（`metrics/post-runB.json`）——移表后测试套仍零增长 |
| （追加）14:57:50→14:59:21 | 收敛 conftest 后口径 A 复跑（runA3） | 0 | 生产库仍 **539** 行、`MAX(ts_ms)`/三文件 sha256 **逐字节不变**（`metrics/post-runA3.json`，14:59:30）——最终 conftest 版本（补 URI-form 重定向 + ruff 格式化）仍零增长，口径 A 亦 **2622/0/41** |

读路径检查（`quarantine/09-readpath.log`，隔离态、`mode=ro`、真用 `logs.py::_query_from_db`
与 `_build_filter_sql` 代码跑真数据）：索引在位（`idx_action_logs_ts_ms`/
`idx_action_logs_level_action`）；`GET /api/system/logs` 首页 total=539、按
`action=generation` 过滤 total=451、`test_operation` 过滤 total=0（隔离后为空是**预期**
显示形态，非报错）、越界页 items=[] 不炸、逐页遍历 539==total；`logs-compat` 端点数据源为
内存环形缓冲（`health_monitor.js` 同源），不读 DB、不受影响；`/logs/clean` 仅 COUNT 形态
探针（total=539、30d 外 353），**未执行其 DELETE**。全部读方清单：路由
`routes/system/logs.py`（唯一 SQL 读方）+ `templates/tabs/settings.html`（page_size=30）+
`static/js/health_monitor.js`（logs-compat）+ `templates/tabs/help.html`（仅文档）。
无任何路径对「行数恒定」或「某 action 必存在」有硬假设——**移表不破坏任何读路径**。

## 6. 两口径测试（日志与计时全在仓外 `_演练/F6-isolation/`）

| 口径 | 命令 | 时刻 | 结果 | exit |
|---|---|---|---|---|
| A ×2 | 见 §2 | 14:38:23→14:40:22 / 14:40:44→14:42:31 | 2622/0/41 ×2 | 0 / 0 |
| A ×1（runA3，最终 conftest 复验） | 同上 | 14:57:50→14:59:21（内部 87.76s） | 2622/0/41，且生产库隔离态 539 行零增长、哈希不变 | 0 |
| B | A 集 + `-m "not integration and not gpu and not cuda and not vram and not benchmark and not stress" --cov=app/integrated_app --cov-branch --cov-report=xml/html→仓外 --cov-fail-under=45` | 14:47:02→14:50:30（内部 203.96s） | **2530/0/10（123 deselected）**；覆盖率 **52.33% ≥ 45%** | 0 |

B 与 ci.yml L233 的口径差：本机 venv 无 `pytest-timeout` 插件（禁装依赖硬约束），故未带
`--timeout=180`（阶段 9 台账同案先例，其 B 亦无该插件）。B 的期望值 = 阶段 9 基线
2522/0/10 + 哨兵 8 = 2530 ✓。A/B 全部产物（`runA1/runA2/runB.log`、`*-exit.txt`、
`coverage.xml`、`htmlcov/`）均在仓外；生产工作树 `git status --porcelain` 仅本轮 5 个改动
路径（2 测试面 + 3 文档面），无 htmlcov/coverage.xml 混入。

## 7. 未覆盖清单（本轮没做什么、为什么）

1. **高度疑似 12 行 + 无法判定 439+88 行**（`generation` 无签名 + `model`）**原地保留**，
   未移未删——无机械可判边界，处置=属主判定（工单处置原则明写）。
2. **既有备份物仍含噪声**：`history_backup_20261102_121704.db`、`_121844.db`（阶段 9）与
   本轮 §1 的 `_142920.db` 均取于隔离前，含可证噪声 2,167 行；**是否以隔离后活体重取
   干净备份并退役旧物，属主定**（本轮不擅自重取/删除备份）。
3. **异地仍未解决**（沿阶段 9：同机同盘，需属主给第二存储位置）。
4. **调度仍未注册**（`install_backup_schedule.py --apply` 本轮未调用，工单硬约束 4）。
5. 哨兵只拦**本进程** `sqlite3.connect`；测试若起子进程**显式硬编码**生产路径跑写库
   脚本，靠 env 继承兜底（§4 第 1 层），该组合路径已由子进程自证覆盖，但理论上「子进程
   内先 delenv 再连生产」不在哨兵视野——此类行为会被 §2 的字节级对账口径当场抓住。
6. `data/generation_versions.db`（旧副本，mtime 2026-09-05）与 `generation_times.json`
   等既有覆盖边界沿阶段 9 表；`push_subscriptions.db` 孤儿残骸处置仍属主决策。
7. 未触碰：`scripts/rollback_release.py`、`requirements-lock.txt`、其他仓库、服务启停、
   系统计划任务、venv/依赖——均未动。无 push、无 tag 变更、无分支切换、无 worktree。

## 8. 留痕索引（`_演练/F6-isolation/`）

`scripts/{measure_db,census_db,window_analysis,quarantine_f6_action_logs,readpath_check}.py`
（全部只读或事务内操作，判据可复放）；`metrics/pre-treatment|post-sentinel-run|pre-runA|
post-runA1|post-runA2|post-isolation|post-runB|post-runA3.json` + `*-extra-files.sha`；
`census/census-pre.json`、`census/window-analysis.json`；`backup-verify/*`（备份 stdout、
exit、验证日志、仓外 restore_tmp）；`testlogs/runA{1,2,3}/runB(.log|-exit.txt)`；
`quarantine/01-isolate…09-readpath.log`（隔离、验证、指纹、回滚、复隔离、读路径全链）；
`coverage.xml`、`htmlcov/`。
