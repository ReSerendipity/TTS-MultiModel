**ADR-0001: 删除 GPT-SoVITS 引擎**

- **状态**: Implemented
- **日期**: 2026-08-01
- **决策者**: 项目维护者 + AI 指挥
- **关联**: `docs/PENDING_ISSUES.md` §1 §2, commit `718f59b`

---

# 背景与问题

GPT-SoVITS 作为第四引擎于 commit `718f59b` 声明式接入 TTS_MultiModel，与 dots.tts 同批集成。
接入后暴露出一系列无法在短期内解决的兼容性与维护性问题：

1. **版本冲突不可调和**（PENDING_ISSUES §2.1）：
   - GPT-SoVITS 要求 `transformers <=4.50` / `numpy <2.0` / `pydantic <=2.10.6`
   - dots.tts 要求 `transformers >=4.57` / `numpy >=2.2.6` / `pydantic >=2.12.5`
   - `pyproject.toml` 已选择 dots.tts 优先策略，GPT-SoVITS 在高版本上**从未经过真实推理验证**

2. **编译依赖链过长**（PENDING_ISSUES §1.1–§1.3）：
   - pynini / WeTextProcessing / jieba_fast / opencc / pyopenjtalk 共 5 个包在 Windows + Python 3.12 下编译失败
   - 当前通过 vendor tn stub + opencc-python-reimplemented 绕过，但 pyopenjtalk（日语 TTS）完全不可用
   - 这些 fallback 步骤增加了部署复杂度和新人上手成本

3. **日语 TTS 能力缺失**（PENDING_ISSUES §1.3）：
   - GPT-SoVITS 在缺少 pyopenjtalk 时无法处理日语，但 UI 仍提供日语选项
   - 用户体验错位：选了日语但实际走中文 tokenization

4. **VRAM 切换风险**（PENDING_ISSUES §4.1）：
   - GPT-SoVITS 的 GPT + SoVITS 双模型同时驻留，引擎切换时 VRAM 释放彻底性未验证
   - 与项目硬约束 #2（显存占用超过 90% 熔断）存在冲突风险

---

# 评估的备选方案

## 方案 A：双轨隔离（独立 venv + HTTP 桥接）

- **描述**：为 GPT-SoVITS 维护独立 venv（`transformers==4.50` / `numpy<2.0`），主项目通过 subprocess / HTTP 调用
- **优点**：两个引擎版本完全隔离，不互相影响
- **缺点**：
  - 维护两套依赖链，CI 复杂度翻倍
  - subprocess 调用增加延迟（模型加载 + IPC 开销）
  - 仍需维护 5 个编译失败包的 fallback
  - 日语问题依然存在
- **结论**：维护成本过高，不采用

## 方案 B：声明式降级（保留注册但标记不可用）

- **描述**：在 `engine_registry` 中保留 gptsovits 注册项，但 `load()` 时返回 `EngineLoadError`，UI 显示"引擎不可用"
- **优点**：代码改动最小，保留回滚能力
- **缺点**：
  - 死代码留在仓库中，增加认知负担
  - i18n 键、模板、测试用例仍需维护
  - 用户看到"不可用"引擎会产生困惑
  - 版本冲突注释仍需在 `pyproject.toml` 中保留
- **结论**：死代码不可接受，不采用

## 方案 C：彻底删除

- **描述**：从代码库中彻底移除 GPT-SoVITS 引擎实现、i18n 键、模板、测试用例和配置项，仅保留归档文档
- **优点**：
  - 消除版本冲突，`pyproject.toml` 注释可简化
  - 消除 5 个编译失败包的维护负担（opencc 等仍为其他引擎保留）
  - 消除日语 TTS 缺失的用户体验问题
  - 代码库更精简，新人上手更快
- **缺点**：
  - 已下载的 GPT-SoVITS 权重（~5GB）变为孤儿文件
  - 如未来需要恢复，需从 git 历史中找回
- **结论**：**采用此方案**

---

# 决策

**采用方案 C（彻底删除）**，理由如下：

1. **兼容性风险不可接受**：GPT-SoVITS 在当前 `transformers >=4.57` / `numpy >=1.24` 环境下从未通过真实推理验证，保留等于在代码库中埋定时炸弹
2. **维护成本与收益不匹配**：GPT-SoVITS 的 5 个编译失败包 + 日语缺失 + VRAM 风险带来的维护成本，远超其作为"少样本克隆"补充引擎的收益（VoxCPM2 和 dots.tts 已覆盖克隆能力）
3. **dots.tts 已替代其定位**：dots.tts 提供 48kHz 高保真零样本克隆，能力上完全覆盖 GPT-SoVITS 的核心用例
4. **回滚路径清晰**：接入 commit `718f59b` 可通过 `git reflog` 找回，独立 venv 方案（方案 A）仍可作为未来恢复的应急路径

---

# 实施影响

## 删除清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `app/integrated_app/engines/gptsovits_engine.py` | 引擎实现 | 整文件删除 |
| `app/integrated_app/templates/partials/gptsovits_advanced.html` | UI 模板 | 整文件删除 |
| `app/integrated_app/templates/tabs/gptsovits_clone.html` | UI 模板 | 整文件删除 |
| `examples/call_gptsovits_api.py` | 示例脚本 | 整文件删除 |
| `scripts/download_gptsovits.py` | 下载脚本 | 整文件删除 |
| `tests/test_gptsovits_engine.py` | 单元测试 | 整文件删除 |

## 调整清单

| 文件 | 改动内容 |
|------|----------|
| `app/integrated_app/engine_interface.py` | 移除 `_register_builtin_engines()` 中 gptsovits 注册块 |
| `app/integrated_app/config.py` | 移除 `GPTSOVITS_MODEL_PATH` 常量 |
| `config.yaml` | 移除 `models.engines.gptsovits` 配置段 |
| `app/integrated_app/templates/base.html` | 移除 gptsovits 侧边栏导航项和模型 Tab |
| `app/integrated_app/routes/tabs.py` | 移除 gptsovits_clone 路由映射和引擎集合项 |
| `app/integrated_app/routes/generate/generic/clone.py` | 移除 GPT-SoVITS 专属参数，保留通用 clone 逻辑 |
| `app/integrated_app/locales/{zh,en,ja,ko}.json` | 移除所有 `gptsovits.*` 和 `tab_gptsovits_clone` i18n 键 |
| `pyproject.toml` | 移除注释中 GPT-SoVITS 版本策略说明 |
| `scripts/verify_model_weights.py` | 移除 gptsovits 权重校验条目 |
| `tests/e2e/test_tab_collapse_interaction.py` | 移除 GPT-SoVITS Tab 测试用例 |
| `tests/integration/test_engine_load_gpu.py` | 移除 GPT-SoVITS GPU 加载测试用例 |
| `tests/test_dotstts_engine.py` | 移除 docstring 中 GPT-SoVITS 引用 |
| `docs/INTEGRATION_DECISIONS.md` | 移除 GPT-SoVITS 专属章节（§1 版本策略 / §5 日语降级 / §6 inflect / §7 权重） |
| `docs/PENDING_ISSUES.md` | 标记 GPT-SoVITS 相关问题为已解决 |

## 保留清单

| 文件 | 保留原因 |
|------|----------|
| `docs/INSTALLATION_FALLBACKS.md` | 历史踩坑档案，记录编译失败包的 fallback 方案 |
| `docs/GPTSOVITS_DOTSTTS_INTEGRATION_GUIDE.md` | 历史集成指南，保留作为回滚参考 |
| `model/GPT-SoVITS/` | 用户已下载的模型权重，不自动删除 |
| `reference_repos/GPT-SoVITS/` | 参考仓库，用于技术学习 |
| `docs/reference_repos_GPT-SoVITS_技术学习报告.md` | 技术分析报告 |

---

# 可回滚路径与待验证项

## 回滚路径

如未来需要恢复 GPT-SoVITS 引擎：

1. **找回接入代码**：
   ```bash
   git reflog | grep 718f59b
   git cherry-pick 718f59b  # 或 git checkout 718f59b -- <files>
   ```

2. **重建独立 venv**（推荐隔离方案）：
   ```bash
   python -m venv .venv-gptsovits
   # Windows
   .venv-gptsovits\Scripts\activate
   pip install 'transformers==4.50.0' 'numpy<2.0' 'pydantic<2.10.6'
   pip install jieba opencc-python-reimplemented
   ```

3. **通过 subprocess / HTTP 桥接**：主项目通过 HTTP 调用独立 venv 中的 GPT-SoVITS 服务

## 待验证项

1. ✅ `scripts/check_engine_compat.py` 通过（3 引擎 import 兼容性验证）
2. ✅ `pytest --collect-only` 无 GPT-SoVITS 相关测试
3. ✅ `git grep "gptsovits\|GPT-SoVITS"` 仅在归档文档中命中
4. ✅ `ruff check` 和 `ruff format --check` 通过

## 实施 commit

| 子任务 | Commit | 说明 |
|--------|--------|------|
| 9.1 删引擎实现 | — | 删除引擎文件 + 更新注册表/配置 |
| 9.2 删 i18n 键 | — | 删除 4 语言文件中 gptsovits 键 |
| 9.3 删测试用例 | — | 删除/更新测试文件 |
| 9.4 改依赖与文档 | — | 更新 pyproject.toml / 文档 |
| 9.5 更新 ADR 状态 | — | 本文件状态改为 Implemented |
