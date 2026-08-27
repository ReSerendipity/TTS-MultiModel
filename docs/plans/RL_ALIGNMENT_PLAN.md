# RL 后训练对齐方案

> 最后更新: 2026-07-24

> 来源参考：Fish Speech (fishaudio/fish-speech) GRPO 强化学习对齐

---

## 一、背景

Fish Speech 使用 GRPO (Group Relative Policy Optimization) 进行后训练对齐，通过多维度奖励信号显著提升生成质量：
- 语义准确性
- 指令遵循
- 声学偏好
- 音色相似度

当前我们的 VoxCPM2 引擎仅使用 CFG (Classifier-Free Guidance) 进行控制，缺乏显式的对齐优化。

---

## 二、GRPO 对齐框架

### 2.1 核心思想

```
标准 TTS 训练:
  模型 → 生成音频 → 直接优化 (CE Loss)

GRPO 对齐:
  模型 → 生成 K 个候选 → 奖励打分 → 相对排序 → 策略更新

关键区别：
  - 不需要单独的 Critic/Value 模型
  - 使用组内相对排序而非绝对分数
  - 训练更稳定，样本效率更高
```

### 2.2 奖励函数设计

```python
class TTORewardFunction:
    """TTS 对齐奖励函数。

    综合 4 个维度计算奖励分数：
    1. Semantic Accuracy (语义准确性)
    2. Instruction Following (指令遵循度)
    3. Acoustic Quality (声学质量)
    4. Speaker Similarity (音色相似度)
    """

    def compute_reward(
        self,
        generated_audio: np.ndarray,
        reference_text: str,
        reference_audio: np.ndarray | None = None,
        control_instruction: str = "",
        sample_rate: int = 48000,
    ) -> dict[str, float]:
        """计算各维度奖励。

        Returns:
            {
                "semantic": 0.0-1.0,      # 语义准确性
                "instruction": 0.0-1.0,   # 指令遵循度
                "acoustic": 0.0-1.0,      # 声学质量
                "speaker": 0.0-1.0,       # 音色相似度
                "total": 0.0-1.0,         # 加权总分
            }
        """
        rewards = {}

        # 1. 语义准确性：ASR 转写 vs 原文
        rewards["semantic"] = self._compute_semantic_reward(
            generated_audio, reference_text, sample_rate
        )

        # 2. 指令遵循度：使用 CLAP 或类似模型评估
        rewards["instruction"] = self._compute_instruction_reward(
            generated_audio, control_instruction, sample_rate
        )

        # 3. 声学质量：PESQ/MOS 预测
        rewards["acoustic"] = self._compute_acoustic_reward(
            generated_audio, sample_rate
        )

        # 4. 音色相似度：Speaker Embedding 余弦相似度
        if reference_audio is not None:
            rewards["speaker"] = self._compute_speaker_reward(
                generated_audio, reference_audio, sample_rate
            )
        else:
            rewards["speaker"] = 1.0  # 无参考音频时默认满分

        # 加权总分
        weights = {
            "semantic": 0.3,
            "instruction": 0.2,
            "acoustic": 0.3,
            "speaker": 0.2,
        }
        rewards["total"] = sum(rewards[k] * weights[k] for k in weights)

        return rewards

    def _compute_semantic_reward(self, audio, text, sr):
        """通过 ASR 转写验证语义一致性。"""
        # 使用 SenseVoiceSmall 转写
        # 计算 WER 或字符级准确率
        ...

    def _compute_instruction_reward(self, audio, instruction, sr):
        """评估生成音频是否符合控制指令。"""
        # 使用 CLAP audio-text matching 或专门的评估模型
        ...

    def _compute_acoustic_reward(self, audio, sr):
        """评估声学质量（无参考）。"""
        # 基于 PESQ 预测或神经 MOS 模型
        ...

    def _compute_speaker_reward(self, generated, reference, sr):
        """通过 Speaker Embedding 计算音色相似度。"""
        # 使用 Resemblyzer / ECAPA-TDNN 提取说话人嵌入
        # 计算余弦相似度
        ...
```

### 2.3 GRPO 训练循环

```
for each training step:
    1. 从 batch 中取 (text, instruction, ref_audio)
    2. 用当前策略生成 K 个候选音频
    3. 对每个候选计算奖励分数
    4. 组内归一化： advantage_i = (reward_i - mean) / std
    5. 计算策略梯度损失：
       L = -E[min(ratio * advantage, clip(ratio, 1-eps, 1+eps) * advantage)]
    6. 更新模型参数
```

---

## 三、实施路径

### Phase 1: 奖励模型基础设施 (3周)
1. 实现 ASR 语义准确性评估（使用已有的 SenseVoiceSmall）
2. 实现 Speaker Embedding 相似度（集成 ECAPA-TDNN 或类似模型）
3. 实现基础声学质量评估（PESQ 或 neural MOS）
4. 创建统一的奖励评估框架

### Phase 2: GRPO 训练器 (4周)
1. 实现 GRPO 采样器（生成 K 个候选）
2. 实现组内归一化和优势计算
3. 实现策略梯度损失（PPO-clip 变体）
4. 添加训练日志和指标追踪

### Phase 3: 评估与调优 (3周)
1. 设计评估数据集（100+ 样本，覆盖多种场景）
2. 对比 baseline vs GRPO 对齐后的质量
3. 调优奖励权重和超参数
4. 编写评估报告

---

## 四、配置示例

```yaml
# config.yaml
rl_alignment:
  enabled: false  # 实验性功能
  method: "grpo"

  training:
    learning_rate: 1e-6
    batch_size: 4
    candidates_per_sample: 8  # K: 每个样本生成的候选数
    clip_epsilon: 0.2  # PPO clip 范围
    entropy_coef: 0.01  # 熵正则化
    max_grad_norm: 1.0

  rewards:
    semantic_weight: 0.3
    instruction_weight: 0.2
    acoustic_weight: 0.3
    speaker_weight: 0.2

  models:
    asr: "model/SenseVoiceSmall"
    speaker_embedder: "model/ECAPATDNN"
    mos_predictor: "model/UTMOS"
```

---

## 五、预期效果

| 指标 | CFG (当前) | GRPO 对齐后 |
|------|-----------|------------|
| ASR 准确率 | ~85% | ~92% |
| 指令遵循度 | 人工判断 | 量化评估 + 提升 |
| MOS 预测分 | 3.5-3.8 | 3.9-4.2 |
| 音色相似度 | Cosine ~0.7 | Cosine ~0.85 |
