"""坏案例自动重试机制（Bad Case Retry）

参考 Fish Speech、CosyVoice 和 Chatterbox 的容错设计：
  - 检测退化输出（静音、过短、爆音、重复模式等）
  - 多维度参数调整策略（cfg_value、temperature、top_p、seed）
  - 指数退避 + 参数渐进调整
  - 重试耗尽时优雅降级，不中断整体生成

核心策略：
  1. 第一次重试：递增 cfg_value + 新 seed
  2. 第二次重试：提高 temperature + 降低 top_p + 新 seed
  3. 第三次重试：大幅提高 cfg_value + 提高 temperature + 新 seed
  4. 重试耗尽：接受当前输出，记录 warning

设计参考：
  - Fish Speech: RAS (Repetition Aware Sampling) 动态参数调整
  - CosyVoice: 流式生成中的段级容错
  - Chatterbox: IntMeanFlow 快速重试
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np

logger = logging.getLogger("tts_multimodel")


class RetryStrategy(Enum):
    """重试策略枚举，定义参数调整的方向。

    每种策略对应一种参数调整方式，可组合使用：
    - CFG_INCREASE: 提高/降低 cfg_value，控制条件引导强度
    - TEMP_INCREASE: 提高 temperature，增加采样随机性
    - TOP_P_DECREASE: 降低 top_p（nucleus sampling），使输出更聚焦
    - TOP_P_INCREASE: 提高 top_p，增加输出多样性
    - SEED_CHANGE: 更换随机种子，避免重复结果
    - COMBINED: 组合多种策略，用于未知失败类型
    """

    CFG_INCREASE = "cfg_increase"
    TEMP_INCREASE = "temp_increase"
    TOP_P_DECREASE = "top_p_decrease"
    TOP_P_INCREASE = "top_p_increase"
    SEED_CHANGE = "seed_change"
    COMBINED = "combined"


class FailureType(Enum):
    """音频生成失败类型枚举，用于分类检测到的坏案例。

    每种失败类型对应不同的参数调整策略：
    - SILENCE: 静音/能量过低，通常需要降低 cfg、提高 temperature
    - TOO_SHORT: 时长过短，需要降低 cfg 避免提前结束
    - TOO_LONG: 时长过长，需要提高 cfg、降低 temperature
    - CLIPPING: 爆音/削波，需要降低 cfg 和 temperature
    - REPETITION: 重复模式，需要提高 temperature 和 top_p 增加多样性
    - LOW_VARIANCE: 方差过低（潜在重复），同 REPETITION 策略
    - INTERNAL_SILENCE: 内部异常静音，需要提高 cfg、降低 top_p
    - UNKNOWN: 未知异常，使用组合策略
    """

    SILENCE = "silence"
    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    CLIPPING = "clipping"
    REPETITION = "repetition"
    LOW_VARIANCE = "low_variance"
    INTERNAL_SILENCE = "internal_silence"
    UNKNOWN = "unknown"


@dataclass
class RetryConfig:
    """重试机制配置参数。

    Attributes:
        max_retries: 最大重试次数，默认 3 次
        base_delay_ms: 基础延迟（毫秒），用于指数退避计算
        enable_seed_rotation: 是否启用种子轮换，避免生成相同结果
        cfg_value_step: cfg_value 调整步长
        cfg_value_max: cfg_value 上限，防止过度引导
        temperature_step: temperature 调整步长
        temperature_max: temperature 上限
        top_p_min: top_p 下限（更聚焦）
        top_p_max: top_p 上限（更多样）
        min_duration_sec: 最小可接受音频时长（秒）
        max_duration_sec: 最大可接受音频时长（秒）
        rms_threshold: RMS 能量阈值，低于此值判定为静音
        variance_threshold: 音频方差阈值，低于此值判定为低方差/重复
        clipping_threshold: 削波阈值（0.0-1.0），超过此值判定为爆音
        enable_combined_strategy: 未知失败类型时是否启用组合策略
    """

    max_retries: int = 3
    base_delay_ms: int = 100
    enable_seed_rotation: bool = True
    cfg_value_step: float = 0.5
    cfg_value_max: float = 5.0
    temperature_step: float = 0.1
    temperature_max: float = 1.2
    top_p_min: float = 0.7
    top_p_max: float = 0.95
    min_duration_sec: float = 0.3
    max_duration_sec: float = 30.0
    rms_threshold: float = 1e-4
    variance_threshold: float = 1e-8
    clipping_threshold: float = 0.98
    enable_combined_strategy: bool = True


@dataclass
class RetryState:
    """重试过程状态追踪，记录当前重试的上下文信息。

    Attributes:
        attempt: 当前尝试次数（1-based）
        failure_type: 最近一次检测到的失败类型
        failure_reason: 最近一次失败的原因描述
        last_params: 上一次重试使用的生成参数
        adjustments: 已应用的调整策略列表（按顺序）
        start_time: 首次尝试开始的时间戳
    """

    attempt: int = 0
    failure_type: FailureType = FailureType.UNKNOWN
    failure_reason: str = ""
    last_params: dict[str, Any] = field(default_factory=dict)
    adjustments: list[RetryStrategy] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)


@dataclass
class RetryResult:
    """重试执行结果。

    Attributes:
        success: 是否最终成功生成可接受的音频（优雅降级时也为 True）
        wav: 生成的音频数组（float32, [-1, 1]），失败时为 None
        sample_rate: 音频采样率（Hz）
        attempts: 实际尝试次数
        final_params: 最终成功时使用的生成参数
        failure_reason: 失败原因描述（重试耗尽时包含降级说明）
    """

    success: bool
    wav: np.ndarray | None = None
    sample_rate: int = 48000
    attempts: int = 0
    final_params: dict[str, Any] = field(default_factory=dict)
    failure_reason: str = ""


def _fmt_num(value: Any) -> str:
    """安全格式化数值用于日志，非数值（如缺失参数回退的 N/A）直接返回字符串。"""
    return f"{value:.2f}" if isinstance(value, (int, float)) else str(value)


def detect_failure_type(
    wav: np.ndarray,
    sample_rate: int,
    expected_duration: float | None = None,
    config: RetryConfig | None = None,
) -> tuple[bool, FailureType, str]:
    """检测音频失败类型

    Args:
        wav: 音频数组
        sample_rate: 采样率
        expected_duration: 期望时长（秒），用于判断过长/过短
        config: 重试配置

    Returns:
        (has_failure, failure_type, reason)
    """
    cfg = config or RetryConfig()

    if wav is None or len(wav) == 0:
        return True, FailureType.SILENCE, "空音频"

    try:
        wav_f64 = wav.astype(np.float64)
        duration = len(wav) / sample_rate

        # 检测过短
        min_dur = cfg.min_duration_sec
        if expected_duration is not None:
            min_dur = max(min_dur, expected_duration * 0.3)
        if duration < min_dur:
            return True, FailureType.TOO_SHORT, f"时长过短 ({duration:.2f}s < {min_dur:.2f}s)"

        # 检测过长（超过期望时长的 3 倍或绝对最大值）
        max_dur = cfg.max_duration_sec
        if expected_duration is not None:
            max_dur = min(max_dur, expected_duration * 3.0)
        if duration > max_dur:
            return True, FailureType.TOO_LONG, f"时长过长 ({duration:.2f}s > {max_dur:.2f}s)"

        # 检测削波/爆音
        peak = float(np.max(np.abs(wav_f64)))
        if peak > cfg.clipping_threshold:
            clipping_ratio = float(np.mean(np.abs(wav_f64) > cfg.clipping_threshold))
            if clipping_ratio > 0.01:  # 超过 1% 的样本削波
                return True, FailureType.CLIPPING, f"检测到削波 (peak={peak:.3f}, ratio={clipping_ratio:.2%})"

        # 检测静音/能量过低
        rms = float(np.sqrt(np.mean(wav_f64**2)))
        if rms < cfg.rms_threshold:
            return True, FailureType.SILENCE, f"能量过低 (RMS={rms:.6f})"

        # 检测低方差（重复模式）
        variance = float(np.var(wav_f64))
        if variance < cfg.variance_threshold:
            return True, FailureType.LOW_VARIANCE, f"方差过低 (var={variance:.2e})，可能重复"

        # 检测内部异常长静音（超过 2 秒的静音段）
        if duration > 2.0:
            frame_size = int(sample_rate * 0.02)  # 20ms 帧
            if len(wav_f64) >= frame_size * 100:  # 至少 2 秒
                frames = np.array_split(wav_f64[: len(wav_f64) // frame_size * frame_size], len(wav_f64) // frame_size)
                frame_rms = np.array([np.sqrt(np.mean(f**2)) for f in frames])
                silence_frames = frame_rms < cfg.rms_threshold * 2
                # 检测连续静音帧超过 100 帧（2秒）
                max_consecutive_silence = 0
                current_silence = 0
                for is_silence in silence_frames:
                    if is_silence:
                        current_silence += 1
                        max_consecutive_silence = max(max_consecutive_silence, current_silence)
                    else:
                        current_silence = 0
                if max_consecutive_silence > 100:  # 2秒以上静音
                    silence_dur = max_consecutive_silence * 0.02
                    return (True, FailureType.INTERNAL_SILENCE, f"内部异常静音 ({silence_dur:.1f}s)")

        return False, FailureType.UNKNOWN, "OK"

    except Exception as e:
        return True, FailureType.UNKNOWN, f"质量检测异常: {type(e).__name__}: {e}"


def adjust_params_for_retry(
    params: dict[str, Any],
    failure_type: FailureType,
    attempt: int,
    config: RetryConfig | None = None,
) -> dict[str, Any]:
    """根据失败类型和重试次数调整生成参数

    参考 Fish Speech RAS 的渐进式参数调整策略。

    Args:
        params: 当前生成参数字典
        failure_type: 检测到的失败类型
        attempt: 当前重试次数（1-based）
        config: 重试配置

    Returns:
        调整后的参数字典
    """
    cfg = config or RetryConfig()
    new_params = dict(params)
    strategies_applied: list[RetryStrategy] = []

    # 根据失败类型选择调整策略
    if failure_type == FailureType.SILENCE or failure_type == FailureType.TOO_SHORT:
        # 静音/过短：降低 cfg（更具表现力）+ 提高 temperature + 新 seed
        new_params["cfg_value"] = max(new_params.get("cfg_value", 2.0) - cfg.cfg_value_step * 0.5, 1.0)
        new_params["temperature"] = min(
            new_params.get("temperature", 0.8) + cfg.temperature_step * attempt, cfg.temperature_max
        )
        strategies_applied.extend([RetryStrategy.CFG_INCREASE, RetryStrategy.TEMP_INCREASE])

    elif failure_type == FailureType.CLIPPING:
        # 削波/爆音：降低 cfg + 降低 temperature
        new_params["cfg_value"] = max(new_params.get("cfg_value", 2.0) - cfg.cfg_value_step, 1.0)
        new_params["temperature"] = max(new_params.get("temperature", 0.8) - cfg.temperature_step * 0.5, 0.5)
        strategies_applied.append(RetryStrategy.CFG_INCREASE)

    elif failure_type == FailureType.REPETITION or failure_type == FailureType.LOW_VARIANCE:
        # 重复/低方差：提高 temperature + 提高 top_p（更多样性）+ 提高 cfg
        new_params["temperature"] = min(
            new_params.get("temperature", 0.8) + cfg.temperature_step * attempt, cfg.temperature_max
        )
        new_params["top_p"] = min(new_params.get("top_p", 0.9) + 0.05 * attempt, cfg.top_p_max)
        new_params["cfg_value"] = min(
            new_params.get("cfg_value", 2.0) + cfg.cfg_value_step * attempt, cfg.cfg_value_max
        )
        strategies_applied.extend(
            [
                RetryStrategy.TEMP_INCREASE,
                RetryStrategy.TOP_P_INCREASE,
                RetryStrategy.CFG_INCREASE,
            ]
        )

    elif failure_type == FailureType.INTERNAL_SILENCE:
        # 内部静音：提高 cfg（更严格遵循条件）+ 新 seed
        new_params["cfg_value"] = min(
            new_params.get("cfg_value", 2.0) + cfg.cfg_value_step * attempt, cfg.cfg_value_max
        )
        new_params["top_p"] = max(new_params.get("top_p", 0.9) - 0.05 * attempt, cfg.top_p_min)
        strategies_applied.extend([RetryStrategy.CFG_INCREASE, RetryStrategy.TOP_P_DECREASE])

    elif failure_type == FailureType.TOO_LONG:
        # 过长：提高 cfg + 降低 temperature
        new_params["cfg_value"] = min(new_params.get("cfg_value", 2.0) + cfg.cfg_value_step * 0.5, cfg.cfg_value_max)
        new_params["temperature"] = max(new_params.get("temperature", 0.8) - cfg.temperature_step * 0.3, 0.6)
        strategies_applied.append(RetryStrategy.CFG_INCREASE)

    else:
        # 未知异常：组合策略
        if cfg.enable_combined_strategy:
            new_params["cfg_value"] = min(
                new_params.get("cfg_value", 2.0) + cfg.cfg_value_step * (attempt % 2 == 0 and 0.5 or -0.25),
                cfg.cfg_value_max,
            )
            new_params["temperature"] = min(
                new_params.get("temperature", 0.8) + cfg.temperature_step * 0.5, cfg.temperature_max
            )
            strategies_applied.append(RetryStrategy.COMBINED)

    # 始终轮换随机种子（避免生成完全相同的结果）
    if cfg.enable_seed_rotation:
        current_seed = new_params.get("seed")
        if current_seed is not None:
            # 基于当前 seed 确定性地生成新 seed
            new_params["seed"] = (current_seed * 1103515245 + 12345 + attempt * 7919) & 0x7FFFFFFF
        else:
            # 原先是随机生成，现在也随机生成新 seed
            new_params["seed"] = random.randint(0, 0x7FFFFFFF)
        strategies_applied.append(RetryStrategy.SEED_CHANGE)

    logger.info(
        f"[BadCaseRetry] 第 {attempt} 次重试参数调整: "
        f"cfg={_fmt_num(new_params.get('cfg_value', 'N/A'))}, "
        f"temp={_fmt_num(new_params.get('temperature', 'N/A'))}, "
        f"top_p={_fmt_num(new_params.get('top_p', 'N/A'))}, "
        f"seed={new_params.get('seed', 'N/A')}, "
        f"策略={[s.value for s in strategies_applied]}"
    )

    return new_params


def retry_with_bad_case_detection(
    generate_fn: Callable[..., np.ndarray],
    params: dict[str, Any],
    sample_rate: int = 48000,
    expected_duration: float | None = None,
    config: RetryConfig | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> RetryResult:
    """带坏案例检测的重试生成函数

    Args:
        generate_fn: 实际生成函数，接受 **kwargs 并返回 np.ndarray
        params: 生成参数字典
        sample_rate: 采样率
        expected_duration: 期望时长（秒）
        config: 重试配置
        progress_callback: 进度回调 (attempt, max_attempts, reason) -> None

    Returns:
        RetryResult 结果对象
    """
    cfg = config or RetryConfig()
    state = RetryState()
    current_params = dict(params)

    for attempt in range(cfg.max_retries + 1):
        state.attempt = attempt + 1

        # 非首次尝试时应用延迟（避免立即重试导致的资源竞争）
        if attempt > 0:
            delay_ms = cfg.base_delay_ms * (2 ** (attempt - 1))  # 指数退避
            time.sleep(delay_ms / 1000.0)

            # 调整参数
            current_params = adjust_params_for_retry(current_params, state.failure_type, attempt, cfg)
            state.last_params = dict(current_params)

            if progress_callback:
                progress_callback(attempt, cfg.max_retries, state.failure_reason)

        try:
            # 执行生成
            wav = generate_fn(**current_params)

            # 质量检测
            has_failure, failure_type, reason = detect_failure_type(wav, sample_rate, expected_duration, cfg)

            if not has_failure:
                logger.info(f"[BadCaseRetry] 生成成功 (尝试 {attempt + 1}/{cfg.max_retries + 1})")
                return RetryResult(
                    success=True,
                    wav=wav,
                    sample_rate=sample_rate,
                    attempts=attempt + 1,
                    final_params=current_params,
                )

            # 检测到失败
            state.failure_type = failure_type
            state.failure_reason = reason
            logger.warning(f"[BadCaseRetry] 尝试 {attempt + 1}/{cfg.max_retries + 1} 失败: {reason}")

            # 如果是最后一次尝试，接受当前结果
            if attempt == cfg.max_retries:
                logger.warning(f"[BadCaseRetry] 重试耗尽 ({cfg.max_retries} 次)，接受当前输出")
                return RetryResult(
                    success=True,  # 优雅降级：即使质量不佳也返回
                    wav=wav,
                    sample_rate=sample_rate,
                    attempts=attempt + 1,
                    final_params=current_params,
                    failure_reason=f"重试耗尽，接受低质量输出: {reason}",
                )

        except Exception as e:
            state.failure_type = FailureType.UNKNOWN
            state.failure_reason = str(e)
            logger.warning(f"[BadCaseRetry] 尝试 {attempt + 1}/{cfg.max_retries + 1} 异常: {type(e).__name__}: {e}")

            if attempt == cfg.max_retries:
                logger.error("[BadCaseRetry] 重试耗尽且所有尝试均异常")
                return RetryResult(
                    success=False,
                    attempts=attempt + 1,
                    final_params=current_params,
                    failure_reason=f"所有重试均失败: {e}",
                )

    # 理论上不会到达这里
    return RetryResult(
        success=False,
        attempts=state.attempt,
        failure_reason="未知错误",
    )


__all__ = [
    "RetryConfig",
    "RetryState",
    "RetryResult",
    "RetryStrategy",
    "FailureType",
    "detect_failure_type",
    "adjust_params_for_retry",
    "retry_with_bad_case_detection",
]
