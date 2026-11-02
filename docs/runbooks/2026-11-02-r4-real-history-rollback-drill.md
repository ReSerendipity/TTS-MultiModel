# R4 发布回滚演练——真仓 756 条完整历史重跑（2026-11-02，整改阶段 8）

> 对应评估门禁：**R4**（回滚预案已演练，⛔P0 绝对项，原判 `[?]`）。
> 动因（GOTCHAS #191 登记未执行的动作）：`scripts/rollback_release.py` 的 merge 兼容修复（提交 `019436a`，2026-11-01）
> 此前**仅在仓外合成的最小仓验证过**（`docs/runbooks/2026-10-31-restore-drill.md` §4 记录真跑在 merge 提交 `8a0851da` 处 exit 128；
> §5.1 F1 行记录修复后最小仓 exit 0），从未在真仓 756 条提交的完整历史上端到端跑通。本轮即闭环该动作。
> R4 判据（第三轮复评成文，③「周期性」不适用，归 D6/O4）：**①记录可复核 ②对象是活体**。

## 1. 结论速览

- 在真仓的 **file 协议全量克隆**（756 条提交、22 个 tag、含全部 merge 提交、非浅克隆）内，用生产 venv 解释器真跑
  `scripts/rollback_release.py --target v2.8.0`：**退出码 0**，43/43 个 revert 全部成功，无残留 sequencer 状态、工作树干净。
- 对平达到最强结果：**回滚后树与 `v2.8.0` 树逐字节一致**（两侧 tree hash 均为 `db91f16d5d32bd7bf88f01751c0082e640db3c88`，
  `git diff --stat` 为 0 文件 / 0 增删）。
- 当初炸掉的 merge 提交 `8a0851da`（PR #239 合并点，2026-10-29T11:41:42+08:00）在本轮枚举中为**第 24/43 个**，
  走 `-m 1` 分支被成功 revert（revert 提交 `ff86f67`，diffstat 与 merge 引入完全互逆）。
- **R4 自判：`[?]→[x]`**（两判据逐项通过，见 §8）。
- 全程零网络（本地 file 协议）、零装包、零触碰生产 checkout 的 git 状态；克隆与日志全部在仓库外演练目录。

## 2. 环境与保真度自检表

生产 checkout：`C:/Users/Doro/APP/repository/TTS-MultiModel`，分支 `fix/desktop-shell-csrf-and-commands`。
演练克隆：`C:/Users/Doro/Desktop/评估报告_2026-10-29/_演练/R4-real-history/repo`
（`git clone --no-hardlinks file:///C:/Users/Doro/APP/repository/TTS-MultiModel`，无 `--depth/--shallow/--single-branch/--filter`）。

| 自检项 | 命令 | 生产实测 | 克隆实测 | 判定 |
|---|---|---|---|---|
| 起测前工作树洁净 | `git status --porcelain \| wc -l` | 0（起测复核 11:21:41 仍为 0） | 0（起测 11:10:44） | ✓ |
| 提交总数 | `git rev-list --count HEAD` | 756 | 756 | ✓ |
| HEAD | `git rev-parse HEAD` | `2eda5a7785a95b41b3866e0140a18f3e7cd083e7` | 同左（`2eda5a7`） | ✓ |
| tag 数 | `git tag -l \| wc -l` | 22 | 22 | ✓ |
| `8a0851da` 为 HEAD 祖先 | `git merge-base --is-ancestor 8a0851da HEAD` | — | exit 0 | ✓ |
| `v2.8.0` tag 在位 | `git for-each-ref refs/tags \| grep v2.8.0` | — | 命中 1 条 | ✓ |
| v2.8.0 → 提交 | `git rev-list -n1 v2.8.0` | — | `1ffccfd6a35e8930ed0c2fcca93b04573642eb51`（与 2026-10-31 演练记录一致） | ✓ |
| 含 merge 提交 | `git rev-list --merges --count HEAD` | — | 85（全史）；主线 `1ffccfd6..HEAD` first-parent merge=10 | ✓ |

克隆起止：2026-11-02 11:10:25 → 11:10:35（exit 0，10s，日志 `clone-stdout.log` / `clone-stderr.log`）。

**LFS 探测（只读）**：git-lfs 3.7.1 客户端在位，但本仓 `.gitattributes` 无任何 `filter=lfs` 规则（实测 0 条）、
`git lfs ls-files` 输出 0 行——仓库未启用 LFS，本轮克隆与演练零 LFS 对象下载（本地 file 协议本就无网络）。

## 3. 脚本活体指纹（判据②：跑的就是仓库里这份）

| 对象 | sha256 | 字节数 |
|---|---|---|
| 生产磁盘 `scripts/rollback_release.py`（`certutil -hashfile`） | `6dbc567eb6597fa3fe5eae0cb6ad4242e084848df65666f33d91ebdb5f000d2a` | 5050 |
| 真仓 blob `2eda5a7:scripts/rollback_release.py`（`git cat-file blob`） | `6dbc567eb6597fa3fe5eae0cb6ad4242e084848df65666f33d91ebdb5f000d2a` | 5050 |
| 生产 `git diff HEAD -- scripts/rollback_release.py` | 0 行（磁盘=blob，`core.autocrlf=true` 未对该文件产生换行差异） | — |

起测时（11:10:44）克隆工作树 `status --porcelain`=0、HEAD=`2eda5a7`，故**被执行的脚本字节 = 上表 blob 字节**。

注（防误读）：演练结束后克隆磁盘上的脚本文件变为 4379 B / `5914559b…`——这是**回滚链自身把修复提交 `019436a`
一并 revert**（链中 `07eb0de Revert "fix(sre): 回滚脚本兼容 merge 提交…"`）的结果，属预期行为，不改变「起测时执行的是
仓库活体」的事实。另实测 `git log 557e845..HEAD -- scripts/rollback_release.py` = 0 条：`019436a` 之后该脚本零改动，
本轮真仓重跑的正是第四轮复评时存续的那份修复。

## 4. 命令原文、退出码与计时

在克隆根目录内执行（脚本 git 子进程按 cwd 解析仓库，GOTCHAS #191 ① 的契约）：

```text
CMD:  C:/Users/Doro/APP/repository/TTS-MultiModel/.venv/Scripts/python.exe scripts/rollback_release.py --target v2.8.0
CWD:  C:/Users/Doro/Desktop/评估报告_2026-10-29/_演练/R4-real-history/repo
START 2026-11-02 11:11:25
END   2026-11-02 11:11:30
耗时  5s
EXIT  0
```

- 解释器：`.venv/Scripts/python.exe` = Python 3.12.10（生产仓既有 venv，只读调用，未装包未改包）。
- 其余参数：脚本 CLI 仅 `--list/--target/--dry-run` 三项；本轮为真跑，未加 `--dry-run`，与 2026-10-31 退 128 那次同形。
- stdout 全量存 `drill-run-stdout.log`（1640 B），stderr 存 `drill-run-stderr.log`（**0 B，空**），退出码存 `drill-run-exit.txt`。

## 5. 结果对平（不只退出码）

| 判据 | 实测 |
|---|---|
| `[revert]` 尝试数 / 成功数 | 43 / 43（stdout `[info] 将反向 revert 其后 43 个提交`；`[revert]` 行 43 条） |
| 回滚产生的提交数 | `git rev-list --count 2eda5a7..HEAD` = 43（含 10 个 `Revert "Merge pull request …"`，对应主线 10 个 merge） |
| HEAD 前 | `2eda5a7785a95b41b3866e0140a18f3e7cd083e7` |
| HEAD 后 | `1a2f068739a86684c84b625f77175c536535eb60` |
| 回滚后树 vs `v2.8.0` 树 | `git rev-parse HEAD^{tree}` = `git rev-parse v2.8.0^{tree}` = `db91f16d5d32bd7bf88f01751c0082e640db3c88` |
| `git diff --stat HEAD v2.8.0` | **0 个文件、0 行增、0 行删**（输出为空） |
| 工作树/序列器 | `status --porcelain` = 0；`.git` 无 `revert-merge-*`/`sequencer` 残留目录 |

**差异判据解释（revert 语义 ≠ checkout 语义，本轮结果为何是「空 diff」）**：
回滚链不移动指针（checkout/reset 语义），而是在主线顶端追加 43 个新提交，历史完整保留、可再 forward。
每个 revert 精确取「被回滚提交相对其（第一）父的差」的逆；对本仓历史，`--first-parent` 枚举恰好覆盖主线全部变更
（10 个 PR merge 的分支线内容作为 merge 相对第一父的引入量、随各自 `-m 1` revert 整体消失，分支线提交不被二次枚举），
且按由新到旧顺序施加不产生内容冲突——因此**理论上允许偏离的 revert 链，本轮逐字节还原出了 v2.8.0 树**（0 文件差异），
这是 revert 型回滚能达到的最强对平结果。`v2.8.0..HEAD`（全枚举含分支线）为 105 条、first-parent 为 43 条，
两个口径的差即「由 merge 整体撤销、无需单独 revert」的分支线提交，不构成遗漏。

43 个回滚提交的 subject 全清单见演练目录 `reconcile.log`（由新到旧：`1a2f068 Revert "Merge pull request #219 …"` …
`a5845a5 Revert "fix(scripts): 修复 download_models.py 里遗留的 stash 冲突标记"`）。

## 6. 关键锚点取证：`8a0851da`（2026-10-31 退 128 的元凶）

- 身份：`Merge pull request #239 from ReSerendipity/docs/triage-exemption-review-5202`，2026-10-29T11:41:42+08:00，
  双亲 `b4fa3669`（第一父/主线）与 `3d90bfb`（PR #239 分支线），`rev-list --parents` 3 token → 脚本 merge 分支条件 `>2` 命中。
- **落在回滚区间内**：起测时 first-parent 枚举（`enumeration` 起测口径 43 条，`preflight.log`）中为第 **24** 条；
  stdout `[revert]` 序列第 24 行即 `8a0851da`。（旧演练中它是第 6 个目标——当时 HEAD 之上主线只有 5 条新提交，口径自洽。）
- 脚本对它的实际处理：`git revert --no-edit -m 1 8a0851da` → revert 提交 `ff86f67`，提交正文铁证 mainline：
  `This reverts commit 8a0851da8efc1abfd36e6303867951635da61ef8, reversing changes made to b4fa36696e627d01908d5d350db8dcfd0ab73e79.`
- diffstat 互逆：merge 相对第一父引入 **10 files, +199/−634**；`ff86f67` 对其父施加 **10 files, +634/−199**。
- 分支线防二次 revert 取证：第二父线端点 `3d90bfb` 在普通枚举 `1ffccfd6..HEAD` 中命中 1 条、在 `--first-parent` 枚举中命中 **0** 条。

## 7. 与 SRE_RUNBOOK §2.4「回滚版本位硬停断言」的关系

该断言（目标 tag ↔ `version.json $.version` ↔ `CHANGELOG.md` 顶部一致且内嵌 changelog 自述一致）是**成文的人工 SOP
检查步骤，`rollback_release.py` 自身并未实现它**（脚本只读：入参仅 `--list/--target/--dry-run`，无版本位校验代码路径）。
按 §2.4/§5.1 既有实测，`v2.8.0` 该断言判 **FAIL**（tag 内嵌 changelog 自述 2.7.0、手工位 2.7.0，F2 缺陷）。
含义与边界：
- 若对**生产**执行 `--target v2.8.0` 的真实回滚，操作者必须按 §2.4 硬停，先补齐人工版本位重打 tag（需所有者授权）或显式接受漂移并记录；
- 本轮是**仓库外克隆的预案演练**（R4 的验证对象是「回滚预案能否端到端执行」），不产生生产回滚动作，
  F2（目标 tag 版本位漂移）作为独立既有项继续挂账，不在本轮整改范围（工单明确「属主决策项一律不动」）。
- 「把断言实现进脚本/测试」登记为未覆盖项（§10），本轮不扩权。

## 8. R4 四态自判

| 判据 | 结论 | 依据 |
|---|---|---|
| ① 记录可复核 | **通过** | 本 runbook + 演练目录 14 个留痕文件（克隆/保真度/枚举/stdout/stderr/退出码/对平/指纹，§11 清单），全部命令与退出码原样可重放；生产 checkout 全程零改动（11:21:41 复核 porcelain=0） |
| ② 对象是活体 | **通过** | 执行对象=真仓 756 条完整历史的 file 协议全量克隆（非浅、非合成最小仓；§2 保真度 8 项全过），脚本字节与生产 blob 三方对平（§3 sha256 `6dbc567e…`，5050 B），解释器为生产 venv 原样调用 |

**R4：`[?]→[x]`**。此前判 `[?]` 的唯一理由（修复仅在小仓验证、真仓完整历史未跑通）已被本轮 exit 0 + 树级对平消除；
「周期性」维度不适用（归 D6/O4）。F2 目标 tag 版本位漂移不改变 R4 两判据的通过，已在 §7 与未覆盖清单如实挂账。

## 9. 全量测试套复验（阶段 4 教训条款：任何触碰仓库的阶段必须跑全量）

口径说明：venv 缺 `pytest-timeout`（实测 `ModuleNotFoundError`），按「禁止装依赖」硬约束如实舍弃 ci 命令行中的
`--timeout=180` 参数（偏差已登记）；`--cov-report=html` 输出重定向到演练目录以免污染生产树（`htmlcov/` 未被 gitignore）。

| 口径 | 命令要点 | passed | failed | skipped | 退出码 |
|---|---|---|---|---|---|
| A（本机基线口径，对齐 2603 基线） | `pytest tests/ -v --tb=short --ignore=tests/e2e` + ci 离线 env | **2603** | **0** | **41** | **0**（11:26:03→11:27:32，86.09s） |
| B（ci.yml L233 口径） | A + `-m "not integration and not gpu and not cuda and not vram and not benchmark and not stress"` + `--cov=app/integrated_app --cov-branch --cov-fail-under=45`（cov 报告见上注） | **2511** | **0** | **10**（另 deselected 123） | **0**（11:27:43→11:31:00，191.82s；覆盖率 **52.41%** ≥ 45% 达标） |

采集参考：`pytest --collect-only -q tests/ --ignore=tests/e2e -m "not integration…stress"` → 2520/2643 collected（123 deselected，2026-11-02 实测）。
基线对照：2603 passed / 0 failed（第四轮复评前实测）——口径 A 本轮**逐字复现基线 2603 / 0 failed**（skipped 41），
证明本轮文档改动（新 runbook + 三处台账）未破坏任何门禁；口径 B 为 ci 选集（含 cov 门禁），两口径合计退出码均为 0。
两日志：`testrun-A.log` / `testrun-B.log`（演练目录，含计时文件 `testrun-A-exit.txt` / `testrun-B-exit.txt`）。

## 10. 未覆盖清单（本轮没验什么、为什么）

1. **生产真实回滚**：未对生产 checkout 执行任何 revert/分支/tag 动作（工单硬约束）；演练对象是克隆。
2. **镜像/kubectl 回滚面**：无集群环境，未演练（沿用 2026-10-31 台账「归 O3」口径）。
3. **回滚后服务层验证**（readiness/`/readyz` 线上实例、审计日志检查）：离线克隆无服务在跑，未做（工单禁启停服务）。
4. **§2.4 硬停断言的脚本化实现**：断言仍是人工 SOP 步骤，本轮不扩权改脚本（v2.8.0 FAIL 为既有 F2 事实，属主决策项）。
5. **回滚态上跑全量测试套**：本轮回滚树=v2.8.0 树，其 `test_version_consistency` 3 failed 为 2026-10-31 既有实测（F2 表现），
   不在本轮重跑范围（本轮测试复验面向生产 HEAD + 文档改动）。
6. **`pytest-timeout`**：venv 缺该插件且禁装，ci 命令的 `--timeout=180` 未随本轮复验执行（见 §9 偏差登记）。
7. **push/部署/分卷构建**：一律未做（工单禁止），回滚链的远端可见性不主张。

## 11. 证据链清单（演练目录，保留勿删）

`C:/Users/Doro/Desktop/评估报告_2026-10-29/_演练/R4-real-history/`：
`repo/`（克隆本体，起测 HEAD `2eda5a7`→回滚后 HEAD `1a2f068`，工作树干净）、
`clone-timestamp.txt`、`clone-stdout.log`(0 B)、`clone-stderr.log`(1255 B)、
`fidelity-check.log`、`preflight.log`、`drill-run-exit.txt`、`drill-run-stdout.log`、`drill-run-stderr.log`(0 B)、
`reconcile.log`、`reconcile2.log`、`reconcile3.log`、`reconcile4.log`、`fingerprint.log`、
`prod-precheck.log`、`enumeration-43.txt`（注意：该文件为**回滚后**采集，86 行=43 revert+43 原主线；起测 43 条口径见 `preflight.log`）、
`testrun-A.log`/`testrun-A-exit.txt`/`testrun-B.log`/`testrun-B-exit.txt`（§9 两口径全量测试套）、`htmlcov/`（口径 B 的 HTML 覆盖率报告，重定向至此避免污染生产树）。

**台账落点备注（GOTCHAS #191）**：#191 行的「动作未执行→已执行」更新写入的是 `docs/agents/GOTCHAS.md`（实测
`git check-ignore -v` 命中 `.gitignore:405 docs/agents/`，该目录属本机独有层、不可入库——正是 #191 ④ 记过的坑）。
入库侧的 R4 闭环记账由本 runbook、`docs/SRE_RUNBOOK.md` §3.4（台账行 + 门禁状态句 `R4 [?]→[x]`）与
`docs/TODO.md`（R4 条目）三处承载；`git add -f` 强推 ignored 目录不在本轮授权范围，如实登记不粉饰。
