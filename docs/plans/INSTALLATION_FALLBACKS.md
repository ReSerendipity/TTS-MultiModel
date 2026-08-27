# 编译失败 Fallback 文档

> 本文档记录 TTS_MultiModel 项目在 Windows + Python 3.12 + MSVC 环境下，
> 编译失败包的完整环境适配步骤与替代方案。
>
> **最后更新**：2026-08-01

---

## 1. pynini / WeTextProcessing

### 触发条件

- Windows + MSVC 编译器（不支持 GCC/Clang 编译参数）
- PyPI 无预编译 wheel
- 源码编译需要 OpenFst C++ 静态库

### 失败原因

`setup.py` 在 `extra_compile_args` 中硬编码了 GCC 特定参数（`-Wno-register`、`-Wno-deprecated-declarations` 等），MSVC 的 `cl.exe` 不识别 `-W` 前缀。

### 解决方案

已创建 `tn` 降级 stub 包，位于 `app/integrated_app/vendor/tn/`：

- `tn/chinese/normalizer.py`：`Normalizer.normalize(text)` 仅做空白符清理
- `tn/english/normalizer.py`：同上

该包通过 `pyproject.toml [tool.setuptools.packages.find]` 被自动发现，无需手动注入 site-packages。

### 完整归一化（可选）

如需完整文本归一化（数字、日期等），使用 conda-forge：

```bash
conda install -c conda-forge pynini we_text_processing
```

---

## 2. opencc

### 触发条件

- `--no-binary=opencc` 标志强制源码编译
- requirements.txt 中强制不使用预编译 wheel

### 解决方案

安装纯 Python 替代品 `opencc-python-reimplemented`（已声明在 `pyproject.toml` dependencies 中）：

```bash
pip install opencc-python-reimplemented
```

---

## 3. jieba_fast

### 触发条件

- C++ 扩展使用 GCC 参数编译

### 解决方案

使用纯 Python `jieba`（已在过滤依赖中安装），功能等效但速度稍慢。

---

## 4. pyopenjtalk / pyopenjtalk-prebuilt

### 触发条件

- Python 3.12 移除了 `pkgutil.ImpImporter`（PEP 451 已废弃），`pyopenjtalk-prebuilt` 的 `setup.py` 仍使用此 API
- `pyopenjtalk` 需要 CMake + C++ 编译 OpenJTalk 库

### 影响范围

GPT-SoVITS 仍支持中/英/韩/粤，**仅缺日语**。选 `ja` 语言时会返回 `ValidationError` 友好提示。

### 短期方案

已在 `gptsovits_engine.py` 中加入语言检测，日语请求返回友好错误：

```
日语 TTS 暂不可用：pyopenjtalk 在当前 Python 3.12 + Windows 环境下无法编译。
请使用中/英/韩/粤等其他语言，或等待上游 pyopenjtalk 修复 Python 3.12 兼容性。
```

### 长期方案

等待上游 `pyopenjtalk` 修复 Python 3.12 兼容性，或使用 conda-forge 安装。

---

## 5. inflect（主动丢弃）

### 触发条件

非编译失败，而是主动丢弃。`inflect` 是 GPT-SoVITS 的英文数字转写依赖，中文场景不需要。

### 决策理由

- 中文 TTS 场景不涉及英文数字转写
- 减少 dependency 树复杂度
- 如需英文场景完整功能，可手动安装：`pip install inflect`

---

## 6. 版本冲突记录

### transformers

| 引擎 | 要求版本 | 当前版本 |
|------|---------|---------|
| GPT-SoVITS | >=4.43, <=4.50 | 实际安装 4.50 |
| dots.tts | >=4.57.0 | — |

**决策**：`pyproject.toml` 声明 `>=4.57.0`（满足 dots.tts），GPT-SoVITS 兼容性待长期验证。

### numpy

| 引擎 | 要求版本 |
|------|---------|
| GPT-SoVITS | <2.0 |
| dots.tts | >=2.2.6 |

**决策**：声明 `>=1.24.0`，实际运行使用 1.26.4（GPT-SoVITS 优先）。

### pydantic

| 引擎 | 要求版本 |
|------|---------|
| GPT-SoVITS | <=2.10.6 |
| dots.tts | >=2.12.5 |

**决策**：声明 `>=2.0`，实际运行使用低版本（GPT-SoVITS 优先）。
