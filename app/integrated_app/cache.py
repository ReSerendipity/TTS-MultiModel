"""LRU 缓存模块，支持 GPU 感知的自适应容量管理。

提供两种缓存实现：
1. LRUCache：基于 OrderedDict 的定容量 LRU（Least Recently Used，最近最少使用）缓存，
   超出容量时淘汰最久未访问的条目。
2. AdaptiveLRUCache：继承自 LRUCache 的 GPU 感知自适应容量缓存，
   根据当前 GPU 显存使用率动态调整缓存最大容量。

Persona 嵌入缓存关联：
    model_manager._persona_embedding_cache = AdaptiveLRUCache(15) 即使用本模块
    的 AdaptiveLRUCache 来缓存 Persona 预计算嵌入向量，避免重复计算并在显存
    紧张时主动缩减以释放内存。

GPU 阈值映射规则（与 AGENTS.md §6 内存熔断 90% 阈值对齐）：
    - GPU 显存使用率 > 90%  → 最大缓存 5 项
    - GPU 显存使用率 > 75%  → 最大缓存 10 项
    - GPU 显存使用率 > 50%  → 最大缓存 15 项
    - 其他情况（≤50%）      → 最大缓存 20 项

依赖关系：
    - 被 model_manager.py 使用：缓存 Persona 嵌入向量。
    - 可被其他需要 LRU 缓存的模块复用。
"""

import logging
import threading
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger("tts_multimodel")


class LRUCache:
    """定容量最近最少使用（LRU）缓存。

    使用 OrderedDict 跟踪访问顺序。当缓存超出容量时，
    优先淘汰最久未访问的条目。线程安全（使用 threading.Lock）。

    Attributes:
        _cache (OrderedDict[str, Any]): 按访问顺序存储的缓存条目字典。
        _maxsize (int): 缓存最大容量（条目数）。
        _hits (int): 缓存命中计数（统计用）。
        _misses (int): 缓存未命中计数（统计用）。
        _lock (threading.Lock): 线程安全锁，保证并发访问安全。
    """

    def __init__(self, maxsize: int = 50) -> None:
        """初始化 LRU 缓存。

        Args:
            maxsize: 缓存最大条目数，默认 50。
        """
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._maxsize: int = maxsize
        self._hits: int = 0
        self._misses: int = 0
        self._lock: threading.Lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        """按键获取缓存条目。

        将访问的条目移动到末尾（标记为最近使用）。

        Args:
            key: 要查找的缓存键。

        Returns:
            找到时返回缓存的值，未找到返回 None。
        """
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._hits += 1
                return self._cache[key]
            self._misses += 1
            return None

    def put(self, key: str, value: Any) -> None:
        """插入或更新缓存条目。

        如果键已存在，将其移动到末尾（最近使用位置）。
        如果缓存超出 maxsize，淘汰最久未使用的条目直到容量符合要求。

        Args:
            key: 缓存键。
            value: 要缓存的值。
        """
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def __contains__(self, key: str) -> bool:
        """判断缓存中是否存在指定键（支持 ``in`` 运算符）。

        Args:
            key: 待检查的缓存键。

        Returns:
            bool: 存在返回 True，否则返回 False。
        """
        with self._lock:
            return key in self._cache

    def __delitem__(self, key: str) -> None:
        """从缓存中删除指定键对应的条目（支持 ``del`` 语句）。

        若键不存在则静默忽略，不抛出异常。

        Args:
            key: 待删除的缓存键。
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]

    def get_stats(self) -> dict[str, Any]:
        """返回缓存性能统计信息。

        Returns:
            dict[str, Any]: 统计字典，包含以下字段：
                - hits: 命中次数
                - misses: 未命中次数
                - hit_rate: 命中率（百分比，保留 1 位小数）
                - size: 当前条目数
                - maxsize: 最大容量
        """
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0.0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate, 1),
                "size": len(self._cache),
                "maxsize": self._maxsize,
            }

    def reset_stats(self) -> None:
        """重置命中和未命中计数器为零。"""
        with self._lock:
            self._hits = 0
            self._misses = 0


class AdaptiveLRUCache(LRUCache):
    """基于 GPU 显存使用率的自适应容量 LRU 缓存。

    自动根据 GPU 显存使用情况反比例调整缓存大小：GPU 使用率高时
    缩减缓存以释放系统内存，使用率低时允许缓存扩容。

    双重淘汰策略：
        1. 按条目数：根据 GPU 使用率动态调整的 _maxsize 限制。
        2. 按总字节数：_MEMORY_LIMIT_MB（默认 512MB）内存上限兜底，
           防止少数大条目（如大嵌入向量）占用过多内存导致 OOM。

    容量调整触发条件（满足任一即触发）：
        - 缓存已满（len >= maxsize）
        - 距离上次调整超过 _adapt_interval 秒（默认 30s）
        - 累计 put 次数达到 _adapt_every_n（默认 10 次）

    Attributes:
        _CAPACITY_MAP (list[tuple[int, int]]): (GPU阈值%, 缓存容量) 的有序映射表。
        _MEMORY_LIMIT_MB (int): 缓存总内存估算上限（MB），默认 512MB。
        _adapt_lock (threading.Lock): 容量调整与内存统计的线程锁。
        _adapt_interval (float): 基于时间的容量调整最小间隔（秒）。
        _last_adapt_time (float): 上次执行容量调整的时间戳（time.monotonic）。
        _put_count (int): 累计 put 次数，用于触发按 N 次 put 调整容量。
        _adapt_every_n (int): 每累计 N 次 put 触发一次容量调整。
        _total_memory_estimate (int): 当前缓存所有条目的总内存估算（字节）。
        _eviction_count (int): 因内存上限触发的淘汰次数统计。
    """

    # Why 90% 以上直接压到 5 项：
    #   对应 AGENTS.md §6 硬约束「显存超过 90% 立即触发熔断」。此时缓存再多也没意义，
    #   反而占内存让 KV cache 空间不足，推理容易 OOM。所以在接近熔断阈值时
    #   主动将缓存条目压到最小可用集合，优先保证推理显存。
    _CAPACITY_MAP: list[tuple[int, int]] = [
        (90, 5),
        (75, 10),
        (50, 15),
        (0, 20),
    ]

    _MEMORY_LIMIT_MB: int = 512
    """缓存总内存估算上限（MB），超过时从最旧条目开始淘汰。"""

    def __init__(self, default_maxsize: int = 15, adapt_interval: float = 30.0) -> None:
        """初始化自适应 LRU 缓存。

        Args:
            default_maxsize: 默认初始最大容量，默认 15。
            adapt_interval: 容量自适应调整的最小时间间隔（秒），默认 30.0。
        """
        super().__init__(maxsize=default_maxsize)
        self._adapt_lock: threading.Lock = threading.Lock()
        self._adapt_interval: float = adapt_interval
        self._last_adapt_time: float = 0.0
        self._put_count: int = 0
        self._adapt_every_n: int = 10
        self._total_memory_estimate: int = 0
        self._eviction_count: int = 0

    @staticmethod
    def _estimate_item_size(value: Any) -> int:
        """估算缓存值的内存占用（字节）。

        支持 numpy 数组（通过 nbytes 属性）和元组/列表的递归估算。
        任何估算异常都兜底返回 1024 字节，不中断 put 操作。

        Args:
            value: 要估算的缓存值。

        Returns:
            int: 估算的内存大小（字节）。
        """
        try:
            if isinstance(value, tuple) and len(value) > 0:
                first = value[0]
                try:
                    if hasattr(first, "nbytes"):
                        total = first.nbytes
                        for item in value[1:]:
                            try:
                                if hasattr(item, "nbytes"):
                                    total += item.nbytes
                                else:
                                    total += getattr(item, "__sizeof__", lambda: 1024)()
                            except AttributeError:
                                total += 1024
                        return total
                except AttributeError:
                    pass
            try:
                if hasattr(value, "__sizeof__"):
                    return value.__sizeof__()
            except AttributeError:
                pass
        except Exception:  # nosec B110 - 尽力而为/兜底异常处理（已有 noqa/日志审计）
            pass
        return 1024

    @staticmethod
    def _get_gpu_memory_percent() -> float:
        """通过 GPUBackendManager 查询当前 GPU 显存分配百分比。

        Returns:
            float: 显存使用百分比（0.0 到 100.0）；GPU 不可用时返回 0.0。
        """
        try:
            from .gpu_backend import GPUBackendManager

            if not GPUBackendManager.is_available():
                return 0.0

            mem_info = GPUBackendManager.get_memory_info()
            total = mem_info[0]
            allocated = mem_info[1]

            if total == 0:
                return 0.0
            return allocated / total * 100
        except Exception:
            return 0.0

    def _calculate_target_capacity(self) -> int:
        """根据当前 GPU 显存使用率确定目标缓存容量。

        Returns:
            int: 目标缓存容量（条目数）。
        """
        gpu_pct = self._get_gpu_memory_percent()
        for threshold, capacity in self._CAPACITY_MAP:
            if gpu_pct > threshold:
                return capacity
        return 20

    def adapt_capacity(self) -> int:
        """根据 GPU 显存调整缓存容量并淘汰多余条目。

        根据当前 GPU 使用率重新计算目标容量，同步调整 _maxsize 并淘汰
        超出新容量的 LRU 条目；容量变化时输出 info 日志。
        所有对 _cache 的修改都在父类 _lock 保护下执行，保证线程安全。

        Returns:
            int: 调整后的新缓存容量。
        """
        target = self._calculate_target_capacity()
        with self._lock:
            old_max = self._maxsize
            self._maxsize = target
            while len(self._cache) > self._maxsize:
                evicted_key, evicted_value = self._cache.popitem(last=False)
                with self._adapt_lock:
                    evicted_size = self._estimate_item_size(evicted_value)
                    self._total_memory_estimate -= evicted_size
                    self._eviction_count += 1
            if old_max != target:
                try:
                    gpu_pct = self._get_gpu_memory_percent()
                    logger.info(f"[AdaptiveCache] 容量已调整: {old_max} -> {target} (GPU 使用率: {gpu_pct:.1f}%)")
                except Exception:
                    logger.info(
                        "[AdaptiveCache] 容量已调整: %d -> %d",
                        old_max,
                        target,
                    )
        return target

    def put(self, key: str, value: Any) -> None:
        """插入或更新缓存条目，执行五步流程：
            1. estimate_item_size：估算新条目内存占用
            2. adapt（若触发条件满足）：按 GPU 显存调整容量
            3. 超过 memory_limit 淘汰：按总字节上限从最旧开始淘汰
            4. 超过容量淘汰：按条目数上限从最旧开始淘汰（在父类 put 中执行）
            5. put_count 触发 adapt：累计 N 次 put 或时间间隔触发容量自检

        Why 既要按条目数又要按字节双重淘汰：
            Persona 嵌入每个大小差异大（例如 10MB/个 vs 1MB/个），单按条目数
            可能 15 条上限下实际总内存已超过 512MB 而触发 OOM。因此叠加
            _MEMORY_LIMIT_MB 字节维度的兜底淘汰，在条目差异大时更稳健。

        Args:
            key: 缓存键。
            value: 要缓存的值。
        """
        size = self._estimate_item_size(value)

        # 先获取父类锁（父类 put 也会获取，我们提前获取以原子地处理旧值内存）
        with self._lock:
            old_size = 0
            if key in self._cache:
                old_value = self._cache[key]
                old_size = self._estimate_item_size(old_value)

            # 更新内存估算（_adapt_lock 仅保护这两个统计字段）
            with self._adapt_lock:
                self._total_memory_estimate = self._total_memory_estimate - old_size + size

            # 调用父类 put 实现 LRU 更新和容量淘汰
            # 注意：父类 put 内部会再次获取 _lock，但 Lock 不支持重入！
            # 因此我们在已经持有 _lock 的情况下，手动执行父类逻辑

            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            while len(self._cache) > self._maxsize:
                evicted_key, evicted_value = self._cache.popitem(last=False)
                evicted_size = self._estimate_item_size(evicted_value)
                with self._adapt_lock:
                    self._total_memory_estimate -= evicted_size
                    self._eviction_count += 1

            # 字节数兜底淘汰：超过内存限制时继续从最旧开始淘汰
            memory_limit = self._MEMORY_LIMIT_MB * 1024 * 1024
            while len(self._cache) > 0:
                with self._adapt_lock:
                    if self._total_memory_estimate <= memory_limit:
                        break
                evicted_key, evicted_value = self._cache.popitem(last=False)
                evicted_size = self._estimate_item_size(evicted_value)
                with self._adapt_lock:
                    self._total_memory_estimate -= evicted_size
                    self._eviction_count += 1

        # 锁外更新统计和触发自适应（这些不需要持锁）
        self._put_count += 1
        now = time.monotonic()
        if (
            len(self._cache) >= self._maxsize
            or now - self._last_adapt_time >= self._adapt_interval
            or self._put_count >= self._adapt_every_n
        ):
            self.adapt_capacity()
            self._last_adapt_time = now
            self._put_count = 0

    def __delitem__(self, key: str) -> None:
        """从缓存中删除指定键，同步更新内存估算。

        Args:
            key: 待删除的缓存键。
        """
        with self._lock:
            if key in self._cache:
                evicted_value = self._cache[key]
                evicted_size = self._estimate_item_size(evicted_value)
                del self._cache[key]
                with self._adapt_lock:
                    self._total_memory_estimate -= evicted_size

    def clear(self) -> None:
        """清空所有缓存条目并重置相关统计。

        除了调用 _cache.clear() 外，还会重置：
        - _total_memory_estimate：总内存估算清零
        - _eviction_count：内存淘汰计数清零
        随后通过 reset_stats() 清零 hits/misses 计数。
        所有操作在 _lock 保护下执行保证原子性。
        """
        with self._lock:
            self._cache.clear()
            with self._adapt_lock:
                self._total_memory_estimate = 0
                self._eviction_count = 0
        self.reset_stats()

    def get_stats(self) -> dict[str, Any]:
        """返回缓存性能统计信息（含内存跟踪）。

        Returns:
            dict[str, Any]: 在 LRUCache 统计基础上增加：
                - memory_estimate_mb: 估算总内存占用（MB，保留 2 位小数）
                - eviction_count: 因内存上限触发的淘汰次数
                - avg_item_size_kb: 平均每条目大小（KB，保留 2 位小数）
        """
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0.0
            cache_size = len(self._cache)
            with self._adapt_lock:
                memory_mb = self._total_memory_estimate / (1024 * 1024)
                eviction_count = self._eviction_count
            avg_kb = (memory_mb * 1024 / cache_size) if cache_size > 0 else 0.0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate, 1),
                "size": cache_size,
                "maxsize": self._maxsize,
                "memory_estimate_mb": round(memory_mb, 2),
                "eviction_count": eviction_count,
                "avg_item_size_kb": round(avg_kb, 2),
            }
