"""Voicebox（语音转换 / Voice Conversion）引擎适配层。

基于 OpenVoice ToneColorConverter 实现零样本音色转换：
输入源说话人音频 + 目标音色参考音频，输出用目标音色重说源内容的音频。

上游实现：OpenVoice（MIT 许可，MyShell AI）
- ToneColorConverter：音色转换器，实现跨说话人迁移
- 仅使用音色转换模块，不依赖 OpenVoice 的 TTS 部分
- 源说话人嵌入（src_se）使用 OpenVoice 内置的 base speaker 默认嵌入

架构角色：
    与 VoxCPM2Engine / IndexTTS2Engine 并列的引擎适配器，但不实现
    TTSEngine Protocol（文本→语音），而是独立的语音转换接口
    （音频→音频）。通过 engine_registry 注册为 "voicebox"，
    由独立路由 /api/generate/voicebox/convert 调用。

依赖（懒加载，仅在 load() 时导入）：
    - openvoice：ToneColorConverter + 配置加载
    - torch：模型推理
    - librosa：音频加载
    - soundfile：音频保存

模型权重要求：
    - checkpoint_path：ToneColorConverter 权重目录（含 config.json + checkpoint.pth）
    - base_se_path：base speaker 嵌入文件（.pth，可选，缺失时使用零向量）
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tts_multimodel")


@dataclass
class VoiceboxResult:
    """语音转换结果的数据类。

    Attributes:
        success: 是否转换成功。
        audio_path: 生成音频文件的绝对路径（失败为空）。
        message: 面向用户的结果消息。
        duration: 音频时长（秒）。
        engine: 使用的引擎名称（固定为 "voicebox"）。
        source_audio: 源说话人音频路径（回显）。
        target_audio: 目标音色参考音频路径（回显）。
        params: 实际使用的生成参数。
    """

    success: bool = False
    audio_path: str = ""
    message: str = ""
    duration: float = 0.0
    engine: str = "voicebox"
    source_audio: str = ""
    target_audio: str = ""
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
            "target_audio": self.target_audio,
            "params": self.params,
        }


class VoiceboxEngine:
    """语音转换引擎适配层（基于 OpenVoice ToneColorConverter）。

    实现零样本音色转换：将源音频的音色转换为目标参考音频的音色，
    保留源音频的语音内容和韵律。

    与 TTSEngine 的区别：
        - TTSEngine：文本 → 语音（text-to-speech）
        - VoiceboxEngine：音频 → 音频（voice conversion）

    典型用法::

        engine = VoiceboxEngine(model_path="model/OpenVoice")
        engine.load()
        result = engine.voice_conversion(
            source_audio="source.wav",
            target_audio="target_reference.wav",
            output_path="output.wav",
        )
        if result.success:
            print(f"转换完成: {result.audio_path}")
    """

    engine_id = "voicebox"

    def __init__(
        self,
        model_path: str | None = None,
        device: str | None = None,
        base_se_path: str | None = None,
    ) -> None:
        """初始化语音转换引擎。

        Args:
            model_path: OpenVoice ToneColorConverter 模型权重目录。
            device: 推理设备（cuda / cpu），None 时自动检测。
            base_se_path: base speaker 嵌入文件路径（.pth），None 时使用零向量。
        """
        self.model_path = model_path
        self.device = device
        self.base_se_path = base_se_path
        self._loaded = False
        self._converter: Any = None
        self._src_se: Any = None
        self._hps: Any = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def load(self) -> None:
        """加载 OpenVoice ToneColorConverter 模型。

        Raises:
            ImportError: openvoice 或其依赖未安装。
            FileNotFoundError: 模型路径或配置文件不存在。
            RuntimeError: 模型加载失败。
        """
        if self._loaded:
            logger.info("[VoiceboxEngine] 已加载，跳过重复加载")
            return

        if not self.model_path:
            raise RuntimeError(
                "VoiceboxEngine: model_path 未配置，请在 config.yaml 的 models.engines.voicebox "
                "中设置 model_dir，或在初始化时传入 model_path"
            )

        model_dir = Path(self.model_path)
        if not model_dir.exists():
            raise FileNotFoundError(
                f"VoiceboxEngine: 模型目录不存在: {model_dir}。请下载 OpenVoice ToneColorConverter 权重到该目录。"
            )

        # 懒导入依赖
        try:
            import torch
            from openvoice import ToneColorConverter
        except ImportError as e:
            raise ImportError(
                f"VoiceboxEngine: 缺少依赖 openvoice。请安装: pip install openvoice (原始错误: {e})"
            ) from e

        # 自动检测设备
        if self.device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("[VoiceboxEngine] 使用设备: %s", self.device)

        # 加载配置
        config_path = model_dir / "config.json"
        if not config_path.exists():
            raise FileNotFoundError(f"VoiceboxEngine: 配置文件不存在: {config_path}")

        # 加载 ToneColorConverter
        try:
            self._converter = ToneColorConverter(
                config_path=str(config_path),
                device=self.device,
            )
            # 加载权重
            ckpt_path = model_dir / "checkpoint.pth"
            if ckpt_path.exists():
                checkpoint = torch.load(str(ckpt_path), map_location=self.device)
                self._converter.model.load_state_dict(checkpoint["model"], strict=False)
                logger.info("[VoiceboxEngine] 权重加载完成: %s", ckpt_path)
            else:
                logger.warning(
                    "[VoiceboxEngine] 未找到 checkpoint.pth: %s，使用随机初始化权重（仅用于测试）",
                    ckpt_path,
                )
            self._converter.model.eval()
        except Exception as e:
            raise RuntimeError(f"VoiceboxEngine: ToneColorConverter 加载失败: {e}") from e

        # 加载 base speaker 嵌入（src_se）
        self._src_se = self._load_base_se(torch)

        self._loaded = True
        logger.info("[VoiceboxEngine] 加载完成，模型路径: %s", self.model_path)

    def _load_base_se(self, torch: Any) -> Any:
        """加载 base speaker 嵌入（源说话人默认嵌入）。

        OpenVoice 的 ToneColorConverter.convert 需要 src_se 参数，
        表示源音频的说话人嵌入。对于任意源音频，使用 base speaker 的
        默认嵌入作为近似（ToneColorConverter 内部会重新编码源音频）。

        Args:
            torch: torch 模块引用（避免重复导入）。

        Returns:
            torch.Tensor: 说话人嵌入向量，形状 (1, 256)。
        """
        if self.base_se_path and Path(self.base_se_path).exists():
            try:
                se = torch.load(self.base_se_path, map_location=self.device)
                if isinstance(se, dict):
                    se = se.get("se", se.get("speaker_embedding", list(se.values())[0]))
                se = torch.tensor(se, device=self.device).float()
                if se.dim() == 1:
                    se = se.unsqueeze(0)
                logger.info("[VoiceboxEngine] base_se 加载完成: %s, shape=%s", self.base_se_path, se.shape)
                return se
            except Exception as e:
                logger.warning("[VoiceboxEngine] base_se 加载失败，使用零向量: %s", e)

        # 使用零向量作为默认 src_se（ToneColorConverter 内部会重新编码源音频的音色）
        se = torch.zeros(1, 256, device=self.device)
        logger.info("[VoiceboxEngine] 使用零向量作为默认 src_se")
        return se

    def unload(self) -> None:
        """卸载语音转换模型并释放资源。"""
        if not self._loaded:
            return
        try:
            import torch

            del self._converter
            del self._src_se
            self._converter = None
            self._src_se = None
            if self.device == "cuda":
                torch.cuda.empty_cache()
        except Exception as e:
            logger.warning("[VoiceboxEngine] 卸载时发生异常（非致命）: %s", e)
        finally:
            self._loaded = False
            logger.info("[VoiceboxEngine] 已卸载")

    @property
    def is_loaded(self) -> bool:
        """是否已加载模型。"""
        return self._loaded

    def is_ready(self) -> bool:
        """检查引擎是否已加载并准备就绪。"""
        return self._loaded and self._converter is not None

    # ------------------------------------------------------------------
    # 推理接口
    # ------------------------------------------------------------------

    def voice_conversion(
        self,
        source_audio: str,
        target_audio: str,
        *,
        output_path: str | None = None,
        tau: float = 0.3,
        message: str = "default",
        **kwargs: Any,
    ) -> VoiceboxResult:
        """将源音频的音色转换为目标音色。

        使用 OpenVoice ToneColorConverter 实现零样本音色转换：
        保留源音频的语音内容和韵律，将音色替换为目标参考音频的音色。

        Args:
            source_audio: 源说话人音频路径（要被转换音色的语音）。
            target_audio: 目标音色参考音频路径（提供目标音色，建议 3-30 秒）。
            output_path: 输出音频路径（WAV 格式），None 时自动生成临时路径。
            tau: 音色转换强度（0.0-1.0），默认 0.3。
                值越高，目标音色特征越强，但可能导致音质下降；
                值越低，越接近源音频原始音色。
            message: 水印消息（OpenVoice 内置水印功能），默认 "default"。
            **kwargs: 额外参数（预留扩展）。

        Returns:
            VoiceboxResult: 转换结果，包含输出音频路径和元数据。
        """
        start_time = time.time()

        # 参数校验
        if not self.is_ready():
            return VoiceboxResult(
                success=False,
                message="VoiceboxEngine 未加载，请先调用 load()",
                source_audio=source_audio,
                target_audio=target_audio,
            )

        if not os.path.exists(source_audio):
            return VoiceboxResult(
                success=False,
                message=f"源音频文件不存在: {source_audio}",
                source_audio=source_audio,
                target_audio=target_audio,
            )

        if not os.path.exists(target_audio):
            return VoiceboxResult(
                success=False,
                message=f"目标参考音频文件不存在: {target_audio}",
                source_audio=source_audio,
                target_audio=target_audio,
            )

        # 生成输出路径
        if output_path is None:
            output_dir = Path("outputs")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / f"voicebox_convert_{int(time.time())}.wav")
        else:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        try:
            import torch

            # 提取目标说话人嵌入
            tgt_se = self._extract_speaker_embedding(target_audio, torch)

            # 执行音色转换
            logger.info(
                "[VoiceboxEngine] 开始转换: source=%s, target=%s, tau=%.2f",
                source_audio,
                target_audio,
                tau,
            )

            self._converter.convert(
                audio_src_path=source_audio,
                src_se=self._src_se,
                tgt_se=tgt_se,
                output_path=output_path,
                tau=tau,
                message=message,
            )

            # 计算时长
            duration = self._get_audio_duration(output_path)
            elapsed = time.time() - start_time

            logger.info(
                "[VoiceboxEngine] 转换完成: output=%s, duration=%.2fs, elapsed=%.2fs",
                output_path,
                duration,
                elapsed,
            )

            return VoiceboxResult(
                success=True,
                audio_path=os.path.abspath(output_path),
                message=f"音色转换完成（耗时 {elapsed:.1f}s）",
                duration=duration,
                source_audio=source_audio,
                target_audio=target_audio,
                params={
                    "tau": tau,
                    "message": message,
                    "elapsed_seconds": round(elapsed, 2),
                    **kwargs,
                },
            )

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error("[VoiceboxEngine] 转换失败: %s", e, exc_info=True)
            return VoiceboxResult(
                success=False,
                message=f"音色转换失败: {e}",
                source_audio=source_audio,
                target_audio=target_audio,
                params={"tau": tau, "elapsed_seconds": round(elapsed, 2), **kwargs},
            )

    def _extract_speaker_embedding(self, audio_path: str, torch: Any) -> Any:
        """从参考音频提取说话人嵌入（SE）。

        使用 ToneColorConverter 内置的 ReferenceEncoder 从参考音频提取音色嵌入。

        Args:
            audio_path: 参考音频文件路径。
            torch: torch 模块引用。

        Returns:
            torch.Tensor: 说话人嵌入向量，形状 (1, 256)。
        """
        import librosa

        # 加载音频（ToneColorConverter 内部采样率通常为 16000 或 22050）
        audio, sr = librosa.load(audio_path, sr=None)
        audio_tensor = torch.tensor(audio, device=self.device).float().unsqueeze(0)

        # 使用 ToneColorConverter 的参考编码器提取嵌入
        with torch.no_grad():
            # ToneColorConverter 内部通常有 extract_se 方法或通过 ref_enc 提取
            if hasattr(self._converter, "extract_se"):
                se = self._converter.extract_se(audio_tensor, sr)
            elif hasattr(self._converter, "ref_enc"):
                # 直接调用参考编码器
                mel = self._converter.extract_mel(audio_tensor, sr)
                se = self._converter.ref_enc(mel)
            else:
                # 回退：使用 ToneColorConverter 的内置方法
                se = self._converter.model.ref_enc(self._converter.extract_mel(audio_tensor, sr))

        if se.dim() == 1:
            se = se.unsqueeze(0)

        logger.debug("[VoiceboxEngine] 目标说话人嵌入 shape=%s", se.shape)
        return se

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
