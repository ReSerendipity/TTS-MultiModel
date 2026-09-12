"""Step-Audio-EditX 音频编辑引擎适配层。

基于 StepFun AI 开源的 Step-Audio-EditX 模型，实现对已有音频的
情绪、风格、副语言、语速等精细编辑，保留原说话人身份和语音内容。

上游实现：stepfun-ai/Step-Audio-EditX（Apache-2.0）
- 核心类：StepAudioTTS（基于 vLLM + CosyVoice 声码器）
- 编辑方法：edit(prompt_wav_path, prompt_text, edit_type, edit_info, target_text)
- 编辑类型：emotion / style / paralinguistic / speed / vad / denoise

架构定位：
    与 VoxCPM2Engine / IndexTTS2Engine 并列的引擎适配器，但不实现
    TTSEngine Protocol（文本→语音），而是独立的音频编辑接口
    （参考音频 + 编辑指令 → 编辑后音频）。通过 engine_registry 注册
    为 "step-audio-editx"，由独立路由 /api/generate/step-audio-editx/edit 调用。

与 VoiceboxEngine 的区别：
    - VoiceboxEngine：音色转换（voice conversion），改变音色，保留内容和韵律
    - StepAudioEditXEngine：音频编辑（audio editing），改变情绪/风格/副语言/语速，
      保留音色和身份，基于参考音频重新合成

依赖（懒加载，仅在 load() 时导入）：
    - step_audio_editx（上游仓库，需添加到 Python path）
    - vllm（推理后端）
    - funasr（音频 tokenizer）
    - cosyvoice（声码器）
    - torch / torchaudio / librosa / soundfile

模型权重要求：
    - model_path：Step-Audio-EditX 模型目录（含 LLM 权重 + CosyVoice-300M-25Hz）
    - tokenizer_path：Step-Audio-Tokenizer 目录（可选，默认自动检测同级目录）
"""

from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tts_multimodel")

# 支持的编辑类型
SUPPORTED_EDIT_TYPES = [
    "emotion",  # 情绪编辑：happy/angry/sad/...
    "style",  # 风格编辑：serious/arrogant/child/older/...
    "paralinguistic",  # 副语言编辑：[Laughter]/[Sigh]/...
    "speed",  # 语速编辑：faster/slower/more faster/...
    "vad",  # 语音活动检测（辅助）
    "denoise",  # 降噪（辅助）
]

# 情绪编辑支持的标签
EMOTION_TAGS = [
    "happy",
    "angry",
    "sad",
    "humour",
    "confusion",
    "disgusted",
    "empathy",
    "embarrass",
    "fear",
    "surprised",
    "excited",
    "depressed",
    "coldness",
    "admiration",
    "remove",
]

# 风格编辑支持的标签
STYLE_TAGS = [
    "serious",
    "arrogant",
    "child",
    "older",
    "girl",
    "pure",
    "sister",
    "sweet",
    "ethereal",
    "whisper",
    "gentle",
    "recite",
    "generous",
    "act_coy",
    "warm",
    "shy",
    "comfort",
    "authority",
    "chat",
    "radio",
    "soulful",
    "story",
    "vivid",
    "program",
    "news",
    "advertising",
    "roar",
    "murmur",
    "shout",
    "deeply",
    "loudly",
    "remove",
    "exaggerated",
]

# 语速编辑支持的标签
SPEED_TAGS = ["faster", "slower", "more faster", "more slower"]


@dataclass
class AudioEditResult:
    """音频编辑结果的数据类。

    Attributes:
        success: 是否编辑成功。
        audio_path: 生成音频文件的绝对路径（失败为空）。
        message: 面向用户的结果消息。
        duration: 音频时长（秒）。
        engine: 使用的引擎名称（固定为 "step-audio-editx"）。
        source_audio: 源参考音频路径（回显）。
        edit_type: 编辑类型（emotion/style/paralinguistic/speed）。
        edit_info: 编辑子类型/标签（回显）。
        params: 实际使用的编辑参数。
    """

    success: bool = False
    audio_path: str = ""
    message: str = ""
    duration: float = 0.0
    engine: str = "step-audio-editx"
    source_audio: str = ""
    edit_type: str = ""
    edit_info: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "success": self.success,
            "audio_path": self.audio_path,
            "message": self.message,
            "duration": self.duration,
            "engine": self.engine,
            "source_audio": self.source_audio,
            "edit_type": self.edit_type,
            "edit_info": self.edit_info,
            "params": self.params,
        }


class StepAudioEditXEngine:
    """Step-Audio-EditX 音频编辑引擎适配层。

    实现对已有音频的情绪、风格、副语言、语速等精细编辑，基于
    StepFun AI 开源的 Step-Audio-EditX 模型（vLLM + CosyVoice 声码器）。

    典型用法::

        engine = StepAudioEditXEngine(
            model_path="model/Step-Audio-EditX",
            repo_path="reference_repos/TTS_MultiModel/Step-Audio-EditX",
        )
        engine.load()
        result = engine.edit_audio(
            source_audio="reference.wav",
            source_text="这是参考音频的转写文本",
            edit_type="emotion",
            edit_info="happy",
            output_path="output.wav",
        )
        if result.success:
            print(f"编辑完成: {result.audio_path}")
    """

    engine_id = "step-audio-editx"

    def __init__(
        self,
        model_path: str | None = None,
        tokenizer_path: str | None = None,
        repo_path: str | None = None,
        device: str | None = None,
        gpu_memory_utilization: float = 0.5,
        max_model_len: int = 3072,
        dtype: str = "bfloat16",
    ) -> None:
        """初始化 Step-Audio-EditX 引擎。

        Args:
            model_path: 模型权重目录路径。
            tokenizer_path: Step-Audio-Tokenizer 目录（None 时自动检测同级目录）。
            repo_path: 上游仓库路径（用于添加到 Python path，导入 StepAudioTTS）。
            device: 推理设备（None 时自动检测）。
            gpu_memory_utilization: vLLM GPU 显存利用率（0.0-1.0）。
            max_model_len: 最大序列长度（影响 KV cache 大小）。
            dtype: 模型数据类型（bfloat16/float16）。
        """
        self.model_path = model_path
        self.tokenizer_path = tokenizer_path
        self.repo_path = repo_path
        self.device = device
        self.gpu_memory_utilization = gpu_memory_utilization
        self.max_model_len = max_model_len
        self.dtype = dtype
        self._loaded = False
        self._model: Any = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def load(self) -> None:
        """加载 Step-Audio-EditX 模型（vLLM + CosyVoice 声码器）。

        Raises:
            RuntimeError: 模型路径未配置或加载失败。
            ImportError: 上游仓库或依赖未正确安装。
        """
        if self._loaded:
            logger.info("[StepAudioEditX] 已加载，跳过重复加载")
            return

        if not self.model_path:
            raise RuntimeError(
                "StepAudioEditXEngine: model_path 未配置，请在 config.yaml 的 "
                "models.engines.step-audio-editx 中设置 model_dir"
            )

        model_dir = Path(self.model_path)
        if not model_dir.exists():
            raise FileNotFoundError(
                f"StepAudioEditXEngine: 模型目录不存在: {model_dir}。"
                f"请从 HuggingFace/ModelScope 下载 stepfun-ai/Step-Audio-EditX 权重。"
            )

        # 添加上游仓库到 Python path
        if self.repo_path and Path(self.repo_path).exists():
            repo_abs = str(Path(self.repo_path).resolve())
            if repo_abs not in sys.path:
                sys.path.insert(0, repo_abs)
                logger.info("[StepAudioEditX] 添加上游仓库到 Python path: %s", repo_abs)

        # 懒导入依赖
        try:
            import torch
            from tokenizer import StepAudioTokenizer
            from tts import StepAudioTTS
        except ImportError as e:
            raise ImportError(
                f"StepAudioEditXEngine: 缺少依赖。请确保上游仓库路径正确且已安装依赖 "
                f"(vllm, funasr, cosyvoice, torchaudio)。原始错误: {e}"
            ) from e

        # 自动检测设备
        if self.device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("[StepAudioEditX] 使用设备: %s", self.device)

        # 自动检测 tokenizer 路径
        tokenizer_path = self.tokenizer_path
        if tokenizer_path is None:
            # 尝试同级目录的 Step-Audio-Tokenizer
            sibling = model_dir.parent / "Step-Audio-Tokenizer"
            if sibling.exists():
                tokenizer_path = str(sibling)
                logger.info("[StepAudioEditX] 自动检测 tokenizer 路径: %s", tokenizer_path)

        # 加载 tokenizer
        logger.info("[StepAudioEditX] 加载 StepAudioTokenizer...")
        audio_tokenizer = StepAudioTokenizer(
            tokenizer_path,
            model_source="local" if tokenizer_path else "auto",
        )

        # 加载模型
        logger.info("[StepAudioEditX] 加载 StepAudioTTS (vLLM + CosyVoice)...")
        try:
            self._model = StepAudioTTS(
                model_path=str(model_dir),
                audio_tokenizer=audio_tokenizer,
                model_source="local",
                gpu_memory_utilization=self.gpu_memory_utilization,
                max_model_len=self.max_model_len,
                dtype=self.dtype,
            )
        except Exception as e:
            raise RuntimeError(f"StepAudioEditXEngine: 模型加载失败: {e}") from e

        self._loaded = True
        logger.info("[StepAudioEditX] 加载完成，模型路径: %s", self.model_path)

    def unload(self) -> None:
        """卸载模型并释放资源。"""
        if not self._loaded:
            return
        try:
            import torch

            del self._model
            self._model = None
            if self.device == "cuda":
                torch.cuda.empty_cache()
        except Exception as e:
            logger.warning("[StepAudioEditX] 卸载时发生异常（非致命）: %s", e)
        finally:
            self._loaded = False
            logger.info("[StepAudioEditX] 已卸载")

    @property
    def is_loaded(self) -> bool:
        """是否已加载模型。"""
        return self._loaded

    def is_ready(self) -> bool:
        """检查引擎是否已加载并准备就绪。"""
        return self._loaded and self._model is not None

    # ------------------------------------------------------------------
    # 编辑接口
    # ------------------------------------------------------------------

    def edit_audio(
        self,
        source_audio: str,
        source_text: str,
        edit_type: str,
        edit_info: str = "",
        *,
        target_text: str | None = None,
        output_path: str | None = None,
        **kwargs: Any,
    ) -> AudioEditResult:
        """对参考音频进行情绪/风格/副语言/语速编辑。

        Args:
            source_audio: 源参考音频路径（提供音色和语音内容）。
            source_text: 源参考音频的转写文本（必须与音频内容精确对齐）。
            edit_type: 编辑类型，支持：
                - "emotion"：情绪编辑（happy/angry/sad/...）
                - "style"：风格编辑（serious/arrogant/child/...）
                - "paralinguistic"：副语言编辑（[Laughter]/[Sigh] 标签嵌入 target_text）
                - "speed"：语速编辑（faster/slower/more faster/...）
                - "vad"：语音活动检测（辅助）
                - "denoise"：降噪（辅助）
            edit_info: 编辑子类型/标签（如 "happy"、"older"、"faster"）。
                paralinguistic 类型时可留空，副语言标签嵌入 target_text。
            target_text: 目标文本（可选）。
                - clone/paralinguistic 类型时使用，可包含副语言标签如 [Laughter]
                - None 时默认使用 source_text
            output_path: 输出音频路径（WAV 格式），None 时自动生成。
            **kwargs: 额外参数（预留扩展）。

        Returns:
            AudioEditResult: 编辑结果，包含输出音频路径和元数据。
        """
        start_time = time.time()

        # 参数校验
        if not self.is_ready():
            return AudioEditResult(
                success=False,
                message="StepAudioEditXEngine 未加载，请先调用 load()",
                source_audio=source_audio,
                edit_type=edit_type,
                edit_info=edit_info,
            )

        if edit_type not in SUPPORTED_EDIT_TYPES:
            return AudioEditResult(
                success=False,
                message=f"不支持的编辑类型: {edit_type}。支持: {SUPPORTED_EDIT_TYPES}",
                source_audio=source_audio,
                edit_type=edit_type,
                edit_info=edit_info,
            )

        if not os.path.exists(source_audio):
            return AudioEditResult(
                success=False,
                message=f"源参考音频文件不存在: {source_audio}",
                source_audio=source_audio,
                edit_type=edit_type,
                edit_info=edit_info,
            )

        if not source_text or not source_text.strip():
            return AudioEditResult(
                success=False,
                message="source_text 不能为空（必须提供参考音频的精确转写文本）",
                source_audio=source_audio,
                edit_type=edit_type,
                edit_info=edit_info,
            )

        # 校验 edit_info
        if edit_type == "emotion" and edit_info and edit_info not in EMOTION_TAGS:
            return AudioEditResult(
                success=False,
                message=f"不支持的情绪标签: {edit_info}。支持: {EMOTION_TAGS}",
                source_audio=source_audio,
                edit_type=edit_type,
                edit_info=edit_info,
            )
        if edit_type == "style" and edit_info and edit_info not in STYLE_TAGS:
            return AudioEditResult(
                success=False,
                message=f"不支持的风格标签: {edit_info}。支持: {STYLE_TAGS}",
                source_audio=source_audio,
                edit_type=edit_type,
                edit_info=edit_info,
            )
        if edit_type == "speed" and edit_info and edit_info not in SPEED_TAGS:
            return AudioEditResult(
                success=False,
                message=f"不支持的语速标签: {edit_info}。支持: {SPEED_TAGS}",
                source_audio=source_audio,
                edit_type=edit_type,
                edit_info=edit_info,
            )

        # 生成输出路径
        if output_path is None:
            output_dir = Path("outputs")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / f"audio_edit_{edit_type}_{int(time.time())}.wav")
        else:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # 确定目标文本
        if target_text is None:
            target_text = source_text

        try:
            import torchaudio

            logger.info(
                "[StepAudioEditX] 开始编辑: type=%s, info=%s, source=%s",
                edit_type,
                edit_info,
                source_audio,
            )

            # 调用上游 edit 方法
            output_audio, output_sr = self._model.edit(
                prompt_wav_path=source_audio,
                prompt_text=source_text,
                edit_type=edit_type,
                edit_info=edit_info,
                target_text=target_text,
            )

            # 保存音频
            torchaudio.save(output_path, output_audio.cpu(), output_sr)

            # 计算时长
            duration = self._get_audio_duration(output_path)
            elapsed = time.time() - start_time

            logger.info(
                "[StepAudioEditX] 编辑完成: output=%s, duration=%.2fs, elapsed=%.2fs",
                output_path,
                duration,
                elapsed,
            )

            return AudioEditResult(
                success=True,
                audio_path=os.path.abspath(output_path),
                message=f"音频编辑完成（{edit_type}/{edit_info}，耗时 {elapsed:.1f}s）",
                duration=duration,
                source_audio=source_audio,
                edit_type=edit_type,
                edit_info=edit_info,
                params={
                    "source_text": source_text,
                    "target_text": target_text,
                    "sample_rate": output_sr,
                    "elapsed_seconds": round(elapsed, 2),
                    **kwargs,
                },
            )

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error("[StepAudioEditX] 编辑失败: %s", e, exc_info=True)
            return AudioEditResult(
                success=False,
                message=f"音频编辑失败: {e}",
                source_audio=source_audio,
                edit_type=edit_type,
                edit_info=edit_info,
                params={"elapsed_seconds": round(elapsed, 2), **kwargs},
            )

    @staticmethod
    def _get_audio_duration(audio_path: str) -> float:
        """获取音频时长（秒）。"""
        try:
            import soundfile as sf

            info = sf.info(audio_path)
            return info.duration
        except Exception:
            try:
                import librosa

                return librosa.get_duration(path=audio_path)
            except Exception:
                return 0.0
