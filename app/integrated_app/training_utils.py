"""训练通用工具函数模块。

本模块提供 LoRA 微调训练过程中使用的通用辅助工具函数，包括：
- 模型参数冻结/解冻工具
- LoRA 权重注入与提取
- 梯度裁剪与学习率调度辅助
- 训练指标计算与格式化
- 音频/文本预处理工具
- 显存监控与清理辅助
- 日志格式化工具

当前实现说明：
    训练相关工具函数目前分散在以下位置：
    - ``training/accelerator.py``：设备、精度、分布式训练相关工具
    - ``training/data.py``：数据加载、音频读取、tokenize 工具
    - ``training/config.py``：配置解析与校验工具
    - ``gpu_utils.py``：GPU 显存监控与清理工具
    本模块预留作为未来训练工具函数的统一汇聚位置，便于跨模块复用。

相关模块：
    - ``training/`` 子包：训练核心实现
    - ``gpu_utils.py``：GPU 显存工具
    - ``utils.py``：项目通用工具函数
"""

from __future__ import annotations

import logging

logger = logging.getLogger("tts_multimodel.training_utils")
