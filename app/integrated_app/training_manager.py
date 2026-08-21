"""训练任务管理器模块。

本模块为 LoRA 微调训练提供高层任务管理接口，负责协调训练生命周期管理、
子进程调度、状态同步等功能。

当前实现说明：
    训练任务的 REST API 路由和子进程管理逻辑目前位于 ``routes/training.py`` 中，
    通过 asyncio subprocess 调用 ``scripts/train_voxcpm_finetune.py`` 训练脚本。
    本模块预留作为未来训练管理器重构后的核心逻辑位置，用于：
    - 封装训练任务的创建/启动/停止/状态查询逻辑
    - 统一管理训练配置、checkpoint、日志
    - 提供与 TrainingState / TrainingTracker / StateManager 的高层集成接口
    - 支持训练队列、并发控制等高级功能

相关模块：
    - ``training/`` 子包：训练核心实现（加速器、数据、打包、状态、追踪）
    - ``routes/training.py``：训练 API 路由（当前子进程管理实现）
    - ``scripts/train_voxcpm_finetune.py``：训练入口脚本
"""

from __future__ import annotations

import logging

logger = logging.getLogger("tts_multimodel.training_manager")
