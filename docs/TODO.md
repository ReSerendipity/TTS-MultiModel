# TODO / 缺陷登记（owner 决策项）

> 建档说明（2026-11-01，阶段 3）：本仓此前无 tracked 缺陷登记文档（`FIX_LOG.md` 为本机独有不入库、`docs/reports/PENDING_ISSUES.md` 在 gitignore 面、CHANGELOG 由 release-please 生成不手写），按整改派单要求新建本文件作为缺陷/待办的 tracked 登记处。以下各项均已**实测复现**，登记待所有者决策，**不在登记轮修复**。

## D-1 进程级无守护（裸跑形态服务被杀后不自愈）

- 实测：2026-11-01 阶段 2b，kill 服务子进程后 T+60s 无任何拉回，launcher 同死；人工恢复 T_manual=9s(liveness)/3m12s(含模型重载)。详见 `docs/runbooks/2026-11-01-o3-failover-drill.md` §2b.3。
- 待决策：引入守护（NSSM/任务计划/clean_launch respawn）或接受「无自愈+人工 RTO≈3m12s」为既定形态。

## D-2 降级链最后一公里断裂（主引擎不可用 → Edge-TTS 无法实际接管）

- 实测：2026-11-01 阶段 2b——主引擎不可用时生成硬失败（engine_not_ready），**无自动降级**；`/api/model/switch` 半自动切换 edge-tts 2.1s 成功，但 `streaming_audio` ×2 均 400（无权重引擎 `loaded` 恒 false 被生成守卫拦截），且 openapi 无 edge-tts 生成路由。详见同文档 §2b.4。
- 待决策：为 edge-tts 补生成路由 / 放宽无权重引擎守卫 / 将降级明确定义为「人工切引擎 + UI 提示」并写入 SOP。

## G1 生产启动链在本机以 CPU torch 运行（GPU 推理不可达）

- 实测：2026-11-01 阶段 2b——服务按授权入口（start.bat → clean_launch.py → venv）启动后显存零增长、GPU util 0%；**venv torch = 2.15.0+cpu**；系统 Python312 torch = 2.11.0+cu128 但缺 funasr（voxcpm2 加载即 ModuleNotFoundError）。VoxCPM2 CPU 推理 RTF≈28（5 字短文本 41s，15 字文本 ≈23 分钟）。
- **矛盾点**：v2.9.0 `version.json` changelog 自称「IndexTTS 2.5 与 Breeze TTS 2 双引擎在 12GB 卡 load/infer/unload 全通」，而当前 venv 为 `torch 2.15.0+cpu`——当时真机 GPU 验收所用环境不是当前 venv，待所有者指认。
- 待决策：GPU 运行环境二选一（venv 装 cu128 torch / 系统 Python312 补 funasr 等依赖）并复跑真机验证。
