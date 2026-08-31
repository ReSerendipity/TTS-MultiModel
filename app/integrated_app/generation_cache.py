# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""生成结果缓存 —— 相同请求短 TTL 命中，降低重复推理成本（评估整改 T9）。

背景（BACKEND_DESIGN_ASSESSMENT §缓存策略）：
    现有缓存仅覆盖 (a) Persona 嵌入（cache.AdaptiveLRUCache）、(b) 参考音频嵌入
    （prompt_cache.py）。**相同生成请求**（相同引擎 + 文本 + 音色 + 参数）会重复走完整
    GPU 推理，无结果级复用。本模块填补该空白：对幂等生成请求做短 TTL 结果缓存，
    在用户多次重复提交、前端重试（如网络抖动导致客户端重发）时直接命中，节省 GPU 算力。

设计约束（不破坏既有硬约束）：
    - 纯内存、单进程：与现有「单 Worker 串行」架构一致，不引入 Redis 依赖。
    - 不替代信号量串行控制：缓存命中也是「结果复用」，仍需经 _execute_generation 的统一入口，
      由调用方在取得信号量后、调用引擎前查询缓存；本模块仅提供「查 / 存」能力，不自行调度。
    - TTL + LRU 双重淘汰：防雪崩（同批缓存分散过期，故 TTL 写入时叠加随机 jitter），
      防内存膨胀（超出 max_entries 时淘汰最久未访问）。
    - 仅缓存「成功且确定性」的结果：相同输入必须产出相同输出才安全命中；
      调用方应使用 ``make_cache_key`` 对全部影响输出的参数做确定性序列化。

典型用法::

    cache = GenerationResultCache(ttl_seconds=300, max_entries=64)
    key = GenerationResultCache.make_cache_key("voxcpm2", text, ref_audio_hash=..., cfg_value=2.0)
    hit = cache.get(key)
    if hit is None:
        audio = engine.synthesize(...)
        cache.put(key, audio)
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger("tts_multimodel")


class GenerationResultCache:
    """生成结果的内存缓存（TTL + LRU 双重淘汰，线程安全）。

    Args:
        ttl_seconds: 缓存条目基础生存时间（秒）。实际过期时间 = ttl + 随机 jitter，
            以打散批量失效（防雪崩）。
        max_entries: 最大缓存条目数；超出时按 LRU 淘汰最久未访问条目。
        ttl_jitter_ratio: TTL 随机抖动比例（0~1），默认 0.1 表示 ±10% 抖动。
    """

    def __init__(
        self,
        ttl_seconds: float = 300.0,
        max_entries: int = 64,
        ttl_jitter_ratio: float = 0.1,
    ) -> None:
        self._ttl_seconds = max(0.0, float(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._ttl_jitter_ratio = min(1.0, max(0.0, float(ttl_jitter_ratio)))
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._hits: int = 0
        self._misses: int = 0
        self._lock = threading.Lock()

    @staticmethod
    def make_cache_key(engine: str, text: str, **params: Any) -> str:
        """构造确定性缓存键（相同输入必得相同键）。

        排序 ``params`` 以保证字典顺序不影响键；所有值经 ``json.dumps``
        （sort_keys=True, ensure_ascii=False）序列化后再做 SHA-256，
        避免超长键与编码歧义。

        Args:
            engine: 引擎名（如 "voxcpm2"）。
            text: 合成文本（已归一化）。
            **params: 影响输出的其余参数（音色哈希、cfg_value、seed、sample_rate 等）。
                **必须包含所有影响结果的维度**，否则可能产生错误命中。

        Returns:
            16 进制缓存键字符串。
        """
        normalized_text = (text or "").replace("\r", "").replace("\n", " ").strip()
        payload = {
            "engine": engine,
            "text": normalized_text,
            "params": {k: params[k] for k in sorted(params.keys())},
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Any | None:
        """查询缓存；命中且在 TTL 内返回缓存值，否则返回 None（并清理过期条目）。

        Args:
            key: 由 ``make_cache_key`` 生成的缓存键。

        Returns:
            缓存的结果对象（如音频 bytes / 路径）；未命中或已过期返回 None。
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            expire_at, value = entry
            if time.monotonic() >= expire_at:
                # 过期：删除并记为未命中
                self._store.pop(key, None)
                self._misses += 1
                return None
            self._store.move_to_end(key)
            self._hits += 1
            return value

    def put(self, key: str, value: Any) -> None:
        """写入缓存；超出 max_entries 时按 LRU 淘汰最久未访问条目。

        实际过期时间 = ``ttl_seconds * (1 ± jitter)``，jitter 随机化以打散
        批量缓存的集中失效，降低「缓存雪崩」风险。

        Args:
            key: 缓存键。
            value: 缓存值（建议为轻量引用，如音频文件路径，而非大块 bytes，
                以控制内存占用）。
        """
        with self._lock:
            jitter = self._ttl_seconds * self._ttl_jitter_ratio
            ttl = self._ttl_seconds + random.uniform(-jitter, jitter) if jitter > 0 else self._ttl_seconds
            ttl = max(1.0, ttl)
            self._store[key] = (time.monotonic() + ttl, value)
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                evicted_key, _ = self._store.popitem(last=False)
                logger.debug("[GenerationResultCache] LRU 淘汰: %s", evicted_key[:16])

    def clear(self) -> None:
        """清空所有缓存条目。"""
        with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0

    def get_stats(self) -> dict[str, Any]:
        """返回缓存统计（命中率、条目数、容量）。

        Returns:
            含 hits / misses / hit_rate / size / max_entries / ttl_seconds 的字典。
        """
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0.0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(hit_rate, 1),
                "size": len(self._store),
                "max_entries": self._max_entries,
                "ttl_seconds": round(self._ttl_seconds, 1),
            }
