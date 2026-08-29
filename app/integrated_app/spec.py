"""TTS 领域公式契约层（Spec Contract）。

本模块是 TTS_MultiModel 的框架无关公式单一事实源（参考 MiniMax-H3 h3/spec.py）。
所有采样率、时长、文本长度约束在此定义，上层代码引用这里而非硬编码。
"""

from dataclasses import dataclass

# ── 常量 ──
SAMPLE_RATE: int = 48000  # 采样率 (Hz)
MAX_CHARS_PER_SEGMENT: int = 200  # 单段 TTS 最大字符数
SPLIT_MAX_CHARS: int = 200  # 长文本分段最大字符数
MIN_CHARS_PER_SEGMENT: int = 50  # 单段 TTS 最小字符数
MAX_CHARS_RANGE: tuple[int, int] = (50, 500)  # 允许的字符数范围


# ── 公式 ──
def duration_to_samples(duration_sec: float) -> int:
    """将时长（秒）转为采样点数。"""
    return int(round(duration_sec * SAMPLE_RATE))


def samples_to_duration(n_samples: int) -> float:
    """将采样点数转为时长（秒）。"""
    return n_samples / SAMPLE_RATE


def is_valid_text_length(text: str, max_chars: int | None = None) -> bool:
    """检查文本长度是否在合法范围内。"""
    if max_chars is None:
        max_chars = MAX_CHARS_PER_SEGMENT
    length = len(text.strip())
    return MIN_CHARS_PER_SEGMENT <= length <= max_chars


def split_text_long(text: str, max_chars: int | None = None) -> list[str]:
    """将长文本按最大字符数分段。"""
    if max_chars is None:
        max_chars = SPLIT_MAX_CHARS
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    segments = []
    for i in range(0, len(text), max_chars):
        segments.append(text[i : i + max_chars])
    return segments


@dataclass
class EngineSpec:
    """引擎规格。"""

    name: str
    display_name: str
    sample_rate: int = SAMPLE_RATE
    requires_gpu: bool = True


# 引擎静态规格（与 config.yaml engines 节对齐）
ENGINES: dict[str, EngineSpec] = {
    "voxcpm2": EngineSpec(name="voxcpm2", display_name="VoxCPM2", sample_rate=48000, requires_gpu=True),
    "indextts2": EngineSpec(name="indextts2", display_name="IndexTTS 2.5", sample_rate=48000, requires_gpu=True),
    "indextts20": EngineSpec(name="indextts20", display_name="IndexTTS 2.0", sample_rate=48000, requires_gpu=True),
}


def supported_engine_names() -> list[str]:
    """返回支持的引擎名列表。"""
    return list(ENGINES.keys())
