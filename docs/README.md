# TTS_MultiModel — 文档与项目速览

> 多引擎语音合成（TTS）Web 应用（IndexTTS2 / VoxCPM2 / 含洛水等）。
> 入口：`app/clean_launch.py` / `start.bat`。
> 详细目录放置规则见 `AGENTS.md` 末尾「文件归档与放置规范」。

## 快速了解本项目
- **做什么**：文本转语音，多人声克隆、音色设计、LoRA 训练、角色/音色库、脚本续写。
- **技术栈**：Python · FastAPI · (IndexTTS2/VoxCPM2) · 原生 JS/CSS。
- **如何启动**：`start.bat` 或 `python app/clean_launch.py`。

## 目录结构速览
| 目录 | 内容 |
|---|---|
| `app/integrated_app/` | **主程序源码**（engines/ routes/ model_manager/ training/ static/） |
| `tests/` | pytest + Playwright；`tests/frontend` |
| `docs/` | 项目文档（见下方索引） |
| `personas/` | 音色库（.pt/.wav/文本） |
| `model/` | 模型权重；`lora/` LoRA |
| `data/` `outputs/` `logs/` `screenshots/` | 运行/产物 |
| `examples/` | API 用法示例；`demo/` HTML 演示 |
| `scripts/` | 运维/工具脚本 |
| `cache/ prompt_cache/ torch_compile_cache/` | 运行时缓存（勿提交） |

## docs/ 索引（本目录）
| 子目录/文件 | 存什么 |
|---|---|
| `project/` | 架构、引擎设计、OpenAI API、参数、决策记录 |
| `plans/` | 实施指南、模型接入/部署/训练指南、路线图、ROADMAP |
| `reports/` | 健康度/功能状态、UX-UI 评估、审计、对比/分析报告 |
| `adr/` | 架构决策记录（ADR） |
| `repo-analysis/` | 参考仓库学习报告 |
| `research/` | 引擎调研报告（voxcpm 等） |
| `_devarchive/` | 历史/一次性产物（含 trae-documents） |
| `screenshots/` | 界面截图 |
| `SECURITY` / `LICENSE` / `COMPLIANCE` | 安全/合规/许可（根目录） |

## 想找内容？
- 想改合成逻辑 → `app/integrated_app/engines/`（voxcpm2/ indextts2_engine.py）
- 想改前端 → `app/integrated_app/static/`、`templates/`
- 想改配置 → `config.yaml`（含模型/引擎/参数）
- 想了解功能范围 → `docs/reports/功能实现状态分析报告.md`
- 想了解架构取舍 → `docs/adr/`