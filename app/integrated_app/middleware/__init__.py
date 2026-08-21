"""ASGI/Starlette 中间件包。

本模块包含 TTS_MultiModel 应用的所有 HTTP 中间件组件，在请求到达路由
handler 之前或响应返回客户端之后执行横切关注点（cross-cutting concerns）。

中间件执行顺序（在 app_server.create_app() 中注册，先注册的在外层）：
    1. :class:`RequestIDMiddleware` — 请求 ID 注入（最外层，最先执行）
       为每个请求分配 UUID4，注入 logging 上下文，响应头回写 X-Request-ID。
    2. :class:`~middleware.csrf.CSRFMiddleware` — CSRF 防护
       Double-Submit Cookie 模式，校验 state-changing 请求的 X-CSRF-Token。
    3. :class:`~auth.APIAuthMiddleware` — Bearer Token 认证（可选启用）
       恒定时间比较，防止定时攻击；未配置 token 时自动跳过。
    4. :class:`~middleware.rate_limit.RateLimitMiddleware` — API 速率限制
       防止单 IP 狂发生成请求打爆 GPU（默认 10 次/分/IP，burst=5）。
    5. CORSMiddleware — FastAPI/Starlette 内置跨域资源共享。
    6. 全局异常处理（通过 register_error_handlers 注册，非 BaseHTTPMiddleware）
       :mod:`middleware.error_handler` 统一捕获 TTSError/ValidationError/HTTPException/通用 Exception。

包含子模块：
    - :mod:`request_id` — 请求 ID 中间件与日志过滤器（RequestIDLogFilter）
    - :mod:`csrf` — CSRF 防护中间件（Double-Submit Cookie 模式）
    - :mod:`rate_limit` — API 速率限制中间件（防 GPU DoS 攻击）
    - :mod:`error_handler` — 全局异常处理器注册与统一 JSON 响应构建

设计原则：
    - Fail-closed（失败关闭）：安全中间件（CSRF/Auth）遇到异常时拒绝请求而非放行。
    - 不吞异常：非安全中间件（RequestID）的异常应记录日志但不中断链路。
    - 向后兼容：所有中间件的公开 API 保持稳定，旧测试依赖的常量名通过别名保留。
"""
