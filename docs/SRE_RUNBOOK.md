# SRE 运行手册（SLA / SLO / 回滚 / 灾难恢复演练）

> 配套评估：`docs/_devarchive/SRE评估_v2.2.0.md`、`docs/_devarchive/容器化成熟度评估_v2.2.1.md`（本地归档目录，不随仓库分发）
> 适用版本：v2.7.0（与 `config.yaml` / `pyproject.toml` 一致）

---

## 1. SLA / SLO

### 1.1 服务等级目标（SLO）

| 指标 | 目标 | 度量方式 | 数据源 |
|------|------|----------|--------|
| 可用性（月度） | ≥ 99.5% | liveness `/api/health/ping` 探活成功率（根路径实现于 `app_server.py`；`routes/system/health.py` 提供同构的 `/api/system/health/ping`） | k8s liveness + `routes/system/health.py` |
| readiness（模型就绪） | ≥ 99.0% | 启动后 `/api/health/ready` / `/readyz` 返回 `ok` | 同上 |
| P95 生成时延（短文本 ≤200 字） | ≤ 8s（GPU） | `_execute_generation` 计时 | `GenerationTracker` / `history_db` |
| 生成成功率 | ≥ 99.0% | 成功/失败计数 | `/api/system/stats`（累计统计）+ Prometheus `tts_generations_total` |
| 鉴权端点 P99 时延 | ≤ 50ms | 中间件耗时 | 访问日志 |
| 审计日志落盘成功率 | 100% | 文件写入失败计数 | `data/audit.log` + 告警 |

### 1.2 错误预算（Error Budget）

- 月度可用性 99.5% ⇒ 允许的不可用时长 ≈ **3.6 小时/月**。
- 超出错误预算时：冻结非紧急发布、优先处理稳定性项、复盘（见 §4）。

### 1.3 探针（Probe）约定

- **liveness**：`GET /api/health/ping`（内存级，不碰 DB/GPU），失败即重启 Pod。
- **readiness**：`GET /api/health/ready` 或 `GET /readyz`（深度：模型加载状态 + DB 连通 + GPU），未就绪则不接流量。

---

## 2. 回滚（Rollback）

### 2.1 镜像回滚（推荐，最快）

```bash
# 查看已部署 digest
docker images tts-multimodel --format '{{.ID}} {{.Digest}} {{.CreatedAt}}'
# 回到上一个已知良好 digest（CI 在 release 时固定钉版，见 SECURITY.md）
docker tag <previous_sha256> tts-multimodel:previous
docker compose up -d
# 或 k8s
kubectl rollout undo deployment/tts-multimodel
```

> 安全红线：生产镜像必须钉版（digest），禁止 `:latest` 漂移。CI 在 `docker-build.yml`
> 中对关键/高危漏洞用 Trivy 阻断（`CRITICAL,HIGH` + `ignore-unfixed`，豁免见 `.trivyignore.yaml`）；发布前需在 `CHANGELOG.md` 登记版本与变更。

### 2.2 配置回滚

`config.yaml` 是单一权威配置源，纳入 Git 版本管理。回滚配置即 `git revert` 对应提交并重新挂载。
**切勿在容器内直接改配置**：容器以只读挂载 `config.yaml` + 环境变量注入密钥（见 `docker-compose.yml`）。

### 2.3 权重回滚（禁区）

`model/` 为权重禁区，禁止运行时修改。权重回滚需经人工逐项确认 + `model_checksums.json`
SHA-256 复验（`scripts/generate_model_checksums.py` 生成，C1 加载链路校验）。

### 2.4 回滚检查清单

- [ ] **【硬停断言，2026-11-01 C 缺口收口新增】回滚前校验「目标 tag ↔ `version.json` `$.version` ↔ `CHANGELOG.md` 顶部条目」三者一致，且 `version.json` 内嵌 `changelog` 字段自述版本 = `$.version`（拦「版本号新、说明旧」）**——任一不一致即**硬停**，不得继续回滚；提示操作者按 `docs/release-governance.md` §1 人工版本位清单补齐并重打 tag（需所有者授权），或显式接受版本位漂移并记录。依据：v2.8.0 实测——tag/version.json/CHANGELOG 顶部名义同为 2.8.0，但**内嵌 changelog 自述 2.7.0**、11 处版本位中手工位（`config.yaml`/`deployment.yaml`/`Cargo.lock`/`setup.nsi`）=2.7.0（`docs/runbooks/2026-11-01-o3-failover-drill.md` §4）——回滚到该 tag 会得到「版本自称 2.8.0、配置与说明是 2.7.0」的树。配套一致性门禁 `tests/test_version_consistency.py` 已存在（SOP 断言复用其口径；本轮不新建脚本）
- [ ] 确认回滚范围（镜像 / 配置 / 权重）与原因
- [ ] 通知相关方（值班 + 业务方）
- [ ] 执行回滚（镜像 tag / kubectl undo / git revert config）
- [ ] 验证 readiness 探针恢复 `ok`
- [ ] 检查审计日志 `data/audit.log` 无异常激增（如 auth_failure 暴涨）
- [ ] 复盘并记录到变更记录

### 2.5 进程级自愈口径（本机裸跑 vs 容器编排，2026-11-01 阶段 4 D-1 收口）

- **本机裸跑形态不承诺进程级自愈**：实测（阶段 2b，T0=10:12:47）kill 服务子进程后 T+60s 无任何守护/自愈拉回、launcher 一并退出；`clean_launch.py` 无 respawn 循环。所有者已拍板**接受该形态**（不引入 NSSM/任务计划/守护脚本）。
- 人工恢复路径（实测 RTO）：重新执行启动命令（`start.bat` 或 `python app\clean_launch.py`）→ **T_manual(liveness)=9s**；模型重载 +61s → **可合成合计 ≈3m12s**。
- **什么形态才承诺自愈**：容器/k8s 编排层 livenessProbe（`deploy/kubernetes/deployment.yaml` L149-156，`/api/health/ping` period 30s × failureThreshold 3 ≈ **90–120s 检测窗，设计值非实测**）+ Pod 重建。本机结论不得外推到容器形态。

---

## 3. 灾难恢复演练（DR Drill）

### 3.1 威胁场景与 RTO/RPO

| 场景 | RTO | RPO | 恢复手段 |
|------|-----|-----|----------|
| 单 Pod 崩溃 | < 1 min | 0 | k8s 自愈（liveness 重启） |
| 节点宕机 | < 5 min | 0 | 调度到其他节点 |
| 配置误改 | < 10 min | 0 | Git 回滚配置 |
| 权重损坏/篡改 | < 30 min | 按留存策略 | 从备份恢复权重 + `model_checksums.json` 复验 |
| 数据库（history_db）损坏 | < 15 min | ≤ 留存窗口 | 从备份恢复 SQLite；HMAC 链校验失败即告警 |
| 密钥泄露 | < 5 min | — | 轮转 `TTS_API_AUTH_TOKEN` / `TTS_PII_FERNET_KEY`，重启使配置生效 |

### 3.2 演练步骤（每季度一次）

1. **准备**：在预发环境复制当前部署（同镜像 digest + 同配置）。
2. **注入故障**：kill 主 Pod / 删除 `config.yaml` / 损坏一个权重文件。
3. **观测**：确认 readiness 探针翻转、告警触发、流量切换。
4. **恢复**：按 §2 执行回滚/恢复，确认 SLO 恢复。
5. **复盘**：记录 RTO/RPO 实测、偏差根因、改进项（更新本手册）。

### 3.3 数据备份

- `history_db`（SQLite）：每日全量备份 + 一致性快照；PII 字段按 `security.pii_retention_days`
  留存，到期由启动期清理任务删除（`history_db.purge_expired`）。
- **落地脚本（唯一推荐入口，阶段 9 修订）**：`scripts/backup_history_db.py`
  （对应评估 P3-1「无 DB/历史数据备份」）。它使用 SQLite backup API 做一致性快照
  （运行期亦可安全备份，含未 checkpoint 的 WAL 内容），时间命名去重、`--keep` 轮转，
  恢复后自动 `PRAGMA integrity_check`，并支持 `--drill` 临时目录恢复演练；产物落
  `data/backups/`。配套测试 `tests/test_backup_history_db.py`。历史库路径按成文优先级
  解析（环境变量 `TTS_HISTORY_DB_PATH` > `config.yaml → history.db_path` > 默认
  `data/history.db`），不依赖 CWD。原推荐入口 `scripts/backup_db.py` 自述对象
  `data/tts_history.db`（系 2026-08-16 `64a753f` 配置清理后遗留的死命名）与
  `data/audit.log`（实测未落盘）**已于阶段 9 退役删除**，决策依据与取证见
  `docs/runbooks/2026-11-02-o4-backup-wiring.md` §3。

  ```bash
  # 每日全量备份（与本机调度登记产物 scripts/install_backup_schedule.py 的注册命令行
  # 一致；该产物默认 --dry-run，真实注册待所有者授权后进行）
  .venv/Scripts/python.exe scripts/backup_history_db.py --keep 14
  # 从指定备份恢复历史库（--target 会覆盖目标路径，执行前确认）
  .venv/Scripts/python.exe scripts/backup_history_db.py --restore data/backups/history_backup_<时间戳>.db --target data/history.db
  ```

- **备份集覆盖（可判定；逐资产实测表见 `docs/runbooks/2026-11-02-o4-backup-wiring.md` §2）**：
  默认备份集仅覆盖主业务库 `data/history.db`。`config.yaml` 由 git 跟踪（内容版本可
  还原，不依赖备份集）；`data/` 下 5 个运行密钥（`.pii_key`/`.history_hmac_key`/
  `.watermark_key`/`.csrf_secret`/`.manifest_signing_key`）**无任何备份覆盖**，丢失即
  链路断裂，密钥侧管理属所有者决策项；`generation_versioning` 库 `enabled=false`
  （治理决策：保留模块、停用功能），不在默认备份集，如启用可经 `--db-path` 显式覆盖；
  审计日志 `data/audit.log` 有代码实体（`audit.py`）且配置开启，但截至 2026-11-02
  实测未落盘，当前无此对象可备。

- **测试噪声治理（阶段 10，2026-11-02）**：F6 已止血——`tests/conftest.py` 全局强制
  `TTS_HISTORY_DB_PATH` 到会话临时库并加 `sqlite3.connect` 哨兵（任何测试打开生产
  `data/history.db` 直接失败并点名测试），`outputs/generation_versions.db` 默认路径透明重定向；
  全量测试两口径实测对生产库**零增长**（行数/max_ts/sha256 逐字节不变）。历史存量可证噪声
  2,167 行已移入 `action_logs_quarantine_20261102`（可一条命令回滚，实测过；疑似 451+88 行
  原地待属主判定）。**注意：隔离前取的备份物（含 12:17/12:18/14:29 三份）仍含噪声**，
  重取干净备份待属主拍板。详见 `docs/runbooks/2026-11-02-f6-test-db-isolation.md`。

- 权重：`model/` 离线冷备，恢复后必须 SHA-256 复验（见 `scripts/verify_model_checksums.py`）。
- 密钥：使用外部 Secret 管理（KMS / CI secrets），禁止明文入库。

### 3.4 演练台账（目标库级，2026-10-31 首录）

> 口径：承接 §3.2 季度演练节奏；每条 = 日期 / 范围 / 命令 / 结果 / 耗时 / 未覆盖项；详细记录与逐条证据见
> `docs/runbooks/2026-10-31-restore-drill.md`。台账首录即 2026-10-29 评估门禁 D6/O4/R4 的补证动作。

| 日期 | 范围 | 命令（入口） | 结果 | 耗时 | 未覆盖项 |
|---|---|---|---|---|---|
| 2026-10-31～11-01（跨午夜窗口） | 目标库级备份恢复实测：生产 `data/history.db` 只读取证 + 实况快照恢复 + 既有 2.1GB 备份物恢复；全程仓库外临时目录，生产库与既有备份物零写入 | `scripts/backup_history_db.py --db-path <生产库> --backup-dir <临时目录> --keep 0`（备份链）；`--restore <快照> --target <临时目录>/restored/history.db`；同法恢复 `data/backups/history_bak_20260905.db`（2,175,934,464 字节） | 生产库 `integrity_check=ok`（9 表 3,635 行）；实况快照恢复 **9/9 表对平**（行数/字段名/按主键 5 行抽样 sha256 全一致，0.645s）；2.1GB 备份物恢复 **8/8 表对平**（10.542s，含恢复后完整性校验，RTO≈10.5s @2.1GB） | 见左 | 线上实例未做任何操作；未触发 purge/checkpoint |
| 同上 | 发布回滚演练（离线，仓库外临时克隆 + tag 内容还原） | `scripts/rollback_release.py --list` / `--target v2.8.0 --dry-run` / `--target v2.8.0`（克隆内真跑）；`git archive v2.8.0` 内容还原；离线断言 `pytest tests/test_version_consistency.py tests/test_readyz.py` | 演练完成并记录，**结论=发现缺陷**：①脚本反向 revert 在首个 merge 提交（`8a0851da`）退 128（缺 `-m`；`-m 1` 单点验证可行）；②v2.8.0 tag 树版本位自相矛盾（RP 自动位 2.8.0 vs 手工位 2.7.0，OutFile/内嵌 changelog 未跟上），版本位门禁 3 failed/9 passed；③`readyz` 离线健康断言 3/3 通过（**未对线上实例执行**） | 验证套 12.5s | 镜像/k8s 回滚面未演练（无集群，归 O3/阶段 2）；健康探针未打线上实例 |
| 2026-11-01 00:5x–01:2x | O3 单点与切换演练（**降格执行**：派单方补充围栏覆盖工单前提——TTS 服务未运行、空闲显存仅 1721 MiB（ComfyUI 占 10224 MiB，无关负载未触碰），注入 A/B 按围栏禁止执行） | 只读取证：`netstat`（7869 无监听 / 8188=ComfyUI PID 28656）、`tasklist`、`nvidia-smi`（total/used/free/util）；纸面算账：`config.yaml models.engines` 各引擎 vram 声明 vs 卡容量 11.94 GiB | **注入 A/B 未执行（前置条件不满足，如实登记未覆盖，无伪造数字）**；单卡多副本冗余算账判定**不成立**（2×voxcpm2=13.0 GiB 超卡、indextts20 之外全超；`deployment.yaml` 每副本请求 `nvidia.com/gpu: 1` 单卡不可调度两副本；同卡复制仍是同一点）；本机裸跑无进程级守护（k8s liveness 属编排层）；RTO：进程级/降级切换未实测，DB 沿用 ≈10.5s | —（无注入即无计时） | 进程级自愈与降级切换实测、硬件级冗余——需所有者指定停 ComfyUI 的窗口或换有空卡的机器（详见 `docs/runbooks/2026-11-01-o3-failover-drill.md` §7 决策三问） |
| 2026-11-01 09:50–10:24 | **O3 真机实测（阶段 2b）**：显存已腾空（ComfyUI 由所有者指令结束，零触碰）；注入 A/B 真做 | 启动：`clean_launch.py` 脱壳启动（`BROWSER` 环境变量抑制弹窗）；A：`taskkill` 服务子进程 + 60s 观察窗；B：`TTS_CONFIG_PATH` 仓外副本（voxcpm2 model_dir 置缺）+ `/api/model/switch`；真实合成 ×3（voxcpm_design，5 字短文本） | **进程级自愈：不存在（实测 T+60s 无恢复，launcher 同死）**；人工恢复 **9s(liveness)/3m12s(含重载 61s)**；**自动降级：无**（生成硬失败 engine_not_ready）；半自动切换 edge-tts **2.1s 成功**但降级输出被生成守卫拦死（×2 止损，**输出不可用**）；撤除后回主引擎合成成功、终态零残留 | 合成 41.0s/69.1s/98.4s；加载 68.3/61.0/64.6s（**CPU 口径**。**GPU 口径已实测（阶段 6，2026-11-01）**：加载 43.2s / 显存 +6609 MiB≈6.45 GiB；合成 8.39s（130,278 B / 1.36s @48kHz）；卸载回落 9648→3280 used；重载 30.3s→再合成 4.18s。环境 torch 2.15.0+cu134） | GPU 推理不可达（**G1**：venv torch=2.15.0+cpu，系统 python 缺 funasr——两环境各缺一半）；OOM 自愈未覆盖；需所有者决策 GPU 环境/守护/降级路由（演练文档 §2b.7） |
| 2026-11-02 11:10–11:18 | **R4 真仓完整历史重跑（阶段 8）**：file 协议全量克隆真仓（756 提交/22 tag/85 merge，非浅克隆）于仓库外演练目录，生产 venv 解释器只读调用，生产 checkout 全程零改动（起测复核 porcelain=0） | `scripts/rollback_release.py --target v2.8.0`（克隆内真跑，与 2026-10-31 退 128 那次完全同形） | **退出码 0**（11:11:25→11:11:30，主执行体 5s）：43/43 revert 全部成功（主线 10 个 merge 全走 `-m 1`）；回滚后树与 `v2.8.0` 树逐字节一致（tree hash `db91f16d`，`diff --stat` 0 文件 0 增删）；`8a0851da` 为第 24/43 个目标、`-m 1` revert 成功（`ff86f67`，diffstat 互逆 +199/−634↔+634/−199）；脚本活体 sha256 三方对平（`6dbc567e…`/5050 B）；**R4 自判 `[?]→[x]`** | 5s（主执行体；含克隆与对平全程 11:10:25–11:18:16） | 生产真实回滚、镜像/kubectl 回滚面、回滚后服务层验证、§2.4 硬停断言脚本化、`pytest-timeout`（venv 缺插件且禁装）——详见 `docs/runbooks/2026-11-02-r4-real-history-rollback-drill.md` §7/§10 |
| 2026-11-02 12:17–12:26 | **O4 备份链路可修半边（阶段 9）**：F3 退役 `backup_db.py` + 文档推荐入口改指活链 + 调度登记产物（仅 dry-run，未注册）+ 活体 `data/history.db` 真跑备份与恢复对平；恢复目标仅仓外临时目录，既有备份物零改动、仅新增 | `.venv/Scripts/python.exe scripts/backup_history_db.py --keep 14`（冷 shell 12:18:44 原样复跑同 exit 0）；`--restore <新备份> --target <仓外临时>/restored_history.db`；`scripts/install_backup_schedule.py --dry-run` | 备份 exit 0（12:17:03→12:17:04），产物 `history_backup_20261102_121704.db` **2,408,448 B** sha256 `874d8447…164f41`（与冷 shell 第二份逐字节同哈希）；恢复 exit 0 含 `integrity_check=ok`；**9/9 表对平 PASS**（12:18:32 报告：字段名/行数/主键前 5 行抽样 sha256 全一致，比对期间活体 mtime 11:29:43 未变）；config.yaml git 覆盖实证（blob `d45ed956…`==HEAD）；dry-run 输出七要素契约、重复运行逐字节一致、`--apply` 未调用 | 备份 1s（12:17:03→12:17:04）；恢复 1s（12:17:50→12:17:51）；对平重放同秒 0s（12:25:43→12:25:43，首跑报告落盘 12:18:32） | 真实调度注册未做（待所有者授权）；异地副本未做（需属主给第二存储位置）；5 个运行密钥与 `generation_versioning` 库不在备份集；**新发现 F6：测试套件将 `test_operation` 等噪声行写入生产 `data/history.db.action_logs`（终跑两阶段各 +20 行，共 2,666→2,706，行时间戳 12:20:38.983–12:36:44.837）**——详见 `docs/runbooks/2026-11-02-o4-backup-wiring.md` |
| 2026-11-02 14:26–14:50 | **F6 止血+可逆隔离（阶段 10）**：噪声普查、conftest 全局隔离+哨兵、两口径全量零增长复跑、可证桶移表与**实测回滚** | 处置前基线备份 `.venv/Scripts/python.exe scripts/backup_history_db.py --keep 0`（14:29:20，2,408,448 B，sha `a845230c…`，仓外 restore 回读对平 2,706 行 exit 0）；口径 A `pytest tests/ -v --tb=short --ignore=tests/e2e` 两跑；口径 B 加 `-m "not integration and not gpu and not cuda and not vram and not benchmark and not stress" --cov … --cov-fail-under=45`（html/xml 重定向仓外）；`quarantine_f6_action_logs.py isolate/rollback/verify`（`_演练/F6-isolation/scripts/`） | 口径 A **2622/0/41** exit 0 ×2（14:38:23→14:40:22、14:40:44→14:42:31）：生产 `action_logs` 2,706→2,706、`MAX(ts_ms)` 不变、`history.db`/`-wal`/`-shm` 与 `generation_versions.db` sha256 **逐字节不变**；口径 B **2530/0/10（123 deselected）** 覆盖率 52.33%≥45% exit 0；隔离对账 `2,167==347+344+1,476` 且 `2,167+539==2,706`（14:45:14，integrity ok）；回滚实测（14:45:29）主表回 2,706、行内容指纹 FP1==FP2；再隔离 14:46:35 VERIFY PASS；读路径 `/api/system/logs` 分页/COUNT/越界页、`logs-compat`（内存源）隔离态全过 | 备份 1s（14:29:20）；口径 A 114.84s/103.11s；口径 B 203.96s；移表/回滚/复隔离各 ≤1s | 451+88 行疑似/无法判定原地保留待属主判定；隔离前备份物（12:17/12:18/14:29 三份）仍含噪声，重取与否待属主；调度注册与异地沿阶段 9——详见 `docs/runbooks/2026-11-02-f6-test-db-isolation.md` |

门禁状态（2026-10-29 评估 → 三轮整改，判定依据详见各演练报告）：**D6 [?]→[x]**（目标库级实测完成且对平通过）；**O4 保持 [?]**（⛔P0；四阻塞中阶段 9 已拔掉两个：②F3 死路径退役、§3.3 推荐入口改指 `backup_history_db.py` 并经冷 shell 原样验证，①调度接线交付 `install_backup_schedule.py` 可验证产物（默认 dry-run、幂等、11 测试），但**真实注册待所有者授权、未注册**；仍阻断的另两个是硬前提：③同机无异地——需属主给第二存储位置，非本轮可伪装关闭；④周期性——待 2027-01 到期轮次佐证；另 F6 新登记：测试噪声直写生产库——**已于阶段 10（2026-11-02）止血**：conftest 全局隔离+`sqlite3.connect` 哨兵令测试套对生产库零增长（两口径实测），可证噪声 2,167 行已移入 `action_logs_quarantine_20261102` 侧表可回滚，见 `docs/runbooks/2026-11-02-f6-test-db-isolation.md`。见 `docs/runbooks/2026-11-02-o4-backup-wiring.md`）；**R4 [?]→[x]**（2026-11-02 阶段 8：真仓 756 条完整历史克隆上 `--target v2.8.0` 真跑 **exit 0**、43/43 revert、回滚后树与 v2.8.0 树逐字节一致；两判据①可复核②活体全过，F1 修复就此真仓闭环——见 `docs/runbooks/2026-11-02-r4-real-history-rollback-drill.md`；F2 目标 tag 版本位漂移为独立挂账项（属主决策），不影响 R4 判据）；**O3 保持 [?]**（「降级切换」子项经阶段 4 修复+真机实测**升 [x]**——自动降级 3.07s、产物 21,888 B MP3、degraded 字段齐备、fallback_failed 可区分；但「无核心单点」仍受单卡算账约束，**整体不升 [x]**）；D-2 已闭环（见 `docs/TODO.md`）、D-1 已按所有者决策接受成文（§2.5）；**E7 [ ]→[?]、P25 [ ]→[?]**（Bus Factor=1 登记成文：`docs/runbooks/BUS-FACTOR-2026-11-01.md`——现状量化/知识单点载体/补偿措施/可判定升级判据齐备，按「登记成文+补偿在位+判据可判定→[?] 带项上线」口径记账；Bus Factor>1 的事实未变，**不升 [x]**，升级为阻断的条件见该文 §5）。

---

## 4. 事故复盘（Postmortem）

模板：

- 时间线（检测 → 响应 → 缓解 → 恢复）
- 影响面（受影响的 SLO / 用户数）
- 根因（5 Whys）
- 改进项（带 owner + 截止日期），优先纳入下一迭代
- 是否触及错误预算；若触及，说明预防措施

---

## 5. 运维入口速查

| 用途 | 命令 / 端点 |
|------|-------------|
| 存活探针 | `GET /api/health/ping`（同构：`GET /api/system/health/ping`） |
| 就绪探针 | `GET /api/health/ready` 或 `GET /readyz`（同构：`GET /api/system/health/ready`） |
| 生成统计 | `GET /api/system/stats` |
| Prometheus 指标 | `GET /api/system/metrics` |
| SLO/SLI 报告（错误预算） | `GET /api/system/slo`（observability，报告 C4 补录） |
| 容量采样时序（GPU/CPU/内存） | `GET /api/system/capacity`（observability，报告 C4 补录） |
| 审计日志（近 N 条） | `GET /api/system/audit?limit=100`（受 `/api/*` 鉴权保护） |
| 优雅关闭 | `POST /api/system/shutdown` |
| 权重哈希清单生成 | `python scripts/generate_model_checksums.py --out model_checksums.json` |

## 治理与勘误速查（2026-09-18）

- **钩子**：`.githooks/pre-commit` 分发器四级回退（.venv → PATH python → 控制台 pre-commit → pre-commit-lite）；`precheck.ps1` 同款 python 回退，依赖缺失打印 `[WARN]` 跳过，完整门禁由 CI 承担。
- **分支**：`main` 受保护（5 项必查：`Lint (ruff)` / `Test (pytest) (3.12, ubuntu-latest)` / `Typecheck (mypy ratchet)` / `DCO Check` / `release-gate`，`strict=false`，口径见 tracked 镜像 `docs/ci/branch-protection.json`）→ 非 owner 协作者不可直推，变更走功能分支 + PR；**仓库所有者 admin 直推豁免**（2026-09-28 治理变更，见 `docs/DOD.md` §0b）。pre-push gitleaks 扫描待推送提交（0 泄露才放行）。
- **Python 安全注释**：bandit 豁免用纯 `# nosec`（`# nosec B603,B607` 只压 B607 不压 B603）；subprocess 一律列表参数。
