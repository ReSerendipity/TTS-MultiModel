# TTS_MultiModel v2.2.1 — 安全评估整改追踪表（家族复用 Image_MultiModel 流程）

> 配套：Image_MultiModel `docs/_devarchive/SECURITY_ASSESSMENT_v2.0.0.md` 与 `docs/SECURITY_REMEDIATION_TRACKER.md`
> 整改日期：2026-09-01｜方法：将 Image_MultiModel 的「配置-实现一致性」根因门禁移植到本仓库
> 验证：`scripts/check_config_refs.py` CI 门禁 [PASS]

## 0. 结论速览

TTS_MultiModel 在家族复用前**已具备较完整的安全基础设施**，多数控制项原本就已实现并接线：

| 控制（对应 IM 评估 ID） | TTS 现状 | 处置 |
|---|---|---|
| C-01 认证 | `auth.py:APIAuthMiddleware` 已注册 `app_server.py:817`，`api_auth` 配置真实消费 | ✅ 已具备 |
| H-03 内容安全 / PII / 审计 | `security.content_safety_enabled` / `pii_encryption_enabled` / `audit_enabled` 均被代码消费 | ✅ 已具备 |
| M-03 限流 | `rate_limit` 配置 + `RateLimitMiddleware`，含可信代理 XFF | ✅ 已具备 |
| H-04 完整性 | `security/integrity_selfcheck.py` + `integrity_manifest.json` | ✅ 已具备 |
| **M-02 安全响应头** | **此前无任何 CSP/nosniff/frame-ancestors** | 🟡 **本轮补** |
| H-01 HTTPS | `server.ssl` 配置存在但注释「currently not active」，uvicorn 未读 ssl | ⚪ 可选（同 IM H-01） |

## 1. 本轮交付

1. **移植根因门禁** `scripts/check_config_refs.py`（源自 Image_MultiModel，已泛化）：
   - `is_config_like` 仅认 `get_config()` 调用根（TTS 应用配置统一经 `get_config()` 访问，避免把 `RASConfig`/`ResamplingConfig` 的局部 `config` 参数误判）；
   - 新增桥接属性白名单 `_BRIDGE_ATTRS`（pydantic_config / api_auth_dict / observability_dict / gen_defaults_dict）；
   - 去掉 Image_MultiModel 特有的 `idle_unload_minutes` 硬编码检查，改为通用 `runtime:` 双向一致性。
   - 结果：`[PASS] security 段 4 键全部被代码消费；runtime 一致`。
2. **补安全响应头中间件** `middleware/security_headers.py`（M-02）：复制 Image_MultiModel 同款，置于中间件栈最外层（`app_server.py`，RequestIDMiddleware 之前），默认下发 CSP(nosniff/object-none/frame-ancestors-none/base-uri-self) + X-Frame-Options + Referrer-Policy + COOP。经 `config.security.headers` 可关闭（TTS 当前无该子键，安全默认开启）。
3. **接入 CI**：`.github/workflows/ci.yml` lint job 新增 `Config-vs-code consistency gate` 步骤（`pip install pyyaml && python scripts/check_config_refs.py`）。

## 2. 验证

```
门禁：scripts/check_config_refs.py -> [PASS]
语法：security_headers.py / app_server.py py_compile OK
```

## 3. 保持绿通纪律（同 IM）

- `config.yaml` 的 `security:` / `runtime:` 段键必须被代码消费，否则门禁 fail。
- 移植的 `check_config_refs.py` 已针对 TTS 访问模式泛化；若后续引入新的「桥接 property」（返回子配置 dict），需同步加入 `_BRIDGE_ATTRS`，否则会被误报为未定义字段。

## 4. 可选后续（服务化部署时再补）

- **H-01 HTTPS**：令 `clean_launch.py` 的 uvicorn 按 `server.ssl` 构造 `ssl_certfile/ssl_keyfile`（与 IM 同款修复）。
- 若需可配置 CSP，可在 `SecurityConfig` 增加 `headers: HeadersConfig(enabled/csp)` 子配置。
