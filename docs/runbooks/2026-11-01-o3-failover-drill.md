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

---

# 阶段 2b 真机实测（2026-11-01 09:50 – 10:24，追加章）

> 派单方已腾空显存（ComfyUI 由所有者指令结束，本轮零触碰）；工单要求注入 A/B 真做。执行窗口 09:50–10:24（+0800），≤40 分钟预算内完成。

## 2b.1 开工基线

- `git status --porcelain`（09:50:48 原文）：HEAD=**17e8b28**（另一 agent 的提交「feat(desktop): 原生标题栏 + 单文件安装包」）；在途仅 `?? scripts/build_runtime_bundle.py`（他人产物：未读、未改、未 add）
- 版本位 `version.json`=2.9.0；`nvidia-smi`：**0 MiB used / 11944 MiB free**，util 0%，compute-apps 空，`:8188` 无监听（exit 1）
- 进程白名单（6 个他人进程：fixture/资源扫描/hf 上传/workbuddy http/zcode monitor/兄弟仓 app_server:13563）——全程未触碰；本仓进程 0 个
- 显存预检：voxcpm2 声明 6.5 GB × 1.2 = 7.8 GiB ≤ 11.66 GiB 空闲 → 允许加载
- 启动（授权入口）：`Start-Process` 脱离 shell 执行 `.venv\Scripts\python.exe app\clean_launch.py`，设 `BROWSER=TTS_DRILL_NOOP` 抑制弹窗（webbrowser 尊重该环境变量；仓库外手段，未改任何文件）；预期端口 7869；就绪 ≈3s（09:55:48）
- 进程树实测：launcher（venv python.exe trampoline）→ 服务子进程（base 解释器 + venv site-packages，**同一逻辑进程、非守护**）；服务全程 VRAM ≈275 MiB（非推理占用）

## 2b.2 关键环境发现 G1（启动链 CPU 化，缺陷登记）

`config.yaml server.auto_load_model=false`；加载 voxcpm2 后**显存零增长（GPU util 0%）**。实测定位：**venv torch = 2.15.0+cpu（无 CUDA）**，而系统 Python312 torch = 2.11.0+cu128（有 CUDA）但缺 `funasr`（voxcpm2 加载即 `ModuleNotFoundError`）——**两条环境各缺一半，授权手段内 GPU 推理不可达**。v2.9.0 changelog 所称「12GB 卡 load/infer/unload 全通」所用环境非当前 venv（待所有者指认）。CPU 推理实测 RTF≈28：预热「你好」（0.6s 音频）17.2s；15 字文本合成进度 334 it × 4.2 s/it ≈ **23 分钟**（首次合成请求据此超时，改用 5 字最短文本后可行）。

## 2b.3 「可用」基线与注入 A（kill 主进程）

- 合成 #1（基线）：POST `/api/generate/voxcpm_design`（Form：text=恢复演练。5 字 + instruction）→ **41.0s**，产物 `voxcpm_design_1793499148.wav`（**84,304 B / 0.88s 音频**）；`readyz=200`
- **T0=10:12:47**：`taskkill /PID 22504 /F`（服务子进程，exit 0）→ 观察 60s（每 10s 采样）：**无监听、健康探测 000（不可达）、launcher 进程一并退出**——**无任何守护/自愈拉回，T+60s 仍不可用**。上轮代码层口径「本机裸跑无进程级守护」被实测**证实**（缺陷登记，非通过）
- 人工恢复路径：重新执行启动命令 → **T_manual(liveness)=9s**（10:14:37→10:14:46，新服务 PID）→ 重载 voxcpm2 **61.0s** → `readyz=200`（10:15:59）。**T0→完全恢复 = 3m12s**
- 断言：健康接口恢复同等状态 ✓；显存回落 ✓；无僵尸/重复进程 ✓（服务进程唯一）；合成 #2 **69.1s**，产物 210,736 B ✓

## 2b.4 注入 B（主引擎不可用 → Edge-TTS 降级）

- 手段（可逆性先写明）：`TTS_CONFIG_PATH` 指向**仓外临时副本**（仅第 35 行 `model_dir: "VoxCPM2"` → 不存在路径；仓库文件零改动）；撤除 = 停服 → 去环境变量重启
- 实测 1（自动降级判定）：主引擎不可用态下 POST voxcpm_design → **硬失败**（HTML 错误块 `data-error-type="engine_not_ready"`「VoxCPM2 模型未加载，请先加载模型」）→ **无自动降级接管**
- 实测 2（人工切换=半自动）：`POST /api/model/switch engine=edge-tts` → **2.1s 成功**（current_engine=edge-tts）。注：edge-tts 此刻**未安装**（可选依赖，`import edge_tts` 失败）——按仓内 config 注释口径 `pip install edge-tts`（venv，演练后已 `pip uninstall` 还原）；带病配置重启后 openapi **无 edge 生成路由**（engine_discovery 未为其建 REST 路由）
- 实测 3（降级输出）：`POST /api/generate/streaming_audio` ×2 → 均 **400 engine_not_ready**（无权重引擎 `loaded` 恒 false，被生成守卫拦截）→ **按两次失败止损：降级输出本轮不可用**（缺陷登记：半自动切换成功但生成「最后一公里」断裂）
- 撤除确认：停服 → 正常配置重启 → voxcpm2 重载 64.6s → `readyz=200` → 合成 #3 **98.4s** 成功（`voxcpm_design_1793499795.wav`）→ **停服**（所有者需回收显存）→ `pip uninstall edge-tts`（`find_spec=False` 已还原）

## 2b.5 RTO 汇总（四行，含本轮实测）

| 恢复对象 | RTO | 口径 |
|---|---|---|
| 进程级自愈（kill→自动拉回） | **不存在**（实测：T+60s 无恢复，launcher 同死） | 缺陷登记 D-1 |
| 人工恢复（重启→liveness / →可合成） | **9s / 3m12s**（含模型重载 61s） | 本轮实测 |
| 降级切换（主引擎→Edge-TTS） | 切换 **2.1s（半自动，人工 API 调用）**；**降级输出不可用**（守卫拦截，D-2） | 本轮实测 |
| 数据库恢复（阶段 1 实测） | **≈10.5s**（2.1GB 备份物） | `2026-10-31-restore-drill.md` §3 |
| 引擎级 OOM 自动重载 | 未覆盖（本轮无 OOM 场景） | 设计在位（冷却 300s） |

## 2b.6 门禁 O3 判定（阶段 2b 后）

- **O3 整体保持 [?]**，口径：**「进程级自愈实测不存在（D-1）；降级切换仅半自动且输出链断裂（D-2）；无核心单点经算账+实测判定单卡上不可消除（需跨机/第二张卡）」**。不得升 [x]。
- 上一章「未执行项登记」逐行对账：基线加载前显存预检→**已覆盖**（2b.1）；注入 A 及四项断言→**已覆盖**（2b.3）；注入 B 及撤除→**已覆盖**（2b.4，降级输出为止损结论）；「最小真实合成」→**已覆盖**（3 次，短文本口径）。
- 本轮缺陷登记：**D-1** 进程级无守护（裸跑形态）；**D-2** 降级链最后一公里（无自动降级 + edge-tts 无 REST 生成路由 + 生成守卫对无权重引擎恒拦）；**G1** 生产启动链在本机以 CPU torch 运行（GPU 推理不可达）。三项均不在本轮修（本轮只改文档）。

## 2b.7 需所有者决策的项（阶段 2b 新增）

1. **GPU 运行环境二选一**：(a) venv 安装 cu128 torch（当前 venv 依赖全但 torch=CPU）；(b) 为系统 Python312 补全缺省依赖（有 cu128 torch 但缺 funasr 等）；或指认 v2.9.0 真机 GPU 验收当时所用环境。不解决则「最小真实合成」恒为分钟级 CPU 吞吐。
2. **D-2 修复方向**：为 edge-tts 补生成路由 / 放宽无权重引擎的生成守卫 / 或把降级明确定义为「人工切引擎 + UI 提示」并写入 SOP。
3. **D-1 处置**：本机裸跑是否引入守护（如 NSSM/任务计划/clean_launch 加 respawn）——或接受「无自愈 + RTO≈3m12s（人工）」为既定形态。

## 2b.8 命令与退出码附录（阶段 2b）

```text
# 启动（BROWSER 抑制，脱壳 Start-Process）×3 + 破配置启动 ×2    全部就绪（ping 200）
# POST /api/model/load engine=voxcpm2 ×3                        exit 0（68.3s / 61.0s / 64.6s）
# POST /api/generate/voxcpm_design ×3（5 字短文本）             41.0s / 69.1s / 98.4s，产物 3 个 wav
# taskkill 服务子进程（注入 A，T0=10:12:47）                    exit 0；60s 观察无自愈
# POST /api/model/switch engine=edge-tts                        exit 0（2.1s）
# POST /api/generate/streaming_audio ×2                        400 engine_not_ready（止损）
# pip install / uninstall edge-tts（venv，演练后已还原）        OK / find_spec=False
# TTS_CONFIG_PATH 仓外副本注入/撤除                             仓库文件零改动
# 终态：7869 无监听；我方 PID 全清；显存 275 MiB 为第三方（PID 26436/36276，非我方）
```
