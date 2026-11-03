# 架构 — TTS-MultiModel

> 治理定位：`delivery=release`、`lifecycle=active`（`governance/desired-state.json`）。
> 本文按 2026-11-03 的实际目录结构生成；数据流与边界须由维护者在实现变更时同步复核。

## 1. 定位

**多模型语音合成平台 · Multi-Model Text-to-Speech Platform**

## 2. 组件与依赖方向

```text
应用层：app/ (1125)、desktop/ (52)
服务层：launcher/ (1)
工具层：scripts/ (93)
测试层：tests/ (206)、perf/ (8)、benchmarks/ (2)
支撑层：docs/ (36)、.github/ (35)
其他层：.githooks/ (10)、configs/ (9)、deploy/ (5)、.ci/ (2)、baselines/ (2)、demo/ (2)
根级层：根级文件 (25)
```

| 组件 | 目录 | 文件数 | 代表文件 |
|---|---|---|---|
| `app` | `app/` | 1125 | `app/SHA256SUMS.known-good`, `app/clean_launch.py`, `app/general_settings.json` |
| `tests` | `tests/` | 206 | `tests/__init__.py`, `tests/adversarial_guardrail_corpus.json`, `tests/benchmarks/GATE_POLICY.md` |
| `scripts` | `scripts/` | 93 | `scripts/add_golden_case.py`, `scripts/assemble_installer_data.ps1`, `scripts/assemble_release_staging.ps1` |
| `desktop` | `desktop/` | 52 | `desktop/README.md`, `desktop/package.json`, `desktop/src-tauri/Cargo.lock` |
| `docs` | `docs/` | 36 | `docs/CODING_STANDARDS.md`, `docs/DOD.md`, `docs/EXCEPTION_SUPPRESSION_LEDGER.md` |
| 根级文件 | `/` | 25 | `.dockerignore`, `.editorconfig`, `.env.example` |
| `.githooks` | `.githooks/` | 10 | `.githooks/README.md`, `.githooks/commit-msg`, `.githooks/install.sh` |
| `configs` | `configs/` | 9 | `configs/coverage_budget.toml`, `configs/golden_baseline.voxcpm2.ref.json`, `configs/golden_cases.multimodel.json` |
| `perf` | `perf/` | 8 | `perf/__init__.py`, `perf/cold-start.py`, `perf/generation-benchmark.py` |
| `deploy` | `deploy/` | 5 | `deploy/kubernetes/README.md`, `deploy/kubernetes/deployment.yaml`, `deploy/kubernetes/pvc.yaml` |
| `.ci` | `.ci/` | 2 | `.ci/mypy_baseline.md`, `.ci/mypy_baseline.txt` |
| `baselines` | `baselines/` | 2 | `baselines/gpu-mttr-baseline.json`, `baselines/streaming-ttfb-baseline.json` |
| `benchmarks` | `benchmarks/` | 2 | `benchmarks/README.md`, `benchmarks/baseline.json` |
| `demo` | `demo/` | 2 | `demo/README.md`, `demo/index.html` |
| `launcher` | `launcher/` | 1 | `launcher/requirements-small.txt` |

依赖方向：入口层（应用 / 页面）调用领域层（业务模块），领域层依赖基础设施层（脚本、配置、模型资产）；反向引用不允许。

## 3. 数据流

文本输入 → 多模型调度 → 音频合成 → 下载；桌面壳负责安装与运行时管理。

## 4. 外部依赖

| 依赖 | 用途 | 不可用时 |
|---|---|---|
| Python 依赖 | 合成后端 | 无法生成音频 |
| Node.js / npm | 桌面壳 | 仅剩 Web 端 |

## 5. 边界与非目标

- 发布链只走 §10：远端优先、tag 驱动、资产附 `SHA256SUMS.txt`。
- 分发产物由 CI 生成，不入库。
- 不入库：密钥 / token / 口令 / 连接串、本机绝对路径、`local/` 与 `AGENTS.md`、构建产物。
- 治理正文只在 `ReSerendipity/.github/governance`，本仓不复制规则正文。

## 6. 变更影响面

| 改什么 | 必须同步 | 由谁校验 |
|---|---|---|
| 目录结构 / 入口 | 本文「组件与依赖方向」与「数据流」 | 维护者 |
| CI 与门禁 | 本仓 `README.md` 的测试命令；治理仓 §20 远端载体口径 | `audit-drift.py` |
| 依赖清单 | 锁文件与 `.github/dependabot.yml` 的生态段 | Dependabot / `lock-consistency` |
| README 展示图 | §8.1 真实性红线（图必须来自真实运行） | 治理仓 `readme-image-provenance` |
