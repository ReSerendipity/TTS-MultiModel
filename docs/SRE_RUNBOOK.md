# SRE 运行手册（SLA / SLO / 回滚 / 灾难恢复演练）

> 配套评估：`SRE评估_v2.2.0.md`、`容器化成熟度评估_v2.2.1.md`
> 适用版本：v2.2.1（与 `config.yaml` / `pyproject.toml` 一致）

---

## 1. SLA / SLO

### 1.1 服务等级目标（SLO）

| 指标 | 目标 | 度量方式 | 数据源 |
|------|------|----------|--------|
| 可用性（月度） | ≥ 99.5% | liveness `/api/health/ping` 探活成功率 | k8s liveness + `routes/system/health.py` |
| readiness（模型就绪） | ≥ 99.0% | 启动后 `/api/health/ready` / `/readyz` 返回 `ok` | 同上 |
| P95 生成时延（短文本 ≤200 字） | ≤ 8s（GPU） | `_execute_generation` 计时 | `GenerationTracker` / `history_db` |
| 生成成功率 | ≥ 99.0% | 成功/失败计数 | `routes/system/health/stats`（Prometheus `tts_generations_total`） |
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
> 中对关键/高危漏洞用 Trivy 阻断；发布前需在 `CHANGELOG.md` 登记版本与变更。

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

- `history_db`（SQLite）：每日全量备份 + WAL 一致性快照；PII 字段按 `security.pii_retention_days`
  留存，到期由启动期清理任务删除（`history_db.purge_expired`）。
- 权重：`model/` 离线冷备，恢复后必须 SHA-256 复验。
- 密钥：使用外部 Secret 管理（KMS / CI secrets），禁止明文入库。

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
| 存活探针 | `GET /api/health/ping` |
| 就绪探针 | `GET /api/health/ready` 或 `GET /readyz` |
| 生成统计 | `GET /api/system/stats` |
| Prometheus 指标 | `GET /api/system/metrics` |
| 审计日志（近 N 条） | `GET /api/system/audit?limit=100`（受 `/api/*` 鉴权保护） |
| 优雅关闭 | `POST /api/system/shutdown` |
| 权重哈希清单生成 | `python scripts/generate_model_checksums.py --out model_checksums.json` |
