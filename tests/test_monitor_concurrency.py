"""monitor.HealthMonitor 的并发正确性回归。

为什么单独开这个文件：HealthMonitor 是进程级单例（monitor.py 末尾的 `_health_monitor`），
路由层通过 `loop.run_in_executor` 把同步生成流程丢到线程池里跑，所以它的计数器是
**真多线程并发写**的。改造前整个类零锁，`self._total_generations += 1` 这种
LOAD-ADD-STORE 三步操作在 GIL 下并不原子（GIL 只保证单条字节码不撕裂，不保证复合操作），
结果是 /metrics 与 /health 少报、`success_rate_pct` 甚至能算出区间外的值。

本文件的用例都断言"精确值"或"硬不变量"，不是"没炸就行"：
加锁后无论线程怎么交错都必须成立；一旦有人把锁摘掉，丢更新会立刻让这些断言变红。

与 tests/benchmarks/test_load_stress.py 的分工：那个文件是 stress 标记的 HTTP 吞吐压测
（nightly 跑，看的是响应时间），这里测的是进程内共享状态的**正确性**（每次 PR CI 都要跑）。
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Iterator

import pytest

from integrated_app.monitor import HealthMonitor

#: 线程数 × 每线程操作数。取够大的量是为了让丢更新在"无锁"版本上稳定复现：
#: 单次 += 的竞态窗口只有纳秒级，样本太少时即便有 bug 也撞不上。
THREADS = 8
OPS_PER_THREAD = 2000


@pytest.fixture
def monitor() -> Iterator[HealthMonitor]:
    """独立实例（不碰进程级单例），避免污染其它用例读取的全局健康数据。"""
    yield HealthMonitor()


@pytest.fixture
def tight_switch_interval() -> Iterator[None]:
    """把 GIL 切换间隔压到最小，放大线程交错。

    这只影响"不加锁时多快暴露问题"，不影响加了锁之后的正确性断言 —— 有锁时
    无论怎么交错结果都一样。用例因此在慢速 CI 机器上也不会反向变红。
    """
    original = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(original)


class _LockSpy:
    """`monitor._lock` 的替身：记录进入次数与当前嵌套深度。

    只实现 `with` 协议即可 —— 生产代码统一用 `with self._lock:`，没有裸 acquire。
    reentrant=False 时故意用非重入锁：若某条路径真的重入了，第二次 acquire 会阻塞/报错，
    从而把"这里需要 RLock"变成可观察的事实而不是注释里的口头承诺。
    """

    def __init__(self, reentrant: bool = True) -> None:
        self._inner: threading.RLock | threading.Lock = threading.RLock() if reentrant else threading.Lock()
        self.enter_count = 0
        self.depth = 0
        self.max_depth = 0

    def __enter__(self) -> _LockSpy:
        self._inner.acquire()
        self.enter_count += 1
        self.depth += 1
        self.max_depth = max(self.max_depth, self.depth)
        return self

    def __exit__(self, *_exc: object) -> None:
        self.depth -= 1
        self._inner.release()


def _run_concurrently(action: Callable[[int], None], threads: int = THREADS) -> None:
    """让 threads 个线程在同一道栅栏后同时执行 action(thread_index)。"""
    barrier = threading.Barrier(threads)
    errors: list[BaseException] = []

    def _worker(index: int) -> None:
        try:
            barrier.wait()
            action(index)
        except BaseException as exc:  # noqa: BLE001 - 线程里的异常必须带回主线程
            errors.append(exc)

    pool = [threading.Thread(target=_worker, args=(i,), name=f"mon-{i}") for i in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join(timeout=60)
        assert not t.is_alive(), "线程未结束：疑似死锁"
    assert not errors, f"工作线程抛出：{errors!r}"


class TestCounterAtomicity:
    """每个 `+= 1` 都不许丢。"""

    def test_record_generation_keeps_every_increment(self, monitor: HealthMonitor, tight_switch_interval: None) -> None:
        def _action(_i: int) -> None:
            for _ in range(OPS_PER_THREAD):
                monitor.record_generation(True)

        _run_concurrently(_action)

        metrics = monitor.get_metrics()
        expected = THREADS * OPS_PER_THREAD
        assert metrics["total_generations"] == expected
        assert metrics["total_errors"] == 0
        assert metrics["success_rate_pct"] == 100.0

    def test_failed_and_success_mix_does_not_lose_either_count(
        self, monitor: HealthMonitor, tight_switch_interval: None
    ) -> None:
        """成功/失败两类写入并发进行：两个计数器都必须精确。"""

        def _action(index: int) -> None:
            success = index % 2 == 0
            for _ in range(OPS_PER_THREAD):
                monitor.record_generation(success)

        _run_concurrently(_action)

        metrics = monitor.get_metrics()
        expected = THREADS * OPS_PER_THREAD
        half = expected // 2
        assert metrics["total_generations"] == expected
        assert metrics["total_errors"] == half
        # 无锁时这里会算出区间外的成功率：分子分母来自两次不同的读，
        # 读到 errors 已涨、generations 还没涨 → 负值；反向 → >100。
        assert 0.0 <= metrics["success_rate_pct"] <= 100.0
        assert metrics["success_rate_pct"] == pytest.approx(50.0, abs=0.1)

    def test_oom_counters_are_exact(self, monitor: HealthMonitor, tight_switch_interval: None) -> None:
        def _action(_index: int) -> None:
            for _ in range(OPS_PER_THREAD):
                monitor.record_oom_retry()
                monitor.record_oom_auto_recovery()

        _run_concurrently(_action)

        metrics = monitor.get_metrics()
        expected = THREADS * OPS_PER_THREAD
        assert metrics["total_oom_retries"] == expected
        assert metrics["total_oom_auto_recoveries"] == expected

    def test_error_type_counts_are_exact_per_type(self, monitor: HealthMonitor, tight_switch_interval: None) -> None:
        types = list(HealthMonitor.ERROR_TYPES)

        def _action(index: int) -> None:
            for _ in range(OPS_PER_THREAD):
                monitor.record_generation_error_type(types[index % len(types)])

        _run_concurrently(_action)

        counts = monitor.error_type_counts()
        expected = THREADS * OPS_PER_THREAD
        assert sum(counts.values()) == expected, "丢更新会直接体现为总数偏小"
        assert all(v > 0 for v in counts.values())


class TestLatencyHistogramAtomicity:
    """直方图三个量（桶 / 累计秒 / 观测数）必须来自同一批写入。"""

    def test_observation_count_and_sum_exact(self, monitor: HealthMonitor, tight_switch_interval: None) -> None:
        def _action(_index: int) -> None:
            for _ in range(OPS_PER_THREAD):
                monitor.record_latency(0.25)

        _run_concurrently(_action)

        buckets, total_seconds, count = monitor.latency_observations()
        expected = THREADS * OPS_PER_THREAD
        assert count == expected
        assert total_seconds == pytest.approx(expected * 0.25, rel=1e-3)
        # record_latency 把样本计入所有 >= 它的上界（累计直方图），所以每个桶都等于总数
        assert all(v == expected for v in buckets.values()), buckets

    def test_buckets_stay_cumulative_and_within_bounds(
        self, monitor: HealthMonitor, tight_switch_interval: None
    ) -> None:
        """并发写入下累计直方图的形状不变量：单调不减、且都不超过观测总数。"""

        def _action(index: int) -> None:
            # 每个线程用不同的耗时，散落到不同桶里
            for i in range(OPS_PER_THREAD):
                monitor.record_latency(((index * 7 + i) % 700) / 10.0)

        _run_concurrently(_action)

        buckets, _sum, count = monitor.latency_observations()
        expected = THREADS * OPS_PER_THREAD
        assert count == expected
        ordered = [buckets[f"{b:g}"] for b in HealthMonitor.LATENCY_BUCKET_SECONDS]
        assert ordered == sorted(ordered), "上界越大的桶计数不可能更小"
        assert all(0 <= v <= count for v in ordered)

    def test_quantile_reads_a_consistent_snapshot(self, monitor: HealthMonitor, tight_switch_interval: None) -> None:
        """分位数估算不能拿"新的 count + 旧的桶"，否则插值会算出负数或越界值。"""

        stop = threading.Event()

        def _reader() -> None:
            bounds = HealthMonitor.LATENCY_BUCKET_SECONDS
            while not stop.is_set():
                p95 = monitor.latency_quantile(0.95)
                assert 0.0 <= p95 <= bounds[-1] * 2, p95

        writer_done = threading.Event()

        def _writer() -> None:
            def _action(_i: int) -> None:
                for _ in range(OPS_PER_THREAD):
                    monitor.record_latency(1.0)

            _run_concurrently(_action)
            writer_done.set()

        readers = [threading.Thread(target=_reader, name=f"q-{i}", daemon=True) for i in range(4)]
        writer = threading.Thread(target=_writer, name="w")
        for r in readers:
            r.start()
        writer.start()
        writer.join(timeout=120)
        assert writer_done.is_set(), "写入线程未完成"
        stop.set()
        for r in readers:
            r.join(timeout=10)

        errors = [t for t in readers if t.is_alive()]
        assert not errors, "读者线程未退出"
        assert monitor.latency_observations()[2] == THREADS * OPS_PER_THREAD


class TestVramBaselineStateMachine:
    """显存采样窗口 + 基线状态机：多字段跨语句推进，无锁会撕裂。"""

    def test_concurrent_samples_and_readers_agree(self, monitor: HealthMonitor, tight_switch_interval: None) -> None:
        stop = threading.Event()
        seen: list[dict[str, object]] = []
        seen_lock = threading.Lock()

        def _reader() -> None:
            while not stop.is_set():
                trend = monitor.get_vram_trend()
                if "status" not in trend:
                    # 同一份快照内部必须自洽：min <= avg <= max、current 落在窗口内、
                    # 且 trend 的自述与 sample_count 用的是同一批样本
                    assert trend["min_mb"] <= trend["avg_mb"] <= trend["max_mb"], trend
                    assert 0 < trend["sample_count"] <= monitor._max_samples, trend
                    with seen_lock:
                        seen.append(trend)

        def _writer(_index: int) -> None:
            for i in range(OPS_PER_THREAD):
                monitor.record_vram_usage(1000.0 + (i % 50))
                monitor.check_memory_leak()

        readers = [threading.Thread(target=_reader, name=f"r-{i}", daemon=True) for i in range(3)]
        for r in readers:
            r.start()
        try:
            _run_concurrently(_writer)
        finally:
            stop.set()
            for r in readers:
                r.join(timeout=10)

        assert seen, "读者一次都没采到样本，说明本用例没真正并发读"
        assert all(not r.is_alive() for r in readers)

    def test_baseline_still_establishes_under_contention(self, monitor: HealthMonitor) -> None:
        """并发喂同样的稳定样本，基线必须建立（有锁后不因计数丢失而迟迟建不起来）。"""
        required = monitor._baseline_required_stable

        def _action(_index: int) -> None:
            for _ in range(required + 3):
                monitor.record_vram_usage(1234.0)

        _run_concurrently(_action, threads=4)
        assert monitor._baseline_mb == pytest.approx(1234.0), "稳定样本下基线应恰为该值"


class TestEverySharedStateAccessHoldsTheLock:
    """结构性断言：凡读写共享状态，必须在锁内。

    为什么需要这一类而不是只靠上面的精确计数 —— 实测结论（写在这里防止后人误读用例强度）：
    CPython 3.12 的求值循环只在 RESUME / 向后跳转 / 函数调用处检查 eval breaker，
    `LOAD_ATTR → BINARY_OP → STORE_ATTR` 这一整段是不可抢占的，所以单个 `self.x += 1`
    在带 GIL 的构建上**观察不到丢更新**。把 monitor.py 的锁整体摘掉，
    上面 TestCounterAtomicity / TestLatencyHistogramAtomicity 依然 11 项全绿。
    也就是说那些用例只对 free-threaded（3.13t）等非 GIL 构建有鉴别力，在 CI 主矩阵里
    是"防回归的语义契约"，不是"能抓到摘锁的检测器"。

    本类才有鉴别力：把实例的 `_lock` 换成计数器，逐个调用公开 API，断言
    (a) 该 API 确实进入过锁、(b) 退出时锁深度归零（没漏 release）。
    摘掉任何一处 `with self._lock` → 对应参数化用例立刻变红。
    """

    MUTATORS: tuple[tuple[str, tuple[object, ...]], ...] = (
        ("record_latency", (0.5,)),
        ("record_generation", ()),
        ("record_generation_error_type", ("oom",)),
        ("record_vram_usage", (1200.0,)),
        ("reset_vram_baseline", ()),
        ("record_oom_retry", ()),
        ("record_oom_auto_recovery", ()),
        ("set_model_status", ("ready",)),
    )

    READERS: tuple[tuple[str, tuple[object, ...]], ...] = (
        ("latency_observations", ()),
        ("latency_quantile", (0.95,)),
        ("error_type_counts", ()),
        ("check_memory_leak", ()),
        ("get_vram_trend", ()),
        ("get_metrics", ()),
    )

    @pytest.mark.parametrize(("method", "args"), MUTATORS + READERS)
    def test_api_enters_and_releases_the_lock(self, monitor: HealthMonitor, method: str, args: tuple) -> None:
        spy = _LockSpy()
        monitor._lock = spy  # type: ignore[assignment] - 用替身观察锁行为，不是真锁
        getattr(monitor, method)(*args)
        assert spy.enter_count > 0, f"{method} 未在任何锁保护下访问共享状态"
        assert spy.depth == 0, f"{method} 泄漏了锁（进入 {spy.enter_count} 次，未配平释放）"

    def test_get_metrics_is_reentrant_safe(self, monitor: HealthMonitor) -> None:
        """get_metrics 内部会再调 latency_quantile / check_memory_leak，
        锁必须是可重入的（RLock），否则自死锁。"""
        spy = _LockSpy(reentrant=True)
        monitor._lock = spy  # type: ignore[assignment] - 用替身观察锁行为，不是真锁
        metrics = monitor.get_metrics()
        assert "total_generations" in metrics
        assert spy.max_depth >= 1
        assert spy.depth == 0

    def test_lock_is_reentrant_type(self, monitor: HealthMonitor) -> None:
        """口径锁死：必须用 RLock。换成 threading.Lock 会在重入路径上直接死锁，
        而这条比上面的行为断言更早暴露问题。"""
        real = HealthMonitor()._lock
        assert type(real).__name__ in {"RLock", "_thread.RLock", "_thread.lock"}, type(real).__name__
        # RLock 支持 _is_owned（Lock 不支持），据此区分两者
        assert hasattr(real, "_is_owned"), "不是可重入锁：重入路径会自死锁"


class TestSingleThreadSemantics:
    """加锁不该改变单线程语义。"""

    def test_single_thread_semantics_unchanged(self, monitor: HealthMonitor) -> None:
        monitor.record_generation(True)
        monitor.record_generation(False)
        monitor.record_latency(2.0)
        monitor.record_generation_error_type("timeout")
        monitor.record_generation_error_type("nonexistent-type")
        for value in [100.0] * 12:
            monitor.record_vram_usage(value)

        metrics = monitor.get_metrics()
        assert metrics["total_generations"] == 2
        assert metrics["total_errors"] == 1
        assert metrics["success_rate_pct"] == 50.0
        counts = monitor.error_type_counts()
        # 未知类型归入 other，不能凭空多出一个新键
        assert counts["timeout"] == 1
        assert counts["other"] == 1
        assert set(counts) == set(HealthMonitor.ERROR_TYPES)

    def test_negative_latency_clamped_to_zero(self, monitor: HealthMonitor) -> None:
        monitor.record_latency(-5.0)
        _buckets, total_seconds, count = monitor.latency_observations()
        assert count == 1
        assert total_seconds == 0.0
