"""性能监控结果可视化报告生成器。

读取 perf/results/ 目录下的所有 JSON 结果文件，汇总为单个 HTML 报告页，
使用 Chart.js（CDN 引入）绘制交互式图表。

图表清单：
    1. 冷启动时间柱状图（process→ping、ping→ready、引擎加载）
    2. 显存占用折线图（时间序列）
    3. RTF 对比图（按文本长度分组）
    4. 压力测试 QPS / 延迟 / 错误率对比图

输出：
    perf/results/report.html

用法::
    python perf/report_generator.py
"""

from __future__ import annotations

import json
from datetime import datetime
from html import escape
from pathlib import Path

_PERF_DIR = Path(__file__).resolve().parent
_RESULTS_DIR = _PERF_DIR / "results"


def _load_all_results() -> dict[str, list[dict]]:
    """加载 results 目录下的所有 JSON 结果文件，按脚本类型分组。

    Returns:
        {script_name: [result_dict, ...], ...}
    """
    grouped: dict[str, list[dict]] = {}
    if not _RESULTS_DIR.exists():
        return grouped

    for f in sorted(_RESULTS_DIR.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            script = data.get("script", "unknown")
            grouped.setdefault(script, []).append(data)
        except (json.JSONDecodeError, OSError):
            pass

    return grouped


def _build_cold_start_charts(data_list: list[dict]) -> str:
    """构建冷启动时间图表的 JSON 数据。"""
    if not data_list:
        return ""

    labels = [r.get("timestamp", "")[:19] for r in data_list]
    ping_data = [r.get("process_to_ping_ms", 0) for r in data_list]
    ready_data = [r.get("ping_to_ready_ms", 0) for r in data_list]
    engine_data = [r.get("engine_load_ms", 0) for r in data_list]

    chart_json = json.dumps(
        {
            "labels": labels,
            "datasets": [
                {"label": "进程→ping (ms)", "data": ping_data, "backgroundColor": "#36a2eb"},
                {"label": "ping→ready (ms)", "data": ready_data, "backgroundColor": "#ff6384"},
                {"label": "引擎加载 (ms)", "data": engine_data, "backgroundColor": "#ff9f40"},
            ],
        }
    )

    return f"""
    <h3>❄️ 冷启动时间</h3>
    <canvas id="coldStartChart" height="100"></canvas>
    <script>
    new Chart(document.getElementById('coldStartChart'), {{
        type: 'bar',
        data: {chart_json},
        options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true, title: {{ text: '毫秒' }} }} }} }}
    }});
    </script>
    """


def _build_vram_charts(data_list: list[dict]) -> str:
    """构建显存占用图表的 JSON 数据。"""
    if not data_list:
        return ""

    # 取最新一次监控
    latest = data_list[-1]
    samples = latest.get("samples", [])
    if not samples:
        return ""

    labels = [f"{s.get('elapsed_s', 0):.0f}s" for s in samples]
    vram_data = [s.get("vram_used_mb", 0) for s in samples]
    util_data = [s.get("gpu_util_pct", 0) for s in samples]

    # 标记引擎切换事件
    events = latest.get("events", [])
    event_annotations = []
    for evt in events:
        if evt.get("action") == "switch":
            event_annotations.append(f"切换→{evt.get('engine', '?')} ({evt.get('duration_ms', 0):.0f}ms)")

    vram_json = json.dumps(
        {
            "labels": labels,
            "datasets": [
                {"label": "显存已用 (MB)", "data": vram_data, "borderColor": "#36a2eb", "fill": False, "yAxisID": "y"},
            ],
        }
    )
    util_json = json.dumps(
        {
            "labels": labels,
            "datasets": [
                {"label": "GPU利用率 (%)", "data": util_data, "borderColor": "#ff9f40", "fill": False, "yAxisID": "y1"},
            ],
        }
    )

    event_note = ""
    if event_annotations:
        event_note = f"<p><strong>事件:</strong> {escape(' | '.join(event_annotations))}</p>"

    summary = latest.get("summary", {})
    summary_note = (
        f"<p><strong>峰值:</strong> {summary.get('vram_peak_mb', 0):.1f}MB | "
        f"<strong>平均:</strong> {summary.get('vram_avg_mb', 0):.1f}MB | "
        f"<strong>设备:</strong> {escape(str(summary.get('device_name', '?')))}</p>"
    )

    return f"""
    <h3>📊 显存占用趋势</h3>
    {summary_note}
    {event_note}
    <canvas id="vramChart" height="100"></canvas>
    <script>
    new Chart(document.getElementById('vramChart'), {{
        type: 'line',
        data: {vram_json},
        options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true, position: 'left', title: {{ text: 'MB' }} }} }} }}
    }});
    </script>
    <canvas id="utilChart" height="100"></canvas>
    <script>
    new Chart(document.getElementById('utilChart'), {{
        type: 'line',
        data: {util_json},
        options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true, position: 'left', title: {{ text: '%' }} }} }} }}
    }});
    </script>
    """


def _build_gen_bench_charts(data_list: list[dict]) -> str:
    """构建生成速度 RTF 对比图表。"""
    if not data_list:
        return ""

    latest = data_list[-1]
    results = latest.get("results", [])
    successful = [r for r in results if not r.get("error")]

    if not successful:
        return ""

    # 按文本长度分组取平均 RTF
    by_length: dict[str, list[float]] = {}
    for r in successful:
        by_length.setdefault(r["text_key"], []).append(r["rtf_estimated"])

    labels = list(by_length.keys())
    rtf_data = [round(sum(v) / len(v), 3) for v in by_length.values()]

    chart_json = json.dumps(
        {
            "labels": labels,
            "datasets": [
                {"label": "RTF (估算)", "data": rtf_data, "backgroundColor": "#4bc0c0"},
            ],
        }
    )

    summary = latest.get("summary", {})
    summary_note = (
        f"<p><strong>引擎:</strong> {escape(str(latest.get('engine', '?')))} | "
        f"<strong>RTF 平均:</strong> {summary.get('rtf_avg', 0):.3f} | "
        f"<strong>RTF 范围:</strong> [{summary.get('rtf_min', 0):.3f}, {summary.get('rtf_max', 0):.3f}]</p>"
    )

    return f"""
    <h3>⚡ 生成速度 RTF（Real-Time Factor）</h3>
    {summary_note}
    <p><em>RTF &lt; 1 = 比实时快 | RTF &gt; 1 = 比实时慢</em></p>
    <canvas id="genBenchChart" height="100"></canvas>
    <script>
    new Chart(document.getElementById('genBenchChart'), {{
        type: 'bar',
        data: {chart_json},
        options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true, title: {{ text: 'RTF' }} }} }} }}
    }});
    </script>
    """


def _build_stress_charts(data_list: list[dict]) -> str:
    """构建压力测试对比图表。"""
    if not data_list:
        return ""

    latest = data_list[-1]
    results = latest.get("results", [])
    if not results:
        return ""

    labels = [f"{r['concurrency']}并发" for r in results]
    qps_data = [r.get("qps", 0) for r in results]
    p95_data = [r.get("p95_latency_ms", 0) for r in results]
    err_data = [r.get("error_rate", 0) * 100 for r in results]

    qps_json = json.dumps(
        {
            "labels": labels,
            "datasets": [
                {"label": "QPS", "data": qps_data, "borderColor": "#36a2eb", "fill": False},
            ],
        }
    )
    p95_json = json.dumps(
        {
            "labels": labels,
            "datasets": [
                {"label": "P95 延迟 (ms)", "data": p95_data, "borderColor": "#ff6384", "fill": False},
            ],
        }
    )
    err_json = json.dumps(
        {
            "labels": labels,
            "datasets": [
                {"label": "错误率 (%)", "data": err_data, "borderColor": "#ff9f40", "fill": False},
            ],
        }
    )

    endpoint = escape(str(latest.get("endpoint", "?")))
    per_level = latest.get("per_level", 0)

    return f"""
    <h3>🔥 并发压力测试</h3>
    <p><strong>端点:</strong> {endpoint} | <strong>每级别请求数:</strong> {per_level}</p>
    <canvas id="stressQpsChart" height="80"></canvas>
    <script>
    new Chart(document.getElementById('stressQpsChart'), {{
        type: 'line',
        data: {qps_json},
        options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true, title: {{ text: 'QPS' }} }} }} }}
    }});
    </script>
    <canvas id="stressP95Chart" height="80"></canvas>
    <script>
    new Chart(document.getElementById('stressP95Chart'), {{
        type: 'line',
        data: {p95_json},
        options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true, title: {{ text: 'ms' }} }} }} }}
    }});
    </script>
    <canvas id="stressErrChart" height="80"></canvas>
    <script>
    new Chart(document.getElementById('stressErrChart'), {{
        type: 'line',
        data: {err_json},
        options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true, title: {{ text: '%' }} }} }} }}
    }});
    </script>
    """


def generate_report() -> Path:
    """生成 HTML 报告并返回输出路径。"""
    grouped = _load_all_results()

    cold_start_html = _build_cold_start_charts(grouped.get("cold-start", []))
    vram_html = _build_vram_charts(grouped.get("vram-usage", []))
    gen_bench_html = _build_gen_bench_charts(grouped.get("generation-benchmark", []))
    stress_html = _build_stress_charts(grouped.get("stress-test", []))

    has_data = any(grouped.values())
    if not has_data:
        no_data_html = """
        <div style="text-align:center; padding:60px; color:#888;">
            <h2>暂无性能数据</h2>
            <p>请先运行以下脚本生成数据：</p>
            <pre>python perf/cold-start.py
python perf/vram-usage.py --duration 60
python perf/generation-benchmark.py
python perf/stress-test.py</pre>
            <p>然后重新运行 <code>python perf/report_generator.py</code></p>
        </div>
        """
    else:
        no_data_html = ""

    # 统计数据文件数
    file_counts = {k: len(v) for k, v in grouped.items()}

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TTS MultiModel 性能监控报告</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f5f5; color: #333; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px 20px; text-align: center; }}
        .header h1 {{ font-size: 28px; margin-bottom: 8px; }}
        .header p {{ opacity: 0.9; }}
        .container {{ max-width: 960px; margin: 0 auto; padding: 20px; }}
        .stats-bar {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 20px 0; }}
        .stat-card {{ flex: 1; min-width: 140px; background: white; border-radius: 8px; padding: 16px; text-align: center; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .stat-card .num {{ font-size: 24px; font-weight: 700; color: #667eea; }}
        .stat-card .label {{ font-size: 13px; color: #888; margin-top: 4px; }}
        .chart-section {{ background: white; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .chart-section h3 {{ margin-bottom: 16px; color: #333; border-left: 4px solid #667eea; padding-left: 10px; }}
        .chart-section canvas {{ max-height: 300px; }}
        .chart-section p {{ margin-bottom: 10px; color: #666; }}
        .footer {{ text-align: center; padding: 20px; color: #aaa; font-size: 13px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🚀 TTS MultiModel 性能监控报告</h1>
        <p>生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
    </div>
    <div class="container">
        <div class="stats-bar">
            <div class="stat-card"><div class="num">{file_counts.get("cold-start", 0)}</div><div class="label">冷启动测试</div></div>
            <div class="stat-card"><div class="num">{file_counts.get("vram-usage", 0)}</div><div class="label">显存监控</div></div>
            <div class="stat-card"><div class="num">{file_counts.get("generation-benchmark", 0)}</div><div class="label">生成基准</div></div>
            <div class="stat-card"><div class="num">{file_counts.get("stress-test", 0)}</div><div class="label">压力测试</div></div>
        </div>
        {no_data_html}
        <div class="chart-section">{cold_start_html or "<!-- 无冷启动数据 -->"}</div>
        <div class="chart-section">{vram_html or "<!-- 无显存数据 -->"}</div>
        <div class="chart-section">{gen_bench_html or "<!-- 无生成基准数据 -->"}</div>
        <div class="chart-section">{stress_html or "<!-- 无压力测试数据 -->"}</div>
    </div>
    <div class="footer">
        TTS MultiModel Performance Report © ReSerendipity, Apache 2.0
    </div>
</body>
</html>"""

    out_path = _RESULTS_DIR / "report.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    return out_path


def main() -> None:
    out = generate_report()
    grouped = _load_all_results()
    total = sum(len(v) for v in grouped.values())
    print(f"[report] 加载 {total} 个结果文件")
    for script, count in grouped.items():
        print(f"  {script}: {count} 个")
    print(f"[report] HTML 报告已生成: {out}")
    print(f"[report] 用浏览器打开: file:///{out.as_posix()}")


if __name__ == "__main__":
    main()
