"""历史记录数据库性能基准：量化 H-R6 优化前后差异。

测量三组对比（同一进程、同一数据集，控制变量）：
    1. 关键词搜索：FTS5 倒排索引 vs LIKE 全表扫描
    2. 深分页：keyset 游标 vs LIMIT/OFFSET
    3. 音频流水线：enhance_audio 是否触发额外全量拷贝

用法（Windows 内置解释器）：
    .\\WPy64-312101\\python\\python.exe scripts\\benchmark_history_db.py
可选参数：--records N（默认 20000），--repeats R（默认 20）。
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import tracemalloc

_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)
os.environ.setdefault("TTS_SKIP_MODEL_LOAD", "1")

import numpy as np  # noqa: E402

from integrated_app.audio_processing import enhance_audio  # noqa: E402
from integrated_app.history_db import HistoryDatabase  # noqa: E402


def _timeit(fn, repeats: int) -> float:
    """返回多次运行的中位耗时（毫秒）。"""
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    return samples[len(samples) // 2]


def seed(db: HistoryDatabase, n: int) -> None:
    records = []
    for i in range(n):
        records.append(
            {
                "filename": f"clip_{i}.wav",
                "filepath": f"/outputs/clip_{i}.wav",
                "created_at": "2026-01-01T00:00:00",
                "file_size_bytes": 1024,
                "duration_seconds": float(i % 12),
                "text_preview": (
                    f"你好世界语音合成编号{i}" if i % 4 == 0 else f"hello world speech synthesis sample {i}"
                ),
                "engine": "voxcpm2" if i % 2 == 0 else "indextts2",
                "created_timestamp": 1_700_000_000.0 + i,
            }
        )
    db.insert_batch(records)


def bench_search(db: HistoryDatabase, repeats: int) -> tuple[float, float, int, int]:
    kw = "speech synthesis"
    db._fts_enabled = True
    fts_res = {}
    fts_ms = _timeit(
        lambda: fts_res.setdefault("n", db.get_paginated_records(limit=50, offset=0, search_keyword=kw)["total"]),
        repeats,
    )
    db._fts_enabled = False
    like_res = {}
    like_ms = _timeit(
        lambda: like_res.setdefault("n", db.get_paginated_records(limit=50, offset=0, search_keyword=kw)["total"]),
        repeats,
    )
    db._fts_enabled = True
    return fts_ms, like_ms, fts_res["n"], like_res["n"]


def bench_pagination(db: HistoryDatabase, total: int, repeats: int) -> tuple[float, float]:
    deep_offset = max(0, total - 50)
    offset_ms = _timeit(lambda: db.get_paginated_records(limit=50, offset=deep_offset), repeats)
    # keyset：先取到接近末尾的游标，再测最后一页
    # 通过一次 offset 拿到第 deep_offset 条的游标（一次性成本，不计入循环）
    anchor = db.query_records_keyset(limit=deep_offset) if deep_offset > 0 else {"next_cursor": None}
    cur = anchor.get("next_cursor")
    if cur is None:
        keyset_ms = _timeit(lambda: db.query_records_keyset(limit=50), repeats)
    else:
        keyset_ms = _timeit(
            lambda: db.query_records_keyset(limit=50, cursor_timestamp=cur["timestamp"], cursor_id=cur["id"]), repeats
        )
    return offset_ms, keyset_ms


def bench_audio_memory() -> tuple[float, float]:
    rng = np.random.default_rng(0)
    # 3 分钟 @ 24kHz 单声道
    audio = (rng.standard_normal(24000 * 180).astype(np.float32)) * 0.3

    def run_noop():
        return enhance_audio(audio, 24000, normalize=False)

    def run_normalize():
        return enhance_audio(audio, 24000, normalize=True)

    tracemalloc.start()
    run_noop()
    _, peak_noop = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    tracemalloc.start()
    run_normalize()
    _, peak_norm = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak_noop / 1024 / 1024, peak_norm / 1024 / 1024


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", type=int, default=20000)
    ap.add_argument("--repeats", type=int, default=20)
    args = ap.parse_args()

    d = tempfile.mkdtemp()
    db = HistoryDatabase(os.path.join(d, "bench.db"))
    print(f"seeding {args.records} records ...")
    seed(db, args.records)
    total = db.get_total_count()
    print(f"total records = {total}, fts_enabled = {db._fts_enabled}\n")

    fts_ms, like_ms, fts_n, like_n = bench_search(db, args.repeats)
    speedup = (like_ms / fts_ms) if fts_ms > 0 else float("inf")
    print(f"=== 1. 关键词搜索 (median over {args.repeats} runs) ===")
    print(f"  FTS5  : {fts_ms:8.3f} ms  (matches={fts_n})")
    print(f"  LIKE  : {like_ms:8.3f} ms  (matches={like_n})")
    print(f"  parity(matches equal) = {fts_n == like_n}")
    print(f"  speedup = {speedup:.1f}x\n")

    off_ms, ks_ms = bench_pagination(db, total, args.repeats)
    pspeedup = (off_ms / ks_ms) if ks_ms > 0 else float("inf")
    print(f"=== 2. 深分页 (offset={max(0, total - 50)}, median over {args.repeats} runs) ===")
    print(f"  OFFSET: {off_ms:8.3f} ms")
    print(f"  keyset: {ks_ms:8.3f} ms")
    print(f"  speedup = {pspeedup:.1f}x\n")

    peak_noop, peak_norm = bench_audio_memory()
    print("=== 3. 音频 enhance_audio 峰值内存 (3min@24kHz) ===")
    print(f"  no-op path   peak = {peak_noop:6.2f} MB")
    print(f"  normalize    peak = {peak_norm:6.2f} MB")
    print("  (优化前 no-op 也会额外 copy 一份 ~%.2f MB)" % (24000 * 180 * 4 / 1024 / 1024))


if __name__ == "__main__":
    main()
