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

## R4 发布回滚演练（门禁补证动作）——已闭环（2026-11-02 阶段 8）：真仓完整历史重跑 exit 0，四态 `[?]→[x]`

- 动作：在真仓 **756 条完整历史的 file 协议全量克隆**（非浅、非合成最小仓，保真度自检 8 项全过）内，用生产 venv 解释器真跑 `scripts/rollback_release.py --target v2.8.0`——与 2026-10-31 退 128 那次完全同形的调用。
- 实测：11:11:25→11:11:30（5s）**退出码 0**；43/43 个 revert 全部成功（主线 10 个 merge 全走 `-m 1`，含当初元凶 `8a0851da`，第 24/43 个目标）；回滚后树与 `v2.8.0` 树逐字节一致（tree hash `db91f16d`，diff 0 文件 0 增删）；脚本活体 sha256 三方对平（`6dbc567e…`/5050 B）。生产 checkout 全程零改动（起测/收测 `git status --porcelain` 均 0）。
- 判据：R4 两判据 **①记录可复核 ②对象是活体** 逐项通过 → `[x]`（③周期性不适用，归 D6/O4）。
- 留痕：`docs/runbooks/2026-11-02-r4-real-history-rollback-drill.md`（含未覆盖清单与台账行）；演练目录 `C:/Users/Doro/Desktop/评估报告_2026-10-29/_演练/R4-real-history/` 保留作证据。
- 未随本轮收口的挂账项：F2（`v2.8.0` tag 内嵌版本位漂移，SRE_RUNBOOK §2.4 硬停断言对其判 FAIL）仍属所有者决策项，不在阶段 8 范围；脚本未实现 §2.4 断言（人工 SOP）亦登记为未覆盖。

## F3 文档推荐入口死路径（`backup_db.py`）——已闭环（2026-11-02 阶段 9）：退役删除，推荐入口改指活链，四态 `[?]（待决策）→[x]`

- 实测判据：`git grep -l backup_db` 仅命中 `docs/SRE_RUNBOOK.md`、`docs/runbooks/2026-10-31-restore-drill.md`，`app/`、`tests/`、`.github/` 零引用；`data/tts_history.db` 不存在（`ls` 实测）。
- 根因（**推翻工单派单锚的「S-R4 合并所致」推测**）：`git show` 取证——`90e8512` 时 `config.yaml:58` 曾有 `db_path: data/tts_history.db`，`64a753f`（2026-08-16「prune legacy sections」）清理该段，`80c13b5`（2026-08-31）新增 `backup_db.py` 时照抄了这个**已死 15 天**的命名；S-R4 合并的是 `outputs/history.db`→`data/history.db`，与 `tts_history.db` 无涉。
- 处置：`git rm scripts/backup_db.py`；SRE_RUNBOOK §3.3 推荐入口改指 `scripts/backup_history_db.py`（有测试 `tests/test_backup_history_db.py`、2026-10-31 演练实测活链），并成文备份集覆盖口径；冷 shell 新进程原样跑文档命令 `.venv/Scripts/python.exe scripts/backup_history_db.py --keep 14` **exit 0**（12:18:44）。详见 `docs/runbooks/2026-11-02-o4-backup-wiring.md` §3/§4。

## O4 备份链路（门禁项，⛔P0）——阶段 9 关掉四阻塞中的两个，**保持 `[?]`**

- 本轮关掉：②F3 死路径（上条，`[x]`）；①无调度接线——交付 `scripts/install_backup_schedule.py`（默认 `--dry-run` 打印任务名/频率/触发时刻/完整命令行/运行身份/下一次应执行时刻，`--apply` 才注册且**本轮未调用、真实注册待所有者授权**；幂等：固定 `/tn` + `/f`，重复 dry-run 逐字节一致），配套 11 项测试全绿且不触碰系统。
- 剩下什么（不可伪装关闭）：③同机无异地——需属主给第二个存储位置，属资源决策；④周期性——待 2027-01 到期轮次佐证（调度未注册前无周期证据）。另备份集覆盖边界新成文：`data/` 下 5 个运行密钥（`.pii_key`/`.history_hmac_key`/`.watermark_key`/`.csrf_secret`/`.manifest_signing_key`，实测 44/64/119/64/32 B）无任何备份覆盖、`generation_versioning` 库（`enabled=false`）不在默认备份集、`data/audit.log` 实测未落盘（无此对象）。留痕：`docs/runbooks/2026-11-02-o4-backup-wiring.md`。

## F6（阶段 9 登记；2026-11-02 阶段 10 止血+可逆隔离完成，剩余项属主决策）测试套件写污染生产 `data/history.db.action_logs`

- **修正后的量级（阶段 10 实测 14:26:19，处置前活体）**：`action_logs` 全量 **2,706 行**。**阶段 9 登记的「+40 行」为局部观测**（仅本轮两次运行窗口 12:20:38–12:36:44），全量为：可证测试签名 **2,167 行** = `test_operation`（`message='测试操作'`/`extra_json={"key": "value"}`，签名 347/347 统一）**347** + `unknown` 空签名双写 **344**（±2s 配对 344/347；时间戳为逐对相差 ~5ms 的双写，非严格同值）+ `generation` 含 `"engine": "test_` 桩引擎签名 **1,476**（其中含 `"endpoint": "ep"` 1,230）；噪声跨度 2026-08-08 00:31:20 → 2026-11-02 12:36:44。不可证部分：`generation` 无上述签名 **451 行**（时间相关性实测：仅 **12 行（2.66%）** 落在可考证的阶段 8/9 测试窗口内，270 行耗时 ≤0.4s，其余 439 行散布于 2026-07-31→11-01 无窗口记录期）+ `model` 动作 **88 行**（全部在窗口外）——工单原文「剩下 449 行也不可采信」经实测为**夸大**，如实分档：可证 2,167 / 高度疑似 12 / 无法判定 439+88。
- **机制（实测）**：`tests/conftest.py` 原 103 行内 `TTS_HISTORY_DB_PATH` 出现 0 次，全测试套仅 `tests/test_history_pruning.py` 自行隔离——默认所有测试经 `get_history_db()` 单例 / `log_operation()` 双写生产库；连带 `outputs/generation_versions.db`（`VersionManager(db_path=None)` 默认路径）同型被写（实测 mtime 12:36:31 落在阶段 9 窗口）。`push_subscriptions.db` 实测无任何代码引用（孤儿 -wal/-shm，不处置）。
- **处置（阶段 10）**：①止血——conftest 全局隔离（强制 `TTS_HISTORY_DB_PATH` 走应用承认的优先级入口）+ `sqlite3.connect` 哨兵（打开生产 `data/history.db` 即抛错并点名测试 nodeid）+ `generation_versions.db` 默认路径透明重定向；配套 `tests/test_f6_db_isolation_sentinel.py` 8 项自证。②有效性证明：口径 A 全量两跑 **2622 passed / 0 failed / 41 skipped**（=基线 2614+新增 8），处置前后 `action_logs` 行数 2706→2706、`MAX(ts_ms)`、`history.db`/`-wal`/`-shm` 与 `generation_versions.db` 字节数+sha256 **逐字节不变**；口径 B **2530 / 0 / 10（123 deselected）** 覆盖率 52.33%≥45%。③可逆隔离：仅三个可证桶移入 `action_logs_quarantine_20261102`（batch `F6-20261102-A`），单事务，对账 `2,167 == 347+344+1,476`、`2,167 + 539 == 2,706` 两等式 PASS；回滚路径**实测执行**（回滚→主表 2,706、行内容指纹 FP1==FP2→再隔离）；读路径（`/api/system/logs` 分页/COUNT、`logs-compat` 走内存、`/logs/clean` 计数形态）隔离态全验通过。留痕：`docs/runbooks/2026-11-02-f6-test-db-isolation.md`。
- **剩余属主决策（不在本轮范围）**：①451+88 行「疑似/无法判定」原地保留待判定；②既有备份物（12:17/12:18/14:29 三份均取于隔离前）仍含噪声，是否重取干净备份待定；③调度注册与异地副本沿阶段 9 不变。
- **阶段 11（2026-11-02/03，B 批闸门 fastfail→零隔离）**：对残留 539 行的第二批判据做代码取证——`8192` **钉上**（`tests/test_generation_error_friendliness.py:160` `"啊"*8192`+`instruction="温柔"` → 写链 `design.py:257`→`utils.py:1017`→`logs.py:273`；隔离库复现行与生产 **206/206** 五元组签名闭合，错误串为 ：142 `object()` 桩指纹）；`streaming` **钉不上**（路由写死 `streaming.py:704/718/736-740/754/909/940`，复现证明测试不传该值也落库；44 行中 **3 行真实合成**（338=27.2s、629/630=3.6/3.3s）会被判据误删）→ 按工单闸门「立即停手」：**B 批 `F6-20261102-B` 零行隔离**，备份/隔离/回滚/复隔离取消。**(B) 22 行 0.0s 失败证伪为真实使用**（错误串 tests/ 零出现、id 987=CUDA assert、守卫 `7326b94` 09-14/`4a45e6f` 09-19 晚于行时间），成因 7 类，判定点与修正（IndexTTS2 路由 `voice_or_persona` 写死为空 `synthesize.py:529`，空 persona≠未选）见诊断；**clone.py:260 空引擎缺口现状仍敞开（唯一），是否补属产品决策**。两口径 A 2545/0/117×2、B 2448/0/91（cov 51.63%）全 exit 0，五快照 `history.db` 2,469,888 B/sha `9652e5e7…` 逐字节不变（哨兵无漏，conftest 未动）。注意：`.venv` 符号链接目标 `D:\_venvs\tts` 已失效，本轮以系统 Python 3.12.10 代跑（未装依赖）。残留精算：206 可证+41 streaming（39 可证+2 钉不到）+3 真实+22 真实+**179 generation 与 88 model 未检**。留痕：`docs/runbooks/2026-11-02-f6-batch-b-and-fastfail.md`。
