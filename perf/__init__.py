"""TTS MultiModel 性能监控脚本包。

本目录包含独立的性能监控脚本，用于度量 TTS 平台的核心性能指标：
冷启动时间、显存占用、生成实时率（RTF）和并发压力。

脚本清单：
    cold-start.py            — 冷启动时间度量（从进程启动到 API 可响应）
    vram-usage.py            — 显存占用持续监控（引擎切换时）
    generation-benchmark.py  — 生成速度基准（RTF = 生成时间 / 音频时长）
    stress-test.py           — 并发压力测试（5/10/20 路 QPS 与错误率）
    report_generator.py      — 将 JSON 结果汇总为 Chart.js HTML 报告页

结果输出：
    所有脚本将 JSON 结果写入 perf/results/ 目录，
    由 report_generator.py 汇总为 perf/results/report.html 可视化报告。

用法示例::
    python perf/cold-start.py
    python perf/vram-usage.py --duration 60
    python perf/generation-benchmark.py --engine voxcpm2
    python perf/stress-test.py --levels 5,10,20
    python perf/report_generator.py
"""
