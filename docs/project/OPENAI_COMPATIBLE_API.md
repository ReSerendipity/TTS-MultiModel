# OpenAI 兼容 API 设计

> 来源参考：VoxCPM (OpenBMB/VoxCPM) OpenAI 兼容 API + CosyVoice gRPC/FastAPI 服务
>
> 最后更新: 2026-07-24

---

## 一、背景

OpenAI TTS API 是业界广泛使用的语音合成接口标准。提供 OpenAI 兼容 API 将：
- 降低第三方应用接入成本
- 兼容现有 OpenAI TTS 客户端库
- 提升项目生态兼容性

---

## 二、API 规范设计

### 2.1 语音合成端点

```
POST /v1/audio/speech

请求体 (兼容 OpenAI 格式):
{
  "model": "tts-multimodel-voxcpm2",  // 模型名称
  "input": "Hello, world!",           // 要合成的文本
  "voice": "alloy",                   // 说话人 (映射到 Persona)
  "response_format": "wav",           // 音频格式: wav/mp3/opus/flac
  "speed": 1.0                        // 语速 (0.25 - 4.0)
}

响应: 音频文件二进制流 (Content-Type: audio/wav)
```

### 2.2 说话人列表端点

```
GET /v1/audio/voices

响应:
{
  "object": "list",
  "data": [
    {
      "id": "alloy",
      "object": "voice",
      "name": "旁白",
      "description": "沉稳的旁白音色",
      "preview_url": "/api/audio/persona/旁白.wav"
    },
    {
      "id": "echo",
      "object": "voice",
      "name": "小林",
      "description": "年轻男性音色",
      "preview_url": "/api/audio/persona/小林.wav"
    },
    ...
  ]
}
```

### 2.3 语音克隆端点 (扩展)

```
POST /v1/audio/clone

请求体:
{
  "model": "tts-multimodel-voxcpm2",
  "input": "Hello, world!",
  "reference_audio": <binary>,        // 参考音频文件
  "reference_text": "这是参考文本",     // 参考音频对应文本
  "response_format": "wav",
  "speed": 1.0
}

响应: 音频文件二进制流
```

### 2.4 模型信息端点

```
GET /v1/models

响应:
{
  "object": "list",
  "data": [
    {
      "id": "tts-multimodel-voxcpm2",
      "object": "model",
      "created": 1700000000,
      "owned_by": "tts-multimodel",
      "capabilities": {
        "voice_clone": true,
        "voice_design": true,
        "script_dubbing": true,
        "streaming": true,
        "emotion_control": true
      }
    },
    {
      "id": "tts-multimodel-indextts2",
      "object": "model",
      "created": 1700000000,
      "owned_by": "tts-multimodel",
      "capabilities": {
        "voice_clone": true,
        "emotion_control": true,
        "duration_control": true
      }
    }
  ]
}
```

---

## 三、实现架构

### 3.1 路由层

```python
# routes/api/openai_compat.py (新文件)

from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import Response

router = APIRouter(prefix="/v1", tags=["OpenAI Compatible"])

# 说话人映射：OpenAI voice name -> Persona name
VOICE_MAPPING = {
    "alloy": "旁白",
    "echo": "小林",
    "fable": "李老师",
    "onyx": "韩立",
    "nova": "南宫婉",
    "shimmer": "御姐",
}

@router.post("/audio/speech")
async def create_speech(
    model: str = Form("tts-multimodel-voxcpm2"),
    input: str = Form(...),
    voice: str = Form("alloy"),
    response_format: str = Form("wav"),
    speed: float = Form(1.0),
):
    """OpenAI-compatible TTS endpoint."""
    # Map voice to persona
    persona_name = VOICE_MAPPING.get(voice, voice)

    # Get current engine and generate
    from integrated_app.model_registry import registry
    if not registry.current_engine:
        raise HTTPException(503, "No engine loaded")

    engine = _get_engine()
    audio_path, message = await engine.generate_voice_clone(
        text=input,
        reference_audio_path=f"personas/{persona_name}.wav",
        normalize=True,
    )

    # Read and return audio
    audio_bytes = Path(audio_path).read_bytes()
    media_types = {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "opus": "audio/opus",
        "flac": "audio/flac",
    }
    return Response(
        content=audio_bytes,
        media_type=media_types.get(response_format, "audio/wav"),
    )


@router.get("/audio/voices")
async def list_voices():
    """List available voices (mapped from Personas)."""
    from integrated_app.persona_manager import list_personas
    personas = list_personas()

    voices = []
    reverse_mapping = {v: k for k, v in VOICE_MAPPING.items()}
    for p in personas:
        voice_id = reverse_mapping.get(p["name"], p["name"])
        voices.append({
            "id": voice_id,
            "object": "voice",
            "name": p["name"],
            "description": p.get("description", ""),
            "preview_url": f"/api/audio/persona/{p['name']}.wav",
        })

    return {"object": "list", "data": voices}


@router.get("/models")
async def list_models():
    """List available TTS models."""
    from integrated_app.model_registry import registry
    models = []
    for engine_name in ["voxcpm2", "indextts2"]:
        if registry.is_loaded(engine_name):
            models.append({
                "id": f"tts-multimodel-{engine_name}",
                "object": "model",
                "created": 1700000000,
                "owned_by": "tts-multimodel",
                "capabilities": _get_engine_capabilities(engine_name),
            })
    return {"object": "list", "data": models}
```

### 3.2 配置

```yaml
# config.yaml
openai_compat:
  enabled: true
  prefix: "/v1"  # API 前缀
  api_key: ""    # 可选的 API Key 认证

  # 说话人映射
  voice_mapping:
    alloy: "旁白"
    echo: "小林"
    fable: "李老师"
    onyx: "韩立"
    nova: "南宫婉"
    shimmer: "御姐"
```

---

## 四、客户端兼容性

### 4.1 Python 客户端

```python
# 使用 OpenAI 官方 SDK
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:7869/v1",
    api_key="not-needed",  # 本地不需要
)

response = client.audio.speech.create(
    model="tts-multimodel-voxcpm2",
    voice="alloy",
    input="你好，欢迎使用 TTS MultiModel！",
)

response.stream_to_file("output.wav")
```

### 4.2 cURL

```bash
curl -X POST http://localhost:7869/v1/audio/speech \
  -H "Content-Type: multipart/form-data" \
  -F "model=tts-multimodel-voxcpm2" \
  -F "input=Hello, world!" \
  -F "voice=alloy" \
  -F "response_format=wav" \
  --output output.wav
```

### 4.3 JavaScript

```javascript
const response = await fetch('http://localhost:7869/v1/audio/speech', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    model: 'tts-multimodel-voxcpm2',
    input: 'Hello, world!',
    voice: 'alloy',
  }),
});

const audioBlob = await response.blob();
const audioUrl = URL.createObjectURL(audioBlob);
```

---

## 五、实施路径

| Phase | 时间 | 内容 |
|-------|------|------|
| P1 | 2周 | `/v1/audio/speech` 基础端点 |
| P2 | 1周 | `/v1/audio/voices` 和 `/v1/models` |
| P3 | 2周 | `/v1/audio/clone` 克隆端点 |
| P4 | 1周 | API Key 认证和限流 |
| P5 | 1周 | 文档和客户端示例 |

---

## 六、注意事项

1. **API Key 认证**: 生产环境应启用 API Key 认证
2. **速率限制**: 建议限制并发请求数量（单 GPU 建议 max 2）
3. **格式支持**: 优先支持 WAV，MP3 需要 pydub 依赖
4. **错误码**: 遵循 OpenAI API 错误格式
5. **流式响应**: 可扩展为 SSE 流式响应 (参考 OpenAI streaming TTS)
