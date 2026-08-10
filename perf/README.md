# perf/ 性能监控脚本目录

本目录包含 TTS MultiModel 平台的独立性能监控脚本，用于度量核心性能指标：
冷启动时间、显存占用、生成实时率（RTF）和并发压力。

## 脚本清单

| 脚本 | 功能 | 核心指标 |
|------|------|----------|
| `cold-start.py` | 冷启动时间度量 | 进程→ping / ping→ready / 引擎加载 耗时 |
| `vram-usage.py` | 显存占用持续监控 | 显存峰值/谷值/平均、GPU利用率、引擎切换事件 |
| `generation-benchmark.py` | 生成速度基准 | RTF = 生成时间/音频时长，按文本长度/说话人分组 |
| `stress-test.py` | 并发压力测试 | QPS、avg/P95/P99延迟、错误率（5/10/20路并发） |
| `report_generator.py` | 结果可视化 | 汇总 JSON 为 Chart.js HTML 交互式报告 |

## 快速开始

```bash
# 1. 启动 TTS MultiModel 服务（另开终端）
start.bat  # 或 start.sh

# 2. 运行各项性能测试
python perf/cold-start.py --engine voxcpm2
python perf/vram-usage.py --duration 60
python perf/generation-benchmark.py --engine voxcpm2 --repeats 3
python perf/stress-test.py --levels 5,10,20

# 3. 生成可视化 HTML 报告
python perf/report_generator.py

# 4. 用浏览器打开报告
# perf/results/report.html
```

## 输出

所有脚本将 JSON 结果写入 `perf/results/` 目录，文件名含时间戳。
`report_generator.py` 汇总所有 JSON 为 `perf/results/report.html`。

## 与 tests/benchmarks/ 的区别

| 特性 | `tests/benchmarks/` | `perf/` |
|------|---------------------|---------|
| 格式 | pytest 测试（需 pytest-benchmark） | 独立 Python 脚本 |
| 运行方式 | `pytest tests/benchmarks/` | 直接 `python perf/xxx.py` |
| 测试对象 | 核心工具函数（文本分割/缓存/音频合并） | 端到端 API 性能（冷启动/RTF/压测） |
| 结果输出 | pytest 控制台 + JSON | JSON + Chart.js HTML 报告 |
| 适用场景 | CI 回归基准 | 手动性能评估、瓶颈分析 |

## 依赖

所有脚本仅依赖 `httpx`（已在项目 requirements.txt 中），无需额外安装。
报告页通过 CDN 引入 Chart.js，需联网加载（仅查看报告时）。
