# Security Policy

## 支持的版本

| 版本 | 支持状态 |
|------|----------|
| v2.2.x | ✅ 安全更新 |
| < v2.2 | ❌ 不再支持 |

## 报告安全漏洞

如果发现安全漏洞，请**不要**通过公开 Issue 报告。请通过以下方式私密联系维护者：

- 邮件：`security@tts-multimodel.local`（占位，请替换为实际联系方式）
- 或在 GitHub 上通过 **Security → Report a vulnerability** 提交私密报告

报告时请包含：
1. 漏洞描述与影响范围
2. 复现步骤（PoC）
3. 受影响版本
4. 建议的修复方案（如有）

## 安全架构概览

TTS_MultiModel 内置多层安全防护：

- **鉴权**：API Token + 可选 CSRF 防护（`security/auth.py`、`security/csrf.py`）
- **速率限制**：滑动窗口全局限流 + 克隆专用 1h 窗口（`middleware/rate_limit.py`）
- **内容安全**：6 类正则检测（暴力/仇恨/自残/色情/违法/骚扰）+ 同音字/拼音/外文变体（`security/content_safety.py`）
- **水印**：DCT 频域不可感知水印 + HMAC 密钥版 v3（`watermark.py`）
- **PII 加密**：历史记录文本字段 Fernet 加密 + 密钥自动管理（`history_db.py`）
- **审计日志**：操作审计 + 10MB 轮转（`security/audit.py`）
- **完整性校验**：核心模块 SHA-256 自检 + 模型权重哈希校验（`security/integrity_check.py`、`security/integrity_selfcheck.py`）
- **AI 标识**：响应头 `X-AI-Generated` + UI 徽标 + 可选音频提示音

## 安全更新流程

1. 漏洞报告 → 24 小时内确认
2. 根因分析 → 制定修复方案
3. 修复代码 → 补充回归测试
4. 发布补丁版本 → 公告影响范围

## 依赖安全

- 依赖唯一声明源：`pyproject.toml`
- CI 集成 Trivy 容器扫描（CRITICAL/HIGH 阻断）
- pre-commit 钩子：ruff / mypy / check-engine-compat 等 14 项

---

*本文件由 P3 安全整改创建（2026-09-05），修复 docs/SECURITY.md 指向不存在的根级文件的断链问题。*
