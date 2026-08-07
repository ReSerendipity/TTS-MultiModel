"""生成时间估算器模块。

本模块实现 :class:`GenerationTimeEstimator`，通过持久化线性回归训练样本
（字符数 → 实际耗时）对新的 TTS 生成任务预测耗时。

数据持久化
----------
训练样本以 JSON 格式存储在 ``data/generation_times.json`` 文件中，
采用滑动窗口策略，最多保留 ``max_entries`` 条记录（默认 200 条），
防止数据文件无限增长。

调用链路
--------
- ``model_manager`` 在每段生成完成后调用 :meth:`record_sample` 记录样本
- 估算结果通过 ``/api/sse/time_estimate`` 事件以 ``estimated_seconds``
  字段推送给前端展示进度条预期
"""

import contextlib
import json
import logging
import os
import threading
import time
from collections import deque
from typing import Any

logger = logging.getLogger("tts_multimodel")

CHARS_PER_SECOND_DEFAULT: float = 15.0
_SAVE_THROTTLE_SECONDS: float = 30.0
_SAVE_THROTTLE_SAMPLES: int = 10


class GenerationTimeEstimator:
    """生成时间估算器：基于历史样本的增量最小二乘线性回归。

    模型公式：``duration = slope * num_chars + intercept``
    使用 Welford 风格在线算法维护统计量，新增/淘汰样本均为 O(1)。

    Attributes:
        data_file (str): 样本持久化 JSON 文件路径。
        max_entries (int): 滑动窗口最大样本数，超出后丢弃最早记录。
        _samples (deque[tuple[int, float]]): 历史样本双端队列，支持 O(1) 头尾操作。
        _lock (threading.RLock): 可重入线程锁，保护样本列表与系数的并发访问。
        _slope (float): 线性回归斜率（秒/字符）。
        _intercept (float): 线性回归截距（秒），代表固定开销。
        _dirty (bool): 自上次持久化后是否有新样本未写入磁盘。
        _count (int): 累计记录样本总数（含被滑动窗口丢弃的）。
        _n (int): 当前窗口内有效样本数。
        _sum_x (float): Σx 统计量。
        _sum_y (float): Σy 统计量。
        _sum_xy (float): Σxy 统计量。
        _sum_xx (float): Σx² 统计量。
        _last_save_time (float): 上次持久化时间戳。
        _samples_since_last_save (int): 自上次持久化后新增样本数。
    """

    def __init__(self, data_file: str, max_entries: int = 200) -> None:
        """初始化生成时间估算器。

        Args:
            data_file: 样本持久化 JSON 文件路径。
            max_entries: 滑动窗口最大样本数，超出后丢弃最早记录。
        """
        self.data_file: str = data_file
        self.max_entries: int = max_entries
        self._samples: deque[tuple[int, float]] = deque()
        self._lock: threading.RLock = threading.RLock()
        self._slope: float = 1.0 / CHARS_PER_SECOND_DEFAULT
        self._intercept: float = 1.0
        self._dirty: bool = False
        self._count: int = 0
        self._n: int = 0
        self._sum_x: float = 0.0
        self._sum_y: float = 0.0
        self._sum_xy: float = 0.0
        self._sum_xx: float = 0.0
        self._last_save_time: float = 0.0
        self._samples_since_last_save: int = 0
        self._load_data()

    def _load_data(self) -> list[tuple[int, float]]:
        """从 JSON 文件加载历史样本。

        支持读取新旧两种持久化格式：
        - 新格式：``{"samples": [[num_chars, elapsed], ...], "count": N}``
        - 旧格式：``{"samples": [{"char_count": N, "duration": S}, ...]}``

        Returns:
            list[tuple[int, float]]: ``[(num_chars, elapsed_seconds), ...]``
            格式的样本列表；文件不存在或损坏时返回空列表。
        """
        if not os.path.exists(self.data_file):
            self._samples = deque(maxlen=self.max_entries)
            self._reset_statistics()
            return list(self._samples)

        try:
            with open(self.data_file, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            logger.warning(
                "生成时间样本文件 %s 损坏（JSONDecodeError: %s），将重置为空白并备份原文件为 .bak",
                self.data_file,
                e,
            )
            bak_path = self.data_file + ".bak"
            with contextlib.suppress(OSError):
                if os.path.exists(bak_path):
                    os.remove(bak_path)
                os.rename(self.data_file, bak_path)
            self._samples = deque(maxlen=self.max_entries)
            self._count = 0
            self._reset_statistics()
            return list(self._samples)
        except OSError as e:
            logger.warning("读取生成时间样本文件失败: %s", e)
            self._samples = deque(maxlen=self.max_entries)
            self._reset_statistics()
            return list(self._samples)

        raw_samples = data.get("samples", []) if isinstance(data, dict) else []
        converted: list[tuple[int, float]] = []
        for item in raw_samples:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    converted.append((int(item[0]), float(item[1])))
                except (TypeError, ValueError):
                    continue
            elif isinstance(item, dict):
                try:
                    converted.append((int(item["char_count"]), float(item["duration"])))
                except (KeyError, TypeError, ValueError):
                    continue

        # 只保留最近 max_entries 条
        if len(converted) > self.max_entries:
            converted = converted[-self.max_entries :]

        self._samples = deque(converted, maxlen=self.max_entries)
        self._count = int(data.get("count", len(converted))) if isinstance(data, dict) else len(converted)

        # 初始化增量统计量
        self._recompute_statistics()
        self._update_coefficients()

        return list(self._samples)

    def _reset_statistics(self) -> None:
        """重置增量统计量和回归系数为默认值。"""
        self._n = 0
        self._sum_x = 0.0
        self._sum_y = 0.0
        self._sum_xy = 0.0
        self._sum_xx = 0.0
        self._slope = 1.0 / CHARS_PER_SECOND_DEFAULT
        self._intercept = 1.0

    def _recompute_statistics(self) -> None:
        """从当前样本队列重新计算所有统计量（O(n)，仅在加载时调用）。"""
        self._n = len(self._samples)
        self._sum_x = 0.0
        self._sum_y = 0.0
        self._sum_xy = 0.0
        self._sum_xx = 0.0
        for chars, dur in self._samples:
            x = float(chars)
            y = float(dur)
            self._sum_x += x
            self._sum_y += y
            self._sum_xy += x * y
            self._sum_xx += x * x

    def _add_sample_stat(self, x: float, y: float) -> None:
        """增量添加一个样本到统计量（O(1)）。"""
        self._n += 1
        self._sum_x += x
        self._sum_y += y
        self._sum_xy += x * y
        self._sum_xx += x * x

    def _remove_sample_stat(self, x: float, y: float) -> None:
        """从统计量中移除一个样本（O(1)）。"""
        if self._n <= 0:
            return
        self._n -= 1
        self._sum_x -= x
        self._sum_y -= y
        self._sum_xy -= x * y
        self._sum_xx -= x * x

    def _update_coefficients(self) -> None:
        """根据当前统计量更新回归系数（O(1)）。"""
        n = self._n
        if n < 2:
            if n == 1:
                # 单样本情况：斜率 = y/x，截距 = max(0, y - slope*x)
                x = self._sum_x
                y = self._sum_y
                if x > 1e-10:
                    self._slope = y / x
                else:
                    self._slope = 1.0 / CHARS_PER_SECOND_DEFAULT
                self._intercept = max(0.0, y - self._slope * x)
            else:
                self._slope = 1.0 / CHARS_PER_SECOND_DEFAULT
                self._intercept = 1.0
            return

        denominator = n * self._sum_xx - self._sum_x * self._sum_x
        if abs(denominator) < 1e-10:
            self._slope = 0.0
            self._intercept = self._sum_y / n if n > 0 else 1.0
        else:
            self._slope = (n * self._sum_xy - self._sum_x * self._sum_y) / denominator
            self._intercept = (self._sum_y - self._slope * self._sum_x) / n

        # 系数钳制，防止异常值
        if self._slope < 0.001:
            self._slope = 0.05
        if self._slope > 1.0:
            self._slope = 0.5
        if self._intercept < 0.5:
            self._intercept = 1.0

    def _save_data(self, force: bool = False) -> None:
        """将当前样本列表持久化到 JSON 文件（带节流）。

        写文件过程中遇到 ``PermissionError`` / ``OSError`` 时仅记录
        error 日志并静默吞掉，避免阻塞 :meth:`record_sample` 主流程。

        Args:
            force: 是否强制立即保存（忽略节流条件）。
        """
        now = time.time()
        # 节流：距离上次保存不足 _SAVE_THROTTLE_SECONDS 且样本数不足 _SAVE_THROTTLE_SAMPLES 时跳过
        if not force and (
            now - self._last_save_time < _SAVE_THROTTLE_SECONDS
            and self._samples_since_last_save < _SAVE_THROTTLE_SAMPLES
        ):
            return

        payload = {
            "samples": [[int(c), float(s)] for c, s in self._samples],
            "count": self._count,
        }
        try:
            tmp_path = self.data_file + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp_path, self.data_file)
            self._dirty = False
            self._last_save_time = now
            self._samples_since_last_save = 0
        except (PermissionError, OSError) as e:
            logger.error("保存生成时间样本文件失败（%s），已忽略", e)
            with contextlib.suppress(OSError):
                tmp_path = self.data_file + ".tmp"
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

    def flush(self) -> None:
        """强制将所有未保存的样本写入磁盘。"""
        with self._lock:
            if self._dirty:
                self._save_data(force=True)

    def record_sample(self, num_chars: int, elapsed_seconds: float, **kwargs: Any) -> None:
        """记录一条完成的生成样本（增量更新，O(1)）。

        Args:
            num_chars: 本次生成文本的字符数量。
            elapsed_seconds: 实际生成耗时（秒）。
            **kwargs: 向后兼容参数（engine、segment_count 等），
                当前版本不持久化，仅静默接收以免破坏历史调用方。
        """
        if num_chars <= 0 or elapsed_seconds <= 0:
            return

        with self._lock:
            x = float(num_chars)
            y = float(elapsed_seconds)

            # 如果队列已满，deque.append 会自动弹出最旧的样本
            if len(self._samples) >= self.max_entries:
                # 需要先从统计量中减去即将被淘汰的样本
                old_x, old_y = self._samples[0]
                self._remove_sample_stat(float(old_x), float(old_y))

            self._samples.append((int(num_chars), float(elapsed_seconds)))
            self._add_sample_stat(x, y)
            self._count += 1

            self._update_coefficients()
            self._dirty = True
            self._samples_since_last_save += 1
            self._save_data()  # 节流保存

    def record(self, char_count: int, duration: float, engine: str = "unknown", segment_count: int = 1) -> None:
        """向后兼容别名：等价于 :meth:`record_sample`。

        Args:
            char_count: 文本字符数（对应 ``num_chars``）。
            duration: 实际耗时秒数（对应 ``elapsed_seconds``）。
            engine: 引擎名（兼容参数，当前不持久化）。
            segment_count: 分段数（兼容参数，当前不持久化）。
        """
        self.record_sample(char_count, duration, engine=engine, segment_count=segment_count)

    def estimate(self, num_chars: int, segment_count: int = 1, **kwargs: Any) -> float:
        """估算给定字符数对应的生成耗时。

        Why 默认 fallback 速度为 15 chars/s：
            无样本冷启动阶段需要给用户一个合理预期；15 字符/秒
            对应常见 GPU 推理速度的中位数，避免显示"0 秒"假乐观
            或夸张的"几十秒"导致进度条跳动过大。

        Args:
            num_chars: 待生成文本字符数；<= 0 时立即返回 0.0。
            segment_count: 分段数（每多一段增加约 0.3s 合并开销）。
            **kwargs: 向后兼容关键字参数（如 char_count）。

        Returns:
            float: 预估生成秒数，最低返回 0.0（空文本）。
        """
        if "char_count" in kwargs and num_chars is None:
            num_chars = int(kwargs["char_count"])

        if num_chars <= 0:
            return 0.0

        sample_count = self._n
        if sample_count < 2:
            per_char = 1.0 / CHARS_PER_SECOND_DEFAULT if sample_count == 0 else self._slope
            base = per_char * float(num_chars) + (self._intercept if sample_count >= 1 else 0.5)
        else:
            base = self._slope * float(num_chars) + self._intercept

        if segment_count and segment_count > 1:
            base += 0.3 * float(segment_count - 1)

        return max(0.0, base)

    def estimate_with_confidence(self, num_chars: int) -> tuple[float, float]:
        """估算耗时并返回置信度（0-1）。

        Args:
            num_chars: 待生成文本字符数。

        Returns:
            tuple[float, float]: ``(estimated_seconds, confidence)``。
        """
        est = self.estimate(num_chars)
        if self._count == 0:
            confidence = 0.0
        elif self._count < 5:
            confidence = self._count / 10.0
        elif self._count < 20:
            confidence = 0.5 + (self._count - 5) / 30.0
        else:
            confidence = min(0.95, 0.65 + (self._count - 20) / 100.0)
        return est, confidence

    def get_stats(self) -> dict[str, Any]:
        """获取估算器统计信息。

        Returns:
            dict[str, Any]: 包含以下字段：
            - ``sample_count``: 累计记录样本数
            - ``slope``: 当前线性回归斜率（秒/字符）
            - ``intercept``: 当前线性回归截距（秒）
            - ``recent_samples``: 最近 5 条样本 ``[(chars, secs), ...]``
            - ``model``: 人类可读的模型公式
        """
        if not self._samples:
            return {
                "sample_count": 0,
                "slope": self._slope,
                "intercept": self._intercept,
                "recent_samples": [],
                "model": "default (no data)",
            }

        durations = [s[1] for s in self._samples]
        chars_list = [s[0] for s in self._samples]
        recent = list(self._samples)[-5:]  # deque 不支持切片，先转 list
        return {
            "sample_count": self._count,
            "slope": round(self._slope, 6),
            "intercept": round(self._intercept, 4),
            "recent_samples": [(int(c), round(float(d), 3)) for c, d in recent],
            "avg_duration": round(sum(durations) / len(durations), 2),
            "min_duration": round(min(durations), 2),
            "max_duration": round(max(durations), 2),
            "avg_chars": round(sum(chars_list) / len(chars_list), 1),
            "model": f"duration = {self._slope:.4f} * chars + {self._intercept:.2f}",
        }

    def reset(self) -> None:
        """清空所有样本并重置回归系数为默认值。"""
        with self._lock:
            self._samples = deque(maxlen=self.max_entries)
            self._count = 0
            self._reset_statistics()
            self._dirty = False
            self._last_save_time = 0.0
            self._samples_since_last_save = 0
            with contextlib.suppress(OSError):
                if os.path.exists(self.data_file):
                    os.remove(self.data_file)
