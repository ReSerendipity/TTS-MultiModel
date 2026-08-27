# TTS MultiModel 双引擎语音合成 - UI 开发指南

> 最后更新: 2026-06-02

> **适用版本**：Gradio >= 4.44.0 | Python 3.12
> 不同 Gradio 版本间 API 可能存在差异，请确认版本后再参考本文档。

---

## 项目结构

```
TTS_MultiModel/
├── app/
│   ── integrated_app.py    # 主应用文件（UI 定义在此）
├── config.yaml              # 配置文件
└── requirements.txt         # 依赖列表
```

---

## UI 修改核心原则

### ✅ Gradio 框架正常支持

**重要**：Gradio 框架完全支持 `elem_classes` 和 `elem_id` 参数！

```python
# ✅ 正确 - 这些参数完全生效
with gr.Group(elem_classes=["output-panel"]):  # 完全有效
with gr.Group(elem_id="my-panel"):             # 完全有效
```

几乎所有 Gradio 组件都支持这两个参数，包括但不限于：
`gr.Blocks`、`gr.Row`、`gr.Column`、`gr.Group`、`gr.Tabs`、`gr.Tab`、
`gr.Textbox`、`gr.Button`、`gr.Audio`、`gr.Dropdown`、`gr.Markdown`、`gr.HTML` 等。

---

## 样式修改方式（按优先级排序）

### 方式一：Gradio 主题系统（推荐，最正统）

通过 `gr.themes` 可以全局控制颜色、字体、圆角等，无需写 CSS。

```python
import gradio as gr

custom_theme = gr.themes.Base(
    primary_hue="violet",
    secondary_hue="slate",
    neutral_hue="slate",
    radius_size="md",
    font=[gr.themes.GoogleFont("Noto Sans SC"), "system-ui", "sans-serif"],
).set(
    # 背景色
    background_fill_primary="#0f0f1a",
    background_fill_secondary="#171724",
    # 文字色
    body_text_color="#e2e8f0",
    body_text_color_subdued="#94a3b8",
    # 按钮色
    button_primary_background_fill="#7c3aed",
    button_primary_background_fill_hover="#8b5cf6",
    # 输入框
    input_background_fill="#1a1a2e",
    input_border_color="rgba(255,255,255,0.08)",
    # 间距
    spacing_x="20px",
    spacing_y="16px",
    block_radius="12px",
)

with gr.Blocks(theme=custom_theme, css=ENHANCED_CSS):
    # ...
```

**适用场景**：全局颜色、字体、圆角、间距等基础视觉变量。
**优势**：Gradio 原生支持，不会与框架冲突，维护成本低。

### 方式二：CSS 自定义样式（精细控制）

**位置**：`app/integrated_app.py` → `ENHANCED_CSS` 变量

```python
ENHANCED_CSS = """
/* 通过自定义类名应用样式 - 推荐方式 */
.output-panel {
    background: #8B5CF6;
    border-radius: 12px;
    padding: 16px;
}

/* 通过 ID 应用样式 */
#my-panel {
    background: #8B5CF6;
    border: none;
}

/* 使用 CSS 变量复用颜色值 */
:root {
    --color-primary: #7c3aed;
    --color-primary-hover: #8b5cf6;
    --color-bg-card: #1a1a2e;
    --color-text-primary: #e2e8f0;
    --color-text-secondary: #94a3b8;
    --radius-md: 12px;
}

.output-panel {
    background: var(--color-primary);
    border-radius: var(--radius-md);
}
"""
```

**适用场景**：主题系统覆盖不到的细节，如动画、渐变、伪元素、复杂布局等。

### 方式三：JavaScript 动态样式（最后手段）

仅在 CSS 无法解决的动态交互场景下使用，如根据运行时状态切换样式。

```javascript
// 仅在 CSS 无法解决时使用
document.querySelector('.output-panel').style.background = '#8B5CF6';
```

---

## Gradio 内置 CSS 类名速查

在浏览器中按 F12 打开开发者工具，可以查看元素的实际类名。以下是常用对照：

| 组件 | 主要 CSS 类名 |
|:---|:---|
| 整体容器 | `.gradio-container` |
| Group / 面板 | `.gr-group`、`.gr-panel`、`.gr-box` |
| Row | `.gr-row` |
| Column | `.gr-column` |
| Tabs 导航栏 | `.tab-nav` |
| Tab 按钮 | `.tab-nav button` |
| 活跃 Tab | `.tab-nav button.selected` |
| 文本输入框 | `.gr-textbox`、`textarea` |
| 按钮（主） | `.gr-button-primary`、`button.primary` |
| 按钮（次） | `.gr-button-secondary`、`button.secondary` |
| 下拉选择框 | `.gr-dropdown`、`select` |
| 音频播放器 | `.gr-audio` |
| Markdown 内容 | `.gr-markdown` |
| 折叠面板 | `.accordion` |
| 标签文字 | `label` |
| 占位文字 | `textarea::placeholder` |

> **提示**：Gradio 版本更新可能会调整类名，建议以浏览器开发者工具实际检查的结果为准。

---

## 常见修改示例

### 修改面板背景色

**Python 定义元素**：
```python
with gr.Group(elem_classes=["output-panel"]):
    gr.HTML('<h3>合成结果</h3>')
    # 其他组件
```

**CSS 设置样式**：
```css
.output-panel {
    background: #8B5CF6;
    border-radius: 12px;
    padding: 16px;
}
```

### 隐藏元素

**CSS 方式**（推荐）：
```css
.class-to-hide {
    display: none;
}
```

### 修改字体/间距

```css
.output-panel label {
    font-size: 14px;
    color: #ffffff;
}

.output-panel textarea {
    background: #14142A;
    border-radius: 8px;
}
```

### 添加过渡动画

```css
.output-panel {
    transition: all 0.25s ease;
}

.output-panel:hover {
    border-color: rgba(139, 92, 246, 0.3);
    box-shadow: 0 4px 24px rgba(139, 92, 246, 0.1);
}
```

---

## 特殊问题处理

### 白色三角形问题

某些 Gradio 组件会生成额外的 SVG 元素（如下拉框箭头），可通过 CSS 隐藏：

```css
/* 隐藏小尺寸 SVG（通常是装饰性元素） */
.gradio-container svg[width="12"][height="12"],
.gradio-container svg[width="10"][height="10"] {
    display: none;
}
```

### 样式优先级问题

**什么时候需要 `!important`**：

| 场景 | 是否需要 `!important` | 说明 |
|:---|:---|:---|
| Gradio 使用了内联 `style` 属性 | ✅ 需要 | 内联样式优先级最高，只能用 `!important` 覆盖 |
| Gradio 默认 CSS 优先级更高 | ❌ 不需要 | 提高自定义选择器的特异性即可 |
| 自定义类名与 Gradio 默认类名冲突 | ❌ 不需要 | 给自定义类名加前缀避免冲突，如 `.tts-output-panel` |

```css
/* 不推荐：到处用 !important */
.output-panel {
    background: #8B5CF6 !important;
    color: #fff !important;
}

/* 推荐：只在确认 Gradio 有内联样式时才用 */
.output-panel {
    background: #8B5CF6;          /* 正常写法 */
    border: none !important;      /* 仅此处确认 Gradio 有内联样式 */
}
```

### 自定义类名前缀规范

为避免与 Gradio 内置类名冲突，建议所有自定义类名统一加前缀：

```python
# Python 中
with gr.Group(elem_classes=["tts-output-panel"]):
with gr.Button(elem_classes=["tts-generate-btn"]):
```

```css
/* CSS 中 */
.tts-output-panel { ... }
.tts-generate-btn { ... }
```

---

## 响应式适配

### Gradio 自带参数

```python
with gr.Blocks(
    theme=custom_theme,
    css=ENHANCED_CSS,
    fill_width=True,       # 容器宽度撑满
    fill_height=True,      # 容器高度撑满
):
```

### CSS 媒体查询

```css
/* 窄屏适配 */
@media (max-width: 768px) {
    .gradio-container {
        padding: 12px !important;
    }

    /* 双栏变单栏 */
    .gr-row {
        flex-direction: column !important;
    }

    /* 标签栏可横向滚动 */
    .tab-nav {
        overflow-x: auto !important;
    }
}
```

---

## 调试技巧

### 1. 查看元素类名

在浏览器控制台中检查元素：
```javascript
// 检查元素是否包含自定义类名
console.log(document.querySelector('.tts-output-panel'));

// 列出所有 gr-group 及其内容摘要
document.querySelectorAll('.gr-group').forEach(function(el, i) {
    console.log('[' + i + ']', el.textContent.substring(0, 80));
});
```

### 2. 临时高亮元素

```css
/* 临时添加，调试后删除 */
.tts-output-panel {
    outline: 2px solid red;
}
```

### 3. 检查浏览器控制台

- 按 `F12` 打开开发者工具
- 查看 `Console` 标签页
- 检查是否有 JavaScript 错误

### 4. 检查样式优先级

在开发者工具的 `Elements` 面板中，选中目标元素，查看 `Styles` 标签：
- 被划掉的样式 = 被更高优先级的规则覆盖
- 带有 `style` 属性标签的 = 内联样式，需要 `!important` 覆盖

---

## 修改后生效步骤

1. **保存文件** - 编辑 `app/integrated_app.py`
2. **重启服务器** - 停止当前进程，重新启动
3. **刷新浏览器** - `Ctrl+Shift+R`（强制刷新，清除缓存）
4. **检查效果** - 查看页面和控制台

---

## 代码规范

### CSS 规范

- ✅ 优先使用 Gradio 主题系统处理全局变量
- ✅ 优先使用具体的 CSS 选择器，而非通配选择器
- ✅ 自定义类名统一加 `tts-` 前缀，避免与 Gradio 冲突
- ✅ 使用 CSS 变量复用颜色、圆角等值
- ✅ `!important` 仅在确认 Gradio 有内联样式时使用
- ❌ 避免对同一属性反复使用 `!important`

### Python 规范

- ✅ 使用 `elem_classes` 为组件添加自定义类
- ✅ 使用 `elem_id` 为需要 JS 交互的组件添加唯一标识符
- ❌ 避免过度使用 JavaScript 操作样式

### 修改优先级

```
Gradio 主题系统 > CSS 自定义样式 > JavaScript 动态样式
```

能通过主题解决的，不写 CSS；能通过 CSS 解决的，不写 JS。

---

## 联系方式

如有问题，请参考本文件或在项目 Issues 中提出。
