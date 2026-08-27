# TTS_MultiModel UI 审美提升实施方案

## Summary

基于对现有25张UI截图（明暗双主题）和前端代码的全面分析，参考 ElevenLabs、Suno、PlayHT、Murf 等现代AI音频工具的设计趋势，对 TTS_MultiModel WebUI 进行分阶段视觉审美提升。

核心设计方向：
- **从"工具感"到"产品感"**：提升视觉层次、留白、呼吸感
- **毛玻璃 + 微妙发光**：现代化玻璃拟态效果，增强深度感
- **去渐变、立克制**：CTA按钮改用纯色+glow，品牌色不滥用
- **微动效全面增强**：hover/active/focus/transition 平滑反馈
- **保持功能零变更**：所有改动仅涉及CSS视觉样式和极少量class替换，不改变任何功能逻辑和交互流程

技术栈保持不变：FastAPI + HTMX + Alpine.js + 原生CSS，不引入新框架。

---

## Current State Analysis

### 现有架构
- **CSS文件结构**：`variables.css`（Design Token三层架构）→ `styles.css`（遗留样式）→ `beautify.css`（美化覆盖）→ `main.css`（布局层+核心组件，使用CSS layers）→ `tabs.css`（Tab共享样式）→ `components.css`（统一组件）
- **模板结构**：`base.html`（骨架）+ `templates/tabs/*.html`（各Tab内容通过HTMX动态加载）
- **主题系统**：`:root` 浅色 + `html.dark` 深色，已支持即时切换
- **主色调**：青色系 `#0891B2` / `#06B6D4`（浅色）/ `#22D3EE`（深色）

### 识别的主要审美问题（基于截图分析）
1. **视觉层次薄弱**：卡片阴影淡（`0 1px 3px rgba(0,0,0,0.08)`）、边框细、留白不足，内容区显得拥挤
2. **按钮设计过时**：大CTA按钮使用线性渐变（`linear-gradient(135deg, #0891B2, #06B6D4)`），视觉生硬
3. **表单密度过高**：控件高度36px、内部间距小、组间距仅16px，缺少呼吸感
4. **侧边栏朴素**：纯白/深灰背景+灰色图标，选中态仅靠颜色区分，缺少层次感和品牌感
5. **顶部栏拥挤**：模型tab + 引擎状态 + GPU/VRAM/RAM监控挤在一行，视觉噪音大
6. **LoRA训练日志区**：纯灰色块（`var(--glass-black-30)`），无终端质感设计
7. **情感滑块颜色混乱**：轨道同时出现绿/红/蓝/紫/黄/绿/灰七条色带，视觉杂乱
8. **交互反馈不足**：hover/active效果弱，缺少平滑过渡和微动效
9. **文件上传区简陋**：简单虚线框，拖拽反馈缺失
10. **预设标签(Preset Tags)朴素**：灰色胶囊，选中态无明显品牌特征
11. **深色模式层次不清**：各级表面灰度差异小，边框可见度不足
12. **全局音频播放器**：设计偏功能化，缺少现代音频App的精致感

---

## Proposed Changes

### Phase 1: Design Token 升级（P0 - 基础层）

**修改文件**：`app/integrated_app/static/css/variables.css`

#### 1.1 圆角系统升级（所有主题统一）
- `--radius-sm`: 4px → 6px（小标签、徽章）
- `--radius-md`: 6px → 8px（按钮、输入框）
- `--radius-lg`: 8px → 12px（卡片）
- `--radius-xl`: 10px → 16px（大卡片、面板）
- `--radius-2xl`: 12px → 20px（模态框、大型容器）

#### 1.2 阴影层级系统升级
- Light主题：多层阴影表达深度，增加glow系列
- Dark主题：更深的投影，配合边框辅助层次区分
- 新增glow token系列：`--glow-xs/sm/md/lg`，用于选中/hover/focus的柔和发光

```css
/* Light */
--shadow-sm: 0 1px 2px rgba(0,0,0,0.04);
--shadow-md: 0 2px 6px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
--shadow-card: 0 1px 3px rgba(0,0,0,0.06), 0 4px 12px rgba(0,0,0,0.04);
--shadow-elevated: 0 4px 16px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.04);
--shadow-modal: 0 8px 32px rgba(0,0,0,0.12), 0 4px 16px rgba(0,0,0,0.08);
--glow-sm: 0 0 8px rgba(8,145,178,0.15);
--glow-md: 0 0 16px rgba(8,145,178,0.2);
--glow-lg: 0 0 24px rgba(8,145,178,0.25);
```

#### 1.3 玻璃拟态Token增强
- `--bg-glass`: 从 `rgba(255,255,255,0.88)` 降至 `rgba(255,255,255,0.72)`，更通透
- `--blur-surface`: 从12px提升到16px
- 深色模式同步调整

#### 1.4 深色模式表面层次优化
- `--bg-primary`: `#0B0D10`（最底层）
- `--bg-secondary`: `#13161B`（卡片层）
- `--bg-tertiary`: `#1A1E24`（悬浮/输入层）
- `--bg-elevated`: `#232738`（弹出层）
- `--border-subtle`: 从 `#3A3F48` 调整为 `#2A2F38`
- `--border-medium`: 从 `#4B5563` 调整为 `#3F4750`

#### 1.5 间距系统微调
- 新增 `--space-7: 28px`
- 控件内部padding-y从8px提升到10px（控件高度从36px→40px）
- 表单组间距从16px提升到20px

---

### Phase 2: 核心布局组件升级（P0 - 布局层）

**修改文件**：`app/integrated_app/static/css/main.css`

#### 2.1 侧边栏视觉升级
- 背景增加 `backdrop-filter: blur(16px)` 毛玻璃效果
- 选中项改为胶囊形背景（`border-radius: 12px`）+ 左侧3px发光指示条（`box-shadow: 0 0 6px`）
- hover效果增加 `translateX(3px)` 位移动效
- `.sidebar-item` 添加 `margin: 2px 4px` 让胶囊不贴边
- brand-icon 添加 `box-shadow: var(--glow-md)` 品牌发光
- 导航分区标签（`sidebar-nav-label`）字号缩小到10px、增加字间距、统一大写样式
- 底部按钮（主题/语言/帮助）统一圆角12px

#### 2.2 顶部状态栏优化
- 顶栏背景改为毛玻璃（`backdrop-filter: blur(16px)`）
- padding从8px增加到10px，gap增加到12px，min-height提升到48px
- 模型tab容器改为圆角pill背景（`border-radius: 16px`，`padding: 4px`，`background: var(--bg-tertiary)`）
- 激活tab去渐变，改纯色背景 + `box-shadow: var(--glow-sm)`
- 引擎状态badge改为完全胶囊形（`border-radius: 9999px`）
- mini-monitor增加hover glow效果，padding略增

#### 2.3 全局音频播放器升级
- 背景改为毛玻璃（`backdrop-filter: blur(16px)`）+ 顶部阴影（`0 -4px 24px`）
- 播放按钮改为圆形实心品牌色（38px），添加glow + 播放时脉冲动画（`@keyframes audio-btn-pulse`）
- 进度条高度从16px降至6px，进度填充增加glow，thumb改为14px圆形（始终可见，非hover才显示）
- 控件按钮（剪辑/下载/关闭/倍速）统一34px圆角8px，hover有 `translateY(-1px)` 效果
- 音量滑块样式统一

---

### Phase 3: 基础组件升级（P0 - 组件层）

**修改文件**：`app/integrated_app/static/css/components.css`

#### 3.1 卡片组件
- 圆角提升到 `--radius-lg`(12px)
- padding从16px提升到20px
- 使用新的 `--shadow-card` 多层阴影
- hover时阴影升级到 `--shadow-elevated`，accent卡片额外增加 `--glow-xs`

#### 3.2 按钮组件
- 高度从36px提升到38px
- 水平padding从16px提升到20px
- 圆角统一为 `--radius-md`(8px)
- Primary按钮：纯色背景（去掉 `--gradient-primary`），hover增加 `translateY(-1px)` + `--glow-sm`，active增加 `scale(0.98)` 按压反馈
- Secondary按钮：hover有背景变化和上浮动效
- 所有按钮transition统一为 `var(--duration-normal) var(--ease-standard)`

**注意**：`.stream-btn` 等仍在使用 `var(--gradient-primary)` 的类，统一改为纯色背景+glow

#### 3.3 输入框/文本域
- 高度从36px提升到38px
- 水平padding从12px提升到16px
- 圆角统一为 `--radius-md`(8px)
- 默认border从 `--border-medium` 改为 `--border-subtle`，hover时变medium，focus时变accent-primary
- focus状态添加 `box-shadow: 0 0 0 3px var(--primary-ring)` 外环
- textarea的min-height从80px提升到100px，padding统一16px，line-height 1.6
- select下拉框同步高度和padding

#### 3.4 Toggle开关
- 尺寸微调：track 44x24px，knob 18x18px
- 选中状态添加微弱glow
- knb添加阴影增加立体感

#### 3.5 表单标签和间距
- `tts-form-label` margin-bottom从4px提升到8px
- `tts-form-group` 垂直margin从16px提升到20px
- 标签字号保持xs但字重600，颜色text-secondary

---

### Phase 4: Tab内组件升级（P1 - 业务组件层）

**修改文件**：`app/integrated_app/static/css/tabs.css`

#### 4.1 文件上传区域升级
- 边框圆角提升到 `--radius-xl`(16px)
- padding从32px提升到32px 48px
- 背景从 `var(--primary-bg)` 改为 `var(--bg-secondary)`，边框从subtle改为medium
- hover效果：边框变accent-primary + 背景变primary-bg + `translateY(-2px)` + `--glow-sm`
- 上传图标（SVG）增大到40px，hover时 `scale(1.1)` + drop-shadow发光
- 新增 `.drag-over` 状态样式（`scale(1.01)` + 边框accent + glow-md）

**配套JS修改**：`app/integrated_app/static/js/tts_form.js` 中添加drag事件监听，切换 `.drag-over` 类

#### 4.2 预设标签(Preset Tags)升级
- padding从8px 12px提升到8px 16px
- 圆角保持 `--radius-full`
- 字号sm，字重500
- hover效果：边框变accent + 文字变accent + 背景变primary-bg + `translateY(-2px)` + shadow-sm
- active效果：纯色accent填充 + 文字白色 + `--glow-sm`
- 同步更新旧类 `.it2-voice-preset` 样式

#### 4.3 情感控制滑块重构
- **解决多色轨道杂乱问题**：移除底部绿/红/蓝/紫/黄/绿/灰七条色带
- 轨道统一为 `var(--bg-tertiary)` 灰色，通过JS动态设置线性渐变表示已填充进度
- thumb改为18px圆形，白底+3px品牌色边框，hover时scale(1.2)+glow-sm，active时scale(1.15)+glow-md
- 情感值显示统一为品牌色（`var(--accent-primary)`），使用tabular-nums等宽数字
- 情感面板（`.it2-emotion-panel`）圆角提升到12px、padding20px、使用shadow-sm

**配套JS修改**：为每个 `.tts-emotion-slider` 添加input事件，动态设置 `--value-percent` CSS变量实现轨道填充效果

#### 4.4 LoRA训练日志区域终端风格化
- 新增CSS类 `.tts-training-log`
- 背景改为 `#0B0D10`（深色终端，浅色模式也保持深色终端风）
- 文字颜色 `#4ADE80`（终端绿），等宽字体 `var(--font-mono)`，字号xs，行高1.7
- 圆角12px，padding16px，高度400px，内阴影inset增加深度
- 添加 `::before` 顶部渐变遮罩装饰
- 自定义窄滚动条（6px宽，半透明滑块）
- 训练状态区（`.tts-training-status`）改为卡片样式

**模板修改**：`app/integrated_app/templates/tabs/lora_training.html`
- 将日志区域内联 `style="background:var(--glass-black-30);..."` 替换为 `class="tts-training-log"`
- 将状态区内联样式替换为 `class="tts-training-status"`

#### 4.5 子Tab导航升级
- `.sub-tab-btn` 增加padding到8px 16px，hover添加背景色变化
- 激活态添加 `background: var(--primary-bg)` 与下划线呼应
- `.it2-emo-mode-tab`（情感模式切换tab）激活态去渐变，改纯色+glow-sm

#### 4.6 格式提示区(format-hint)升级
- 保持信息功能，增强视觉：圆角从6px→8px，padding略增

---

### Phase 5: 微动效与交互反馈（P1 - 体验增强层）

**修改文件**：`app/integrated_app/static/css/beautify.css`

#### 5.1 全局过渡统一
- 为 `button, a, input, select, textarea, .sidebar-item, .model-tab, .tts-preset-tag, .tts-card, .tts-file-upload` 统一设置 `transition-timing-function: var(--ease-standard)`
- 所有按钮 `:active` 添加 `scale(0.97)` 按压反馈
- focus-visible统一使用 `--shadow-focus`

#### 5.2 Tab内容入场动画
- 为 `#tab-content > *` 添加 `@keyframes card-entrance`（opacity 0→1 + translateY 12px→0），时长250ms

#### 5.3 骨架屏加载动画优化
- `.skeleton-block` 使用shimmer动画（线性渐变+200%背景位移+1.5s循环）替代静态灰色

#### 5.4 Toast通知升级
- 圆角提升到12px，padding增加，使用 `backdrop-filter: blur(12px)`，添加右侧滑入动画

#### 5.5 进度条增强
- 进度容器添加卡片背景+边框+圆角12px+padding+shadow-sm
- 进度条高度8px，圆角9999px，填充使用品牌色渐变+glow

#### 5.6 全局滚动条美化
- 统一8px宽滚动条，圆角4px，滑块使用 `--border-medium`，hover变 `--text-muted`
- 深色模式滑块颜色适配

#### 5.7 选中文字效果
- `::selection` 使用品牌色20%透明度背景

---

### Phase 6: 遗留样式清理与验证（P2 - 整理层）

**修改文件**：`app/integrated_app/static/css/styles.css`

#### 6.1 识别并移除重复定义
- 重点排查 `.sidebar` 及子元素在styles.css中的旧定义（约在5164行后）
- `.sidebar-item.active` 旧定义可能覆盖main.css新样式，需检查
- 非 `.tts-` 前缀的旧类（`.card`、`.btn`、`.preset-tag`）确保继承新token值

**策略**：由于styles.css在main.css/components.css之前加载，新样式通过CSS layers和后置加载顺序保证优先级。如发现覆盖冲突，在beautify.css中加最终覆盖规则。

#### 6.2 响应式验证
- 确保所有新样式在 375px（手机）/ 768px（平板）/ 1024px+（桌面）断点下正常
- 重点验证：侧边栏折叠/展开、表单控件宽度、播放器控件排列
- 毛玻璃效果在 `prefers-reduced-motion: reduce` 时降级为纯色

---

## Assumptions & Decisions

### 设计决策
1. **主色调保持青色系不变**：青色符合"科技/音频/冷静"的产品定位，不更换主色
2. **不引入新CSS框架**：继续使用原生CSS+CSS变量，保持零依赖
3. **CTA按钮去渐变**：渐变按钮在现代UI中已被纯色+微阴影+glow取代，视觉更克制高级
4. **情感滑块统一色系**：放弃多彩轨道设计，统一品牌色系，通过面板标题和上下文区分情感维度
5. **训练日志始终深色终端风**：即使在浅色模式下，日志/终端区域保持深色是行业惯例（VS Code/iTerm等）
6. **不添加波纹(ripple)效果**：Material Design风格的水波纹与现代简约AI工具风格不符，仅用scale+glow反馈
7. **玻璃拟态适度使用**：仅在sidebar/top-bar/player/toast等"浮层"元素使用，主内容卡片保持实色（避免性能问题和可读性下降）
8. **毛玻璃提供降级**：通过 `@supports not (backdrop-filter: blur())` 提供纯色fallback

### 技术假设
1. 用户的浏览器支持backdrop-filter（Chrome 76+/Firefox 103+/Safari 9+，覆盖95%+用户）
2. 现有CSS layers机制（main.css使用@layer）正常工作，新样式优先级正确
3. templates/tabs/*.html中的内联样式仅在lora_training.html有较多使用，其他tab较少
4. JavaScript改动量小（drag类切换+滑块填充进度），不影响现有功能

### 不做的事
- 不改变HTML结构和元素class命名（除了lora_training日志区的class替换）
- 不改变任何功能逻辑和API
- 不引入新图标库（继续使用Lucide风格的内联SVG）
- 不做大规模的组件重写
- 不修改Python后端代码

---

## Verification Steps

### 验证方法
1. **启动应用**：运行 `start.bat` 启动服务，访问 `127.0.0.1:7869`
2. **逐项视觉检查**：
   - [ ] 浅色模式下首页/声音设计页：卡片圆角12px、阴影层次感、按钮hover效果
   - [ ] 深色模式下侧边栏：背景层次、选中项发光条、导航分组标签
   - [ ] 顶部栏：毛玻璃效果、模型tab切换glow、监控组件hover
   - [ ] 音频播放器：播放按钮脉冲动画（播放时）、进度条发光、thumb可见
   - [ ] 表单控件：输入框高度38px、focus蓝色外环、圆角8px
   - [ ] 文件上传区：hover边框发光、拖拽时scale+glow（需实际拖拽文件测试）
   - [ ] 情感控制页：滑块无多彩条、thumb品牌色圆形、滑动时轨道填充正确
   - [ ] LoRA训练页：日志区深色终端风格、绿色等宽字体
   - [ ] 预设标签：hover上浮、选中glow
   - [ ] 全局：滚动条样式、文字选中颜色、toast通知样式
   - [ ] 主题切换：浅色↔深色切换无闪烁、所有颜色正确适配
3. **功能验证**：
   - [ ] 各Tab页面正常加载（HTMX无报错）
   - [ ] 模型切换（VoxCPM2/IndexTTS2/无）正常
   - [ ] 语音合成/克隆功能正常（音频生成+播放）
   - [ ] 侧边栏展开/折叠正常
   - [ ] 响应式：浏览器窗口缩放到375px宽度无布局错乱
4. **浏览器兼容性**：
   - [ ] Chrome/Edge正常
   - [ ] backdrop-filter不支持时fallback到纯色背景（可通过禁用浏览器该功能测试）
5. **性能检查**：
   - [ ] 动画流畅无卡顿（transform/opacity动画走GPU加速）
   - [ ] 无CSS重排性能问题（避免box-shadow动画，使用transform/opacity）

### 验收标准
提升后界面应达到：
- 视觉层次清晰，卡片浮起感明显，留白舒适
- CTA按钮醒目但不刺眼，hover有明确反馈
- 深色模式下各层表面灰度有区分，边框可见
- 所有交互元素有150-200ms平滑过渡
- 整体风格接近ElevenLabs/Suno等现代AI工具的专业感
- 功能100%与改造前一致
