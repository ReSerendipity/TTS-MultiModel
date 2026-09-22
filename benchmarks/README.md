# Benchmark 基线存储

本目录存放 TTS_MultiModel 的性能基准测试基线，采用**三级存储策略**：

| 层级 | 位置 | 持久性 | 用途 |
|------|------|--------|------|
| L1 热缓存 | CI `actions/cache` (`output/benchmarks/`) | **实测两小时就被驱逐**（仓库缓存 10.23 GB / 配额 10 GB） | PR 回归对比的第一顺位，命中就用、不命中退 L3 |
| L2 发布资产 | ~~GitHub Release Assets~~ | — | **已删**：触发器决定那两步永远不会执行，见下面"更新流程" |
| L3 仓库基线 | `benchmarks/baseline.json` | 版本控制 | **当前真正稳定生效的一级**：手动锚定的基线，可 code review，随 checkout 就在 |

## 基线格式

基线文件为 pytest-benchmark 的 storage JSON 格式（单 run），顶层结构：

```json
{
  "benchmark": {
    "name": "baseline-vX.Y.Z",
    "datetime": "2026-09-05T00:00:00",
    "version": "4.0.0",
    "git_commit": "abc1234",
    "machine_info": {...},
    "benchmarks": [
      {
        "name": "test_xxx",
        "stats": {"mean": 0.123, "median": 0.120, "stddev": 0.005, "min": 0.110, "max": 0.150},
        "params": {...}
      }
    ]
  }
}
```

## 更新流程

### 自动（CI）—— 只有 L1，而且 L1 不可靠
- main 分支 push 时 CI 会跑 benchmark 并写 L1 缓存。**但别指望它当基线**：
  2026-09-22 实测本仓库 Actions 缓存已用 **10.23 GB / 配额 10 GB**，main 在 07:42 存的基线
  到 09:42 就被驱逐，同一天 PR 侧 `restore` 直接 `Cache not found for input keys: …,
  benchmark-storage-refs/heads/main`。全仓只剩一条 2.8 KB 的 PR 自己的缓存。
- ~~tag push（release）时 CI 自动导出基线并上传为 Release Asset（L2）~~ —— **不成立**：
  `benchmark.yml` 的触发器里没有 tag（`push` 只跟 `branches: [main]` 且带 `paths` 过滤，
  带 paths 过滤时 tag push 不会触发），那两步的 `if: startsWith(github.ref,'refs/tags/')`
  正常路径下永远为假；v2.2.1/v2.2.2/v2.2.3 三个 Release 的资产里也确实没有
  `benchmark-baseline-*.json`。这两步已于 2026-09-22 从工作流里删掉。

### 真正生效的一级：L3（仓内基线）
`benchmark.yml` 的对比步现在按 L1 → L3 退让，并在表头打印用的是哪一级；
两边都没有才"仅记录"。注意 **L3 文件在但 `benchmarks` 是空数组** 不算有基线 ——
会打 `::warning::` 点名它是占位文件（这条仓库里那个文件长期是 0 条的占位）。

刷新 L3（本地跑，不进 CI 写权限）：

```bash
# 1. 跑一遍 benchmark（或从 CI 那次 run 的 artifact 里取 JSON）
pytest tests/benchmarks/ --benchmark-only --benchmark-storage=output/benchmarks --benchmark-save=manual

# 2. 导出为仓库基线（--name 写清来源，便于回看是谁锚的）
python scripts/export_benchmark_baseline.py output/benchmarks/<平台目录> \
    benchmarks/baseline.json --name main-<短 sha>

# 3. 提交
git add benchmarks/baseline.json && git commit -m "benchmark: 锚定 main-<短 sha> 基线"
```

## 回归门禁

判据（2026-09-22 换过一次，原因见 `tests/benchmarks/GATE_POLICY.md` 与 issue #118）：
只比 **`median`**、**单向**、阈值 **50%**，变快不判失败；逐条打印
`基线 median / 本轮 median / 变化% / 判定`，不再整段重定向到 step summary。
门禁逻辑本身由 `tests/test_benchmark_gate.py` 覆盖（它从工作流 YAML 里抽出真正执行的那段脚本，
喂 L1/L3/空占位/缺本轮等场景）—— 改脚本必须同时让它继续非空洞。
