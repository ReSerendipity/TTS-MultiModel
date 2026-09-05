# Benchmark 基线存储

本目录存放 TTS_MultiModel 的性能基准测试基线，采用**三级存储策略**：

| 层级 | 位置 | 持久性 | 用途 |
|------|------|--------|------|
| L1 热缓存 | CI `actions/cache` (`output/benchmarks/`) | 7 天自动驱逐 | PR 回归对比（`--benchmark-compare`） |
| L2 发布资产 | GitHub Release Assets (`benchmark-baseline-*.json`) | 永久 | 跨版本趋势对比、release 时锚定 |
| L3 仓库基线 | `benchmarks/baseline.json` | 版本控制 | 手动锚定的里程碑基线，可 code review |

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

### 自动（CI）
- main 分支 push 时，CI 自动运行 benchmark 并更新 L1 缓存
- tag push（release）时，CI 自动导出基线并上传为 Release Asset（L2）

### 手动（里程碑）
```bash
# 1. 本地运行 benchmark
pytest tests/benchmarks/ --benchmark-only --benchmark-storage=output/benchmarks --benchmark-save=manual

# 2. 导出为仓库基线
python scripts/export_benchmark_baseline.py output/benchmarks benchmarks/baseline.json

# 3. 提交
git add benchmarks/baseline.json
git commit -m "benchmark: anchor baseline vX.Y.Z"
```

## 回归门禁

PR 中 CI 执行：
```bash
pytest tests/benchmarks/ --benchmark-only --benchmark-storage=output/benchmarks \
  --benchmark-compare --benchmark-compare-fail=mean:20%
```
均值回退超过 20% 即阻断合并。
