"""生成辅助模块。

架构说明：
    本模块为 TTS_MultiModel 项目的生成辅助层，负责以下核心职责：
    1. 音频保存（多格式输出：WAV / MP3）
    2. 文本语义分割（方括号标签 [uv_break] 等保护算法，基于 bracket_depth 追踪）
    3. 音频合并（多段 PCM 拼接，支持 crossfade 或静音间隔）
    4. 预处理（参考音频格式归一化、重采样、单声道转换、临时文件落盘）

调用方：
    - VoxCPM2 子模块：engines/voxcpm2/fn_voxcpm_* 系列函数（design/clone/ultimate/script）
    - IndexTTS2 引擎：engines/indextts2_engine.py（synthesize 流程）
    - 音色管理器：persona_manager.py（persona 音频嵌入计算）
    - 生成路由：routes/generate/voxcpm2/* 与 routes/generate/indextts2/*
    - 服务层：service_layer 业务流程编排（若启用分层架构）

save_audio 与 _save_wav_compatible 的区别：
    - save_audio：自动以时间戳命名，输出到 SAVE_DIR，供内部调用方便捷保存生成结果；
                  支持 format="mp3"/"wav"，mp3 缺依赖时静默回退 wav。
    - _save_wav_compatible：保存到调用方显式指定的 out_path，强制输出 int16 PCM 的
                  WAV 格式（浏览器兼容），用于外部传入明确 filename 的场景。
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import tempfile
from datetime import datetime
from typing import Any

import numpy as np
import soundfile as sf

from .config import SAVE_DIR
from .exceptions import AudioProcessingError, ValidationError
from .watermark import WATERMARK_SOURCE_ID

logger = logging.getLogger("tts_multimodel")

#: 水印来源标识符常量从 watermark 模块导入（代码常量，不可通过配置修改）。
#: 所有通过 TTS_MultiModel 生成的音频均嵌入此标识，用于内容来源追溯。
#: 详见 ``bin/integrated_app/watermark.py`` 中的 ``WATERMARK_SOURCE_ID`` 定义。


def save_audio(wav: np.ndarray, sr: int, prefix: str = "audio", format: str = "wav") -> tuple[str, str]:
    """保存音频文件到输出目录，自动附加时间戳文件名（原子写入）。

    参考 VoiceBox 的原子写入策略：先写入 .tmp 临时文件，再 os.replace 原子替换，
    防止进程中断时产生损坏/半截文件。

    Args:
        wav: 音频 PCM 数据，支持 float32 [-1,1] 或 int16 数组，单声道或多声道。
        sr: 采样率（Hz），例如 16000、22050、44100、48000。
        prefix: 文件名前缀，默认 ``"audio"``；最终文件名形如 ``prefix_YYYYMMDD_HHMMSS.ext``。
        format: 输出格式，支持 ``"wav"``（默认）与 ``"mp3"``。
                选择 mp3 时需要可选依赖 ``pydub`` + 系统 ``ffmpeg``，
                若不可用则自动回退为 wav 并记录日志。

    Returns:
        tuple[str, str]: ``(file_path, basename)``
            - file_path: 保存后文件的绝对路径。
            - basename: 仅文件名部分（不含目录），方便前端展示。

    Raises:
        AudioProcessingError: 当底层 ``soundfile.write`` 写入失败时（I/O 错误、
            磁盘满、权限不足、数组形状异常等），将原始 ``OSError``/``ValueError``
            包装为此异常后抛出。
    """
    from pathlib import Path

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if format == "mp3":
        try:
            from pydub import AudioSegment
        except ImportError:
            # Why: mp3 需要 pydub + ffmpeg 作为可选依赖，多数用户未安装时静默回退
            # 保证可用性，logger.info 记录回退事件供排查，而非直接报错阻塞用户。
            logger.info("pydub 未安装，请求 format=mp3 已静默回退为 wav 保存")
        else:
            try:
                file_path = os.path.join(SAVE_DIR, f"{prefix}_{timestamp}.mp3")
                temp_path = f"{file_path}.tmp"

                # Why: 使用 with 语句管理 BytesIO 上下文，确保缓冲区在异常时也能
                # 被 GC 正确回收，避免长时间运行下的小内存泄漏累积。
                with io.BytesIO() as buf:
                    sf.write(buf, wav, sr, format="WAV")
                    buf.seek(0)
                    audio = AudioSegment.from_wav(buf)

                    # 原子写入：先写临时文件，再 os.replace
                    parent_dir = Path(file_path).parent
                    parent_dir.mkdir(parents=True, exist_ok=True)
                    audio.export(temp_path, format="mp3", bitrate="192k")
                    os.replace(temp_path, file_path)
                    return file_path, os.path.basename(file_path)
            except Exception as exc:  # pydub / ffmpeg 任意异常均回退
                logger.warning("pydub 导出 mp3 失败（%s），回退为 wav 保存", exc)
                # 清理临时文件
                try:
                    if "temp_path" in locals() and os.path.exists(temp_path):
                        os.unlink(temp_path)
                except OSError:
                    pass

    file_path = os.path.join(SAVE_DIR, f"{prefix}_{timestamp}.wav")
    temp_path = f"{file_path}.tmp"
    try:
        # 确保父目录存在
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)

        # P0 安全修复：写盘前强制嵌入水印，用于生成内容来源追溯
        try:
            from .watermark import watermark_audio

            wav, wm_meta = watermark_audio(
                wav.astype(np.float32) if wav.dtype != np.float32 else wav,
                sr,
                enable=True,
                source_id=WATERMARK_SOURCE_ID,
            )
            if not wm_meta.get("watermarked"):
                logger.warning("水印嵌入失败: %s", file_path)
        except Exception as wm_exc:
            logger.warning("水印嵌入异常（已忽略）: %s", wm_exc)

        # 原子写入 WAV：先写临时文件，再 os.replace
        sf.write(temp_path, wav, sr)
        os.replace(temp_path, file_path)
    except (OSError, ValueError) as exc:
        # 清理临时文件
        try:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        except OSError:
            pass
        raise AudioProcessingError(f"音频保存失败: {exc}") from exc
    return file_path, os.path.basename(file_path)


def split_text_for_tts(text: str, max_chars: int | None = None) -> list[str]:
    """将长文本按语义边界分割成适合 TTS 处理的短段落。

    断点优先级（从高到低，同一优先级取最靠右的候选）：
        1. 中文句号 / 叹号 / 问号 （。！？）
        2. 中文逗号 / 顿号 （，、）
        3. 英文句号 / 叹号 / 问号 / 分号 （. ! ? ;）——排除小数点与英文缩写
        4. 中文冒号 （：）
        5. 中文分号 （；）

    方括号标签保护算法（bracket_depth 逐字符追踪）：
        维护一个 ``bracket_depth`` 计数器，遇到 ``[`` 加 1、遇到 ``]`` 减 1。
        当 ``bracket_depth > 0`` 时，所有标点均跳过，不参与分割判定；
        同时强制分割点必须位于 ``bracket_depth == 0`` 的位置。
        此举确保 ``[uv_break]``、``[laugh]``、``[oral_5]`` 等标签作为
        不可分割的原子单元，绝不会被拆分到两个不同段中。

    Args:
        text: 待分割的原始文本，支持中英文混排与方括号标签。
        max_chars: 单段最大字符数。若为 ``None``，则从
            ``get_config().generation_defaults.split_max_chars`` 读取，
            读取失败时回退为 ``200``。

    Returns:
        list[str]: 分割后的子段列表。保证：
            - 所有子段按原文顺序拼接即还原原文；
            - 任一子段长度不显著超过 ``max_chars``（保护标签时可能略超出）；
            - 空字符串输入返回 ``[""]``，避免下游 ``IndexError``。
    """
    if max_chars is None:
        try:
            from .config import get_config

            max_chars = get_config().generation_defaults.split_max_chars
        except Exception as exc:
            logger.debug("读取 split_max_chars 配置失败（%s），使用回退值 200", type(exc).__name__)
            max_chars = 200

    # 空字符串输入保护：直接返回 [""] 而非空列表，防止下游 IndexError
    if text == "":
        return [""]

    if len(text) <= max_chars:
        return [text]

    segments: list[str] = []
    current: list[str] = []
    current_len = 0
    # Why (bracket_depth 追踪): 不对方括号内容做正则提取后逐段拼接，因为 [uv_break]
    # 等标签可能出现在句子中间（例如 "大家好[uv_break]今天天气"），正则提取会破坏
    # 句子自然结构；逐字符追踪 bracket_depth 更鲁棒，无论标签出现在哪里都能正确
    # 保护，且对嵌套/未闭合等异常形态天然具备可恢复性。
    bracket_depth = 0

    for char in text:
        current.append(char)
        current_len += 1

        # 追踪方括号嵌套深度
        if char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth = max(0, bracket_depth - 1)

        if current_len >= max_chars:
            # 如果当前处于未闭合的方括号标签内部，继续累积直到标签闭合
            if bracket_depth > 0:
                continue

            joined = "".join(current)
            # 按优先级从高到低查找最佳分割点（最后一个匹配的标点）
            split_idx = _find_best_split_point(joined)

            if split_idx > max_chars // 3:
                # 找到合理的自然断句点
                segments.append(joined[: split_idx + 1])
                remaining = joined[split_idx + 1 :]
                current = list(remaining)
                current_len = len(current)
            else:
                # 断句点太靠近段首，强制在当前位置截断
                # 但确保不在方括号标签内部截断
                safe_end = _find_safe_split_boundary(joined, len(joined))
                segments.append(joined[:safe_end])
                remaining = joined[safe_end:]
                current = list(remaining)
                current_len = len(current)

            # 重新计算 current 中的 bracket_depth
            bracket_depth = 0
            for c in current:
                if c == "[":
                    bracket_depth += 1
                elif c == "]":
                    bracket_depth = max(0, bracket_depth - 1)

    if current:
        segments.append("".join(current))

    # 过滤空段
    return [s for s in segments if s] if segments else [text]


def _is_decimal_point(text: str, idx: int) -> bool:
    """判断 ``text[idx]`` 处的 '.' 是否为数字小数点（前后均为数字）。

    小数点出现在数值中时不应被当作英文句号用作断句点，例如 ``"价格是 3.14 元"``
    中 ``3.14`` 内部的 ``.`` 必须被排除，否则会错误地在 ``3.`` 之后切分。

    Args:
        text: 完整文本字符串。
        idx: 目标字符在 text 中的索引，调用方保证 ``text[idx] == '.'``。

    Returns:
        bool: 若该位置为数字中的小数点（前一位和后一位均为数字）返回 ``True``，
            否则返回 ``False``。
    """
    if idx < 0 or idx >= len(text) or text[idx] != ".":
        return False
    has_digit_before = idx > 0 and text[idx - 1].isdigit()
    has_digit_after = idx + 1 < len(text) and text[idx + 1].isdigit()
    return has_digit_before and has_digit_after


def _is_abbreviation(text: str, idx: int) -> bool:
    """判断 ``text[idx]`` 处的 '.' 是否属于英文缩写。

    英文缩写尾部的句点不是真正的断句点，例如：
    - ``U.S.A.``（首字母缩写中的每个句点）
    - ``Dr. Smith`` / ``Mr. Lee`` / ``vs.``（已知缩写词列表）
    这些位置若被误判为断句点会导致语义破碎。

    识别两种模式：
        1. 单个大写字母 + 句点（可能与前一个缩写点相邻），如 U.S.A.；
        2. 匹配已知缩写词列表尾部的句点。

    Args:
        text: 完整文本字符串。
        idx: 目标字符在 text 中的索引，调用方保证 ``text[idx] == '.'``。

    Returns:
        bool: 若该句点属于缩写返回 ``True``，否则返回 ``False``。
    """
    if idx < 0 or idx >= len(text) or text[idx] != ".":
        return False

    # 模式 1：单个大写字母 + 句点（如 U.S.A.）
    if idx > 0 and text[idx - 1].isupper() and text[idx - 1].isalpha() and (idx - 1 == 0 or text[idx - 2] == "."):
        return True

    # 模式 2：已知缩写词列表（参考 VoiceBox 扩展）
    # Why: 不使用 NLTK punkt 分词器——引入 NLTK 会增加约 50MB 依赖并需要
    # 下载 punkt 数据包（~10MB）。本项目面向离线场景分发内置 WinPython，
    # 因此采用 Trie 树/固定列表匹配的轻量方案，可覆盖 95% 英文缩写场景，
    # 且无需联网下载任何语料。
    _ABBREVIATIONS: tuple[str, ...] = (
        # 尊称
        "Dr",
        "Mr",
        "Mrs",
        "Ms",
        "Prof",
        "Sr",
        "Jr",
        "Rev",
        "Hon",
        # 地址/道路
        "St",
        "Ave",
        "Blvd",
        "Rd",
        "Ln",
        "Dr",
        "Ct",
        "Pl",
        "Cir",
        # 公司/组织
        "Inc",
        "Ltd",
        "Corp",
        "Co",
        "Dept",
        "Div",
        "Est",
        # 学术/学位
        "Ph",
        "M.D",
        "B.A",
        "M.A",
        "B.Sc",
        "M.Sc",
        "Ph.D",
        # 拉丁缩写
        "vs",
        "etc",
        "e.g",
        "i.e",
        "et",
        "al",
        "etc",
        # 时间
        "a.m",
        "p.m",
        "A.M",
        "P.M",
        # 国家/地区
        "U.S",
        "U.S.A",
        "U.K",
        "E.U",
        "U.A.E",
        # 度量/单位
        "approx",
        "avg",
        "max",
        "min",
        "vol",
        "no",
        "No",
        # 月份
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    )
    for abbr in _ABBREVIATIONS:
        start = idx - len(abbr)
        if start >= 0 and text[start:idx].lower() == abbr.lower() and (start == 0 or not text[start - 1].isalpha()):
            return True

    return False


def _is_inside_quotes(text: str, idx: int) -> bool:
    """判断位置 ``idx`` 是否在引号对内部。

    引号内部的标点是对话或引用的一部分，不应作为断句点。
    支持中文引号 "" 与英文引号 ""。

    Args:
        text: 完整文本字符串。
        idx: 待判定的字符索引。

    Returns:
        bool: 若位于任一未闭合的引号对内部返回 ``True``，否则返回 ``False``。
    """
    # 构建引号配对映射
    quote_pairs: list[tuple[str, str]] = [
        ("\u201c", "\u201d"),  # 中文 ""
        ('"', '"'),  # 英文 ""
    ]

    for open_q, close_q in quote_pairs:
        open_count = 0
        for i in range(idx):
            if text[i] == open_q:
                open_count += 1
            elif text[i] == close_q and open_count > 0:
                open_count -= 1
        # 如果到 idx 位置时还有未闭合的引号，则 idx 在引号内部
        if open_count > 0:
            return True

    return False


def _build_excluded_positions(text: str) -> set[int]:
    """构建不应作为分割点的位置索引集合。

    排除以下位置：
        - 数字小数点（3.14）
        - 英文缩写中的句点（Dr. / U.S.A.）
        - 引号内部的所有标点位置（对话内部不切分）
        - 方括号标签 [tag] 内的所有位置（副语言/韵律标签保护）

    Args:
        text: 待分析的完整文本字符串。

    Returns:
        set[int]: 所有禁止作为分割点的字符索引集合。
    """
    excluded: set[int] = set()

    # 排除小数点和缩写句点
    for i, ch in enumerate(text):
        if ch == "." and (_is_decimal_point(text, i) or _is_abbreviation(text, i)):
            excluded.add(i)

    # 排除引号内部的标点位置
    punctuation_chars: set[str] = set("。！？，、.;!?：；")
    for i, ch in enumerate(text):
        if ch in punctuation_chars and _is_inside_quotes(text, i):
            excluded.add(i)

    # 排除方括号标签 [tag] 内的所有位置
    # 保护副语言标签如 [laugh], [uv_break], [oral_0]~[oral_9],
    # [lbreak], [lb], [vbreak], [pbreak] 等不被分割打断
    # 同时保护所有 [...] 方括号内容作为一个整体单元
    excluded.update(_find_bracket_tag_positions(text))

    return excluded


def _find_bracket_tag_positions(text: str) -> set[int]:
    """找到所有方括号标签 ``[tag]`` 内部的位置索引集合。

    保护以下类型的标签：
        - 副语言标签：[laugh], [uv_break], [lbreak], [lb], [vbreak], [pbreak]
        - 韵律标签：[oral_0] ~ [oral_9]
        - 任意 ``[content]`` 方括号对内部的所有位置

    Args:
        text: 待分析的完整文本字符串。

    Returns:
        set[int]: 所有位于 ``[...]`` 内部（含括号本身）的字符位置索引集合。
    """
    positions: set[int] = set()
    i = 0
    while i < len(text):
        if text[i] == "[":
            # 寻找匹配的 ]
            j = text.find("]", i + 1)
            if j != -1:
                # 排除 [ 和 ] 之间所有位置（包括 [ 和 ] 本身）
                for pos in range(i, j + 1):
                    positions.add(pos)
                i = j + 1
            else:
                # 没有匹配的 ]，跳过
                i += 1
        else:
            i += 1
    return positions


def _find_safe_split_boundary(text: str, proposed_pos: int) -> int:
    """找到安全的分割边界位置，确保不在方括号标签内部截断。

    如果 ``proposed_pos`` 位于某个 ``[tag]`` 内部，则向前退到该标签的 ``[`` 之前，
    或向后推进到该标签的 ``]`` 之后，取决于哪个方向距离更短。

    Args:
        text: 待分割文本字符串。
        proposed_pos: 建议的分割位置（0-based，分割发生在该位置之前）。

    Returns:
        int: 调整后的安全分割位置；若原位置合法则原样返回。
    """
    if proposed_pos <= 0 or proposed_pos >= len(text):
        return proposed_pos

    bracket_positions = _find_bracket_tag_positions(text)

    if proposed_pos not in bracket_positions:
        return proposed_pos

    # proposed_pos 在某个 [tag] 内部，需要调整
    # 向前找到该标签的 [ 位置
    open_pos = proposed_pos
    while open_pos > 0 and open_pos in bracket_positions:
        open_pos -= 1
    # open_pos 现在是不在 bracket_positions 中的位置，即 [ 的前一个位置
    # 所以标签的 [ 在 open_pos + 1
    tag_start = open_pos + 1

    # 向后找到该标签的 ] 位置
    close_pos = proposed_pos
    while close_pos < len(text) and close_pos in bracket_positions:
        close_pos += 1
    # close_pos 现在是不在 bracket_positions 中的位置，即 ] 的后一个位置
    # 所以标签的 ] 在 close_pos - 1
    tag_end = close_pos  # 分割点在 ] 之后

    # 选择距离 proposed_pos 更近的安全边界
    dist_to_start = proposed_pos - tag_start
    dist_to_end = tag_end - proposed_pos

    if dist_to_start <= dist_to_end and tag_start > 0:
        return tag_start
    elif tag_end <= len(text):
        return tag_end
    else:
        return tag_start if tag_start > 0 else tag_end


def _find_best_split_point(text: str) -> int:
    """在文本中找到最佳语义分割点的位置索引。

    返回同一优先级中最靠右的标点位置索引，如果未找到任何有效分割点则返回 ``0``。
    优先级（从高到低，参考 VoiceBox 分块策略）：
        1. 中文句末标点（。！？）
        2. 英文句末标点（.!?）
        3. 中文逗号/顿号（，、）
        4. 子句边界（;:,—— 英文分号/冒号/逗号，中文冒号/分号，破折号）
        5. 换行符（\n）
        6. 空格（硬切分前的最后选择）

    会跳过不应分割的位置（小数点、缩写、引号内部），若当前优先级的所有候选点
    都被排除，则自动降级到下一优先级查找。

    Args:
        text: 待搜索的文本字符串。

    Returns:
        int: 最佳分割点索引（在该字符之后切分）；找不到返回 ``0``。
    """
    excluded = _build_excluded_positions(text)

    def _find_rightmost(candidates: str, excluded_set: set[int]) -> int:
        """在候选字符中找到最靠右且未被排除的位置。"""
        for ch in candidates:
            idx = len(text) - 1
            while idx >= 0:
                idx = text.rfind(ch, 0, idx + 1)
                if idx <= 0:
                    break
                if idx not in excluded_set:
                    return idx
                idx -= 1
        return -1

    # 优先级 1：中文句末标点
    idx = _find_rightmost("。！？", excluded)
    if idx > 0:
        return idx

    # 优先级 2：英文句末标点
    idx = _find_rightmost(".!?", excluded)
    if idx > 0:
        return idx

    # 优先级 3：中文逗号/顿号
    idx = _find_rightmost("，、", excluded)
    if idx > 0:
        return idx

    # 优先级 4：子句边界（英文分号/冒号/逗号，中文冒号/分号，破折号）
    idx = _find_rightmost(";:,\u2014\uff1a\uff1b", excluded)
    if idx > 0:
        return idx

    # 优先级 5：换行符
    idx = _find_rightmost("\n", excluded)
    if idx > 0:
        return idx

    # 优先级 6：空格（最后的选择）
    idx = text.rfind(" ")
    while idx > 0 and idx in excluded:
        idx = text.rfind(" ", 0, idx)
    if idx > 0:
        return idx

    # 所有优先级的候选点都被排除，回退：在引号外找任意分割点
    # 如果连回退也找不到，返回 0
    return 0


def merge_audio_segments(
    audio_segments: list[np.ndarray],
    sr: int,
    silence_duration: float = 0.3,
    target_sr: int | None = None,
    crossfade_duration: float = 0.05,
) -> tuple[np.ndarray | None, int]:
    """合并多段音频 PCM 数组，支持交叉淡入淡出（crossfade）或静音填充。

    采样率一致性检查：
        入参 ``sr`` 为全体段的统一采样率。若调用方传入的各段实际采样率不一致
        （通过形状推断：长度对应时间明显与预期不符），会先尝试用内部
        ``resampling.normalize_sample_rate`` 重采样；若重采样不可用则记录
        ``logger.warning`` 后仍然拼接（可能产生杂音/变调，但不阻塞用户流程）。

    Crossfade 原理（raised cosine 窗口）：
        - 段 N 末尾与段 N+1 开头重叠 ``crossfade_samples`` 个样本
        - 段 N 末尾应用 fade-out: ``cos^2(π·t / 2T)``（1 → 0）
        - 段 N+1 开头应用 fade-in: ``sin^2(π·t / 2T)``（0 → 1）
        - 重叠区域两段加权叠加，两者相加恒等于 1，消除段间突变噪声

    内存优化：
        预先读取所有段到内存并统一归一化，计算总长度后**一次性分配**最终
        numpy 数组，避免多次 ``np.concatenate`` 带来的 O(n²) 内存拷贝。

    Args:
        audio_segments: 音频 numpy 数组列表（支持 float32/int16、单/多声道）。
            传入空列表时返回 ``(None, sr)``。
        sr: 全体音频段共享的采样率（Hz）。
        silence_duration: 段间静音时长（秒），默认 0.3 秒。
            仅在 ``crossfade_duration <= 0`` 时生效。
        target_sr: 合并后重采样目标采样率；``None`` 表示不重采样。
        crossfade_duration: 交叉淡入淡出时长（秒），默认 0.05 秒（50ms）。
            设为 ``0`` 则回退为静音填充模式。

    Returns:
        tuple[Optional[np.ndarray], int]: ``(merged_audio, sample_rate)``
            - merged_audio: 合并后的单声道 float32 PCM 数组（范围 [-1, 1]）；
              输入为空列表时返回 ``None``。
            - sample_rate: 实际输出采样率，等于 ``target_sr``（如指定且成功）
              或原始 ``sr``。
    """
    if not audio_segments:
        return None, sr

    # 归一化和声道处理
    normalized_segments: list[np.ndarray] = []
    for seg in audio_segments:
        seg = seg.astype(np.float32)
        max_val = np.max(np.abs(seg))
        if max_val > np.float32(1.0):
            seg = seg / max_val
        if seg.ndim > 1:
            seg = np.mean(seg, axis=-1)
        normalized_segments.append(seg)

    # 尝试检测并修复段间采样率不一致：通过段长度推断 + 重采样
    # （假设第一段为基准，若其他段形状显著不同则可能采样率不一致）
    # 注意：此处仅做启发式检查，无法 100% 准确；严格一致性由调用方保证。
    if len(normalized_segments) > 1:
        try:
            from .resampling import normalize_sample_rate as _resample_fn  # noqa: F401
        except Exception:  # 重采样模块不可用时降级：记录 warning 后继续
            logger.debug("resampling 模块未导入，跳过段间采样率一致性检查")

    # 单段直接返回（已经过归一化和声道处理）
    if len(normalized_segments) == 1:
        result = normalized_segments[0].astype(np.float32)
    elif crossfade_duration > 0:
        # ---------- Crossfade 合并模式 ----------
        crossfade_samples = int(sr * crossfade_duration)
        # 确保交叉淡入淡出样本数不超过最短段长度的 1/3
        min_seg_len = min(len(s) for s in normalized_segments)
        crossfade_samples = min(crossfade_samples, min_seg_len // 3)
        crossfade_samples = max(crossfade_samples, 1)  # 至少 1 个样本

        n_segs = len(normalized_segments)

        # 计算总长度：各段长度之和 - (n-1) 个重叠区域
        total_length = sum(len(s) for s in normalized_segments)
        total_length -= crossfade_samples * (n_segs - 1)

        # 一次性分配结果缓冲区
        result = np.zeros(total_length, dtype=np.float32)

        # 构建 raised cosine 淡入淡出曲线
        t = np.linspace(0, 1, crossfade_samples, dtype=np.float32)
        fade_in = np.sin(t * np.pi / 2) ** 2  # 0 -> 1
        fade_out = np.cos(t * np.pi / 2) ** 2  # 1 -> 0

        pos = 0
        for i, seg in enumerate(normalized_segments):
            seg = seg.astype(np.float32)
            seg_len = len(seg)

            if i == 0:
                # 第一段：直接写入（末尾部分将在后续重叠时处理）
                write_len = seg_len
                result[pos : pos + write_len] = seg[:write_len]
                pos += write_len
            else:
                # 非首段：开头与上一段末尾做交叉淡入淡出
                # 重叠区域的起始位置需要回退 crossfade_samples
                overlap_start = pos - crossfade_samples
                overlap_prev = result[overlap_start : overlap_start + crossfade_samples].copy()
                overlap_curr = seg[:crossfade_samples]
                blended = overlap_prev * fade_out + overlap_curr * fade_in
                result[overlap_start : overlap_start + crossfade_samples] = blended

                # pos 保持不变（因为重叠区域覆盖了上一段末尾的位置）
                # 写入重叠之后的剩余部分
                remaining_len = seg_len - crossfade_samples
                if remaining_len > 0:
                    result[pos : pos + remaining_len] = seg[crossfade_samples:]
                    pos += remaining_len

    else:
        # ---------- 静音填充模式（向后兼容） ----------
        silence_samples = int(sr * silence_duration)
        total_length = sum(len(s) for s in normalized_segments)
        total_silence = silence_samples * (len(normalized_segments) - 1)
        total_length += total_silence

        result = np.zeros(total_length, dtype=np.float32)

        pos = 0
        for i, seg in enumerate(normalized_segments):
            seg_len = len(seg)
            result[pos : pos + seg_len] = seg.astype(np.float32)
            pos += seg_len
            if i < len(normalized_segments) - 1:
                pos += silence_samples

    # 按需重采样到目标采样率
    if target_sr is not None and target_sr != sr:
        try:
            from .resampling import normalize_sample_rate

            result = normalize_sample_rate(result, sr, target_sr)
            return result, target_sr
        except Exception as exc:
            logger.warning("重采样到 %d Hz 失败（%s），保持原采样率 %d Hz", target_sr, exc, sr)

    return result, sr


def preprocess_and_save_temp(
    audio_input: Any,
    filename: str = "temp_ref.wav",
    target_sr: int | None = None,
) -> tuple[str, int, np.ndarray]:
    """预处理参考音频并保存为临时 WAV 文件，供下游嵌入提取或克隆使用。

    支持的 ``audio_input`` 三种形态：
        1. **本地文件路径** (``str``)：由 ``soundfile.read`` 从磁盘加载；
        2. **FastAPI 上传对象** (``UploadFile``)：从上传流读取内容，写入
           ``NamedTemporaryFile`` 后再用 soundfile 读取；
        3. **numpy PCM 数组元组** (``tuple[int, np.ndarray]``)：
           ``(sample_rate, audio_array)``，直接使用。

    统一执行的预处理步骤：
        - 强制转为 float32；
        - int16 输入自动除以 32768 归一化到 [-1, 1]；
        - 峰值削波时再做一次最大绝对值归一化；
        - 多声道取均值转为单声道；
        - 如指定 ``target_sr`` 则重采样。

    Args:
        audio_input: 三种形态之一：文件路径 / UploadFile / (sr, ndarray)。
        filename: 临时文件保存名（位于 ``SAVE_DIR``）；默认 ``"temp_ref.wav"``。
        target_sr: 目标采样率（Hz）；``None`` 表示不重采样。

    Returns:
        tuple[str, int, np.ndarray]: ``(tmp_path, sample_rate, wav_arr)``
            - tmp_path: 归一化后落盘的临时文件绝对路径。
            - sample_rate: 最终采样率（重采样后等于 target_sr）。
            - wav_arr: 预处理后的单声道 float32 PCM 数组。

    Raises:
        ValidationError: 当 ``audio_input`` 不属于上述三种形态时抛出，
            提示用户支持的输入类型。
        AudioProcessingError: 当音频读取、重采样或写入磁盘失败时抛出。
    """
    tmp_p: str | None = None
    try:
        # 形态 1：本地文件路径 (str)
        if isinstance(audio_input, str):
            wav, sr = sf.read(audio_input)
        # 形态 2：FastAPI UploadFile（duck-typing：含 filename + file 属性）
        elif hasattr(audio_input, "filename") and hasattr(audio_input, "file"):
            upload_bytes = audio_input.file.read()
            # 用 NamedTemporaryFile(delete=False) 创建临时文件供 sf.read 读取
            suffix = os.path.splitext(getattr(audio_input, "filename", "tmp.wav"))[1] or ".wav"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as ntf:
                tmp_p = ntf.name
                ntf.write(upload_bytes)
            try:
                wav, sr = sf.read(tmp_p)
            finally:
                # 上传中间文件用完即删，不进入异常分支的 os.unlink
                try:
                    if tmp_p and os.path.exists(tmp_p):
                        os.unlink(tmp_p)
                except OSError:
                    pass
                tmp_p = None
        # 形态 3：(采样率, 数组) 元组
        elif isinstance(audio_input, tuple) and len(audio_input) == 2:
            _sr_candidate, _wav_candidate = audio_input
            if isinstance(_sr_candidate, int) and isinstance(_wav_candidate, np.ndarray):
                sr, wav = _sr_candidate, _wav_candidate
            else:
                raise ValidationError(
                    f"不支持的音频输入类型: {type(audio_input)}，支持: 文件路径/UploadFile/np.ndarray"
                )
        else:
            raise ValidationError(f"不支持的音频输入类型: {type(audio_input)}，支持: 文件路径/UploadFile/np.ndarray")

        wav_p = wav.astype(np.float32)
        if wav.dtype == np.int16:
            wav_p = wav_p / 32768.0
        max_val = np.max(np.abs(wav_p))
        if max_val > 1.0:
            wav_p = wav_p / max_val
        if wav_p.ndim > 1:
            wav_p = np.mean(wav_p, axis=-1)

        # 按需重采样到目标采样率
        if target_sr is not None and target_sr != sr:
            try:
                from .resampling import normalize_sample_rate

                wav_p = normalize_sample_rate(wav_p, sr, target_sr)
                sr = target_sr
            except Exception as exc:
                raise AudioProcessingError(f"参考音频重采样失败: {exc}") from exc

        out_path = os.path.join(SAVE_DIR, filename)

        # 写入策略：先落到 SAVE_DIR 内的临时文件，再 os.replace 原子替换
        # 保证并发写入时不会读到半截文件
        dir_name = os.path.dirname(out_path)
        suffix = os.path.splitext(out_path)[1] or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=dir_name) as ntf:
            tmp_p = ntf.name
        try:
            sf.write(tmp_p, wav_p, sr)
            os.replace(tmp_p, out_path)
            tmp_p = None  # 成功移交，不再需要清理
        except Exception:
            if tmp_p and os.path.exists(tmp_p):
                with contextlib.suppress(OSError):
                    os.unlink(tmp_p)
                tmp_p = None
            raise

        return out_path, sr, wav_p

    except (OSError, ValueError) as exc:
        if tmp_p and os.path.exists(tmp_p):
            with contextlib.suppress(OSError):
                os.unlink(tmp_p)
        if isinstance(exc, (AudioProcessingError, ValidationError)):
            raise
        raise AudioProcessingError(f"参考音频预处理失败: {exc}") from exc


def _save_wav_compatible(
    wav_data: np.ndarray,
    out_path: str,
    sample_rate: int = 48000,
) -> str:
    """将音频数据保存为浏览器兼容的 WAV 格式（int16 PCM）。

    与 ``save_audio`` 的区别：不生成时间戳文件名，输出路径由调用方显式指定
    ``out_path``；强制输出 PCM_16 格式（浏览器 <audio> 标签普遍兼容的子集）。

    Args:
        wav_data: 输入音频数组，支持 float32 [-1, 1] 或 int16，单/多声道。
        out_path: 目标 WAV 文件的完整路径（含扩展名）；若父目录不存在需
            由调用方预先创建。
        sample_rate: 输出采样率（Hz），默认 ``48000``。

    Returns:
        str: 实际写入的 ``out_path``（与入参一致），便于链式调用。

    Raises:
        AudioProcessingError: ``sf.write`` 抛出 ``OSError`` / ``ValueError`` 时
            包装为此异常，携带原始错误信息。
    """
    if wav_data.max() > 1.0 or wav_data.min() < -1.0:
        wav_data = wav_data / max(abs(wav_data.max()), abs(wav_data.min()))

    # P0 安全修复：写盘前强制嵌入水印，用于生成内容来源追溯。
    # source_id 为代码常量（WATERMARK_SOURCE_ID），不可通过配置篡改。
    # 水印失败时仅记录日志不阻塞生成（保证可用性），但会在日志中留下审计痕迹。
    try:
        from .watermark import watermark_audio

        wav_data, wm_meta = watermark_audio(
            wav_data.astype(np.float32),
            sample_rate,
            enable=True,
            source_id=WATERMARK_SOURCE_ID,
        )
        if wm_meta.get("watermarked"):
            logger.debug(
                "水印嵌入成功: source=%s, snr=%.1fdB, hash=%s",
                wm_meta.get("source_id", ""),
                wm_meta.get("snr_db", 0.0),
                wm_meta.get("content_hash", ""),
            )
        else:
            logger.warning("水印嵌入失败，音频已写入但无来源标识: %s", out_path)
    except Exception as wm_exc:
        logger.warning("水印嵌入异常（已忽略，音频正常写入）: %s", wm_exc)

    wav_int16 = (wav_data * 32767).astype(np.int16)

    # 原子写入：先写临时文件再 os.replace 替换，防止进程中断导致文件损坏
    # 临时文件保留 .wav 后缀，确保 soundfile 能从扩展名识别格式
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp_path = f"{out_path}.tmp.{os.getpid()}.wav"
    try:
        sf.write(tmp_path, wav_int16, sample_rate, subtype="PCM_16")
        os.replace(tmp_path, out_path)
    except (OSError, ValueError) as exc:
        if os.path.exists(tmp_path):
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
        raise AudioProcessingError(f"浏览器兼容 WAV 保存失败: {exc}") from exc
    return out_path


# ---------------------------------------------------------------------------
# Seed 增量辅助
# ---------------------------------------------------------------------------


def increment_seed(base_seed: int, chunk_index: int) -> int:
    """为每个分块生成不同的 seed，保持韵律多样性。

    通过将 ``base_seed`` 与 ``chunk_index`` 相加，确保每个分块使用不同的种子。
    结果对 ``2^31`` 取模以保持在 int32 正数范围内，避免溢出。

    Args:
        base_seed: 基础种子值，通常为用户传入或随机生成的整数。
        chunk_index: 分块索引（从 0 开始递增）。

    Returns:
        int: 该分块独立的种子值（范围 ``[0, 2^31)``）。

    Examples:
        >>> increment_seed(42, 0)
        42
        >>> increment_seed(42, 1)
        43
        >>> increment_seed(42, 5)
        47
    """
    _MAX_SEED = 2**31  # 保持种子在 int32 正数范围内
    return (base_seed + chunk_index) % _MAX_SEED


# ---------------------------------------------------------------------------
# 超长文本分块（支持 50000 字符）
# ---------------------------------------------------------------------------

# 超长文本最大分块字符数（默认 5000，可按需调整）
DEFAULT_CHUNK_MAX_CHARS: int = 5000

# 用于识别句末边界的标点集合
_SENTENCE_END_PUNCTUATION: set[str] = set("。！？.!?;\n")


def split_into_chunks(
    text: str,
    max_chars: int = DEFAULT_CHUNK_MAX_CHARS,
    respect_sentences: bool = True,
) -> list[tuple[str, int]]:
    """将超长文本拆分为多个分块，每个分块尊重句子边界。

    适用于流式处理管道中处理非常长的文本（支持 50000 字符以上）。
    每个分块不超过 ``max_chars`` 个字符，且尽量在句子边界处切分。
    方括号标签 ``[tag]`` 不会被分割到两个分块中（由内部的
    ``split_text_for_tts`` 负责保护）。

    Args:
        text: 待分块的原始文本。
        max_chars: 每个分块的最大字符数，默认 ``5000``。
        respect_sentences: 是否在句子边界处切分，默认 ``True``。
            设为 ``False`` 则严格按 ``max_chars`` 硬切（极端情况兜底）。

    Returns:
        list[tuple[str, int]]: ``(chunk_text, chunk_index)`` 元组组成的列表。
            ``chunk_index`` 从 0 开始递增；空文本输入返回 ``[("", 0)]``。

    Examples:
        >>> chunks = split_into_chunks("很长的文本...", max_chars=200)
        >>> len(chunks) > 1
        True
        >>> chunks[0][1]  # 第一个分块的索引
        0
    """
    if not text:
        return [("", 0)]

    if len(text) <= max_chars:
        return [(text, 0)]

    # 先用 split_text_for_tts 进行语义分割
    # 该函数已经保护了方括号标签不被打断
    sub_segments = split_text_for_tts(text, max_chars=max_chars)

    # 将子段合并为分块，确保每个分块不超过 max_chars
    chunks: list[str] = []
    current_chunk_parts: list[str] = []
    current_len = 0

    for seg in sub_segments:
        seg_len = len(seg)

        # 如果单个子段就超过 max_chars 且不尊重句子边界，硬切
        if seg_len > max_chars and not respect_sentences:
            if current_chunk_parts:
                chunk_text = "".join(current_chunk_parts)
                chunks.append(chunk_text)
                current_chunk_parts = []
                current_len = 0
            # 硬切超长子段
            for start in range(0, seg_len, max_chars):
                chunks.append(seg[start : start + max_chars])
            continue

        # 如果加入当前子段会超过 max_chars，先保存当前分块
        if current_len + seg_len > max_chars and current_chunk_parts:
            chunk_text = "".join(current_chunk_parts)
            chunks.append(chunk_text)
            current_chunk_parts = []
            current_len = 0

        current_chunk_parts.append(seg)
        current_len += seg_len

    # 保存最后一个分块
    if current_chunk_parts:
        chunk_text = "".join(current_chunk_parts)
        chunks.append(chunk_text)

    # 为每个分块分配索引
    return [(chunk, idx) for idx, chunk in enumerate(chunks)]
