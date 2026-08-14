"""引擎 UI 数据定义模块。

提供 TTS 引擎在前端界面展示所需的元数据、参数定义、默认值、
UI 组件类型、验证规则等结构化数据：
- 引擎基础信息（名称、描述、显存要求）
- 生成参数定义（类型、范围、默认值、UI 组件类型）
- 参数分组（基础/高级/实验性）
- 支持的功能特性标记
- UI 提示文本和国际化键

设计要点：
- 数据驱动 UI：前端根据此数据动态渲染参数控件
- 类型安全：使用 dataclass 和 Literal 类型确保数据结构一致
- 可扩展：新引擎只需添加 EngineUIData 实例
- 支持 i18n：描述使用 i18n 键而非硬编码文本
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# 枚举类型
# ---------------------------------------------------------------------------


class ParamType(str, Enum):
    """参数 UI 组件类型。"""

    SLIDER = "slider"
    NUMBER = "number"
    SELECT = "select"
    CHECKBOX = "checkbox"
    TEXT = "text"
    TEXTAREA = "textarea"
    FILE = "file"
    SEED = "seed"


class ParamGroup(str, Enum):
    """参数分组。"""

    BASIC = "basic"
    ADVANCED = "advanced"
    EXPERIMENTAL = "experimental"
    REFERENCE = "reference"


class EngineFeature(str, Enum):
    """引擎功能特性标记。"""

    VOICE_DESIGN = "voice_design"
    VOICE_CLONE = "voice_clone"
    ULTIMATE_CLONE = "ultimate_clone"
    SCRIPT_WORKSHOP = "script_workshop"
    STREAMING = "streaming"
    EMOTION_CONTROL = "emotion_control"
    PROMPT_CONTINUE = "prompt_continue"
    LORA = "lora"
    BATCH = "batch"


# ---------------------------------------------------------------------------
# 参数定义数据类
# ---------------------------------------------------------------------------


@dataclass
class ParamOption:
    """下拉选择框选项。

    Attributes:
        value: 选项值（提交给后端的值）。
        label_i18n: 显示文本的 i18n 键。
        description_i18n: 选项描述的 i18n 键（可选）。
    """

    value: Any
    label_i18n: str
    description_i18n: str | None = None


@dataclass
class ParamDefinition:
    """单个生成参数的 UI 定义。

    Attributes:
        key: 参数键名（对应 API 参数名）。
        label_i18n: 标签文本的 i18n 键。
        description_i18n: 描述文本的 i18n 键（tooltip 显示）。
        param_type: UI 组件类型。
        group: 参数分组。
        default: 默认值。
        min: 最小值（slider/number）。
        max: 最大值（slider/number）。
        step: 步长（slider/number）。
        options: 下拉选项（select 类型）。
        placeholder_i18n: 占位符文本 i18n 键（text/textarea）。
        file_types: 接受的文件类型（file 类型），如 [".wav", ".mp3"]。
        required: 是否必填。
        visible: 是否在 UI 中可见（可通过其他参数控制）。
        affects_quality: 是否影响生成质量（用于提示用户）。
        affects_speed: 是否影响生成速度（用于提示用户）。
    """

    key: str
    label_i18n: str
    param_type: ParamType
    group: ParamGroup = ParamGroup.BASIC
    description_i18n: str | None = None
    default: Any = None
    min: int | float | None = None
    max: int | float | None = None
    step: int | float | None = None
    options: list[ParamOption] | None = None
    placeholder_i18n: str | None = None
    file_types: list[str] | None = None
    required: bool = False
    visible: bool = True
    affects_quality: bool = False
    affects_speed: bool = False


# ---------------------------------------------------------------------------
# 引擎 UI 数据类
# ---------------------------------------------------------------------------


@dataclass
class EngineUIData:
    """引擎 UI 元数据。

    Attributes:
        engine_id: 引擎唯一标识符（如 "voxcpm2", "indextts2"）。
        name_i18n: 引擎显示名称的 i18n 键。
        description_i18n: 引擎描述的 i18n 键。
        version: 引擎版本字符串。
        min_vram_gb: 最低显存要求（GB）。
        recommended_vram_gb: 推荐显存要求（GB）。
        features: 支持的功能特性列表。
        params: 生成参数定义列表。
        tab_order: 标签页显示顺序（数字越小越靠前）。
        icon: 图标名称（对应前端图标库）。
        color: 主题色（十六进制颜色码）。
        sample_rate: 默认输出采样率（Hz）。
    """

    engine_id: str
    name_i18n: str
    description_i18n: str
    version: str = "1.0.0"
    min_vram_gb: float = 4.0
    recommended_vram_gb: float = 8.0
    features: list[EngineFeature] = field(default_factory=list)
    params: list[ParamDefinition] = field(default_factory=list)
    tab_order: int = 100
    icon: str = "microphone"
    color: str = "#6366f1"
    sample_rate: int = 24000

    def get_param(self, key: str) -> ParamDefinition | None:
        """根据键名获取参数定义。

        Args:
            key: 参数键名。

        Returns:
            ParamDefinition 实例，不存在时返回 None。
        """
        for p in self.params:
            if p.key == key:
                return p
        return None

    def get_params_by_group(self, group: ParamGroup) -> list[ParamDefinition]:
        """获取指定分组的参数列表。

        Args:
            group: 参数分组。

        Returns:
            该分组的可见参数列表。
        """
        return [p for p in self.params if p.group == group and p.visible]

    def get_default_params(self) -> dict[str, Any]:
        """获取所有参数的默认值字典。

        Returns:
            {key: default_value} 字典。
        """
        return {p.key: p.default for p in self.params if p.default is not None}

    def has_feature(self, feature: EngineFeature) -> bool:
        """检查引擎是否支持指定功能。

        Args:
            feature: 功能特性。

        Returns:
            True 表示支持。
        """
        return feature in self.features


# ---------------------------------------------------------------------------
# VoxCPM2 引擎 UI 数据
# ---------------------------------------------------------------------------

VOXCPM2_UI_DATA = EngineUIData(
    engine_id="voxcpm2",
    name_i18n="engine.voxcpm2.name",
    description_i18n="engine.voxcpm2.description",
    version="2.0",
    min_vram_gb=4.0,
    recommended_vram_gb=8.0,
    features=[
        EngineFeature.VOICE_DESIGN,
        EngineFeature.VOICE_CLONE,
        EngineFeature.ULTIMATE_CLONE,
        EngineFeature.SCRIPT_WORKSHOP,
        EngineFeature.STREAMING,
        EngineFeature.PROMPT_CONTINUE,
        EngineFeature.LORA,
    ],
    tab_order=10,
    icon="sparkles",
    color="#8b5cf6",
    sample_rate=24000,
    params=[
        ParamDefinition(
            key="text",
            label_i18n="param.text.label",
            description_i18n="param.text.description",
            param_type=ParamType.TEXTAREA,
            group=ParamGroup.BASIC,
            placeholder_i18n="param.text.placeholder",
            required=True,
        ),
        ParamDefinition(
            key="instruction",
            label_i18n="param.instruction.label",
            description_i18n="param.instruction.description",
            param_type=ParamType.TEXT,
            group=ParamGroup.BASIC,
            placeholder_i18n="param.instruction.placeholder",
            default="",
        ),
        ParamDefinition(
            key="cfg_value",
            label_i18n="param.cfg.label",
            description_i18n="param.cfg.description",
            param_type=ParamType.SLIDER,
            group=ParamGroup.BASIC,
            default=2.0,
            min=1.0,
            max=5.0,
            step=0.1,
            affects_quality=True,
        ),
        ParamDefinition(
            key="inference_timesteps",
            label_i18n="param.steps.label",
            description_i18n="param.steps.description",
            param_type=ParamType.SLIDER,
            group=ParamGroup.BASIC,
            default=10,
            min=5,
            max=50,
            step=1,
            affects_quality=True,
            affects_speed=True,
        ),
        ParamDefinition(
            key="denoise",
            label_i18n="param.denoise.label",
            description_i18n="param.denoise.description",
            param_type=ParamType.CHECKBOX,
            group=ParamGroup.BASIC,
            default=True,
            affects_quality=True,
        ),
        ParamDefinition(
            key="normalize",
            label_i18n="param.normalize.label",
            description_i18n="param.normalize.description",
            param_type=ParamType.CHECKBOX,
            group=ParamGroup.ADVANCED,
            default=True,
        ),
        ParamDefinition(
            key="reference_audio",
            label_i18n="param.reference_audio.label",
            description_i18n="param.reference_audio.description",
            param_type=ParamType.FILE,
            group=ParamGroup.REFERENCE,
            file_types=[".wav", ".mp3", ".flac", ".ogg", ".m4a"],
        ),
        ParamDefinition(
            key="seed",
            label_i18n="param.seed.label",
            description_i18n="param.seed.description",
            param_type=ParamType.SEED,
            group=ParamGroup.ADVANCED,
            default=-1,
            min=-1,
            max=2**31 - 1,
            step=1,
        ),
        ParamDefinition(
            key="advanced_denoise",
            label_i18n="param.advanced_denoise.label",
            description_i18n="param.advanced_denoise.description",
            param_type=ParamType.SLIDER,
            group=ParamGroup.EXPERIMENTAL,
            default=1.0,
            min=0.0,
            max=1.0,
            step=0.05,
            visible=False,
        ),
    ],
)


# ---------------------------------------------------------------------------
# IndexTTS2 引擎 UI 数据
# ---------------------------------------------------------------------------

INDEXTTTS2_UI_DATA = EngineUIData(
    engine_id="indextts2",
    name_i18n="engine.indextts2.name",
    description_i18n="engine.indextts2.description",
    version="2.5",
    min_vram_gb=6.0,
    recommended_vram_gb=12.0,
    features=[
        EngineFeature.VOICE_CLONE,
        EngineFeature.EMOTION_CONTROL,
        EngineFeature.STREAMING,
    ],
    tab_order=20,
    icon="heart",
    color="#ef4444",
    sample_rate=22050,
    params=[
        ParamDefinition(
            key="text",
            label_i18n="param.text.label",
            description_i18n="param.text.description",
            param_type=ParamType.TEXTAREA,
            group=ParamGroup.BASIC,
            placeholder_i18n="param.text.placeholder",
            required=True,
        ),
        ParamDefinition(
            key="lang",
            label_i18n="param.lang.label",
            description_i18n="param.lang.description",
            param_type=ParamType.SELECT,
            group=ParamGroup.BASIC,
            default="Auto",
            options=[
                ParamOption("Auto", "lang.auto"),
                ParamOption("ZH", "lang.zh"),
                ParamOption("EN", "lang.en"),
                ParamOption("JA", "lang.ja"),
                ParamOption("ES", "lang.es"),
                ParamOption("AR", "lang.ar"),
            ],
        ),
        ParamDefinition(
            key="reference_audio",
            label_i18n="param.reference_audio.label",
            description_i18n="param.reference_audio.description",
            param_type=ParamType.FILE,
            group=ParamGroup.REFERENCE,
            file_types=[".wav", ".mp3", ".flac", ".ogg", ".m4a"],
            required=True,
        ),
        ParamDefinition(
            key="emotion",
            label_i18n="param.emotion.label",
            description_i18n="param.emotion.description",
            param_type=ParamType.SELECT,
            group=ParamGroup.BASIC,
            default="neutral",
            options=[
                ParamOption("neutral", "emotion.neutral"),
                ParamOption("happy", "emotion.happy"),
                ParamOption("sad", "emotion.sad"),
                ParamOption("angry", "emotion.angry"),
                ParamOption("surprised", "emotion.surprised"),
                ParamOption("calm", "emotion.calm"),
                ParamOption("fearful", "emotion.fearful"),
                ParamOption("disgusted", "emotion.disgusted"),
            ],
        ),
        ParamDefinition(
            key="emotion_intensity",
            label_i18n="param.emotion_intensity.label",
            description_i18n="param.emotion_intensity.description",
            param_type=ParamType.SLIDER,
            group=ParamGroup.BASIC,
            default=0.5,
            min=0.0,
            max=1.0,
            step=0.1,
            affects_quality=True,
        ),
        ParamDefinition(
            key="speed",
            label_i18n="param.speed.label",
            description_i18n="param.speed.description",
            param_type=ParamType.SLIDER,
            group=ParamGroup.ADVANCED,
            default=1.0,
            min=0.5,
            max=2.0,
            step=0.1,
            affects_speed=True,
        ),
        ParamDefinition(
            key="normalize",
            label_i18n="param.normalize.label",
            description_i18n="param.normalize.description",
            param_type=ParamType.CHECKBOX,
            group=ParamGroup.ADVANCED,
            default=True,
        ),
        ParamDefinition(
            key="seed",
            label_i18n="param.seed.label",
            description_i18n="param.seed.description",
            param_type=ParamType.SEED,
            group=ParamGroup.ADVANCED,
            default=-1,
            min=-1,
            max=2**31 - 1,
            step=1,
        ),
    ],
)


# ---------------------------------------------------------------------------
# 引擎注册表
# ---------------------------------------------------------------------------

_ENGINE_UI_REGISTRY: dict[str, EngineUIData] = {
    VOXCPM2_UI_DATA.engine_id: VOXCPM2_UI_DATA,
    INDEXTTTS2_UI_DATA.engine_id: INDEXTTTS2_UI_DATA,
}


def register_engine_ui(ui_data: EngineUIData) -> None:
    """注册引擎 UI 数据。

    Args:
        ui_data: 引擎 UI 数据实例。
    """
    _ENGINE_UI_REGISTRY[ui_data.engine_id] = ui_data


def get_engine_ui(engine_id: str) -> EngineUIData | None:
    """获取指定引擎的 UI 数据。

    Args:
        engine_id: 引擎 ID。

    Returns:
        EngineUIData 实例，不存在时返回 None。
    """
    return _ENGINE_UI_REGISTRY.get(engine_id)


def get_all_engine_uis() -> list[EngineUIData]:
    """获取所有已注册引擎的 UI 数据，按 tab_order 排序。

    Returns:
        EngineUIData 列表。
    """
    engines = list(_ENGINE_UI_REGISTRY.values())
    engines.sort(key=lambda e: e.tab_order)
    return engines


def get_engine_ids() -> list[str]:
    """获取所有已注册引擎的 ID 列表。

    Returns:
        引擎 ID 列表。
    """
    return list(_ENGINE_UI_REGISTRY.keys())
