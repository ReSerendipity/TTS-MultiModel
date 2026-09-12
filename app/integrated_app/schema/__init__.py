"""Schema 数据结构定义模块。

包含剧本配音、多角色对话等场景的结构化数据类定义。
"""

from .script_dubbing import (
    ScriptConfig,
    ScriptDubbingResult,
    ScriptSegment,
    SpeakerConfig,
    parse_script,
)

__all__ = [
    "ScriptConfig",
    "ScriptDubbingResult",
    "ScriptSegment",
    "SpeakerConfig",
    "parse_script",
]
