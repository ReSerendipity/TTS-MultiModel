"""Edge-TTS 引擎适配器（CPU 兜底降级引擎，NEXT 评估 P2-2 落地）。

**定位**：ENGINE_EVALUATION.md 评估结论「Edge-TTS 建议作为 CPU 兜底引擎集成」的注册落地。
本适配器只做「注册 + 能力声明 + 可用的流式 TTS 实现」，**不**冒充完整 TTSEngine：
- 实现：``is_ready`` / ``load`` / ``unload`` / ``generate_streaming``（文本→语音流式）；
- 不实现：voice_design / clone / script / ultimate / prompt / lora —— 未声明能力不冒充实现
  （tests/test_engine_capability_contract.py 的 ``test_undeclared_features_not_implemented`` 校验此点）；
  因此 ``/api/engines`` 发现端点会如实报告 ``tts_engine=False``（部分能力引擎）。

**依赖与降级**（与 step-audio-editx「仓库未附带 → 安全降级」同一模式）：
- ``edge-tts`` 为可选轻量依赖（微软在线 TTS 客户端，需要外网），**不随仓库分发**；
- 缺失时 ``is_ready()=False``、``load()`` 抛 ``EngineLoadError`` 并提示安装命令，
  不影响其它引擎启动/切换。
- 启用：``pip install "edge-tts>=6.1,<8"``（config.yaml engines.edge-tts.pip_extra）。

**输出格式**：优先请求 ``riff-24khz-16bit-mono-pcm``（24000 Hz PCM，与注册的
sample_rate=24000 一致，便于后续接入 SSE/二进制分块）；低版本 edge-tts 不支持
``output_format`` 时回退默认 mp3 并如实记录（不再伪装成 PCM）。

**协议说明**：edge_tts 是异步 API，``generate_streaming`` 实现为 **async generator**
（``async for chunk in communicate.stream()``）；本引擎当前无路由接线，属能力声明 +
后续接线参考实现，接入路由时按异步流式消费即可。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from ..exceptions import EngineLoadError

logger = logging.getLogger("tts_multimodel.engines.edge_tts")

#: edge-tts 在线音色名 → 项目可读展示（部分常用音色；完整清单见 edge-tts 文档）
_KNOWN_VOICES = {
    "zh-CN-XiaoxiaoNeural": "晓晓（中文女声）",
    "zh-CN-YunxiNeural": "云希（中文男声）",
    "en-US-JennyNeural": "Jenny（英语女声）",
    "en-US-GuyNeural": "Guy（英语男声）",
    "ja-JP-NanamiNeural": "Nanami（日语女声）",
}

#: 默认音色（中文女声，与测试文本语境匹配）
_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

#: 请求的 PCM 输出格式（24000 Hz 16bit mono，与注册 sample_rate 一致）
_PCM_FORMAT = "riff-24khz-16bit-mono-pcm"


def _import_edge_tts() -> Any:
    """懒导入 edge-tts；缺失时抛 EngineLoadError（存在性守卫安全降级）。"""
    try:
        import edge_tts
    except ImportError as exc:
        raise EngineLoadError(
            'edge-tts 未安装（CPU 兜底引擎不可用，不影响其它引擎）。启用：pip install "edge-tts>=6.1,<8"'
        ) from exc
    return edge_tts


class EdgeTTSEngine:
    """Edge-TTS 在线 TTS 引擎适配器（CPU/零显存，网络依赖）。

    仅提供文本→语音流式合成；不加载任何本地权重，``vram_requirement=0``。
    """

    def __init__(self) -> None:
        self._ready = False
        self._backend: Any = None  # edge_tts 模块引用（懒导入后缓存）

    def is_ready(self) -> bool:
        """就绪 = edge-tts 可导入（在线服务无本地加载态；网络在调用时暴露）。"""
        if not self._ready:
            try:
                self._backend = _import_edge_tts()
                self._ready = True
            except EngineLoadError:
                return False
        return True

    def load(self) -> None:
        """校验依赖并标记就绪；缺失时抛 EngineLoadError（安全降级）。"""
        self._backend = _import_edge_tts()
        self._ready = True
        logger.info("[edge-tts] 依赖就绪（在线服务，无本地权重加载）")

    def unload(self) -> None:
        """幂等卸载：仅清状态（在线服务无资源可释放）。"""
        self._ready = False
        self._backend = None

    async def generate_streaming(
        self,
        text: str,
        reference_audio_path: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[tuple[Any, str]]:
        """流式合成：逐块 yield (音频字节, 进度消息)。

        Args:
            text: 待合成文本。
            reference_audio_path: 未支持（Edge-TTS 无克隆能力），仅占位。
            **kwargs: 支持 ``voice``（edge-tts 音色名，默认中文晓晓）。

        Yields:
            tuple[bytes, str]：PCM/MP3 音频块与进度消息。
        """
        edge_tts = self._backend or _import_edge_tts()
        voice = str(kwargs.get("voice") or _DEFAULT_VOICE)
        output_format: str | None = _PCM_FORMAT

        try:
            communicate = edge_tts.Communicate(text, voice, output_format=output_format)
        except TypeError:
            # 低版本 edge-tts 不支持 output_format → 回退默认 mp3，如实记录
            logger.warning("[edge-tts] 当前版本不支持 output_format=%s，回退默认 mp3", output_format)
            communicate = edge_tts.Communicate(text, voice)
            output_format = None

        emitted = 0
        async for chunk in communicate.stream():
            if chunk.get("type") != "audio":
                continue
            data = chunk.get("data")
            if not data:
                continue
            emitted += 1
            yield data, f"edge-tts streaming chunk #{emitted} ({voice})"
        if emitted == 0:
            raise EngineLoadError(f"[edge-tts] 无任何音频块返回（voice={voice}，检查网络/音色名）")
        logger.info(
            "[edge-tts] 完成 text=%d 字符 chunks=%d fmt=%s", len(text), emitted, output_format or "mp3(default)"
        )


def _describe_engine() -> dict[str, Any]:
    """引擎静态信息（供 UI/文档展示）。"""
    return {
        "name": "edge-tts",
        "display_name": "Edge-TTS",
        "requires_gpu": False,
        "kind": "online-tts（CPU 兜底，网络依赖）",
        "voices_sample": _KNOWN_VOICES,
        "default_voice": _DEFAULT_VOICE,
        "notes": "上游 TTS 为微软在线接口；license 见 config.yaml engines.edge-tts.license（LGPL-3.0 动态链接 + 商用自行核实条款）。",
    }
