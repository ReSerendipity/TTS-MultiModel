"""TTS 引擎模块包。

架构说明：
    本包是 TTS_MultiModel 项目的引擎层，负责管理所有 TTS 引擎的注册、发现与切换。
    通过 InMemoryEngineRegistry 实现运行时动态引擎注册，支持 VoxCPM2 和 IndexTTS2
    两种引擎的无缝切换，无需重启应用。

当前支持的引擎：
    1. VoxCPM2（核心引擎）：
       - 子包：voxcpm2/
       - 功能：语音设计、语音克隆、终极克隆、剧本工坊、流式生成、Prompt 延续、LoRA
       - 显存需求：约 8-12GB（取决于模型大小和 LoRA 加载数量）
       - 兼容层：voxcpm2_engine.py（向后兼容旧导入路径）

    2. IndexTTS2（情感控制引擎）：
       - 文件：indextts2_engine.py
       - 功能：零样本语音克隆、8 维情感向量控制、时长控制
       - 显存需求：最低 6GB 显存 + 16GB 内存
       - 多后端：支持 CUDA / MPS / CPU

引擎注册机制：
    引擎通过 engine_interface.py 中的 engine_registry（InMemoryEngineRegistry 实例）
    统一注册，每个引擎包含：
    - 引擎 ID（如 "voxcpm2"、"indextts2"）
    - 引擎类（实现 TTSEngine 或 ControllableTTSEngine 协议）
    - display_name：UI 显示名称
    - vram_requirement：显存需求（GB），用于加载前预检

对外 API（fn_voxcpm_* 系列，向后兼容）：
    fn_voxcpm_design:              VoxCPM2 语音设计
    fn_voxcpm_clone:               VoxCPM2 语音克隆
    fn_voxcpm_ultimate_clone:      VoxCPM2 终极克隆
    fn_voxcpm_script_studio:       VoxCPM2 剧本工坊
    fn_voxcpm_streaming:           VoxCPM2 流式生成
    fn_voxcpm_prompt_continue:     VoxCPM2 Prompt 延续
    fn_voxcpm_load_lora:           加载 LoRA 权重
    fn_voxcpm_unload_lora:         卸载 LoRA 权重
    fn_voxcpm_set_lora_enabled:    启用/禁用 LoRA
    fn_voxcpm_get_lora_state:      获取 LoRA 状态

依赖关系：
    - engine_interface: 引擎协议定义与注册表
    - model_registry: 线程安全的模型状态单例
    - model_manager: 模型加载/卸载/切换管理
    - voxcpm2/: VoxCPM2 引擎子包
    - indextts2_engine.py: IndexTTS2 引擎实现
"""

from .voxcpm2_engine import (
    fn_voxcpm_clone,
    fn_voxcpm_design,
    fn_voxcpm_get_lora_state,
    fn_voxcpm_load_lora,
    fn_voxcpm_prompt_continue,
    fn_voxcpm_script_studio,
    fn_voxcpm_set_lora_enabled,
    fn_voxcpm_streaming,
    fn_voxcpm_ultimate_clone,
    fn_voxcpm_unload_lora,
)

__all__ = [
    "fn_voxcpm_design",
    "fn_voxcpm_clone",
    "fn_voxcpm_ultimate_clone",
    "fn_voxcpm_script_studio",
    "fn_voxcpm_streaming",
    "fn_voxcpm_load_lora",
    "fn_voxcpm_unload_lora",
    "fn_voxcpm_set_lora_enabled",
    "fn_voxcpm_get_lora_state",
    "fn_voxcpm_prompt_continue",
]

from ..engine_interface import engine_registry
from .indextts2_engine import IndexTTS20Engine, IndexTTS2Engine

engine_registry.register(
    "indextts2",
    IndexTTS2Engine,
    display_name="IndexTTS 2.5",
    vram_requirement=6.0,
)

engine_registry.register(
    "indextts20",
    IndexTTS20Engine,
    display_name="IndexTTS 2.0",
    vram_requirement=5.5,
)
