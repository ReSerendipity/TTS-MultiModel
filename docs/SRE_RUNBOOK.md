# SRE 运行手册（SLA / SLO / 回滚 / 灾难恢复演练）

> 配套评估：`local/docs-archive/_devarchive/SRE评估_v2.2.0.md`、`local/docs-archive/_devarchive/容器化成熟度评估_v2.2.1.md`（本地归档目录，未随仓库发布）
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
  # 解释器入口统一走 scripts/run_python.py（仓库唯一解析入口，2026-11-03 F7）：
  #   实际执行探测顺序 = venv（.venv/Scripts | .venv/bin）→ PATH python → python3 → py -3 → 当前进程；
  #   自检：python scripts/run_python.py --print | --explain | --list
  # 每日全量备份（与本机调度登记产物 scripts/install_backup_schedule.py 的注册命令行同源，
  # 该产物默认 --dry-run，真实注册待所有者授权后进行）
  python scripts/run_python.py scripts/backup_history_db.py --keep 14
  # 从指定备份恢复历史库（--target 会覆盖目标路径，执行前确认）
  python scripts/run_python.py scripts/backup_history_db.py --restore data/backups/history_backup_<时间戳>.db --target data/history.db
  ```

- **为什么不再写 `.venv/Scripts/python.exe`（F7，2026-11-03）**：项目 `.venv` 是指向 `D:\_venvs\tts` 的
  目录联接（junction），该目标目录自 2026-11-03 起不存在，`test -x .venv/Scripts/python.exe` 实测 **NO**——
  写死它的命令一律「找不到解释器」。上面两条命令改经 `scripts/run_python.py` 解析后，2026-11-03
  **10:35:25** 冷 shell（新 `cmd.exe`，`VIRTUAL_ENV`/`CONDA_PREFIX`/`PYTHONPATH`/`TTS_PYTHON` 全空）原样执行
  **exit 0**，产出 `data/backups/history_backup_20261103_103525.db` **2,469,888 B**、sha256 `02c9d487…`；
  `scripts/check_engine_compat.py` 经同一入口与直接跑系统 Python 输出逐字节一致（sha256 同值，均 exit 1 =
  `funasr` 缺失 8/9，与本条无关）。**边界**：本条只解决「入口可执行」，**不重建 venv、不装包**——venv 现状与
  GPU 口径仍待所有者决策；证据与未覆盖清单见 `docs/runbooks/2026-11-03-f7-venv-entrypoint-and-f8-hooks-drift.md`。

  > **当前受支持的解释器 = 系统 Python `C:\Python312\python.exe`（3.12.10，已装 `funasr==1.4.16`）**
  > （2026-11-03 阶段 14 实测）。项目 `.venv` **仍是悬空联接**（指向已不存在的 `D:\_venvs\tts`），
  > 未重建、未改符号链接——**重建属待决项（G4）**，不写成本轮的承诺。补装后 `scripts/check_engine_compat.py`
  > 由 8/9（exit 1）转为 **9/9（exit 0）**，`sh .githooks/pre-commit` 由 exit 1 转为 **exit 0**；
  > 详见 `docs/runbooks/2026-11-03-f8-funasr-install.md`。

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
| 2026-11-02 12:17–12:26 | **O4 备份链路可修半边（阶段 9）**：F3 退役 `backup_db.py` + 文档推荐入口改指活链 + 调度登记产物（仅 dry-run，未注册）+ 活体 `data/history.db` 真跑备份与恢复对平；恢复目标仅仓外临时目录，既有备份物零改动、仅新增 | `.venv/Scripts/python.exe scripts/backup_history_db.py --keep 14`（**当时环境有效；该入口已于 2026-11-03 失效，见 F7**；冷 shell 12:18:44 原样复跑同 exit 0）；`--restore <新备份> --target <仓外临时>/restored_history.db`；`scripts/install_backup_schedule.py --dry-run` | 备份 exit 0（12:17:03→12:17:04），产物 `history_backup_20261102_121704.db` **2,408,448 B** sha256 `874d8447…164f41`（与冷 shell 第二份逐字节同哈希）；恢复 exit 0 含 `integrity_check=ok`；**9/9 表对平 PASS**（12:18:32 报告：字段名/行数/主键前 5 行抽样 sha256 全一致，比对期间活体 mtime 11:29:43 未变）；config.yaml git 覆盖实证（blob `d45ed956…`==HEAD）；dry-run 输出七要素契约、重复运行逐字节一致、`--apply` 未调用 | 备份 1s（12:17:03→12:17:04）；恢复 1s（12:17:50→12:17:51）；对平重放同秒 0s（12:25:43→12:25:43，首跑报告落盘 12:18:32） | 真实调度注册未做（待所有者授权）；异地副本未做（需属主给第二存储位置）；5 个运行密钥与 `generation_versioning` 库不在备份集；**新发现 F6：测试套件将 `test_operation` 等噪声行写入生产 `data/history.db.action_logs`（终跑两阶段各 +20 行，共 2,666→2,706，行时间戳 12:20:38.983–12:36:44.837）**——详见 `docs/runbooks/2026-11-02-o4-backup-wiring.md` |
| 2026-11-02 14:26–14:50 | **F6 止血+可逆隔离（阶段 10）**：噪声普查、conftest 全局隔离+哨兵、两口径全量零增长复跑、可证桶移表与**实测回滚** | 处置前基线备份 `.venv/Scripts/python.exe scripts/backup_history_db.py --keep 0`（**当时环境有效；该入口已于 2026-11-03 失效，见 F7**；14:29:20，2,408,448 B，sha `a845230c…`，仓外 restore 回读对平 2,706 行 exit 0）；口径 A `pytest tests/ -v --tb=short --ignore=tests/e2e` 两跑；口径 B 加 `-m "not integration and not gpu and not cuda and not vram and not benchmark and not stress" --cov … --cov-fail-under=45`（html/xml 重定向仓外）；`quarantine_f6_action_logs.py isolate/rollback/verify`（`_演练/F6-isolation/scripts/`） | 口径 A **2622/0/41** exit 0 ×2（14:38:23→14:40:22、14:40:44→14:42:31）：生产 `action_logs` 2,706→2,706、`MAX(ts_ms)` 不变、`history.db`/`-wal`/`-shm` 与 `generation_versions.db` sha256 **逐字节不变**；口径 B **2530/0/10（123 deselected）** 覆盖率 52.33%≥45% exit 0；隔离对账 `2,167==347+344+1,476` 且 `2,167+539==2,706`（14:45:14，integrity ok）；回滚实测（14:45:29）主表回 2,706、行内容指纹 FP1==FP2；再隔离 14:46:35 VERIFY PASS；读路径 `/api/system/logs` 分页/COUNT/越界页、`logs-compat`（内存源）隔离态全过 | 备份 1s（14:29:20）；口径 A 114.84s/103.11s；口径 B 203.96s；移表/回滚/复隔离各 ≤1s | 451+88 行疑似/无法判定原地保留待属主判定；隔离前备份物（12:17/12:18/14:29 三份）仍含噪声，重取与否待属主；调度注册与异地沿阶段 9——详见 `docs/runbooks/2026-11-02-f6-test-db-isolation.md` |
| 2026-11-02 23:25–11-03 00:34 | **F6 B 批闸门 fastfail（阶段 11）**：8192/streaming 两判据代码取证（复现实验闭合）、B 批**零隔离**、22 行 0.0s 失败证伪+代码路径诊断；生产库全程零写入 | 只读取证 `db_census_b/db_bucket_profile/db_burst_profile/db_join_repro/inspect_iso_dbs.py`（`_演练/F6-batch-B/scripts/`）；两复现实验单跑 `test_generation_error_friendliness.py::…::test_within_engine_cap_passes_length_check` 与 `test_edge_tts_fallback.py::test_main_engine_loaded_does_not_fallback`（conftest 隔离库承接）；两口径 A×2/B×1 同阶段 10 命令（系统 Python 3.12.10 代跑，见未覆盖） | 普查对平 539=250(206+44)+201+88、z22=22；**8192 钉上**（test:160+写链+隔离库复现行与生产 **206/206** 签名闭合+桩错误指纹）；**streaming 钉不上**（路由写死 streaming.py 六处；44=39 可证+3 真实 338/629/630+2 钉不到 325/337）→ **B 批 `F6-20261102-B` 零行隔离，备份/隔离/回滚四步按工单「立即停手」取消**；z22 证伪=**真实使用**（7 类成因，守卫 7326b94/4a45e6f 晚于行时间；clone.py:260 空引擎缺口现状仍敞开）；口径 A **2545/0/117** ×2（00:27:26→00:29:17、00:29:34→00:31:13，107.72s/95.11s）、口径 B **2448/0/91（123 deselected）** cov **51.63%**≥45%（00:31:18→00:33:44，141.49s）全 exit 0；五快照 `history.db` 2,469,888 B / sha256 `9652e5e7…`（==阶段 10 收口值）、`-wal` 0 B、`-shm` 32,768 B、`action_logs` 539、`MAX(ts_ms)` 逐项**逐字节不变**（哨兵无漏，conftest 未动） | 普查/取证/复现各秒级；口径 A 107.72s/95.11s；口径 B 141.49s | B 批零隔离（判据不成立）；206 行 8192 桶已可证待属主授权下轮单列隔离；44 行 streaming 全部原地保留；22 行 z22=真实历史永久保留（是否加校验属产品决策）；残留精算 539=206+41+3+22+**179 generation+88 model 未检**；备份未重取（沿阶段 10）；`.venv` 符号链接目标 `D:\_venvs\tts` 失效未修（硬约束 6），本轮系统 Python 代跑——详见 `docs/runbooks/2026-11-02-f6-batch-b-and-fastfail.md` |
| 2026-11-03 07:52–08:06 | **F6 C 批隔离（阶段 12）**：阶段 11 钉上的 `text_length==8192` 判据 **206 行**移入隔离表批次 `F6-20261103-C`；备份→隔离→两等式→回滚实测+指纹→re-isolate→两口径零增长全链闭环 | 处置前备份 `C:\Python312\python.exe scripts/backup_history_db.py --db-path data/history.db --backup-dir 仓外F6-batch-C/backups --keep 0`（07:56:07，2,469,888 B，sha `e4394184…`，仓外 restore 回读对平 539+2,167=2,706、`MAX(ts_ms)` 1793594184555 exit 0）；隔离执行器 `quarantine_f6_action_logs_c.py isolate/rollback/verify`（阶段 10 原件派生，仓外 `_演练/F6-batch-C/scripts/`，机制同源：单事务 `BEGIN IMMEDIATE`、全列 `INSERT…SELECT`、同事务按隔离表 id 删主表）；两口径 A×2/B×1 同阶段 11 命令（系统 Python 3.12.10 代跑） | 谓词命中 **206**（206/206 `failed`、0 success、与 streaming/model/0.0s 禁触集合零交集、8193=**0 行**无待决）；等式① `206==206`、② `333+2,167+206==2,706` PASS（07:57:26 isolate、integrity ok）；回滚实测（07:57:44 + 干净复跑 08:00:21 ROLLBACK PASS exit 0）主表回处置前 **539**、`MAX(ts_ms)` 回 **1793594184555**、C 批清零、A 批 **2,167**；六列有序指纹 C 批四态同值 `22fbff87…`、A 批处置前（备份件）=回滚态=终态 `b955196a…` 零触碰；re-isolate 终态 **333/2,167/206** verify PASS（08:00:31）；口径 A **2545/0/117** ×2（08:01:06→08:02:32 82.67s、08:02:38→08:03:55 74.43s）、口径 B **2448/0/91（123 deselected）** cov **51.58%**≥45%（08:04:02→08:05:52 106.24s）全 exit 0；测后 `history.db` 2,469,888 B / sha `051d1688…`、`-wal` 0 B、`-shm` 32,768 B（+`generation_versions.db`）与隔离终态**逐字节一致**（08:06:19，零增长在 C 批终态成立） | 备份 1s（07:56:07）；isolate/rollback/verify 各 ≤1s；口径 A 82.67s/74.43s、口径 B 106.24s | 残留主表 333 行全为待决项（41 streaming+3 真实 streaming+22 真实+179 generation+88 model）；可证噪声累计 2,167+206=2,373 行隔离在案可回滚；调度注册/异地副本/旧备份物重取沿阶段 9/10；`.venv` 符号链接目标 `D:\_venvs\tts` 失效未修（硬约束 6），本轮系统 Python 代跑——详见 `docs/runbooks/2026-11-02-f6-batch-b-and-fastfail.md` §9 |
| 2026-11-03 10:29–10:56 | **F7 文档解释器入口修复 + F8 钩子漂移登记（阶段 13）**：`.venv` 联接失效取证（目标 `D:\_venvs\tts` 不存在）、23 个可执行入口站点分类处置、仓库唯一解析入口 `scripts/run_python.py` 落地、调度命令行改指解析结果 | 冷 shell 原样：`python scripts/run_python.py scripts/backup_history_db.py --keep 14`；对照 `python scripts/run_python.py scripts/check_engine_compat.py` vs `python scripts/check_engine_compat.py`；`sh .githooks/pre-commit`；两口径 A×2/B×1 | 冷 shell **exit 0**（10:35:25，产物 `history_backup_20261103_103525.db` **2,469,888 B** sha256 `02c9d487…`；首跑 10:33:29 产物同哈希，逐字节一致；既有备份删除 0 份）；对照两口径输出 sha256 同值 `49bd9fce…`、均 exit 1（funasr 缺失 8/9）；`sh .githooks/pre-commit` **exit 1**（红在 Engine Compatibility Check）；两口径 A **2564/0/118** ×2、B **2467/0/92（123 deselected）** cov **51.58%**≥45%，全 exit 0；活体 `history.db` 2,469,888 B / sha `051d1688…`、`action_logs` 333、`MAX(ts_ms)` 1793594173715 测前测后**逐字节不变** | 冷 shell 444 ms；对照 6210 ms / 6604 ms；A 120.73s / 119.13s；B 159.60s | venv 未重建（硬约束 1）、钩子未接线（F8 只登记，接线前须解决 funasr）、funasr 未装、计划任务未注册、异地未解决；发版链脚本与 `.githooks` 内部两级回退未收敛——详见 `docs/runbooks/2026-11-03-f7-venv-entrypoint-and-f8-hooks-drift.md` |
| 2026-11-03 11:27–11:39 | **F8 前置条件解除（阶段 14）**：系统 Python 3.12.10 补装 `funasr==1.4.16`（属主授权选项 A：只补 funasr、不重建 venv）；装前 dry-run 硬闸 + torch 四重指纹 + GPU 探测 + 两口径全量 + 活体零增长 | `pip install --dry-run "funasr==1.4.16"` → `pip install "funasr==1.4.16"`；`python scripts/run_python.py scripts/check_engine_compat.py` vs 直跑；`sh .githooks/pre-commit`；两口径 A×3/B×1 同阶段 13 命令 | dry-run **exit 0**：`Would install` **16 个包**，torch/torchaudio/torchvision/numpy/transformers **均不在改动列表**（numpy 2.4.6、transformers 5.16.1 = already satisfied）→ 闸门通过；安装 **exit 0**，实装 16 个，`pip list` 304→**320** 行闭合；**torch 未被改动**（`2.11.0+cu128` / cuda `12.8` 逐字不变、目录 4,415,065,323 B / 13,795 文件同值、6 关键文件 sha256 全同含 `torch_cuda.dll`、`RECORD` sha256 `69c1d26c…` 同值）；GPU 探测 exit 0（fp32 2048² matmul **0.209s**、fp16 conv2d **0.109s**，装前 0.277/0.226）；engine-compat **9/9 exit 0**，经入口与直跑输出 sha256 同值 `c2e5b041…`；`sh .githooks/pre-commit` **exit 0**（11:30:48→11:31:08，20s；装前 exit 1）；加载路径正反双向证明（有 funasr 通过 / 屏蔽后 `ModuleNotFoundError`）→ 导入层不再抛错；两口径 A **2566/0/116**（经入口 104.21s、直跑 98.20s、文档改完终跑 95.92s）、B **2469/0/90（123 deselected）** cov **51.59%**≥45%（191.23s）全 exit 0——相对阶段 13 **+2/−2**，归因 funasr 依赖树带入 `jieba==0.42.1`（`test_fts_tokenizer.py` 2 个门控用例转真跑，7 passed/0 skipped）；活体 `history.db` 2,469,888 B / sha `051d1688…`、`-wal` 0 B、`-shm` 32,768 B、333 行、`MAX(ts_ms)` 1793594173715、`generation_versions.db` sha `41ab2af7…` **测前测后逐字节不变**；`data/backups/` 12 文件零增删 | dry-run 与安装各秒级；pre-commit 20s；A 104.21s/98.20s；B 191.23s | **真加载未做**：`nvidia-smi` free **1,719 MiB** < 需 **6,609 MiB**（ComfyUI PID 31,096 占 10,226 MiB，零触碰）；venv 未重建（悬空联接，G4 待决）；**钩子未接线**（`core.hooksPath` 仍 `local/hooks`，属主授权动作）；计划任务未注册、异地未解决、ASR/降噪权重未下载——详见 `docs/runbooks/2026-11-03-f8-funasr-install.md` |

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
- **钩子接线漂移（F8，2026-11-03 登记，未修）**：`git config core.hooksPath` 实测 = **`local/hooks`**（来源 `file:.git/config`）——该目录被 `.gitignore:388` 的 `/local/` 忽略，内只有 `pre-push` 与 `.origin`（内容 `.githooks`）。因此 `pre-push` 经转发仍生效，而 `.githooks/` 下的 `pre-commit`、`commit-msg`（DCO）、`post-merge`、`post-checkout`、`prepare-commit-msg` **全部休眠**，与 `scripts/install-hooks.ps1:5-6` 的成文口径（2026-09-16 起统一 `core.hooksPath .githooks`）冲突。影响：2026-10-31 00:00:19 之后产生的 **25 个提交**在本地 pre-commit / DCO 校验缺席下产生（25/25 仍带 `Signed-off-by`：事实未违反，**校验缺席**）。**接线前置条件（2026-11-03 阶段 14：已解除）**：手跑 `sh .githooks/pre-commit` 装前 **exit 1**（红在 `Engine Compatibility Check`：`funasr` 缺失，8/9），补装 `funasr==1.4.16` 到系统 Python 后复跑 **exit 0**（9/9，其余实跑门禁 6/6 Passed，11:30:48→11:31:08）——`funasr` 是 VoxCPM2 **加载期**硬依赖（`model_manager_core/load.py:350`；IndexTTS 2.5/2.0、edge-tts、voicebox、breeze-tts2 不受影响）。**但钩子仍未接线**（`core.hooksPath` 本轮未动，硬约束），剩余前置只有属主授权动作。**属主修复命令**：`./.githooks/install.sh` 或 `git config core.hooksPath .githooks`（**勿用**已退役的 `scripts/install-hooks.ps1`）。登记与证据见 `docs/TODO.md` F8、`docs/runbooks/2026-11-03-f7-venv-entrypoint-and-f8-hooks-drift.md` §6 与 `docs/runbooks/2026-11-03-f8-funasr-install.md`（阶段 14）。
- **分支**：`main` 受保护（5 项必查：`Lint (ruff)` / `Test (pytest) (3.12, ubuntu-latest)` / `Typecheck (mypy ratchet)` / `DCO Check` / `release-gate`，`strict=false`，口径见 tracked 镜像 `docs/ci/branch-protection.json`）→ 非 owner 协作者不可直推，变更走功能分支 + PR；**仓库所有者 admin 直推豁免**（2026-09-28 治理变更，见 `docs/DOD.md` §0b）。pre-push gitleaks 扫描待推送提交（0 泄露才放行）。
- **Python 安全注释**：bandit 豁免用纯 `# nosec`（`# nosec B603,B607` 只压 B607 不压 B603）；subprocess 一律列表参数。
