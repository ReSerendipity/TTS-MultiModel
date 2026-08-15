# Contributing to TTS MultiModel

Thank you for your interest in contributing to TTS MultiModel — a multi-model TTS platform (VoxCPM2, IndexTTS 2.5, dots.tts).

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

- 运行依赖 → requirements.txt；测试/开发依赖 → requirements-dev.txt（TTS 可并入 requirements 或建 dev 文件）
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


### E2E（Playwright）编写纪律

> 2026-08 三仓 CI 修复共修复 231 个 E2E 测试，以下每条都对应一次真实失败。

1. **禁止假等待**：`waitForFunction(() => true)` 是无意义等待，立即断言异步状态
   （SSE 连接、GPU 探测、mock 渲染）必 flaky。用 `expect.poll(真实条件)` 或
   `waitForFunction(真实条件)` 等待**实际状态**（元素出现 / 计数变化 / 文本非空）。
2. **选择器必须与产品当前 DOM 对齐**：产品重构后测试还在找旧 ID/类
   （如 `.sv-restore-layout` → 实际是 `.sv-restore-workspace > .sv-restore-main + .sv-param-sidebar`；
   model status 实际是 `#statusModel`）。改产品前先扫测试引用的全部选择器。
3. **断言与产品实现语义一致**：产品刻意不跟随 `prefers-color-scheme`（默认暗色主题，
   避免与手动选择冲突）——测试就不该断言"跟随系统"；连接 error 后产品清空
   `__sseConnection` 并指数退避重连——测试就该条件断言。先读产品代码再写断言。
4. **首次访问弹窗（onboarding）**：restore 页无 `sv_onboarding_seen_v2` 标记时弹
   引导层，会挡住按钮点击。测试在 goto 前用
   `page.addInitScript(() => localStorage.setItem('sv_onboarding_seen_v2', '1'))` 预置。
5. **SSE mock**：`route.fulfill` 是一次性响应，EventSource 读完 body 必然 error →
   产品走重连。断言"请求被发出 / 事件被处理"，不要断言"连接常驻"。
6. **视觉回归基线**：改 UI 后本地基线会旧（本地跑必红）。基线只能在 CI（Linux）
   重新生成（Update Baselines 工作流 / e2e.yml 引导步骤，见下文），**不要**在
   Windows 本地生成并提交基线（渲染环境不同会导致 CI 反复红）。
7. **axe 对比度扫描前冻结动画**：`addStyleTag({ content: '* { animation: none !important; transition: none !important; }' })`
   + 等待 200ms，否则入场动画中途采样会误报对比度。
8. **改完就本地重跑受影响 spec**：`python scripts/check_local.py --e2e --e2e-specs specs/xxx.spec.ts`
   （会自动检查 fastapi/playwright 环境）。不要攒到最后一次性全量验证。

### 提交纪律

1. **绝不 `git add -A` / `git add .`**：只 add 明确文件，防止调试产物、
   临时 spec（tmp-*.spec.ts）、测试输出混进提交。
2. 提交前清理临时产物（调试脚本、复验输出、本地生成的截图基线）。
3. push 后等 CI 出结果再推下一个 commit（连续 push 触发 concurrency 取消旧 run，
   `cancelled` 不是失败，以最新 HEAD 为准）。
4. 本地服务器起不来时先区分环境失败与测试失败：`ModuleNotFoundError: fastapi`
   是 PATH 里 python 不对（用装了依赖的解释器手动起服务器，测试会复用），
   不是代码问题。

### 经验教训清单

2026-08 三仓 CI 修复的完整经验教训（20 条，分测试纪律 / 对比度专项 / 环境工具链 /
质量门禁 / 提交节奏 / 防复发补强六组）见仓库根目录 **CI-LESSONS.md**。提交前花两分钟
过一遍相关条目，能避免大部分返工。

