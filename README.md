<div align="center">

# TTS MultiModel

[![gitleaks](https://img.shields.io/badge/secret%20scan-gitleaks%20passing-0080FF?style=for-the-badge)](https://github.com/ReSerendipity/TTS_MultiModel/actions/workflows/gitleaks.yml)

**多模型语音合成平台 | Multi-Model Text-to-Speech Platform**

五引擎开源语音合成平台：声音克隆、声音设计、LoRA 微调与多角色剧本配音

A powerful open-source multi-engine Text-to-Speech platform with voice cloning, voice design, LoRA fine-tuning, and multi-character script dubbing

[![CI](https://github.com/ReSerendipity/TTS_MultiModel/actions/workflows/ci.yml/badge.svg)](https://github.com/ReSerendipity/TTS_MultiModel/actions)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.0+-EE4C2C.svg?logo=pytorch&logoColor=white)](https://pytorch.org/)
[![Docker](https://img.shields.io/badge/Docker-Supported-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com/)

[中文](#中文) · [功能亮点](#功能亮点) · [快速开始](#快速开始) · [模型下载](#模型下载) · [API](#api-端点) · [许可证](#许可证)

</div>

***

<a id="中文"></a>

## Why TTS MultiModel?

| 优势 | 说明 |
|---|---|
| **五引擎一站式平台** | 集成 VoxCPM2 / IndexTTS 2.5 / IndexTTS 2.0 / OpenVoice / Step-Audio-EditX 五套引擎，声音克隆、声音设计、剧本配音，无需在多个工具间切换（LoRA 微调为实验特性，见下方说明） |
| **极低门槛** | 一键安装脚本 + 便携 WinPython 部署方案，Windows 用户开箱即用；Docker 部署仅需一行命令 |
| **完整工具链** | 从数据准备到推理部署，覆盖 TTS 全生命周期（训练链路为实验特性，未经真机验证） |
| **开源透明** | 项目代码 Apache 2.0；**模型权重许可各异**（见「模型许可说明」），商用前请逐项核对 |
| **多语言界面** | 支持中文（简/繁）、英文、日文、韩文，国际化开箱即用 |

## Demo

在线模拟演示（无需 GPU / 模型权重，纯前端仿真）：<https://reserendipity.github.io/TTS_MultiModel/>

界面截图（VoxCPM2 引擎，1440×900 视口）已入库 `docs/screenshots/`：

| 声音设计 | 声音克隆 | 极致克隆 |
|---|---|---|
| ![声音设计](docs/screenshots/voxcpm2_01_voice_design_viewport.png) | ![声音克隆](docs/screenshots/voxcpm2_02_voice_clone_viewport.png) | ![极致克隆](docs/screenshots/voxcpm2_03_ultimate_clone_viewport.png) |
| **剧本配音** | **LoRA 管理** | **系统设置** |
| ![剧本配音](docs/screenshots/voxcpm2_04_script_workshop_viewport.png) | ![LoRA 管理](docs/screenshots/voxcpm2_06_lora_viewport.png) | ![系统设置](docs/screenshots/voxcpm2_08_settings_viewport.png) |

> 文件名编号 05/07 缺位是有意为之，不代表有截图待补；`.gitignore` 里对这 6 个文件名做了显式白名单。

> 欢迎在 [Discussions](https://github.com/ReSerendipity/TTS_MultiModel/discussions) 中分享你的使用体验！

## 功能亮点

| 功能 | 描述 |
|---|---|
| **五引擎架构** | VoxCPM2 / IndexTTS 2.5 / IndexTTS 2.0 / OpenVoice / Step-Audio-EditX，灵活切换（引擎注册见 `app/integrated_app/engines/`） |
| **声音克隆** | 仅需少量音频样本即可克隆声音（可控克隆 + 极致克隆） |
| **声音设计** | 通过文字描述生成目标音色的语音 |
| **剧本配音** | 多角色对话剧本自动分配说话人，批量生成配音 |
| **流式生成** | 长文本实时流式音频输出（SSE） |
| **LoRA 微调** | 自定义数据集 LoRA 微调训练（实验特性，未经真机验证，见下方说明） |
| **Web 界面** | FastAPI + HTMX + Jinja2 现代化响应式 Web UI |
| **批量处理** | 支持批量音频生成，任务断点续跑 |
| **历史管理** | SQLite 历史记录，支持搜索、筛选、分页 |
| **多语言界面** | 支持中文（简/繁）、英文、日文、韩文界面切换（`app/integrated_app/locales/`，5 份语言文件） |
| **多 GPU 后端** | NVIDIA CUDA / Apple MPS / CPU |
| **自定义音色库** | 支持用户保存和管理自定义音色 |

> **实验特性说明（LoRA 微调 / 训练链路）**：训练代码已实现（数据加载、LoRA 注入、混合精度、断点续训、TensorBoard 日志），单元测试 57 项全部通过；但 `lora/` 与 `checkpoints/` 下无真机训练产物，CI 无 GPU 训练冒烟测试；训练依赖为 optional extra（`pip install -e .[training]`）。12GB 显存上训练前必须先卸载推理引擎，否则 OOM。

## 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10/11 (64-bit) 或 Linux |
| Python | 系统 Python 3.10+（3.12 最佳），或自行下载 WinPython（`WPy64-312101/`）解压到项目根目录完全隔离 |
| GPU | NVIDIA (CUDA) / Apple Silicon (MPS)，推荐 6.5GB+ VRAM |
| VC 运行库 | Windows 需自行下载安装 Visual C++ Redistributable（x64） |
| SoX（音频效果处理） | Windows 需下载 SoX 14.4.2 并解压到 `app/sox-14.4.2-win32/sox-14.4.2/`（`clean_launch.py` 会自动加入 PATH）；Linux/macOS 用包管理器安装 |

## 快速开始

### Windows 安装

**方式一：使用系统 Python（推荐，节省磁盘空间）**

```bash
git clone https://github.com/ReSerendipity/TTS_MultiModel.git
cd TTS_MultiModel

# 1. 安装系统 Python 3.10+（推荐 3.12），务必勾选 "Add Python to PATH"
# 2. 一键安装依赖（会自动检测系统 Python）
install.bat
# 3. 下载模型（见下方"模型下载"）
# 4. 启动应用
start.bat
```

> 多个项目（如 SeedVR2、TTS_MultiModel）可共享一套系统 Python 与依赖，避免每个项目 1~2GB 的重复 WinPython 环境。

**方式二：使用便携 WinPython（完全隔离，无需系统 Python）**

```bash
git clone https://github.com/ReSerendipity/TTS_MultiModel.git
cd TTS_MultiModel

# 1. 下载 WinPython 并解压到项目根目录，确保 WPy64-312101\python\python.exe 存在
#    https://github.com/winpython/winpython/releases
# 2. 自行下载安装微软官方 Visual C++ Redistributable（x64）
# 3. install.bat（检测不到系统 Python 时自动回退到 WinPython）
# 4. 下载模型 → 5. start.bat
```

> `install.bat` / `start.bat` 的 Python 查找优先级：常见系统安装路径 → PATH 注册的 `python` → 项目内 WinPython。

### Linux 安装

```bash
git clone https://github.com/ReSerendipity/TTS_MultiModel.git
cd TTS_MultiModel
chmod +x install.sh && ./install.sh
chmod +x start.sh && ./start.sh
```

### Docker 部署

```bash
docker compose up -d

# 或直接拉取 GHCR 镜像（随版本发布：v2.2.1 对应 tag 2.2.1）
docker pull ghcr.io/reserendipity/tts_multimodel:2.2.1
docker run -d --gpus all -p 7869:7869 \
  -v ./model:/app/model \
  -v ./outputs:/app/outputs \
  -v ./personas:/app/personas \
  ghcr.io/reserendipity/tts_multimodel:2.2.1

# 或手动构建
docker build -t tts-multimodel .
docker run -d --gpus all -p 7869:7869 \
  -v ./model:/app/model \
  -v ./outputs:/app/outputs \
  -v ./personas:/app/personas \
  tts-multimodel
```

访问 `http://localhost:7869`（默认端口 7869）。Docker 部署需要 nvidia-docker runtime。

## 模型下载

本项目是**多引擎编排框架（Apache-2.0），本身不含任何模型权重**。所有模型权重需从官方仓库由用户自行下载，放入 `model/` 下对应子目录（详见「模型许可说明」）。

### 统一一键下载（国内镜像优先）

```bash
pip install huggingface_hub modelscope
python scripts/download_models.py --all                        # 拉全部 7 个模型集合（HF 镜像优先，失败回退魔搭）
python scripts/download_models.py --model voxcpm2              # 只拉某一个
python scripts/download_models.py --all --source modelscope    # 强制只用魔搭
python scripts/download_models.py --all --no-verify            # 跳过 SHA256 校验
```

下载完成后可用 `python scripts/verify_model_checksums.py` 校验完整性。

### 各模型存放目录与来源

| 模型目录 | 归属引擎 | 官方仓库（HF / ModelScope） |
|---|---|---|
| `model/VoxCPM2/` | voxcpm2 | `openbmb/VoxCPM2` / `OpenBMB/VoxCPM2` |
| `model/SenseVoiceSmall/` | voxcpm2（ASR） | `FunAudioLLM/SenseVoiceSmall` / `iic/SenseVoiceSmall` |
| `model/speech_zipenhancer/` | voxcpm2（降噪） | 仅 ModelScope `iic/speech_zipenhancer_ans_multiloss_16k_base` |
| `model/IndexTTS-2.5/` | indextts2 | `IndexTeam/IndexTTS-2.5` |
| `model/IndexTTS-2.0/` | indextts20 | `IndexTeam/IndexTTS-2` |
| `model/OpenVoice/` | voicebox | `myshell-ai/OpenVoice` |
| `model/Step-Audio-EditX/` | step-audio-editx | `stepfun-ai/Step-Audio-EditX` |

## API 端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/system/health` | GET | 健康检查 |
| `/api/system/gpu` | GET | GPU 利用率信息 |
| `/api/generate/voxcpm2/voxcpm_clone` | POST | 声音克隆（VoxCPM2） |
| `/api/generate/voxcpm2/voxcpm_design` | POST | 声音设计（VoxCPM2） |
| `/api/generate/voxcpm2/voxcpm_script` | POST | 剧本配音（VoxCPM2） |
| `/api/generate/voxcpm2/streaming_sse` | POST | 流式生成（SSE） |
| `/api/generate/indextts2/synthesize` | POST | TTS 合成（IndexTTS 2.5） |
| `/api/model/load` | POST | 加载模型 |
| `/api/model/unload` | POST | 卸载模型 |
| `/api/history` | GET | 生成历史 |

> 生产环境建议在 `config.yaml` 的 `api_auth` 区域启用 API 认证。

## 安全与可靠性

- **配置原子写入**：`save_config()` 使用 tempfile + `os.replace`，避免写入中断导致配置半写损坏（源自 Seedvr2）
- **配置验证失败回退**：Pydantic 验证失败自动回退原始 YAML 加载，保证应用不因格式错误无法启动
- **核心模块完整性自校验**：启动时对 16 个核心文件做 SHA-256 比对（`app/integrated_app/security/integrity_manifest.json`），检测篡改（CWE-912 防御），失败只告警不阻塞
- **模型路径 shared / portable 双模式**：`config.yaml → models.model_source_mode`（`portable` 项目内 `model/` / `shared` 外部共享目录）
- **断点续跑**：批量配音 / 克隆任务中断后重启可跳过已完成子任务（`data/checkpoints/`）
- **差异化静态文件缓存**：CSS/JS `no-cache`、字体 30 天、图片 1 天

## 技术栈

| 层级 | 技术 |
|---|---|
| Web 框架 | FastAPI + Uvicorn |
| 前端 | HTMX + Jinja2 + Bootstrap |
| TTS 引擎 | VoxCPM2 / IndexTTS 2.5 / IndexTTS 2.0 / OpenVoice / Step-Audio-EditX |
| ASR 引擎 | SenseVoiceSmall |
| 音频处理 | speech_zipenhancer + FFmpeg（系统自带）+ SoX |
| 深度学习 | PyTorch + Transformers + FunASR |
| 数据库 | SQLite |
| 容器化 | Docker + Docker Compose |

## 故障排除

| 问题 | 解决方案 |
|---|---|
| VC 运行库错误 (Windows) | 自行下载安装微软官方 Visual C++ Redistributable（x64） |
| 模型未找到 | 确保模型下载到 `model/` 且目录结构正确 |
| GPU 未检测到 | 安装对应 PyTorch 版本 (CUDA/MPS)，更新驱动 |
| 端口被占用 | 应用会自动选择可用端口，查看控制台输出 |
| Docker GPU 访问 | 确保安装 nvidia-docker runtime |

详细日志查看 `logs/app.log`。

## 参与贡献

欢迎贡献！报告 Bug（附复现步骤）、功能建议（带 `enhancement` 标签的 Issue）、提交代码（Fork → Branch → Commit → Push → PR）、改进文档。详见 [贡献指南](https://github.com/ReSerendipity/.github/blob/main/CONTRIBUTING.md)（组织默认，Conventional Commits + DCO）。

## 模型许可说明

> 本表为**模型权重**的许可清单（项目代码为 Apache-2.0，见 [LICENSE](LICENSE)）。本项目**只做编排、不打包任何权重**——所有模型权重均来自下表官方仓库、由用户自取，框架分发本身不涉及再分发权重风险；但使用各模型仍须遵守其各自许可，商用前请逐项核对。**接入新引擎时：更新本表 + `config.yaml` 中对应引擎的 `license` 字段。**

| 模型 / 权重 | 归属引擎 | 权重许可 | 商用提示 |
|---|---|---|---|
| VoxCPM2 | voxcpm2 | Apache-2.0 | 可商用（默认推荐引擎） |
| SenseVoiceSmall | voxcpm2（ASR） | 自定义 model-license | 可商用（遵循模型许可；FunASR 标注可商用） |
| speech_zipenhancer | voxcpm2（降噪） | Apache-2.0 | 可商用（2026-09-15 经 ModelScope API 核实） |
| IndexTTS 2.5 | indextts2 | bilibili Model Use License Agreement | 商用须事先向 bilibili 登记并取得书面授权 |
| IndexTTS 2.0 | indextts20 | bilibili Model Use License Agreement | 同上 |
| OpenVoice | voicebox | MIT | 可商用（2026-09-16 经 HF API 核实 V1/V2 卡均 MIT） |
| Step-Audio-EditX | step-audio-editx | Apache-2.0 | 代码经 GitHub API 实证（2026-09-16）；权重使用前留意官方更新 |

> 历史 / 参考引擎（非默认分发）：CosyVoice2 / ChatTTS / F5-TTS 等曾出现在 `data/` 参考实现中，其中 ChatTTS、F5-TTS 模型为**非商用**许可，仅作研究参考或标注后使用，不得作为商用发行默认引擎。

### 免责声明

- 本项目按 Apache-2.0 提供，不对任何第三方模型权重的许可合规性、适用性、准确性负责；用户须自行核对所下载权重的许可并承担使用后果
- "IndexTTS" 为 bilibili 的商标/产品名，使用须遵守 **bilibili Model Use License Agreement**（第 5.2 条仅允许合理且符合惯例的描述性引用，不得暗示官方背书；商用须事先登记并取得书面授权）；使用 IndexTTS 引擎还须遵守其 DISCLAIMER，包括禁止合成政治人物、公众人物等声音
- 请勿将本项目用于侵权、诈骗、伪造等违法用途

## 相关项目

| 项目 | 说明 |
|---|---|
| [VoxCPM](https://github.com/OpenBMB/VoxCPM) | OpenBMB 多语言 TTS，本项目 VoxCPM2 引擎的上游 |
| [Fish Speech](https://github.com/fishaudio/fish-speech) | Fish Audio 多语言 TTS，80+ 语言支持，RL 对齐 |
| [OpenVoice](https://github.com/myshell-ai/OpenVoice) | MyShell 即时语音克隆，风格控制 |
| [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) | 阿里多语言 TTS，Flow Matching + vLLM 加速 |
| [Chatterbox](https://github.com/resemble-ai/chatterbox) | Resemble AI 低延迟 TTS，模型分级策略 |

## 许可证

本项目基于 [Apache License 2.0](LICENSE) 开源。Copyright (c) 2026 ReSerendipity。

**如果这个项目对你有帮助，请给个 Star 支持一下！**
