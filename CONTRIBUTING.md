# 贡献指南（CONTRIBUTING）

感谢关注 TTS_MultiModel！本文件面向**人类贡献者**，给出从克隆到合并的最短路径。
仓库另有 AI 协作协议 `AGENTS.md` 与治理文档（FIX_LOG / docs/agents/），属维护者本地
治理层、未随仓库分发；其纪律要求在下列章节中已摘录为红线。

> 创建：2026-09-15（此前本仓无 CONTRIBUTING，参考 SeedVR2-lite 同名文件补齐）。

## 1. 环境搭建

```bat
:: Windows
install.bat          :: 建 .venv（3.12.10），装依赖
start.bat            :: 启动 http://127.0.0.1:7869
```

Linux/macOS 使用 `install.sh` / `start.sh`。模型权重（VoxCPM2 / IndexTTS 2.5）按 README「模型下载」自备——IndexTTS2 权重许可为 bilibili Model Use License，商用需书面授权（见 NOTICE）。

## 2. 提交纪律（红线）

- **Conventional Commits**：`feat:` / `fix:` / `docs:` / `chore:` …（release-please 据此自动发版）。
- **DCO 签名**：`git commit -s`；`.githooks/` 已启用（`core.hooksPath=.githooks`），commit-msg 校验缺签名阻断；换机器先执行 `git config core.hooksPath .githooks`。
- **禁止** force push、禁止 `--no-verify`（除非维护者明确要求）。

## 3. 本地质量门禁

```bat
pre-commit run --all-files   :: 21 个钩子（ruff / mypy 基线棘轮 / 结构守卫 / gitleaks 等）
pytest                       :: 覆盖率门禁 --cov-fail-under=40
python scripts/check_spec_refs.py   :: 文档引用完整性，退出码须为 0
```

CI（3 系统 × 4 版本矩阵）含 typecheck（组织级 reusable workflow）、覆盖率、gitleaks、Trivy 容器扫描（HIGH/CRITICAL 阻断）；push 后用 `gh run view <id> --json conclusion` 确认终态。

## 4. 发版

release-please 自动维护 `CHANGELOG.md` 与 `pyproject.toml` 版本；**发版后需人工补齐 `config.yaml` 的 `version` 字段**（前端缓存参数依赖它，见 AGENTS.md 头部说明）。

## 5. 其他

- 参与贡献请同时遵循[组织级贡献指南](https://github.com/ReSerendipity/.github/blob/main/CONTRIBUTING.md)。
- 安全漏洞不要开公开 Issue：走 `.github/SECURITY.md` 的私密披露渠道（Security Advisory / 邮箱）。