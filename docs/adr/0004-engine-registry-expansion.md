**ADR-0004: 引擎注册表扩张至 5 引擎（indextts2 / indextts20 / voicebox / step-audio-editx 正式登记）**

- **状态**: Implemented
- **日期**: 2026-09-16
- **决策者**: 项目维护者 + AI 指挥（文档审核整改批次）

---

# 背景与问题

ADR-0003 定稿时运行时唯一激活引擎为 VoxCPM2；其后陆续接入 IndexTTS 2.5（indextts2）、
IndexTTS 2.0（indextts20）、Voicebox VC（voicebox，语音转换接口）、Step-Audio-EditX
（step-audio-editx，音频编辑接口），但未按家族纪律「引入或移除模型引擎必须写 ADR」补录，
致 ADR-0003 表述与 `engine_interface._register_builtin_engines()` 实况（5 个 register 调用）
脱钩（2026-09-16 文档审核发现，README/AGENTS 已按多引擎口径描述而 ADR 层缺位）。

# 评估的备选方案

- 方案 A：改写 ADR-0003 正文——破坏其历史快照语义，且其「后续追加记录」节已承担演进留痕职责。不采用。
- 方案 B：新增本 ADR 登记扩张事实，ADR-0003 以追加记录指向本条。**采用**。

# 决策

现役注册引擎 5 个（`engine_interface._register_builtin_engines()` 实况）：

| 注册名 | 引擎 | 说明 |
|---|---|---|
| `voxcpm2` | VoxCPM2 | 核心 TTS，sample_rate=48000，懒导入回退双注册 |
| `indextts2` | IndexTTS 2.5 | 懒导入 |
| `indextts20` | IndexTTS 2.0 | 懒导入，复用 indextts2 代码包薄子类 |
| `voicebox` | Voicebox VC | 懒导入，OpenVoice 语音转换（音频→音频，非 TTSEngine Protocol） |
| `step-audio-editx` | Step-Audio-EditX | 懒导入，音频编辑接口（非 TTSEngine Protocol） |

「多引擎」语义沿用 ADR-0003：**注册契约多引擎、同一时刻单激活引擎**（显存与串行信号量约束）。

# 实施影响

- AGENTS.md §1 现役引擎清单与自进化自检清单同步（48kHz 修正、补 voicebox / step-audio-editx）。
- README 已按多引擎口径描述，无需改动。
- 后续新引擎按本 ADR 与 SOP-17 接入，并即时补 ADR（本条即为该纪律的补课）。

# 可回滚路径与待验证项

- 回滚 = 按引擎逐一移除注册与配置段，各引擎接入时的原始 commit 为回滚点。
- 待验证：AGENTS 引擎枚举与注册实况的一致性属语义层，check_spec_refs 不覆盖，
  由 AGENTS 自进化自检清单人工核对项承接。