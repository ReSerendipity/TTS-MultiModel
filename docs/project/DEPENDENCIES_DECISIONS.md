# 依赖决策归档

> 本文档正式记录项目依赖管理中的关键技术决策，作为 `pyproject.toml` 和 `requirements.txt` 的设计理由参考。
>
> **最后更新**：2026-08-01

---

## 1. inflect 丢弃决策

### 背景

`inflect` 是一个英文数字转写库（如 "2" → "two"），原为 GPT-SoVITS 引擎的文本归一化依赖。

### 决策

**主动丢弃 inflect，不安装、不声明在 dependencies 中。**

### 理由

1. 中文场景不需要英文数字转写，VoxCPM2 和 IndexTTS2 的中文文本归一化不依赖 inflect
2. 减少依赖树复杂度，避免不必要的包安装
3. 如需英文完整功能，用户可手动 `pip install inflect`

### 相关文件

- `pyproject.toml` — inflect 未出现在 dependencies 中
- `docs/INTEGRATION_DECISIONS.md` §5 — 首次记录

---

## 2. tn stub 迁移决策

### 背景

dots.tts 引擎依赖 `tn` 包（Text Normalization），但 `tn` 依赖 `pynini`（C++ 编译），
在 Windows 上编译失败率高。原方案在 `site-packages/tn/` 手工注入 stub 文件。

### 决策

**将 tn stub 迁移到 `app/integrated_app/vendor/tn/`，通过 `pyproject.toml` 自动发现。**

### 理由

1. 不再依赖 out-of-tree 注入，换环境/重装不丢失
2. Git 跟踪，版本可追溯
3. `scripts/check_3engine_compat.py` 可自动验证

### 相关文件

- `app/integrated_app/vendor/tn/` — stub 文件
- `pyproject.toml` — `[tool.setuptools.packages.find]` 包含 vendor
- `scripts/check_3engine_compat.py` — 检测项 9

---

## 3. opencc-python-reimplemented 替代决策

### 背景

原 GPT-SoVITS 引擎依赖 `opencc`（C 编译版），Windows 上编译困难。

### 决策

**使用 `opencc-python-reimplemented`（纯 Python）替代，并声明在 `pyproject.toml` dependencies 中。**

### 理由

1. 纯 Python 实现，无需编译
2. 声明在 dependencies 中，`pip install` 自动安装
3. GPT-SoVITS 删除后此依赖仍保留（供文本前端使用）

### 相关文件

- `pyproject.toml` — dependencies 中声明 `opencc-python-reimplemented`

---

## 4. 版本约束策略

### 决策

**以 dots.tts 的最低版本约束为基准，满足所有引擎的依赖要求。**

### 当前版本约束

| 包 | 最低版本 | 约束来源 |
|----|----------|----------|
| torch | >= 2.5.1 | VoxCPM2 + dots.tts |
| transformers | >= 4.57.0 | dots.tts 硬性要求 |
| numpy | >= 2.2.6 | dots.tts 硬性要求（<2.5 兼容 numba） |
| pydantic | >= 2.12.5 | dots.tts 硬性要求 |

### 理由

dots.tts 的版本约束最严格，且其 `import` 成功依赖高版本 transformers API。
满足 dots.tts 即可满足 VoxCPM2 和 IndexTTS2。

### 回滚路径

如 dots.tts 出现运行时错误：
1. `dotstts_engine.py` 的 `load()` 已有 try/except 兜底
2. 使用独立 venv 安装 dots.tts 及其依赖以隔离版本冲突

---

## 5. 依赖一致性检查

### 决策

**使用 `scripts/sync_requirements.py --check` 确保 `pyproject.toml` 与 `requirements.txt` 一致。**

CI 在测试步骤前自动运行此检查，不一致时阻断。

### 相关文件

- `scripts/sync_requirements.py` — 一致性检查脚本
- `.github/workflows/ci.yml` — CI 集成

---

## 相关文档

| 文档 | 描述 |
|------|------|
| [`docs/INTEGRATION_DECISIONS.md`](INTEGRATION_DECISIONS.md) | 集成决策归档（版本策略/编译失败/tn stub/opencc/inflect） |
| [`docs/INSTALLATION_FALLBACKS.md`](INSTALLATION_FALLBACKS.md) | 安装兜底方案 |
| [`docs/PENDING_ISSUES.md`](PENDING_ISSUES.md) | 待解决问题清单 |
