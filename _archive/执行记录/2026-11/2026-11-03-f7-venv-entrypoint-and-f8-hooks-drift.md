# F7 文档解释器入口失效 · F8 本地钩子接线漂移 — 阶段 13 留痕（2026-11-03）

> 工单：整改阶段 13——把「文档指定的入口」变成「真的能跑的入口」（F7），并把「本地 git 钩子未接线」
> 登记成仓库内可追踪缺陷（F8，只登记不修）。
> 执行分支 `fix/desktop-shell-csrf-and-commands`；起测 HEAD `051624b`；起测 `git status --porcelain` 空。
> 解释器：系统 `C:\Python312\python.exe`（3.12.10）；项目 `.venv` 本轮**未触碰**（不删不建不改，硬约束 1）。
> 证据目录：`C:/Users/Doro/Desktop/评估报告_2026-10-29/_演练/F7-f8/`（下文以 `_演/` 指代，仓外、不入库）。

## 1. 结论速览

| 项 | 结论 |
|---|---|
| **F7** | **已闭环**：新增仓库唯一解析入口 `scripts/run_python.py`；`docs/SRE_RUNBOOK.md` §3.3 备份/恢复两条命令改指该入口；冷 shell 原样复跑 **exit 0**，产出 `data/backups/history_backup_20261103_103525.db` **2,469,888 B**、sha256 `02c9d487d30be050bab5e204865fa5827b8132732e50322e1b2fde75c065a8ce` |
| **F8** | **已登记、未修**：`git config core.hooksPath` = `local/hooks`（只有 `pre-push` + `.origin`），`.githooks/` 下 `pre-commit`、`commit-msg`(DCO)、`post-merge`、`post-checkout`、`prepare-commit-msg` 全部休眠；手跑 `sh .githooks/pre-commit` **exit 1**（`Engine Compatibility Check`，8/9，`funasr` 缺失）→ 接线后会立刻拦提交。登记见 `docs/TODO.md` F8 条 + `docs/SRE_RUNBOOK.md`「治理与勘误速查」 |
| 入口盘点 | tracked 文件内 `.venv` 出现 **105 处 / 86 行 / 38 文件**；其中**可执行入口 23 站点**（12 文档命令 + 11 代码）——逐站点分类处置见 §3 |
| 能力未变 | 本轮只修「入口不可执行」，**不重建 venv、不装任何包**；GPU/引擎能力口径与 `.venv` 是否可达无关 |
| 两口径全量 | A **2564 passed / 0 failed / 118 skipped**（×2：经入口 120.73s、直跑 119.13s）、B **2467 / 0 / 92（123 deselected）** cov **51.58%**≥45%（159.60s），**全 exit 0** |
| 活体零写入 | 测试前后 `data/history.db` 2,469,888 B / sha256 `051d1688…` 与 `action_logs` 333 行 / `MAX(ts_ms)` 1793594173715 **逐字节不变** |

## 2. F7 根因取证（本机复验，非引用）

| 事实 | 实测值 |
|---|---|
| `.venv` 类型 | 目录联接（`Mode=l----`、`LinkType=Junction`、`Target=D:\_venvs\tts`；`Get-Item .venv` 的 `LastWriteTime` 2026-10-31 20:43:09） |
| 目标目录 | `Test-Path D:\` = **True**；`Test-Path D:\_venvs` = **False** |
| D: 盘顶层残留 | `$RECYCLE.BIN, model, need load, other, qm, short-films, System Volume Information, DumpStack.log.tmp, NoctQ_V4_int8_convrot.safetensors, pagefile.sys`（无 `_venvs`） |
| 解释器可执行性 | `Test-Path .venv\Scripts\python.exe` = **False**（等价于工单的 `test -x` = NO） |
| 兜底解释器 | `python` → `C:\Python312\python.exe`，3.12.10（`where python` 首条） |
| 判定 | **坏的是「文档写死的入口」，不是能力**：`.venv` 目录还在（`Test-Path .venv` = True），所以任何「文件存在即用」的旧逻辑都看不出异常；只有真去执行才会失败 |

## 3. 可执行入口清单与分类处置（23 站点）

计数口径（可复算）：`git grep -o "\.venv"` = **105 处**，分布在 **38 个 tracked 文件**、**86 行**；
「可执行入口」= 该行把 `.venv` 下的 python 当作解释器用于**执行 / 派生进程 / 写入调度命令**。
按此口径共 **23 站点 = 12 个文档命令 + 11 个代码站点**（`_演/01-venv-entrypoint-inventory.tsv` 为**处置前**逐行原始清单，
`_演/01b-executable-entry-sites.tsv` 为这 23 个站点的逐条分类与处置）。

> 与派单方口径（43 处 / 约 14 文件 / 16 处可执行）对账：本机复测是 105 处 / 38 文件 / 23 站点。
> 差异来自口径而非事实分歧——派单方数字更接近「文档 + 脚本里出现的行」，本清单按「行内出现次数」
> 与「是否用于执行」两条独立维度重算，并把结论逐条列出，便于复核；**不沿用未复现的数字**。

### 3.1 文档命令（12 站点）

| # | 站点 | 处置 | 说明 |
|---|---|---|---|
| 1 | `docs/SRE_RUNBOOK.md:117`（§3.3 每日备份） | **改指解析入口** | 现行推荐命令，必须能跑 → `python scripts/run_python.py scripts/backup_history_db.py --keep 14` |
| 2 | `docs/SRE_RUNBOOK.md:119`（§3.3 恢复） | **改指解析入口** | 同上 |
| 3 | `docs/SRE_RUNBOOK.md:154`（§3.4 阶段 9 台账行） | **保留 + 加注** | 历史数字不改；注「当时环境有效，该入口现已失效（F7）」 |
| 4 | `docs/SRE_RUNBOOK.md:155`（§3.4 阶段 10 台账行） | **保留 + 加注** | 同上 |
| 5 | `docs/TODO.md:52`（F3 条目的历史证据） | **保留 + 加注** | 同上 |
| 6 | `docs/runbooks/2026-11-01-o3-failover-drill.md:128` | 保留原样 | 演练留痕（当时 `Start-Process` 直调成功）；属历史证据 |
| 7 | `docs/runbooks/2026-11-02-f6-test-db-isolation.md:15` | 保留原样 | 阶段 10 备份命令留痕 |
| 8 | `docs/runbooks/2026-11-02-o4-backup-wiring.md:83` | **保留 + 加注** | 该文是备份链当前口径的详版，读者最可能照抄 → 命令块下加「入口已失效」一行 |
| 9 | `docs/runbooks/2026-11-02-o4-backup-wiring.md:99` | **保留 + 加注** | 同上 |
| 10 | `docs/runbooks/2026-11-02-o4-backup-wiring.md:147` | 保留原样 | 调度 dry-run 的历史**输出**，改了就是伪造记录 |
| 11 | `docs/runbooks/2026-11-02-r4-real-history-rollback-drill.md:62` | 保留原样 | 阶段 8 回滚实跑记录 |
| 12 | `docs/runbooks/2026-11-02-r4-real-history-rollback-drill.md:70` | 保留原样 | 同上（解释器说明句） |

### 3.2 代码站点（11 处）

| # | 站点 | 类别 | 处置 |
|---|---|---|---|
| 13-15 | `.githooks/pre-commit:20`、`pre-commit-lite:48`、`pre-push:107` | 钩子内两级回退（`.venv/Scripts` → `.venv/bin` → PATH） | **不动**——这就是被收敛的**原型**，本身已含兜底 |
| 16-19 | `scripts/check_engine_compat.py:43`、`check_engine_specs.py:54`、`check_declared_capabilities.py:34`、`check_training_isolation.py:34` | 脚本内路径拼接（venv 自举 `os.execv`） | **改用唯一入口** `run_python.venv_python()`：顺带获得 POSIX 布局支持，四处重复的写死路径归零 |
| 20 | `scripts/install_backup_schedule.py:40` | 路径拼接 → **写入 schtasks 注册命令** | **改为解析结果**：旧值会把死路径注册成每日任务（dry-run 已打印 `[warn] 未找到 venv 解释器`，是新证据） |
| 21 | `scripts/build_portable_bundle.ps1:801` | 构建期回退（无 venv 时退系统 python） | **不动**——已有 `Test-Path` 回退，且属发版链（硬约束 6 禁区相邻） |
| 22 | `scripts/release_gate.ps1:39` | 发布闸回退（同上） | **不动** |
| 23 | `start.bat:16-18` | 启动器首选 venv | **不动**——其后有系统 python / WinPython 四级回退（`:27-108`） |

### 3.3 非可执行引用（63 行，保留）

忽略规则（`.gitignore:356`、`.dockerignore:2`、`.github/layout-rules.yaml:37-38`）、散文/注释（`pyproject.toml`
的版本决策注释、`CHANGELOG.md` 历史条目、`desktop/README.md`、`tests/` 说明串等）**全部保留**：
它们描述的是「这台机器曾经/应该有什么」，不是「照着敲这条命令」。删掉它们等于抹掉环境证据。

## 4. 处置：仓库唯一解释器解析入口 `scripts/run_python.py`

解析顺序（**逐个实际执行探测**，不只看 `exists()`）：

1. `TTS_PYTHON` 环境变量（显式覆盖：设置即只认它，探测失败**直接报错**，不静默回退）
2. 项目 `.venv/Scripts/python.exe`（Windows；目标失效时自动跳过）
3. 项目 `.venv/bin/python`（POSIX）
4. PATH 上的 `python`
5. PATH 上的 `python3`
6. Windows `py -3` 启动器
7. 当前进程解释器（最后安全网）

判定与契约：

- 探测 = 真启动一次并检查 `sys.version_info >= (3, 10)`；「文件存在但跑不起来」会被跳过（正是本次事故形态）；
- `--print` / `--explain` / `--list` 三个自检模式，输出解释器路径、命中来源、各候选探测结果；
- 退出码**原样透传**；解析不到 → exit **127** 并打印修复指引（不把「没解析到」写成「已通过」）；
- 目标脚本相对路径先按 CWD、再按仓库根解析，文档命令不依赖当前目录。

**本轮实测到的 Windows 坑（已修，留档）**：`os.execv` 在 Windows 上经 `_P_OVERLAY` 会**丢失目标退出码**
（目标 `sys.exit(7)` → 入口 exit 0，且父进程立即返回），第一版对照因此出现「输出逐字节相同、退出码却 0 vs 1」。
现按平台分流：POSIX 用 `os.execv` 替换进程，Windows 用子进程回传退出码；回归守卫
`tests/test_run_python_entry.py::test_exit_code_is_propagated_verbatim`。

同源改动（均在 F7 爆炸半径内，不越界）：

- `scripts/install_backup_schedule.py`：`VENV_PY`（写死）→ `PYTHON_EXE`（解析结果）+ `PYTHON_PREFIX_ARGS`；
  dry-run 的 `python_exists` 改为 `interpreter_exists`，告警文案改为「未找到可用解释器」；
- `tests/test_install_backup_schedule.py`：断言随常量改名同步（**仅此一处语义改动**）；
- **顺带发现的既有红灯（已在本文披露）**：`scripts/install_backup_schedule.py` 与 `tests/test_install_backup_schedule.py`
  在 **HEAD `051624b` 上就不满足 CI 钉版 `ruff 0.16.5` 的 `ruff format --check`**（实测：把 HEAD 版两个文件
  取到仓外、用 pre-commit 缓存里的 ruff 0.16.5 复检 → `2 files would be reformatted`）。本轮按 **0.16.5**（不是
  本机 PATH 的 0.16.2）把这两个文件格式修平；**改动的可分离性有实测审计**：把 HEAD 版按 0.16.5 格式化后与工作区版
  逐行比较（`_演/12-format-churn-audit.txt`），剩余差异**只有**上述语义行（`VENV_PY`→`PYTHON_EXE`、`python_exists`→
  `interpreter_exists`、告警文案、新增 `_BOOTSTRAP` 三行、测试断言与注释），其余全部为 formatter 重排；修平后
  `ruff check app/integrated_app/ scripts/ tests/` = All checks passed、`ruff format --check …` = `437 files already formatted`；
- `tests/test_run_python_entry.py`（新增 20 项）：覆盖候选顺序、探测跳过、覆盖变量不静默回退、
  CLI 三个自检模式、真入口冒烟、退出码透传。

## 5. 冷 shell 实跑（唯一算数判据）

**命令原文**（修订后 SRE_RUNBOOK §3.3 第一行，一字不差）：

```text
python scripts/run_python.py scripts/backup_history_db.py --keep 14
```

**冷 shell 条件**：新 `cmd.exe` 子进程（`cmd.exe /c`），未激活任何 venv，未预设 python 相关环境变量。
实测 `VIRTUAL_ENV=[]`、`CONDA_PREFIX=[]`、`PYTHONPATH=[]`、`TTS_PYTHON=[]`；`where python` 首条 =
`C:\Python312\python.exe`。

| 项 | 实测值（`_演/02-cold-shell-backup.log`） |
|---|---|
| 起始 / 结束 | 2026-11-03 **10:35:25** / 2026-11-03 **10:35:25**（耗时 444 ms） |
| **退出码** | **0** |
| 产物 | `data/backups/history_backup_20261103_103525.db` |
| 字节数 | **2,469,888 B** |
| sha256 | **`02c9d487d30be050bab5e204865fa5827b8132732e50322e1b2fde75c065a8ce`** |
| 既有备份物 | 9 → 10（首跑 10:33:28→10:33:29、419 ms、`history_backup_20261103_103329.db` 与本次**同 sha256**，逐字节一致）→ 本轮共 11 份，**删除 0 份**、既有文件零覆盖（`--keep 14` 未触发轮转） |

**恢复命令同样实跑**（§3.3 第二行，目标仅仓外临时目录）：

```text
python scripts/run_python.py scripts/backup_history_db.py --restore data/backups/history_backup_20261103_103525.db --target <仓外>/restore_tmp/restored_history.db
→ exit 0，产物 2,469,888 B（脚本内置恢复后 PRAGMA integrity_check，不通过会抛错退非 0）
```

**对照：`scripts/check_engine_compat.py` 经入口 vs 直接系统 Python**（`_演/03-engine-compat-control.txt`）

| 口径 | 命令 | 退出码 | 输出 sha256 | 耗时 |
|---|---|---|---|---|
| 经入口 | `python scripts/run_python.py scripts/check_engine_compat.py` | **1** | `49bd9fce0a2c5978b25139192251fb4dec85ef2fbd3690ba10898f06c26b01e1` | 6210 ms |
| 直跑 | `python scripts/check_engine_compat.py` | **1** | `49bd9fce0a2c5978b25139192251fb4dec85ef2fbd3690ba10898f06c26b01e1` | 6604 ms |

`OUTPUT_IDENTICAL = True`、`EXIT_EQUAL = True`（exit 1 的原因是 `funasr` 缺失 8/9，与本轮改动无关，见 §6.4）。

**解析入口自检输出**（同一次取证）：

```text
$ python scripts/run_python.py --print
C:\Python312\python.EXE
$ python scripts/run_python.py --explain
C:\Python312\python.EXE  <- PATH:python
$ python scripts/run_python.py --list
[OK  ] C:\Python312\python.EXE  <- PATH:python
[OK  ] C:\Program Files\PyManager\python3.EXE  <- PATH:python3
[OK  ] C:\WINDOWS\py.EXE -3  <- py-launcher
[OK  ] C:\Python312\python.exe  <- current-process
```

注：`.venv` 的两个候选在 `--list` 里**不出现**，因为失效联接不满足可执行判定——这正是设计意图（跳过而非报错）。

**调度命令同源验证**（`_演/07-schedule-dryrun.txt`，只 dry-run、未注册）：

```text
完整命令行 : "C:\Python312\python.EXE" "...\scripts\backup_history_db.py" --keep 14
[同一条 dry-run] 同名任务探测 : 不存在 → --apply 时将新建唯一任务
```

旧版这里会是 `"...\.venv\Scripts\python.exe"`（并打印 `[warn] 未找到 venv 解释器`）——即注册出来就不可执行。

## 6. F8：本地 git 钩子接线漂移（登记，未修）

### 6.1 事实（`_演/05-env-and-hooks-facts.txt`）

| 项 | 实测 |
|---|---|
| `git config core.hooksPath` | **`local/hooks`**（来源 `file:.git/config`） |
| `local/hooks/` 内容 | `.origin`（内容 **`.githooks`**）、`pre-push`（338 B，转发到 `.githooks/pre-push` 后再跑家族台账脚本） |
| `local/hooks` 是否入库 | 否——`.gitignore:388` 规则 `/local/` |
| `.githooks/` tracked 文件 | `README.md, commit-msg, install.sh, lib-depwatch.sh, post-checkout, post-merge, pre-commit, pre-commit-lite, pre-push, prepare-commit-msg`（10 个） |
| **休眠的钩子** | `pre-commit`、`commit-msg`（DCO）、`post-merge`、`post-checkout`、`prepare-commit-msg` |
| **仍生效的钩子** | `pre-push`（经 `local/hooks/pre-push` 委托） |
| 与成文口径冲突 | `scripts/install-hooks.ps1:5-6` 的墓碑明写「自 2026-09-16 起本仓统一 `core.hooksPath .githooks`」；实际指向 `local/hooks` |

### 6.2 影响面（可量化）

`local/hooks/pre-push` 的 mtime 是 2026-10-31 00:00:19，按此时间点统计：**25 个提交**在该漂移下产生
（`git log --since="2026-10-31 00:00:20"`）。其中 **25/25 仍带 `Signed-off-by`**（作者手工 `-s`），
即 **DCO 事实未被违反，但校验缺席**；同理 pre-commit 的 ruff / 大文件 / 私钥 / 引擎一致性等检查在这
25 个提交上**只在 CI 兜底，本地未跑**。

### 6.3 接线前置条件（先解决再接线）

手跑 `sh .githooks/pre-commit`（2026-11-03 10:29:13→10:29:31，**exit 1**，`_演/04-hook-precommit-manual-run.log`）：

```text
Engine Compatibility Check...............................................Failed
- hook id: check-engine-compat
- exit code: 1
[FAIL] funasr         import 失败: No module named 'funasr'
总体：8/9 通过
```

其余门禁当日全过：`Engine Specs Consistency Check` / `Training Isolation AST Gate` /
`Declared Capabilities Consistency Check` / `Integrity Manifest Sync Check` /
`Upstream Vendor Sync Check (offline)` / `Structure Guard (root layout)` = **Passed**；
文件类钩子因无暂存文件而 Skipped。

**结论：现在接线会立刻开始拦提交**（每个提交都红在 `Engine Compatibility Check`）。故本轮**只登记不接线**（硬约束 1 亦禁止改 `core.hooksPath`）。

### 6.4 `funasr` 依赖面（只读取证，未安装）

| 结论 | 证据 |
|---|---|
| **必装依赖（非 extra）** | `pyproject.toml:78` `"funasr>=1.0.0"` 位于 `[project].dependencies`；`requirements.txt:7`；锁 `requirements-lock.txt:30` `funasr==1.4.16`；便携清单 `launcher/requirements-small.txt:35` |
| **VoxCPM2 加载期硬依赖** | `app/integrated_app/model_manager_core/load.py:350` `from funasr import AutoModel`（VoxCPM2 加载流程内**无条件**执行，用于 ASR 子模型；`:420` `AutoModel(model=get_voxcpm2_asr_path(), …)`）。缺它 → VoxCPM2 `load()` 直接 `ModuleNotFoundError`（与 O3 演练 2026-11-01 的现象一致） |
| **step-audio-editx 依赖** | `app/integrated_app/engines/step_audio_editx_engine.py:22-27` 依赖清单含「funasr（音频 tokenizer）」；`:257` 失败文案点名 `vllm, funasr, cosyvoice, torchaudio` |
| **辅助模型通道** | `docs/model-cards/SenseVoiceSmall.yaml`（ASR）、`docs/model-cards/speech_zipenhancer.yaml`（降噪）均标注「经 funasr 调用」 |
| **不受影响** | `indextts2`、`indextts20`、`edge-tts`、`voicebox`、`breeze-tts2`：`git grep -n funasr -- app/integrated_app/engines app/integrated_app/vendor` 除上述 step-audio-editx 两行外**零命中**；实测 `check_engine_compat.py` 在缺 funasr 的系统 python 下这三个本地引擎**import 全部 OK**（8/9） |
| **粒度差异（重要）** | `funasr` 不是这 3 个引擎的**import 期**依赖，而是 VoxCPM2 的**加载期**依赖：`check_engine_compat` 只测 import，会给出「VoxCPM2 OK」的绿灯；真加载才失败 |

**给属主的决策输入**：装 `funasr`（及其传递依赖 modelscope/torch 生态）才能恢复 VoxCPM2 的真实加载能力；
不装则 VoxCPM2 不可用、IndexTTS 系与 edge-tts / voicebox / breeze-tts2 不受影响。本轮按硬约束**不装**。

### 6.5 给属主的确切修复命令（本轮未执行）

```bash
# 二选一（等价；第一条是成文口径）
./.githooks/install.sh
git config core.hooksPath .githooks

# 接线前先确认前置条件已解决（当前会 exit 1）
sh .githooks/pre-commit        # 期望 exit 0；现在红在 Engine Compatibility Check（funasr）
# ⚠️ 不要运行 scripts/install-hooks.ps1：已退役（内容级墓碑，指向不存在的 scripts/git-hooks）
```

## 7. 两口径全量测试与零写入（`_演/11-test-runs-summary.txt`）

| 口径 | 命令要点 | 结果 | 耗时 | exit |
|---|---|---|---|---|
| A（经入口） | `python scripts/run_python.py -m pytest tests/ -v --tb=short --ignore=tests/e2e` | **2564 passed / 0 failed / 118 skipped** | 120.73s（含入口 124s） | 0 |
| A（直跑） | `python -m pytest tests/ -v --tb=short --ignore=tests/e2e` | **2564 / 0 / 118** | 119.13s（124s） | 0 |
| B（覆盖率） | 同 A + `-m "not integration and not gpu and not cuda and not vram and not benchmark and not stress" --cov --cov-fail-under=45` | **2467 / 0 / 92，123 deselected**，cov **51.58%** ≥ 45% | 159.60s（166s） | 0 |

三次运行的时间窗：A1 10:49:08→10:51:12、A2 10:51:12→10:53:16、B 10:53:16→10:56:01（**最终树**，含两处历史格式债按 CI 钉版 ruff 0.16.5 修平后的状态）。

对账（相对阶段 12 的 2545/0/117 与 2448/0/91）：**+19 passed / +1 skipped**，正好等于新增
`tests/test_run_python_entry.py` 的 20 项（19 过 + 1 项 POSIX-only 用例在 Windows 跳过）——**零回归**。

**活体零写入**（`_演/06-db-pre-tests.txt` 与 `06c-db-post-tests-final.txt` 逐字节对比）：`history.db`
2,469,888 B / sha256 `051d16889e0e90de576852b2e49ef1109913f1656ad9fa5b5fdce6821e200158`、
`-wal` 0 B、`-shm` 32,768 B（内容 sha 相同，仅 mtime 变）、`action_logs` **333** 行、
`MAX(ts_ms)` **1793594173715**、`generation_versions.db` 65,536 B / sha256 `41ab2af7…` —— 测前测后**全部一致**，
`integrity_check=ok`。

## 8. 历史数字保留声明（反「抹证据」）

阶段 8/9/10/12 的台账行里 `.venv/Scripts/python.exe` 的**当时**用法与数字（备份 2,408,448 B / sha `874d8447…`、
`a845230c…`、2,469,888 B / sha `e4394184…`、`051d1688…` 等）**一字未改**，只在 §3.3/§3.4 相关位置加注：

> **当时环境有效，该入口现已失效**（`.venv` 联接目标 `D:\_venvs\tts` 自 2026-11-03 起不存在，见 F7）。

本轮新增的备份物与既有的 9 份并存（`--keep 14` 未触发轮转），既有备份**删除 0 份、覆盖 0 份**。

## 9. 未覆盖清单（如实）

1. **`venv` 未重建、符号链接未修**（硬约束 1：不删不建不改）——现只是「有兜底」，不是「恢复原环境」；
   GPU torch 版本口径（阶段 5/6 的 `2.15.0+cu134`）**无法在本轮复验**，因为该 venv 不可达。
2. **钩子未接线**（硬约束 1：不改 `core.hooksPath`）——F8 只登记；接线前必须先解决 `funasr`。
3. **`funasr` 未安装**——VoxCPM2 的真实加载能力因此仍不可验证；只做了依赖面取证。
4. **计划任务未注册**——`install_backup_schedule.py` 只跑 `--dry-run`，真实注册待所有者授权。
5. **异地副本未解决**——需第二存储位置（资源决策），沿阶段 9 不变。
6. **发版链脚本未统一**（`build_portable_bundle.ps1` / `release_gate.ps1`）：它们已有回退逻辑，属发版链
   禁区相邻，本轮不动；若要「全仓零写死 `.venv`」，应另开一轮并有发版回归窗口。
7. **`.githooks/` 内部解析未收敛**：三个钩子仍自带两级回退（功能正确）；若也要「单一入口」，需先保证钩子在
   `bash`/`sh` 下可调用 Python 入口，属钩子改造，与本轮 F8「只登记不修」冲突，未做。
8. **Windows `os.execv` 行为**：只在本机（3.12.10 / win32）实测；POSIX 分支未在本轮真跑（CI 会在下一次 push 覆盖）。

## 10. 证据索引与复现

`_演/F7-f8/`：

| 文件 | 内容 |
|---|---|
| `01-venv-entrypoint-inventory.tsv` | `.venv` 逐行清单（38 文件 / 86 行 / 105 处）+ 可执行判定 |
| `02-cold-shell-backup.log`、`02a/02b-backups-*.txt`、`02-cold-shell-backup.ps1` | 冷 shell 实跑（命令原文、环境变量、退出码、字节、sha256、前后备份清单） |
| `03-engine-compat-control.txt`、`03a/03b-*.txt` | 经入口 vs 直跑的对照输出与哈希 |
| `04-hook-precommit-manual-run.log` | `sh .githooks/pre-commit` 手跑（exit 1 原文） |
| `05-env-and-hooks-facts.txt` | `.venv` 联接 / D: 盘 / 钩子接线 / `funasr` 取证原始输出 |
| `06-db-pre-tests.txt`、`06b/06c-db-post-tests*.txt`、`scripts/snapshot_db.py` | 活体库测前/测后只读快照（`06c` 为最终树复跑后的收口快照） |
| `07-schedule-dryrun.txt` | 调度 dry-run（新命令行） |
| `08-runA1-via-entry.log`、`09-runA2-direct.log`、`10-runB-cov.log`、`11-test-runs-summary.txt` | 两口径全量日志与计时 |
| `coverage.xml`、`htmlcov/` | 口径 B 覆盖率产物（仓外） |
| `12-format-churn-audit.txt`、`scripts/format_churn_audit.py` | 格式改动可分离性审计（HEAD 经 0.16.5 格式化后 vs 工作区） |

复现（任一条都可独立执行）：

```bash
python scripts/run_python.py --list                                   # 候选与探测结果
python scripts/run_python.py --print                                  # 解析结果
python scripts/run_python.py scripts/backup_history_db.py --keep 14   # 冷 shell 验收命令
python scripts/run_python.py -m pytest tests/test_run_python_entry.py -q
```
