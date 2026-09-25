"""流式基线脚本的纯函数测试（度量本身坏了，报出来的 P50/P95 就没意义）。

覆盖 scripts/perf 里不依赖服务端的三块：SSE 行解析、百分位、语料拼装。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# perf/ 目录名带连字符且不是包，只能按路径加载
_SPEC = importlib.util.spec_from_file_location(
    "streaming_baseline", str(Path(__file__).resolve().parents[1] / "perf" / "streaming-baseline.py")
)
assert _SPEC and _SPEC.loader
mod = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("streaming_baseline", mod)
_SPEC.loader.exec_module(mod)


class TestSseParsing:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("event: meta", ("meta", "")),
            ("event:audio", ("audio", "")),
            ('data: {"a":1}', ("", '{"a":1}')),
            ('data:{"a":1}', ("", '{"a":1}')),
            (": ping", ("", "")),  # 心跳注释行必须被忽略而不是当成数据
            ("", ("", "")),
        ],
    )
    def test_line(self, line: str, expected: tuple[str, str]) -> None:
        assert mod.parse_sse_line(line) == expected

    def test_event_then_data_pairing(self) -> None:
        """服务端逐行发 event:/data:，解析器要把 event 记到下一条 data 上。"""
        stream = ["event: done", 'data: {"status":"done"}']
        pending = ""
        for line in stream:
            event, data = mod.parse_sse_line(line)
            if event:
                pending = event
            assert data or pending
        assert pending == "done"


class TestPercentile:
    def test_single_sample(self) -> None:
        assert mod.percentile([100.0], 95) == 100.0

    def test_p50_is_median_for_odd_count(self) -> None:
        assert mod.percentile([5.0, 1.0, 3.0], 50) == 3.0

    def test_p95_never_returns_below_max_on_small_n(self) -> None:
        vals = [10.0, 20.0, 30.0, 40.0, 50.0]
        assert mod.percentile(vals, 95) == 50.0

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            mod.percentile([], 50)

    def test_p50_not_above_p95(self) -> None:
        vals = [12.0, 44.0, 8.0, 90.0, 30.0, 15.0, 22.0, 70.0, 5.0, 61.0]
        assert mod.percentile(vals, 50) <= mod.percentile(vals, 95)


class TestCorpus:
    def test_exact_length(self) -> None:
        assert len(mod.build_text(200)) == 200

    def test_shorter_than_sample_still_truncates(self) -> None:
        assert len(mod.build_text(10)) == 10

    def test_rejects_non_positive(self) -> None:
        with pytest.raises(ValueError):
            mod.build_text(0)
