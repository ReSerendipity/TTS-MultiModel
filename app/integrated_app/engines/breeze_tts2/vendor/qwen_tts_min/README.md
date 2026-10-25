# Vendored (trimmed): PyPI qwen-tts==0.1.1 的最小子集

- Upstream: https://pypi.org/project/qwen-tts/ · 源码版权 Alibaba Qwen team，Apache-2.0
  （各文件头部自带 SPDX 版权声明）
- Vendored from: qwen-tts==0.1.1 wheel（2026-10-25）
- Scope（共 4 个上游文件 + 3 个 __init__）：
  - core/__init__.py（**裁剪版**：仅导入 12Hz V2；上游还导入 25Hz V1）
  - core/tokenizer_12hz/{configuration,modeling}_qwen3_tts_tokenizer_v2.py（原样）
  - inference/qwen3_tts_tokenizer.py（**裁剪版**：删除 V1 import 与
    "qwen3_tts_tokenizer_25hz" 的 AutoConfig/AutoModel 注册）
- Why trimmed: 上游 qwen-tts 的依赖闭包含 gradio / onnxruntime / sox /
  descript-audiotools 等重依赖（~200MB，主要被 25Hz V1 的 vq 链顶层硬导入拖入）；
  Breeze TTS 2 只用 12Hz V2 tokenizer。裁剪后闭包仅为 torch/transformers/librosa/
  soundfile/numpy，无需在 requirements 中声明 qwen-tts。
- 同步修改的引用方（breeze vendor 内）：
  - models/stream_runtime/core/compat.py：qwen_tts.* → ....qwen_tts_min.*
  - breeze_infer/runtime.py：from qwen_tts import → from ..qwen_tts_min import
- Upgrade：从新版 wheel 重拷上述文件，重放两处 VENDORED-TRIM 裁剪与引用改写，
  然后 `pytest tests/engines/test_breeze_tts2_*.py -v`。
