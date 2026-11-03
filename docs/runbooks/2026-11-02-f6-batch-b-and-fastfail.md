# F6 B 批闸门取证与 fastfail（阶段 11，2026-11-02 23:25 – 11-03 00:34）

> 工单：桌面 `wo_stage11.txt`。两件事：**(A)** 把残留 539 行中「8192 / streaming」两判据钉到测试代码，
> 钉上了才隔离 250 行；**(B)** 22 行「0.0s 真实失败」先证伪后诊断。
> **本轮结果：`8192` 钉上、`streaming` 钉不上（工单预设失败例「应用默认 persona 而非测试桩」命中）
> → 按闸门「立即停手，不隔离任何一行」执行：B 批 `F6-20261102-B` 零行隔离，备份/隔离/回滚/复隔离四步一并取消；
> (B) 22 行证伪为真实使用并完成代码路径诊断。全程生产库零写入。**
> 证据目录（仓外）：`_演练/F6-batch-B/`（脚本/日志/取证/对照表全量留痕）。

## 1. 前置普查（只读复核派单方数字，11-02 23:25:41，`logs/01_census_pre.log`）

生产 `action_logs` 主表 **539** 行、A 批隔离表 **2,167** 行（`action_logs_quarantine_20261102` / batch
`F6-20261102-A`）。派单方分布逐项复核一致：`text_length=8192` **206**、`voice_persona='streaming'` **44**
（两桶互斥，并集 **250**）、`generation` 不命中 **201**、`action='model'` **88**，对平 250+201+88=**539** ✓。
`action='generation' AND message LIKE '%(0.0s)%' AND voice_persona 为空` = **22** 行 ✓。

## 2. (A) 闸门取证与裁定（详见仓外 `forensics/A_gate_verdict.md`）

### 2.1 `8192`：**钉上了**
1. **测试文件与行号**：`tests/test_generation_error_friendliness.py:160`
   `data={"text": "啊" * 8192, "instruction": "温柔"}` → `POST /api/generate/voxcpm_design`；
   负例 ：151（8193 → 400，不落库；生产库 `text_length=8193` 实测 **0** 行）；档位断言 :83。
2. **写链**：`routes/generate/voxcpm2/design.py:91`（`instruction: str = Form("")`）→
   `design.py:252-259`（`endpoint_name="VoxCPM design"` / `voice_or_persona=instruction[:50]` /
   `engine="voxcpm2"`）→ `routes/generate/utils.py:1017-1064 _log_generation`（`text_length=len(text)`）
   → `routes/system/logs.py:273-283 INSERT INTO action_logs`。
3. **复现实验**（conftest 隔离库，生产零接触）：单跑 ：160 用例 → 隔离库恰 1 行，签名与生产行逐字段一致，
   错误串 `VoxCPM声音设计生成失败: 'object' object has no attribute 'generate'` 为测试夹具
   （:142 `monkeypatch.setattr(registry, "voxcpm_model", object())`）的**桩指纹**，真实引擎不可能产生。
4. **闭合**：与复现行五元组同签名的生产行 = **206/206**（`logs/09_join_repro_prod.log`）。
   行为侧证：id 近等差（每轮全套件 +1 行）、202/204 时间簇为单行、duration 全部 0.31–1.03s、
   测试 docstring 自标 2026-09-19 与最早行 2026-09-19 14:06:43 同日。

### 2.2 `streaming`：**钉不上 → 批次 B 判据不成立**
1. **值由路由写死，非测试写入**：`routes/generate/voxcpm2/streaming.py:704/718/736-740/754/909/940`
   六处 `_log_generation("Streaming", text, "voxcpm2", "streaming", …)` —— 对该端点**任何调用者**一律如此。
   tests 内 `"streaming"` 字面量均为能力集断言，无一处写入该值（`forensics/grep_streaming_writepath.txt`）。
2. **复现反证**：`tests/test_edge_tts_fallback.py:48` 仅提交 `{"text": "回归保护测试。"}`（无 persona 字段）
   → 隔离库落库行仍带 `voice_persona: "streaming"`——测试代码没写这个值，是路由写的。
3. **判据会误删真实历史**：44 行 = 39（tl=7，0.0s，复现闭合可归因测试）+ 5 非 tl=7：
   **id 338（09-13 23:33:25）`success (27.2s)` tl=98、id 629/630（09-19 09:44）`success (3.6s)/(3.3s)` tl=15
   为真实合成**；id 325/337（09-13，tl=111，0.31s，桩错误串）高度疑似测试但钉不到文件行号。
   `voice_persona='streaming'`（+endpoint/engine 组合，同为路由写死）命中面覆盖 3 行真实使用。
4. 工单闸门失败例原文「streaming 是应用默认 persona 而非测试桩」即本案 →
   **B 批零隔离**；若下轮获授权，可单列判据 `text_length=7 AND endpoint='Streaming'
   AND duration<0.05`（命中恰 39 行，均测试）另案处置，338/629/630/325/337 原地保留。

## 3. (B) 22 行：证伪结论=**真实用户（非测试）**，成因 7 类（详见仓外 `forensics/B_codepath_diagnosis.md`）

证伪六证据：①三条错误文案在 tests/ 零出现；②单元测试桩引擎只能产生 `'object' object …` 类错误，
而 22 行是真实引擎类错误；③id 987 为 CUDA device-side assert（测试物理不可能）；④唯一真引擎测试面
（`tests/integration/test_real_inference_smoke.py`）文本 23/12 字与 22 行无一相同且断言 200；
⑤每类错误的守卫合入日期（`7326b94` 2026-09-14、`4a45e6f` 2026-09-19）**晚于行产生时间**，
守卫上线后同类行归零；⑥文本长度 4–182 自然多样 + 2–4 行重试簇，与测试套「每轮 +1 行、id 等差」形态迥异。

| 类 | 行 | 判定点（文件:行号） | 返回 |
|---|---|---|---|
| VoxCPM design 空描述（6 行：1/106/122/303/304/341） | `engines/voxcpm2/design.py:57 →:76-79` `ValidationError`；路由层无空值拦截（design.py:91 默认空、:160 仅软上限）；前端拦截 `voice_design.html:305` 为 7326b94（09-14）后置 | 400（general） |
| IndexTTS2 空参考音频（4 行：123/124/329/330） | 当时：`engines/indextts2_engine.py:822` `FileNotFoundError`；现状路由前置：`routes/generate/indextts2/synthesize.py:318-324`（7326b94） | 400 |
| IndexTTS2 情感文本缺 QwenEmotion（5 行：128/133/135/138/331） | `engines/indextts2_engine.py:880-883` `ValueError`；现状路由捕获 `synthesize.py:146,476-482`（7326b94）→ utils.py:2033-2037 | 400 |
| IndexTTS2 端点跑在 VoxCPM2 引擎（4 行：536-539 同秒 09-18 16:07:28） | `synthesize.py:293-295`（只判非空）→ :479 `engine.infer` → `AttributeError`；现状版本闸 `synthesize.py:123+288-291`（4a45e6f 09-19）→ engine_not_ready | 400 |
| IndexTTS2 绝对时长拒绝（1 行：645） | `engines/indextts2_engine.py:904-912` `ValidationError`——**有意设计**（:895-903 注释），非校验缺失 | 400（文案自含 3 个替代方案） |
| VoxCPM clone 空引擎（1 行：427） | `routes/generate/voxcpm2/clone.py:260-261` + `model_registry.py:769,789`（无引擎返回 None）→ `'NoneType' object has no attribute 'generate_voice_clone'`；**现状仍无前置拦截（唯一仍敞开），是否补属产品决策** | 400（原始 AttributeError 文案） |
| CUDA device-side assert（1 行：987） | 真实 GPU 推理崩溃，非校验/拦截问题 | — |

**派单方论断修正**：22 行=真实用户 ✓；但「voice_persona 为空=未选 persona」对 IndexTTS2 **不成立**
——该路由把日志字段写死为空（`synthesize.py:529` `voice_or_persona=""`），15 行的空 persona 是日志常量；
真正反映用户未填的只有 6 行 VoxCPM design（persona 取自 instruction，`design.py:257`）。
「0 秒挡回是单一致因」不成立：7 类成因分属路由层缺失/引擎校验/能力边界/GPU 崩溃。

## 4. 生产库零写入（工单硬约束 4 口径；五快照，`logs/04,08,10,12,14,16_metrics_*.json`）

| 快照 | 时刻 | history.db | sha256(前16) | -wal | -shm | action_logs | A批 |
|---|---|---|---|---|---|---|---|
| pre-round-baseline | 00:23:25 | 2,469,888 B | `9652e5e78a679981` | 0 B | 32,768 B | 539 | 2,167 |
| post-repro-pre-suites | 00:26:36 | 2,469,888 B | 同上 | 0 B | 32,768 B | 539 | 2,167 |
| post-runA1 | 00:29:29 | 2,469,888 B | 同上 | 0 B | 32,768 B | 539 | 2,167 |
| post-runA2 | 00:31:13 | 2,469,888 B | 同上 | 0 B | 32,768 B | 539 | 2,167 |
| post-runB-final | 00:33:54 | 2,469,888 B | 同上 | 0 B | 32,768 B | 539 | 2,167 |

全程 `MAX(ts_ms)=1793594184555` 不变、`integrity_check=ok`；sha 与阶段 10 收口值一致——
自阶段 10 收口起生产库逐字节未动，哨兵无漏，`tests/conftest.py` 无需补丁（工单硬约束 9 的例外未触发）。

## 5. 两口径全量（日志/计时/覆盖率产物全在仓外 `F6-batch-B/logs/`、`coverage.xml`、`htmlcov/`）

| 口径 | 命令（同阶段 10） | 窗口 | 结果 | exit |
|---|---|---|---|---|
| A ×1 | `pytest tests/ -v --tb=short --ignore=tests/e2e` | 00:27:26→00:29:17 | **2545 passed / 0 failed / 117 skipped**（107.72s） | 0 |
| A ×2 | 同上 | 00:29:34→00:31:13 | **2545 passed / 0 failed / 117 skipped**（95.11s） | 0 |
| B ×1 | A 集 + `-m "not integration and not gpu and not cuda and not vram and not benchmark and not stress" --cov=app/integrated_app --cov-branch --cov-report=xml/html→仓外 --cov-fail-under=45` | 00:31:18→00:33:44 | **2448 passed / 0 failed / 91 skipped / 123 deselected**；覆盖率 **51.63% ≥ 45%**（141.49s） | 0 |

**环境差异披露（如实）**：仓库 `.venv` 为符号链接 → `D:\_venvs\tts`，**现已失效**（目标目录不存在；
阶段 10 尚可用）。按硬约束 6（不装依赖、不动 venv）未修复，改用系统 `C:\Python312\python.exe`
（3.12.10，pytest 9.1.1，依赖齐备）。故通过/跳过计数与阶段 10（2622/0/41、2530/0/10）不可直接比：
skip 41→117、10→91 源于环境插件差异；两轮相同点：**0 failed、exit 0**。`pytest-timeout` 插件本轮
环境可用（2.4.0）但为与阶段 10 命令保持同形仍未加。生产库零增长已在两口径下实测（§4）。

## 6. 备份/隔离/回滚步骤未执行的原因（工单硬约束 2 的适用性裁定）

工单硬约束 2 的备份是**隔离步骤的前置**（「动手前备份并验证…未通过不得进入下一步」）。
本轮闸门未过、依工单「立即停手，不隔离任何一行」，隔离四步整体取消，故无「动手」、无前置备份义务；
且阶段 10 未覆盖清单已登记「隔离前备份物含噪声、是否重取属主定」——本轮不擅自新增备份物。
零写入由 §4 五快照逐字节对照替代证明。

## 7. 未覆盖清单（本轮没做什么、为什么）

1. **B 批隔离 0 行**（判据不成立）；206 行 8192 桶已可证但按闸门随批停手，**未隔离**——处置待属主授权。
2. **44 行 streaming 桶**：39 行可证测试（tl=7）、3 行真实（338/629/630）、2 行钉不到（325/337）——全部原地保留。
3. **22 行 z22=真实历史**：按「误删真实历史才是失败」**永久保留**，不隔离不删除；其成因诊断已交付，
   是否加校验（如 clone.py 空引擎前置拦截、design.py 路由层空描述拦截）属产品决策。
4. **残留 539 行精算**：206 可证测试（8192 桶）+ 41 streaming（39 可证+2 钉不到）+ 3 真实 streaming
   + 22 真实 z22 + **179 generation 未检**（非 8192/非 streaming/非 0.0s 空 persona）+ **88 model 未检** = 539。
   179+88 两块本轮未做归属分析。
5. **备份未重取**（沿阶段 10 未覆盖 ②）；调度注册与异地副本沿阶段 9 不变（硬约束 6）。
6. **`.venv` 失效**（符号链接目标 `D:\_venvs\tts` 不存在）：未修复（硬约束 6），属主需决定重建 venv 或改用系统解释器基线。
7. 未触碰：`scripts/rollback_release.py`、`requirements-lock.txt`、阶段 10 的 `tests/conftest.py` 与哨兵测试
   （零增长实证无漏，无需补丁）、远端（无 push/无 tag/无分支切换/无 worktree）、服务启停、系统计划任务。

## 8. 留痕索引（仓外 `_演练/F6-batch-B/`）

`scripts/{db_census_b,db_bucket_profile,db_burst_profile,db_metrics,inspect_iso_dbs,db_join_repro}.py`
（全部只读；隔离执行器本轮未调用）；
`logs/01_census_pre.log`、`02_bucket_profile.log`、`03_burst_profile.log`、
`04,08,10,12,14,16_metrics_*.json`（五快照）、`05/06_repro_*`（两复现实验+exit）、
`07_iso_db_dump.log`（隔离库落库行原文；**GBK 编码**，Windows 管道默认所致）、
`11/13/15_runA1|A2|B.log`、`09_join_repro_prod.log`；
`forensics/A_gate_verdict.md`（闸门裁定）、`forensics/B_codepath_diagnosis.md`（B 诊断全链）、
`forensics/grep_8192_writepath.txt`、`grep_streaming_writepath.txt`、`grep_z22_falsify.txt`；
`coverage.xml`、`htmlcov/`（口径 B 覆盖率产物）。

## 9. C 批执行记录（阶段 12，2026-11-03 07:52 – 08:06）

> 工单：桌面 `wo_stage12.txt`。把阶段 11 已钉上的唯一判据（`text_length == 8192`）从残留 539 行中隔离，
> 批次标记 **`F6-20261103-C`**；`voice_persona='streaming'` 44 行、22 行真实秒失败、88 行 `model` 等其余项按工单明令禁触。
> 执行器为阶段 10 原执行器（§8 所引 `_演练/F6-isolation/scripts/quarantine_f6_action_logs.py`）的 **C 批派生**
> `_演练/F6-batch-C/scripts/quarantine_f6_action_logs_c.py`：机制逐字同源（单事务 `BEGIN IMMEDIATE`、
> 先全列 `INSERT…SELECT` 入隔离表、同事务内才按隔离表 id 删主表、无 DROP TABLE、无 VACUUM、除该步外无 DELETE），
> 仅三处不同——批次标记、判据桶（单个：`json_extract(extra_json,'$.text_length') = 8192`）、verify 等式（工单口径①②）。
> 解释器：系统 `C:\Python312\python.exe`（3.12.10，pytest 9.1.1）；`.venv` 符号链接目标 `D:\_venvs\tts` 失效未修（硬约束 6），沿阶段 11 口径。

### 9.1 处置前复核（只读，07:52:44，`logs/census_pre_isolation.log` + `logs/preflight.log`）

- 对平：主表 **539**、A 批隔离表 **2,167**（`F6-20261102-A`），合计 **2,706** ✓；`history.db` 2,469,888 B（mtime 2026-11-02 14:46:35、sha `9652e5e78a679981…` == 阶段 11 收口值）、`-wal` 0 B、`-shm` 32,768 B、`integrity_check=ok`；HEAD `48ae20b`、porcelain=0、ahead 4 ✓。
- **谓词命中 206 行，全部 `failed`、0 条 `success`**：206/206 `message LIKE '%failed%'`（`"VoxCPM design failed (0.3s)"`×173、`(0.4s)`×32、`(1.0s)`×1），全部 `action='generation'`、level=INFO，extra_json 一律含测试桩指纹错误串 `VoxCPM声音设计生成失败: 'object' object has no attribute 'generate'`——判据纯净 ✓。
- **禁触集合零交集实测**：与 `voice_persona='streaming'`（44 行原地）交集 **0**、与 `action='model'` 交集 **0**、与 `'%(0.0s)%'` 交集 **0**；与 A 批隔离表 id 交集 **0**（主表 id 上限 2,698 < A 批隔离 id 上限 2,706；`action_logs` 主键为 `INTEGER PRIMARY KEY AUTOINCREMENT`，无 id 复用风险）。
- **8193 检查：0 行**（`tests/test_generation_error_friendliness.py:151` 负例 8193→400 不落库，与阶段 11 §2.1 一致）→ **无 8193 待决项**；全表 `text_length` 分布中 8192 之外均为 ≤31 自然小值，无邻近可疑值。

### 9.2 备份与恢复验证（工单硬约束 2 闸门，通过后方进入隔离）

- 备份 07:56:07：`scripts/backup_history_db.py --db-path data/history.db --backup-dir <仓外 F6-batch-C>/backups --keep 0`，产物 `backups/history_backup_20261103_075607.db` **2,469,888 B**、sha256 `e43941847713eb67c593b720c93f46d77a0979a892324cad081085372792a136`、**exit 0**（`logs/02_backup.log`、`03_backup_sha.log`）。
- 仓外恢复回读（`_演练/F6-batch-C/restore_drill/`）：`--restore` **exit 0**（脚本内置 integrity_check 通过）；回读 `action_logs` **539** 行（=处置前主表）、`MAX(ts_ms)` **1793594184555**、A 批 **2,167**、合计 **2,706**、谓词命中 206、`integrity ok`（`logs/04_restore.log`、`05_restore_readback.log`）。

### 9.3 隔离与两等式对账（首跑 07:57:26 `logs/07_isolate.log`；断言修正后干净复跑 08:00:21 `logs/15_isolate_clean.log`）

- `isolate`（批次 `F6-20261103-C`）：事务内断言谓词命中 `206 == 处置前实测 206`，全列 `INSERT…SELECT` 入隔离表 **206** 行、同事务删主表对应 id；`integrity_check=ok`，**exit 0**。
- **等式①：C 批隔离数 206 == 谓词命中数 206 ✓**；**等式②：主表剩余 333 + A 批 2,167 + C 批 206 == 2,706 ✓**。A 批不变量 2,167 ✓、主表谓词残留 0 ✓、隔离态 verify **VERIFY PASS exit 0**（`logs/08,16,22`）。
- 隔离态 `MAX(ts_ms)=1793594173715`：处置前最大行 id 2698 属 C 桶，隔离后最大值自然回落至 2697（`Streaming success (0.0s)`）——回滚态须回到 1793594184555（见 §9.4，已验）。

### 9.4 回滚实测与指纹比对（工单硬约束 5）

- **首跑如实登记**：07:57:44 回滚（`logs/11_rollback.log`）操作数字全对（`restored=206`、主表 539、`MAX(ts_ms)=1793594184555`、C 批 0、A 批 2,167、integrity ok），但执行器初版把回滚态主表期望误写为阶段 10 口径的 2,706（打印 ROLLBACK/VERIFY FAIL）——**校准缺陷、非操作缺陷**，主表状态从未出错。当场修正断言（08:00:12 回滚态 verify 复核 **VERIFY PASS exit 0**，`logs/12b`），并于 08:00:21 起完整干净复跑（`logs/15–20`）：isolate→verify→**rollback（ROLLBACK PASS exit 0）**→回滚态 verify（**VERIFY PASS exit 0**）。
- **回滚态不变量口径钉死（登记备查）**：工单硬约束 5 原文「主表须回到 2,706」与同句「隔离表 C 批清零而 A 批仍为 2,167」在单表无重复行约束下不能同时成立（按工单等式②，回滚态即 539 + 2,167 + 0 == 2,706）。本轮按等式②执行：**C 批回滚使主表回到处置前值 539**、总账回 2,706、`MAX(ts_ms)` 回 1793594184555、C 批清零、A 批原样。
- **逐行内容指纹（六列 `id,ts_ms,level,action,message,extra_json` 按 id 升序规范化 sha256）**：C 批 206 行（id 714→2698）指纹在**处置前主表态 = 隔离态 = 回滚态 = 终态隔离态**四态全部 `22fbff87d04758d2f886c554723ff2f4334ea91081aee3c514a839ec2e9222db`（`logs/06,09,13,17,20,23_fp*.json`）——`INSERT…SELECT` 逐列保真、DELETE/回插零损伤。A 批 2,167 行（含隔离原因/批次标记八列）指纹在**处置前（备份恢复件）= 回滚态 = 终态隔离态**全部 `b955196a280bc3b91ac51dfbea5bf070c041362406ed1c84c380de2ae39a7403`（`logs/10b,14,24`）——**A 批全程零触碰**。

### 9.5 re-isolate 终态（08:00:31，`logs/21_reisolate_final.log`、`22_verify_final.log`）

- 终态：主表 **333**、A 批 **2,167**、C 批 **206**、`MAX(ts_ms)=1793594173715`、`integrity_check=ok`、隔离态 verify **VERIFY PASS exit 0**。

### 9.6 两口径全量与三文件零增长（工单硬约束 6）

- 隔离终态基线快照（08:00:51，`logs/25_metrics_pre_tests.json`）：`history.db` 2,469,888 B sha `051d16889e0e90de…`、`-wal` 0 B sha `e3b0c442…`、`-shm` 32,768 B sha `fd4c9fda9cd3f9ae…`、`generation_versions.db` 65,536 B sha `41ab2af72b3af97c…`。
- 两口径（系统 Python 代跑；skip 数沿阶段 11 口径不可与阶段 10 横比，**不变量为 0 failed / exit 0**）：

| 口径 | 命令（同阶段 11） | 窗口 | 结果 | exit |
|---|---|---|---|---|
| A ×1 | `pytest tests/ -v --tb=short --ignore=tests/e2e` | 08:01:06→08:02:32 | **2545 passed / 0 failed / 117 skipped**（82.67s） | 0 |
| A ×2 | 同上 | 08:02:38→08:03:55 | **2545 passed / 0 failed / 117 skipped**（74.43s） | 0 |
| B ×1 | A 集 + `-m "not integration and not gpu and not cuda and not vram and not benchmark and not stress" --cov=app/integrated_app --cov-branch --cov-report=xml/html→仓外 --cov-fail-under=45` | 08:04:02→08:05:52 | **2448 passed / 0 failed / 91 skipped / 123 deselected**；覆盖率 **51.58% ≥ 45%**（106.24s） | 0 |

- 测后快照（08:06:19，`logs/29_metrics_post_tests.json`）：**`history.db` / `-wal` / `-shm` 三文件字节数+sha256 与隔离终态逐字节一致**（`-shm` 内容 sha 亦同，仅 mtime 因只读连接开合刷新），附加 `generation_versions.db` 亦逐字节一致；账目 333/2,167/206、合计 2,706、`MAX(ts_ms)`、integrity ok 全部不变——**哨兵无漏，测试套对生产库零增长在 C 批终态下成立**。覆盖率 51.58% 与阶段 11 的 51.63% 差 0.05pp（同命令同环境的分支覆盖计数运行间波动，远在 45% 门槛内），如实登记。
- 全部产物/日志在仓外 `_演练/F6-batch-C/`；生产工作树 porcelain 全程 0（`.coverage` 为 gitignore 内测试产物，未入库）。

### 9.7 未覆盖清单（本轮没做什么、为什么）

1. **44 行 streaming、22 行真实秒失败、88 行 `model`、179 行未检 `generation`**：全部原地未动（工单明令禁触），归属待决沿阶段 11 §7。
2. **8193：0 行**，无待决项（阶段 11 §2.1 结论本轮复验成立）。
3. **备份链既有挂账不变**：调度注册、异地副本、旧备份物（12:17/12:18/14:29 三份含噪声）重取与否沿阶段 9/10；本轮新增仓外备份 `history_backup_20261103_075607.db` 为 C 批处置前干净快照（含 A 批隔离表）。
4. **`.venv` 失效未修**（硬约束 6），本轮与阶段 11 同用系统 Python 3.12.10。
5. 未触碰：`scripts/rollback_release.py`、`requirements-lock.txt`、`tests/conftest.py` 与哨兵测试、远端（无 push/无 tag/无分支切换/无 worktree）、`core.hooksPath`、服务启停、系统计划任务、其他仓库。

### 9.8 留痕索引（仓外 `_演练/F6-batch-C/`）

`scripts/{census_c, quarantine_f6_action_logs_c, fingerprint_c, db_metrics_c}.py` + `preflight` 只读内联查询
（全部只读，除隔离/回滚事务内部删除外无任何写操作；隔离执行器为阶段 10 原件派生，差异仅批次/判据/verify）；
`logs/census_pre_isolation.log`（07:52:44）、`preflight.log`（07:55:52）、`02_backup.log`、`03_backup_sha.log`、
`04_restore.log`、`05_restore_readback.log`、`06_fp_pre_isolation.json`、`07_isolate.log`（首跑）、
`08_verify_isolated.log`、`09_fp_isolated.json`、`10_fp_A_isolated.json`（缺考——内联脚本 import 缺陷，
A 批处置前指纹以备份件锚定见 `10b`）、`10b_fp_A_backup_vs_rollback.log`、`11_rollback.log`（首跑，含校准缺陷 FAIL 原样留痕）、
`12_verify_rollback.log`（同上）、`12b_verify_rollback_fixed.log`、`13_fp_rollback.json`、`14_fp_A_rollback.json`、
`15–20`（干净复跑：isolate/verify/指纹/rollback/verify/指纹）、`21_reisolate_final.log`、`22_verify_final.log`、
`23_fp_final_C.json`、`24_fp_final_A.json`、`25_metrics_pre_tests.json`、`26_runA1.log`、`27_runA2.log`、`28_runB.log`、
`29_metrics_post_tests.json`；`backups/history_backup_20261103_075607.db`（处置前快照）、
`restore_drill/history.db`（恢复验证件）、`coverage.xml`、`htmlcov/`（口径 B 覆盖率产物）。
