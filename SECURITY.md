# Security Policy

## Supported Versions

We actively maintain the code in this repository. If you discover a security issue, please report it as outlined below.

## Reporting a Vulnerability

Please DO NOT open a public issue for security vulnerabilities. Instead, contact the maintainers privately:

- Preferred: Open a private Discussion in the repository and mark it "security" (if Discussions is enabled).
- Alternative: Open a private issue and add the label `security` (maintainers will convert it to a private channel if needed).

Include:

- A short description of the issue
- Steps to reproduce
- A minimal reproducible example (if applicable)
- A proposed mitigation

We will respond as soon as possible and coordinate a fix and disclosure plan.

## Supported Contact

Maintainers will triage reports via GitHub Discussions / Issues. If you need a direct contact, use the repository owner's public contact information on GitHub.

## Timeline

We aim to:

- Acknowledge receipt within 48 hours
- Provide a fix or mitigation plan within 14 days for high severity issues

## Disclosure

Do not publicly disclose the vulnerability until a fix or mitigation has been published.

---

# 安全架构与防护策略文档

> TTS_MultiModel 安全架构与防护策略文档。本文件由原 `.github/SECURITY.md`（漏洞报告政策）与 `docs/SECURITY.md`（安全架构）合并而来，路径均已按 `app/integrated_app/` 实际布局校正。

## 1. CSRF 防护

### 机制

项目使用 **Double-Submit Cookie** 模式的 CSRF 防护：

- 登录/首次访问时，服务器在 Cookie 中设置一个随机 token
- 所有 state-changing 请求（POST/PUT/DELETE）必须通过 `X-CSRF-Token` 请求头携带相同 token
- 服务器验证 Cookie 中的 token 与 Header 中的 token 是否匹配

### 配置

CSRF 中间件位于 `app/integrated_app/middleware/csrf.py`，默认启用。

### 豁免路径

- `GET` 请求（幂等操作）
- `/api/sse/events`（SSE 端点，使用 GET）
- API 认证路径

---

## 2. API 认证（Bearer Token）

### 机制

- 可选启用，通过 `config.yaml` 中 `api_auth.enabled` 控制
- 使用 `Authorization: Bearer <token>` 头携带 token
- 服务器使用 `hmac.compare_digest` 进行**恒定时间比较**，防止定时攻击

### 配置

```yaml
api_auth:
  enabled: false  # 默认关闭
  token: "your-secret-token"
```

---

## 3. CSRF 与 API Auth 协同工作

### 两者同时启用时的行为

当 `api_auth.enabled: true` 且 CSRF 防护同时启用时：

1. **请求处理顺序**：API Auth 中间件先于 CSRF 中间件执行
2. **认证失败**：返回 `401 Unauthorized`，不进入 CSRF 检查
3. **认证成功但 CSRF 失败**：返回 `403 Forbidden`
4. **两者都通过**：请求到达业务路由

### 设计考量

- **API 客户端**（非浏览器）：Bearer Token 认证后 CSRF 无效（无 Cookie），需在 CSRF 中间件中豁免 API 路径
- **浏览器用户**：双重防护，Bearer Token 防 CSRF（攻击者无法获取 token），CSRF Cookie 防跨站请求
- **推荐配置**：
  - 浏览器访问：启用 CSRF + 可选 API Auth
  - API 调用：启用 API Auth + 豁免 CSRF（通过 `api_auth` 路径前缀或 Header 检测）

### 常见问题

**Q: 为什么启用了 API Auth 还需要 CSRF？**

A: API Auth 防止未授权访问，CSRF 防止已登录用户被诱导发起非自愿请求。两者防护维度不同。

**Q: API 调用方如何处理 CSRF？**

A: 在 `app/integrated_app/middleware/csrf.py` 中，对携带 `Authorization: Bearer` 头的请求自动豁免 CSRF 检查（因 Bearer Token 本身已提供 CSRF 防护）。

---

## 4. 其他安全措施

### 路径穿越防护

- 音频文件服务（`app/integrated_app/routes/audio.py`）对请求路径做规范化处理
- Persona 文件操作使用 `os.path.join` + 基目录检查

### 敏感信息

- `.env` 文件不纳入 git 跟踪
- 密钥和证书文件不读取或修改
- API Token 通过 `config.yaml` 或环境变量配置

#### VAPID 密钥管理（P1 安全修复）

> **废弃通知**：项目早期 `.env` 中的 VAPID 私钥已被视为**已污染并废弃**。
> 任何克隆者如需使用 Web Push 通知功能，必须自行生成新的密钥对。

生成新 VAPID 密钥对：

```bash
python -c "from py_vapid import Vapid; v = Vapid(); v.generate_keys(); v.save_key('vapid_private.pem'); v.save_public_key('vapid_public.pem')"
```

- **公钥**：可放入 `.env` 的 `VAPID_PUBLIC_KEY` 变量
- **私钥**：应存放在安全的密钥管理系统中，**不要**直接放入 `.env`

pre-commit 已配置 `detect-private-key` 与自定义 `forbid-private-key-in-env` hook，
禁止提交包含 `BEGIN PRIVATE KEY` 等标记的文件。

### 异常处理

- 所有异常通过 `app/integrated_app/middleware/error_handler.py` 统一捕获
- 返回标准化 JSON 格式：`{"code": "...", "message": "...", "detail": {...}}`
- 内部错误信息不直接暴露给客户端

---

## 5. 安全加固建议

1. 上传文件名规范化：使用 `uuid.uuid4().hex` 重命名上传的参考音频
2. 权重下载完整性校验：增加 SHA256 校验（见 PENDING_ISSUES P3-7）
3. 速率限制：对 API 端点增加请求频率限制
4. HTTPS：生产环境必须启用 SSL/TLS

---

## 相关文件

| 文件 | 职责 |
|------|------|
| `app/integrated_app/middleware/csrf.py` | CSRF 双重提交 Cookie 防护 |
| `app/integrated_app/auth.py` | Bearer Token API 认证中间件 |
| `app/integrated_app/middleware/error_handler.py` | 全局异常处理 |
| `app/integrated_app/routes/audio.py` | 音频文件服务（路径穿越防护） |
| `config.yaml` | 认证配置 |