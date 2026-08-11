# Contributing to TTS MultiModel

Thank you for your interest in contributing to TTS MultiModel — a multi-model TTS platform (VoxCPM2, IndexTTS 2.0, dots.tts).

This document gives a short "10-minute quick start" to get contributors productive, and a concise reference for common contribution tasks.

---

## Quick Start (10 minutes)

1. Clone the repository

```bash
git clone https://github.com/ReSerendipity/TTS_MultiModel.git
cd TTS_MultiModel
```

2. Run with Docker (one-line start)

```bash
# Build + run (example)
docker compose up --build -d
# Or use provided script (Windows)
start.bat
```

3. Create a branch for your change

```bash
git checkout -b fix/short-description
# make changes, run tests, then push
git add .
git commit -m "fix(docs): update short description"
git push origin fix/short-description
```

4. Open a Pull Request using the provided template.

---

## Development (local)

Prerequisites
- Python 3.10+ (3.12 recommended)
- Optional GPU (NVIDIA CUDA or Apple MPS) for model testing
- Git

Install (dev)

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
pip install -e ".[dev]"
```

Run tests (skip GPU tests locally if you don't have GPU)

```bash
pytest tests/ -v -k "not gpu and not cuda and not vram" -m "not integration"
```

Lint/format

```bash
ruff check bin/integrated_app/ scripts/
ruff format bin/integrated_app/ scripts/
```

---

## How to File Good Issues

- Bug reports: include environment (OS, Python, GPU), steps to reproduce, expected vs actual behavior, and logs (logs/app.log).
- Feature requests: describe the use case, proposed solution, and any alternatives.

Use the provided issue templates (bug_report / feature_request).

---

## Pull Request Checklist

- Use a descriptive title and include a short summary in the PR body.
- Link related issues using `Closes #<issue>` when appropriate.
- Add tests for new behavior where feasible.
- Run tests & linters locally before opening the PR.
- Follow Conventional Commits for commit messages (`feat:`, `fix:`, `docs:`, etc.).

---

## Labels and Their Meaning

- good-first-issue — Suitable for newcomers (small docs/bug fixes)
- help-wanted — Tasks where maintainers welcome community help
- enhancement — New features / improvements
- bug — Bug reports
- documentation — Docs-only changes
- discussion-needed — Complex proposals that need discussion first

(These labels are recommended and will be created in the repository settings.)

---

## Contributing translations

Add JSON translation files under `bin/integrated_app/locales/` and follow the existing format. Submit a PR with the new locale file.

---

## Architecture & Large Changes

For major changes (new engines, large refactors):
1. Open an Issue to discuss the approach and design.
2. Wait for maintainer feedback.
3. Break large work into smaller, reviewable PRs.

---

## License

By contributing, you agree your contributions are licensed under the Apache License 2.0 (see LICENSE).

## DCO (Developer Certificate of Origin)
## DCO (Developer Certificate of Origin)

This project requires all contributors to sign their commits with the
Developer Certificate of Origin (DCO). This attests that you have the right
to submit the work under the project's license.

### How to Sign

Add the `-s` (or `--signoff`) flag to your git commit:

```bash
git commit -s -m "feat(voxcpm2): add streaming generation support"
```

This adds a `Signed-off-by: Your Name <your.email@example.com>` line to the
commit message, which serves as your DCO attestation.

### DCO Full Text

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.

Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

### Verification

PRs will be checked for DCO compliance. Commits without a `Signed-off-by` line
will be rejected. If you forgot to sign a commit, you can amend it:

```bash
git commit --amend -s --no-edit
```

## Getting Help

- **Issues**: For bugs and feature requests
- **Discussions**: For questions and general discussion
- **Code Review**: Maintainers will review PRs within a reasonable timeframe
---

Thank you for contributing — the community makes this project better!


---

## 提交前必做（本地门禁）

> 目标：让每一次提交都能顺利通过 CI，而不是反复修。

### 安装 git hooks（一次即可）

``powershell
powershell -ExecutionPolicy Bypass -File scripts/install-hooks.ps1
``

之后每次 git push 前自动跑**快检**（ruff / 格式 / compileall 语法 / UTF-8 编码扫描），不过会阻止推送。也可手动：

``bash
python scripts/check_local.py          # 快检（秒级）
python scripts/check_local.py --full   # 快检 + 全量 pytest
``

> CI 是唯一权威门禁。git push --no-verify 可绕过（不推荐）。

### 编码卫生（防乱码）

- 所有源码/文本文件必须为 UTF-8 无 BOM（.gitattributes 已统一 LF 行尾）
- 禁止用第三方编码转换工具批量改写源文件后直接提交（曾导致中文乱码 SyntaxError）
- 本地检查会自动扫描全部被跟踪文本文件的 UTF-8 合法性

### 新增依赖

- 运行依赖 → equirements.txt；测试/开发依赖 → equirements-dev.txt（TTS 可并入 requirements 或建 dev 文件）
- 不要只 pip install 后就不管：CI 从干净环境只装 requirements，漏写必红
- 测试工具链尽量固定版本（防漂移）；TTS 的 playwright 已固定 1.62.0

### 覆盖率门槛

- 只在 CI 判定（跨平台数值有差异，本地不判）；CI 红在覆盖率时补测试而不是调门槛

### CI 红了先看什么

| 现象 | 常见根因 | 处理 |
|---|---------|------|
| cancelled | 连续 push 取消旧 run | **不是失败**，看最新 run |
| ruff/black 红 | 没跑本地门禁 | python scripts/check_local.py 修复后重推 |
| mypy 红 | 类型错误 | 本地 python -m mypy bin/integrated_app 先修 |
| pytest 红 | 测试失败/缺依赖 | 本地 --full 复现；缺依赖补 requirements |
| 覆盖率红 | 新代码没测 | 补测试 |
| SyntaxError/乱码 | 编码损坏 | 本地 UTF-8 扫描定位修复 |
| E2E 视觉回归红（TTS） | UI 改动未更新 baseline | 触发 Update Baselines 工作流（见下） |
| E2E 超时取消 | 测试量大/个别慢 | 日志定位慢测试；必要时提高 job 超时 |

### 视觉回归 baseline 更新（仅 TTS_MultiModel）

改了 UI/样式后视觉回归会红。在 GitHub Actions 页面手动触发 **Update Baselines** 工作流（CI/Linux 环境生成并自动提交）。**不要**在 Windows 本地生成 baseline 提交（渲染环境不同会反复红）。

### 提交节奏

- push 后等 CI 出结果再推下一个 commit（避免旧 run 被取消）
- 检查 CI 状态以最新 HEAD 的 run 为准
