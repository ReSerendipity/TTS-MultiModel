# TTS-MultiModel TODO 已闭环段归档（2026-11-19）

> 来源：`docs/TODO.md` @ `ed6c3dc`（2026-11-19）
> 依据：`REPOSITORY_GUIDELINES.md` §18.4 A 轨（TODO/清单类，已完成占比 ≥ 30%）
> 状态：默认不可见；仅在活跃文档指针或 `_archive/INDEX.md` 索引时按需追溯
> 原文完整性：以下各段自 `docs/TODO.md` 逐字节搬运，未改写。

## D-1 进程级无守护（裸跑形态服务被杀后不自愈）——已按所有者决策接受（2026-11-01，阶段 4）

- 实测：2026-11-01 阶段 2b，kill 服务子进程后 T+60s 无任何拉回，launcher 同死；人工恢复 T_manual=9s(liveness)/3m12s(含模型重载)。详见 `_archive/执行记录/2026-11/2026-11-01-o3-failover-drill.md` §2b.3。
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
- **WO-B28（2026-11-09）G1 口径落定（所有者决策）**：v2.9.0「12GB 卡全通」的验收环境**无法指认 → 按「未证」处理，不再作为阻塞项**；阶段 6 在修复后环境上的真机自证保留为独立证据（43.2s 加载 / +6609 MiB / 8.39s 合成与 4.9× 提速），**不改 changelog、不重打 tag**、不改版本钉。

## G4 lock 与 venv 差异——**对账完成（2026-11-01 阶段 7）：定性为交付轨契约，非漂移**；变更需三处连动窗口（待所有者决策）

- 对账实证：lock 的 `torch==2.13.0` / `torchaudio==2.11.0` 是**三处同钉契约**（锁 ↔ `scripts/build_portable_bundle.ps1` ↔ docker 镜像；owner 2026-09-23 定「不在发版关口改版本」，显存实测过的轨=2.13.0+cu132）——由 `tests/test_torch_track_consistency.py` 强制（本轮 L2 手工对账两次实测撞闸：torchvision 移除破三件套同钉、torch 升版破三处一致，按备份还原后全绿）。
- **语义分工**：venv=开发环境（`2.15.0+cu134`，阶段 5/6 GPU 修复成果，收工硬判据逐字符保持）；lock=交付轨声明（2.13.0+cu132 轨）——两者**有意不同**，不构成漂移。
- **待所有者决策（各占一行，未顺手修）**：① torch 交付轨升版窗口——三处连动（锁+便携脚本版本与 SHA256+docker）后 lock 才能记 2.15.0；② edge-tts==7.3.1 是否进交付轨——联网依赖 vs 离线便携场景（L2 试加即触发 `--check-small`「便携清单缺失」红）。
- **③ 既有红已闭环（2026-11-08，代码侧）**：`sync_requirements --check` / `--check-small` 的三条红各有独立根因，不是同一个「漂移」——(a) `requirements.txt` 多 `munch>=4.0.0`：modelscope 的 `audio/vc（Starganv3.py）` 运行期 `from munch import Munch`，上游元数据没声明，于是它长期只活在 `requirements.txt` 里、不在 `pyproject`，`--check` 一直红；按仓规「requirements.txt 由 pyproject 生成、不手工编辑」，正解是把 `munch` 补进 `pyproject`（带原因注释）再重新生成清单。(b) `--check-small` 报 `filelock==4.2.1`、`pillow==13.0.0`：便携清单这两条钉版**早于 lock 的当前值**（lock 为 4.1.0 / 12.3.0），属 Dependabot 抬过 lock 却没回头同步便携清单的残留；改的是便携清单（对齐 lock，`cryptography` 那行的 GOTCHAS #98 注释保持不动）。(c) **为什么这红能躺一个月没人管**：`scripts/sync_requirements.py` 被 `.gitignore:293` 排除，干净 clone 和 CI runner 上根本没有这个脚本，`--check` / `--check-small` **从来没在 CI 跑过**——本地偶尔手跑一次才看得见。本轮把两条规则搬进 `tests/test_requirements_sync_gate.py`（CI 常规 pytest job 就会真跑），脚本仍可本地用；每条规则各配一条 `*_is_not_vacuous` 变异自证。验证：`--check` 「36 个依赖一致」、`--check-small` 「105 个钉装项对齐」、新闸 4 passed；负控三种形态各 1 红（pyproject 去掉 munch / 便携改 pillow 13.0.0 / 便携漏 `jinja2`），复原后哈希一致。**未做**：`requirements-lock.txt` 未重新 `pip-compile`（本轮零改动；工作区里那份「已对齐」的 lock 实为 `pip freeze` 产物，带进 ipython/matplotlib/werkzeug/tensorboard 等开发工具链，不属交付轨，已弃用并备份）；`munch>=4.0.0` 与 modelscope 的实际兼容性只在 venv 实测（venv 有 4.0.0），未做跨版本矩阵。
- 本轮 lock 文件**零改动**（尝试性编辑已按备份还原，`git diff` 空）；收工自证：全量套 **2603 passed / 0 failed**、venv 硬判据 `2.15.0+cu134 True 13.4` 逐字符一致。
- **WO-B25（2026-11-09）G4 影响面清单（只读，零版本改动）**：三处候选连动位里**被机器校验的硬钉只有两处**，docker 侧其实没钉——`requirements-lock.txt:100/102/103`（`torch==2.13.0` / `torchaudio==2.11.0` / `torchvision==0.28.0`）↔ `scripts/build_portable_bundle.ps1:42-44`（版本参数）、`:50-52`（三条 SHA256 权威值）、`:681-683`（wheel 文件名由版本参数拼出，版本与哈希不同步就报「钉版 wheel 缺失」）是唯一成对校验的一组（`tests/test_torch_track_consistency.py:31-41`）；**`Dockerfile:54/59` 装的是 `requirements.txt` 而不是锁**，而 `requirements.txt:2-4` 只有 `torch>=2.5.1` / `torchvision>=0.20.1` / `torchaudio>=2.5.1` 下限——镜像里的 torch 版本是构建时活解析的，与锁和便携轨都不是同一份钉。另：CI 各 job 一律 `pip install torch torchvision torchaudio --index-url .../cpu`（`ci.yml:163/179/193`、`benchmark.yml:49`、`e2e.yml:45`、`stress.yml:30`、`update-baselines.yml:35`），**没有任何 job 按锁装过**。→ 「三处同钉契约」这条旧表述按实测**更正为「两处硬钉 + docker 一处未钉」**：升版前必须先定 docker 侧怎么钉（本工单只出影响面，未做）。
- **目标版本已在源上可取（`pip index versions` 实测，2026-11-09）**：cu132 索引 `torch 2.15.0+cu132`、`torchvision 0.29.2+cu132`、`torchaudio 2.11.1+cu132`（**torchaudio 已有 +cu132 轮**，与 `build_portable_bundle.ps1:476-480`「cu132 索引实测无 torchaudio 轮子、改从 PyPI 取 CPU 轮」的注释已过期相矛盾——升版时要顺带决定 torchaudio 走哪个源）；本机 `.venv` 实装 `torch 2.15.0+cu132` / `torchvision 0.29.2+cu132` / `torchaudio 2.11.1+cu132`（Python 3.12.10、`torch.version.cuda=13.2`）。即上文「本轮 lock 文件零改动」那条记录的时点值「venv=**2.15.0+cu134**」与当前实装已不一致（疑为后续 F8 补装/环境重建后的变化，非本条改动所致），本轮以仪器输出为准记 `+cu132`：所谓「锁 vs venv 差异」实为**同一 cu132 构建通道上的 torch 小版本差**（锁 2.13.0 vs venv 2.15.0），**不是** cu134/cu132 两条构建线。
- **闸门有效性实测（不是只看代码）**：`tests/test_torch_track_consistency.py` **4 passed**；负控——把锁副本改成 `torch==2.15.0`/`torchvision==0.29.2`、脚本不动 → **红**并报出 `[('torch','2.15.0','2.13.0'), ('torchvision','0.29.2','0.28.0')]`；wheel 哈希门禁负控——新版本文件名配旧哈希 → `throw「wheel SHA256 不符」`；哈希参数置空 → `checked=0` **静默跳过**（排障口，CI 不可依赖）。**未做**：任何版本钉改动、镜像构建、显存复测。清单与回滚/验证命令全文见报告 `C:/Users/Doro/Documents/Trae-CN/temp_20261109_01/reports/WO-B25-tts-decisions.md`。
- **WO-B28（2026-11-09）G4 口径落定（所有者决策）**：**暂不升版（等 torch 2.13 cu128）；`edge-tts` 保持不入交付轨**（离线优先）。→ 上文 ①② 两项「待所有者决策」至此落定、不再挂账；升版前置条件仍是三处连动（锁 ↔ 便携脚本版本与 SHA256 ↔ docker 侧未钉，见 WO-B25 实测），`tests/test_torch_track_consistency.py` 继续钉住「锁 ↔ 便携脚本」两处硬钉。**本单零版本改动**：`requirements-lock.txt`、`scripts/build_portable_bundle.ps1`、`Dockerfile`、`version.json` 均未动。（注：所有者口径原文为「等 torch 2.13 cu128」，与上文记载的「2.13.0+cu132 轨」在 CUDA 小版本标注上不一致；本轮只落口径、不改钉，差异留待所有者复核。）

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
- 留痕：`_archive/执行记录/2026-11/2026-11-02-r4-real-history-rollback-drill.md`（含未覆盖清单与台账行）；演练目录 `C:/Users/Doro/Desktop/评估报告_2026-10-29/_演练/R4-real-history/` 保留作证据。
- 未随本轮收口的挂账项：F2（`v2.8.0` tag 内嵌版本位漂移，SRE_RUNBOOK §2.4 硬停断言对其判 FAIL）仍属所有者决策项，不在阶段 8 范围；脚本未实现 §2.4 断言（人工 SOP）亦登记为未覆盖。
- **WO-B19（2026-11-09）补 §2.4 可执行断言**：新增 `tools/check_rollback_assertions.py`（只读；`--worktree` / `--tag` / `--all-tags`，退出码 0/1/2），把「目标 tag ↔ `version.json $.version` ↔ `CHANGELOG.md` 顶部条目」及「内嵌 changelog 自述 = `$.version`」写成逐项 PASS/FAIL；配套 `tests/test_rollback_assertions.py` 9 项（正控 + tag/version/内嵌说明/CHANGELOG 顶部四形态负控 + 「`--tag` 读 tag 树不读工作树」）。**实测**：当前工作树 `2.9.0` 全 PASS（rc=0）；`--tag v2.9.0` **FAIL（rc=1）**——tag 内 `version.json` 的 `changelog` 自述仍是 `2.8.0`（新发现，非本轮改动引入）；`--tag v2.8.0` 亦 FAIL（自述 `2.7.0`，F2 已登记）；`--all-tags` 16 个 tag = 10 PASS / 6 FAIL（另 4 个为 `v2.0.1`/`v2.1.0`/`v2.2.0`/`v2.2.1` 无 `version.json`）。**口径冲突如实记录**：`docs/SRE_RUNBOOK.md` §2.4 原文自述「本轮不新建脚本」，与本工单「写成可执行脚本」冲突；按工单执行且**未改 runbook 文本语义**。

## F3 文档推荐入口死路径（`backup_db.py`）——已闭环（2026-11-02 阶段 9）：退役删除，推荐入口改指活链，四态 `[?]（待决策）→[x]`

- 实测判据：`git grep -l backup_db` 仅命中 `docs/SRE_RUNBOOK.md`、`_archive/执行记录/2026-11/2026-10-31-restore-drill.md`，`app/`、`tests/`、`.github/` 零引用；`data/tts_history.db` 不存在（`ls` 实测）。
- 根因（**推翻工单派单锚的「S-R4 合并所致」推测**）：`git show` 取证——`90e8512` 时 `config.yaml:58` 曾有 `db_path: data/tts_history.db`，`64a753f`（2026-08-16「prune legacy sections」）清理该段，`80c13b5`（2026-08-31）新增 `backup_db.py` 时照抄了这个**已死 15 天**的命名；S-R4 合并的是 `outputs/history.db`→`data/history.db`，与 `tts_history.db` 无涉。
- 处置：`git rm scripts/backup_db.py`；SRE_RUNBOOK §3.3 推荐入口改指 `scripts/backup_history_db.py`（有测试 `tests/test_backup_history_db.py`、2026-10-31 演练实测活链），并成文备份集覆盖口径；冷 shell 新进程原样跑文档命令 `.venv/Scripts/python.exe scripts/backup_history_db.py --keep 14` **exit 0**（12:18:44；**当时环境有效；该入口已于 2026-11-03 失效，见 F7**）。详见 `_archive/执行记录/2026-11/2026-11-02-o4-backup-wiring.md` §3/§4。

## F7 文档写死 `.venv` 解释器路径已成死路径——**已修复（2026-11-03 阶段 13）**：仓库唯一解析入口 + 冷 shell 实测 exit 0

- **事实（实测）**：项目 `.venv` 是指向 `D:\_venvs\tts` 的目录联接（junction），该目标目录**不存在**
  （`Test-Path D:\` True / `D:\_venvs` False）；`Test-Path .venv\Scripts\python.exe` = **False**（等价
  `test -x` = NO）。同机系统 `C:\Python312\python.exe`（3.12.10）可正常跑脚本——**坏的是「文档指定的入口」，
  不是能力**。
- **盘点**：tracked 文件内 `.venv` 出现 **105 处 / 86 行 / 38 文件**，其中**可执行入口 23 站点**（12 文档命令
  + 11 代码站点）。逐个站点的分类处置（改指 / 改解析 / 保留+加注 / 不动及理由）见留痕 §3。
- **处置**：新增 `scripts/run_python.py` 作为**仓库唯一解释器解析入口**（顺序：`TTS_PYTHON` 覆盖 → 项目 venv
  Windows/POSIX → PATH `python`/`python3` → `py -3` → 当前进程；候选**逐个实际执行探测**，退出码原样透传，
  解析不到 exit 127 并给修复指引）。`docs/SRE_RUNBOOK.md` §3.3 备份/恢复两条命令改指该入口；
  `scripts/install_backup_schedule.py` 的调度命令行改为解析结果（旧值会把死路径注册成每日任务，dry-run 曾
  打印 `[warn] 未找到 venv 解释器`）；`check_engine_compat/specs/declared_capabilities/training_isolation`
  四处重复的写死路径收敛到同一入口。
- **验收（唯一算数判据）**：冷 shell（新 `cmd.exe`，`VIRTUAL_ENV`/`CONDA_PREFIX`/`PYTHONPATH`/`TTS_PYTHON`
  全空）原样执行 `python scripts/run_python.py scripts/backup_history_db.py --keep 14` → **exit 0**
  （10:35:25，444 ms），产物 `data/backups/history_backup_20261103_103525.db` **2,469,888 B**、sha256
  `02c9d487…`；首跑 10:33:29 产物**同哈希**（逐字节一致），既有备份删除 **0** 份。`check_engine_compat.py`
  经入口与直跑系统 Python **输出逐字节一致**（sha256 同值 `49bd9fce…`，均 exit 1 = funasr 缺失 8/9）。
- **未覆盖（诚实边界）**：venv 未重建（硬约束）、符号链接未修；GPU 口径（阶段 5/6 的 `2.15.0+cu134`）无法复验；
  发版链脚本（`build_portable_bundle.ps1` / `release_gate.ps1`）与 `.githooks` 内部两级回退按「已有兜底」保留
  未收敛。
- 留痕：`_archive/执行记录/2026-11/2026-11-03-f7-venv-entrypoint-and-f8-hooks-drift.md`（含入口清单、冷 shell 日志、对照、
  两口径与零写入证据索引）。

## F8 本地 git 钩子未接线（`core.hooksPath=local/hooks`）——**已接线（2026-11-06，`core.hooksPath=.githooks`），断链闭合**

- **漂移事实（实测）**：`git config core.hooksPath` = **`local/hooks`**（来源 `file:.git/config`，本地改动、不入库）；
  该目录被 `.gitignore:388` 的 `/local/` 忽略，内只有 `pre-push` 与 `.origin`（内容 `.githooks`）。
  → `pre-push` 经 `local/hooks/pre-push` 转发到 `.githooks/pre-push` **仍生效**；`.githooks/` 下的
  `pre-commit`、`commit-msg`（DCO）、`post-merge`、`post-checkout`、`prepare-commit-msg` **全部休眠**。
  与 `scripts/install-hooks.ps1:5-6` 的成文口径（2026-09-16 起统一 `core.hooksPath .githooks`）冲突。
- **影响（量化）**：`local/hooks/pre-push` mtime = 2026-10-31 00:00:19，此后 **25 个提交**在本地 pre-commit 与
  DCO 校验缺席下产生；**25/25 仍带 `Signed-off-by`**（作者手工 `-s`）——事实未违反，**校验缺席**（本地 ruff /
  大文件 / 私钥 / 引擎一致性 / 上游同步等门禁在这 25 个提交上只有 CI 兜底）。
- **接线前置条件——已解除（2026-11-03 阶段 14 实测）**：手跑 `sh .githooks/pre-commit` 现为 **exit 0**
  （11:30:48→11:31:08，20s），`Engine Compatibility Check ... Passed`（**9/9**），其余实跑门禁 6/6 Passed；
  装前同命令 **exit 1**（8/9，红在 funasr）。**钩子仍未接线**：`git config core.hooksPath` 仍为 **`local/hooks`**
  （本轮未动，硬约束 4）。→ 剩余前置只有**属主授权动作**：`./.githooks/install.sh` 或
  `git config core.hooksPath .githooks`（接线会立刻开始拦提交，需属主知情）。
- **`funasr` 依赖面（阶段 14 已补装到系统 Python）**：`pyproject.toml:78` / `requirements.txt:7` / 锁 `==1.4.16`
  属**必装依赖**，三处同钉契约本轮**未改动**；
  **VoxCPM2 加载期硬依赖**（`model_manager_core/load.py:350` 无条件 `from funasr import AutoModel`，`:420` 加载
  ASR 子模型；缺则 `load()` `ModuleNotFoundError`）；`step-audio-editx` 依赖它做音频 tokenizer；辅助模型
  `SenseVoiceSmall`（ASR）/ `speech_zipenhancer`（降噪）经 funasr 通道。**不受影响**：`indextts2`、`indextts20`、
  `edge-tts`、`voicebox`、`breeze-tts2`（`engines/` 与 `vendor/` 内 funasr 零命中；缺 funasr 时三者 import 均 OK）。
  注意粒度：`funasr` 不是它们的 import 期依赖，而是 VoxCPM2 的**加载期**依赖——`check_engine_compat` 只测 import；
  阶段 14 补装后该项已 9/9（该门禁**不**证明权重能加载，只证明依赖可导入）。
- **阶段 14 实做（2026-11-03，`funasr` 已到位 / 钩子仍未接线）**：给系统 Python `C:\Python312\python.exe`
  （3.12.10）补装 `funasr==1.4.16`——先 `pip install --dry-run` 落盘，`Would install` **16 个包**且
  torch / torchaudio / torchvision / numpy / transformers **一个都不在改动列表**（numpy 2.4.6、transformers 5.16.1
  均为 `Requirement already satisfied`），闸门通过后才真装（**exit 0**，实装 16 个、`pip list` 304→320 行闭合）。
  **torch 未被改动的证据（四重）**：①`torch.__version__` = `2.11.0+cu128`、`torch.version.cuda` = `12.8` 逐字不变；
  ②`torch/` 目录 **4,415,065,323 B / 13,795 文件**装前装后同值；③6 个关键文件 sha256 全同（含 `torch_cuda.dll`
  811,555,840 B、`cublas64_12.dll` 113,716,224 B）；④`torch-2.11.0+cu128.dist-info/RECORD` sha256
  `69c1d26cfc4e5a1556004838ac1fb19c7806412c4c8db9193302335a60afc345` 同值。GPU 探测（fp32 2048² matmul
  0.209s + fp16 conv2d 0.109s）通过、`cuda.is_available()` 仍 True。**未做真加载**：`nvidia-smi` free
  **1,719 MiB** < 需 **6,609 MiB**（ComfyUI PID 31,096 占 10,226 MiB，零触碰）→ 按硬停口径止于 import 层，
  并用 `MetaPathFinder` 屏蔽 `funasr` 的反向对照证明探针有分辨力。两口径 A **2566/0/116** ×3、B **2469/0/90**
  （123 deselected，cov **51.59%**）全 exit 0——相对阶段 13 **+2 passed / −2 skipped**，归因实测为 funasr 依赖树
  带入 `jieba==0.42.1`（`tests/test_fts_tokenizer.py` 恰有 2 个 `_check_jieba()` 门控用例，现 7 passed/0 skipped）。
  活体库零增长：`history.db` 2,469,888 B / sha `051d1688…`、333 行、`MAX(ts_ms)` 1793594173715 测前测后逐字节一致。
  留痕：`_archive/执行记录/2026-11/2026-11-03-f8-funasr-install.md`。
- **属主修复命令（本轮未执行，硬约束禁改 `core.hooksPath`）**：`./.githooks/install.sh` **或**
  `git config core.hooksPath .githooks`；⛔ **勿用** `scripts/install-hooks.ps1`（已退役墓碑，指向不存在的
  `scripts/git-hooks`，会让钩子静默失效）。
- **接线完成（2026-11-06，属主工单授权）**：`sh .githooks/install.sh` → `git config core.hooksPath` 回读 **`.githooks`**；
  主仓 `sh .githooks/pre-commit`（pre-commit 框架全链）exit 0；fresh clone（本机临时克隆）out-of-box 钩子
  不生效（`core.hooksPath` 本地配置不入库，属 git 机制）→ 同一 install.sh 一步引导即生效，commit-msg DCO
  门禁无签 **exit 1** / 带签 **exit 0** 双向实测；全链真实提交首轮被 `Engine Compatibility Check` 8/9 **正确拦截**
  （funasr 现仅存于主仓 2026-11-05 重建的 `.venv`——系统 `C:\Python312` 实测 `ModuleNotFoundError`，阶段 14
  「装进系统 Python」口径已被 venv 重建动作超越），克隆具备项目 venv（文档化 onboarding 终态，实测以 junction
  指向主仓 venv 代演，零安装零联网）后 12 项钩子全 Passed、提交 `96215e5` 带 SOB（作者=签署者）。
  断链窗口 **31 笔**提交（报告口径 29 + 后续 2 笔）**不重写不补钩**（rebase 禁改；逐笔 trailers 实测 31/31 全带
  `Signed-off-by`——报告点名的 ea27533/b49297e 缺签两笔已以同主题重建为 34ef17f/043f200 且带签，内容逐字节一致）；
  `local/hooks/` 原样保留未删（inert，回滚 = `git config core.hooksPath local/hooks`）。
- 留痕：`docs/SRE_RUNBOOK.md`「治理与勘误速查」F8 条 + `_archive/执行记录/2026-11/2026-11-03-f7-venv-entrypoint-and-f8-hooks-drift.md` §6
  \+ `_archive/执行记录/2026-11/2026-11-03-f8-funasr-install.md`（阶段 14：装前/装后对照、dry-run、torch 指纹、未覆盖清单）
  \+ `_archive/执行记录/2026-11/2026-11-06-f8-hooks-wiring.md`（接线动作、主仓/fresh clone 双向验证、环境演进登记）。

---

## 迁移记录

- 迁移段数：10
- 原文总字节（`docs/TODO.md` @ `ed6c3dc` 全文）：43282 B
- 迁移段原文合计字节：24815 B
- 归档文件字节：25482 B
- `docs/TODO.md` 迁移前字节：43282 B
- `docs/TODO.md` 迁移后字节：21544 B
