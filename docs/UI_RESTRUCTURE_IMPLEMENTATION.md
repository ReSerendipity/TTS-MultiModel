# TTS_MultiModel 全站信息架构重构 · 实施清单

> 依据：2026-08-08 批准的线框原型（Canvas 工作区 `index.html`，可点击走查版）
> 范围：信息架构 / 布局 / 外观组件微调；**不涉及**后端路由、接口协议与模型逻辑变更
> 优先级约定：本文件 > `docs/SPEC_optimization.md` > 其他项目文档（与 AGENTS.md 冲突时以 AGENTS.md 为准）

---

## 0.5 实施进度

| 阶段 | 状态 | 说明 |
|------|------|------|
| P0 · 结构重构（D1/D2/D3） | 已完成并验证 | 2026-08-09；test_i18n/test_app/test_app_server/test_routes_htmx 共 12 用例通过；base.html 端到端渲染 6 项断言全 PASS |
| P1 · 工作区重构 | 全部完成 | D4 输出格式已移入 10 个生成 tab 输入卡（`partials/output_format.html`，base.html 移除全局选择器）；D5 右栏合并（voice_design 真结构合并 + 其余三卡 tab CSS 组卡一体化）；D6 左栏提示可收起（标签搜索过滤原本已实现，补齐关闭按钮） |
| P2 · 修复与外观 | 全部完成 | D7 i18n 泄漏修复（5 locale 补 4 键 + 模型名可读化）；D8 主题一致性排查通过；D9 引擎切换横幅内联化（删除全宽 indigo 横幅 → tab 图标旋转）；D10 截断排查通过 |

**✅ 全清单收官**：D1–D10 全部完成。两次渲染断言验证（base.html 6 项 + voice_design 单卡结构 6 项 + D6 5 项）与 12 用例测试回归全部通过。

**后续功能项（不属于本次 UI 重构，已记录）**：
1. `output_format` 后端接入：让 wav/mp3 选择真正生效（生成路由读取参数并转码），当前为装饰性控件
2. 其余 4 个三卡 tab（voice_clone/ultimate_clone/script/prompt_continue）的 DOM 级结果卡合并（当前为 CSS 视觉合一）
3. 实机验证清单见本文件第 5 节（浏览器核对清单）

**重要事实（D4 调研发现）**：前端 wav/mp3 选择器此前为**装饰性控件**——后端生成路由写死 `output_format="wav"`（仅写入历史记录），前端 JS 也不读取该 radio。D4 已把 radio 移入表单（随 htmx 提交，`name=output_format` 与后端兼容，FastAPI 忽略未声明字段、无回归），但**"选择 mp3 真正输出 mp3"需要后端接入**（生成路由读取参数并转码），已列入后续功能项，不属于本次 UI 重构范围。

> 注：`bin/integrated_app/static/` 目录在 .gitignore 中（项目现状），JS/CSS 修改不进入 git 跟踪但已在文件系统生效。

---

## 0. 结论摘要

现状代码中，线框方案的**运行时基础已有约 80%**：

- `model_switcher.js` 的 `switchModel()` 已实现"按引擎隐藏侧边栏分组"（`section-hidden` / `sidebar-item-hidden`）
- `sidebar.js` 已有分组折叠（`initCollapsibleSections`）与侧边栏整体折叠（`toggleCollapse`）
- `CachedStaticFiles` 静态资源已为 `no-cache`（`app_server.py` 注释"All static assets: no-cache"），改 CSS/JS 无需担心旧缓存

因此实施重点不是"从零搭建"，而是**消除双导航冗余、收敛顶部栏信息、重构工作区卡片、修复 i18n 键泄漏**四项结构性工作，外加外观微调。

**已确认的两处 i18n 键泄漏根因**（截图直接证据）：
- `help_gpu_note1` / `help_gpu_note2` / `help_gpu_note4` 在全部 5 个 locale 文件（zh/en/ja/ko/zh-tw）中**缺失**，`t()` 回退显示键名
- `model_size` 键同样缺失，且 `health_monitor.js:141` 直接展示后端原始 `model_size` 值（如 `voxcpm2`）

---

## 1. 决策 → 文件映射总览

| # | 线框决策 | 现状位置 | 主要改动文件 | 工作量 |
|---|---------|---------|-------------|-------|
| D1 | 顶部唯一引擎切换器，消除双导航 | `base.html` L60-219 侧边栏全模型分组 + L230-243 顶部 model-tabs | `base.html`、`model_switcher.js`、`sidebar.js` | 中 |
| D2 | 侧边栏精简（高级工具默认折叠） | `base.html` L99-132 VoxCPM2 高级分组 | `base.html`、`sidebar.js` | 小 |
| D3 | 顶部栏收敛为单系统状态入口 | `base.html` L256-279 mini-monitor 常驻数值 | `base.html`、`health_monitor.js`、CSS | 中 |
| D4 | 输出格式移入输入设置卡 | `base.html` L326-332 全局游离 | `base.html`、新建 `partials/output_format.html`、各生成 tab | 中 |
| D5 | 右栏结果/后处理/快速操作合并 | `partials/result_card.html` + 各 tab 内独立区块 | `partials/result_card.html`、`partials/post_processing.html`、各 tab | 中 |
| D6 | 左栏提示可收起 + 标签筛选 | `tabs/voice_design.html` 提示/标签/搜索 | `tabs/voice_design.html`、CSS | 小 |
| D7 | 帮助抽屉与监控面板 i18n 修复 | `base.html` L379-436、L441-543 | `locales/*.json` ×5、`health_monitor.js` | 小 |
| D8 | 主题一致性排查 | `static/css/variables.css`、`styles.css` | `variables.css`、`styles.css`、`theme_lang.js` | 小 |
| D9 | 引擎切换横幅改为内联状态 | `model_switcher.js` `updateEngineStatus('loading', …)` | `model_switcher.js`、`app_init.js`/`main.js` | 小 |
| D10 | 快速操作截断修复 | 各 tab 快速操作区 | CSS | 小 |

---

## 2. 分项实施清单

### D1 顶部唯一引擎切换器（消除双导航冗余）

**目标**：引擎（模型）维度只保留顶部 model-tabs 一个入口；侧边栏不再出现"VoxCPM2 · 语音合成"等引擎分组标题，只展示当前引擎的功能项 + 底部公共工具区。

**现状**：
- `base.html` L74-201：侧边栏静态渲染 5 个 `sidebar-nav-section`（voxcpm2 核心/高级、indextts2、dotstts、工具），分组标题含引擎名前缀
- `base.html` L230-243：顶部 model-tabs 调 `window.switchModel()`
- `model_switcher.js` L120-131：切换时已按 `data-section-model`/`data-model` 隐藏非当前分组

**改动**：
1. `base.html` 侧边栏：分组标题改为纯功能语义（如"核心功能 / 高级工具 / 工具"），去掉引擎名前缀；保留 `data-section-model` 供现有 JS 逻辑继续工作
2. （可选，推荐）将侧边栏功能项改为后端按 `current_engine` 传入渲染，模板中只保留当前引擎的分组——彻底消除"未加载模型时全部分组可见"的现状（`model_switcher.js` 中 `modelName === 'none'` 分支目前不隐藏任何分组，导致截图中的全分组状态）
3. 决策点：模型未加载（`none`）时侧边栏应显示什么？建议：显示最近一次引擎或全部功能但标注"引擎未加载"

**验证**：切换三个引擎 tab，侧边栏仅显示对应功能；未加载状态下侧边栏行为符合决策；页面不闪烁、无重复高亮。

---

### D2 侧边栏精简（默认可见项 ≤9）

**目标**：VoxCPM2 视图默认可见 8 项（核心 3 + 高级工具分组标题 1 + 工具 4），高级工具分组默认折叠。

**现状**：`base.html` L99-132 VoxCPM2 高级分组全部展开；`sidebar.js` `initCollapsibleSections()`（L483 起）已有折叠能力但默认展开。

**改动**：
1. `base.html` L99：高级工具分组增加默认 `collapsed` 类
2. `sidebar.js`：折叠状态持久化（可选，`localStorage` 需确认项目约定；或会话内记忆）

**验证**：VoxCPM2 视图侧边栏默认 8 项可见；点击分组标题可展开/收起；切换引擎后折叠状态不串扰。

---

### D3 顶部栏收敛为单系统状态入口

**目标**：GPU/显存/内存数值不再常驻顶部，收敛为单个"系统状态"chip；数值与告警分级移入健康面板。

**现状**：
- `base.html` L261-279：`#mini-monitor` 常驻三组数值（GPU%/显存/内存%），点击打开健康面板
- `health_monitor.js`：已有进度条渲染（`health-green` 类），无分级颜色

**改动**：
1. `base.html` L261-279：`#mini-monitor` 改为单个 chip（图标 + "系统状态"文案 + 状态圆点），保留 `onclick="openHealthPanel()"`
2. `health_monitor.js`：进度条按阈值分级（<60% 绿 / 60–80% 黄 / >80% 红），新增 `health-warn`/`health-danger` 样式类
3. 健康面板内新增一行"内存占用超 90% 将自动熔断推理"的说明（如现有文案已含则跳过）

**验证**：顶部不再显示原始数值；打开健康面板可见分级进度条；内存 72% 显示黄色。

---

### D4 输出格式移入输入设置卡

**目标**：`wav/mp3` 选择器从全局游离位置移入各生成页"输入设置"卡片内。

**现状**：`base.html` L326-332 全局 `format-selector-compact` 位于 tab 内容之上，所有 tab 共用；radio 名称为 `output_format`（后端表单以此读取）。

**改动（推荐方案 A）**：
1. 新建 `partials/output_format.html`：内容为现有 L327-331 的 radio 组，`name="output_format"` 保持不变（后端兼容）
2. `base.html` L326-332：删除全局区块
3. 各生成 tab（voice_design / voice_clone / ultimate_clone / script / prompt_continue / lora / indextts2 / indextts2_clone / indextts2_emotion / indextts2_duration / dotstts_clone）的输入设置卡内 include 该 partial（放在"合成文本"字段上方，与线框一致）

**备选方案 B**：保留全局 DOM，用 CSS 将其定位到输入卡视觉位置（改动最小但 DOM 语义不变，不推荐长期方案）。

**验证**：任一生成页输入卡内可见 wav/mp3 选择；选择后生成接口收到正确格式（回归测试现有测试夹具中的格式参数读取逻辑）。

---

### D5 右栏结果区合并

**目标**：结果音频为主卡片；后处理为卡内折叠子区；快速操作为卡内工具条；空状态更紧凑、无截断。

**现状**：
- `partials/result_card.html`：仅 spinner + status + audio（最小化）
- 各 tab 模板独立包含"后处理"（`{{ "post_processing"|t(lang) }}`）与"快速操作"（`{{ "quick_actions"|t(lang) }}`）区块（如 `tabs/voice_design.html` L238/L269）

**改动**：
1. 重构 `partials/result_card.html`：单卡结构 = 头部（图标+标题+格式 tag）+ 结果区（空态/加载态/结果态）+ 后处理折叠区（include `partials/post_processing.html`）+ 快速操作工具条（重新生成/复制文本/清除结果，保持现有按钮语义与 ID 前缀，避免破坏 `tts_form.js`/`reprocess.js` 的事件绑定）
2. 各 tab 移除独立的后处理/快速操作区块，统一改为 include 重构后的 `result_card.html`（通过参数传入 `prefix`、`title_key`，现有机制已支持）
3. CSS：空状态占位缩小、工具条两列布局、容器高度自适应避免截断

**注意**：`tts_form.js`、`reprocess.js`、`waveform_preview.js` 对结果区元素（`#{{ prefix }}-result`、`#{{ prefix }}-audio` 等）有事件绑定，重构时必须保持这些 ID 不变。

**验证**：三个生成页均显示单结果卡；生成模拟/真实生成后播放、保存、重新生成、复制文本、清除结果按钮全部可用；无底部截断。

---

### D6 左栏提示与标签优化

**目标**：描述提示可收起；预设标签与标签搜索合并为可筛选标签组。

**现状**：`tabs/voice_design.html` 中提示框 + 预设标签 + 搜索框三层堆叠（需按线框合并）。

**改动**：
1. 提示框增加关闭按钮（`display:none` 切换，会话内记忆）
2. 搜索框置于标签组顶部，输入时过滤 pill（纯前端过滤）

**验证**：收起提示后布局不塌；搜索词过滤标签正确；选中标签填充声音描述（现有逻辑保留）。

---

### D7 帮助抽屉与监控面板 i18n 修复（截图问题直接根因）

**目标**：无键名泄漏、无内部字段暴露、步骤编号连续。

**现状**：
- `base.html` L420-436：`help_gpu_note1/2/3/4` 四个键，其中 **1/2/4 在 5 个 locale 文件中均缺失**
- `base.html` L492-494：健康面板"当前模型"区标签 `{{ "model_size"|t(lang) }}`（键缺失）+ `health_monitor.js:141` 展示原始 `data.model.model_size`（如 `voxcpm2`）
- `help_step_*` 系列键已全部存在（模板 ol 编号连续），无需改动

**改动**：
1. `locales/zh.json`、`en.json`、`ja.json`、`ko.json`、`zh-tw.json` 各补 4 个键：
   - `help_gpu_note1`：NVIDIA 显卡完全支持，性能和稳定性最佳 / NVIDIA GPUs are fully supported with the best performance and stability
   - `help_gpu_note2`：建议使用 12GB 以上显存，生成时避免同时运行其他高显存任务 / 12GB+ VRAM recommended; avoid running other memory-heavy tasks during generation
   - `help_gpu_note4`：首次加载模型需要数分钟，请耐心等待 / First model load may take a few minutes, please wait
   - `model_size`：模型标识 / Model
2. `health_monitor.js:141`：展示用户可读名称。推荐：后端 `routes/system/health.py`（或 `model_registry`）返回 `display_name`；最低成本方案：前端维护 `{voxcpm2:'VoxCPM2 主模型', indextts2:'IndexTTS 2.0', dotstts:'dots.tts'}` 映射表兜底
3. 顺带核对：健康面板 `help_gpu_note3` 值是否与意图一致（该键存在）

**验证**：切到 4 种语言，帮助抽屉与健康面板无键名泄漏；模型标识显示为可读名称；现有 `test_i18n.py` 通过。

---

### D8 主题一致性排查

**目标**：消除"侧边栏深色 + 主内容浅色"混用（截图中语音克隆上传参考页疑似出现）。

**现状**：主题由 `theme_lang.js` + `variables.css` 控制（CSS 变量随主题切换）。

**改动**：
1. 排查 `.sidebar` 及其子元素在 `styles.css`/`variables.css` 中的背景/文字色是否全部走主题变量（而非硬编码）
2. 检查上传参考 tab 是否有独立样式覆盖（`tabs.css`/`ui_enhancements.css`）
3. 修复后刷新验证浅/深主题下侧边栏与主内容一致

**验证**：深浅主题各截一张全页图，侧边栏与主内容底色一致；主题切换即时生效（现有 transition 保留）。

---

### D9 引擎切换状态内联化

**目标**：移除全宽"正在切换引擎…"紫色横幅，切换状态收敛到顶部引擎 tab 内（旋转指示）。

**现状**：`model_switcher.js` `updateEngineStatus('loading', …)` 驱动顶部 `#engine-status`（`base.html` L256-260）；全宽横幅来源需在 `app_init.js`/`main.js` 中确认（截图可见紫色横幅元素）。

**改动**：
1. 定位横幅元素（grep `正在切换引擎|switching_engine`），删除或改为轻量 toast
2. 切换中的引擎 tab 显示 spinner（CSS 动画），完成后恢复
3. `#engine-status` 保留为就绪/错误状态指示

**验证**：切换引擎时无横幅；tab 内可见 loading 指示；切换完成状态正确。

---

### D10 快速操作截断修复

**目标**：所有页面右栏内容完整可见，无底部截断。

**现状**：部分截图"快速操作"底部被截断（容器高度/滚动问题）。

**改动**：`workspace`/结果卡容器高度策略：`min-height:0` + 允许滚动，或自适应高度；`col-right` sticky 在短视口（<760px 高）降级为 static（线框已实现该规则，实施时同步到 CSS）。

**验证**：1280×800 与更小窗口下滚动到底部，快速操作完整可见。

---

## 3. 优先级与阶段划分

| 阶段 | 内容 | 理由 |
|------|------|------|
| **P0 · 结构重构**（一次 PR） | D1 双导航消除、D2 侧边栏精简、D3 顶部收敛 | 信息架构核心，改动集中于 base.html + model_switcher.js |
| **P1 · 工作区重构**（一次 PR） | D4 格式选择器、D5 结果卡合并、D6 左栏优化 | 涉及多 tab 模板，需回归生成流程 |
| **P2 · 修复与外观**（可合并进 P1） | D7 i18n 修复、D8 主题、D9 横幅、D10 截断 | 独立小改动，可随时合入 |

建议 P0 先行落地并实测，确认导航结构后再推进 P1/P2——避免工作区改动与导航结构反复。

---

## 4. 风险与注意事项

1. **JS 事件绑定**：D5 重构结果卡时，`tts_form.js`、`reprocess.js`、`waveform_preview.js`、`api_cache.js` 依赖结果区元素的固定 ID 与结构，**ID 与 htmx 目标必须保持不变**，否则生成后行为回归
2. **格式选择器兼容**：D4 移动 DOM 但 `name="output_format"` 不变；若某 tab 通过 `FormData` 序列化整卡表单，需确认字段仍在表单内
3. **静态资源缓存**：`CachedStaticFiles` 已 no-cache；若误改回 `immutable` 会复现"改完不生效"（AGENTS.md 6.1 陷阱），改动 CSS/JS 后刷新验证
4. **`?v=` 版本号**：CSS/JS 引用带 `?v={{ app_version }}`，版本号由 `config.yaml` 驱动，无需手动递增（保持即可）
5. **模型未加载态**：D1 需明确 `none` 状态下侧边栏显示策略，避免回退到"全分组可见"
6. **i18n 键新增**：5 个 locale 文件必须同步新增，`test_i18n.py` 对键一致性有断言
7. **HTMX 局部刷新**：侧边栏/顶部栏在 `base.html`（整页壳），tab 内容由 HTMX 刷新——导航结构改动不涉及 HTMX 路由，但新 partial 被 include 后需验证 `htmx:afterSwap` 事件链（sidebar.js L361）

---

## 5. 验证方案

本地验证（与 CI 门禁对齐）：

```bash
# 启动（Windows 推荐入口）
start.bat
# 或 bin/start_app.bat（系统 Python）

# 回归测试（离线环境变量 + marker 过滤，见 AGENTS.md 2.5）
set TRANSFORMERS_OFFLINE=1 & set HF_HUB_OFFLINE=1 & set MODELSCOPE_OFFLINE=1 & set CUDA_VISIBLE_DEVICES=
.\WPy64-312101\python\python.exe -m pytest tests/ -m "not integration and not gpu and not cuda and not vram" --cov=bin/integrated_app --cov-branch --cov-report=term-missing --cov-fail-under=40
```

浏览器核对清单（每阶段验收）：
- [ ] 顶部仅一个引擎入口，切换引擎侧边栏同步变化、无重复高亮
- [ ] 侧边栏默认可见项 ≤9，高级工具分组可折叠
- [ ] 顶部无 GPU/显存/内存常驻数值；健康面板含分级进度条
- [ ] 输出格式位于输入设置卡内，生成接口格式生效
- [ ] 结果卡单卡结构，生成/播放/保存/重新生成/复制/清除全部可用，无截断
- [ ] 4 种语言下帮助抽屉与健康面板无 i18n 键泄漏、无内部字段名
- [ ] 深浅主题下侧边栏与主内容一致
- [ ] 切换引擎无全宽横幅，tab 内联 loading 正常
