# 方言支持扩展方案

> 来源参考：CosyVoice (FunAudioLLM/CosyVoice) 18+ 中国方言支持
>
> 最后更新: 2026-07-24

---

## 一、背景

CosyVoice 支持 18+ 中国方言，包括粤语、四川话、上海话等，通过方言感知的语音合成提供更自然的本地化体验。

当前我们的项目仅支持普通话和英语，方言支持将显著扩大用户群体。

---

## 二、方言分类体系

### 2.1 中国方言层级

```
官话 (Mandarin)
├── 东北话
├── 四川话 / 重庆话
├── 河南话
├── 山东话
├── 湖北话
├── 湖南话
└── 陕西话

非官话
├── 粤语 (Cantonese)
├── 吴语
│   ├── 上海话
│   ├── 苏州话
│   └── 温州话
├── 闽南语
│   ├── 厦门话
│   ├── 潮汕话
│   └── 福州话
├── 客家话
├── 赣语
└── 徽语
```

### 2.2 方言编码

```python
DIALECT_CODES = {
    "cmn": "普通话",         # Chinese Mandarin
    "yue": "粤语",           # Cantonese
    "wuu": "吴语",           # Wu (Shanghainese etc.)
    "min": "闽南语",         # Min Nan
    "hak": "客家话",         # Hakka
    "gan": "赣语",           # Gan
    "hsn": "湘语",           # Xiang (Hunanese)
    "sjy": "四川话",         # Sichuanese
    "jil": "河南话",         # Henan dialect
    "nne": "东北话",         # Northeastern Mandarin
    "scm": "陕西话",         # Shaanxi dialect
}
```

---

## 三、技术方案

### 3.1 方言适配策略

```
方案 A: 方言微调模型 (推荐)
  标对方言数据 → 方言 LoRA 微调 → 方言适配器

  优点: 质量高，保持基座模型能力
  缺点: 需要每种方言的训练数据

方案 B: 方言标注控制
  输入文本 + 方言标签 → 方言控制指令 → 基座模型

  优点: 无需额外训练
  缺点: 方言特征可能不明显

方案 C: 混合方案
  常见方言 (粤语/四川话) → 微调适配器
  罕见方言 → 标注控制
```

### 3.2 方言 LoRA 适配器

```python
class DialectAdapter:
    """方言适配器管理器。

    每种方言对应一个 LoRA 适配器，按需加载/卸载。
    """

    DIALECT_LORA_PATHS = {
        "yue": "personas/dialects/cantonese.pt",
        "sjy": "personas/dialects/sichuanese.pt",
        "wuu": "personas/dialects/shanghainese.pt",
        "min": "personas/dialects/hokkien.pt",
        # ...
    }

    def load_dialect(self, dialect_code: str) -> bool:
        """加载指定方言的 LoRA 适配器。"""
        lora_path = self.DIALECT_LORA_PATHS.get(dialect_code)
        if not lora_path:
            return False
        # 委托给 VoxCPM2 engine 的 load_lora
        ...

    def unload_dialect(self) -> None:
        """卸载当前方言适配器。"""
        ...

    def list_available_dialects(self) -> list[dict]:
        """列出所有可用方言。"""
        ...
```

### 3.3 Web UI 扩展

```
语言/方言选择器
├── 普通话 (默认)
├── 英语
├── 日语
├── 韩语
├── 粤语
├── 四川话
├── 上海话
├── 厦门话
├── 潮汕话
├── 客家话
└── 自定义方言...
```

---

## 四、数据需求

| 方言 | 最低数据量 | 推荐数据量 | 数据来源 |
|------|-----------|-----------|---------|
| 粤语 | 1h | 10h | 公开数据集 / 用户贡献 |
| 四川话 | 1h | 5h | 公开数据集 |
| 上海话 | 0.5h | 5h | 方言保护项目 |
| 闽南语 | 1h | 10h | 闽南语歌曲/广播 |
| 客家话 | 0.5h | 3h | 客家话录音 |

---

## 五、实施路径

| Phase | 时间 | 内容 |
|-------|------|------|
| P1 | 4周 | 粤语 LoRA 适配器训练 + 集成 |
| P2 | 3周 | 四川话/上海话适配器 |
| P3 | 持续 | 社区贡献方言适配器 |
| P4 | 2周 | 方言质量评估框架 |

---

## 六、配置示例

```yaml
# config.yaml
dialects:
  enabled: true
  default_dialect: "cmn"  # 普通话

  adapters:
    yue:
      enabled: true
      model_path: "personas/dialects/cantonese.pt"
      display_name: "粤语"
    sjy:
      enabled: false
      model_path: "personas/dialects/sichuanese.pt"
      display_name: "四川话"
```
