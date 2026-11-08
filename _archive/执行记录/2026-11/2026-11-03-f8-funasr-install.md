# F8 前置条件 — 系统 Python 补装 funasr 1.4.16 — 阶段 14 留痕（2026-11-03）

> 工单：整改阶段 14——给本机**系统 Python**（`C:\Python312\python.exe`，3.12.10）补装 `funasr==1.4.16`，
> 恢复 VoxCPM2 的加载期依赖，解除 F8「钩子接线前置条件」的红灯，并把结果如实登记入库。
> 执行分支 `fix/desktop-shell-csrf-and-commands`；起测 HEAD `20d13d6`；起测 `git status --short` 空。
> 执行解释器：系统 `C:\Python312\python.exe`（3.12.10，`MSC v.1943 64 位`）；项目 `.venv` **全程未触碰**
> （不删、不建、不改符号链接，硬约束 1）。
> 证据目录：`C:/Users/Doro/Desktop/评估报告_2026-10-29/_演练/F8-funasr/`（下文以 `_演/` 指代；**仓外、不入库**）。
> 时间戳精度到秒，退出码原样记录，数字全部为实测值。

## 1. 结论速览

| 项 | 结论 |
|---|---|
| **torch 未被改动** | **成立（四重证据）**：版本 `2.11.0+cu128` 逐字不变；`torch/` 目录 **4,415,065,323 B / 13,795 文件**装前装后同值；6 个关键文件 sha256 全同（含 `torch_cuda.dll` 811,555,840 B）；`torch-2.11.0+cu128.dist-info/RECORD` sha256 `69c1d26cfc4e5a1556004838ac1fb19c7806412c4c8db9193302335a60afc345` 同值 |
| **GPU 仍可用** | `torch.cuda.is_available()` = **True**；最小探测 `fp32 2048×2048 matmul` **0.209s**、`fp16 conv2d 1×3×512×512` **0.109s** 均通过（装前 0.277s / 0.226s）；设备 `NVIDIA GeForce RTX 5070 Ti Laptop GPU`，capability `(12, 0)` |
| **engine-compat** | **9/9 通过、exit 0**（装前 8/9、exit 1）；经入口与直调系统 Python 输出 **sha256 同值** `c2e5b041d61318fb8f45367e23524586c03b2ac826477a416fb4f73f7426b7c6`，两者 exit 0 |
| **pre-commit 复跑** | `sh .githooks/pre-commit` **exit 0**（11:30:48→11:31:08，20s），`Engine Compatibility Check ... Passed`，其余 6 个实跑门禁 Passed；装前同命令 exit 1 |
| **两口径全量** | A **2566 passed / 0 failed / 116 skipped**（经入口 104.21s、直跑 98.20s、文档改完终跑 95.92s，三跑均 exit 0）、B **2469 / 0 / 90（123 deselected）** cov **51.59%** ≥45%（191.23s，exit 0）——相对阶段 13 基线 **+2 passed / −2 skipped**，成因已定位到 jieba（§7） |
| **活体库零增长** | `data/history.db` **2,469,888 B / sha256 `051d1688…`**、`-wal` 0 B、`-shm` 32,768 B / sha256 `fd4c9fda…`、`action_logs` **333 行**、`MAX(ts_ms)` **1793594173715**、`generation_versions.db` 65,536 B / sha256 `41ab2af7…`——测前测后**逐字节一致**（`-shm` 仅 mtime 刷新，内容 sha256 同值） |
| **未做真加载** | **未做**，原因为显存不足：`nvidia-smi` 实测 free **1,719 MiB** < 需 **6,609 MiB**；ComfyUI（PID **31,096**）占 10,226 MiB 总用量，**零触碰** |
| **钩子仍未接线** | `git config core.hooksPath` 仍为 **`local/hooks`**（未动）；本轮只解除前置条件，接线与否待派单方 |

## 2. 装前状态（`_演/02-env-BEFORE.json`、`_演/01-pip-list-BEFORE.txt`）

| 项 | 实测值（2026-11-03 11:27:33） |
|---|---|
| 解释器 | `C:\Python312\python.exe`，`3.12.10 (tags/v3.12.10:0cc8128, Apr  8 2025, 12:21:36) [MSC v.1943 64 bit (AMD64)]` |
| `site-packages` | `C:\Python312`、`C:\Python312\Lib\site-packages` |
| torch | **`2.11.0+cu128`**，`torch.version.cuda` = **`12.8`**，`cuda.is_available()` = **True**，cuDNN **91900** |
| torchaudio / torchvision | `2.11.0+cu128` / `0.26.0+cu128` |
| numpy / transformers | **`2.4.6`** / **`5.16.1`** |
| scipy / tqdm | `1.18.1` / `4.70.0` |
| funasr | **`ABSENT(ModuleNotFoundError)`**（直跑 `C:\Python312\python.exe -c "import funasr"` 复验同结论） |
| torch 包体量 | **4,415,065,323 B / 13,795 文件** |
| 已装包数 | `pip list --format=freeze` = **304** 行 |
| jieba / numba / librosa / scikit-learn / tiktoken / hydra-core | **全部未装**（`pip list` 零命中；这决定了 §7 的 +2/−2） |

装前 torch 关键文件 sha256（`_演/02-env-BEFORE.json`）：

| 文件 | 字节数 | sha256 |
|---|---|---|
| `torch/__init__.py` | 110,846 | `9805fe98af34a65b1ab758c7909cc7b9906f40777257e513968545b19e319aa1` |
| `torch/version.py` | 327 | `533e322ff85c9adf6f17cde43856524d757ffdac13dd06e35628b0d43520a070` |
| `torch/_C.cp312-win_amd64.pyd` | 10,752 | `8b4f9f50a2b6ad1d51b35d654d1c2927401ddf9219cf44f5654e7eb59a0da38d` |
| `torch/lib/cudart64_12.dll` | 573,952 | `c2c9a9c22a9bcba90e261825968836787b331038047a26770cffb7a583c28344` |
| `torch/lib/cublas64_12.dll` | 113,716,224 | `9513540e4ec4c51ee9e7304138c2cc255c29a8c181f9e80c38efa25738becd99` |
| `torch/lib/torch_cuda.dll` | 811,555,840 | `d5ca93bdf3e246af9c962ec92554e5576dec03b665f9cbf1c664e653f76ddb50` |

装前 GPU 探测（`_演/03-gpu-probe-BEFORE.txt`，exit 0）：

```json
{"ok": true, "steps": [{"step": "fp32 matmul 2048x2048", "seconds": 0.277, ...},
 {"step": "fp16 conv2d 1x3x512x512 -> 16ch", "seconds": 0.226, "out_shape": [1, 16, 512, 512], ...}],
 "torch": "2.11.0+cu128", "cuda_build": "12.8", "cuda_available": true,
 "device_name": "NVIDIA GeForce RTX 5070 Ti Laptop GPU", "capability": [12, 0]}
```

装前显存（`nvidia-smi`，`--query-gpu=memory.total,memory.free,memory.used`）：
**total 12,227 MiB / free 1,719 MiB / used 10,226 MiB**；`--query-compute-apps` 只有
`pid 31096 C:\Users\Doro\APP\ComfyUI-aki-v3.2\python\python.exe`。

> 与工单口径对账：工单写「ComfyUI PID 37536、占显存约 10.9 GiB」，本机实测 PID **31,096**、总量 used
> **10,226 MiB**。差异按实测登记（**PID 会随重启变化，不影响本轮任何判定**）；关键量是
> **free 1,719 MiB < 6,609 MiB**，与工单结论一致。

## 3. dry-run（硬闸，`_演/04-dryrun-funasr-1.4.16.txt`，exit 0）

命令：`C:\Python312\python.exe -m pip install --dry-run "funasr==1.4.16"`（索引：清华 + 华为云）

`Would install` **16 个包**（全部为新增，无一条是降级/升级已装包）：

```
aliyun-python-sdk-core-2.16.1  aliyun-python-sdk-kms-2.16.5  funasr-1.4.16
hydra-core-1.3.8  jieba-0.42.1  kaldiio-2.18.1  librosa-1.0.0  llvmlite-0.50.0
numba-0.68.1  oss2-2.19.1  pynndescent-0.7.0  scikit-learn-1.9.1  tensorboardX-2.6.5
tiktoken-0.14.0  torch-complex-0.4.4  umap-learn-0.5.12
```

**硬闸判据**（torch / torchaudio / torchvision / numpy / transformers 中任一个版本被改动即停手）：

| 受保护包 | dry-run 中的状态 | 判定 |
|---|---|---|
| torch | 未出现在任何收集/安装列表；后续 `resolve` 未触发 | **不改动** |
| torchaudio | 同上 | **不改动** |
| torchvision | 同上 | **不改动** |
| numpy | `Requirement already satisfied ... (2.4.6)` | **不改动** |
| transformers | `Requirement already satisfied ... (5.16.1)` | **不改动** |

→ 闸门通过，**按常规姿势安装**（未使用 `--no-deps`，因为 dry-run 证明不动受保护包；`--no-deps`
只在 dry-run 显示冲突时才需要，本轮不触发该分支）。

## 4. 安装执行（`_演/05-install-funasr-1.4.16.txt`）

- 命令：`C:\Python312\python.exe -m pip install "funasr==1.4.16"`（2026-11-03 11:28:0x 起）
- **退出码 0**
- 实装 16 个包，**与 dry-run 预报的集合逐字相同**（无额外解析偏差）：
  `aliyun-python-sdk-core-2.16.1 aliyun-python-sdk-kms-2.16.5 funasr-1.4.16 hydra-core-1.3.8
  jieba-0.42.1 kaldiio-2.18.1 librosa-1.0.0 llvmlite-0.50.0 numba-0.68.1 oss2-2.19.1
  pynndescent-0.7.0 scikit-learn-1.9.1 tensorboardX-2.6.5 tiktoken-0.14.0 torch-complex-0.4.4
  umap-learn-0.5.12`
- 已装包数 304 → **320**（+16，与实装数闭合；`_演/07-pip-list-AFTER.txt`）

## 5. 装后复验（`_演/06-env-AFTER.json`、`_演/08-gpu-probe-AFTER.txt`）

### 5.1 torch / numpy / transformers 对照（2026-11-03 11:28:46）

| 项 | 装前 | 装后 | 结论 |
|---|---|---|---|
| `torch.__version__` | `2.11.0+cu128` | `2.11.0+cu128` | **逐字不变** |
| `torch.version.cuda` | `12.8` | `12.8` | **逐字不变** |
| `torch.cuda.is_available()` | `True` | `True` | 不变 |
| torch 目录字节数 | 4,415,065,323 | 4,415,065,323 | **同值** |
| torch 目录文件数 | 13,795 | 13,795 | **同值** |
| 6 个关键文件 sha256 | 见 §2 表 | **6/6 同值** | **逐字节不变** |
| `torch-2.11.0+cu128.dist-info/RECORD` | `69c1d26c…c345` / 1,411,331 B | 同值 | **逐字节不变** |
| numpy | `2.4.6` | **`2.4.6`** | **未变** |
| transformers | `5.16.1` | **`5.16.1`** | **未变** |
| torchaudio | `2.11.0+cu128` | `2.11.0+cu128` | 未变 |
| torchvision | `0.26.0+cu128` | `0.26.0+cu128` | 未变 |
| cuDNN | 91900 | 91900 | 未变 |
| funasr | `ABSENT(ModuleNotFoundError)` | **`1.4.16`** | 到位 |

numpy **未变**（`2.4.6` 前后同值）、transformers **未变**（`5.16.1` 前后同值）——无「被顺手升级」的包。

### 5.2 GPU 探测（装后 exit 0）

```json
{"ok": true, "steps": [{"step": "fp32 matmul 2048x2048", "seconds": 0.209, ...},
 {"step": "fp16 conv2d 1x3x512x512 -> 16ch", "seconds": 0.109, "out_shape": [1, 16, 512, 512], ...}],
 "torch": "2.11.0+cu128", "cuda_build": "12.8", "cuda_available": true,
 "device_name": "NVIDIA GeForce RTX 5070 Ti Laptop GPU", "capability": [12, 0]}
```

matmul **0.209s**、fp16 conv2d **0.109s** 均成功（装前 0.277s / 0.226s；差异属正常抖动，均远低于超时面）。
**未触发回滚条件**（torch 未变 + GPU 探测通过）。

### 5.3 funasr 导入层

```
C:\Python312\python.exe -c "from funasr import AutoModel; import funasr; print(...)"
→ funasr 1.4.16 AutoModel funasr.auto.auto_model   （exit 0）
```

## 6. 加载路径证明（不加载权重，`_演/load_path_proof.py`、`_演/11-load-path-proof.txt`）

做法：驱动 `_do_load_voxcpm2_internal(None, "cpu", include_denoiser=False)` 生成器**只到第一个 yield**——
该 yield 位于 `load.py:349-352` 的 `import voxcpm` / `from funasr import AutoModel` **之后**、
`voxcpm.VoxCPM.from_pretrained`（`:372`）**之前**，随后立即 `gen.close()`，因此 `from_pretrained` 与
`AutoModel(...)`（ASR 子模型，`:420`）**从不执行**，不占显存、不下权重。

同时做**反向对照**（`importlib.abc.MetaPathFinder` 屏蔽 `funasr`），验证探针本身有分辨力：

| 场景 | 结果 |
|---|---|
| 有 funasr | **`ok: true`**，首条 yield = `正在加载 VoxCPM2 模型...`（即 import 段已通过） |
| 屏蔽 funasr | **`ok: false`**，`ModuleNotFoundError: No module named 'funasr' (blocked by probe)` |
| 判定 | `probe_has_discriminating_power: true` → 该探针能区分「有/无 funasr」，不是恒绿的空探针 |

→ `load.py` 的导入路径**不再抛 `ModuleNotFoundError`**（本轮允许的验证止于 import 与依赖层）。

> 首跑失误（如实登记）：脚本初版用自身 `__file__` 推仓库根，而脚本住在仓外证据目录，结果是
> `ModuleNotFoundError: No module named 'integrated_app'`、exit 1——**探针位置错了，不是仓库坏了**。
> 改为「第一个命令行参数 > 当前工作目录」并加「缺 `app/` 即报错退出 2」的守卫后复跑 exit 0。

## 7. 两口径全量测试与测试数变化归因

| 口径 | 命令要点 | 结果 | 耗时 | exit |
|---|---|---|---|---|
| A（经入口） | `python scripts/run_python.py -m pytest tests/ -q --tb=short --ignore=tests/e2e` | **2566 passed / 0 failed / 116 skipped** | 104.21s（11:31:22→11:33:11） | **0** |
| A（直跑） | `python -m pytest tests/ -q --tb=short --ignore=tests/e2e` | **2566 / 0 / 116** | 98.20s（11:37:00→11:38:42） | **0** |
| A（经入口·文档改完之后终跑） | 同 A 首行 | **2566 / 0 / 116** | 95.92s（11:42:19→11:43:58） | **0** |
| B（覆盖率） | 同 A + `-m "not integration and not gpu and not cuda and not vram and not benchmark and not stress" --cov --cov-fail-under=45` | **2469 / 0 / 90，123 deselected**，cov **51.59%** ≥ 45% | 191.23s（11:33:29→11:36:45） | **0** |

基线（阶段 13）：A **2564/0/118**、B **2467/0/92（123 deselected）** cov 51.58%。

**变化：+2 passed / −2 skipped（A、B 同幅）。** 归因已实测闭合，不是「测试数漂移」：

- funasr 的依赖树带入 **`jieba==0.42.1`**（装前 `pip list` 零命中，装后 `07-pip-list-AFTER.txt:119`）；
- `tests/test_fts_tokenizer.py` 有**恰好 2 个**用例以 `if not _check_jieba(): pytest.skip("jieba 不可用")` 为门
  （`:24 test_chinese_text`、`:42 test_chinese_jieba_or_query`）；
- 现跑该文件实测 **7 passed / 0 skipped**（`_演/16-jieba-gated-tests.log`，1.47s，exit 0）——
  2 个原本被 skip 的用例现在真执行且**通过**。
- 测试套内**没有任何**以 funasr 为 skip 门（`git grep funasr tests/` 仅命中 `test_dependency_consistency.py:386`
  的注释与声明断言），因此 funasr 本身不改变用例计数，只改变 VoxCPM2 的**加载期**能力。
- 不变量成立：**0 failed / 全 exit 0**。

## 8. 活体库零增长（`_演/13-db-BEFORE-tests.json`、`_演/18-db-AFTER-tests.json`）

| 对象 | 测前（11:31:18） | 测后（11:38:46） | 结论 |
|---|---|---|---|
| `data/history.db` | 2,469,888 B / `051d16889e0e90de576852b2e49ef1109913f1656ad9fa5b5fdce6821e200158` | 同值 | **逐字节一致** |
| `data/history.db-wal` | 0 B / `e3b0c442…b855`（空文件哈希） | 同值 | **一致** |
| `data/history.db-shm` | 32,768 B / `fd4c9fda9cd3f9ae7c962b0ddf37232294d55580e1aa165aa06129b8549389eb` | 同值 | **一致**（mtime 11:04:44→11:38:46，仅共享内存映射刷新，**内容 sha256 同值**） |
| `outputs/generation_versions.db` | 65,536 B / `41ab2af72b3af97c95ee6d89eba2e5c07544a88fae5ae65297ac4b8e59fda401` | 同值 | **逐字节一致** |
| `action_logs` 行数 | 333 | 333 | **零增长** |
| `MAX(ts_ms)` | 1793594173715（= 阶段 13 收口值） | 1793594173715 | **不变** |
| `PRAGMA integrity_check` | — | `ok` | — |
| `data/backups/` 既有物 | 12 个文件 / 4,698,087,424 B | 同 | **未删未改**（本轮未新增） |

结论：阶段 10 的 conftest 全局隔离 + `sqlite3.connect` 哨兵在**装了 funasr、多跑了 2 个用例**的情况下
仍然兜住，**无遗漏**。文档三处 staged 后（11:42:19→11:43:58）再跑一轮 A 口径 **2566/0/116 exit 0**，
11:44:04 终态复核（`_演/21-db-FINAL.json`）与前表**完全相同**——零增长在三跑之后仍成立。

## 9. 未覆盖清单（诚实边界）

| 未覆盖项 | 原因 / 现状 |
|---|---|
| **VoxCPM2 真加载验证** | **未做**。`nvidia-smi` 实测 free **1,719 MiB** < 加载需 **6,609 MiB**；ComfyUI（PID 31,096，占 10,226 MiB）**零触碰**——结束/暂停非本轮启动的进程需属主单独下指令。按工单「不足 6,609 MiB 就硬停」，本轮止于 import 与依赖层（§5.3 / §6） |
| **`.venv` 重建** | 未重建、未创建/删除/修改符号链接（属主选 A，B 待 **G4** 决策）。`.venv` 仍是指向不存在的 `D:\_venvs\tts` 的悬空联接 |
| **git 钩子未接线** | `core.hooksPath` 仍为 `local/hooks`（**未动**）。本轮只把「`pre-commit` 会因 funasr 红」这一前置条件解除（§10）；接线会立刻开始拦提交，需属主知情后确认 |
| **计划任务未注册** | `scripts/install_backup_schedule.py` 仍只有 dry-run 产物，未注册（沿阶段 9） |
| **异地副本未解决** | 需属主提供第二存储位置（沿阶段 9） |
| **GPU 推理口径未复验** | 阶段 5/6 记录的 `torch 2.15.0+cu134`（venv 口径）在本机已不可达；本轮只验当前系统 Python 的 `2.11.0+cu128` |
| **`requirements*.txt` 未改动** | 按硬约束 2 保持三处同钉契约（`pyproject.toml:78` / `requirements.txt:7` / 锁 `==1.4.16`）；系统 Python 的实装状态**不写回**这些文件 |
| **ASR / 降噪子模型权重** | 未下载、未加载（硬约束 5：不联网下权重） |

## 10. F8 前置条件状态（接线判据）

| 判据 | 阶段 13（装前） | 阶段 14（装后） |
|---|---|---|
| `sh .githooks/pre-commit` | **exit 1**，红在 `Engine Compatibility Check`（8/9，funasr 缺失） | **exit 0**，`Engine Compatibility Check ... Passed`（9/9），其余实跑门禁 6/6 Passed |
| `check_engine_compat.py` | 8/9，exit 1 | **9/9，exit 0** |
| `funasr` | 缺失 | **1.4.16 到位** |
| 钩子接线 | 未接线 | **仍未接线**（`core.hooksPath` = `local/hooks`，未动） |

→ 「`pre-commit` 会因 funasr 拦提交」这一**接线前置条件已解除**；剩余前置为**属主授权动作**
（`./.githooks/install.sh` 或 `git config core.hooksPath .githooks`），不在本轮执行范围。

## 11. 证据索引（`C:/Users/Doro/Desktop/评估报告_2026-10-29/_演练/F8-funasr/`）

| 文件 | 内容 |
|---|---|
| `01-pip-list-BEFORE.txt` / `07-pip-list-AFTER.txt` | `pip list --format=freeze` 全量快照（304 → 320 行） |
| `02-env-BEFORE.json` / `06-env-AFTER.json` | 环境快照（torch 四重指纹、numpy/transformers/torchaudio/torchvision、funasr 有无） |
| `03-gpu-probe-BEFORE.txt` / `08-gpu-probe-AFTER.txt` | 最小 GPU 探测（matmul + fp16 conv2d）原始输出 |
| `04-dryrun-funasr-1.4.16.txt` | dry-run 完整输出 + `Would install` 清单 |
| `05-install-funasr-1.4.16.txt` | 安装完整输出 + 退出码 |
| `09-engine-compat-via-run_python.txt` / `10-engine-compat-direct-systempython.txt` | 9/9 对照（两份 sha256 同值） |
| `11-load-path-proof.txt` | 加载路径正反双向证明（含分辨力判定） |
| `12-precommit-rerun.txt` | `sh .githooks/pre-commit` 复跑全文（exit 0） |
| `14-runA-via-entry.log` / `17-runA-direct.log` / `20-runA-final-after-docs.log` / `15-runB-cov.log` | 两口径全量日志（A 共 3 跑：经入口 104.21s / 直跑 98.20s / 文档改完终跑 95.92s） |
| `16-jieba-gated-tests.log` | `test_fts_tokenizer.py` 7 passed（+2/−2 归因证据） |
| `13-db-BEFORE-tests.json` / `18-db-AFTER-tests.json` / `21-db-FINAL.json` | 活体库零增长对照（含全部跑完后的终态复核） |
| `19-precommit-staged.txt` | 文档三处 staged 后复跑 `sh .githooks/pre-commit`（exit 0） |
| `snapshot.py` / `gpu_probe.py` / `db_snapshot.py` / `load_path_proof.py` | 本轮取证脚本（仓外，不入库） |

## 12. 复算命令

```bash
# 1) 环境快照（装前/装后各一次）
C:\Python312\python.exe _演/F8-funasr/snapshot.py after _演/F8-funasr/06-env-AFTER.json

# 2) 引擎依赖门禁（两口径应逐字节一致）
python scripts/run_python.py scripts/check_engine_compat.py; echo $?
C:\Python312\python.exe scripts/check_engine_compat.py; echo $?

# 3) 加载路径证明（不加载权重）
C:\Python312\python.exe _演/F8-funasr/load_path_proof.py "C:/Users/Doro/APP/repository/TTS-MultiModel"; echo $?

# 4) 钩子复跑
sh .githooks/pre-commit; echo $?

# 5) 两口径全量
python scripts/run_python.py -m pytest tests/ -q --tb=short --ignore=tests/e2e; echo $?
python scripts/run_python.py -m pytest tests/ -q --tb=short --ignore=tests/e2e \
  -m "not integration and not gpu and not cuda and not vram and not benchmark and not stress" \
  --cov --cov-fail-under=45; echo $?
```
