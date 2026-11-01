# TODO / 缺陷登记（owner 决策项）

> 建档说明（2026-11-01，阶段 3）：本仓此前无 tracked 缺陷登记文档（`FIX_LOG.md` 为本机独有不入库、`docs/reports/PENDING_ISSUES.md` 在 gitignore 面、CHANGELOG 由 release-please 生成不手写），按整改派单要求新建本文件作为缺陷/待办的 tracked 登记处。以下各项均已**实测复现**，登记待所有者决策，**不在登记轮修复**。

## D-1 进程级无守护（裸跑形态服务被杀后不自愈）——已按所有者决策接受（2026-11-01，阶段 4）

- 实测：2026-11-01 阶段 2b，kill 服务子进程后 T+60s 无任何拉回，launcher 同死；人工恢复 T_manual=9s(liveness)/3m12s(含模型重载)。详见 `docs/runbooks/2026-11-01-o3-failover-drill.md` §2b.3。
- **处置**：所有者拍板**接受无自愈、不引入守护**；口径已写入 `docs/SRE_RUNBOOK.md` §2.5（含「什么形态才承诺自愈」的容器/k8s 边界）。状态：**闭环（口径成文）**。

## D-2 降级链最后一公里断裂——已修复并实测闭环（2026-11-01，阶段 4）

- 阶段 2b 实测的三断点：①edge-tts 无 REST 生成路由；②无权重引擎被生成守卫恒拦；③主引擎不可用无自动降级。
- **处置**（commit 本轮）：①新增 `POST /api/generate/edge-tts/tts` 直连路由（`routes/generate/edge_tts.py`）；②守卫放宽至无权重引擎（requires_gpu=False 且 vram_gb=0，raw YAML 兜底判定），主引擎口径不变；③`streaming_audio` 主引擎「未加载且权重目录缺失」时自动降级，200 JSON 带 `degraded` 字段（openapi 快照已登记），降级也失败返回可区分的 `error_type=fallback_failed`（不假成功）。
- 实测（真机、坏 model_dir 仓外副本注入）：T0=13:48:40 → **3.07s** 返回 200，产物 `edge_tts_fallback_1793512123451.mp3` **21,888 B**（edge-tts 7.3.1 恒出 MP3，RIFF/WAV 不可用），degraded 实际取值 `{from: voxcpm2, to: edge-tts, reason: main_engine_unavailable}`；撤除后主路径回归 `engine_not_ready` 原样（未加载流不再被降级劫持——首版语义缺陷已修）。测试 `tests/test_edge_tts_fallback.py` 5/5 绿。状态：**闭环**。

## G1 生产启动链在本机以 CPU torch 运行——环境已修复（阶段一，2026-11-01），GPU 推理实测待窗口

- 阶段 2b 实测：服务按授权入口启动后显存零增长、GPU util 0%，venv torch=2.15.0+cpu，VoxCPM2 CPU 推理 RTF≈28（5 字 41s，15 字 ≈23 分钟）。
- **阶段 5 处置**（同版本换 CUDA 构建，不降级）：venv torch **2.15.0+cpu → 2.15.0+cu134**（wheel 2162.1 MB，dry-run 闸门确认仅替换 torch 本体）。装后实测：`cuda.is_available()=True`、`torch.version.cuda=13.4`（驱动 UMD 13.4 同代）、capability=(12,0)、真 GPU matmul 1024² 0.271s/8MB、torchaudio 2.11.1+cpu 完好、七引擎 import 冒烟全过、测试套 2598→2600 passed 且新增失败=0（同一既有失败为 openapi 快照漂移，见下）。
- **诚实边界**：v2.9.0 changelog「12GB 卡 load/infer/unload 全通」所用环境仍**不是**本 venv，本轮修好环境**不等于**证实该历史主张——维持「待所有者指认」，未改 changelog、未追认。
- **剩余**：voxcpm2 加载→最小真实合成→卸载→显存回落（需 7.8 GiB 预检额度），待派单方窗口（阶段二）。

## G4（新登记）requirements-lock.txt 与 venv 漂移（独立 chore，本轮不修）

- lock 记 `torch==2.13.0` / `torchaudio==2.11.0` / `torchvision==0.28.0`；venv 实为 `2.15.0+cu134`（本轮换构前为 2.15.0+cpu）/ `2.11.1` / **torchvision 未安装**。三处不一致；禁止本轮改 lock。

## G5（新登记）tests openapi 快照与实现漂移（独立 chore，本轮不修）

- 阶段 4 新增 edge-tts 路由与 streaming_audio 降级 responses 后，`tests/test_openapi_snapshot.py::test_contract_matches_snapshot` 持续失败（快照 fixture 未再生）。属仓库文件改动（快照再生），按阶段 4/5 的改动面限制未处理；G1 轮测试基线前后均为该同一失败，非本轮引入。
