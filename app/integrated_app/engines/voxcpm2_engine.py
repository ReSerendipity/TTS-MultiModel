"""VoxCPM2 引擎向后兼容模块。

架构说明：
    本模块是历史遗留兼容层，用于保持旧代码中 ``from engines.voxcpm2_engine import fn_voxcpm_*``
    的导入路径不变。VoxCPM2 引擎的所有实际实现已重构到 ``engines/voxcpm2/`` 子包中，
    本模块仅通过 ``from .voxcpm2 import *`` 重新导出所有公共 API，不包含任何业务逻辑。

重构历史：
    原先所有 VoxCPM2 功能（design/clone/ultimate/script/streaming/prompt/lora）
    都实现在单一的 voxcpm2_engine.py 文件中（超过 2000 行），难以维护。
    后拆分为 voxcpm2/ 子包按功能模块化，本文件保留为兼容 shim。

导出内容：
    所有在 voxcpm2/__init__.py 的 __all__ 中声明的公共符号，包括：
    - fn_voxcpm_design / fn_voxcpm_clone / fn_voxcpm_ultimate_clone
    - fn_voxcpm_script_studio / fn_voxcpm_streaming / fn_voxcpm_prompt_continue
    - fn_voxcpm_load_lora / fn_voxcpm_unload_lora
    - fn_voxcpm_set_lora_enabled / fn_voxcpm_get_lora_state
    - get_advanced_params / build_advanced_params / _advanced_kwargs

注意事项：
    新代码应直接从 ``engines.voxcpm2`` 子包导入，避免依赖此兼容层。
    兼容层将在未来大版本（3.0）中移除。
"""

from .voxcpm2 import *  # noqa: F403
