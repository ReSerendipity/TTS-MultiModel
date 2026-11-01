# 目标库级备份恢复与发布回滚演练记录（2026-10-31 · 阶段 1）

> 对应评估门禁：**R4**（回滚预案已演练）、**D6**（备份恢复已实测·目标库级）、**O4**（备份恢复定期实测）
> 依据：2026-10-29 评估报告 §11.2 与 `gate.p0_fail_detail`（绝对 P0 中本轮做 R4/D6/O4；O3 与 E7/P25 分属阶段 2、3，未做）
> 台账落点：`docs/SRE_RUNBOOK.md` §3.4「演练台账」；本文件为该次演练的完整记录
> 执行窗口：2026-10-31 深夜开工、跨午夜至 2026-11-01 凌晨（下文「演练日期」按此窗口口径）
> 执行约束：生产库零写入；未启动/停止/重启任何服务、桌面实例或 GPU 推理进程；全部写操作发生在仓库外临时目录；离线，无子智能体、单线程顺序执行

---

## 1. 前提与环境

- 实测 HEAD：`f5f22d0`（fix(build): 打包时显式补拷完整性公钥进卷）
- 开工工作树：4 项外部在途文件（`desktop/src-tauri/src/window.rs`、`desktop/src/desktop-bridge.js`、`desktop/src/index.html`、`scripts/installer/setup.nsi`）——全程未触碰、未纳入任何提交；收工 git status 与开工逐行一致
- 演练空间：仓库外临时目录（会话级 %TEMP% 之下，演练结束后可整目录删除）；生产库本体与既有备份物只读
- 证据口径：只输出表名/行数/字段名/校验和，未读取或粘贴任何用户数据正文

## 2. 生产库只读取证（`data/history.db`）

方式：`stat` + SQLite 只读 URI（`file:...?mode=ro`）+ `PRAGMA query_only=ON` 双保险。

| 项 | 生产库（实况） | 既有备份物 `data/backups/history_bak_20260905.db` |
|---|---|---|
| 字节数 | **2,150,400**（≈2.05MiB） | **2,175,934,464**（≈2.03GiB，即评估报告口径的「约 2.1GB」对象） |
| mtime | 2026-10-31 22:02:53（活跃库，旁路 `-wal` 5,088,232 字节、`-shm` 32,768 字节） | 2026-09-05 15:44 |
| journal_mode | wal | wal |
| page_size / page_count | 4096 / 525 | 4096 / 531,234 |
| freelist_count | 165 | 225,080（≈921MB 空闲页：历史大库删除后未 VACUUM 的形态） |
| integrity_check | **ok**（0.029s） | **ok**（9.505s） |
| 表数 / 总行数 | **9 表 / 3,635 行** | 8 表 / 770 行 |

生产库表清单（行数）：`_schema_migrations` 8、`action_logs` 2337、`generation_history` 342、`generation_history_fts` 342、`generation_history_fts_config` 1、`generation_history_fts_data` 129、`generation_history_fts_docsize` 342、`generation_history_fts_idx` 127、`meta_kv` 7。

**口径差异登记**：评估报告称「2.1GB 生产 history.db」，实况生产库主文件仅 ≈2.05MiB；2.1GB 量级对应的是 2026-09-05 的真实备份物。差异原因：PII 留存清理后库文件缩小（`data/history.db.bak-20261006` 已是 1.4MB 量级），报告取证对象应为 `data/backups/` 内的备份物。本轮对**两类对象都**做了目标库级实测，覆盖两种口径。

sha256（全量口径）：
- 生产库主文件：`23ee503bc06b34cae99e94dff7f74c9496c0fb8004c58acc653c977be69536f1`
- 实况快照（见 §3）：`44ffde0d372049ca1f2c7510d5ddeea72b98605330b7dc93662ac00af9e05773`
- 2.1GB 备份物：`c285dcedda8a3f04dedcceaa0c1d4473d62a6228e9155910dad22197208f96f5`

## 3. 目标库级恢复实测（仓库外临时目录）

真实恢复链，全部使用仓内入口 `scripts/backup_history_db.py`（`--db-path` / `--backup-dir` / `--restore` / `--target` 显式路径）：

1. **备份链**：`--db-path <生产库> --backup-dir <临时目录>/snapshot --keep 0` → SQLite 在线备份 API 一致性快照 `history_backup_20261101_001227.db`（2.05MB，退出码 0，主路径成功、未触发文件复制回退）
2. **恢复链 ①（实况快照）**：`--restore <快照> --target <临时目录>/restored_live/history.db` → 退出码 0，入口内置恢复后 `PRAGMA integrity_check` 通过，**耗时 0.645s**
3. **恢复链 ②（2.1GB 备份物）**：`--restore data/backups/history_bak_20260905.db --target <临时目录>/restored_bak2g/history.db` → 退出码 0，内置完整性校验通过，**耗时 10.542s（含 2.1GB 复制 + 恢复后完整性校验）→ RTO≈10.5s（2.1GB 级）**

**逐表对平（源 vs 恢复，双只读连接）**：

| 对 | 结果 |
|---|---|
| 实况快照 vs 恢复① | **9/9 表对平**：表清单一致、逐表行数一致（3635 总行）、逐表字段名一致、按主键排序 5 行抽样 sha256 全一致 |
| 2.1GB 备份物 vs 恢复② | **8/8 表对平**：同口径（770 总行），抽样校验和全一致 |

抽样口径：每表按主键（无主键表按 rowid）排序取 5 行，对行内容做 sha256 摘要比对，仅输出摘要与布尔结果，正文不出临时目录。失败项：无。

## 4. 发布回滚演练（离线，不碰线上服务）

按 `docs/release-governance.md` §5 与 `docs/rollback_sop.md` 的既有口径，在**仓库外临时克隆**（`git clone --no-hardlinks`，源仓零影响）执行；`scripts/rollback_release.py` 按 git cwd 契约在克隆目录内运行：

1. `--list`：22 个 tag 正常列出（退出码 0）
2. `--target v2.8.0 --dry-run`：目标提交 `1ffccfd6`，预览反向 revert 44 个提交（退出码 0）
3. **`--target v2.8.0` 真跑**：成功生成 5 个 revert 提交后，在第 6 个目标 **merge 提交 `8a0851da`（PR #239 合并点）处失败，退出码 128**——`git revert` 对 merge 提交缺 `-m` 选项；单点手工验证 `git revert --no-edit -m 1 8a0851da` 退出码 0（修复方向可行，脚本修复不在本阶段范围）
4. **tag 内容还原路径**（工单允许的替代口径）：`git archive v2.8.0` 提取到独立目录，验证回滚后版本位与离线健康：
   - `tests/test_version_consistency.py`：**3 failed / 9 passed** —— v2.8.0 tag 树版本位自相矛盾：RP 自动位 2.8.0（`version.json`、`desktop/package.json`、`tauri.conf.json`、`Cargo.toml`）vs 手工位 2.7.0（`config.yaml`、`deploy/kubernetes/deployment.yaml`、`Cargo.lock`、`setup.nsi` OutFile）；且 `version.json` 自称 2.8.0、内嵌 changelog 仍在讲 2.7.0
   - `tests/test_readyz.py`：**3/3 passed** —— 回滚态代码上 `/readyz` 门控行为正确（进程内 TestClient 离线断言）
   - 基线对照：HEAD（2.9.0）同两文件 12 passed 全绿，证明验证门架本身可用
5. **未对线上实例执行**：健康检查的 `curl /api/health/ping`、`/api/health/ready`（`docs/SRE_RUNBOOK.md` §2.4/§5）需要服务在跑，本轮以离线断言替代并在此显式登记；未重启、未停止、未改动任何运行中的服务或桌面实例

## 5. 发现与改进项（演练产出，按证据登记）

| # | 发现 | 证据 | 建议 | 归属 |
|---|---|---|---|---|
| F1 | `scripts/rollback_release.py` 反向 revert 遇 merge 提交即失败（本仓 PR 合并历史下必然命中） | 真跑退出码 128，首个 merge `8a0851da`；`-m 1` 单点验证可行 | 脚本对 merge 提交传 `-m 1`（或 `--no-merges` + 单点 revert 策略），修复后重跑本演练闭环 R4 | 阶段 2 前置修复项 |
| F2 | 回滚目标 tag（v2.8.0）版本位自相矛盾（自动位 vs 手工位脱节落在 tag 里） | `test_version_consistency` 3 failed（OutFile、内嵌 changelog、`assert 2 == 1`） | 回滚 SOP 增补硬步骤：回滚后必跑 `tests/test_version_consistency.py` 并补齐人工版本位；发版侧把人工位检查前移到 tag 前 | 阶段 2 |
| F3 | `docs/SRE_RUNBOOK.md` §3.3 推荐的每日备份入口 `scripts/backup_db.py` 指向遗留路径 `data/tts_history.db`（不存在，运行即 `[skip]`），真实备份链是 `scripts/backup_history_db.py`（`config.yaml → history.db_path` 单一事实源） | `data/` 目录实测无 `tts_history.db`；两脚本源码比对 | §3.3 修订指向真实链路，避免「每日备份」静默空转 | 阶段 2 |
| F4 | `config.yaml` 既有校验告警（`models.engines.edge-tts.vram_gb=0.0` 触发 pydantic 宽松回退） | 恢复入口运行时 stderr（与本演练无因果关系，顺带登记） | 按 P0-2 宽松回退设计属预期行为；建议修配置值并重存 | 常规维护 |

### 5.1 阶段 3 处置对账（2026-11-01，逐行对账不删行）

| # | 阶段 1 登记内容 | 阶段 3 处置 | 状态 |
|---|---|---|---|
| F1 | `rollback_release.py` 遇 merge 提交反向 revert 失败 | **已修**：脚本加 merge→`revert -m 1` 分支 + range 改 `--first-parent`（线性仓行为不变）；仓外最小仓先复现（脚本退 1，内层 git revert 报 "is a merge but no -m option was given"）→修复→重跑 **exit 0**、树状态判据全过（文件还原、merge 引入文件消失、工作树干净）；`py_compile` 过；仓内无既有覆盖测试（如实登记，以最小仓演练为验证） | 已闭环 |
| F2 | v2.8.0 tag 版本位自相矛盾（内嵌 changelog 自述 2.7.0 + 手工位 2.7.0） | **已补断言**：SRE_RUNBOOK §2.4 新增回滚前硬停断言（tag ↔ version.json ↔ CHANGELOG 顶部 + 内嵌 changelog 自述一致）；按新断言只读判定：v2.8.0=**FAIL**、HEAD v2.9.0=PASS；tag 与历史未动；`tests/test_version_consistency.py` 已在（本轮只许改 rollback_release.py，未改它；「补内嵌说明断言进测试」登记为后续独立 chore） | 断言已入 SOP；测试补强待后续 |
| F3 | SRE_RUNBOOK §3.3 推荐入口 `backup_db.py` 指向不存在的 `data/tts_history.db` | **未处理（待决策）**：§3.3 修订未列入本轮范围；真实链为 `scripts/backup_history_db.py`（本文件 §3 已实测） | 仍待所有者授权修订 |
| F4 | config.yaml edge-tts vram_gb=0.0 pydantic 告警 | **未处理（待决策）**：属配置值修正，本轮禁改 config.yaml。**阶段 5b 补注新后果**：阶段 4 的「无权重引擎」降级判据依赖 `vram_gb == 0.0`，而该值被 pydantic 判为非法（`Input should be greater than 0`，测试日志实测出现两次并触发回退原始 YAML）——即**降级判据建立在一个 schema 认为无效的配置值上**，为此判据实现不得不内置 raw YAML 兜底路径 | 仍待所有者授权修正（与 F4 同源，一并拍板） |

## 6. 门禁状态对照（2026-10-29 评估 → 本轮）

| 门禁 | 前 | 后 | 依据 |
|---|---|---|---|
| D6 备份恢复已实测 | [?] | **[x]** | 目标库级实测完成（§3）：生产实况快照与 2.1GB 既有备份物双对象走真实恢复链，integrity_check ok、9/9 与 8/8 表对平、抽样校验和一致、RTO≈10.5s（2.1GB 级）；台账已建（SRE_RUNBOOK §3.4 + 本文件）；且备份物在 90 天窗口内（2026-09-05） |
| O4 备份恢复定期实测 | [?] | **保持 [?]** | 首个目标库级周期点已建立并台账化，但「定期/周期」需要下一到期轮次佐证；建议到期 2027-01（季度口径，SRE_RUNBOOK §3.2），到期执行后升 [x] |
| R4 回滚预案已演练 | [?] | **保持 [?]** | 演练记录已建立（本文件 §4），但演练结论是回滚链**当前不可直接执行**：F1 脚本 merge 缺陷 + F2 目标 tag 版本位漂移未收口；F1 修复并重跑通过前不升 [x] |

## 7. 未覆盖项与阶段 2/3 前置条件

- **O3（阶段 2）**：单副本单卡冗余与切换实测——本轮未触碰 `deploy/kubernetes/` 清单；镜像/kubectl 回滚面因无集群未演练
- **R4 收口前置**：F1 修复 + 重跑回滚演练；F2 收口（SOP 增补回滚后版本位断言）
- **O4 收口前置**：下一到期轮次（建议 2027-01）目标库级演练并入台账
- **E7/P25（阶段 3）**：Bus Factor 1 登记观察——未做
- 演练产生的仓库外临时目录（快照、两份恢复产物、临时克隆、tag 内容树）在验收后可整目录删除；其中不含生产库本体改动，生产库与既有备份物全程只读

## 8. 附录：命令与退出码

```text
# 生产库只读取证（ro URI + query_only）                       exit 0（integrity ok）
# scripts/backup_history_db.py --db-path <生产库> --backup-dir <tmp>/snapshot --keep 0    exit 0
# sha256sum <生产库> <快照> <2.1GB 备份物>                     exit 0（全量口径）
# scripts/backup_history_db.py --restore <快照> --target <tmp>/restored_live/history.db    exit 0（0.645s）
# scripts/backup_history_db.py --restore <2.1GB 物> --target <tmp>/restored_bak2g/history.db  exit 0（10.542s）
# reconcile（自建只读对平脚本，仓外）x3                       exit 0（9/9；9/9 复跑；8/8）
# rollback_release.py --list                                  exit 0
# rollback_release.py --target v2.8.0 --dry-run               exit 0（44 commits 预览）
# rollback_release.py --target v2.8.0（临时克隆内真跑）        exit 128（merge 提交，F1）
# git revert --no-edit -m 1 8a0851da（单点验证）               exit 0
# git archive v2.8.0 | tar -x -C <tmp>/rollback_content_v2.8.0  exit 0
# pytest tests/test_version_consistency.py tests/test_readyz.py（HEAD 基线，克隆内）  exit 0（12 passed）
# pytest 同上（v2.8.0 还原树）                                exit 1（3 failed / 9 passed；readyz 3/3 过）
```
