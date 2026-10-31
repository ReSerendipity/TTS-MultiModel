# O3 单点与切换演练记录（2026-11-01 · 阶段 2 · 降格执行）

> 对应评估门禁：**O3**（无核心组件单点且切换演练过，⛔P0 绝对）
> 依据：2026-10-29 评估报告 §11.2（O3[ ]）+ 阶段 2 工单 + **派单方补充围栏（2026-11-01 01:1x，优先级高于工单正文，覆盖其第 1/2/3 项前提）**
> 执行窗口：2026-11-01 00:5x – 01:2x（+0800）；上轮记录：`docs/runbooks/2026-10-31-restore-drill.md`
> 执行结果一句话：**故障注入 A/B 未执行（前置条件不满足，按围栏登记为未覆盖）；单卡多副本冗余经纸面算账判定不成立；O3 保持 [?]**
> 执行约束遵守情况：未启动/停止/触碰任何进程（含 ComfyUI）；未改任何 yaml/代码；本轮只改文档；生产库零接触

---

## 1. 开工机器事实复核（派单方围栏三条，逐条自行复验）

| # | 围栏事实 | 复核命令（原文） | 复核输出（原文） | 退出码 | 复核时刻 |
|---|---|---|---|---|---|
| 1 | TTS 服务未运行 | `netstat -ano -p tcp` + 过滤 `:7869` | （无输出行） | 1（findstr 无匹配） | 2026-11-01 01:14:30 +0800 |
| 2 | `127.0.0.1:8188` 监听者=ComfyUI（PID 28656），非本仓负载、严禁触碰 | `netstat -ano -p tcp` + 过滤 `:8188`；`tasklist` + 过滤 `28656` | `TCP 127.0.0.1:8188 0.0.0.0:0 LISTENING 28656`；`python.exe 28656 Console 1 203,080 K` | 0 / 0 | 2026-11-01 01:14 ~ 01:15 |
| 3 | 显存不足以加载任何 TTS 引擎 | `nvidia-smi --query-gpu=... --format=csv` | `NVIDIA GeForce RTX 5070 Ti Laptop GPU, 12227 MiB, 10224 MiB, 1721 MiB, 0 %`（total/used/free/util） | 0 | 2026-11-01 01:15 |

补充证据：`nvidia-smi --query-compute-apps` 显示唯一显存占用进程为 PID 28656（ComfyUI 的 python.exe；输出含本机用户名的路径列，按仓规打码不入库）；GPU util 0%（该负载空闲挂显存）。本机全部 python 进程清点中无本仓 `integrated_app` 实例（兄弟仓 ImageMultiModel 的实例在 127.0.0.1:8284，与本轮无关，未触碰）。

**服务未运行的旁证**：`logs/app.log` 最后写入 2026-10-31 22:05:35（最后活动实例 PID 26384），此后约 3 小时无任何日志；工作树开工基线 = 阶段 1 收工基线（见 §8）。

## 2. 基线快照（按围栏 (a)：以「服务未运行」证据链为基线内容）

- 监听端口：**无**（7869–7889 无任何监听者；原工单假设的「运行中的服务」不成立）
- 进程树：无本仓推理进程（ComfyUI PID 28656 为另一 agent/所有者的无关负载，**严禁 kill/停止/挂起/改端口**，未触碰）
- 健康接口：无法探测（服务未运行）；`/api/health/ping`、`/readyz` 本轮零调用
- GPU：RTX 5070 Ti Laptop，总 12227 MiB / 已用 10224 MiB（ComfyUI）/ **空闲 1721 MiB**，util 0%
- 已加载模型：无（服务未运行）；推理队列：不适用
- 当前版本号：`version.json` = **2.9.0**（HEAD `51c6f91` + 5 项在途）

## 3. 未执行项登记（按围栏：如实登记，不伪造数据）

| 原工单项 | 状态 | 原因（前置条件核对） |
|---|---|---|
| 基线「加载模型前显存预检」 | 未执行加载 | 空闲 1721 MiB < 任一 GPU 引擎声明门槛（最低 indextts20 5.5 GB）；按「显存不足硬停并报告」条款本就应硬停 |
| 注入 A：kill 主进程 → 观察自愈 | **未执行** | 服务未运行，无可 kill 的主进程；围栏明令禁止为注入而自行启动服务/加载模型 |
| 注入 A 后断言（健康/显存/僵尸进程/最小真实合成） | **未执行** | 依赖注入 A；「最小真实合成」亦需引擎加载（显存不足） |
| 注入 B：主引擎不可用 → Edge-TTS 降级切换 | **未执行** | 同上；未拿任何无关进程凑数，未伪造切换耗时 |
| B 撤除与回切验证 | **未执行** | 无注入即无撤除；「收工服务健康态」= 维持开工原状（服务未运行），零残留进程、零显存增量 |

补充（代码层读到的口径，供下轮实测设计用，本轮未实测）：进程级自愈在本机裸跑形态下**无实现**——`app/clean_launch.py` 无守护/重启循环，k8s `livenessProbe`（`deployment.yaml` L149-156，period 30s × failureThreshold 3）只在编排层生效；引擎级自愈为 OOM 自动重载（`config.yaml` L238-240，冷却 300s；实现 `routes/generate/utils.py` `_schedule_oom_auto_recovery`，卸载→重载当前引擎，失败标不健康交容器重启）；Edge-TTS 为 CPU 兜底引擎（`config.yaml` L108-136，vram 0.0、requires_gpu: false，可选依赖存在性守卫降级）。

## 4. 单卡多副本可行性（纸面算账，按围栏 (b)；未改任何 yaml）

各引擎显存声明值（`config.yaml models.engines`，只读提取）：

| 引擎 | vram_gb（声明） | requires_gpu |
|---|---|---|
| voxcpm2（核心） | 6.5 | true |
| indextts2 | 6.0 | true |
| indextts20 | 5.5 | true |
| voicebox | 4.0 | false |
| step-audio-editx | 8.0 | true（另声明 gpu_memory_utilization 0.5） |
| breeze-tts2 | 7.5 | true |
| edge-tts（降级兜底） | 0.0（CPU） | false |

卡容量：12227 MiB ≈ **11.94 GiB**（RTX 5070 Ti Laptop）。

**结论：单卡上把 replicas 提到 >1 不能消除 SPOF——判定「不成立」**，依据四条：

1. **显存算账**：2×voxcpm2=13.0 GiB、2×indextts2=12.0 GiB、2×step-audio-editx=16 GiB、2×breeze-tts2=15 GiB——全部超卡总容量；唯一装得下的 indextts20（2×5.5=11.0 GiB）在**空卡**上也只剩 ≈0.9 GiB 余量，未含每进程 CUDA 上下文（数百 MiB 级）与推理激活峰值，实际必然 OOM。声明值本身是加载预检下限，不是峰值。
2. **编排事实**：`deploy/kubernetes/deployment.yaml` 每副本请求 `nvidia.com/gpu: 1`（L134/L138），本机单卡 → 第二副本不可调度（Pending），replicas>1 在本机连第二实例都起不来；且 L8 自述「GPU 独占（单卡 SPOF，与单机一致）」、L3-4 自述多副本需先解决「单 Worker 串行」硬约束与多卡调度。
3. **语义层面**：即便显存装下，两副本仍共享同一块物理卡——卡故障/驱动故障/显存 OOM 同时命中两副本，只是把同一单点复制两遍，SPOF 未消除。
4. **当前机器状态佐证**：空闲仅 1721 MiB（ComfyUI 占 10224 MiB），本机此刻**连单实例都无法加载**，与多副本无涉。

可写为设计事实的替代方案（需所有者决策，见 §7）：跨机冷备（真冗余，需第二张卡/第二台机）、仅降级引擎作冗余（Edge-TTS 已在位，CPU、零显存——现状即此形态）、或接受单点并以「自愈 + 快速恢复」压缩 RTO。

## 5. RTO 汇总表（按围栏 (c)）

| 恢复对象 | RTO | 口径 |
|---|---|---|
| 进程级恢复（kill→拉回） | **本轮未实测**（前置条件不满足） | 本机裸跑无守护实现；k8s liveness 理论检测窗 ≈90-120s + 重启时长，为设计值非实测（`deployment.yaml` L149-156） |
| 降级切换（主引擎不可用→Edge-TTS） | **本轮未实测**（前置条件不满足） | 切换耗时/降级可用性均无实测数字，不伪造 |
| 数据库恢复（阶段 1 实测） | **≈10.5s** | 2.1GB 级备份物 → 真实恢复入口 + 恢复后完整性校验（`docs/runbooks/2026-10-31-restore-drill.md` §3） |
| 引擎级 OOM 自动重载 | 未实测（设计在位） | 冷却 300s；重载成败轮询上限 600s（`routes/generate/utils.py`） |

## 6. 门禁 O3 判定（按围栏 (d)）

- **O3：保持 [?]**（2026-10-29 评估口径 O3[ ] 为「单副本单卡无冗余无切换实测」；本轮后口径更新为）——**「进程级自愈与降级切换未实测（前置条件不满足：服务未运行 + 空闲显存 1721 MiB）；单卡多副本冗余经算账判定不成立（显存装不下 / 不可调度 / 同卡复制单点）」**。不得升 [x]。
- 阶段 1 遗留缺口沿用登记、**不在本轮处理**：①`scripts/rollback_release.py` 对 merge 提交反向 revert 退 128（`-m 1` 单点已验证可行）；②v2.8.0 tag 版本位自相矛盾（自动位 vs 手工位）。两项均标记为「待阶段 3 或所有者决策」，未偷偷修代码。
- 附带登记：本轮只读复核确认 `config.yaml` L112 `edge-tts.vram_gb: 0.0` 即阶段 1 发现 F4（pydantic 校验告警）的来源，属既有事项，不在本轮修。

## 7. 需所有者决策的项（按围栏第三节逐条回答）

1. **O3 实测窗口**：需要所有者择一——(a) 指定一个可停掉 ComfyUI（释放 ≈10.2 GiB，空出整卡）的时间窗，派单方在窗内重跑注入 A/B 实测；(b) 改用一台有空卡的机器（≥12 GiB 空闲显存、可装 voxcpm2 权重）执行同一套注入脚本。二者任一即可闭环「切换实测」部分；硬件级冗余则仍需第二张卡/第二台机。
2. **设计事实确认**：若本机长期无法同时驻留两实例（算账结论已给出），是否接受「不做多副本；以引擎级 OOM 自愈 + Edge-TTS 降级兜底 + 快速恢复压缩 RTO」作为本仓的单点处置口径？接受则 O3 的「无单点」部分按形态豁免/接受风险登记，仅保留「切换实测」待窗口。
3. **阶段 1 遗留缺口**：`rollback_release.py` merge revert 缺陷（修脚本 + 重跑回滚演练）与 tag 版本位漂移（回滚 SOP 增补版本位断言 / 发版侧前移检查）——留到阶段 3 一并处理，还是所有者授权现在单独处理？

## 8. 开工/收工基线（git status --porcelain）

开工（2026-11-01 00:5x，阶段 2 接单时）与 01:16:20 复核输出一致：

```
 M desktop/src-tauri/src/window.rs
 M desktop/src/desktop-bridge.js
 M desktop/src/index.html
 M scripts/installer/setup.nsi
?? "docs/发布与分发-单文件安装包.md"
```

5 项在途（4 项已跟踪 WIP + 1 项未跟踪他 agent 产物）全程未触碰、未读取其内容、未纳入提交。

## 9. 命令与退出码附录（本轮全部只读）

```text
# date；netstat -ano -p tcp | findstr :7869      exit 1（无匹配 → 服务未运行）
# netstat -ano -p tcp | findstr :8188            exit 0（LISTENING 28656）
# tasklist | findstr "28656"                     exit 0（python.exe 28656）
# nvidia-smi --query-gpu=... --format=csv        exit 0（12227/10224/1721 MiB, util 0%）
# nvidia-smi --query-compute-apps=...            exit 0（唯一占用者 PID 28656，路径列已按仓规打码）
# git status --porcelain                          exit 0（5 项在途原样）
# 读 config.yaml / deploy/kubernetes/deployment.yaml / version.json / logs/app.log（只读）
```
