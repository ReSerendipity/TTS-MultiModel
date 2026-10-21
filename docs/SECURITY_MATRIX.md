# TTS_MultiModel 安全矩阵

> 本文档列出项目的安全控制措施，供安全审计和合规检查参考。

## 身份与访问控制

| 控制项 | 实现方式 | 状态 |
|---|---|---|
| 分支保护 | GitHub Branch Protection（main 受保护、五项必需检查：`Lint (ruff)` / `Test (pytest) (3.12, ubuntu-latest)` / `Typecheck (mypy ratchet)` / `DCO Check` / `release-gate`，口径见 tracked 镜像 `docs/ci/branch-protection.json`（2026-10-07 按 live 复核）；仓库所有者 admin 直推豁免，见 `docs/DOD.md` §0b） | ✅ |
| DCO 签名 | .githooks/commit-msg 强制 Signed-off-by（CI 侧 `.github/workflows/dco.yml`） | ✅ |
| 代码审查 | `.github/CODEOWNERS` 已配置，PR 按 owner 规则自动指派 review；`require_code_owner_reviews=false`（非强制，PR 仍需 1 个 approval） | ✅ |

## 代码安全

| 控制项 | 实现方式 | 状态 |
|---|---|---|
| 密钥扫描 | gitleaks（pre-commit + CI） | ✅ |
| 依赖漏洞扫描 | pip-audit（`.github/workflows/security.yml`）/ Dependabot + Trivy（`docker-build.yml` / `docker-publish.yml`） | ✅ |
| SAST | CodeQL（`.github/workflows/codeql.yml`，非必需检查） | ✅ |
| 静态分析 | ruff（lint + format 门禁）+ mypy（棘轮基线 `.ci/mypy_baseline.txt`） | ✅ |
| 完整性校验 | Ed25519 签名清单（`app/integrated_app/security/integrity_manifest.json` + `app/integrated_app/security/integrity_selfcheck.py`，公钥 `manifest_signing_public_key.pem`） | ✅ |

## 数据安全

| 控制项 | 实现方式 | 状态 |
|---|---|---|
| 敏感配置不入库 | .env.example 模板，真实配置 gitignore | ✅ |
| 路径遍历防护 | path_guard / CSRF middleware | ✅ |
| 输入校验 | 参数验证 / schema 校验 | ✅ |

## 发布安全

| 控制项 | 实现方式 | 状态 |
|---|---|---|
| 发布签名 | 不采用 GPG 分离签名（2026-10-18 裁决，`gpg-signed-release.yml` 已删除）；签名链为 Tauri ed25519 更新器签名 + `SHA256SUMS.txt`，见 `docs/release-governance.md` §4 | ✅ |
| 制品不可变 | Immutable Release Artifacts（semver tag + `SHA256SUMS.txt`，无 floating `latest`） | ✅ |

---
*最后更新：2026-10-21*
