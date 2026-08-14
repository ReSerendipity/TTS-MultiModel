# TTS MultiModel 总体架构文档

> 本文档面向新加入项目的贡献者，用 Mermaid 图描述三大核心架构关系。
> 建议配合 [ADR 架构决策记录](adr/) 和 [AGENTS.md](../AGENTS.md) 阅读。

---

## 一、系统分层总览

```
┌─────────────────────────────────────────────────────────┐
│                    对外接口层                              │
│  WebUI (Jinja2+Alpine.js+htmx)  │  OpenAI 兼容 API  │  MCP Server │
├─────────────────────────────────────────────────────────┤
│                    路由层 (routes/)                        │
│  generate/ │ system/ │ model/ │ training/ │ sse/ │ pages/ │
├─────────────────────────────────────────────────────────┤
│                    服务层                                  │
│  model_manager │ generation │ audio_processing │ history_db │
│  persona_manager │ task_queue │ cache │ watermark         │
├─────────────────────────────────────────────────────────┤
│                    引擎抽象层 (engines/)                    │
│  TTSEngine Protocol + EngineRegistry                      │
│  ┌──────────┐ ┌──────────────┐ ┌───────────┐            │
│  │ VoxCPM2  │ │ IndexTTS 2.5 │ │ dots.tts  │            │
│  └──────────┘ └──────────────┘ └───────────┘            │
├─────────────────────────────────────────────────────────┤
│                    基础设施层                               │
│  gpu_backend │ config │ monitor │ middleware │ security   │
│  text_frontend │ ras_sampling │ bad_case_retry            │
└─────────────────────────────────────────────────────────┘
```

---

## 二、引擎抽象层（Mermaid 图 1）

三个 TTS 引擎通过 `TTSEngine` Protocol 统一接口，路由层无需感知具体引擎实现。
`InMemoryEngineRegistry` 管理引擎类的注册与发现，`model_registry` 持有运行时引擎实例。

```mermaid
graph TB
    subgraph "路由层 (routes/generate/)"
        R_VOX["voxcpm2/ 路由<br/>design · clone · script · streaming"]
        R_IDX["indextts2/ 路由<br/>synthesize"]
        R_DOTS["generic/ 路由<br/>clone"]
    end

    subgraph "引擎抽象层 (engines/)"
        PROTOCOL["TTSEngine Protocol<br/>───<br/>is_ready() · load() · unload()<br/>generate_voice_design()<br/>generate_voice_clone()<br/>generate_script()<br/>generate_streaming()"]

        CTRL["ControllableTTSEngine Protocol<br/>extends TTSEngine<br/>───<br/>generate_with_emo_vector()<br/>generate_with_duration()"]

        REG["InMemoryEngineRegistry<br/>register() · get() · list()"]

        VOX["VoxCPM2Engine (Facade)<br/>engines/voxcpm2/<br/>───<br/>design.py · clone.py · ultimate.py<br/>script.py · streaming.py · prompt.py<br/>lora.py · _base.py"]
        IDX["IndexTTS2Engine<br/>engines/indextts2_engine.py<br/>───<br/>8维情感向量控制<br/>时长控制 · 零样本克隆"]
        DOTS["DotsTTSEngine<br/>engines/dotstts_engine.py<br/>───<br/>48kHz 高保真克隆<br/>低延迟流式"]
    end

    subgraph "运行时状态 (model_registry)"
        MREG["registry 单例<br/>───<br/>current_engine: str<br/>voxcpm_model: VoxCPM2Engine<br/>indextts2_engine: IndexTTS2Engine<br/>is_engine_ready() · set_*_loaded()"]
    end

    subgraph "底层推理库"
        LIB_VOX["VoxCPM2 / minicpm-audio<br/>(OpenBMB)"]
        LIB_IDX["indextts.infer_v2_5.IndexTTS2<br/>(IndexTeam)"]
        LIB_DOTS["dots_tts.runtime<br/>(rednote-hilab)"]
    end

    R_VOX --> PROTOCOL
    R_IDX --> CTRL
    R_DOTS --> PROTOCOL

    PROTOCOL -.-> VOX
    PROTOCOL -.-> DOTS
    CTRL -.-> IDX
    CTRL -.->|继承| PROTOCOL

    REG -->|register| VOX
    REG -->|register| IDX
    REG -->|register| DOTS

    VOX --> LIB_VOX
    IDX --> LIB_IDX
    DOTS --> LIB_DOTS

    VOX --> MREG
    IDX --> MREG
    DOTS --> MREG

    style PROTOCOL fill:#667eea,color:#fff,stroke:#333
    style CTRL fill:#764ba2,color:#fff,stroke:#333
    style REG fill:#4bc0c0,color:#fff,stroke:#333
    style MREG fill:#ff9f40,color:#fff,stroke:#333
```

**关键设计点**：

1. **Protocol 而非继承**：`TTSEngine` 是 `typing.Protocol`（鸭子类型），引擎无需显式继承，降低耦合
2. **Facade 模式**：`VoxCPM2Engine` 是外观类，将调用转发至 `voxcpm2/` 子包内各功能模块
3. **运行时注册**：`engine_registry.register()` 在 `engines/__init__.py` 模块加载时自动执行
4. **统一状态管理**：`model_registry.registry` 全局单例持有当前引擎引用，路由层通过 `registry.current_engine` 判断可用引擎

---

## 三、TTS 请求完整流程（Mermaid 图 2）

以"声音克隆"为例，展示从用户输入到音频返回的完整请求流程，包含 SSE 进度推送、
OOM 自动降级、音频后处理和历史记录。

```mermaid
sequenceDiagram
    participant U as 用户浏览器
    participant F as FastAPI 路由
    participant MW as 中间件层
    participant MM as ModelManager
    participant REG as model_registry
    participant ENG as TTSEngine
    participant AP as 音频后处理
    participant DB as history_db
    participant SSE as SSE 通道

    U->>F: POST /api/generate/voxcpm2/clone<br/>(text, ref_audio, persona)
    F->>MW: CSRF 校验 + RequestID
    MW-->>F: 校验通过

    F->>F: pre_validate()<br/>检查引擎就绪 + 文本非空
    F->>REG: registry.current_engine?
    REG-->>F: "voxcpm2"

    F->>MM: _execute_generation(semaphore)
    MM->>MM: asyncio.Semaphore.acquire()<br/>(串行锁，防并发 OOM)

    F->>SSE: 推送 status 事件<br/>(任务开始)

    rect rgb(240, 248, 255)
        Note over F,ENG: OOM 自动降级循环 (最多 2 次)
        F->>ENG: generate_voice_clone(text, ref_audio)

        alt 生成成功
            ENG->>ENG: split_text_for_tts(text)<br/>文本分段
            loop 每段音频
                ENG->>ENG: model.generate(**kwargs)
                ENG->>ENG: _check_segment_quality(wav)<br/>(RAS 段级质量检测)
                ENG->>SSE: 推送 progress 事件<br/>(第 N/M 段完成)
            end
            ENG->>ENG: merge_audio_segments()<br/>(crossfade 合并)
            ENG-->>F: (sample_rate, wav, filename)
        else CUDA OOM
            ENG-->>F: InsufficientVRAMError
            F->>F: 减半 steps + 关闭 denoise<br/>(adjust_params_for_retry)
            F->>ENG: 重试 generate_voice_clone()
        end
    end

    F->>AP: 后处理<br/>(tempo 变速 · voice_enhancement · LUFS 归一化)
    AP-->>F: 处理后音频文件

    F->>DB: history_db.insert()<br/>(音频路径 · 文本 · 引擎 · 耗时)

    F->>SSE: 推送 complete 事件<br/>(携带音频 URL)
    F-->>U: HTMLResponse<br/>(HTMX 片段 + audio 标签)

    MM->>MM: Semaphore.release()
```

**流程要点**：

1. **串行锁**：`_execute_generation` 通过 per-engine `asyncio.Semaphore`（默认容量 1）保证同一引擎不并发推理，防止显存溢出
2. **文本分段**：`split_text_for_tts` 按标点和长度切分，避免超过模型 `max_len` 限制
3. **RAS 质量检测**：每段生成后检测时长/RMS/方差三重指标，退化时自动递增 `cfg_value` 重试
4. **OOM 降级**：捕获 `InsufficientVRAMError` 后自动减半推理步数、关闭降噪重试
5. **SSE 实时推送**：进度通过独立 SSE 端点 `/api/sse/events` 推送，不阻塞主响应
6. **原子写入**：音频文件通过 tempfile + `os.replace` 原子写入，防止进程中断产生损坏文件

---

## 四、训练 → 推理权重流转（Mermaid 图 3）

LoRA 微调训练产生的权重如何加载回推理引擎，实现从训练到推理的闭环。
训练模块（`training/`）与推理模块（`engines/`）通过 checkpoint 文件解耦。

```mermaid
graph LR
    subgraph "训练阶段 (training/)"
        DATA["数据准备<br/>HFVoxCPMDataset<br/>(wav + txt 对)"]
        CFG["TrainingConfig<br/>(LoRA rank/alpha/lr/epochs)"]
        ACCEL["TrainingAccelerator<br/>(设备/精度/梯度累积)"]
        PACK["AudioFeatureProcessingPacker<br/>(batch 打包)"]
        TRAIN["训练主循环<br/>forward → loss → backward → step"]
        TRACK["TrainingTracker<br/>(SSE 推送 + TensorBoard)"]
        STATE["StateManager<br/>(checkpoint 保存/加载)"]

        DATA --> TRAIN
        CFG --> TRAIN
        ACCEL --> TRAIN
        PACK --> TRAIN
        TRAIN --> TRACK
        TRAIN --> STATE
    end

    subgraph "Checkpoint 存储 (lora/)"
        CKPT["checkpoint-N/<br/>───<br/>state.json (训练状态)<br/>pytorch_model.bin<br/>(LoRA safetensors)"]
    end

    subgraph "推理加载 (engines/voxcpm2/lora.py)"
        LOAD["fn_voxcpm_load_lora()<br/>(加载 LoRA 权重)"]
        MERGE["PeftModel.merge_and_unload()<br/>(可选：合并权重)"]
        ENABLE["fn_voxcpm_set_lora_enabled()<br/>(运行时启用/禁用)"]
        UNLOAD["fn_voxcpm_unload_lora()<br/>(卸载 LoRA)"]
    end

    subgraph "推理引擎 (model_registry)"
        MODEL["registry.voxcpm_model<br/>(VoxCPM2Engine 实例)"]
        LORA_STATE["registry.lora_state<br/>(loaded/enabled/paths)"]
    end

    subgraph "API 路由 (routes/)"
        API_TRAIN["POST /api/training/start"]
        API_LOAD["POST /api/model/lora/load"]
        API_TOGGLE["POST /api/model/lora/toggle"]
        API_GEN["POST /api/generate/voxcpm2/clone"]
    end

    STATE -->|safetensors| CKPT
    CKPT -->|文件路径| LOAD

    LOAD --> MODEL
    LOAD --> LORA_STATE
    MERGE --> MODEL
    ENABLE --> LORA_STATE
    UNLOAD --> MODEL

    API_TRAIN --> TRAIN
    API_LOAD --> LOAD
    API_TOGGLE --> ENABLE
    API_GEN --> MODEL

    MODEL -->|推理时| TRACK

    style DATA fill:#e3f2fd,stroke:#1976d2
    style CKPT fill:#fff3e0,stroke:#f57c00
    style MODEL fill:#e8f5e9,stroke:#388e3c
    style LORA_STATE fill:#e8f5e9,stroke:#388e3c
```

**权重流转要点**：

1. **训练独立**：`training/` 模块在训练过程中不直接操作推理引擎，仅产出 checkpoint 文件
2. **Checkpoint 格式**：`state.json`（可序列化训练状态）+ `pytorch_model.bin`（LoRA safetensors 权重）
3. **断点续训**：`StateManager.load_latest()` 恢复 `TrainingState`，从中断 epoch 继续
4. **运行时加载**：`fn_voxcpm_load_lora()` 将 LoRA 权重注入已加载的 `registry.voxcpm_model`
5. **动态启停**：`fn_voxcpm_set_lora_enabled()` 可在推理时热切换 LoRA 效果，无需重新加载模型
6. **权重合并**：可选调用 `merge_and_unload()` 将 LoRA 权重合并到基础权重，消除推理开销

---

## 五、关键模块速查表

| 模块 | 位置 | 职责 |
|------|------|------|
| `engine_interface.py` | `bin/integrated_app/` | `TTSEngine` Protocol + `InMemoryEngineRegistry` |
| `model_registry.py` | `bin/integrated_app/` | 全局引擎状态单例 |
| `model_manager.py` | `bin/integrated_app/` | 模型加载/卸载/切换管理（RLock 串行） |
| `generation.py` | `bin/integrated_app/` | 文本分割、音频合并、WAV 保存 |
| `gpu_backend.py` | `bin/integrated_app/` | GPU 后端抽象（CUDA/MPS/CPU） |
| `config.py` / `config_models.py` | `bin/integrated_app/` | 配置加载/验证/原子写入 |
| `history_db.py` | `bin/integrated_app/` | SQLite 异步历史记录 |
| `mcp_server.py` | `bin/integrated_app/` | MCP Server（Model Context Protocol） |
| `monitor.py` | `bin/integrated_app/` | HealthMonitor（显存/利用率/泄漏检测） |
| `task_queue.py` | `bin/integrated_app/` | 异步生成任务队列 |
| `watermark.py` / `audio_watermark.py` | `bin/integrated_app/` | FFT 频域水印嵌入/检测 |
| `ras_sampling.py` | `bin/integrated_app/` | RAS 重复感知采样（Fish Speech 借鉴） |
| `bad_case_retry.py` | `bin/integrated_app/` | 坏案例重试（参数调整 + 种子轮换） |

---

*文档生成时间：2026-08-10 | 基于 TTS MultiModel v2.2.0*
