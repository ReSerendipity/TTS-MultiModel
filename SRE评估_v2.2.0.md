# TTS_MultiModel 运维稳定性（SRE）深度完整性评估

> 评估对象：TTS_MultiModel（声明的评估版本 **v2.2.0**）
> 评估日期：2026-08-31
> 评估性质：**客观性 / 完整性审视**（只评估现状，不提供落地方案）
> 评估依据：实际代码与配置（非文档声明），证据均标注文件路径

---

## 0. 前置勘误（评估边界）

| 项 | 声明/文档 | 实测代码 | 影响 |
|---|---|---|---|
| 评估版本 | 用户称 v2.2.0 | `config.yaml:1` → `version: "2.2.1"` | 评估以仓库代码为准，覆盖 v2.2.x 全量 |
| `perf/perf_monitor.py` | 用户列为关键路径 | **不存在**（仓库无此文件） | 改为评估 `scripts/perf_monitor.py` |
| 性能监控模块 | 视为独立体系 | `scripts/perf_monitor.py` 实为**一次性 ad-hoc 基准脚本**，且 `perf/results/` 仅落本地 JSON | 非持续监控，属诊断工具 |
| 端口一致性 | — | `scripts/perf_monitor.py:21-22` 硬编码 `127.0.0.1:8000`，而实际服务端口为 **7869**（`config.yaml:6`），启动提示亦写 8000 | 该脚本**默认连不上服务**，属监控工具失效（盲区证据之一） |

---

## 1. 子体系评估

### 1.1 监控告警（Metrics / Alerting / Logging）

**指标（Metrics）— 内部 instrumentation 较强，外部可观测性为零**

- 正向：`monitor.py` 的 `HealthMonitor` 单例提供了较完整的进程内指标：
  - GPU 显存泄漏检测（100 样本滑动窗口 + 稳定基线，`monitor.py:161` `check_memory_leak`）
  - 显存熔断（90% 阈值，`monitor.py:257` `check_vram_circuit_breaker`）
  - 模型加载预检（权重 ×1.5 安全系数，`monitor.py:297`）
  - 运行统计：总生成数 / 错误数 / OOM 重试 / 熔断次数 / 成功率 / uptime（`get_metrics()`）
- 暴露面：`/api/system/health`、`/stats`、`/health/gpu-leak`、`/queue`、`/health/ready`（`routes/system/health.py`）
- **关键缺口**：
  - **无 Prometheus / OpenTelemetry / Sentry exporter**（全仓检索 `prometheus|grafana|opentelemetry|sentry` → **0 命中**，`pyproject.toml` 无相关依赖）。指标仅能由人/脚本**主动 HTTP pull**，无推送、无时序存储、无保留。
  - 指标是**进程内存单例**，进程重启即清零（`_health_monitor = HealthMonitor()`，`monitor.py:544`）。无跨重启累积 → 无法做长期趋势 / SLO 计算。
  - GPU 利用率、显存为**按需采样**（仅在调用 `/health` 时 `record_vram_usage`），非周期性采集 → 无法刻画历史曲线。

**告警（Alerting）— 完全缺失**

- 全仓检索 `alert|webhook|slack|pagerduty|alertmanager` → **无任何告警通道**。
- 熔断触发、显存泄漏、成功率下降、OOM 重试**仅 `logger.warning/error` 落日志**（`monitor.py:185,287`）。没有任何机制把"异常"转为"通知到人"。
- 结论：**MTTD 完全依赖用户投诉或人工巡检**，无主动发现能力。

**日志（Logging）— 结构化但无集中聚合**

- 标准库 `logging`，文件轮转 10MB×3（`config.yaml:73-76`），含 request_id（需在日志格式中注入）。
- 操作日志双通道：内存环形缓冲（2000 条）+ `sqlite action_logs` 表（`routes/system/logs.py:124,248`），支持分页/过滤/清理（30 天或 10 万条双阈值）。
- **缺口**：无日志集中采集（无 Loki / ELK / 云日志），无 ERROR 日志告警，无结构化 JSON 日志（纯文本格式串）。跨多实例无法聚合。

### 1.2 SLA / SLO 定义与追踪 — 缺失

- 全仓**无任何 SLO/SLI 定义**：无可用性目标、无延迟 SLO、无错误预算、无 SLA 文档。
- 存在 `success_rate_pct` 指标但**无阈值、无追踪、无预算消耗模型**。
- 测试覆盖率门禁 40%（`ci.yml:152` `--cov-fail-under=40`），但这与运维 SLO 无关。

### 1.3 故障应急响应流程 — 缺失

- **无 Runbook、无事故流程、无 oncall、无 MTTR 记录**（全仓检索 `runbook|应急|incident|oncall|值班` → 仅 1 个 ADR 误命中，无实际文档）。
- `docs/plans/DEPLOYMENT.md` 覆盖反向代理 / systemd / Docker 配置，但**不含"X 故障时应执行什么"**的处置手册。
- 唯一"自愈"：容器 `restart: unless-stopped`（`docker-compose.yml:32`）+ systemd `Restart=always`（`DEPLOYMENT.md:126`）+ 优雅关闭 `POST /shutdown`（`health.py:493`）。属崩溃重启，非故障响应流程。

### 1.4 容量规划与弹性伸缩 — 静态，无弹性

- **硬约束 #4（AGENTS.md）**：所有推理单 Worker 串行（`routes/generate/utils.py` 信号量容量 1）。**无法水平扩展推理**。
- `docker-compose.yml:22-31` 固定 `memory: 16G / cpus: 4.0 / GPU count:1`，无 HPA、无弹性伸缩。
- 容量工具为**离线诊断脚本**：`perf/vram-usage.py`、`perf/cold-start.py`、`perf/generation-benchmark.py`、`perf/stress-test.py`、`perf/report_generator.py`，需人工触发、结果落 `perf/results/*.json`，**无持续容量监控、无容量预警**。
- GPU 为 `reservations.devices count: 1` 强绑定 → 单卡 SPOF。

### 1.5 部署回滚机制 — 无自动化

- CI（`ci.yml`）只做 lint/test/build，**不部署（无 CD）**。
- 发版由 `release-please.yml` 自动 `draft: false` 直接发（`release-please.yml:31`），无 staging→prod 灰度，无蓝绿/金丝雀（全仓检索 `回滚|rollback|blue-green|canary` → 仅代码级 git revert 提及，无机制）。
- 回滚 = **人工 `git revert` + 重新部署**，无一键回滚、无版本化回滚产物（仅 GitHub Release 制品，未提供回滚 SOP）。
- 部署本身为**人肉发布**：`start.sh/start.bat`、`install.sh/install.bat`、systemd（`DEPLOYMENT.md:115`）。

### 1.6 灾难恢复演练 — 无

- 无 DR 文档、无备份脚本（`scripts/` 下有 `verify_model_checksums.py`/`generate_integrity_manifest.py` 做权重完整性校验，但**无 DB/历史数据备份**）。
- 历史库 `data/tts_history.db`（SQLite）无备份/异地副本策略。
- 单容器 + 单 GPU + 单主机（`config.yaml:4` `host: "127.0.0.1"` 默认仅本地监听）→ 无 HA，主机/卡故障即全量中断。
- 无任何 DR 演练记录。

---

## 2. 反模式识别（对照清单）

| # | 反模式 | 是否存在 | 证据 |
|---|---|---|---|
| 1 | 告警风暴（alert fatigue） | **否（反向存在）** | 无告警系统 → 不是"过度告警"而是"零告警"，更危险的盲区 |
| 2 | Runbook 缺失或过期 | **是** | 无 runbook 文档；处置知识散落于代码注释 |
| 3 | 人肉发布（manual deployment） | **是** | 无 CD；start/install 脚本人工执行；release 自动发版无门禁 |
| 4 | 单点故障长期存在 | **是** | 单 Worker 串行 + 单 GPU `count:1` + 单容器 + 默认 127.0.0.1 本地监听 |
| 5 | 监控盲区（unmonitored critical path） | **是** | 无外部 exporter；指标随进程重启清零；`scripts/perf_monitor.py` 端口错配连不上；成功率/延迟无 SLO；请求级延迟未导出 |
| 6 | MTTR 不达标但未优化 | **是（更严重：未度量）** | 无 MTTR 度量；无事故时间线；回滚靠人工 git revert |

---

## 3. 核心指标打分（MTTD / MTTR / 可用性）

> 评分尺度 0–10（10=成熟）。打分为**现状客观评级**，非目标。

| 指标 | 得分 | 评级 | 依据 |
|---|---|---|---|
| **MTTD**（平均故障发现时间） | **2** | 极差 | 无告警通道；发现依赖用户投诉或人工 `curl` 健康检查；GPU 泄漏/OOM/熔断仅写日志无人知 |
| **MTTR**（平均故障恢复时间） | **2** | 极差 | 无度量、无 runbook、无自动回滚；恢复靠人工重启/回滚；串行单 Worker 使故障影响面集中 |
| **可用性**（Availability） | **6** | 中等 | 探针与自愈基础好（liveness/readiness 分离、`restart: unless-stopped`、优雅关闭）；但**无 HA**，单主机/单卡故障即全局不可用，无多副本 |

**综合稳定性成熟度：≈ 3.3 / 10（起步级 / Level 1 观测）** —— 具备"进程内健康自检 + 崩溃重启"的初级能力，但完全不具备"主动告警 + SLO 追踪 + 弹性 + 演练"的 SRE 成熟度要素。

### 打分依据小结
- 可用性未给更低分的原因：容器/进程级自愈链相对完整（`docker-compose.yml` healthcheck + restart + `health.py` 双探针 + `POST /shutdown` 优雅退出）。
- MTTD/MTTR 给极低分的核心原因：可观测性止步于"本机内存单例"，且**完全无通知与响应编排**。

---

## 4. 改进建议优先级排序（仅排序，不展开方案）

| 优先级 | 主题 | 对应缺口 | 预期收益 |
|---|---|---|---|
| **P0** | 建立告警通道（将日志级异常转为通知） | §1.1 告警缺失 / §2-1,5 | 直接拉高 MTTD：从"用户投诉"→"分钟级主动发现" |
| **P0** | 指标外部化（Prometheus exporter + 时序存储） | §1.1 无 exporter / 内存清零 | 解除盲区，支撑长期趋势与 SLO 计算 |
| **P1** | 定义 SLO/SLI 与错误预算 | §1.2 全缺失 | 使可用性可度量、可追责 |
| **P1** | 编写 Runbook + 事故响应流程 | §1.3 / §2-2 | 拉高 MTTR，降低人为误操作 |
| **P1** | 自动化回滚 / 版本化回滚 SOP | §1.5 / §2-3 | 缩短故障恢复时间，降低人肉发布风险 |
| **P2** | 跨重启指标持久化 + 集中日志聚合 | §1.1 日志/指标 | 支撑事后复盘与 MTTR 度量 |
| **P2** | 消除单点（多副本/多卡/反代 LB/外部监听） | §1.4 / §1.6 / §2-4 | 抬升可用性天花板 |
| **P2** | 持续容量监控 + 容量预警 | §1.4 仅离线工具 | 容量类故障前置发现 |
| **P3** | DR 备份（DB/历史）+ 演练机制 | §1.6 无 | 降低灾难级 RTO/RPO |
| **P3** | 修复监控工具错配（`scripts/perf_monitor.py` 端口） | §0 勘误 | 恢复既有诊断能力 |

---

## 5. 结论

TTS_MultiModel 在**进程内健康自检与崩溃自愈**层面已有扎实实现（显存熔断、泄漏检测、双探针、优雅关闭），但整体运维稳定性仍处于 **SRE 成熟度 Level 1（基础观测）**：

1. **可观测性不完整**——指标停留在内存单例、无外部导出、无集中日志、无长期保留；
2. **完全无主动告警**——这是当前最大的 MTTD 短板；
3. **无 SLO 与事故响应体系**——可用性不可度量、MTTR 不可追踪；
4. **架构性单点 + 人肉发布 + 无回滚/无演练**——决定了可用性上限与恢复速度天花板。

按 MTTD/MTTR/可用性三维评分分别为 **2 / 2 / 6**，综合约 **3.3/10**。最高杠杆的改进集中在 P0（告警 + 指标外部化），可在不改动业务架构的前提下显著改善故障发现能力。
