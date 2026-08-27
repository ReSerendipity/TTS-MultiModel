# 集成决策归档

> 本文档正式归档 dots.tts 引擎集成过程中的关键技术决策，
> 替代 `need read.txt`（私人对话总结）作为权威来源。
>
> **最后更新**：2026-08-01

---

## 1. 版本策略决策

### 实际执行（pyproject.toml）

dots.tts 优先，满足其严格版本约束：
- transformers: `>=4.57.0`（满足 dots.tts）
- numpy: `>=1.24.0`（上限放开）
- pydantic: `>=2.0`（上限放开）

### 决策原因

dots.tts 的版本约束更严格，且其 `import` 成功依赖高版本 transformers API。

### 回滚路径

如 dots.tts 出现运行时错误：
1. 在 `dotstts_engine.py` 的 `load()` 中已有 try/except 兜底（捕获 RuntimeError/AttributeError/TypeError/ImportError）
2. 使用独立 venv 安装 dots.tts 及其依赖以隔离版本冲突

---

## 2. 编译失败处理

详见 [`docs/INSTALLATION_FALLBACKS.md`](INSTALLATION_FALLBACKS.md)。

---

## 3. tn stub 包策略

- **原始方案**：在 `site-packages/tn/` 手工注入 stub 文件
- **改进方案**：迁移到 `app/integrated_app/vendor/tn/`，通过 `pyproject.toml` 自动发现
- **优势**：不再依赖 out-of-tree 注入，换环境/重装不丢失

---

## 4. opencc 替代方案

- `opencc-python-reimplemented` 已声明在 `pyproject.toml` dependencies 中
- 不再需要手动 `pip install`

---

## 5. inflect 丢弃决策

- inflect 是英文数字转写依赖
- 中文场景不需要，主动丢弃
- 需英文完整功能时可手动安装

---

## 6. 模型权重

| 引擎 | 权重大小 | 下载位置 |
|------|---------|---------|
| dots.tts | ~4.9GB (17 文件) | `model/dots.tts/` |

权重通过 `scripts/download_dotstts.py` 下载。
目前无 SHA256 校验（见 PENDING_ISSUES P3-7）。

---

## 7. 相关文档

| 文档 | 描述 |
|------|------|
| [`docs/INSTALLATION_FALLBACKS.md`](INSTALLATION_FALLBACKS.md) | 编译失败 fallback 完整文档 |
| [`docs/PENDING_ISSUES.md`](PENDING_ISSUES.md) | 待解决问题清单 |
| [`docs/adr/0001-remove-gptsovits.md`](adr/0001-remove-gptsovits.md) | 删除 GPT-SoVITS 引擎的架构决策记录 |
