"""路由包初始化模块 — 自动发现与注册机制。

架构说明：
    本包采用 ``pkgutil`` 自动发现机制，``app_server.py`` 在应用启动时
    遍历 ``routes/`` 目录下所有子模块（含 ``generate/``、``system/`` 子包），
    自动导入并调用 ``include_router(router)`` 注册到 FastAPI 主应用，
    无需手动维护路由列表。新增路由文件时只需在文件内定义 ``router = APIRouter(...)``
    即可自动注册，降低遗漏风险。

路由组织结构（按功能域划分）：
    - pages.py:      根路径 ``/`` — 首页渲染、favicon、下载引导
    - tabs.py:       HTMX 标签页懒加载 ``/tab/{tab_name}``
    - model.py:      模型管理 API ``/api/model/*``
    - audio.py:      音频服务与历史记录 ``/api/audio/*``、``/api/history/*``
    - sse.py:        统一 SSE 事件流 ``/api/sse/events``
    - persona.py:    音色管理 API ``/api/persona/*``
    - training.py:   LoRA 训练管理 API ``/api/training/*``
    - generate/:     生成相关路由子包
        - voxcpm2/:     VoxCPM2 引擎生成接口（design/clone/script/streaming）
        - indextts2/:   IndexTTS2 引擎生成接口（synthesize）
        - utils.py:     生成路由共享工具
    - system/:       系统管理路由子包
        - health.py:    健康检查与统计 ``/api/system/*``
        - gpu.py:       GPU 状态与显存信息 ``/api/system/*``
        - logs.py:      操作日志查询 ``/api/system/*``
        - settings.py:  运行时设置 ``/api/system/*``

中间件与横切关注点：
    - CSRF 防护：所有 state-changing 请求（POST/PUT/DELETE）由
      ``middleware/csrf.py`` 统一校验 ``X-CSRF-Token`` 请求头
    - API 认证：Bearer Token 认证由 ``auth.py`` 中间件处理（可选开启）
    - 异常处理：所有路由异常通过 ``middleware/error_handler.py`` 统一捕获，
      返回标准化 JSON 错误响应
    - 请求 ID：``middleware/request_id.py`` 为每个请求分配唯一 ID 并注入日志上下文

约定：
    - 每个路由模块必须定义模块级 ``router: APIRouter`` 对象供自动发现
    - 只读端点使用 GET，写操作使用 POST/DELETE
    - 所有响应统一使用 JSONResponse 或 FileResponse/StreamingResponse
    - 文件路径参数必须执行白名单 + realpath 前缀校验，防止路径遍历攻击
"""
