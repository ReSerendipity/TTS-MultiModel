# Step-Audio-EditX 技术学习报告（TTS_MultiModel 竞品对标 · 代码级）

> **性质**：竞品对标学习报告（非建议文档）。事实来自 `C:\Users\Doro\reference_repos\TTS_MultiModel\Step-Audio-EditX` 浅克隆 + `gh api` 实时核验。
> **核验**：`stepfun-ai/Step-Audio-EditX` — **972★ / Apache-2.0 / 推送 2026-04-09**。

## 一、概览
- **定位**：**音频编辑（Audio Editing）**模型——在保留原音色/内容前提下，精细编辑情绪、说话风格、副语言（paralinguistic）与发音；支持**复调发音控制（polyphonic pronunciation control）**。
- **许可**：**Apache-2.0**（gh-api 确认）——可自由借鉴，无 GPL 风险。
- **形态**：模型权重（HF/ModelScope `stepfun-ai/Step-Audio-EditX`）+ 推理代码 + **训练代码（SFT/DPO/GRPO）**。

## 二、技术栈（README）
- 推理：Python；**官方支持 vLLM 推理**（与 vllm-omni 生态同源，见 MiniMax-H3-lite 报告）。
- 训练：发布 SFT / DPO / GRPO 三套训练代码——可直接微调编辑能力。
- 多语言：2025-11 起支持 **日语 / 韩语**；中文原生。

## 三、核心能力
- **副语言编辑**：新增 `exhale`/`snort`/`inhale`/`chuckle`/`clears throat`/`giggle` 等标签，细粒度情绪/气息控制。
- **情绪与说话风格编辑**：基于参考音频修改情绪而不改身份。
- **复调发音控制**：精确控制多音字/特定发音。
- **可训练**：SFT/DPO/GRPO 代码齐备，支持本仓定制编辑风格 LoRA。

## 四、与 TTS_MultiModel 对标点（关键）
- **音频编辑新范式**：本仓现有引擎（VoxCPM2/IndexTTS 2.5）偏「从文本生成」，缺乏**对已有音频做情绪/副语言编辑**的能力；Step-Audio-EditX 正好补「声音设计 / 后期编辑」缺口（见 `docs/plans/` 声音设计方向）。
- **训练代码可复用**：SFT/DPO/GRPO 三件套可直接作为本仓编辑能力微调基座（对应 `TRAINING_TOOLCHAIN_PLAN`）。
- **vLLM 推理**：与 vllm-omni 路线一致，便于统一服务栈。

## 五、许可与合规
- Apache-2.0：代码/权重可自由借鉴与商用；按 `THIRD_PARTY_NOTICES.md` 登记。
- 无 GPL 依赖。

## 六、可借鉴点（P0/P1）
- **P0**：副语言标签体系（exhale/snort/...）作为本仓「情绪编辑」参数 schema。
- **P0**：SFT/DPO/GRPO 训练代码作为编辑能力微调模板。
- **P1**：vLLM 推理接入本仓统一推理服务。

## 七、风险 / 不适用
- 星数较低（972★）、社区规模小于主引擎；宜作「编辑」专用模块而非主合成引擎。
- 编辑质量依赖参考音频质量，须与本仓克隆流程对齐。

## 八、参考文件（克隆内可复核）
- `reference_repos/TTS_MultiModel/Step-Audio-EditX/README.md`（News/能力/训练）
- `reference_repos/TTS_MultiModel/Step-Audio-EditX/`（推理+训练代码）
