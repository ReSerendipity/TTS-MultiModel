# 多说话人生成增强方案

> 来源参考：Fish Speech (fishaudio/fish-speech) `<|speaker:i|>` 多说话人 token 控制
>
> 最后更新: 2026-07-24

---

## 一、背景

Fish Speech 通过 `<|speaker:i|>` token 实现单次生成包含多说话人，非常适合剧本配音场景。

当前我们的 Script Studio 已支持多角色对话，但实现方式是逐句生成后拼接，存在：
- 说话人切换不自然
- 跨句韵律不连贯
- 需要多次推理，效率低

---

## 二、目标架构

### 2.1 原始方案 vs 增强方案

```
当前 Script Studio (逐句生成):
  角色A: "你好" → 生成音频A
  角色B: "你好啊" → 生成音频B
  拼接: [音频A] + [静音] + [音频B]

增强方案 (多说话人 token):
  "<|speaker:0|>你好<|speaker:1|>你好啊" → 一次生成完整音频
  自然的角色切换，连贯的韵律
```

### 2.2 Speaker Token 机制

```python
class MultiSpeakerGenerator:
    """多说话人生成器。

    通过在输入文本中嵌入 speaker token 来控制不同角色的语音特征。
    """

    SPEAKER_TOKEN_PATTERN = re.compile(r"<\|speaker:(\d+)\|>")

    def __init__(self):
        self._speaker_embeddings: dict[int, np.ndarray] = {}
        self._speaker_names: dict[int, str] = {}

    def register_speaker(
        self,
        speaker_id: int,
        reference_audio: np.ndarray,
        sample_rate: int,
        name: str = "",
    ) -> None:
        """注册一个说话人及其参考音频。"""
        # 从参考音频提取说话人嵌入
        embedding = self._extract_speaker_embedding(reference_audio, sample_rate)
        self._speaker_embeddings[speaker_id] = embedding
        self._speaker_names[speaker_id] = name

    def generate_dialogue(
        self,
        text: str,
        speakers: dict[int, str] | None = None,
    ) -> np.ndarray:
        """生成包含多说话人的完整对话音频。

        Args:
            text: 包含 speaker token 的文本，例如：
                  "<|speaker:0|>你好！<|speaker:1|>你好啊，最近怎么样？"
            speakers: 可选的说话人映射 {speaker_id: reference_audio_path}

        Returns:
            生成的完整音频数组。
        """
        # 解析文本中的 speaker token
        segments = self._parse_segments(text)
        # 生成每个片段并拼接
        audio_segments = []
        for speaker_id, segment_text in segments:
            audio = self._generate_segment(speaker_id, segment_text)
            audio_segments.append(audio)
        return self._concatenate_segments(audio_segments)

    def _parse_segments(self, text: str) -> list[tuple[int, str]]:
        """解析文本为 (speaker_id, text) 片段列表。"""
        segments = []
        current_speaker = 0
        current_text = []

        for part in self.SPEAKER_TOKEN_PATTERN.split(text):
            if self.SPEAKER_TOKEN_PATTERN.fullmatch(f"<|speaker:{part}|>"):
                # Flush current text
                if current_text:
                    segments.append((current_speaker, "".join(current_text).strip()))
                    current_text = []
                current_speaker = int(part)
            else:
                current_text.append(part)

        # Flush remaining
        if current_text:
            segments.append((current_speaker, "".join(current_text).strip()))

        return [(sid, txt) for sid, txt in segments if txt]

    def _extract_speaker_embedding(self, audio: np.ndarray, sr: int) -> np.ndarray:
        """从参考音频提取说话人嵌入向量。"""
        # 使用 ECAPA-TDNN 或类似模型
        ...

    def _generate_segment(self, speaker_id: int, text: str) -> np.ndarray:
        """使用指定说话人的嵌入生成单个片段。"""
        embedding = self._speaker_embeddings.get(speaker_id)
        # 使用嵌入作为条件生成音频
        ...

    def _concatenate_segments(self, segments: list[np.ndarray]) -> np.ndarray:
        """拼接音频片段，添加自然过渡。"""
        ...
```

---

## 三、与现有 Script Studio 集成

### 3.1 API 扩展

```python
# routes/generate/voxcpm2/script.py 增强

class ScriptRequest(BaseModel):
    """增强的脚本请求模型。"""
    text: str
    speakers: dict[str, SpeakerConfig]  # {speaker_id: {name, reference_audio}}
    # 新增参数
    use_multi_speaker_token: bool = True  # 使用 multi-speaker token
    natural_transitions: bool = True  # 自然过渡
    crossfade_duration: float = 0.1  # 交叉淡化时长
```

### 3.2 Web UI 增强

```
剧本工坊 (增强版)
├── 角色管理
│   ├── 角色1: [上传音频] [名称: 旁白]
│   ├── 角色2: [上传音频] [名称: 小明]
│   └── + 添加角色
├── 剧本编辑
│   ├── [角色1] 你好，欢迎来到我们的节目
│   ├── [角色2] 大家好，很高兴见到大家
│   └── [角色1] 今天我们要聊的话题是...
├── 生成选项
│   ├── [x] 多说话人 Token 模式 (推荐)
│   ├── [ ] 逐句生成模式 (兼容)
│   ├── 过渡自然度: ──●───────
│   └── 交叉淡化: ──●───────
└── 预览与导出
```

---

## 四、实施路径

| Phase | 时间 | 内容 |
|-------|------|------|
| P1 | 3周 | Speaker Embedding 提取模块 |
| P2 | 3周 | Multi-Speaker Token 生成器 |
| P3 | 2周 | Script Studio 集成 |
| P4 | 2周 | Web UI 增强 + 测试 |

---

## 五、配置示例

```yaml
# config.yaml
multi_speaker:
  enabled: true
  embedding_model: "ecapa_tdnn"  # 说话人嵌入模型
  max_speakers: 10  # 单次生成最大说话人数
  crossfade_duration: 0.1  # 交叉淡化时长 (秒)
  natural_transitions: true  # 启用自然过渡
```
