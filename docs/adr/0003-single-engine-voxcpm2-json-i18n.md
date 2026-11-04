**ADR-0003: 引擎收缩为 voxcpm2 + i18n 由 gettext 改 JSON 词表**

- **状态**: Implemented
- **日期**: 2026-08-27
- **决策者**: 项目维护者 + AI 指挥（家族规范审计 Phase A 确认；多引擎为终态契约，用户 2026-08-27 确认）

---

# 背景与问题

AGENTS.md 曾声明「三引擎热插拔（CosyVoice2 / ChatTTS / F5-TTS）」与
「gettext + `common/locale/<lang>/LC_MESSAGES/messages.po`，msgmerge/msgfmt，5 语言」。
实测：`app/integrated_app/engines/` 下**只有 `voxcpm2` 一个引擎**；
`app/integrated_app/locales/` 为 **5 个 `.json` 词表**（无任何 `.po`/`.mo`/`.pot`，无 gettext 体系）。

# 评估的备选方案

- **方案 A：文档继续宣称多引擎 + gettext** —— 保留幻影，且无法通过审计器校验。不采用。
- **方案 B：文档对齐实现** —— 引擎叙述改为「多引擎 = 能力注册/切换契约多引擎、同一时刻单一激活引擎」的准确语义；
  i18n 改为 JSON 词表实现。**采用**。

# 决策

- 引擎终态：**多引擎注册/切换契约为真**（`app/integrated_app/engine_interface.py` 的 `_register_builtin_engines()` 注册表），
  当前运行时唯一激活引擎为 **VoxCPM2**（`app/integrated_app/engines/voxcpm2/`）。
- i18n 技术选型为 **JSON 词表**（`app/integrated_app/locales/<lang>.json`），废除 gettext 承诺与 `check-i18n-keys.py` 假门禁。

# 实施影响

| 位置 | 影响 |
|---|---|
| AGENTS.md §1 | 核心特色改写为当前单一引擎 VoxCpm2 + 历史多引擎说明 |
| AGENTS.md §8 | gettext 六步流程替换为 JSON 词表读取/回退流程 |
| AGENTS.md §10 | 移除 `check-i18n-coverage` 假钩子，钩子表与 `.pre-commit-config.yaml` 双向一致 |

# 可回滚路径与待验证项

- 纯文档决策。待验证：审计器对 `common/locale`、`scripts/update_pot.py` 零命中。

# 后续追加记录（不推翻原决策）

- **2026-09-03**：新增 voicebox（语音转换 / voice conversion）MCP 桥接脚手架（SCAFFOLD）。
  经检索（`grep voicebox|voice_conversion|seed-vc|so-vits-svc|OpenVoice|MegaTTS3|voxcom|voxcpm2`）
  确认本仓**无**任何已落地的语音转换引擎；`app_server.py`/`audio_processing.py` 中的
  "VoiceBox" 仅为「串行队列」设计参考注释。新增：
  - `app/integrated_app/engines/voicebox_engine.py`：`VoiceboxEngine` 适配层（接口对齐现有引擎，推理未实现）。
  - `app/integrated_app/mcp_voicebox_bridge.py`：`register_voicebox_tools(server)` + 独立 stdio 启动入口，
    复用 `mcp_server.MCPServer`，在 `_register_default_tools()` 中挂载 `voice_conversion` / `list_voicebox_models` 两个工具。
  状态：**脚手架已落地，真实后端未接入**，工具调用返回明确「未实现」提示，未编造上游 API。
- **2026-09-16**：引擎注册实况扩至 5 个（voxcpm2 / indextts2 / indextts20 / voicebox / step-audio-editx，ngine_interface._register_builtin_engines() 实测）；本 ADR「当前运行时唯一激活引擎为 VoxCPM2」的表述按『注册契约多引擎、同一时刻单激活引擎』语义更新；扩张决策详见 ADR-0004。
