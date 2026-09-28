# TTS_MultiModel 对内项目说明（DOCS-README.internal.md）

> 本文件为**对内**维护者说明，入库后位于 `DOCS/README.internal.md`，是 `DOCS/` 对内说明的唯一入口。
> 面向 GitHub 访客的对外文档只有根目录 `README.md`。凡「陌生用户读了无法行动」的内容——开发状态细节、维护者 checklist、许可核实记录、本地环境约定——一律收在本文件，不得写回根 README。

## 1. 实验特性（LoRA 微调 / 训练链路）验证状态

- 训练代码已实现：数据加载、LoRA 注入、混合精度、断点续训、TensorBoard 日志。
- 单元测试 57 项全部通过；**CI 无 GPU 训练冒烟测试**。
- `lora/` 与 `checkpoints/` 下**无真机训练产物**，训练链路未经真机验证。
- 训练依赖为 optional extra（`pip install -e .[training]`）——该安装命令已在对外 README 的实验特性说明中保留。
- 对外 README 仅保留两句用户视角能力边界（已提供但未经真机验证；12GB 显存训练前先卸载推理引擎），本节细节不外发。

## 2. Demo 截图素材约定

- `docs/screenshots/` 中 VoxCPM2 截图共 6 张（voxcpm2_01 / 02 / 03 / 04 / 06 / 08）。
- **文件名编号 05/07 缺位是有意为之**，不代表有截图待补。
- `.gitignore` 里对这 6 个文件名做了显式白名单。调整截图集时需同步维护该白名单。

## 3. 维护者 checklist：接入新引擎时

- 更新对外 README「模型许可说明」的权重许可表；
- 更新 `config.yaml` 中对应引擎的 `license` 字段。

## 4. 引擎许可核实记录（历史沿革）

以下核实途径与日期仅记录在本文件，对外表格只呈现结论：

- **speech_zipenhancer**：Apache-2.0，2026-09-15 经 ModelScope API 核实。
- **OpenVoice**：MIT，2026-09-16 经 HF API 核实 V1/V2 模型卡均 MIT。
- **Step-Audio-EditX**：代码为 Apache-2.0，2026-09-16 经 GitHub API 实证；权重使用前留意官方更新。
- **历史 / 参考引擎**：CosyVoice2 / ChatTTS / F5-TTS 等曾出现在 `data/` 参考实现中，其中 ChatTTS、F5-TTS 模型为**非商用**许可，仅作研究参考或标注后使用，不得作为商用发行默认引擎。对外仅保留一句「历史参考引擎不随默认分发，其中部分模型许可为非商用」。

## 5. 已从对外 README 移除的内部性表述

- **快速开始备忘**：「多个项目（如 SeedVR2、TTS_MultiModel）可共享一套系统 Python 与依赖，避免每个项目 1~2GB 的重复 WinPython 环境」——跨项目本地环境约定，不属对外内容。
- **Windows 方式二 Python 查找优先级**：常见系统安装路径 → PATH 注册的 `python` → 项目内 WinPython；对外改为一句「脚本自动检测 Python」。
- **功能亮点表源码路径点缀**：引擎注册目录 `app/integrated_app/engines/`；多语言目录 `app/integrated_app/locales/`（5 份语言文件）。
- **安全与可靠性实现细节**：`save_config()` 使用 tempfile + `os.replace` 的原子写入实现；Pydantic 验证失败自动回退原始 YAML 加载；启动时对 16 个核心文件做 SHA-256 比对（`app/integrated_app/security/integrity_manifest.json`，CWE-912 防御）；「源自 Seedvr2」的出处标注。对外保留用户可感知的行为描述。

## 6. 对外/对内拆分备忘

- Demo 章节：删去 05/07 编号缺位与 `.gitignore` 白名单说明（收本文件 §2），截图表格与演示页链接保留。
- 实验特性说明：改写为两句用户视角能力边界，保留诚实披露；57 项单测 / CI 冒烟 / 训练产物状态收本文件 §1。
- 模型许可说明：删「接入新引擎时：更新本表 + config.yaml」（收本文件 §3）与各引擎核实日期/途径（收本文件 §4）。
- 安全与可靠性：压缩为用户视角特性列表，删「源自 Seedvr2」。
- 其余章节（Why、环境要求、快速开始步骤、模型下载、API 端点、技术栈、故障排除、参与贡献、免责声明、相关项目、许可证）原样保留。
