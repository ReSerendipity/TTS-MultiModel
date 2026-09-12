"""剧本配音多角色 Schema 数据类定义。

借鉴 dia（nari-labs/dia，Apache-2.0）的 `[S1]/[S2]` 多角色标签对话生成范式，
结合本仓现有架构扩展为描述性角色名标签 + 多引擎可配置的结构化 Schema。

设计文档：docs/plans/SCRIPT_DUBBING_SCHEMA.md

状态：数据类定义（未接入生成流程，供未来剧本工坊功能扩展使用）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SegmentStatus(str, Enum):
    """剧本段落生成状态。"""

    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SpeakerConfig:
    """角色配置：每个角色的独立 TTS 参数。

    Attributes:
        name: 角色名称（与剧本标签一致，在整个剧本中唯一）。
        engine: 使用的 TTS 引擎标识（None 时使用全局默认引擎）。
        reference_audio: 参考音频路径（克隆模式，None 时使用 voice_design）。
        voice_design_instruction: 语音设计指令（设计模式，如"温柔的女声"）。
        emotion: 默认情绪标签（如 "happy" / "sad"，None 时使用引擎默认）。
        emotion_intensity: 情绪强度 0.0-1.0，默认 0.5。
        speed: 语速因子 0.5-2.0，默认 1.0。
        normalize: 是否响度归一化，默认 True。
        seed: 随机种子（-1 为随机，默认 -1）。
        persona_name: 已注册的 Persona 名称（优先于 reference_audio）。
    """

    name: str
    engine: str | None = None
    reference_audio: str | None = None
    voice_design_instruction: str = ""
    emotion: str | None = None
    emotion_intensity: float = 0.5
    speed: float = 1.0
    normalize: bool = True
    seed: int = -1
    persona_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "name": self.name,
            "engine": self.engine,
            "reference_audio": self.reference_audio,
            "voice_design_instruction": self.voice_design_instruction,
            "emotion": self.emotion,
            "emotion_intensity": self.emotion_intensity,
            "speed": self.speed,
            "normalize": self.normalize,
            "seed": self.seed,
            "persona_name": self.persona_name,
        }


@dataclass
class ScriptSegment:
    """剧本段落：解析后的单个台词段落。

    Attributes:
        speaker: 说话人角色名。
        text: 台词文本。
        index: 段落在剧本中的序号（从 0 开始）。
        emotion_override: 本段覆盖的情绪（可选，支持行内标签）。
        speed_override: 本段覆盖的语速（可选）。
        generated_audio: 生成后的音频路径（生成后填充）。
        status: 生成状态。
        error: 失败原因（失败时填充）。
    """

    speaker: str
    text: str
    index: int = 0
    emotion_override: str | None = None
    speed_override: float | None = None
    generated_audio: str | None = None
    status: SegmentStatus = SegmentStatus.PENDING
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "speaker": self.speaker,
            "text": self.text,
            "index": self.index,
            "emotion_override": self.emotion_override,
            "speed_override": self.speed_override,
            "generated_audio": self.generated_audio,
            "status": self.status.value,
            "error": self.error,
        }


@dataclass
class ScriptConfig:
    """剧本配音任务配置：整个剧本的全局参数 + 角色配置映射。

    Attributes:
        script_text: 原始剧本文本（带说话人标签）。
        speakers: 角色名 → 角色配置映射。
        global_engine: 全局默认引擎（角色未指定时使用）。
        global_normalize: 全局响度归一化，默认 True。
        target_lufs: 目标响度 LUFS，默认 -16.0。
        output_format: 输出格式，默认 "wav"。
        concatenate: 是否拼接为完整音频，默认 True。
        segment_pause_ms: 段落间停顿（毫秒），默认 300。
        parallel_generation: 是否并行生成（受单 Worker 限制，默认 False）。
    """

    script_text: str
    speakers: dict[str, SpeakerConfig] = field(default_factory=dict)
    global_engine: str | None = None
    global_normalize: bool = True
    target_lufs: float = -16.0
    output_format: str = "wav"
    concatenate: bool = True
    segment_pause_ms: int = 300
    parallel_generation: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "script_text": self.script_text,
            "speakers": {name: cfg.to_dict() for name, cfg in self.speakers.items()},
            "global_engine": self.global_engine,
            "global_normalize": self.global_normalize,
            "target_lufs": self.target_lufs,
            "output_format": self.output_format,
            "concatenate": self.concatenate,
            "segment_pause_ms": self.segment_pause_ms,
            "parallel_generation": self.parallel_generation,
        }


@dataclass
class ScriptDubbingResult:
    """剧本配音生成结果。

    Attributes:
        success: 是否全部成功。
        output_path: 拼接后的完整音频路径（concatenate=True 时）。
        segments: 各段落的生成结果（含每段音频路径）。
        total_duration: 总时长（秒）。
        failed_count: 失败段落数。
        message: 结果消息。
    """

    success: bool = False
    output_path: str | None = None
    segments: list[ScriptSegment] = field(default_factory=list)
    total_duration: float = 0.0
    failed_count: int = 0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "success": self.success,
            "output_path": self.output_path,
            "segments": [seg.to_dict() for seg in self.segments],
            "total_duration": self.total_duration,
            "failed_count": self.failed_count,
            "message": self.message,
        }


# 说话人标签正则：匹配行首的 [角色名] 标签
_SPEAKER_TAG_PATTERN = re.compile(r"^\[([^\]]+)\]\s*(.*)$")


def parse_script(script_text: str) -> list[ScriptSegment]:
    """解析带说话人标签的剧本文本为段落列表。

    标签格式：``[角色名] 台词内容``

    规则：
    - 标签以 ``[`` 开头、``]`` 结尾，中间为角色名称（支持中英文）
    - 标签后紧跟空格，然后是该角色的台词
    - 角色名称在整个剧本中唯一，用于关联角色配置
    - 支持多行台词（同一角色的连续无标签行会被合并）
    - 空行忽略

    Args:
        script_text: 带说话人标签的剧本文本。

    Returns:
        list[ScriptSegment]: 解析后的段落列表，按出现顺序排列。

    Example:
        >>> text = "[爱丽丝] 你好\\n[鲍勃] 嗨"
        >>> segments = parse_script(text)
        >>> [s.speaker for s in segments]
        ['爱丽丝', '鲍勃']
    """
    segments: list[ScriptSegment] = []
    current_speaker: str | None = None
    current_text: list[str] = []
    index = 0

    for line in script_text.split("\n"):
        line = line.strip()
        if not line:
            continue

        match = _SPEAKER_TAG_PATTERN.match(line)
        if match:
            # 保存上一段
            if current_speaker is not None and current_text:
                segments.append(
                    ScriptSegment(
                        speaker=current_speaker,
                        text=" ".join(current_text),
                        index=index,
                    )
                )
                index += 1

            # 开始新段落
            current_speaker = match.group(1).strip()
            rest = match.group(2).strip()
            current_text = [rest] if rest else []
        elif current_speaker is not None:
            # 续行（同一角色的多行台词）
            current_text.append(line)

    # 保存最后一段
    if current_speaker is not None and current_text:
        segments.append(
            ScriptSegment(
                speaker=current_speaker,
                text=" ".join(current_text),
                index=index,
            )
        )

    return segments
