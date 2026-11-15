"""剧本说话人感知分块规划器（P1-3）。

## 职责

把 ``schema/script_dubbing.py`` 的 ``ScriptConfig`` / ``ScriptSegment`` 变成
可直接驱动的合成计划：

1. **保护角色标签**：每个块内所有台词都重新渲染为 ``[角色] 台词``（带情绪时
   ``[角色](情绪) 台词``），标签永远完整，不会在块边界被腰斩。
2. **限制每块说话人数**：默认每个块最多 3 个角色（对齐 Fish-Speech 的
   drama 分块经验值），超过即开新块，降低长剧本的串角色概率。
3. **限制块长**：默认 300 渲染字符；可选注入 ``token_counter`` + ``max_tokens``
   做 token 预算（复用 P1-1 的 ``TextSegmenter``，受保护片段同样不会被切断）。
4. **不混引擎**：同一块只能属于一个引擎；角色声明了不同引擎时按引擎切块。
5. **指令行保序**：``[uv_break]`` / ``[pause=500]`` 跟随其后第一句台词进入同块，
   既不会被丢弃，也不会把停顿与台词拆散。

## 与既有引擎脚本解析的关系

本模块是“规划层”，不替换 ``engines/voxcpm2/script.py`` 的行级生成器。块的
``text`` 仍是 ``[角色] 台词`` 文本，可直接交给既有 ``parse_script``；引擎按块
依次生成，块内的角色引用只允许来自该块的 ``speakers``，从入口挡住“角色串到
别的音色”。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .schema.script_dubbing import (
    ScriptConfig,
    ScriptSegment,
    SpeakerConfig,
    parse_script,
)
from .text_segmenter import TextSegmenter

#: 每块最大角色数（对齐 Fish-Speech drama 分块的经验值）。
DEFAULT_MAX_SPEAKERS_PER_BLOCK = 3

#: 每块最大渲染字符数。
DEFAULT_MAX_CHARS_PER_BLOCK = 300


def render_segment(segment: ScriptSegment) -> str:
    """把段落渲染为引擎可解析的标签文本。

    - 指令段：``[uv_break]`` / ``[pause=500]``
    - 台词段：``[角色] 台词``；有情绪覆盖时 ``[角色](情绪) 台词``
    """
    if segment.is_instruction:
        return f"[{segment.text.strip()}]"
    tag = f"[{segment.speaker}]"
    if segment.emotion_override:
        return f"{tag}({segment.emotion_override}) {segment.text}".rstrip()
    return f"{tag} {segment.text}".rstrip()


@dataclass
class ScriptBlock:
    """一个说话人感知的合成块。

    Attributes:
        index: 块序号（从 0 开始）。
        speakers: 块内出现的角色（去重、保序）；指令块为空元组。
        segments: 块内段落（含指令段），保持剧本原始顺序。
        text: 渲染后的 ``[角色] 台词`` 文本，可直接喂给引擎解析。
        char_count: ``text`` 的字符数。
        engine: 块统一使用的引擎；None 表示跟随全局默认。
    """

    index: int
    speakers: tuple[str, ...]
    segments: tuple[ScriptSegment, ...]
    text: str
    char_count: int
    engine: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典（不含段落全文，避免响应体过大）。"""
        return {
            "index": self.index,
            "speakers": list(self.speakers),
            "text": self.text,
            "char_count": self.char_count,
            "engine": self.engine,
            "segment_count": len(self.segments),
        }


@dataclass
class ScriptPlan:
    """一次剧本规划结果。

    Attributes:
        blocks: 分块列表。
        warnings: 规划期告警（未声明角色、空台词等），由调用方显式上报。
        speakers: 参与规划的角色配置（缺省角色会以默认配置补齐）。
    """

    blocks: list[ScriptBlock]
    warnings: list[str] = field(default_factory=list)
    speakers: dict[str, SpeakerConfig] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "blocks": [b.to_dict() for b in self.blocks],
            "warnings": list(self.warnings),
            "speakers": {k: v.to_dict() for k, v in self.speakers.items()},
        }


def _validate_limits(max_speakers: int, max_chars: int, max_tokens: int | None) -> None:
    if max_speakers <= 0:
        raise ValueError(f"max_speakers 必须为正数，得到: {max_speakers}")
    if max_chars <= 0:
        raise ValueError(f"max_chars 必须为正数，得到: {max_chars}")
    if max_tokens is not None and max_tokens <= 0:
        raise ValueError(f"max_tokens 必须为正数，得到: {max_tokens}")


def _split_long_segment(
    segment: ScriptSegment,
    *,
    max_chars: int,
    max_tokens: int | None,
    token_counter: Callable[[str], int] | None,
) -> list[ScriptSegment]:
    """把超预算的单句台词切成多段，角色标签由渲染层补齐。"""
    if segment.is_instruction or not segment.text:
        return [segment]

    overhead = len(render_segment(segment)) - len(segment.text)
    text_budget = max(1, max_chars - overhead)
    segmenter = TextSegmenter(
        max_chars=text_budget,
        min_chars=1,
        max_tokens=max_tokens,
        token_counter=token_counter,
    )
    pieces = segmenter.segment_texts(segment.text, "zh")
    if len(pieces) <= 1:
        return [segment]
    return [
        ScriptSegment(
            speaker=segment.speaker,
            text=piece,
            index=segment.index,
            emotion_override=segment.emotion_override,
            speed_override=segment.speed_override,
        )
        for piece in pieces
    ]


def plan_script_segments(
    segments: list[ScriptSegment],
    *,
    max_speakers: int = DEFAULT_MAX_SPEAKERS_PER_BLOCK,
    max_chars: int = DEFAULT_MAX_CHARS_PER_BLOCK,
    max_tokens: int | None = None,
    token_counter: Callable[[str], int] | None = None,
    speaker_engine: Callable[[str], str | None] | None = None,
    default_engine: str | None = None,
) -> list[ScriptBlock]:
    """把解析后的段落按“≤N 角色 / ≤M 字符 / ≤T token / 同引擎”切块。

    Args:
        segments: ``schema.script_dubbing.parse_script`` 的输出。
        max_speakers: 每块最大角色数。
        max_chars: 每块最大渲染字符数（含角色标签）。
        max_tokens: 每块 token 预算，需配合 ``token_counter``。
        token_counter: 文本 → token 数计数器。
        speaker_engine: 角色 → 引擎标识；用于阻止不同引擎混入同一块。
        default_engine: 未声明引擎时的默认值。

    Returns:
        list[ScriptBlock]: 保序分块；空输入返回空列表。
    """
    _validate_limits(max_speakers, max_chars, max_tokens)
    if not segments:
        return []

    expanded: list[ScriptSegment] = []
    for seg in segments:
        expanded.extend(
            _split_long_segment(seg, max_chars=max_chars, max_tokens=max_tokens, token_counter=token_counter)
        )

    # 指令段前置到下一句台词；尾部指令单独成块（或并入最后一块）。
    units: list[tuple[list[ScriptSegment], ScriptSegment]] = []
    pending_instructions: list[ScriptSegment] = []
    for seg in expanded:
        if seg.is_instruction:
            pending_instructions.append(seg)
            continue
        units.append((pending_instructions, seg))
        pending_instructions = []
    trailing_instructions = pending_instructions

    def _engine_for(speaker: str) -> str | None:
        if speaker_engine is not None:
            return speaker_engine(speaker)
        return default_engine

    blocks: list[ScriptBlock] = []
    cur: list[ScriptSegment] = []
    cur_speakers: list[str] = []
    cur_engine: str | None = None

    def _text_of(segs: list[ScriptSegment]) -> str:
        return "\n".join(render_segment(s) for s in segs)

    def _fits(segs: list[ScriptSegment]) -> bool:
        text = _text_of(segs)
        if len(text) > max_chars:
            return False
        if token_counter is not None and max_tokens is not None:
            return token_counter(text) <= max_tokens
        return True

    def _flush() -> None:
        nonlocal cur, cur_speakers, cur_engine
        if not cur:
            return
        text = _text_of(cur)
        blocks.append(
            ScriptBlock(
                index=len(blocks),
                speakers=tuple(cur_speakers),
                segments=tuple(cur),
                text=text,
                char_count=len(text),
                engine=cur_engine,
            )
        )
        cur, cur_speakers, cur_engine = [], [], None

    for instructions, speech in units:
        engine = _engine_for(speech.speaker)
        new_speakers = list(cur_speakers)
        if speech.speaker not in new_speakers:
            new_speakers.append(speech.speaker)
        candidate = cur + instructions + [speech]

        over_speakers = len(new_speakers) > max_speakers
        over_engine = cur_engine is not None and engine != cur_engine
        if cur and (over_speakers or over_engine or not _fits(candidate)):
            _flush()

        if not cur:
            cur_engine = engine
            cur_speakers = []
        if speech.speaker not in cur_speakers:
            cur_speakers.append(speech.speaker)
        cur.extend(instructions)
        cur.append(speech)

    _flush()

    if trailing_instructions:
        if blocks:
            last = blocks[-1]
            merged_text = last.text + "\n" + _text_of(trailing_instructions)
            blocks[-1] = ScriptBlock(
                index=last.index,
                speakers=last.speakers,
                segments=last.segments + tuple(trailing_instructions),
                text=merged_text,
                char_count=len(merged_text),
                engine=last.engine,
            )
        else:
            text = _text_of(trailing_instructions)
            blocks.append(
                ScriptBlock(
                    index=0,
                    speakers=(),
                    segments=tuple(trailing_instructions),
                    text=text,
                    char_count=len(text),
                    engine=default_engine,
                )
            )

    return blocks


def plan_script_text(
    script_text: str,
    *,
    max_speakers: int = DEFAULT_MAX_SPEAKERS_PER_BLOCK,
    max_chars: int = DEFAULT_MAX_CHARS_PER_BLOCK,
    max_tokens: int | None = None,
    token_counter: Callable[[str], int] | None = None,
    speakers: dict[str, SpeakerConfig] | None = None,
    default_engine: str | None = None,
) -> ScriptPlan:
    """解析剧本文本并按说话人感知规则分块。

    Args:
        script_text: ``[角色] 台词`` 格式的剧本文本。
        max_speakers: 每块最大角色数。
        max_chars: 每块最大渲染字符数。
        max_tokens: 每块 token 预算。
        token_counter: token 计数器。
        speakers: 已声明的角色配置（用于按角色切引擎）。
        default_engine: 未声明角色/引擎时的默认值。

    Returns:
        ScriptPlan: 分块结果与告警。
    """
    segments = parse_script(script_text)
    warnings: list[str] = []
    resolved_speakers: dict[str, SpeakerConfig] = dict(speakers or {})

    for seg in segments:
        if seg.is_instruction or not seg.speaker:
            continue
        if seg.speaker not in resolved_speakers:
            resolved_speakers[seg.speaker] = SpeakerConfig(name=seg.speaker, engine=default_engine)
            warnings.append(f"角色 [{seg.speaker}] 未在脚本配置中声明，按默认引擎处理")
        if not seg.text:
            warnings.append(f"角色 [{seg.speaker}] 的台词为空，该行将标记为错误")

    def _engine_of(speaker: str) -> str | None:
        cfg = resolved_speakers.get(speaker)
        if cfg is not None:
            return cfg.engine or default_engine
        return default_engine

    blocks = plan_script_segments(
        segments,
        max_speakers=max_speakers,
        max_chars=max_chars,
        max_tokens=max_tokens,
        token_counter=token_counter,
        speaker_engine=_engine_of,
        default_engine=default_engine,
    )
    return ScriptPlan(blocks=blocks, warnings=warnings, speakers=resolved_speakers)


def plan_script_config(
    config: ScriptConfig,
    *,
    max_tokens: int | None = None,
    token_counter: Callable[[str], int] | None = None,
) -> ScriptPlan:
    """按 ``ScriptConfig`` 的声明规划剧本分块。

    块上限来自配置自身（``max_speakers_per_block`` / ``max_chars_per_block`` /
    ``max_tokens_per_block``），未声明角色会补齐默认配置并写告警。
    """
    return plan_script_text(
        config.script_text,
        max_speakers=config.max_speakers_per_block,
        max_chars=config.max_chars_per_block,
        max_tokens=max_tokens if max_tokens is not None else config.max_tokens_per_block,
        token_counter=token_counter,
        speakers=config.speakers,
        default_engine=config.global_engine,
    )


# ---------------------------------------------------------------------------
# speaker_map 归一化（VoxCPM2 不再静默忽略该参数）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpeakerBinding:
    """单个说话人的解析结果。

    Attributes:
        role: 剧本中的角色名。
        engine: 该角色声明的引擎。
        wav_path: 参考音频绝对路径；解析失败为 None。
        ref_text: 参考音频转写文本。
        persona_name: 若来自已注册 Persona，记录其名称。
    """

    role: str
    engine: str | None
    wav_path: str | None
    ref_text: str = ""
    persona_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "role": self.role,
            "engine": self.engine,
            "wav_path": self.wav_path,
            "ref_text": self.ref_text,
            "persona_name": self.persona_name,
        }


@dataclass
class SpeakerBindingResult:
    """speaker_map 归一化结果。

    Attributes:
        bindings: 角色 → 绑定。
        unsupported: 声明了非目标引擎的角色（``(role, engine)``）。
        warnings: 解析告警。
    """

    bindings: dict[str, SpeakerBinding] = field(default_factory=dict)
    unsupported: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def wav_map(self) -> dict[str, dict[str, str]]:
        """转换为引擎侧 ``{角色: {"wav": ..., "text": ...}}`` 映射。"""
        return {role: {"wav": b.wav_path, "text": b.ref_text} for role, b in self.bindings.items() if b.wav_path}


def build_speaker_bindings(
    speaker_map: dict[str, Any] | None,
    *,
    target_engine: str = "voxcpm2",
) -> SpeakerBindingResult:
    """把 ``speaker_map`` 归一化为参考音频绑定。

    支持的值形态：
        - ``str``：参考音频路径；
        - ``dict``：``{"wav"|"reference_audio": path, "text": 转写,
          "persona_name": 已注册音色, "engine": 引擎}``。

    Persona 解析走 P1-2 的 Voice Profile（``load_voice_profile``），只读资产层，
    不触发模型推理。声明了 ``target_engine`` 之外引擎的角色进入 ``unsupported``，
    由调用方显式拒绝或转派，绝不静默丢弃。

    Args:
        speaker_map: 角色 → 音色配置。
        target_engine: 当前执行引擎标识。

    Returns:
        SpeakerBindingResult: 绑定、跨引擎角色与告警。
    """
    result = SpeakerBindingResult()
    if not speaker_map:
        return result

    for role, value in speaker_map.items():
        role_name = str(role).strip()
        if not role_name:
            result.warnings.append("speaker_map 中存在空角色名，已跳过")
            continue

        engine: str | None = None
        wav_path: str | None = None
        ref_text = ""
        persona_name: str | None = None

        if isinstance(value, str):
            wav_path = value
        elif isinstance(value, dict):
            engine = value.get("engine") or None
            persona_name = value.get("persona_name") or None
            wav_path = value.get("wav") or value.get("reference_audio") or None
            ref_text = str(value.get("text") or value.get("reference_text") or "")
        elif isinstance(value, SpeakerConfig):
            # 允许直接传 Schema 的 SpeakerConfig，避免调用方再做一层 dict 转换。
            engine = value.engine
            persona_name = value.persona_name
            wav_path = value.reference_audio
        else:
            result.warnings.append(f"角色 [{role_name}] 的 speaker_map 值类型不支持: {type(value).__name__}")
            continue

        if persona_name:
            from .persona_manager import load_voice_profile

            profile = load_voice_profile(str(persona_name))
            if profile is None:
                result.warnings.append(f"角色 [{role_name}] 的 Persona [{persona_name}] 不存在或缺少参考音频")
            else:
                wav_path = wav_path or profile.reference_wav
                ref_text = ref_text or profile.reference_text

        resolved_engine = (engine or target_engine).lower()
        if resolved_engine != target_engine.lower():
            result.unsupported.append((role_name, resolved_engine))

        if wav_path and not os.path.isfile(wav_path):
            result.warnings.append(f"角色 [{role_name}] 的参考音频不存在: {wav_path}")
            wav_path = None

        result.bindings[role_name] = SpeakerBinding(
            role=role_name,
            engine=resolved_engine,
            wav_path=wav_path,
            ref_text=ref_text,
            persona_name=str(persona_name) if persona_name else None,
        )

    return result


@dataclass
class ScriptRequestPlan:
    """生成入口的完整准备结果（引擎可直接消费）。

    Attributes:
        plan: 分块计划。
        persona_map: 合并后的 ``{角色: {"wav", "text"}}`` 映射。
        unsupported_engines: 声明了非目标引擎的角色。
        warnings: 汇总告警。
    """

    plan: ScriptPlan
    persona_map: dict[str, dict[str, str]] = field(default_factory=dict)
    unsupported_engines: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def prepare_script_request(
    script_text: str,
    *,
    speaker_map: dict[str, Any] | None = None,
    persona_map: dict[str, Any] | None = None,
    config: ScriptConfig | None = None,
    target_engine: str = "voxcpm2",
    max_speakers: int = DEFAULT_MAX_SPEAKERS_PER_BLOCK,
    max_chars: int = DEFAULT_MAX_CHARS_PER_BLOCK,
    max_tokens: int | None = None,
    token_counter: Callable[[str], int] | None = None,
) -> ScriptRequestPlan:
    """把剧本文本 + speaker_map/persona_map/config 归一化为生成计划。

    合并优先级：``persona_map`` 提供的显式路径最低；``speaker_map`` 中的
    ``persona_name`` / ``wav`` 会覆盖同名角色；``config.speakers`` 只影响
    分块引擎归属，不覆盖已解析的音频路径。
    """
    bindings = build_speaker_bindings(speaker_map, target_engine=target_engine)

    merged_persona: dict[str, dict[str, str]] = {}
    for role, value in (persona_map or {}).items():
        role_name = str(role).strip()
        if not role_name:
            continue
        if isinstance(value, str):
            merged_persona[role_name] = {"wav": value, "text": ""}
        elif isinstance(value, dict):
            merged_persona[role_name] = {
                "wav": str(value.get("wav") or value.get("reference_audio") or ""),
                "text": str(value.get("text") or ""),
            }
    for role, payload in bindings.wav_map().items():
        merged_persona[role] = payload

    speakers = dict(config.speakers) if config is not None else {}
    for role, binding in bindings.bindings.items():
        if role not in speakers:
            speakers[role] = SpeakerConfig(name=role, engine=binding.engine)
    for role in merged_persona:
        if role not in speakers:
            speakers[role] = SpeakerConfig(name=role, engine=target_engine)

    if config is not None:
        plan = plan_script_text(
            script_text,
            max_speakers=config.max_speakers_per_block,
            max_chars=config.max_chars_per_block,
            max_tokens=config.max_tokens_per_block if max_tokens is None else max_tokens,
            token_counter=token_counter,
            speakers=speakers,
            default_engine=config.global_engine or target_engine,
        )
        plan.warnings.extend(bindings.warnings)
    else:
        plan = plan_script_text(
            script_text,
            max_speakers=max_speakers,
            max_chars=max_chars,
            max_tokens=max_tokens,
            token_counter=token_counter,
            speakers=speakers,
            default_engine=target_engine,
        )
        plan.warnings.extend(bindings.warnings)

    return ScriptRequestPlan(
        plan=plan,
        persona_map=merged_persona,
        unsupported_engines=list(bindings.unsupported),
        warnings=list(plan.warnings),
    )
