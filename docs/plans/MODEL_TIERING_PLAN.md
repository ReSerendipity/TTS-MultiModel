# 模型分级部署架构设计

> 最后更新: 2026-07-24
>
> 来源参考：Chatterbox (resemble-ai/chatterbox) Turbo/Nano/Multilingual 模型分级策略

---

## 一、背景与目标

### 现状
当前 TTS_MultiModel 项目仅支持两种引擎：
- **VoxCPM2**：2B 参数，~6GB VRAM，功能全面
- **IndexTTS2**：情感控制引擎，~4GB VRAM

这导致：
- 资源受限设备（4GB VRAM/集成显卡）无法使用
- 实时对话场景延迟过高
- 批量处理时 GPU 利用率不理想

### 目标
借鉴 Chatterbox 的三级模型策略，设计适配我们项目的模型分级架构：

```
┌─────────────────────────────────────────────────┐
│              TTS MultiModel 架构                   │
├─────────────┬──────────────┬────────────────────┤
│   Turbo     │    Nano      │    Standard        │
│  (极速版)   │  (轻量版)    │    (标准版)        │
├─────────────┼──────────────┼────────────────────┤
│ <1B params  │ 1-2B params  │ 2B+ params         │
│ <3GB VRAM   │ 3-6GB VRAM   │ 6GB+ VRAM          │
│ RTF <0.2    │ RTF <0.5     │ RTF <1.0           │
│ CPU 可用    │ CPU 勉强     │ GPU 必需           │
├─────────────┼──────────────┼────────────────────┤
│ 实时对话    │ 批量生产     │ 最高质量           │
│ 语音助手    │ 有声读物     │ 专业配音           │
└─────────────┴──────────────┴────────────────────┘
```

---

## 二、架构设计

### 2.1 模型注册表扩展

```python
# 在 engine_interface.py 中扩展
@dataclass
class ModelTier:
    """Model tier definition."""
    name: str  # "turbo", "nano", "standard"
    display_name: str
    engine_name: str  # Which engine backend to use
    vram_requirement_gb: float
    parameters_count: str  # e.g., "500M", "2B"
    rtf_target: float  # Real-time factor target
    features: list[str]  # Supported features
    cpu_compatible: bool = False
    quantization: str | None = None  # "int8", "int4", None

# 预定义分级
MODEL_TIERS = {
    "turbo": ModelTier(
        name="turbo",
        display_name="Turbo (极速版)",
        engine_name="voxcpm2-turbo",
        vram_requirement_gb=2.0,
        parameters_count="<1B",
        rtf_target=0.2,
        features=["voice_clone", "voice_design"],
        cpu_compatible=True,
        quantization="int8",
    ),
    "nano": ModelTier(
        name="nano",
        display_name="Nano (轻量版)",
        engine_name="voxcpm2-nano",
        vram_requirement_gb=4.0,
        parameters_count="1-2B",
        rtf_target=0.5,
        features=["voice_clone", "voice_design", "script"],
        cpu_compatible=False,
        quantization=None,
    ),
    "standard": ModelTier(
        name="standard",
        display_name="Standard (标准版)",
        engine_name="voxcpm2",
        vram_requirement_gb=6.0,
        parameters_count="2B+",
        rtf_target=1.0,
        features=["voice_clone", "voice_design", "script", "streaming", "lora"],
        cpu_compatible=False,
        quantization=None,
    ),
}
```

### 2.2 自动分级选择

```python
def auto_select_tier(available_vram_gb: float, requested_features: list[str]) -> str:
    """根据可用显存和请求功能自动选择最佳模型分级。

    Args:
        available_vram_gb: 可用 GPU 显存 (GB)
        requested_features: 请求的功能列表

    Returns:
        推荐的模型分级名称
    """
    # 从最高级向下匹配
    for tier_name in ["standard", "nano", "turbo"]:
        tier = MODEL_TIERS[tier_name]
        if available_vram_gb >= tier.vram_requirement_gb:
            if all(f in tier.features for f in requested_features):
                return tier_name

    # Fallback to turbo (CPU mode)
    return "turbo"
```

### 2.3 配置变更

```yaml
# config.yaml 新增部分
model_tiers:
  auto_select: true  # 自动选择最佳分级
  preferred_tier: "standard"  # 首选分级（自动选择失败时使用）
  fallback_to_cpu: true  # GPU 不足时回退到 CPU

  turbo:
    enabled: true
    quantization: "int8"  # INT8 量化减少显存
    model_path: "model/VoxCPM2-turbo"

  nano:
    enabled: false  # 需要单独训练/转换
    model_path: "model/VoxCPM2-nano"

  standard:
    model_path: "model/VoxCPM2"
```

### 2.4 量化支持

```
标准模型 (2B) ──INT8量化──> Turbo (<1B, ~3GB VRAM)
标准模型 (2B) ──INT4量化──> Nano (<0.5B, ~1.5GB VRAM)

量化工具链：
  1. torch.quantization.quantize_dynamic()
  2. GPTQ / AWQ 量化
  3. GGUF 格式 (llama.cpp 兼容)
```

---

## 三、实施路径

### Phase 1: Turbo 版本 (4周)
1. 使用 INT8 动态量化将 VoxCPM2 压缩为 Turbo 版
2. 在 `engine_interface.py` 中注册 Turbo 引擎
3. 添加自动分级选择逻辑到 `model_manager.py`
4. 更新 Web UI 显示当前使用的模型分级
5. 添加配置选项到 `config.yaml`

### Phase 2: Nano 版本 (6周)
1. 评估是否需要从头训练轻量模型
2. 或使用知识蒸馏从标准模型派生
3. 集成 Nano 引擎到引擎注册表
4. 添加 CPU 推理优化 (ONNX Runtime / llama.cpp)

### Phase 3: 自动切换 (2周)
1. 实现显存感知的自动分级选择
2. 添加降级路径：Standard → Nano → Turbo → CPU
3. 在 Web UI 中显示分级切换通知
4. 添加性能基准测试对比

---

## 四、预期收益

| 指标 | 当前 | Turbo 版 | Nano 版 |
|------|------|----------|---------|
| 最低 VRAM | 6GB | 2GB | 4GB |
| RTF (实时因子) | ~0.8 | ~0.15 | ~0.3 |
| CPU 可用 | 否 | 是 | 勉强 |
| 功能完整度 | 100% | 60% | 80% |
| 首 token 延迟 | ~2s | ~0.3s | ~0.8s |
