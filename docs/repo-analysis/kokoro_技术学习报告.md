# kokoro 技术学习报告（TTS_MultiModel 竞品对标 · 代码级）

> **性质**：竞品对标学习报告（非建议文档）。事实来自 `gh api` 实时核验 + README。
> **核验**：`hexgrad/kokoro` — **8,672★ / Apache-2.0 / 推送 2025-08-06**。

## 一、概览
- **定位**：**Kokoro-82M** 推理库——82M 参数开放权重 TTS，轻量却接近大模型音质，显著更快更省成本；适合生产到个人项目全场景。
- **许可**：**Apache-2.0**（gh-api + README「Apache-licensed weights」确认）——权重与代码均可自由部署，无 GPL 风险。
- **形态**：`pip install kokoro` 推理库；权重 `hexgrad/Kokoro-82M`（HF）；G2P 用 `misaki`。

## 二、技术栈（README）
- 运行时：Python；`KPipeline` 按 `lang_code` 选择语言管线（a/b/... 多语）；`misaki` G2P 库做注音。
- 输出：24kHz 音频（`sf.write(..., 24000)`）；依赖 `espeak-ng` 作英文 OOD 兜底。
- 边缘友好：82M 参数可在 CPU/低资源设备实时。

## 三、核心能力
- **极致轻量**：82M 参数，远低于 VoxCPM2/IndexTTS 2.5，CPU 可跑。
- **多语言管线**：`lang_code` 切换（a=美英、b=英英等），覆盖多语。
- **Apache 开放权重**：可任意部署（生产/个人），合规无碍。
- **质量接近大模型**：小模型却达可用音质，适合高频/低延迟场景。

## 四、与 TTS_MultiModel 对标点（关键）
- **边缘部署落点**：直接对应本仓 `EDGE_DEPLOYMENT_PLAN.md`——82M 轻量、Apache 权重，是 CPU/边缘场景理想引擎（主报告 §2.5 已标 🟢）。
- **轻量梯队补齐**：与 dia（1.6B 对话）、F5-TTS（Flow Matching）形成「轻量→中量」引擎梯队，丰富本仓多引擎聚合。
- **G2P 借鉴**：`misaki` 注音库可作本仓多语言前端 G2P 参考。

## 五、许可与合规
- **Apache-2.0**：权重+代码可自由部署与商用；按 `THIRD_PARTY_NOTICES.md` 登记。
- 无 GPL 依赖。

## 六、可借鉴点（P0/P1）
- **P0**：作为本仓边缘/CPU 部署引擎（对应 `EDGE_DEPLOYMENT_PLAN.md`）；`misaki` G2P 作多语前端参考。

## 七、风险 / 不适用
- 音质上限低于主引擎，仅适合低延迟/边缘，不作通用主合成。
- 多语言覆盖依赖 `lang_code` 管线完整度，须本仓核实目标语种。

## 八、参考文件
- `gh api repos/hexgrad/kokoro`（stars/license/pushed 核验）
- `hexgrad/kokoro` GitHub README（用法/advanced）
- `hexgrad/Kokoro-82M`（HF 权重）
