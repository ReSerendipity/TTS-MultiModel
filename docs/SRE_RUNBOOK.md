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

- [ ] 确认回滚范围（镜像 / 配置 / 权重）与原因
- [ ] 通知相关方（值班 + 业务方）
- [ ] 执行回滚（镜像 tag / kubectl undo / git revert config）
- [ ] 验证 readiness 探针恢复 `ok`
- [ ] 检查审计日志 `data/audit.log` 无异常激增（如 auth_failure 暴涨）
- [ ] 复盘并记录到变更记录

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
- **落地脚本**：`scripts/backup_db.py` 已提供（对应评估 P3-1「无 DB/历史数据备份」）。
  它使用 SQLite 在线备份 API 做一致性快照（不阻塞在线写入），并一并备份审计日志与
  `config.yaml`，本地按时间命名轮转（默认保留 14 份），支持 `--restore` 一键恢复
  （恢复前自动备份当前版本防误操作）。

  ```bash
  # 每日全量备份（建议由 cron / k8s CronJob 调用）
  python scripts/backup_db.py --retain 14
  # 从指定备份恢复历史库
  python scripts/backup_db.py --restore backups/history-20260831-030000.db
  ```

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
| 2026-11-01 09:50–10:24 | **O3 真机实测（阶段 2b）**：显存已腾空（ComfyUI 由所有者指令结束，零触碰）；注入 A/B 真做 | 启动：`clean_launch.py` 脱壳启动（`BROWSER` 环境变量抑制弹窗）；A：`taskkill` 服务子进程 + 60s 观察窗；B：`TTS_CONFIG_PATH` 仓外副本（voxcpm2 model_dir 置缺）+ `/api/model/switch`；真实合成 ×3（voxcpm_design，5 字短文本） | **进程级自愈：不存在（实测 T+60s 无恢复，launcher 同死）**；人工恢复 **9s(liveness)/3m12s(含重载 61s)**；**自动降级：无**（生成硬失败 engine_not_ready）；半自动切换 edge-tts **2.1s 成功**但降级输出被生成守卫拦死（×2 止损，**输出不可用**）；撤除后回主引擎合成成功、终态零残留 | 合成 41.0s/69.1s/98.4s；加载 68.3/61.0/64.6s | GPU 推理不可达（**G1**：venv torch=2.15.0+cpu，系统 python 缺 funasr——两环境各缺一半）；OOM 自愈未覆盖；需所有者决策 GPU 环境/守护/降级路由（演练文档 §2b.7） |

门禁状态（2026-10-29 评估 → 三轮整改，判定依据详见各演练报告）：**D6 [?]→[x]**（目标库级实测完成且对平通过）；**O4 保持 [?]**（首轮周期点已建，待 2027-01 到期轮次佐证周期性）；**R4 保持 [?]**（演练记录已建，但回滚链存在待修缺陷 F1/F2，修复重跑通过前不升）；**O3 保持 [?]**（进程级自愈实测不存在 D-1；降级切换仅半自动且输出链断裂 D-2；无核心单点经算账+实测判定单卡上不可消除，需跨机/第二张卡——**不升 [x]**）。

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
