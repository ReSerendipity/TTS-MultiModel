# Vendored (trimmed) from qwen_tts.core — 仅保留 Breeze TTS 2 所需的 12Hz (V2) tokenizer。
# 裁剪：上游此文件还导入 tokenizer_25hz (V1)，其 vq 链顶层硬导入 onnxruntime + sox，
# 与 Breeze 无关且拖入 ~200MB 重依赖，故不随附。
from .tokenizer_12hz.configuration_qwen3_tts_tokenizer_v2 import Qwen3TTSTokenizerV2Config
from .tokenizer_12hz.modeling_qwen3_tts_tokenizer_v2 import Qwen3TTSTokenizerV2Model
