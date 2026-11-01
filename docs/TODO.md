# TODO / 缺陷登记（owner 决策项）

> 建档说明（2026-11-01，阶段 3）：本仓此前无 tracked 缺陷登记文档（`FIX_LOG.md` 为本机独有不入库、`docs/reports/PENDING_ISSUES.md` 在 gitignore 面、CHANGELOG 由 release-please 生成不手写），按整改派单要求新建本文件作为缺陷/待办的 tracked 登记处。以下各项均已**实测复现**，登记待所有者决策，**不在登记轮修复**。

## D-1 进程级无守护（裸跑形态服务被杀后不自愈）——已按所有者决策接受（2026-11-01，阶段 4）

- 实测：2026-11-01 阶段 2b，kill 服务子进程后 T+60s 无任何拉回，launcher 同死；人工恢复 T_manual=9s(liveness)/3m12s(含模型重载)。详见 `docs/runbooks/2026-11-01-o3-failover-drill.md` §2b.3。
- **处置**：所有者拍板**接受无自愈、不引入守护**；口径已写入 `docs/SRE_RUNBOOK.md` §2.5（含「什么形态才承诺自愈」的容器/k8s 边界）。状态：**闭环（口径成文）**。

## D-2 降级链最后一公里断裂——已修复并实测闭环（2026-11-01，阶段 4）

- 阶段 2b 实测的三断点：①edge-tts 无 REST 生成路由；②无权重引擎被生成守卫恒拦；③主引擎不可用无自动降级。
- **处置**（commit 本轮）：①新增 `POST /api/generate/edge-tts/tts` 直连路由（`routes/generate/edge_tts.py`）；②守卫放宽至无权重引擎（requires_gpu=False 且 vram_gb=0，raw YAML 兜底判定），主引擎口径不变；③`streaming_audio` 主引擎「未加载且权重目录缺失」时自动降级，200 JSON 带 `degraded` 字段（openapi 快照已登记），降级也失败返回可区分的 `error_type=fallback_failed`（不假成功）。
- 实测（真机、坏 model_dir 仓外副本注入）：T0=13:48:40 → **3.07s** 返回 200，产物 `edge_tts_fallback_1793512123451.mp3` **21,888 B**（edge-tts 7.3.1 恒出 MP3，RIFF/WAV 不可用），degraded 实际取值 `{from: voxcpm2, to: edge-tts, reason: main_engine_unavailable}`；撤除后主路径回归 `engine_not_ready` 原样（未加载流不再被降级劫持——首版语义缺陷已修）。测试 `tests/test_edge_tts_fallback.py` 5/5 绿。状态：**闭环**。

## G1 生产启动链在本机以 CPU torch 运行——**已闭环（2026-11-01 阶段 6）：环境修复 + GPU 真机实测均完成**

- 阶段 2b 实测：服务按授权入口启动后显存零增长、GPU util 0%，venv torch=2.15.0+cpu，VoxCPM2 CPU 推理 RTF≈28（5 字 41s，15 字 ≈23 分钟）。
- 阶段 5 处置：venv torch **2.15.0+cpu → 2.15.0+cu134**（同版本换构建，dry-run 闸门仅 torch 本体；`cuda.is_available()` 实测 True、13.4、capability (12,0)、七引擎冒烟、测试套零新增失败）。
- **阶段 6 GPU 真机实测**（8906 MiB free 预检过，真实 config.yaml）：加载 **43.2s**、显存 **+6609 MiB（≈6.45 GiB，与声明 6.5 同量级）**；合成（5 字，与 2b 同口径）**8.39s** / 130,278 B / 1.36s 音频 @48kHz（CPU 基线 41.0s / 84,304 B / 0.88s → **4.9× 提速**）；卸载→显存回落（9648→3280 used）；重载 **30.3s**→再合成 **4.18s**。**load/infer/unload 循环真机成立**。
- **诚实边界（保留）**：v2.9.0 changelog「12GB 卡 load/infer/unload 全通」所用环境仍非本 venv，本轮是修复后环境上的自证，**不等于**证实当年验收环境——维持「待所有者指认」，未改 changelog。

## G4 lock 与 venv 差异——**对账完成（2026-11-01 阶段 7）：定性为交付轨契约，非漂移**；变更需三处连动窗口（待所有者决策）

- 对账实证：lock 的 `torch==2.13.0` / `torchaudio==2.11.0` 是**三处同钉契约**（锁 ↔ `scripts/build_portable_bundle.ps1` ↔ docker 镜像；owner 2026-09-23 定「不在发版关口改版本」，显存实测过的轨=2.13.0+cu132）——由 `tests/test_torch_track_consistency.py` 强制（本轮 L2 手工对账两次实测撞闸：torchvision 移除破三件套同钉、torch 升版破三处一致，按备份还原后全绿）。
- **语义分工**：venv=开发环境（`2.15.0+cu134`，阶段 5/6 GPU 修复成果，收工硬判据逐字符保持）；lock=交付轨声明（2.13.0+cu132 轨）——两者**有意不同**，不构成漂移。
- **待所有者决策（各占一行，未顺手修）**：① torch 交付轨升版窗口——三处连动（锁+便携脚本版本与 SHA256+docker）后 lock 才能记 2.15.0；② edge-tts==7.3.1 是否进交付轨——联网依赖 vs 离线便携场景（L2 试加即触发 `--check-small`「便携清单缺失」红）；③ 既有红（非本轮引入、未修）：`sync_requirements --check` 报 requirements.txt 多 `munch>=4.0.0`、`--check-small` 报 filelock 4.2.1/pillow 13.0.0 与 lock 不一致。
- 本轮 lock 文件**零改动**（尝试性编辑已按备份还原，`git diff` 空）；收工自证：全量套 **2603 passed / 0 failed**、venv 硬判据 `2.15.0+cu134 True 13.4` 逐字符一致。

## F4（对账自 restore-drill §5.1）edge-tts vram_gb=0.0 被 pydantic 判非法——已闭环（2026-11-01 阶段 6）

- 处置：schema 放宽 `vram_gb`/`ram_gb` 为 `ge=0`（0=无本地权重的真实语义），config.yaml 完整过 pydantic（voxcpm2 vram=6.5 文件值落位），raw-YAML 兜底死码删除；判据 `requires_gpu=False 且 vram_gb==0.0` 不变（voicebox vram 4.0 不受影响，测试钉住）。详见 commit `cabed0f` 与 `tests/test_config_zero_vram.py`。

## G5（更正定性，2026-11-01 阶段 5b 闭环）tests openapi 快照与实现不一致——阶段 4 的回归，已重基线闭环

- 性质更正：阶段 5 曾误记为「待排期漂移」；实际是**阶段 4（`036e78b`）新增 edge-tts 路由与 streaming_audio 503 后未同步基线**造成的门禁红灯（快照上次入库为第三方提交 `a24c44d`）——根因归属 `036e78b`，非独立漂移。
- 处置：`UPDATE_OPENAPI_SNAPSHOT=1` 重基线（仓内既有机制，`tests/test_openapi_snapshot.py:20-21,83-85`）；diff 逐行核对恰两条（`[ADDED] /api/generate/edge-tts/tts`、`[CHANGED] streaming_audio 422→422,503`），零夹带。复验：快照测试 3 passed；全量套 **0 failed / 2601 passed / 41 skipped**（红灯清零，passed +1）。
- 状态：**闭环**。
