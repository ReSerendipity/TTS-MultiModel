"""RAS (Repetition Aware Sampling) — 重复感知采样策略。

在自回归 TTS 生成过程中检测 token 级别的重复模式，
并动态调整采样参数（temperature / top_p / min_p / repetition_penalty）以打破重复循环。

参考 Fish Speech 的 RAS 实现 + min_p 采样（来自开源 LLM 社区）+ 重复惩罚机制：
  - 滑动窗口追踪最近生成的 token
  - 检测 n-gram 重复（可配置 n 值）
  - 重复出现时自动提升 temperature、调整 top_p/min_p、应用重复惩罚
  - 参数设有上限，防止过度发散
  - min_p 采样：动态过滤累计概率低于 min_p 比例的 token，比固定 top_p 更自适应
  - 重复惩罚：对近期出现过的 token 施加概率惩罚，直接抑制循环重复

典型用法::

    from bin.integrated_app.ras_sampling import RepetitionDetector, adjust_sampling_params

    detector = RepetitionDetector()
    for token in generate_tokens(...):
        detector.append(token)
        temperature, top_p, min_p, rep_penalty = adjust_sampling_params_v2(
            temperature=0.7, top_p=0.9, min_p=0.05,
            repetition_penalty=1.0, detector=detector,
        )
        # 用调整后的参数进行下一步采样
"""

from __future__ import annotations

import logging
import threading
from collections import Counter, deque
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger("tts_multimodel")


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass
class RASConfig:
    """RAS 采样策略的可配置参数。

    Attributes:
        window_size: 滑动窗口大小，追踪最近 N 个 token 用于重复检测。
        ngram_n: n-gram 重复检测的 n 值。检测最近 window_size 个 token
                 中是否存在连续 n 个 token 重复出现。
        temp_increase: 每次检测到重复时 temperature 的增量。
        top_p_increase: 每次检测到重复时 top_p 的增量。
        max_temperature: temperature 的上限。
        max_top_p: top_p 的上限。
        repetition_threshold: n-gram 重复出现次数阈值，达到此值才触发调整。
        decay_factor: 非重复步骤中参数衰减因子（0 表示不衰减，1 表示立即回到原值）。
        min_p_base: min_p 基础值（动态过滤阈值，相对于最高概率 token 的比例）。
        min_p_floor: min_p 下限（防止过度过滤）。
        repetition_penalty_base: 基础重复惩罚（1.0 = 无惩罚，>1.0 惩罚近期 token）。
        repetition_penalty_max: 重复惩罚上限。
        penalty_window: 重复惩罚的历史窗口大小（最近 N 个 token 会被惩罚）。
        enable_min_p: 是否启用 min_p 采样。
        enable_repetition_penalty: 是否启用重复惩罚。
    """

    window_size: int = 50
    ngram_n: int = 3
    temp_increase: float = 0.2
    top_p_increase: float = 0.07
    max_temperature: float = 2.0
    max_top_p: float = 0.99
    repetition_threshold: int = 2
    decay_factor: float = 0.0

    # min_p 采样参数（参考 LLM 社区最佳实践）
    min_p_base: float = 0.05
    min_p_floor: float = 0.01
    enable_min_p: bool = True

    # 重复惩罚参数
    repetition_penalty_base: float = 1.0
    repetition_penalty_max: float = 1.5
    penalty_window: int = 128
    enable_repetition_penalty: bool = True


# ---------------------------------------------------------------------------
# 重复检测器
# ---------------------------------------------------------------------------


class RepetitionDetector:
    """基于滑动窗口的 n-gram 重复检测器 + token 频率统计。

    线程安全设计：内部使用 threading.Lock 保护状态变更。
    每个 TTS 生成任务应创建独立的检测器实例，或在任务边界调用 reset()。

    增强功能：
      - 维护 token 频率计数（用于重复惩罚）
      - 支持多种重复模式检测（n-gram、循环模式、单 token 循环）
      - 跟踪近期 token 历史用于惩罚计算

    用法::

        detector = RepetitionDetector(RASConfig(window_size=60, ngram_n=3))
        for token_id in token_stream:
            detector.append(token_id)
            if detector.is_repetitive():
                # 执行参数调整逻辑
                ...
    """

    def __init__(self, config: RASConfig | None = None) -> None:
        self._config = config or RASConfig()
        self._window: deque[int] = deque(maxlen=self._config.window_size)
        self._lock = threading.Lock()
        self._repetition_count: int = 0  # 连续检测到重复的累计次数
        self._total_detections: int = 0  # 历史总检测次数（用于统计/日志）

        # 重复惩罚相关状态
        self._penalty_history: deque[int] = deque(maxlen=self._config.penalty_window)
        self._token_counts: Counter = Counter()

    # -- 公共接口 --

    @property
    def config(self) -> RASConfig:
        """当前配置（只读）。"""
        return self._config

    @property
    def repetition_count(self) -> int:
        """当前连续重复检测计数。"""
        with self._lock:
            return self._repetition_count

    @property
    def total_detections(self) -> int:
        """历史总重复检测次数。"""
        with self._lock:
            return self._total_detections

    def append(self, token: int) -> None:
        """向滑动窗口追加一个 token。

        Args:
            token: 生成的 token ID。
        """
        with self._lock:
            self._window.append(token)

            # 更新重复惩罚历史
            if self._config.enable_repetition_penalty:
                self._penalty_history.append(token)
                # Counter 只保留窗口内的计数
                if len(self._penalty_history) > self._config.penalty_window:
                    old_token = self._penalty_history[0]
                    if self._token_counts[old_token] > 0:
                        self._token_counts[old_token] -= 1
                        if self._token_counts[old_token] == 0:
                            del self._token_counts[old_token]
                self._token_counts[token] += 1

            # 追加后检查是否触发重复，更新内部计数
            is_repeat = self._check_ngram_repetition()
            # 额外检查单 token 循环（如 "a a a a" 模式）
            if not is_repeat:
                is_repeat = self._check_single_token_loop()
            # 额外检查双 token 循环（如 "a b a b a b" 模式）
            if not is_repeat:
                is_repeat = self._check_two_token_cycle()

            if is_repeat:
                self._repetition_count += 1
                self._total_detections += 1
            else:
                # 非重复步骤：衰减计数（但不低于 0）
                if self._config.decay_factor > 0:
                    self._repetition_count = max(
                        0,
                        self._repetition_count - 1,
                    )

    def append_batch(self, tokens: Sequence[int]) -> None:
        """批量追加 token。

        Args:
            tokens: token ID 序列。
        """
        for t in tokens:
            self.append(t)

    def is_repetitive(self) -> bool:
        """判断当前是否处于重复状态。

        当连续重复检测计数 >= 配置的 repetition_threshold 时返回 True。

        Returns:
            True 表示检测到有意义的重复模式，应调整采样参数。
        """
        with self._lock:
            return self._repetition_count >= self._config.repetition_threshold

    def get_repetition_level(self) -> int:
        """获取当前重复严重程度等级。

        Returns:
            超过阈值的重复计数差值，0 表示未超过阈值。
        """
        with self._lock:
            excess = self._repetition_count - self._config.repetition_threshold
            return max(0, excess)

    def get_token_count(self, token: int) -> int:
        """获取指定 token 在惩罚窗口内的出现次数。

        Args:
            token: token ID

        Returns:
            出现次数
        """
        with self._lock:
            return self._token_counts.get(token, 0)

    def get_window_tokens(self) -> list[int]:
        """获取当前滑动窗口中的所有 token（副本）。

        Returns:
            token 列表（从旧到新）。
        """
        with self._lock:
            return list(self._window)

    def get_recent_tokens(self, n: int = 50) -> list[int]:
        """获取最近 N 个 token（用于重复惩罚）。

        Args:
            n: 要获取的 token 数量

        Returns:
            最近的 token 列表
        """
        with self._lock:
            return list(self._penalty_history)[-n:]

    def reset(self) -> None:
        """重置检测器状态，清空滑动窗口和计数器。"""
        with self._lock:
            self._window.clear()
            self._penalty_history.clear()
            self._token_counts.clear()
            self._repetition_count = 0
            self._total_detections = 0

    # -- 内部方法 --

    def _check_ngram_repetition(self) -> bool:
        """检查最近 n-gram 是否在滑动窗口内重复出现。

        算法：取窗口末尾 ngram_n 个 token 作为目标 n-gram，
        在窗口剩余部分中搜索是否存在相同的 n-gram。

        Returns:
            True 表示目标 n-gram 在窗口内有重复。
        """
        n = self._config.ngram_n
        window = self._window

        if len(window) < n * 2:
            # 窗口太短，无法形成两组 n-gram 进行比较
            return False

        # 最近 n 个 token 构成的目标 n-gram
        target = list(window)[-n:]

        # 在窗口前部搜索相同 n-gram
        window_list = list(window)
        search_range = len(window_list) - n  # 不包含末尾的 target 本身
        for i in range(search_range - n + 1):
            candidate = window_list[i : i + n]
            if candidate == target:
                return True

        return False

    def _check_single_token_loop(self) -> bool:
        """检查单 token 循环模式（如 "a a a a" 连续重复）。

        Returns:
            True 表示检测到单 token 连续重复。
        """
        window = self._window
        if len(window) < 4:
            return False
        # 检查最后 4 个 token 是否全部相同
        last_tokens = list(window)[-4:]
        return len(set(last_tokens)) == 1

    def _check_two_token_cycle(self) -> bool:
        """检查双 token 循环模式（如 "a b a b a b"）。

        Returns:
            True 表示检测到双 token 循环。
        """
        window = self._window
        if len(window) < 6:
            return False
        last_tokens = list(window)[-6:]
        # 检查是否符合 ababab 模式
        return (
            last_tokens[0] == last_tokens[2] == last_tokens[4]
            and last_tokens[1] == last_tokens[3] == last_tokens[5]
            and last_tokens[0] != last_tokens[1]
        )


# ---------------------------------------------------------------------------
# 采样策略核心函数
# ---------------------------------------------------------------------------


def apply_min_p_filtering(
    logits: np.ndarray,
    min_p: float,
    temperature: float = 1.0,
) -> np.ndarray:
    """应用 min-p 采样过滤。

    min-p 采样策略：保留概率不低于 (最高概率 * min_p) 的所有 token。
    这比固定的 top-p/top-k 更自适应：当模型非常确定时（一个 token 概率极高），
    过滤掉绝大多数低概率 token；当模型不确定时（分布平坦），保留更多候选。

    参考：https://github.com/ggerganov/llama.cpp/pull/3853

    Args:
        logits: 原始 logits 数组 (vocab_size,)
        min_p: 最小概率比例（相对于最高概率 token）
        temperature: 温度参数

    Returns:
        过滤后的 logits（被过滤位置设为 -inf）
    """
    if min_p >= 1.0 or min_p <= 0.0:
        return logits

    # 应用温度
    if temperature != 1.0:
        logits = logits / max(temperature, 1e-8)

    # 计算 softmax 概率
    logits_max = np.max(logits)
    exp_logits = np.exp(logits - logits_max)
    probs = exp_logits / np.sum(exp_logits)

    # 找到最高概率
    max_prob = np.max(probs)
    threshold = max_prob * min_p

    # 过滤低于阈值的 token
    sorted_indices = np.argsort(-probs)
    cumulative_prob = 0.0
    mask = np.ones_like(probs, dtype=bool)

    for idx in sorted_indices:
        if probs[idx] < threshold and cumulative_prob > 0:
            mask[idx] = False
        else:
            cumulative_prob += probs[idx]

    filtered_logits = logits.copy()
    filtered_logits[~mask] = -float("inf")

    return filtered_logits


def apply_repetition_penalty(
    logits: np.ndarray,
    token_counts: dict[int, int],
    penalty: float,
    presence_penalty: float = 0.0,
    frequency_penalty: float = 0.0,
) -> np.ndarray:
    """应用重复惩罚（Repetition Penalty）。

    对近期出现过的 token 降低其 logit 值，直接抑制重复生成。
    参考 Transformers 库的 repetition_penalty 实现。

    Args:
        logits: 原始 logits 数组 (vocab_size,)
        token_counts: token 出现次数字典 {token_id: count}
        penalty: 重复惩罚系数（1.0 = 无惩罚，>1.0 惩罚重复 token）
        presence_penalty: 存在惩罚（对出现过的 token 附加固定惩罚）
        frequency_penalty: 频率惩罚（按出现次数惩罚）

    Returns:
        应用惩罚后的 logits
    """
    if penalty <= 1.0 and presence_penalty <= 0.0 and frequency_penalty <= 0.0:
        return logits

    penalized_logits = logits.copy()

    for token_id, count in token_counts.items():
        if count <= 0:
            continue
        if 0 <= token_id < len(logits):
            logit_val = penalized_logits[token_id]

            # 重复惩罚（对数概率缩放）
            if penalty > 1.0:
                if logit_val > 0:
                    penalized_logits[token_id] = logit_val / penalty
                else:
                    penalized_logits[token_id] = logit_val * penalty

            # 存在惩罚（固定偏移）
            if presence_penalty > 0.0:
                penalized_logits[token_id] -= presence_penalty

            # 频率惩罚（按次数偏移）
            if frequency_penalty > 0.0:
                penalized_logits[token_id] -= frequency_penalty * count

    return penalized_logits


def adjust_sampling_params(
    temperature: float,
    top_p: float,
    detector: RepetitionDetector,
    config: RASConfig | None = None,
) -> tuple[float, float]:
    """根据重复检测状态动态调整采样参数（兼容旧接口）。

    当检测器报告重复时，按以下策略调整：
      1. 根据重复严重程度（repetition_level）决定调整幅度
      2. 温和调整（level=0）：增加 temp_increase / top_p_increase
      3. 中度调整（level=1）：增加 1.5 倍幅度
      4. 强力调整（level>=2）：增加 2 倍幅度
      5. 所有调整受 max_temperature / max_top_p 约束

    Args:
        temperature: 当前 temperature 值。
        top_p: 当前 top_p 值。
        detector: 重复检测器实例。
        config: RAS 配置，默认使用 detector 自带的配置。

    Returns:
        (adjusted_temperature, adjusted_top_p) 元组。
        若无重复，返回原始参数。
    """
    if not detector.is_repetitive():
        return temperature, top_p

    cfg = config or detector.config
    level = detector.get_repetition_level()

    # 根据重复严重程度确定调整倍率
    if level == 0:
        multiplier = 1.0
    elif level == 1:
        multiplier = 1.5
    else:
        multiplier = 2.0

    new_temp = min(
        temperature + cfg.temp_increase * multiplier,
        cfg.max_temperature,
    )
    new_top_p = min(
        top_p + cfg.top_p_increase * multiplier,
        cfg.max_top_p,
    )

    logger.debug(
        f"[RAS] 检测到重复 (level={level}), "
        f"temperature: {temperature:.3f} -> {new_temp:.3f}, "
        f"top_p: {top_p:.3f} -> {new_top_p:.3f}"
    )

    return new_temp, new_top_p


def adjust_sampling_params_v2(
    temperature: float,
    top_p: float,
    detector: RepetitionDetector,
    config: RASConfig | None = None,
    min_p: float | None = None,
    repetition_penalty: float | None = None,
) -> tuple[float, float, float, float]:
    """根据重复检测状态动态调整采样参数（增强版 v2）。

    新增支持：
      - min_p 动态调整：重复时降低 min_p 以保留更多候选
      - repetition_penalty 动态调整：重复时增加惩罚力度

    Args:
        temperature: 当前 temperature 值。
        top_p: 当前 top_p 值。
        detector: 重复检测器实例。
        config: RAS 配置，默认使用 detector 自带的配置。
        min_p: 当前 min_p 值（为 None 时使用配置默认值）。
        repetition_penalty: 当前重复惩罚值（为 None 时使用配置默认值）。

    Returns:
        (adjusted_temperature, adjusted_top_p, adjusted_min_p, adjusted_rep_penalty) 元组。
        若无重复，返回原始参数。
    """
    cfg = config or detector.config

    # 默认值
    if min_p is None:
        min_p = cfg.min_p_base
    if repetition_penalty is None:
        repetition_penalty = cfg.repetition_penalty_base

    if not detector.is_repetitive():
        return temperature, top_p, min_p, repetition_penalty

    level = detector.get_repetition_level()

    # 根据重复严重程度确定调整倍率
    if level == 0:
        multiplier = 1.0
    elif level == 1:
        multiplier = 1.5
    else:
        multiplier = 2.0

    # Temperature 提升（增加多样性）
    new_temp = min(
        temperature + cfg.temp_increase * multiplier,
        cfg.max_temperature,
    )

    # Top-p 提升（增加采样范围）
    new_top_p = min(
        top_p + cfg.top_p_increase * multiplier,
        cfg.max_top_p,
    )

    # Min-p 调整：重复时降低 min_p，保留更多候选 token
    if cfg.enable_min_p:
        min_p_decrease = 0.02 * multiplier
        new_min_p = max(min_p - min_p_decrease, cfg.min_p_floor)
    else:
        new_min_p = min_p

    # 重复惩罚调整：重复时增加惩罚力度
    if cfg.enable_repetition_penalty:
        penalty_increase = 0.1 * multiplier
        new_rep_penalty = min(
            repetition_penalty + penalty_increase,
            cfg.repetition_penalty_max,
        )
    else:
        new_rep_penalty = repetition_penalty

    logger.debug(
        f"[RAS-v2] 检测到重复 (level={level}), "
        f"temperature: {temperature:.3f} -> {new_temp:.3f}, "
        f"top_p: {top_p:.3f} -> {new_top_p:.3f}, "
        f"min_p: {min_p:.3f} -> {new_min_p:.3f}, "
        f"rep_penalty: {repetition_penalty:.3f} -> {new_rep_penalty:.3f}"
    )

    return new_temp, new_top_p, new_min_p, new_rep_penalty


# ---------------------------------------------------------------------------
# VoxCPM2 引擎集成辅助
# ---------------------------------------------------------------------------


class RASContext:
    """VoxCPM2 引擎的 RAS 上下文管理器。

    封装 RepetitionDetector + 参数调整逻辑，提供简洁的集成接口。
    建议在 VoxCPM2 生成函数中作为上下文管理器使用，确保每次
    生成任务自动重置检测器状态。

    v2 增强：支持 min_p 和重复惩罚参数调整。

    用法::

        with RASContext() as ras:
            for token in generate_stream:
                ras.feed(token)
                temp, top_p, min_p, rep_penalty = ras.get_params_v2(
                    temperature=0.7, top_p=0.9, min_p=0.05, repetition_penalty=1.1,
                )
    """

    def __init__(self, config: RASConfig | None = None) -> None:
        self._config = config or RASConfig()
        self._detector = RepetitionDetector(self._config)

    @property
    def detector(self) -> RepetitionDetector:
        """底层检测器实例（高级用途）。"""
        return self._detector

    @property
    def config(self) -> RASConfig:
        """当前 RAS 配置。"""
        return self._config

    def feed(self, token: int) -> None:
        """向检测器喂入一个 token。

        Args:
            token: 生成的 token ID。
        """
        self._detector.append(token)

    def feed_batch(self, tokens: Sequence[int]) -> None:
        """批量喂入 token。

        Args:
            tokens: token ID 序列。
        """
        self._detector.append_batch(tokens)

    def get_params(
        self,
        temperature: float,
        top_p: float,
    ) -> tuple[float, float]:
        """获取当前调整后的采样参数（兼容旧接口）。

        Args:
            temperature: 基础 temperature。
            top_p: 基础 top_p。

        Returns:
            (adjusted_temperature, adjusted_top_p) 元组。
        """
        return adjust_sampling_params(
            temperature=temperature,
            top_p=top_p,
            detector=self._detector,
            config=self._config,
        )

    def get_params_v2(
        self,
        temperature: float,
        top_p: float,
        min_p: float | None = None,
        repetition_penalty: float | None = None,
    ) -> tuple[float, float, float, float]:
        """获取当前调整后的采样参数（增强版 v2）。

        Args:
            temperature: 基础 temperature。
            top_p: 基础 top_p。
            min_p: 基础 min_p（None 使用配置默认值）。
            repetition_penalty: 基础重复惩罚（None 使用配置默认值）。

        Returns:
            (adjusted_temperature, adjusted_top_p, adjusted_min_p, adjusted_rep_penalty) 元组。
        """
        return adjust_sampling_params_v2(
            temperature=temperature,
            top_p=top_p,
            detector=self._detector,
            config=self._config,
            min_p=min_p,
            repetition_penalty=repetition_penalty,
        )

    def get_token_counts(self) -> dict[int, int]:
        """获取当前 token 频率计数（用于重复惩罚）。

        Returns:
            {token_id: count} 字典
        """
        return dict(self._detector._token_counts)

    def apply_logits_penalties(
        self,
        logits: np.ndarray,
        penalty: float | None = None,
        presence_penalty: float = 0.0,
        frequency_penalty: float = 0.0,
    ) -> np.ndarray:
        """对 logits 应用重复惩罚。

        Args:
            logits: 原始 logits 数组
            penalty: 重复惩罚系数（None 使用动态调整后的值）
            presence_penalty: 存在惩罚
            frequency_penalty: 频率惩罚

        Returns:
            惩罚后的 logits
        """
        if penalty is None:
            penalty = self._config.repetition_penalty_base
            if self._detector.is_repetitive():
                level = self._detector.get_repetition_level()
                penalty = min(
                    penalty + 0.1 * (level + 1),
                    self._config.repetition_penalty_max,
                )

        return apply_repetition_penalty(
            logits,
            self._detector._token_counts,
            penalty=penalty,
            presence_penalty=presence_penalty,
            frequency_penalty=frequency_penalty,
        )

    def is_repetitive(self) -> bool:
        """当前是否处于重复状态。"""
        return self._detector.is_repetitive()

    def get_repetition_level(self) -> int:
        """获取当前重复严重程度。"""
        return self._detector.get_repetition_level()

    def reset(self) -> None:
        """手动重置检测器。"""
        self._detector.reset()

    def __enter__(self) -> RASContext:
        """进入上下文：重置检测器状态。"""
        self._detector.reset()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """退出上下文：记录统计信息并重置。"""
        detections = self._detector.total_detections
        if detections > 0:
            logger.info(f"[RAS] 生成任务完成，共检测到 {detections} 次 n-gram 重复")
        self._detector.reset()


def create_ras_context_from_generation_config(
    **overrides,
) -> RASContext:
    """从生成配置创建 RAS 上下文。

    优先使用传入的 override 参数，其余使用默认值。
    方便 VoxCPM2 各生成函数快速创建 RAS 上下文。

    Args:
        **overrides: 覆盖 RASConfig 默认值的参数。

    Returns:
        配置好的 RASContext 实例。
    """
    config = RASConfig(**overrides)
    return RASContext(config=config)


__all__ = [
    "RASConfig",
    "RepetitionDetector",
    "RASContext",
    "adjust_sampling_params",
    "adjust_sampling_params_v2",
    "apply_min_p_filtering",
    "apply_repetition_penalty",
    "create_ras_context_from_generation_config",
]
