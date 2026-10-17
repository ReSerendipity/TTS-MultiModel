# .qoder 本地资产层：快照 / 回退点机制

> 对应 Better Harness 批次待批 item6.3。执行载体：`scripts/qoder_snapshot.py`；
> 测试：`tests/test_qoder_snapshot.py`（6 条，全部 hermetic，不碰真实 `.qoder/`）。

## 1. 口径与依据（为什么是快照，不是入库）

`.qoder/`（Qoder 钩子注册 `settings.json`、编辑后验证钩子 `hooks/edit_verify_loop.py`、
`rules/`、`skills/`、`commands/`、`tools/` 等）按 **AGENTS.md §00 定案**整层
**不入库**：`.gitignore` L209 忽略整目录，`git ls-files .qoder/` 跟踪数为 0。
该定案的理由仍然成立——这一层是本机 IDE 生命周期资产，新克隆 / CI / 干净检出里
本来就必然不存在（见 AGENTS.md 干净检出检查项里的同款警告）。

但"不入库"带来一个被 P5 暴露的问题：**没有回退点**。P5（把 event 贯穿 4 个调用点）
因此"既不能撤销也不能证明"——`edit_verify_loop.py` 不在版本控制内，12 项 selftest
也没有超时分支夹具。

本批次推荐口径：**给快照/回退点机制，而不是把整层入库**。即保持 `.qoder/` 的
gitignore 边界不变，另建一套本地快照，让这层资产的每次危险演进前都有一个
可一键回退的锚点。

## 2. 用法

```bash
# 改 .qoder 里任何脚本/配置之前，先打一个回退点：
python scripts/qoder_snapshot.py create --note "改 edit_verify_loop 前留底"

# 看看有哪些快照（逐份复验 sha256，CORRUPT 会点名）：
python scripts/qoder_snapshot.py list

# 当前 .qoder 与某个快照差在哪（退出码 1 = 有差异）：
python scripts/qoder_snapshot.py diff qoder-snap-20261017T110555Z --current

# 回退到某个快照（自动先把当前状态存为 qoder-pre-restore-*，fail-closed）：
python scripts/qoder_snapshot.py restore qoder-snap-20261017T110555Z

# 只保留最近 N 份（默认 10），最旧的先删：
python scripts/qoder_snapshot.py prune --keep 5
```

存储位置：默认 `<repo>/.qoder-snapshots/`（与 `.qoder/` 同在 `.gitignore`，
不会误入库）。`--source` / `--store` 可覆盖，测试即用 tmp_path 驱动。

## 3. 设计约束（为什么这样做）

- **还原前必留备份**：`restore` 先把当前 `.qoder/` 存为一份 `qoder-pre-restore-*`
  快照再动手——回退点自己也要有回退点，防止"回退到更坏的状态"。
- **fail-closed**：还原前逐文件复验快照清单的 sha256，任一缺失/不符即拒绝执行
  （退出码 2），绝不写半个坏树。半拷贝 / 磁盘损坏因此不会借还原之手扩散。
- **清单即证据**：每份快照带 `manifest.json`（逐文件 sha256 + 大小 + 当时的
  `git rev-parse HEAD` 锚点），`list` 会复验；锚点让人能把本地资产态与提交态对上。
- **离线优先**（AGENTS.md 硬约束 #5）：只读写本地文件；唯一外部调用是取 git HEAD
  做锚点，取不到记 `null`，不影响任何子命令成败。

## 4. 已知边界（如实说明，别当成承诺）

- `git clean -xfd` 会连忽略文件一起删，**仓内快照同样会被清掉**。需要抗清理的
  回退点，用 `--store` 指向仓外耐久位置（如另一块盘 / 同步目录）。
- 快照是 `.qoder/` 的**整树副本**，含日志与 fixtures；本仓实测约 200 文件 /
  ~20 MB，`prune` 负责控制总量。
- IDE **无热重载**：改过 `.qoder/hooks/` 后必须重启 IDE 才生效——还原也一样，
  还原后不重启则运行中的还是旧逻辑。
- 本机制不改变"`.qoder/` 不入库"的边界，也不构成对 CI / 新克隆的任何承诺
  （那里本来就没有这一层）。
