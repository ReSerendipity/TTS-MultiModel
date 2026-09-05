"""追加数据治理实施阶段的 GOTCHAS / SOPS / REVISION_LOG。"""

from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent

# ── GOTCHAS.md 追加 ──
gotchas_path = ROOT / "docs" / "agents" / "GOTCHAS.md"
gotchas_add = """

### #44 PowerShell 特殊字符解析陷阱（数据治理实施）
- **触发场景**：在 PowerShell 中执行含 `>1000`、`*`、`|` 等字符的 Python 内联命令。
- **现象/报错**：`>1000` 被解析为输出重定向、`*` 被解析为通配符、`|` 被解析为管道，导致 SQL 语句截断或语法错误。
- **正确做法**：一律将 SQL/Python 逻辑写入 `.py` 脚本文件，用 `.venv\\Scripts\\python.exe script.py` 执行，避免内联命令的特殊字符解析问题。
- **首次发现日期**：2026-09-05

### #45 action_logs 表非死表（评估报告结论修正）
- **触发场景**：数据治理评估报告称 action_logs 为"死表，现行代码无 INSERT 写入方"。
- **现象/报错**：经 grep 核实，`routes/system/logs.py` 有完整的 INSERT/SELECT/DELETE 逻辑（L227 INSERT, L248 _ensure_action_logs_table, L363/377 查询, L473/477/483 清理）。
- **正确做法**：评估报告中关于 action_logs 的结论有误，该表是活跃的审计日志表，不应 DROP。遇到评估结论时需交叉验证代码实际读写方。
- **首次发现日期**：2026-09-05

### #46 created_at 双格式 + 批量插入时间不一致
- **触发场景**：统一 created_at 与 created_timestamp 时间口径时。
- **现象/报错**：137 条记录中 111 条 created_at 为 ISO 格式（`2026-05-04T19:05:01.065337`），22 条为空格格式；15 条批量插入记录 created_timestamp 用了统一 `time.time()` 而 created_at 为各自真实时间。
- **正确做法**：①`_build_record_tuple` 中兼容三种格式解析（`%Y-%m-%dT%H:%M:%S.%f` / `%Y-%m-%dT%H:%M:%S` / `%Y-%m-%d %H:%M:%S`），统一输出空格格式；②以 created_timestamp 为权威时间源，created_at 由其派生；③批量插入时每条记录应使用各自的 created_at 解析时间戳，而非统一 time.time()。
- **首次发现日期**：2026-09-05
"""

with open(gotchas_path, "a", encoding="utf-8") as f:
    f.write(gotchas_add)
print("GOTCHAS.md 已追加 3 条新坑")

# ── SOPS.md 追加 ──
sops_path = ROOT / "docs" / "agents" / "SOPS.md"
sops_add = """

### SOP-14：数据治理评估报告落地实施
**适用场景**：根据数据治理评估报告（含 P0/P1/P2 优先级 + 验收标准）全量落地实施所有治理任务。

**步骤**：
1. **前置检查**：Git 工作区状态、应用是否运行、测试基线（pytest 通过率）、Python 环境确认。
2. **优先级排序执行**：严格按 P0 → P1 → P2 顺序，每项任务完成后对照验收标准自检（写 verify_*.py 脚本）。
3. **数据变更先备份**：任何修改数据库的操作前，先 `shutil.copy2` 备份，确认无误后再清理。
4. **代码修改最小侵入**：优先在现有方法中扩展，不重构未点名的模块；新增功能用配置开关控制默认行为。
5. **全量测试**：`pytest tests/ -q --ignore=tests/e2e` + `powershell -File precheck.ps1`；flaky test 单独运行确认与本次修改无关。
6. **ruff 全量修复**：`ruff check --fix .` + `ruff format .`，注意 ruff 可能修改未被本次点名的文件（格式统一），提交时区分本次逻辑变更与格式变更。
7. **文档同步**：追加 GOTCHAS（新坑）、SOPS（新流程）、REVISION_LOG（版本递增）。
8. **分批提交**：按模块依赖顺序（底层 config/db → 上层 routes/server → 脚本 → 文档），每批附规范 commit message。

**关键约束**：
- PowerShell 内联命令含特殊字符时一律改用 .py 脚本文件。
- 评估报告结论需交叉验证代码实际读写方，不盲目信任。
- 数据修复后必须重算 HMAC 链（如果修改了 HMAC 输入字段）。

**首次记录日期**：2026-09-05
"""

with open(sops_path, "a", encoding="utf-8") as f:
    f.write(sops_add)
print("SOPS.md 已追加 SOP-14")

# ── REVISION_LOG.md 追加 ──
rev_path = ROOT / "docs" / "agents" / "REVISION_LOG.md"
rev_add = f"""
| v1.26 | {date.today().isoformat()} | **数据治理评估 v2.2.1 全量落地实施（P0×3 + P1×4 + P2×4）** | ①P0：脏数据清理（2075MB→0.7MB，64条output_format修正+VACUUM）、留存策略统一（get_effective_retention_days，消除keep_days与pii_retention_days冲突）、PII密钥轮换脚本（rotate_pii_key.py，dry-run验证137条可解密）；②P1：HMAC哈希链加固（密钥迁移到%APPDATA%/tts-multimodel/、启动verify+一次性回填、删除后重算链、insert_batch补HMAC，137条verified=True）、orphan扫描接入lifespan（103条file_missing标记）、训练血缘接线（train_voxcpm_finetune.py新增_collect_lineage_info+run_manifest.json写入，action_logs经核实为活跃表非死表）、时间口径统一（133条修复，created_at格式统一+以created_timestamp为权威，HMAC链重算）；③P2：版本化迁移机制（_schema_migrations表+5个版本迁移，替代运行时探测）、generation_versions谱系表下线（清理95条测试数据+config开关默认关闭）、personas/i18n校验脚本（7音色四件套完整+5语言877key一致）、outputs生命周期治理（清理1.2GB迁移备份+clean_outputs.py脚本+config策略说明）；④全量测试1745 passed（2 flaky与本次无关）、precheck全绿；⑤ruff修复60+处格式/风格问题。 | v2.2.1 | ✓(check_spec_refs) |
"""

with open(rev_path, "a", encoding="utf-8") as f:
    f.write(rev_add)
print("REVISION_LOG.md 已追加 v1.26")
