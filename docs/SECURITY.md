# Security Policy

> **本文件不是 GitHub 识别的策略入口**：GitHub 只读取仓库根 `SECURITY.md` 与 `.github/SECURITY.md`（本仓根目录无该文件），
> 对外生效的策略是 **`.github/SECURITY.md`**（含中英双语、私密报告渠道、SLA）；本文件仅保留英文速查摘要，
> 两者如有出入，**以 `.github/SECURITY.md` 为准**。

## Supported Versions

支持版本范围以 `.github/SECURITY.md` 的 Supported Versions 表为准（当前登记：v2.2.x ✅ 安全更新 / < v2.2 ❌ 不再支持；该表与最新版本 v2.7.0 的对应关系待 owner 复核）。

## Reporting a Vulnerability

请走 **GitHub Security → Report a vulnerability**（私密漏洞报告），**不要**开公开 Issue。
完整渠道与联系方式见仓库根 `.github/SECURITY.md`。

We aim to acknowledge receipt within 48 hours and provide a fix or mitigation plan within 14 days for high severity issues (see Timeline in `.github/SECURITY.md`).

## Security Measures

- Ed25519 signed integrity manifest
- gitleaks secret scanning (pre-commit + CI)
- Dependabot dependency updates
- CodeQL SAST analysis
- Path traversal protection
- CSRF middleware

See SECURITY_MATRIX.md for details.
