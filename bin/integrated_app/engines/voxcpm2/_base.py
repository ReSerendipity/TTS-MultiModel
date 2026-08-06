"""VoxCPM2 引擎子包 - 共享基础工具模块。

本模块是 VoxCPM2 引擎子包 (engines/voxcpm2/) 的公共基础设施层，
集中管理跨生成模式复用的四类核心能力：

一、AdvancedParams 高级参数构建
    - get_advanced_params(): 获取默认高级参数字典
    - build_advanced_params(): 基于用户覆盖构建合法的参数配置对象
    - _advanced_kwargs(): 将配置对象转换为 model.generate() 所需 kwargs
    所有参数通过 Pydantic model_fields 白名单过滤，防止拼写错误或
    未定义字段传入导致下游验证失败。

二、段级质量检查 (RAS - Repetition Aware Sampling)
    - _check_segment_quality(): 对单段音频执行"时长 + RMS 能量 + 方差"
      三重检测，识别静音退化、重复模式、过短片段等异常输出。
    - 在多段生成流程中配合 RASContext 做段级重试。

三、公共生成模板 generate_with_template()
    封装 design/clone/ultimate/script/streaming/prompt 六种生成模式的
    通用流水线：文本分割 -> 逐段推理(含进度追踪+RAS重试) -> 音频合并 ->
    文件保存 -> 结果返回，避免各模式重复实现相同逻辑。

四、公共符号 re-export (通过 __all__)
    将 config/exceptions/generation/model_manager/persona_manager/
    ras_sampling/utils 等模块的常用符号统一再导出，供上层
    fn_voxcpm_* 函数以 `from ._base import *` 方式批量导入，
    减少各模块的重复 import 语句。

调用方依赖：
    - engines/voxcpm2/design.py      (语音设计生成)
    - engines/voxcpm2/clone.py       (语音克隆)
    - engines/voxcpm2/ultimate.py    (终极克隆模式)
    - engines/voxcpm2/script.py      (剧本工坊多角色)
    - engines/voxcpm2/streaming.py   (长文本流式生成)
    - engines/voxcpm2/prompt.py      (Prompt 续写模式)
"""

import logging
import os
import time
from collections.abc import Callable
from typing import Any

import numpy as np
from pydantic import ValidationError

from ...audio_processing import normalize_loudness, trim_tts_output
from ...bad_case_retry import (
    FailureType,
    RetryConfig,
    adjust_params_for_retry,
    detect_failure_type,
)
from ...config import SAVE_DIR
from ...config_models import AdvancedParamsConfig
from ...exceptions import EngineSwitchError, GenerationError, tts_error_handler
from ...generation import (
    _save_wav_compatible,
    increment_seed,
    merge_audio_segments,
    split_text_for_tts,
)
from ...model_manager import _gen_tracker, _progress_mgr
from ...persona_manager import get_persona_map
from ...ras_sampling import RASConfig, RASContext
from ...text_frontend import normalize_text
from ...utils import cleanup_temp_files

logger = logging.getLogger("tts_multimodel")

_DEFAULT_ADVANCED: AdvancedParamsConfig = AdvancedParamsConfig()

__all__ = [
    "SAVE_DIR",
    "EngineSwitchError",
    "GenerationError",
    "RASContext",
    "RASConfig",
    "_advanced_kwargs",
    "_gen_tracker",
    "_progress_mgr",
    "_save_wav_compatible",
    "build_advanced_params",
    "cleanup_temp_files",
    "generate_with_template",
    "get_advanced_params",
    "get_persona_map",
    "logger",
    "split_text_for_tts",
    "tts_error_handler",
]


def get_advanced_params() -> dict[str, Any]:
    """获取默认高级参数字典。

    将单例 `_DEFAULT_ADVANCED` (AdvancedParamsConfig) 序列化为
    普通 dict 返回，供前端展示默认值或与用户提交的覆盖值合并。

    Returns:
        dict[str, Any]: 默认高级参数字典，键为 Pydantic 模型字段名，
            值为对应默认值（如 max_len=3000, retry_badcase=True 等）。
    """
    return _DEFAULT_ADVANCED.to_dict()


def build_advanced_params(**overrides: Any) -> AdvancedParamsConfig:
    """基于用户覆盖参数构建合法的 AdvancedParamsConfig 对象。

    使用 AdvancedParamsConfig.model_fields.keys() 作为白名单过滤
    用户传入的 overrides：仅保留 Pydantic 模型已定义的字段，
    其余字段丢弃并记录 warning。这样即使高级 UI 新增了前端字段
    但后端模型尚未同步更新，也不会因 ValidationError 中断生成。
    若过滤后的参数验证失败（字段类型不匹配等），fail-soft 回退
    为默认配置，保证流程不中断。

    Args:
        **overrides: 用户覆盖的任意关键字参数。只有键名出现在
            AdvancedParamsConfig.model_fields 中的参数才会被采纳。

    Returns:
        AdvancedParamsConfig: 构建完成的配置对象。若验证失败则返回
            默认 _DEFAULT_ADVANCED 单例。
    """
    valid_keys = AdvancedParamsConfig.model_fields.keys()
    # Why valid_keys 严格过滤：
    # AdvancedParamsConfig 虽有 extra="ignore" 会静默忽略未定义字段，
    # 但用户常因拼写错误（如 cfg_value 写成 cfg_val）导致参数"默默不生效"，
    # 很难排查。显式过滤 + logger.warning 可以在日志中提前暴露此类问题。
    invalid_keys = [k for k in overrides if k not in valid_keys]
    if invalid_keys:
        logger.warning(
            f"build_advanced_params: 忽略未定义参数 {invalid_keys}，合法字段为 {sorted(valid_keys)}。请检查参数拼写。"
        )
    filtered = {k: v for k, v in overrides.items() if k in valid_keys}
    try:
        return AdvancedParamsConfig(**filtered)
    except ValidationError as e:
        logger.warning(f"build_advanced_params: 参数验证失败，回退为默认配置。错误详情: {e}")
        return _DEFAULT_ADVANCED


def _advanced_kwargs(advanced: AdvancedParamsConfig | None = None) -> dict[str, Any]:
    """将 AdvancedParamsConfig 对象转换为 model.generate() 所需参数字典。

    仅提取 VoxCPM generate 接口实际消费的字段（max_len / retry_badcase /
    retry_badcase_max_times / retry_badcase_ratio_threshold），避免把
    RAS 相关配置或其他不相关字段传入底层。

    Args:
        advanced: 可选的高级配置对象。为 None 时使用模块级默认
            `_DEFAULT_ADVANCED`。

    Returns:
        dict[str, Any]: 可直接作为 kwargs 传入 voxcpm_model.generate()
            的字典，仅包含 4 个键：max_len、retry_badcase、
            retry_badcase_max_times、retry_badcase_ratio_threshold。
    """
    if advanced is None:
        advanced = _DEFAULT_ADVANCED
    return dict(
        max_len=advanced.max_len,
        retry_badcase=advanced.retry_badcase,
        retry_badcase_max_times=advanced.retry_badcase_max_times,
        retry_badcase_ratio_threshold=advanced.retry_badcase_ratio_threshold,
    )


def _check_segment_quality(
    wav: np.ndarray,
    sample_rate: int,
    expected_min_duration: float = 0.5,
) -> tuple[bool, str]:
    """检查单个音频段的质量，检测退化/重复输出。

    借鉴 Fish Speech RAS 的核心思想：在生成过程中检测异常模式。
    这里适配为音频段级别——检测生成的音频是否退化为静音、
    过短或能量异常低的片段。

    失败模式（通过 reason 字段返回，函数本身不抛异常）：
      - "空音频"           : wav 为 None 或长度为 0
      - "音频过短 (Xs < Ys)" : 实际时长小于 expected_min_duration
      - "音频能量过低 (RMS=X)" : RMS < 1e-4，疑似静音退化
      - "音频方差过低 (var=X，可能为重复退化)" : 方差 < 1e-8，疑似单调重复
      - "质量检测异常: <ExceptionName>" : numpy 运算过程中捕获到异常

    Args:
        wav: 生成的音频 numpy 数组。
        sample_rate: 采样率。
        expected_min_duration: 最小期望时长（秒），低于此值视为退化。

    Returns:
        tuple[bool, str]: (is_valid, reason) 元组。
            is_valid=True 表示音频质量可接受；
            reason 为 "OK" 或具体失败描述字符串。
    """
    if wav is None or len(wav) == 0:
        return False, "空音频"

    try:
        duration = len(wav) / sample_rate
        if duration < expected_min_duration:
            return False, f"音频过短 ({duration:.2f}s < {expected_min_duration}s)"

        wav_f64 = wav.astype(np.float64)

        # 检查能量：如果均方根能量过低，可能是静音退化
        rms = float(np.sqrt(np.mean(wav_f64**2)))
        if rms < 1e-4:
            return False, f"音频能量过低 (RMS={rms:.6f})"

        # Why RMS + 方差 双重检测：
        # RMS 低只能说明"整体音量小"——有可能是尾音自然衰减的正常片段。
        # 但方差接近零意味着整段声波几乎是同一条水平线（单调直流信号）
        # 或高度重复的固定模式（典型的推理退化表现），这才是真正的异常。
        # 两个判据独立生效，可以区分"弱声正常段"与"重复退化段"。
        variance = float(np.var(wav_f64))
        if variance < 1e-8:
            return False, f"音频方差过低 (var={variance:.2e})，可能为重复退化"

        return True, "OK"
    except (TypeError, RuntimeError, ValueError) as e:
        return False, f"质量检测异常: {type(e).__name__}"


def _ras_retry_segment(
    ras_ctx: RASContext | None,
    idx: int,
    cur_len: int,
    audio_segments: list[np.ndarray],
) -> None:
    """RAS 段级重复检测辅助函数：用段长度差异作为简易重复信号喂入 RASContext。

    当相邻两段音频长度差小于 5% 时，视为潜在的重复模式，向 RASContext
    喂入段索引；否则喂入负索引表示不同长度。该启发式方法用于在缺乏
    token 级信息时近似模拟 Fish Speech 的 n-gram 重复检测。

    Args:
        ras_ctx: RAS 上下文对象，为 None 时直接返回不做处理。
        idx: 当前段索引（用于构造"伪 token"值）。
        cur_len: 当前生成段的样本点数。
        audio_segments: 已生成的所有段列表。用于取前一段长度对比。
    """
    if ras_ctx is None or len(audio_segments) < 2:
        return
    prev_len = len(audio_segments[-2])
    if prev_len > 0 and abs(cur_len - prev_len) / prev_len < 0.05:
        ras_ctx.feed(idx)
    else:
        ras_ctx.feed(-idx)


def _save_output(
    wav: np.ndarray,
    output_prefix: str,
    sample_rate: int,
) -> tuple[str, str]:
    """保存音频到 SAVE_DIR 并返回 (绝对路径, 文件名)（原子写入）。

    使用时间戳作为文件名后缀，保证多次生成之间不覆盖；
    音频格式通过 _save_wav_compatible 转为浏览器兼容的 int16 PCM。
    采用临时文件 + os.replace 原子写入策略，防止进程中断时产生损坏文件。

    Args:
        wav: 待保存的音频 numpy 数组（float32/float64 范围 [-1, 1]）。
        output_prefix: 文件名前缀（如 "voxcpm_clone"）。
        sample_rate: 采样率。

    Returns:
        tuple[str, str]: (out_path, filename)
            out_path 为保存文件的绝对路径，filename 仅含文件名部分。
    """
    from pathlib import Path as _Path

    timestamp = int(time.time())
    out_path = os.path.join(SAVE_DIR, f"{output_prefix}_{timestamp}.wav")
    # _save_wav_compatible 内部已实现原子写入（tmp + os.replace），直接传入最终路径
    _Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    _save_wav_compatible(wav, out_path, sample_rate)
    filename = os.path.basename(out_path)
    return out_path, filename


def generate_with_template(
    text: str,
    instruction: str,
    gen_kwargs_builder: Callable[[str, str | None, Any], dict[str, Any]],
    output_prefix: str,
    phase_name: str,
    sample_rate: int = 48000,
    ref_audio_path: str | None = None,
    prompt_cache: Any = None,
    start_time: float | None = None,
    message_builder: Callable[[float, int], str] | None = None,
    skip_progress_start: bool = False,
    ras_config: RASConfig | None = None,
) -> tuple[tuple[int, np.ndarray, str] | None, str]:
    """VoxCPM2 公共生成模板函数。

    封装了文本分割->逐段推理(含进度追踪+RAS段级重复检测)->音频合并->文件保存->返回结果的通用流程。

    RAS 集成流程：
        1. 初始化阶段：若 ras_config 为 None 且 AdvancedParamsConfig.enable_ras=True，
           自动创建默认 RASConfig(window_size=50, ngram_n=3, repetition_threshold=2)。
        2. 单段生成阶段：每段推理完立即调用 _check_segment_quality 做"时长+RMS+方差"三重检测。
           若检测到退化且未达 _RAS_MAX_RETRIES 上限，则按 step=0.5 递增 cfg_value
           （上限 4.0）并重试该段；达到上限仍不通过则记录 warning 后接受当前输出。
        3. 段间检测阶段：多段模式下，对相邻两段的长度差做 5% 阈值启发式判断，
           向 RASContext 喂入"伪 token"以累积段级重复模式。
        4. 收尾阶段：生成完成后若 ras_ctx.is_repetitive() 为真，记录重复等级日志。

    进度条更新频率：
        - 文本分割完成：update_phase("文本分割中...") -> start/advance 切换到 "VoxCPM2 推理中..."
        - 每段推理前：advance_segment(f"第 N/M 段推理中...")
        - 成功完成：complete()（通过 finally 保证必达）
        - 用户取消：complete() 在 finally 中执行，避免卡在中途进度

    坏案例重试策略：
        - 段内 RAS 质量检测失败：最多重试 _RAS_MAX_RETRIES 次（默认来自
          AdvancedParamsConfig.ras_max_retries，禁用 RAS 时退化为 2 次），
          每次递增 cfg_value 0.5（封顶 4.0）。
        - 重试耗尽：logger.warning 记录，接受当前退化段不中断整体生成。
        - 用户取消 (_progress_mgr.should_stop())：直接抛 GenerationError。

    Args:
        text: 待合成的输入文本。
        instruction: 指令前缀（如情感控制指令）。非空时会被包裹为 "(instruction)"
                     后拼接到每段 seg_text 前面。
        gen_kwargs_builder: 可调用对象，签名为 (seg_text_with_instruction, ref_audio_path, prompt_cache)
                           返回用于 model.generate() 的 kwargs 字典。
        output_prefix: 输出文件名前缀（如 "voxcpm_clone"）。
        phase_name: 日志前缀名称（如 "VoxCPM可控克隆"）。
        sample_rate: 音频采样率（默认 48000）。
        ref_audio_path: 参考音频文件路径。
        prompt_cache: 缓存的音色特征（来自 prompt_cache 模块）。
        start_time: 用于段循环中估算剩余时间的起始时间。为 None 时取 time.time()。
        message_builder: 可选的可调用对象，签名为 (duration_sec, total_segments)，
                        返回成功消息字符串。若为 None 则使用默认格式。
        skip_progress_start: 是否跳过 _progress_mgr.start() 调用。
                            当调用方已在外层启动进度条时设为 True，避免重复启动。
        ras_config: RAS 配置，None 时使用默认值。若为 None 且 AdvancedParamsConfig
                    中启用了 RAS，则自动创建默认配置。

    Returns:
        tuple[Optional[tuple[int, np.ndarray, str]], str]:
            ((sample_rate, wav_data, filename), message) 元组。
            第一元素为 None 仅在生成失败路径（已被异常取代，保留类型兼容）。
            message 为展示给用户的成功消息（含时长/分段信息）。

    Raises:
        GenerationError: 用户取消生成时抛出；或无有效音频段时抛出；
            或底层推理 / 保存流程中任何未捕获异常经 logger.exception 记录后，
            通过 `from e` 链式重抛为 GenerationError（保留完整异常链）。
        ~exception.InsufficientVRAMError: OOM 等显存不足异常直接向上传播，
            交由全局异常处理器（tts_error_handler / middleware/error_handler）
            做标准 JSON 响应，不在这里二次包装。
    """
    from ...model_registry import registry

    # Why 不直接整段生成而要 split_text_for_tts 分段：
    # ① VoxCPM 推理底层有固定 max_len（默认 3000 tokens），超长文本会被静默截断，
    #    导致后半段无声，分段可以保证每个 chunk 都在 max_len 范围内完整生成。
    # ② 长文本分段可做进度可视化：每段完成时 advance_segment，用户可以看到
    #    "第 N/M 段" 的进度反馈，而不是长时间黑屏等待。
    # ③ RAS 段级质量检测可以对异常段落进行精准重试：如果第 3 段退化，
    #    只需重试第 3 段而不用丢掉前两段已生成的正确结果，节省算力和时间。
    if start_time is None:
        start_time = time.time()

    _adv = _DEFAULT_ADVANCED
    if ras_config is None and _adv.enable_ras:
        ras_config = RASConfig(
            window_size=50,
            ngram_n=3,
            repetition_threshold=2,
        )
    use_ras = ras_config is not None
    ras_ctx: RASContext | None = RASContext(config=ras_config) if use_ras else None
    _RAS_MAX_RETRIES = _adv.ras_max_retries if use_ras else 2

    # Bad Case Retry 配置
    retry_config = RetryConfig(
        max_retries=_RAS_MAX_RETRIES,
        min_duration_sec=0.3,
        rms_threshold=1e-4,
        variance_threshold=1e-8,
        enable_seed_rotation=True,
    )

    # 文本预处理：清理 Markdown/Emoji + 标点规范化 + 数字展开
    # 参考 Fish Speech / VoiceBox 最佳实践，提升 TTS 输入质量
    try:
        text = normalize_text(text)
        if instruction and instruction.strip():
            instruction = normalize_text(instruction)
    except Exception as e:
        logger.debug(f"[{phase_name}] 文本预处理失败（使用原始文本）: {e}")

    _progress_mgr.update_phase("文本分割中...")
    segments = split_text_for_tts(text)
    total = len(segments)

    if skip_progress_start:
        _progress_mgr.update_phase("VoxCPM2 推理中...")
    else:
        _progress_mgr.start(total_segments=total, phase="VoxCPM2 推理中...")

    def _build_text(seg_text: str) -> str:
        """将指令前缀与分段文本拼接为模型接受的完整输入格式。

        当 instruction 非空时，用括号包裹指令后拼接到文本前面，
        格式为 "(instruction)seg_text"，这是 VoxCPM2 条件生成的标准 Prompt 格式。
        空指令时直接返回原分段文本。

        Args:
            seg_text: 当前待处理的分段文本（不含指令前缀）。

        Returns:
            str: 拼接后的完整输入文本。
        """
        if instruction and instruction.strip():
            return "(" + instruction.strip() + ")" + seg_text
        return seg_text

    def _generate_segment(
        seg_text: str,
        ref_path: str | None,
        prompt_cache_val: Any,
        retry_count: int = 0,
        segment_seed: int | None = None,
        current_failure_type: FailureType = FailureType.UNKNOWN,
    ) -> np.ndarray:
        """生成单段音频，带增强 Bad Case Retry 质量检测 + 多维度参数调整重试。

        增强的重试策略（参考 Fish Speech RAS 和 Chatterbox 容错）：
          - 第一次重试：针对失败类型调整 cfg/temp/top_p + 新 seed
          - 后续重试：渐进式参数调整 + 指数退避
          - 支持检测：静音、过短、过长、削波、重复、内部静音
          - 重试耗尽：优雅降级，接受当前输出

        Args:
            seg_text: 分段文本（不含 instruction，由 _build_text 内部拼接）。
            ref_path: 参考音频路径，克隆模式使用。
            prompt_cache_val: 预计算的音色 prompt 缓存。
            retry_count: 当前重试次数，递归自增。
            segment_seed: 该段使用的随机种子（None 为随机，支持 per-chunk seed）。
            current_failure_type: 上一次检测到的失败类型（用于针对性参数调整）。

        Returns:
            np.ndarray: 生成的音频波形数组。
        """
        built_text = _build_text(seg_text)
        kwargs = gen_kwargs_builder(built_text, ref_path, prompt_cache_val)

        # 应用 per-chunk seed（如果提供）：无论 kwargs 原本是否有 seed 键都设置
        if segment_seed is not None:
            kwargs["seed"] = segment_seed

        # 非首次重试时，根据失败类型调整参数
        if retry_count > 0:
            kwargs = adjust_params_for_retry(
                kwargs,
                current_failure_type,
                retry_count,
                retry_config,
            )

        wav = registry.voxcpm_model.generate(**kwargs)

        # 对单段音频应用 TTS 输出裁切（去除首尾静音和爆音）
        try:
            wav = trim_tts_output(wav, sample_rate, detect_internal_hallucination=False)
        except Exception as e:
            logger.debug(f"[{phase_name}] 单段 trim_tts_output 失败（可忽略）: {e}")

        if use_ras and ras_ctx is not None:
            # 使用增强的 Bad Case 检测（替代简单的 _check_segment_quality）
            has_failure, failure_type, reason = detect_failure_type(
                wav,
                sample_rate,
                config=retry_config,
            )
            if not has_failure:
                # 兼容原有的简单质量检查（双重保险）
                is_valid, simple_reason = _check_segment_quality(wav, sample_rate)
                if not is_valid:
                    has_failure = True
                    failure_type = FailureType.UNKNOWN
                    reason = simple_reason

            if has_failure and retry_count < _RAS_MAX_RETRIES:
                original_cfg = kwargs.get("cfg_value", 2.0)
                new_cfg = kwargs.get("cfg_value", original_cfg)
                logger.warning(
                    f"[{phase_name}] BadCase 检测: {reason}，"
                    f"cfg_value {original_cfg:.1f} -> {new_cfg:.1f}，"
                    f"重试 {retry_count + 1}/{_RAS_MAX_RETRIES}"
                )
                return _generate_segment(
                    seg_text,
                    ref_path,
                    prompt_cache_val,
                    retry_count + 1,
                    segment_seed,
                    failure_type,
                )
            elif has_failure:
                logger.warning(f"[{phase_name}] BadCase: 重试耗尽，接受退化输出: {reason}")

        return wav

    try:
        # per-chunk seed 状态：首次生成时探测基础 seed，后续段自动递增
        _base_seed: int | None = None
        _seed_probed: bool = False

        audio_segments: list[np.ndarray] = []
        for idx, seg in enumerate(segments):
            if _progress_mgr.should_stop():
                logger.info(f"[{phase_name}] 生成已被用户取消")
                raise GenerationError("生成已取消")
            seg = seg.strip()
            if not seg:
                continue

            if total == 1:
                _progress_mgr.advance_segment("推理生成中...")
            else:
                _progress_mgr.advance_segment(f"第 {idx + 1}/{total} 段推理中...")

            elapsed = time.time() - start_time
            if idx > 0:
                avg = elapsed / idx
                remaining = avg * (total - idx)
                logger.info(f"[{phase_name}] 第 {idx + 1}/{total} 段，已耗时 {elapsed:.1f}s，预计剩余 {remaining:.1f}s")
            else:
                mode_str = "reference_wav" if ref_audio_path else "默认音色"
                logger.info(f"[{phase_name}] 第 1/{total} 段，使用 {mode_str} 模式...")

            # per-chunk seed：首次生成时探测 kwargs 中的 seed，后续段递增
            chunk_seed: int | None = None
            if _seed_probed and _base_seed is not None:
                chunk_seed = increment_seed(_base_seed, idx)

            # 包装 _generate_segment 以在首次调用时探测 seed
            if not _seed_probed:
                built_text = _build_text(seg)
                _probe_kwargs = gen_kwargs_builder(built_text, ref_audio_path, prompt_cache)
                if "seed" in _probe_kwargs and _probe_kwargs["seed"] is not None:
                    _base_seed = _probe_kwargs["seed"]
                    chunk_seed = _base_seed  # 第一段使用原始 seed
                _seed_probed = True

            wav = _generate_segment(seg, ref_audio_path, prompt_cache, segment_seed=chunk_seed)
            audio_segments.append(wav)

            _ras_retry_segment(ras_ctx, idx, len(wav), audio_segments)

        if not audio_segments:
            raise GenerationError(f"VoxCPM2 {phase_name}生成失败：无有效音频段")

        if len(audio_segments) == 1:
            # 单段模式：应用完整后处理（与多段一致）
            merged = audio_segments[0]
            merged_sr = sample_rate
            try:
                merged = trim_tts_output(
                    merged,
                    merged_sr,
                    detect_internal_hallucination=True,
                    max_internal_silence_ms=1000,
                    fade_ms=30,
                )
            except Exception as e:
                logger.debug(f"[{phase_name}] 单段 trim_tts_output 失败（可忽略）: {e}")
            try:
                merged = normalize_loudness(merged, merged_sr, target_lufs=-16.0, method="auto")
            except Exception as e:
                logger.debug(f"[{phase_name}] 单段响度归一化失败（可忽略）: {e}")
        else:
            # 使用 crossfade 合并音频段（参考 VoiceBox 的 raised cosine 交叉淡入淡出）
            # 避免段间产生 click 噪声
            merged, merged_sr = merge_audio_segments(
                audio_segments,
                sr=sample_rate,
                crossfade_duration=0.05,  # 50ms crossfade
                silence_duration=0.15,  # crossfade 失败时回退到 150ms 静音
            )
            if merged is None:
                merged = np.concatenate(audio_segments)
                merged_sr = sample_rate

            # 对最终合并音频应用完整后处理：内部长静音幻觉检测 + 响度归一化
            try:
                merged = trim_tts_output(
                    merged,
                    merged_sr,
                    detect_internal_hallucination=True,
                    max_internal_silence_ms=1000,
                    fade_ms=30,
                )
            except Exception as e:
                logger.debug(f"[{phase_name}] 最终 trim_tts_output 失败（可忽略）: {e}")

            try:
                merged = normalize_loudness(merged, merged_sr, target_lufs=-16.0, method="auto")
            except Exception as e:
                logger.debug(f"[{phase_name}] 响度归一化失败（可忽略）: {e}")

        out_path, filename = _save_output(merged, output_prefix, merged_sr)

        duration_sec = len(merged) / merged_sr
        logger.info(f"[{phase_name}] 音频已保存: {out_path}，时长 {duration_sec:.1f}s，分段: {total}")
        if use_ras and ras_ctx is not None and ras_ctx.is_repetitive():
            logger.info(f"[{phase_name}] RAS 统计: 检测到 {ras_ctx.get_repetition_level()} 级段间重复")
        msg = (
            message_builder(duration_sec, total)
            if message_builder
            else f"生成成功！音频时长 {duration_sec:.1f} 秒，分段: {total}。"
        )
        return (merged_sr, merged, filename), msg
    except GenerationError:
        raise
    except Exception as e:
        logger.exception(f"[{phase_name}] 生成流程异常")
        raise GenerationError(f"{phase_name}生成失败: {e}") from e
    finally:
        _progress_mgr.complete()
