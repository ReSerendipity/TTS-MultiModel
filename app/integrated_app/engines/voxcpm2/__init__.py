"""VoxCPM2 引擎子包。

架构说明：
    本包实现 TTS_MultiModel 项目的核心 TTS 引擎 —— VoxCPM2（基于 VoxCPM 第二代
    语音大模型）。采用模块化设计，将不同生成模式拆分为独立子模块，通过 engine.py
    中的 VoxCPM2Engine 类统一对外实现 TTSEngine / ControllableTTSEngine 协议。

子模块概览：
    - engine.py: VoxCPM2Engine 主类，实现引擎协议，统一入口
    - design.py: 语音设计模式（文本描述 → 语音，零样本随机音色）
    - clone.py: 语音克隆模式（参考音频 + 文本 → 克隆语音）
    - ultimate.py: 终极克隆模式（完整参数控制：cfg/denoise/steps/seed）
    - script.py: 剧本工坊（多角色对话批量生成，支持静音/停顿指令）
    - streaming.py: 流式生成（长文本分段流式输出，TTFB < 2s）
    - prompt.py: Prompt 延续模式（参考音频+文本对，续写保持朗读风格）
    - lora.py: LoRA 微调权重管理（加载/卸载/启用/禁用/多 LoRA 混合）
    - decorators.py: 生成上下文装饰器（@with_generation_context）
    - _base.py: 共享工具函数、高级参数构建、日志与进度管理器引用

对外 API（向后兼容 fn_voxcpm_* 系列）：
    fn_voxcpm_design:              语音设计入口
    fn_voxcpm_clone:               语音克隆入口
    fn_voxcpm_ultimate_clone:      终极克隆入口
    fn_voxcpm_script_studio:       剧本工坊入口
    fn_voxcpm_streaming:           流式生成入口
    fn_voxcpm_prompt_continue:     Prompt 延续入口
    fn_voxcpm_load_lora:           加载 LoRA
    fn_voxcpm_unload_lora:         卸载 LoRA
    fn_voxcpm_set_lora_enabled:    启用/禁用 LoRA
    fn_voxcpm_get_lora_state:      获取 LoRA 状态

工具函数：
    get_advanced_params:           获取当前高级生成参数
    build_advanced_params:         构建高级参数字典
    _advanced_kwargs:              返回高级参数 kwargs（内部使用）

依赖关系：
    - model_registry: 获取已加载的 voxcpm_model 实例
    - model_manager: 生成锁、追踪器、进度管理器
    - gpu_utils: OOM 检测、显存释放
    - exceptions: 统一异常层次
    - audio_processing: 音频后处理（响度归一化等）
"""

from ._base import _advanced_kwargs, build_advanced_params, get_advanced_params
from .clone import fn_voxcpm_clone
from .design import fn_voxcpm_design
from .lora import fn_voxcpm_get_lora_state, fn_voxcpm_load_lora, fn_voxcpm_set_lora_enabled, fn_voxcpm_unload_lora
from .prompt import fn_voxcpm_prompt_continue
from .script import fn_voxcpm_script_studio
from .streaming import fn_voxcpm_streaming
from .ultimate import fn_voxcpm_ultimate_clone

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
    "get_advanced_params",
    "build_advanced_params",
    "_advanced_kwargs",
]
