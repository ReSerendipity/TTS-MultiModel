# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""Breeze TTS 2 引擎适配器（BreezeBlue Breeze TTS 2 · 中英双语）。

**功能定位**：
    封装 BreezeBlue 开源权重 Breeze-TTS-2（3.48B，Gemma 文本编码器 + Qwen3
    backbone + Qwen3-TTS 12Hz codec，24kHz 输出）的推理管线。核心能力与
    ``TTSEngine`` / ``ControllableTTSEngine`` 协议方法的映射：

    - Voice Clone（参考音频 + 准确转写 → 克隆）      → generate_voice_clone
    - Voice Design（自然语言音色描述，无需参考音频）  → generate_voice_design
    - Voice Direction（克隆 + 指令控制语气/情感/语速）→ generate_ultimate_clone
    - Vocal Events（正文内嵌 ``(laugh)`` / ``[笑]`` 等事件标记，随文本透传）
    - 流式生成（逐 chunk 吐 24kHz PCM）               → generate_streaming

**上游代码**：
    推理运行时固化于同包 ``vendor/``（Apache-2.0，breezeblue-ai/breeze-tts，
    见 vendor/README.md），本适配器只做加载与模板组装，不复制推理逻辑；
    音频 tokenizer 采用 ``vendor/qwen_tts_min``（qwen-tts==0.1.1 的 12Hz V2
    裁剪副本，避免 gradio/onnxruntime 等无关重依赖）。

**运行模式约束**：
    - 一律 **eager 注意力 + 全部 fast 阶段关闭**：官方 fast path 依赖 CUDA
      Graph（需 14.4GiB 显存且仅 Linux Docker 环境验证过），Windows 原生与
      12GB 级显卡均不可用；eager 峰值显存约 7.5GiB（社区 RTX 5090 实测）。
    - bf16 权重（约 7GB）+ audio_tokenizer（682MB），冷加载耗时数十秒。
    - CPU 理论可跑但 3.5B 自回归模型实时性极差，仅作应急；注册表已声明
      requires_gpu=True，切换预检会先行拦截。

**与 model_manager 的协作**：
    引擎走通用新式引擎路径（``_load_generic_engine``）：无参构造 + ``load()``；
    切换预检用 ``estimate_engine_vram_need_gb("breeze-tts2")``（基线 7.5GB ×
    可配安全裕度）。本引擎另含推理串行锁（同 IndexTTS2 模式）：上游流式
    runtime 不可重入，预热/用户请求/批量必须互斥。

**License 注意**：
    上游**源码** Apache-2.0，但**模型权重与自托管输出**为 BreezeBlue
    Research and Non-Commercial License（研究/非商用），config.yaml 的
    engines.breeze-tts2.license 与 docs/LICENSE_COMPLIANCE.md 已登记。
"""

from __future__ import annotations

import gc
import logging
import math
import os
import random
import threading
import time
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import numpy as np

from ...config import BREEZE_TTS2_MODEL_PATH, SAVE_DIR
from ...engine_interface import BaseControllableTTSEngine
from ...exceptions import (
    EngineLoadError,
    EngineNotLoadedError,
    GenerationError,
    InsufficientVRAMError,
    ValidationError,
)
from ...text_frontend import check_text_safety

logger = logging.getLogger("tts_multimodel")

#: 引擎注册名（与 engine_registry / config.yaml models.engines / model_registry.EngineName 同源）
ENGINE_NAME = "breeze-tts2"

#: 输出采样率（上游 codec 固定 24kHz 单声道）
SAMPLE_RATE = 24000

#: 模型文件完整性清单（相对 model_dir；缺失任一即拒绝加载）
REQUIRED_FILES: tuple[str, ...] = (
    "config.json",
    "generation_config.json",
    "tokenizer.json",
    "model.safetensors.index.json",
    "model-00001-of-00002.safetensors",
    "model-00002-of-00002.safetensors",
    os.path.join("audio_tokenizer", "model.safetensors"),
)

#: 生成上限（对齐上游 infer.py CLI 常量；1500 token ≈ 60s 音频 @ 24kHz 12.5Hz 帧）
_MAX_NEW_TOKENS = 1500
_MAX_SEQ_LEN = 2048
_REPETITION_PENALTY = 1.1

#: Voice Design / Voice Direction 的官方推荐 CFG；纯文本/纯克隆模板无负分支，必须为 1.0
_CFG_INSTRUCTION_DEFAULT = 4.0
_CFG_NO_NEGATIVE = 1.0

#: 加载前显存软门禁（GiB）：低于该值直接给出可行动的报错，而不是让 CUDA OOM
#: 在权重加载到一半时爆栈。口径小于 estimate_engine_vram_need_gb 的预检
#: （那侧含安全裕度），这里只挡"明显装不下"的情形。
_MIN_FREE_VRAM_GB = 7.0

# ---------------------------------------------------------------------------
# 推理串行锁：上游 FastBreezeStreamingRuntime 不可重入（同 indextts2_engine 模式）
# ---------------------------------------------------------------------------
_INFER_LOCK = threading.RLock()


class BreezeTTSEngine(BaseControllableTTSEngine):
    """Breeze TTS 2 引擎（Voice Design / Clone / Direction / Streaming / Vocal Events）。

    继承 :class:`BaseControllableTTSEngine`：未实现的 Controllable 桩
    （LoRA / Prompt 续写）保持 NotImplementedError，由路由层能力探测兜底。
    """

    def __init__(self, model_dir: str | None = None, device: str | None = None) -> None:
        """初始化引擎（不加载权重；加载发生在 :meth:`load`）。

        Args:
            model_dir: 权重目录（须含 audio_tokenizer/ 子目录）。
                默认 ``model/Breeze-TTS-2``（config.BREEZE_TTS2_MODEL_PATH）。
            device: 显式设备串（如 ``"cuda:0"``）；None 时按 GPU 后端自动检测。
        """
        super().__init__()
        self.model_dir: str = model_dir or BREEZE_TTS2_MODEL_PATH
        self.device: str | None = device
        self._tokenizer: Any = None
        self._model: Any = None
        self._audio_tokenizer: Any = None
        self._runtime: Any = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        """权重与流式 runtime 均就绪时返回 True。"""
        return self._loaded and self._runtime is not None

    def _validate_model_files(self) -> None:
        """校验权重目录完整性，缺失时抛带下载指引的 EngineLoadError。"""
        if not os.path.isdir(self.model_dir):
            raise EngineLoadError(
                f"Breeze TTS 2 模型目录不存在: {self.model_dir}\n"
                "请运行: python scripts/download_models.py --model breeze-tts2 下载模型"
                f"（约 7.7GB，含 audio_tokenizer/），或从 ModelScope/HF 镜像手动放置到该目录。",
            )
        missing = [f for f in REQUIRED_FILES if not os.path.isfile(os.path.join(self.model_dir, f))]
        if missing:
            raise EngineLoadError(
                f"Breeze TTS 2 模型文件缺失: {missing}\n目录: {self.model_dir}\n"
                "多半是下载中断，请重跑 python scripts/download_models.py --engine breeze-tts2"
                "（snapshot_download 支持断点续传）。",
            )

    def load(self) -> None:
        """加载 tokenizer / 主模型（bf16 eager）/ 音频 tokenizer 并构建流式 runtime。

        Raises:
            EngineLoadError: 权重缺失或加载失败。
            InsufficientVRAMError: CUDA 空闲显存明显不足（软门禁）或加载中 OOM。
        """
        if self.is_ready():
            logger.info("[BreezeTTS2] 引擎已加载，跳过重复加载")
            return

        self._validate_model_files()

        from ..gpu_backend import GPUBackend, GPUBackendManager

        backend = GPUBackendManager.detect_backend()
        device: str = self.device or ""
        if not device:
            if backend == GPUBackend.CUDA:
                device = "cuda:0"
            elif backend == GPUBackend.MPS:
                device = "mps"
            else:
                device = "cpu"
        self.device = device
        if device.startswith("cpu"):
            logger.warning(
                "[BreezeTTS2] 以 CPU 模式运行 3.5B 自回归模型，实时性极差（预计远慢于实时）；"
                "生产环境请使用 NVIDIA CUDA GPU（≥12GB 显存）"
            )
        if device.startswith("cuda"):
            try:
                import torch

                free_b, _total_b = torch.cuda.mem_get_info(torch.cuda.current_device())
                free_gb = free_b / (1024**3)
                if free_gb < _MIN_FREE_VRAM_GB:
                    raise InsufficientVRAMError(
                        f"CUDA 空闲显存不足：当前 {free_gb:.1f}GiB，Breeze TTS 2 eager 推理"
                        "峰值约 7.5GiB。请先切换/卸载当前引擎（释放显存），或关闭其他占用"
                        "显存的程序后重试。",
                    )
            except InsufficientVRAMError:
                raise
            except Exception as probe_err:  # noqa: BLE001 - 探测失败不阻塞加载
                logger.debug("[BreezeTTS2] 显存探测失败（忽略，继续加载）: %s", probe_err)

        logger.info("[BreezeTTS2] 开始加载模型（bf16 eager，device=%s）...", device)
        start = time.time()
        try:
            from .vendor.breeze_infer.runtime import (
                load_runtime,
                update_generation_config_for_breeze,
            )
            from .vendor.models.fast_streaming import (
                FastBreezeStreamingRuntime,
                FastStreamingConfig,
            )

            tokenizer, model, audio_tokenizer = load_runtime(
                Path(self.model_dir), device=device, attn_implementation="eager"
            )
            update_generation_config_for_breeze(model)
            # Windows/12GB 级显卡约束：全部 fast 阶段关闭（CUDA Graph 不可用），
            # 见模块 docstring「运行模式约束」。
            config = FastStreamingConfig(
                max_new_tokens=_MAX_NEW_TOKENS,
                max_seq_len=_MAX_SEQ_LEN,
                fast_all=False,
                fast_text_encoder=False,
                fast_backbone_prefill=False,
                fast_backbone_decode=False,
                fast_depth_decoder=False,
                fast_codec=False,
                repetition_penalty=_REPETITION_PENALTY,
            )
            runtime = FastBreezeStreamingRuntime(model, audio_tokenizer, config, tokenizer=tokenizer)
        except InsufficientVRAMError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一换算为引擎加载异常
            err = str(exc).lower()
            if "out of memory" in err or "cuda oom" in err:
                raise InsufficientVRAMError(f"Breeze TTS 2 权重加载触发 CUDA OOM: {exc}") from exc
            raise EngineLoadError(f"Breeze TTS 2 模型加载失败: {type(exc).__name__}: {exc}") from exc

        self._tokenizer = tokenizer
        self._model = model
        self._audio_tokenizer = audio_tokenizer
        self._runtime = runtime
        self._loaded = True
        logger.info("[BreezeTTS2] 模型加载完成，耗时 %.1fs（device=%s）", time.time() - start, device)

    def unload(self) -> None:
        """释放权重与 runtime 引用（幂等；CUDA 下顺带清空缓存）。"""
        self._runtime = None
        self._model = None
        self._audio_tokenizer = None
        self._tokenizer = None
        self._loaded = False
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001 - 卸载路径不允许抛错
            pass
        logger.info("[BreezeTTS2] 引擎已卸载")

    def get_memory_info(self) -> dict[str, Any]:
        """返回显存快照（无 CUDA 时返回空 dict），供路由/监控使用。"""
        try:
            import torch

            if torch.cuda.is_available() and self.device and str(self.device).startswith("cuda"):
                idx = int(str(self.device).split(":")[1]) if ":" in str(self.device) else 0
                free_b, total_b = torch.cuda.mem_get_info(idx)
                return {
                    "device": self.device,
                    "free_gb": round(free_b / 1024**3, 2),
                    "total_gb": round(total_b / 1024**3, 2),
                }
        except Exception:  # noqa: BLE001
            pass
        return {}

    # ------------------------------------------------------------------
    # 内部统一生成路径
    # ------------------------------------------------------------------

    def _ensure_ready(self) -> None:
        if not self.is_ready():
            raise EngineNotLoadedError(
                "Breeze TTS 2 引擎未加载，请先在设置中切换/加载该引擎",
            )

    @staticmethod
    def _resolve_seed(seed: int | None) -> int:
        """-1/None → 随机种子；正整数原样返回（确定性复现）。"""
        if seed is None or int(seed) < 0:
            return random.randint(0, 2**31 - 1)
        return int(seed)

    def _save_wav(self, audio: np.ndarray) -> str:
        """把 float32 单声道波形写盘到 SAVE_DIR，返回相对文件名。"""
        import soundfile as sf

        os.makedirs(SAVE_DIR, exist_ok=True)
        filename = f"breeze_tts2_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.wav"
        sf.write(os.path.join(SAVE_DIR, filename), audio, SAMPLE_RATE, subtype="PCM_16")
        return filename

    def _build_request(
        self,
        text: str,
        instruction: str | None,
        ref_audio_path: str | None,
        ref_text: str | None,
    ) -> dict[str, Any]:
        """组装上游模板 request（select_template_name 据此自动选 4 类模板）。"""
        request: dict[str, Any] = {"id": f"ttsm-{uuid.uuid4().hex[:8]}", "text": text, "speaker": "S0"}
        if instruction and instruction.strip():
            request["instruction"] = instruction.strip()
        if ref_audio_path:
            request["ref_audio_path"] = str(ref_audio_path)
            request["ref_text"] = (ref_text or "").strip()
        return request

    def _generate_audio(
        self,
        *,
        text: str,
        instruction: str | None = None,
        ref_audio_path: str | None = None,
        ref_text: str | None = None,
        cfg_scale: float = 1.0,
        seed: int = -1,
    ) -> np.ndarray:
        """统一推理入口：选模板 → prepare_inputs → 流式迭代并拼接整段波形。

        Returns:
            np.ndarray: float32 单声道波形（24kHz）。

        Raises:
            ValidationError: 参数组合不被上游模板支持（如克隆缺转写、克隆传 CFG>1）。
            GenerationError: 推理失败或产出空音频。
        """
        self._ensure_ready()
        if not text or not text.strip():
            raise ValidationError("文本不能为空")
        check_text_safety(text)
        if instruction:
            check_text_safety(instruction)
        if ref_audio_path is not None and not os.path.isfile(ref_audio_path):
            raise FileNotFoundError(f"参考音频不存在: {ref_audio_path}")

        if not math.isfinite(cfg_scale) or cfg_scale <= 0:
            raise ValidationError(f"cfg_scale 必须为正数，收到 {cfg_scale}")

        has_ref = ref_audio_path is not None
        if has_ref and not (ref_text and ref_text.strip()):
            raise ValidationError(
                "Breeze TTS 2 的克隆/声音导演必须同时提供参考音频的**准确转写文本**（ref_text），"
                "转写与音频不对齐会显著劣化克隆质量",
            )
        if not has_ref and (ref_text and ref_text.strip()):
            raise ValidationError("提供了 ref_text 但没有参考音频（ref_audio_path）")

        from .vendor.breeze_infer.templates import get_template, prepare_inputs, select_template_name

        request = self._build_request(text, instruction, ref_audio_path, ref_text)
        template_name = select_template_name(request)
        template = get_template(template_name)
        if cfg_scale != _CFG_NO_NEGATIVE and template.build_negative_segments is None:
            # 纯文本/纯克隆模板无负分支，上游 prepare_inputs 会因 CFG 拒绝；提前给出可读错误
            raise ValidationError(
                f"模板 {template_name} 不支持 cfg_scale>1（无负样本分支）；"
                "CFG 引导仅在 Voice Design / Voice Direction（带 instruction）下有效",
            )

        seed = self._resolve_seed(seed)
        from .vendor.breeze_infer.runtime import set_all_seeds

        with _INFER_LOCK:
            try:
                set_all_seeds(seed)
                inputs = prepare_inputs(
                    self._tokenizer,
                    self._audio_tokenizer,
                    self._model,
                    [request],
                    template,
                    guidance_scale=cfg_scale,
                    guidance_scale_ref=None,
                    guidance_scale_ins=None,
                )
                chunks = [
                    np.asarray(chunk.audio, dtype=np.float32)
                    for chunk in self._runtime.iter_audio_chunks(inputs, request_id=request["id"], seed=seed)
                ]
            except (ValidationError, FileNotFoundError):
                raise
            except Exception as exc:  # noqa: BLE001
                err = str(exc).lower()
                if "out of memory" in err or "cuda oom" in err:
                    raise GenerationError(
                        f"Breeze TTS 2 推理触发 CUDA OOM: {exc}（eager 峰值约 7.5GiB，"
                        "请缩短文本或释放其他显存占用后重试）",
                    ) from exc
                raise GenerationError(f"Breeze TTS 2 推理失败: {type(exc).__name__}: {exc}") from exc

        if not chunks:
            raise GenerationError("Breeze TTS 2 未产出任何音频块")
        audio = np.concatenate(chunks)
        if audio.size == 0:
            raise GenerationError("Breeze TTS 2 产出空音频（长度为 0）")
        return audio

    # ------------------------------------------------------------------
    # TTSEngine 协议实现
    # ------------------------------------------------------------------

    def generate_voice_design(
        self,
        text: str,
        instruction: str = "",
        normalize: bool = True,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """Voice Design：自然语言描述塑造音色（无参考音频）；instruction 为空时退化为默认音色朗读。

        kwargs:
            cfg_scale: CFG 引导强度，默认 4.0（官方推荐；仅在有 instruction 时生效）。
            seed: 随机种子，-1 随机。
        """
        has_ins = bool(instruction and instruction.strip())
        cfg = float(kwargs.get("cfg_scale", _CFG_INSTRUCTION_DEFAULT if has_ins else _CFG_NO_NEGATIVE))
        seed = int(kwargs.get("seed", -1))
        audio = self._generate_audio(
            text=text,
            instruction=instruction or None,
            cfg_scale=cfg,
            seed=seed,
        )
        filename = self._save_wav(audio)
        mode = "voice design" if has_ins else "默认音色朗读"
        return (audio, SAMPLE_RATE, filename), f"Breeze TTS 2 {mode} 完成（seed={seed}）: {filename}"

    def generate_voice_clone(
        self,
        text: str,
        reference_audio_path: str | None = None,
        instruction: str = "",
        normalize: bool = True,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """Voice Clone：参考音频 + **准确转写** → 音色克隆。

        转写来源优先级：kwargs ``ref_text``（专属路由直传）> ``instruction``
        （通用 generic/clone 路由把转写放在 instruction 位置）。Breeze 纯克隆
        模板无负分支，CFG 固定 1.0。
        """
        ref_text = kwargs.get("ref_text") or instruction
        seed = int(kwargs.get("seed", -1))
        audio = self._generate_audio(
            text=text,
            instruction=None,
            ref_audio_path=reference_audio_path,
            ref_text=ref_text,
            cfg_scale=_CFG_NO_NEGATIVE,
            seed=seed,
        )
        filename = self._save_wav(audio)
        return (audio, SAMPLE_RATE, filename), f"Breeze TTS 2 voice clone 完成（seed={seed}）: {filename}"

    def generate_ultimate_clone(
        self,
        text: str,
        instruction: str = "",
        ref_audio_path: str | None = None,
        advanced_cfg: float = 4.0,
        advanced_norm: bool = True,
        advanced_denoise: float = 1.0,
        advanced_steps: int = 10,
        advanced_seed: int = -1,
        **kwargs: Any,
    ) -> tuple[Any, str]:
        """Voice Direction（协议槽位 ultimate）：克隆音色 + 自然语言指令控制语气/情感/语速。

        与扩散系引擎不同，Breeze 为自回归模型：``advanced_denoise`` / ``advanced_steps``
        不适用（忽略并在消息中说明）；``advanced_cfg`` → 上游 CFG（默认 4.0，
        官方推荐用于强化指令遵循）；``advanced_seed`` → 随机种子。

        kwargs:
            ref_text: 参考音频的准确转写（**必填**，Voice Direction 模板要求）。
        """
        if not (instruction and instruction.strip()):
            raise ValidationError("Voice Direction 需要 instruction（如“说得慢一些，语气克制而严肃”）")
        ref_text = kwargs.get("ref_text")
        if not (ref_text and str(ref_text).strip()):
            raise ValidationError("Voice Direction 必须提供参考音频的准确转写（ref_text）")
        if ref_audio_path is None:
            raise ValidationError("Voice Direction 必须提供参考音频（ref_audio_path）")
        audio = self._generate_audio(
            text=text,
            instruction=instruction,
            ref_audio_path=ref_audio_path,
            ref_text=str(ref_text),
            cfg_scale=float(advanced_cfg),
            seed=advanced_seed,
        )
        filename = self._save_wav(audio)
        return (
            (audio, SAMPLE_RATE, filename),
            f"Breeze TTS 2 voice direction 完成（cfg={advanced_cfg}, seed={advanced_seed}; "
            f"denoise/steps 为扩散系参数，本引擎忽略）: {filename}",
        )

    def generate_streaming(
        self,
        text: str,
        reference_audio_path: str | None = None,
        **kwargs: Any,
    ) -> Generator[tuple[Any, str], None, None]:
        """流式生成：逐 chunk yield ``(float32 波形片段, 进度消息)``。

        kwargs:
            ref_text / instruction: 与非流式方法同义；两者组合决定模板
            （纯文本 / 设计 / 克隆 / 导演四选一，由上游自动选择）。
            cfg_scale / seed: 同非流式方法。
        """
        self._ensure_ready()
        if not text or not text.strip():
            raise ValidationError("文本不能为空")
        check_text_safety(text)

        instruction = kwargs.get("instruction")
        ref_text = kwargs.get("ref_text")
        if instruction:
            check_text_safety(str(instruction))
        if reference_audio_path is not None and not os.path.isfile(reference_audio_path):
            raise FileNotFoundError(f"参考音频不存在: {reference_audio_path}")
        if reference_audio_path is not None and not (ref_text and str(ref_text).strip()):
            raise ValidationError("流式克隆/导演必须同时提供参考音频的准确转写（ref_text）")

        has_ins = bool(instruction and str(instruction).strip())
        has_ref = reference_audio_path is not None
        cfg = float(
            kwargs.get(
                "cfg_scale",
                _CFG_INSTRUCTION_DEFAULT if has_ins else _CFG_NO_NEGATIVE,
            )
        )
        if has_ref and not has_ins:
            cfg = _CFG_NO_NEGATIVE  # 纯克隆模板无负分支
        seed = self._resolve_seed(int(kwargs.get("seed", -1)))

        from .vendor.breeze_infer.runtime import set_all_seeds
        from .vendor.breeze_infer.templates import get_template, prepare_inputs, select_template_name

        request = self._build_request(text, instruction, reference_audio_path, ref_text)
        template = get_template(select_template_name(request))

        with _INFER_LOCK:
            set_all_seeds(seed)
            inputs = prepare_inputs(
                self._tokenizer,
                self._audio_tokenizer,
                self._model,
                [request],
                template,
                guidance_scale=cfg,
                guidance_scale_ref=None,
                guidance_scale_ins=None,
            )
            for idx, chunk in enumerate(
                self._runtime.iter_audio_chunks(inputs, request_id=request["id"], seed=seed),
                start=1,
            ):
                audio = np.asarray(chunk.audio, dtype=np.float32)
                if audio.size:
                    yield (audio, f"Breeze TTS 2 流式生成第 {idx} 块（seed={seed}）")
