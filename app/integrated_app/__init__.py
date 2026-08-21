"""TTS_MultiModel 集成应用包 —— 多引擎 TTS 服务的核心入口。

本包是 TTS_MultiModel 项目的核心，提供完整的多引擎文本转语音服务：

**架构概览**
    - 双引擎支持：VoxCPM2（语音设计/克隆/剧本工坊/LoRA）+ IndexTTS2（情感控制）
    - FastAPI Web 服务：HTMX 前端 + REST API + OpenAI 兼容 API + SSE 事件流
    - GPU 资源管理：单 Worker 串行、显存预检、LRU 模型缓存、OOM 自动降级
    - 音色管理：Persona 角色库、嵌入缓存、导入导出

**启动链路**
    start.bat → app/clean_launch.py → app/integrated_app/__init__.py:run_integrated()
    → app_server.py:create_app() → uvicorn.run()

**主要子模块**
    - app_server: FastAPI 应用创建、生命周期、中间件、路由注册
    - config / config_models: YAML 配置解析与 Pydantic 模型
    - model_manager / model_registry: 模型加载/卸载/切换与状态管理
    - engine_interface / engines/: TTS 引擎协议与具体实现
    - routes/: HTTP API 路由（页面、模型、生成、系统等）
    - middleware/: 请求ID、CSRF、认证、异常处理中间件
    - training/: VoxCPM2 LoRA 微调训练模块

**对外接口**
    通过 :func:`run_integrated` 函数启动服务，采用延迟导入避免启动时
    加载所有重型依赖（如 PyTorch、模型权重）。

Example:
    >>> from app.integrated_app import run_integrated
    >>> run_integrated("127.0.0.1", 7869)
"""


def run_integrated(ip: str, port: int) -> None:
    """启动集成 TTS 应用服务器。

    采用延迟导入模式，在函数内部才导入 app_server 模块，
    避免启动脚本加载时立即初始化 PyTorch 等重型依赖，加快启动速度。

    Args:
        ip: 服务监听的 IP 地址，通常为 "127.0.0.1" 或 "0.0.0.0"。
        port: 服务监听的端口号，默认为 7869。

    Note:
        此函数会阻塞当前线程直到服务器终止（Ctrl+C 或进程退出）。
        调用前应确保工作目录正确、config.yaml 存在且模型路径配置合理。
    """
    from .app_server import run_server

    return run_server(ip, port)


__all__ = ["run_integrated"]
